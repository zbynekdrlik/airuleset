"""cli_ticket_facts — the machine facts behind the #1141 P / M / C buckets.

Facts no label can give (#1141 slice 3), read into a
`cli_ticket_state.TicketFacts` for `bucketize`:

- `open_pr` / `pipeline` (P): the tickets an OPEN pull request is linked to,
  and the subset whose PR is in CI — PENDING checks on a non-draft PR whose
  head commit is at most PIPELINE_MAX_AGE_S old (an EXPECTED check that never
  reports, or a stuck run, is not "in CI": the ticket stays I). ONE GraphQL
  query over the repo's open PRs (one page of 100; more = unknown). A PR is
  linked the same way M links a merged PR (`cli_release_state._issue_refs`: a
  `#N` in the title, a closing keyword or an `Issue: #N` line in the body),
  plus GitHub's own `closingIssuesReferences`. Any open linked PR keeps C open.
- `on_main`: the OPEN tickets whose fix commit is already on main
  (`cli_release_state.merged_released_oids` — 3-branch repos only: only their
  PR cache records the fix commit), split by the deploy state the repo
  declares (`watchdog/deploy_state.deploy_declaration`): DEPLOYED when every
  declared PROD instance runs at least the version of the first main commit
  that contains the fix (the release merge — a bump-at-cut repo such as
  odoo-erp carries the PREVIOUS release at the fix commit itself), PENDING
  when one does not yet (M), RELEASED when the repo declares no deploy state
  (C). An unreadable registry, a partial PROD read, an unreadable PROD
  version or an unparseable release version leaves the ticket out (unknown).
- `reopened` (ruling 1): for the live (C) candidates only, ONE batched
  GraphQL call reads `stateReason` on every refresh (never reused); REOPENED
  keeps C open, and an unreadable answer is unknown (no C). The quals path
  sees a reopen at the next footer refresh (its cache is at most 10 min old).

The #1067 lesson: no blocking gh call on the watchdog / footer render /
`--count` path. Only `refresh()` reads the facts, and only the detached footer
refresher (`tickets-status --refresh`, via `cli_ticket_route.footer`) calls
it. It writes one cache file per repo, which `load()` reads with zero gh, so
`core-quals`/`slice-quals` count the same buckets as the footer. Every failure
is "unknown", never a guess: the PR facts None, an `on_main` ticket left out,
a stale / missing / corrupt cache → empty facts → the ticket keeps the bucket
the labels give it (the pre-slice-3 number).
"""

import calendar
import hashlib
import json
import os
import re
import subprocess
import time

import cli_release_state
import cli_ticket_state as ts
import statusbar

FACTS_MAX_AGE_S = 600       # the quals commands trust a cache at most this old
DEPLOY_TTL_S = 600          # a deploy-state read (HTTP to PROD) is reused this long
PR_REUSE_S = 60             # panes of one repo share one PR read this long
PIPELINE_MAX_AGE_S = 3 * 3600   # a head commit older than this is stuck, not P

_ON_MAIN_STATES = (ts.DEPLOYED, ts.RELEASED, ts.PENDING)
_OID_RE = re.compile(r"[0-9a-f]{7,40}")
_PR_QUERY = (
    "query($owner:String!,$name:String!){repository(owner:$owner,name:$name)"
    "{pullRequests(states:OPEN,first:100,orderBy:{field:UPDATED_AT,"
    "direction:DESC}){pageInfo{hasNextPage} nodes{number title body isDraft "
    "closingIssuesReferences(first:20){nodes{number repository{nameWithOwner}}}"
    " commits(last:1){nodes{commit{committedDate statusCheckRollup{state}}}}}}}}")


def cache_path(root, home=None):
    """`~/.claude/tickets-status/facts-<hash of the repo root>.json` — keyed on
    the root (the footer and the quals commands resolve the same root, while
    their slugs can differ on a fork clone, #1141 slice 1). The #689 cache
    sweep drops it once its `root` is gone or its `ts` is a week old."""
    key = hashlib.sha1(os.path.realpath(str(root)).encode()).hexdigest()[:16]
    return statusbar.cache_dir(home) / ("facts-%s.json" % key)


