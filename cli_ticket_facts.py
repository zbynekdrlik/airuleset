"""cli_ticket_facts — the machine facts behind the #1141 P / M / C buckets.

Two facts no label can give (#1141 slice 3), read into a
`cli_ticket_state.TicketFacts` for `bucketize`:

- `pipeline` (P): the tickets an OPEN pull request is linked to while the
  checks on its latest commit are still running. ONE GraphQL query over the
  repo's open PRs (the 100 most recently updated). A PR is linked to a ticket
  the same way M links a merged PR (`cli_release_state._issue_refs`: a `#N`
  in the title, a closing keyword or an `Issue: #N` line in the body), plus
  GitHub's own `closingIssuesReferences`.
- `on_main`: the OPEN tickets whose fix commit is already on main
  (`cli_release_state.merged_released_oids`), split by the deploy state the
  repo declares (`watchdog/deploy_state`): DEPLOYED when every declared PROD
  instance runs a version at or above the one at that commit (C), PENDING
  when one does not yet (M), RELEASED when the repo declares no deploy state
  (C). A PROD version that cannot be read, or a commit version that cannot be
  parsed, leaves the ticket out (unknown).

The #1067 lesson: no blocking gh call on the watchdog / footer render /
`--count` path. Only `refresh()` reads the facts, and only the detached footer
refresher (`tickets-status --refresh`, via `cli_ticket_route.footer`) calls
it. It writes one cache file per repo, which `load()` reads with zero gh, so
`core-quals`/`slice-quals` count the same buckets as the footer. Every failure
is "unknown", never a guess: `pipeline` None, an `on_main` ticket left out, a
stale / missing / corrupt cache → empty facts → the ticket keeps the bucket
the labels give it (the pre-slice-3 number).
"""

import hashlib
import json
import os
import subprocess
import time

import cli_release_state
import cli_ticket_state as ts
import statusbar

FACTS_MAX_AGE_S = 600   # the quals commands trust a cache at most this old
DEPLOY_TTL_S = 600      # a deploy-state read (HTTP to PROD) is reused this long

_RUNNING = ("PENDING", "EXPECTED")   # statusCheckRollup states still in CI
_ON_MAIN_STATES = (ts.DEPLOYED, ts.RELEASED, ts.PENDING)
_PR_QUERY = (
    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name)"
    "{pullRequests(states:OPEN,first:100,orderBy:{field:UPDATED_AT,"
    "direction:DESC}){nodes{number title body closingIssuesReferences("
    "first:20){nodes{number}} commits(last:1){nodes{commit{"
    "statusCheckRollup{state}}}}}}}}")


def cache_path(root, home=None):
    """`~/.claude/tickets-status/facts-<hash of the repo root>.json` — keyed on
    the root (the footer and the quals commands resolve the same root, while
    their slugs can differ on a fork clone, #1141 slice 1). The #689 cache
    sweep drops it once its `root` is gone or its `ts` is a week old."""
    key = hashlib.sha1(os.path.realpath(str(root)).encode()).hexdigest()[:16]
    return statusbar.cache_dir(home) / ("facts-%s.json" % key)


def _dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def pipeline_numbers(payload):
    """The ticket numbers an open PR in `payload` (the parsed `_PR_QUERY`
    answer) is linked to while its latest commit's checks run; None when the
    payload is not a readable answer (unknown, never an empty "no PR")."""
    nodes = _dig(payload, "data", "repository", "pullRequests", "nodes")
    if not isinstance(nodes, list):
        return None
    out = set()
    for pr in nodes:
        number = pr.get("number") if isinstance(pr, dict) else None
        if not isinstance(number, int):
            continue
        commits = _dig(pr, "commits", "nodes")
        last = commits[-1] if isinstance(commits, list) and commits else None
        if _dig(last, "commit", "statusCheckRollup", "state") not in _RUNNING:
            continue
        out |= cli_release_state._issue_refs(pr.get("title"), pr.get("body"),
                                             number)
        closes = _dig(pr, "closingIssuesReferences", "nodes")
        out |= {_dig(c, "number") for c in (closes or [])
                if isinstance(_dig(c, "number"), int)}
    return frozenset(out)


def read_pipeline(slug, gh_fn):
    """ONE GraphQL call (`gh_fn(args) -> stdout`, "" on any error) → the P
    numbers, or None when the slug or the answer is unusable."""
    owner, _, name = (slug or "").partition("/")
    if not owner or not name:
        return None
    raw = gh_fn(["api", "graphql", "-f", "query=" + _PR_QUERY,
                 "-f", "owner=" + owner, "-f", "name=" + name])
    try:
        return pipeline_numbers(json.loads(raw))
    except (TypeError, ValueError):
        return None


