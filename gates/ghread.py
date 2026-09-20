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
import hashlib
import json
import os
import re
import subprocess
import time

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


# --------------------------------------------------------------------------- #
# #1087 L1 item (b) -- ETag-conditional REST reads for the periodic readers.
#
# GitHub's conditional-request mechanism: a request carrying `If-None-Match:
# <etag>` answered `304 Not Modified` does NOT count against the primary REST
# rate limit (verified live on this fleet: `rate_limit.core.used` unchanged
# across a 304). So a periodic reader that re-polls the SAME open-issue set can
# re-poll for FREE as long as nothing changed. `rest_get_cached` caches
# `{etag, body, ts}` per URL and sends the ETag; `list_open_issues_cached`
# pages the REST issues endpoint through it (PRs dropped via the `pull_request`
# key, exactly as `is_pull_request`/`gates.designdispatch` do); the client-side
# `search_*` helpers replicate the label/@me `--search` qualifiers so a caller
# can filter one cached snapshot instead of spending one GraphQL search per
# qual. FAIL-OPEN throughout: any error falls back to a plain uncached GET, and
# a failed cache write just means the next read refetches (never a raise).
# --------------------------------------------------------------------------- #
def _etag_dir():
    """The per-URL ETag cache dir, overridable via AIRULESET_GH_ETAG_DIR (for
    tests, mirroring cli_gh_rate's overridable dirs). Token-free: only an ETag
    string + the response body are stored, never an auth header."""
    return (os.environ.get("AIRULESET_GH_ETAG_DIR")
            or os.path.join(os.path.expanduser("~"), ".claude", "gh-etag"))


def _etag_cache_path(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(_etag_dir(), h + ".json")


def _load_etag_cache(url):
    try:
        with open(_etag_cache_path(url), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _save_etag_cache(url, etag, body, now):
    """Best-effort atomic write; a failure is fail-open (the next read simply
    refetches without a conditional header)."""
    d = _etag_dir()
    try:
        os.makedirs(d, exist_ok=True)
        p = _etag_cache_path(url)
        tmp = "%s.tmp.%d" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"etag": etag, "body": body, "ts": now}, fh)
        os.replace(tmp, p)
    except OSError:
        return


def _build_query_url(path, params):
    """`path` (e.g. `repos/o/r/issues`) + `params` dict -> a stable query URL.
    Keys are SORTED so the same logical request always maps to the same cache
    key regardless of dict construction order."""
    if not params:
        return path
    from urllib.parse import urlencode
    return path + "?" + urlencode(sorted(params.items()))