def forget(root, home=None):
    """Drop the cached facts (a failed refresh): the quals commands then read
    "unknown" instead of facts that no longer match the footer."""
    try:
        cache_path(root, home).unlink()
    except OSError:
        pass   # already gone / unwritable: load() ages it out anyway


def _dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _epoch(stamp):
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def open_pr_states(payload, now=None, slug=None):
    """`{ticket: in_ci}` for every ticket an open PR in `payload` (the parsed
    `_PR_QUERY` answer) is linked to; `in_ci` is True when one such PR's head
    commit has PENDING checks, is not a draft and is at most
    PIPELINE_MAX_AGE_S old (no date = fresh). A `closingIssuesReferences`
    entry of another repo than `slug` is ignored. None when the payload is not
    a readable answer or more open PRs exist than one page holds (unknown,
    never an empty "no PR")."""
    now = time.time() if now is None else now
    prs = _dig(payload, "data", "repository", "pullRequests")
    nodes = _dig(prs, "nodes")
    if not isinstance(nodes, list) or _dig(prs, "pageInfo", "hasNextPage"):
        return None
    out = {}
    for pr in nodes:
        number = pr.get("number") if isinstance(pr, dict) else None
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        commits = _dig(pr, "commits", "nodes")
        head = _dig(commits[-1] if isinstance(commits, list) and commits
                    else None, "commit")
        born = _epoch(_dig(head, "committedDate"))
        in_ci = (_dig(head, "statusCheckRollup", "state") == "PENDING"
                 and pr.get("isDraft") is not True
                 and (born is None or now - born <= PIPELINE_MAX_AGE_S))
        linked = cli_release_state._issue_refs(pr.get("title"), pr.get("body"),
                                               number)
        closes = _dig(pr, "closingIssuesReferences", "nodes")
        linked |= {_dig(c, "number") for c in (closes or [])
                   if isinstance(_dig(c, "number"), int) and _same_repo(c, slug)}
        for ticket in linked:
            out[ticket] = out.get(ticket, False) or in_ci
    return out


def _same_repo(ref, slug):
    other = _dig(ref, "repository", "nameWithOwner")
    return not (slug and isinstance(other, str)
                and other.lower() != slug.lower())


def pipeline_numbers(payload, now=None):
    """The P tickets of `payload` (linked to an open PR in CI); None when the
    payload is unreadable."""
    states = open_pr_states(payload, now)
    return None if states is None else frozenset(
        n for n, in_ci in states.items() if in_ci)


def read_prs(slug, gh_fn, now=None):
    """ONE GraphQL call (`gh_fn(args) -> stdout`, "" on any error) → the
    `open_pr_states` map, or None when the slug or the answer is unusable."""
    owner, _, name = (slug or "").partition("/")
    if not owner or not name:
        return None
    raw = gh_fn(["api", "graphql", "-f", "query=" + _PR_QUERY,
                 "-f", "owner=" + owner, "-f", "name=" + name])
    try:
        return open_pr_states(json.loads(raw), now, slug)
    except (TypeError, ValueError):
        return None


REOPENED_MAX = 100   # one batched stateReason call; more candidates = unknown


def read_reopened(slug, gh_fn, numbers):
    """ONE batched GraphQL call (`iN:issue(number:N){stateReason}` aliases,
    ruling 1): the REOPENED subset of `numbers`. None (unknown) on a bad
    slug, more than REOPENED_MAX numbers, any gh error or a missing issue."""
    owner, _, name = (slug or "").partition("/")
    numbers = sorted({int(n) for n in numbers or ()})
    if not numbers:
        return frozenset()
    if not owner or not name or len(numbers) > REOPENED_MAX:
        return None
    query = ("query($owner:String!,$name:String!){repository(owner:$owner,"
             "name:$name){%s}}" % " ".join(
                 "i%d:issue(number:%d){stateReason}" % (n, n) for n in numbers))
    raw = gh_fn(["api", "graphql", "-f", "query=" + query,
                 "-f", "owner=" + owner, "-f", "name=" + name])
    try:
        repo = _dig(json.loads(raw), "data", "repository")
    except (TypeError, ValueError):
        return None
    nodes = [_dig(repo, "i%d" % n) for n in numbers]
    if not all(isinstance(node, dict) for node in nodes):
        return None
    return frozenset(n for n, node in zip(numbers, nodes)
                     if node.get("stateReason") == "REOPENED")