def on_main_states(oids, instances, version_at):
    """`{ticket: DEPLOYED|RELEASED|PENDING}` for the tickets whose fix is on
    main. `oids` maps a ticket to its fix commit (or a list of them: every
    one must be live); `instances` is None when the repo declares no deploy
    state, else the `fetch_deploy_state` rows (`main_version`/`prod_version`);
    `version_at(oid)` reads the version at a commit (None = unknown). A ticket
    whose state cannot be told is left out: it keeps its label bucket."""
    if not oids:
        return {}
    if instances is None:
        return {int(n): ts.RELEASED for n in oids}
    rows = [i for i in instances if isinstance(i, dict)]
    prods = [i.get("prod_version") for i in rows]
    if not prods or not all(prods):
        return {}      # a PROD version not read: nothing can be told
    from watchdog import release_watch

    def ahead(version):
        return [release_watch.main_ahead_of_prod(version, p) for p in prods]

    main = next((i.get("main_version") for i in rows
                 if i.get("main_version")), None)
    if main and all(a is False for a in ahead(main)):
        return {int(n): ts.DEPLOYED for n in oids}   # PROD runs main itself
    out = {}
    for number, commits in oids.items():
        commits = [commits] if isinstance(commits, str) else list(commits or ())
        versions = [version_at(c) for c in commits]
        if not versions or not all(versions):
            continue
        verdicts = [a for v in versions for a in ahead(v)]
        if any(a is None for a in verdicts):
            continue
        out[int(number)] = ts.PENDING if any(verdicts) else ts.DEPLOYED
    return out


def _read(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path, data):
    # airuleset:script-ok never-raise: a failed write only makes the quals
    # commands read "unknown" (the old buckets); the footer refresh goes on.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w") as fh:
            fh.write(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        pass


def _facts(merged, handed, pipeline, on_main):
    return ts.TicketFacts(merged=frozenset(int(n) for n in (merged or ())),
                          handed=dict(handed or {}),
                          pipeline=frozenset(pipeline or ()),
                          on_main=dict(on_main or {}))


def refresh(root, slug, numbers, *, merged=(), handed=None, home=None,
            now=None, gh_fn=None, released_fn=None, deploy_fn=None,
            version_at_fn=None):
    """Read the facts for the open `numbers` of the repo at `root`, cache them
    for the quals commands, and return the `TicketFacts` (with the given
    `merged` numbers and `handed` map). Seams: `gh_fn(args) -> stdout`,
    `released_fn(root, numbers, slug) -> {ticket: commits}`, `deploy_fn(root)
    -> None | {"instances", "version_file"}`, `version_at_fn(root, file, oid)`.
    The deploy read (HTTP to PROD) runs only when a released ticket exists
    and is reused for DEPLOY_TTL_S."""
    now = time.time() if now is None else now
    numbers = {int(n) for n in (numbers or ())}
    path = cache_path(root, home)
    prev = _read(path)
    pipeline = (read_pipeline(slug, gh_fn or _default_gh(root))
                if numbers else frozenset())
    oids = (released_fn or _default_released)(root, numbers, slug) or {}
    deploy = prev.get("deploy")
    fresh = (isinstance(deploy, dict)
             and isinstance(deploy.get("ts"), (int, float))
             and 0 <= now - deploy["ts"] <= DEPLOY_TTL_S)
    if oids and not fresh:
        deploy = {"ts": now, "decl": (deploy_fn or _default_deploy)(root)}
    decl = deploy.get("decl") if isinstance(deploy, dict) else None
    states = on_main_states(
        oids, None if decl is None else (decl.get("instances") or []),
        lambda oid: (version_at_fn or _default_version_at)(
            root, (decl or {}).get("version_file"), oid))
    if pipeline is not None:
        pipeline = frozenset(pipeline) & numbers
    _write(path, {"ts": now, "root": str(root),
                  "pipeline": None if pipeline is None else sorted(pipeline),
                  "on_main": {str(n): s for n, s in sorted(states.items())},
                  "deploy": deploy if isinstance(deploy, dict) else None})
    return _facts(merged, handed, pipeline, states)


def load(root, *, merged=(), handed=None, home=None, now=None):
    """The cached facts for the repo at `root` (zero gh), when the footer
    refresher wrote them at most FACTS_MAX_AGE_S ago; otherwise only the
    given `merged` numbers and `handed` map (P and C unknown: old buckets)."""
    now = time.time() if now is None else now
    data = _read(cache_path(root, home))
    stamp = data.get("ts")
    if not (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and -60 <= now - stamp <= FACTS_MAX_AGE_S):
        return _facts(merged, handed, (), {})
    pipeline = data.get("pipeline")
    pipeline = [n for n in pipeline if isinstance(n, int)] if isinstance(
        pipeline, list) else ()
    on_main = data.get("on_main")
    on_main = {int(k): v for k, v in on_main.items()
               if str(k).isdigit() and v in _ON_MAIN_STATES} if isinstance(
        on_main, dict) else {}
    return _facts(merged, handed, pipeline, on_main)


def _default_gh(root):
    def run(args):
        try:
            r = subprocess.run(["gh", *args], cwd=root or None,
                               capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        return r.stdout if r.returncode == 0 else ""
    return run


def _default_released(root, numbers, slug):
    return cli_release_state.merged_released_oids(root, numbers,
                                                  slug=slug or None)


def _default_deploy(root):
    """None when the repo declares no deploy state (a local registry read, no
    network); else the per-instance PROD read + the declared version file."""
    from watchdog import deploy_state as ds
    registry = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(ds.__file__))), ds._REGISTRY_FILENAME)
    project = ds._find_project(ds._load_registry(registry), root)
    decl = project.get("deploy_state") if isinstance(project, dict) else None
    if not isinstance(decl, dict):
        return None
    return {"instances": ds.fetch_deploy_state(root) or [],
            "version_file": decl.get("main_version_file")}


def _default_version_at(root, version_file, oid):
    if not version_file or not oid:
        return None
    from watchdog import deploy_state as ds
    return ds.read_main_version(root, version_file, ref=oid)
