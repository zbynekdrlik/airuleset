"""gates.ghread -- REST-first GitHub issue/comment reader with a GraphQL
fallback, budget-aware, for the design/dispatch gates (#1070 W38 item 1).

The owner identity's GraphQL 5000/h budget is exhausted hourly on the gk box
(gh-rate log 19:23/20:34/21:22). `gh issue view` (GraphQL) then errors, and a
gate that reads design presence through it wrongly concludes "no design" and
hard-blocks a worker whose main HAD posted the design (odoo-erp #7120/#7293,
18.9.2026). REST (`gh api repos/<o>/<r>/issues/...`) draws on a SEPARATE 5000/h
budget and keeps working through the GraphQL exhaustion. This helper reads
REST-first, falls back to GraphQL, and -- only when BOTH paths fail
(quota/transport) -- reports an explicit `gate-unavailable:<reason>` so the
caller blocks fail-closed with an HONEST reason (never the words "missing
design").

STDLIB ONLY at import; airuleset._gh_env is imported lazily.
"""
import json
import re
import subprocess

GATE_UNAVAILABLE_PREFIX = "gate-unavailable:"

# Rate-limit / quota signatures in a gh error. ANY both-paths failure is
# gate-unavailable; this pattern only NAMES quota when it is the cause, so the
# owner reads "rate limit" and not a bare rc.
_RATE_LIMIT_RE = re.compile(
    r"rate limit|rate_limit|rate.?limited|secondary rate|abuse detection|"
    r"\b429\b|would exceed|quota", re.IGNORECASE)


def _gh_env():
    try:
        import airuleset
        return airuleset._gh_env()
    except Exception:
        return None


def _run(argv, cwd, timeout, env, runner):
    """(rc, stdout, stderr). `runner(argv)` is the test seam; production shells
    out. A subprocess launch failure is (127, "", <err>). Never raises."""
    if runner is not None:
        rc, out, err = runner(argv)
        return rc, out or "", err or ""
    try:
        r = subprocess.run(argv, cwd=cwd or None, capture_output=True,
                           text=True, timeout=timeout, env=env)
    except Exception as e:
        return 127, "", str(e)
    return r.returncode, r.stdout or "", r.stderr or ""


def _gate_unavailable(err_text, rc):
    """A `gate-unavailable:<reason>` string. NEVER contains the words 'missing
    design' -- this is a READ failure, not an absent design (#1070)."""
    lines = [ln for ln in (err_text or "").splitlines() if ln.strip()]
    tail = lines[-1].strip()[:200] if lines else ("rc=%d" % rc)
    if _RATE_LIMIT_RE.search(err_text or ""):
        return ("%s GitHub read unavailable (rate limit / quota) -- %s"
                % (GATE_UNAVAILABLE_PREFIX, tail))
    return ("%s GitHub read unavailable (gh error rc=%d) -- %s"
            % (GATE_UNAVAILABLE_PREFIX, rc, tail))


def _parse_ndjson_bodies(out):
    """Comment bodies from `gh api …/comments -q '.[]'` ndjson (one compact
    JSON object per line). A malformed line is skipped, never fatal."""
    bodies = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("body"), str):
            bodies.append(obj["body"])
    return bodies


def _parse_gql_comments(out):
    """`[body, ...]` from `gh issue view --json comments` output, or None when
    the payload is not the expected JSON object (a broken/stub gh returning rc0
    garbage) -- the caller then treats that as a failed read, never 'no
    comments'."""
    try:
        obj = json.loads(out)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return [c["body"] for c in obj.get("comments", [])
            if isinstance(c, dict) and isinstance(c.get("body"), str)]


def resolve_slug(cwd, runner=None):
    """`owner/name` from `git -C <cwd> remote get-url origin` (a LOCAL git op,
    NEVER an API call, so it survives a GraphQL/REST quota exhaustion), or None.
    Handles the ssh (`git@github.com:owner/name.git`) and https
    (`https://github.com/owner/name(.git)`) remote forms."""
    argv = ["git"] + (["-C", cwd] if cwd else []) + ["remote", "get-url", "origin"]
    rc, out, _err = _run(argv, cwd, 6, None, runner)
    if rc != 0:
        return None
    url = (out or "").strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # ssh `git@host:owner/name` -> the ':' becomes '/', then last two segments.
    url = url.replace(":", "/")
    parts = [p for p in url.split("/") if p]
    if len(parts) >= 2:
        return "%s/%s" % (parts[-2], parts[-1])
    return None


def read_comment_bodies(number, slug, cwd=None, runner=None, timeout=8, env=None):
    """(bodies, err): `bodies` a list[str] of comment bodies in CREATION order,
    or None; `err` None on success, else a `gate-unavailable:<reason>` string
    (BOTH the REST and the GraphQL read failed -- quota or transport).

    REST-first (`gh api repos/<slug>/issues/<n>/comments`, a core-budget call),
    then a GraphQL fallback (`gh issue view <n> -R <slug> --json comments`)."""
    if env is None and runner is None:
        env = _gh_env()
    rest_rc, rest_out, rest_err = _run(
        ["gh", "api", "repos/%s/issues/%s/comments" % (slug, number),
         "--paginate", "-q", ".[]"], cwd, timeout, env, runner)
    if rest_rc == 0:
        return _parse_ndjson_bodies(rest_out), None
    gql_rc, gql_out, gql_err = _run(
        ["gh", "issue", "view", str(number), "-R", slug, "--json", "comments"],
        cwd, timeout, env, runner)
    if gql_rc == 0:
        bodies = _parse_gql_comments(gql_out)
        if bodies is not None:
            return bodies, None
        # rc0 but unparseable (a stub / broken gh) -> BOTH paths effectively
        # failed; fall through to the gate-unavailable block below.
    combined = "%s\n%s" % (rest_err or "", gql_err or "")
    return None, _gate_unavailable(combined, rest_rc)


def read_issue(number, slug, cwd=None, runner=None, timeout=8, env=None):
    """(obj, err): the issue/PR JSON dict from `gh api repos/<slug>/issues/<n>`
    (GitHub's issues endpoint returns a PR too, carrying a `pull_request` key),
    or (None, `gate-unavailable:<reason>`). REST-only -- the `pull_request`
    field only exists on the REST issues endpoint, and REST survives the
    GraphQL exhaustion this whole module exists for."""
    if env is None and runner is None:
        env = _gh_env()
    rc, out, err = _run(
        ["gh", "api", "repos/%s/issues/%s" % (slug, number)],
        cwd, timeout, env, runner)
    if rc == 0:
        try:
            return json.loads(out), None
        except (ValueError, TypeError):
            return None, _gate_unavailable("unparseable issue JSON", rc)
    return None, _gate_unavailable(err, rc)


def is_pull_request(number, slug, cwd=None, runner=None, timeout=8, env=None):
    """(is_pr, err): True/False when the issue is resolvable, else
    (None, `gate-unavailable:<reason>`) -- UNKNOWN, never guessed."""
    obj, err = read_issue(number, slug, cwd=cwd, runner=runner,
                          timeout=timeout, env=env)
    if err:
        return None, err
    if not isinstance(obj, dict):
        return None, "%s issue read returned no object" % GATE_UNAVAILABLE_PREFIX
    return bool(obj.get("pull_request")), None