def on_main_states(oids, instances, version_at):
    """`{ticket: DEPLOYED|RELEASED|PENDING}` for the tickets whose fix is on
    main. `oids` maps a ticket to its fix commit (or a list of them: every
    one must be live); `instances` is None when the repo declares no deploy
    state, else the `fetch_deploy_state` rows (`main_version`/`prod_version`);
    `version_at(oid)` reads the version that shipped the commit (None =
    unknown). A ticket whose state cannot be told is left out: it keeps its
    label bucket."""
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


def _ints(values):
    return [n for n in values if isinstance(n, int) and not isinstance(n, bool)
            ] if isinstance(values, list) else []


def _facts(merged, handed, prs, on_main, reopened=frozenset()):
    """The TicketFacts. `prs` or `reopened` None = UNKNOWN, so no ticket can
    be proven done: a live (DEPLOYED/RELEASED) state is dropped and the
    ticket keeps its label bucket (review round 2, ruling 1)."""
    if prs is None or reopened is None:
        on_main = {n: s for n, s in (on_main or {}).items() if s == ts.PENDING}
    prs = prs or {}
    return ts.TicketFacts(merged=frozenset(int(n) for n in (merged or ())),
                          handed=dict(handed or {}),
                          pipeline=frozenset(n for n, c in prs.items() if c),
                          on_main=dict(on_main or {}),
                          open_pr=frozenset(prs),
                          reopened=frozenset(reopened or ()))


def _fresh(stamp, now, ttl):
    return (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and 0 <= now - stamp <= ttl)


def refresh(root, slug, numbers, *, gh_fn, merged=(), handed=None, home=None,
            now=None, released_fn=None, deploy_fn=None, version_at_fn=None):
    """Read the facts for the open `numbers` of the repo at `root`, cache them
    for the quals commands, and return the `TicketFacts` (with the given
    `merged` numbers and `handed` map). Seams: `gh_fn(args) -> stdout`,
    `released_fn(root, numbers, slug) -> {ticket: commits}`, `deploy_fn(root,
    slug) -> None | {"instances", "version_file"}`, `version_at_fn(root,
    file, oid)`. The PR read is shared by the panes of one repo for
    PR_REUSE_S; the deploy read (HTTP to PROD) runs only when a released
    ticket exists and is reused for DEPLOY_TTL_S."""
    now = time.time() if now is None else now
    numbers = {int(n) for n in (numbers or ())}
    path = cache_path(root, home)
    prev = _read(path)
    if (_fresh(prev.get("pr_ts"), now, PR_REUSE_S)
            and isinstance(prev.get("open_pr"), list)):
        running = set(_ints(prev.get("pipeline")))
        prs = {n: n in running for n in _ints(prev.get("open_pr"))}
        pr_ts = prev["pr_ts"]
    else:
        prs = read_prs(slug, gh_fn, now) if numbers else {}
        pr_ts = now if numbers else None   # an empty row set is never reused
    oids = (released_fn or _default_released)(root, numbers, slug) or {}
    deploy = prev.get("deploy")
    if oids and not (isinstance(deploy, dict)
                     and _fresh(deploy.get("ts"), now, DEPLOY_TTL_S)):
        deploy = {"ts": now, "decl": (deploy_fn or _default_deploy)(root, slug)}
    decl = deploy.get("decl") if isinstance(deploy, dict) else None
    states = on_main_states(
        oids, None if decl is None else (decl.get("instances") or []),
        lambda oid: (version_at_fn or _default_version_at)(
            root, (decl or {}).get("version_file"), oid))
    # Read every refresh, never reused: a close+reopen by the self-close guard
    # inside any reuse window would read C again (delta re-review). It runs
    # only when a live (C) candidate exists.
    reopened = read_reopened(
        slug, gh_fn, [n for n, st in states.items()
                      if st in (ts.DEPLOYED, ts.RELEASED)])
    _write(path, {
        "ts": now, "root": str(root), "pr_ts": pr_ts,
        "pipeline": None if prs is None else sorted(n for n, c in prs.items()
                                                    if c),
        "open_pr": None if prs is None else sorted(prs),
        "on_main": {str(n): s for n, s in sorted(states.items())},
        "reopened": None if reopened is None else sorted(reopened),
        "deploy": deploy if isinstance(deploy, dict) else None})
    return _facts(merged, handed, None if prs is None else {
        n: c for n, c in prs.items() if n in numbers}, states, reopened)