def _loads(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


_STATUS_RE = re.compile(r"^HTTP/\S+\s+(\d{3})")


def _parse_include(out):
    """Parse `gh api --include` raw stdout into (status:int|None, headers:{lower
    -> value}, body:str). The status line is first; headers run until the first
    blank line; everything after is the body. Robust to \\r\\n and to a missing
    status line (transport error) -> status None."""
    lines = re.split(r"\r?\n", out or "")
    status = None
    if lines and lines[0].startswith("HTTP/"):
        m = _STATUS_RE.match(lines[0])
        if m:
            status = int(m.group(1))
    headers, i = {}, 1
    while i < len(lines):
        if lines[i].strip() == "":
            i += 1
            break
        if ":" in lines[i]:
            k, v = lines[i].split(":", 1)
            headers[k.strip().lower()] = v.strip()
        i += 1
    body = "\n".join(lines[i:])
    return status, headers, body


def _record_rate_headers(headers, now=None):
    """#1087 L1b: record a response's ``X-RateLimit-*`` headers into the gh-rate
    status cache — the ONLY truthful budget signal on an App-shim (installation-
    token) box, where ``gh api rate_limit`` reports a fresh bucket while real
    calls 403. Best-effort + fail-open: any error (no headers, cli_gh_rate not
    importable, an unwritable cache) is swallowed — a budget-capture failure must
    never break a REST read. Token-free (only the numeric rate fields)."""
    try:
        if not isinstance(headers, dict):
            return
        rem = headers.get("x-ratelimit-remaining")
        if rem is None:
            return                     # no rate headers on this response
        remaining = int(rem)
        reset = headers.get("x-ratelimit-reset")
        reset = int(reset) if reset is not None else None
        limit = headers.get("x-ratelimit-limit")
        limit = int(limit) if limit is not None else None
        resource = headers.get("x-ratelimit-resource") or "core"
        import cli_gh_rate
        cli_gh_rate.record_headers_reading(resource, remaining=remaining,
                                           reset=reset, limit=limit, now=now)
    except Exception:                  # noqa: BLE001 — budget capture is best-effort
        return


def rest_get_cached(path, params=None, cwd=None, runner=None, timeout=8,
                    env=None, now=None, max_age=0):
    """(obj, err): an ETag-conditional GET of a REST endpoint.

      * `304 Not Modified` -> the cached parsed body (BUDGET-FREE).
      * `200 OK` -> refresh the cache (store the new ETag + body) and return the
        parsed body.
      * any other status / transport error / unparseable body -> fail-open to a
        plain uncached GET (`gh api <url>`); if that also fails -> (None,
        `gate-unavailable:<reason>`).

    `max_age`: when > 0 and the cached entry's `ts` is within `max_age` seconds,
    return it with NO gh call at all (per-sweep dedup for several consumers
    reading the same URL). Never raises."""
    if env is None and runner is None:
        env = _gh_env()
    if now is None:
        now = time.time()
    url = _build_query_url(path, params)
    cache = _load_etag_cache(url)

    if cache and max_age and max_age > 0:
        ts = cache.get("ts")
        if isinstance(ts, (int, float)) and 0 <= (now - ts) < max_age:
            obj = _loads(cache.get("body"))
            if obj is not None:
                return obj, None

    argv = ["gh", "api", "--include"]
    etag = cache.get("etag") if cache else None
    if etag:
        argv += ["-H", "If-None-Match: %s" % etag]
    argv.append(url)
    rc, out, err = _run(argv, cwd, timeout, env, runner)
    status, headers, body = _parse_include(out)
    # #1087 L1b: capture the response's rate-limit headers into the gh-rate
    # status cache (the truthful budget on installation-token boxes). Runs on
    # EVERY --include response — 200, 304 (still carries the budget) and 403
    # (remaining 0). Best-effort + fail-open; never affects the read's result.
    _record_rate_headers(headers, now=now)

    if status == 304 and cache:
        obj = _loads(cache.get("body"))
        if obj is not None:
            return obj, None                 # cached body, free
    if status == 200:
        obj = _loads(body)
        if obj is not None:
            _save_etag_cache(url, headers.get("etag"), body, now)
            return obj, None

    # Fail-open: a plain uncached GET (no --include, no conditional header).
    prc, pout, perr = _run(["gh", "api", url], cwd, timeout, env, runner)
    if prc == 0:
        obj = _loads(pout)
        if obj is not None:
            return obj, None
    return None, _gate_unavailable("%s\n%s" % (err or "", perr or ""), rc)


def _normalize_issue(it):
    """A REST issue object -> the row shape the periodic readers consume, mapping
    REST field names to the gh `--json` names the old GraphQL path returned
    (`created_at`->`createdAt`, `updated_at`->`updatedAt`, `user.login`->
    `authorLogin`). Labels/assignees keep the `[{name}]`/`[{login}]` shape."""
    labels = [{"name": (lb or {}).get("name")}
              for lb in (it.get("labels") or []) if isinstance(lb, dict)]
    assignees = [{"login": (a or {}).get("login")}
                 for a in (it.get("assignees") or []) if isinstance(a, dict)]
    user = it.get("user")
    return {
        "number": it.get("number"),
        "title": it.get("title"),
        "createdAt": it.get("created_at"),
        "updatedAt": it.get("updated_at"),
        "labels": labels,
        "assignees": assignees,
        "authorLogin": user.get("login") if isinstance(user, dict) else None,
    }


def list_open_issues_cached(slug, cwd=None, runner=None, timeout=8, env=None,
                            per_page=100, max_pages=20, now=None, max_age=0):
    """(rows, err): every OPEN ISSUE of `slug` as normalized rows (PRs dropped
    via the `pull_request` key), paged through `rest_get_cached` so an unchanged
    set re-reads for free. Returns (None, err) if ANY page read fails -- a
    PARTIAL listing read as complete would silently reclassify rows (#1021), so
    the caller falls back to its GraphQL path on None rather than trust a
    truncated set."""
    rows = []
    complete = False
    for page in range(1, max_pages + 1):
        params = {"state": "open", "per_page": per_page, "page": page}
        obj, err = rest_get_cached("repos/%s/issues" % slug, params, cwd=cwd,
                                   runner=runner, timeout=timeout, env=env,
                                   now=now, max_age=max_age)
        if err or not isinstance(obj, list):
            return None, (err or "%s issue list returned no array"
                          % GATE_UNAVAILABLE_PREFIX)
        for it in obj:
            if not isinstance(it, dict) or it.get("pull_request"):
                continue                     # skip PR rows (issues endpoint mixes them)
            rows.append(_normalize_issue(it))
        if len(obj) < per_page:
            complete = True
            break                            # short page -> provably the last page
    if not complete:
        # #1087 review: max_pages exhausted with a STILL-FULL last page -> the
        # set is TRUNCATED. A partial listing read as complete would silently
        # reclassify rows (#1021, the very failure this fn's docstring cites),
        # so fail-safe to None and let the caller fall back to GraphQL.
        return None, ("%s more than %d open issues (paging truncated)"
                      % (GATE_UNAVAILABLE_PREFIX, max_pages * per_page))
    return rows, None


# The `--search` qualifiers the client-side matcher can replicate against a
# cached snapshot. Anything else (free text, in:title, is:, created:, ...) is
# NOT client-side -> the caller keeps the exact GraphQL search for that qual.
def _token_kind(tok):
    base = tok[1:] if tok.startswith("-") else tok
    if base.startswith("label:"):
        return "label"
    if base in ("no:label", "no:assignee"):
        return "no"
    if base.startswith("assignee:") or base.startswith("author:"):
        return "person"
    return None


def search_client_side_ok(search, me_login=None):
    """True iff EVERY token of `search` is a qualifier this module can filter
    client-side, AND any `@me` person-qualifier has a resolvable `me_login`.
    A single unsupported token -> False (keep GraphQL, exact semantics).

    #1087 review: GitHub search semantics the exact-string matcher does NOT
    honour must fall back to GraphQL, or a FALSE EXCLUSION under-counts (the
    never-stop / footer-wrong class): a comma value (`label:a,b` is ANY-OF), a
    quoted value (`label:"needs answer"` / `assignee:'x'` keeps the quotes), and
    an empty `label:` value all defeat it. Reject any value carrying `,`/`"`/`'`
    and an empty label value; case is handled by `_token_matches` (casefold)."""
    for tok in (search or "").split():
        kind = _token_kind(tok)
        if kind is None:
            return False
        base = tok[1:] if tok.startswith("-") else tok
        value = base.split(":", 1)[1] if ":" in base else ""
        if any(c in value for c in (",", '"', "'")):
            return False               # comma-ANY-OF / quoted value -> GraphQL
        if kind == "label" and value == "":
            return False               # `label:` with no value -> GraphQL
        if kind == "person" and value == "@me" and not me_login:
            return False
    return True


def _cf(s):
    """casefold, or None for a non-string (GitHub compares label names + logins
    case-insensitively — #1087 review)."""
    return s.casefold() if isinstance(s, str) else None


def _token_matches(row, t, me_login):
    if t.startswith("label:"):
        name = _cf(t[len("label:"):])
        return any(_cf((lb or {}).get("name")) == name
                   for lb in row.get("labels") or [])
    if t == "no:label":
        return not row.get("labels")
    if t == "no:assignee":
        return not row.get("assignees")
    if t.startswith("assignee:"):
        who = t[len("assignee:"):]
        who = _cf(me_login if who == "@me" else who)
        return who is not None and any(_cf((a or {}).get("login")) == who
                                       for a in row.get("assignees") or [])
    if t.startswith("author:"):
        who = t[len("author:"):]
        who = _cf(me_login if who == "@me" else who)
        return who is not None and _cf(row.get("authorLogin")) == who
    return False


def issue_matches_search(row, search, me_login=None):
    """True iff `row` (a `_normalize_issue` snapshot row) satisfies every token
    of `search` (space = AND, a `-` prefix negates). Assumes
    `search_client_side_ok(search, me_login)` -- an unknown token never matches,
    so it fails the positive case (never a false include)."""
    for tok in (search or "").split():
        neg = tok.startswith("-")
        t = tok[1:] if neg else tok
        ok = _token_matches(row, t, me_login)
        if neg and ok:
            return False
        if not neg and not ok:
            return False
    return True