def load(root, *, merged=(), handed=None, home=None, now=None):
    """The cached facts for the repo at `root` (zero gh), when the footer
    refresher wrote them at most FACTS_MAX_AGE_S ago; otherwise only the
    given `merged` numbers and `handed` map (P and C unknown: old buckets).
    A corrupt value is skipped, never raised into a count."""
    now = time.time() if now is None else now
    data = _read(cache_path(root, home))
    stamp = data.get("ts")
    if not (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and -60 <= now - stamp <= FACTS_MAX_AGE_S):
        return _facts(merged, handed, {}, {})
    running = set(_ints(data.get("pipeline")))
    prs = ({n: n in running for n in _ints(data.get("open_pr")) + list(running)}
           if isinstance(data.get("open_pr"), list) else None)
    on_main = {}
    raw = data.get("on_main")
    for key, state in (raw.items() if isinstance(raw, dict) else ()):
        if str(key).isdecimal() and state in _ON_MAIN_STATES:
            try:
                on_main[int(key)] = state
            except ValueError:
                continue
    reopened = data.get("reopened")
    return _facts(merged, handed, prs, on_main, frozenset(_ints(reopened))
                  if isinstance(reopened, list) else None)


def _default_released(root, numbers, slug):
    return cli_release_state.merged_released_oids(root, numbers,
                                                  slug=slug or None)


def _default_deploy(root, slug):
    """None when the repo declares no deploy state (a local registry read, no
    network); else the per-instance PROD read + the declared version file.
    An unreadable registry, or fewer PROD reads than declared instances
    (the 20 s fetch budget, a fork clone the reader cannot place), gives no
    instances: unknown, never a partial "every instance runs it"."""
    from watchdog import deploy_state as ds
    decl, known = ds.deploy_declaration(root, slug=slug or None)
    if not known:
        return {"instances": [], "version_file": None}
    if decl is None:
        return None
    declared = [i for i in decl.get("instances") or [] if isinstance(i, dict)]
    instances = ds.fetch_deploy_state(root) or []
    return {"instances": instances if len(instances) >= len(declared) else [],
            "version_file": decl.get("main_version_file")}


def _default_version_at(root, version_file, oid):
    """The version that SHIPPED commit `oid`: the version file at the first
    commit on origin/main's first-parent chain that contains it (the release
    merge). None (unknown) when the fix sits ON that chain (a bump-at-cut
    repo may have shipped it in a later fast-forwarded release), is not on
    main, is not a commit hash, or git cannot tell (review round 2)."""
    if not version_file or not isinstance(oid, str) or not _OID_RE.fullmatch(oid):
        return None
    rng = "%s..origin/main" % oid
    chain = _git_lines(root, "log", "--first-parent", "--reverse",
                       "--format=%H %P", rng)
    after = _git_lines(root, "rev-list", "--ancestry-path", rng)
    if chain is None or after is None:
        return None
    descendants = set(after)
    shipped = None
    for line in chain:
        commit, *parents = line.split()
        if parents and parents[0].startswith(oid):
            break     # ON the chain: a later ff release may have shipped it
        if commit in descendants:
            shipped = commit         # the first main commit that contains it
            break
    if shipped is None:
        return None   # on the chain, main's tip, or not on main: unknown
    from watchdog import deploy_state as ds
    return ds.read_main_version(root, version_file, ref=shipped)


def _git_lines(root, *args):
    """The stdout lines of a bounded local git read, None on any failure."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None
