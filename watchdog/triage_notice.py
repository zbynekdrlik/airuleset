"""#1404 — re-triage the W / I rot of EVERY live project session, armed or not.

Owner, 28.11.2026: "claude napchal do W kopec ticketov ktore by sa mali pohnut
ale sa nehybu ... aspon raz za 3 dni ak je nieco v W alebo na zaklade nejakeho
casovaca ... by sa W veci mali rozhybat" and "claude nieco chce odo mna a vobec
to nie je v U". Before this leaf the only machinery that ever put a W ticket
back in front of a session was Job 20 (`ops_wait_recheck`), and it runs only
inside an ARMED `/goal` pane; a project session the owner runs without
`/autopilot` (most dev1 projects) never heard of its W tickets again, and an
owner step written as prose ("Waiting for the next FOH slot", dasio 127) sat
in I with no U label.

What it does, per project checkout (the footer cache root of a live Claude
pane), at most once per REPEAT_S and only when there is something to triage:

- W rot: a plain `ops-wait` ticket whose wait is unbacked — no valid
  `Waiting-on:` line, one older than 3 days with no newer cited push, or a
  passed own `Ops-wait-target:` date (`gates.opswait.unbacked_reason`, the
  SAME predicate and the same comment parse `cli_quals._ages_from_comments`
  the footer fact `wait_unbacked` uses);
- a hidden owner step: an I ticket with no U label whose newest OWN comment
  states a wait on a person / on-site slot / owner window
  (`gates.ownerdep.owner_dependency`, the Stop gates' detector).

The candidate SET is the footer cache's own partition (`open_numbers` = I,
`ops_wait_numbers` = W), so a shared-repo stream judges only its slice; the
ONE batched read (a GraphQL alias query over exactly those numbers, chunked)
runs only when a notice could go now (kind on, root not told / checked
recently, a ready unarmed pane). `gh issue list --json comments` is not used:
it 502s on a busy repo (#1067). The notice is a machine nudge of kind
`w-triage` (`send_verified` makes it the #1157 pointer row, a registered
machine prefix, never read as the owner) through the shared idle-pane gate
set (`watchdog.idle_pane`), the `nudge_gate` floor and the B9 pane budget.
An armed `/goal` pane is skipped: Job 20 already re-checks its W. The leaf
only SURFACES: setting / clearing labels stays the session's judgment (#547).
A dry run reads (so `watchdog --once --dry-run` shows what it would name) but
types and records nothing.
"""
import json
import os
import re

NUDGE_KIND = "w-triage"
STATE_KEY = "triage_notice"
DAY_S = 24 * 3600
REPEAT_S = 3 * DAY_S          # a project told once is told again after 3 days at the earliest
CHECK_S = DAY_S               # a read that found nothing is repeated daily at most
FAIL_S = 3600                 # a failed read is retried hourly at most
KEEP_S = 7 * DAY_S            # records older than this (and no live pane) are pruned
CACHE_MAX_AGE_S = DAY_S       # the footer cache only scopes the read; labels are re-read live
MIN_BUDGET_S = 60             # one GraphQL read (30 s cap) + one verified delivery
KIND_OFF_LOG_S = 3600
MAX_NUMBERS = 150             # tickets read per project per notice
CHUNK = 25                    # aliases per GraphQL request (#1188: parent page size = cost)
COMMENT_CAP = 100             # newest comments per ticket; a longer thread's W check is unknown
NUDGE_MAX_CHARS = 700         # the #714 size cap
U_LABELS = frozenset({"needs-answer", "needs-decision", "needs-owner-action"})
_LANE_DIR = os.sep + os.path.join(".claude", "worktrees") + os.sep
_SLUG_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _ints(values):
    return {n for n in values if isinstance(n, int) and not isinstance(n, bool)} \
        if isinstance(values, list) else set()


# --------------------------------------------------------------------------- #
# Scope: the footer cache of the pane's checkout.
# --------------------------------------------------------------------------- #
def cache_of(cwd, home=None, now=None):
    """`(root, i_numbers, w_numbers)` from the footer cache of `cwd` (or of its
    nearest parent up to the git checkout root, the key the footer writes), or
    None: a lane worktree pane, no cache, a failed / stale cache."""
    import time
    import statusbar
    if not cwd or _LANE_DIR in os.path.realpath(cwd) + os.sep:
        return None
    now = time.time() if now is None else now
    path = os.path.abspath(cwd)
    while True:
        cache = statusbar._load(statusbar.cache_dir(home) / (statusbar.cwd_key(path) + ".json"))
        if isinstance(cache, dict):
            break
        if os.path.exists(os.path.join(path, ".git")) or os.path.dirname(path) == path:
            return None
        path = os.path.dirname(path)
    ts = _num(cache.get("ts"))
    if ts is None or not 0 <= now - ts <= CACHE_MAX_AGE_S or cache.get("stale_since") \
            or not isinstance(cache.get("open_numbers"), list):
        return None
    return (str(cache.get("root") or path), _ints(cache.get("open_numbers")),
            _ints(cache.get("ops_wait_numbers")))


# --------------------------------------------------------------------------- #
# The decision (pure).
# --------------------------------------------------------------------------- #
def _names(row):
    return {str(x) for x in (row.get("labels") or []) if isinstance(x, str)}


def _newest_own(comments, self_login):
    import cli_quals
    best, best_ts = None, None
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        author = c.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if not cli_quals._is_own_login(login, self_login):
            continue
        ts = cli_quals._parse_iso_ts(c.get("createdAt"))
        if ts is not None and (best_ts is None or ts > best_ts):
            best, best_ts = c, ts
    return best


def candidates(rows, i_numbers, self_login, now):
    """`(w, u)`: `w` = `[(n, reason)]` plain ops-wait tickets whose wait is
    unbacked; `u` = `[(n, line)]` I tickets with no U label whose newest own
    comment states an owner / on-site dependency. A closed row, a U-labelled
    row and an unreadable shape are skipped; with no own login the U check is
    skipped (never a false "U?")."""
    import cli_quals
    import cli_ticket_state
    from gates import opswait, ownerdep
    w, u = [], []
    for n in sorted(rows or {}):
        row = rows[n]
        if not isinstance(row, dict) or row.get("state") != "OPEN":
            continue
        names = _names(row)
        if names & U_LABELS:
            continue                                   # already the owner's court
        comments = row.get("comments") if isinstance(row.get("comments"), list) else []
        if cli_ticket_state.plain_ops_wait({"labels": [{"name": x} for x in names]}):
            total = _num(row.get("total"))
            if total is None or total > COMMENT_CAP:
                continue                               # an old anchor may be missing: unknown
            reason = opswait.unbacked_reason(cli_quals._ages_from_comments(
                comments, self_login), now)
            if reason:
                w.append((n, reason))
        elif opswait.OPS_WAIT_LABEL not in names and n in i_numbers and self_login:
            own = _newest_own(comments, self_login)
            line = ownerdep.owner_dependency(own.get("body")) if own else ""
            if line:
                u.append((n, line))
    return w, u


def _join_capped(head, nums, budget):
    """`head` + as many `#N` as fit in `budget` chars, then `+K more`."""
    out, shown = head, 0
    for n in nums:
        piece = ("" if shown == 0 else ", ") + "#%d" % n
        if len(out) + len(piece) + 12 > budget:
            break
        out += piece
        shown += 1
    rest = len(nums) - shown
    return out + (" +%d more" % rest if rest else "")


_TAIL = (" For EACH ticket do ONE now: work it; or move it to U with a concrete owner "
         "ask (`needs-owner-action` / `needs-answer` + a comment saying what he must do); "
         "or post a fresh `Waiting-on: <3rd party> — <event> — <check>` line (+ "
         "`Ops-wait-target: <event> by <YYYY-MM-DD>` when a date is known).")


def notice_text(w, u):
    """The ONE notice (<= NUDGE_MAX_CHARS): counts + numbers, never reasons."""
    head = "w-triage: this project's tickets look stuck (#1404, re-checked every 3 days)."
    budget = NUDGE_MAX_CHARS - len(head) - len(_TAIL)
    parts = []
    if w:
        parts.append(_join_capped(" W with no fresh third-party wait or a passed target: ",
                                  [n for n, _ in w], budget // (2 if u else 1)))
    if u:
        parts.append(_join_capped(" I that waits on a person / on-site slot (U?): ",
                                  [n for n, _ in u], budget - sum(len(p) for p in parts)))
    return head + ".".join(parts) + "." + _TAIL


# --------------------------------------------------------------------------- #
# The job.
# --------------------------------------------------------------------------- #
def _hold(rec, now):
    """None when the root may be judged now, else why not."""
    if not isinstance(rec, dict):
        return None
    for key, window in (("at", REPEAT_S), ("checked", CHECK_S), ("failed", FAIL_S)):
        t = _num(rec.get(key))
        if t is not None and 0 <= now - t < window:
            return "%s %d min ago" % ({"at": "told", "checked": "checked",
                                       "failed": "read failed"}[key], (now - t) // 60)
    return None


def triage_job(now, state, panes, *, scope, ready, armed, fetch, self_login, deliver,
               mark_sent, nudges_enabled, handled=None, dry_run=False):
    """One pass; returns journal lines. Injected deps (wiring in `run_job`):
      scope(cwd) -> (root, i_numbers, w_numbers) | None   the footer cache
      ready(pid, cwd) -> (ok, why, sid, tpath)              the shared gate set
      armed(tpath) -> bool                                  an armed /goal (Job 20's)
      fetch(root, numbers) -> {n: row} | None               the ONE batched read
      self_login() -> str | None                            own comment identity
      deliver(pid, tpath, text) -> SendOutcome
      mark_sent(state, sid, kind, now); nudges_enabled(kind) -> bool
    At most ONE read per sweep (the rest wait for the next sweep)."""
    from watchdog import idle_pane
    logs = []
    groups = {}
    for pid, cwd in panes or []:
        sc = scope(cwd)
        if sc and (sc[1] or sc[2]):
            groups.setdefault(sc[0], (sc, []))[1].append((pid, cwd))
    if not groups:
        return logs
    if not nudges_enabled(NUDGE_KIND):
        last = _num(state.get(STATE_KEY + "_kindoff"))
        if last is None or last > now or now - last >= KIND_OFF_LOG_S:
            logs.append("w-triage: skip:kind-off (%d project(s); stage: airuleset.py nudges "
                        "on --kind %s)" % (len(groups), NUDGE_KIND))
            if not dry_run:
                state[STATE_KEY + "_kindoff"] = now
        return logs
    store = dict(state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {})
    read_done = False
    for root in sorted(groups):
        (root, i_nums, w_nums), own = groups[root]
        if _hold(store.get(root), now):
            continue                                   # quiet: holds would journal every sweep
        pick = None
        for pid, cwd in own:
            ok, why, sid, tpath = ready(pid, cwd)
            if ok and armed(tpath):
                why, ok = "armed-goal (Job 20)", False
            if ok:
                pick = (pid, sid, tpath)
                break
        if pick is None:
            logs.append("w-triage: %s hold:%s" % (root, why))
            continue
        if read_done:
            logs.append("w-triage: %s hold:one-read-per-sweep" % root)
            continue
        read_done = True
        numbers = sorted(i_nums | w_nums)[:MAX_NUMBERS]
        try:
            rows = fetch(root, numbers)
        except Exception as exc:  # noqa: BLE001 -- a read error is journalled, retried hourly
            logs.append("w-triage: %s read raised %r" % (root, exc))
            rows = None
        if rows is None:
            if not dry_run:
                store[root] = {"failed": now}
            logs.append("w-triage: %s read failed (retry in %d min)" % (root, FAIL_S // 60))
            continue
        w, u = candidates(rows, i_nums, self_login(), now)
        if not (w or u):
            if not dry_run:
                store[root] = {"checked": now}
            logs.append("w-triage: %s nothing to triage (%d read)" % (root, len(rows)))
            continue
        pid, sid, tpath = pick
        if dry_run:
            logs.append("w-triage: %s -> %s would tell (W %s; U? %s) [dry-run]" % (
                root, pid, [n for n, _ in w], [n for n, _ in u]))
            continue
        if handled is not None:
            handled.add(sid)
        try:
            outcome = deliver(pid, tpath, notice_text(w, u))
        except Exception as exc:  # noqa: BLE001 -- keys may be in: at-most-once
            logs.append("w-triage: %s delivery raised %r" % (root, exc))
            outcome = None
        kind = idle_pane.outcome_kind(outcome)
        if kind in idle_pane.DELIVERED or kind in idle_pane.TYPED_NOT_DELIVERED:
            mark_sent(state, sid, NUDGE_KIND, now)
        if kind in idle_pane.DELIVERED:
            store[root] = {"at": now}
        logs.append("w-triage: %s -> %s %s (W %s; U? %s)" % (
            root, pid, kind, [n for n, _ in w], [n for n, _ in u]))
    if not dry_run:
        state[STATE_KEY] = {k: r for k, r in store.items() if k in groups or (
            isinstance(r, dict) and any(_num(v) is not None and now - v < KEEP_S
                                        for v in r.values()))}
    return logs


# --------------------------------------------------------------------------- #
# Production reads.
# --------------------------------------------------------------------------- #
_ISSUE_GQL = ("i%d: issue(number: %d) { state labels(first: 50) { nodes { name } } "
              "comments(last: %d) { totalCount nodes { author { login } createdAt body } } }")


def _query(numbers):
    return ("query($owner: String!, $name: String!) { rateLimit { cost remaining } "
            "repository(owner: $owner, name: $name) { %s } }"
            % " ".join(_ISSUE_GQL % (n, n, COMMENT_CAP) for n in numbers))


def parse_rows(data, numbers):
    """`{n: {"state", "labels", "comments", "total"}}` from one alias query's
    JSON, or None on an error / odd shape (the whole chunk is unknown)."""
    if not isinstance(data, dict) or data.get("errors"):
        return None
    repo = (data.get("data") or {}).get("repository")
    if not isinstance(repo, dict):
        return None
    out = {}
    for n in numbers:
        node = repo.get("i%d" % n)
        if not isinstance(node, dict):
            continue                                   # deleted / transferred: skipped
        labels = ((node.get("labels") or {}).get("nodes")) or []
        cconn = node.get("comments") or {}
        out[n] = {"state": node.get("state"),
                  "labels": [lb.get("name") for lb in labels if isinstance(lb, dict)],
                  "comments": cconn.get("nodes") if isinstance(cconn.get("nodes"), list) else [],
                  "total": cconn.get("totalCount")}
    return out


def fetch_rows(root, numbers, runner=None):
    """The ONE batched read for `root`'s `numbers`: `{n: row}`, or None when the
    slug is unresolvable, the gh-rate backoff holds, or any chunk fails.
    Refuses under pytest unless a `runner` seam is given (no real gh in tests)."""
    from gates import ghread
    from watchdog import disk_guard_escalation
    if runner is None and disk_guard_escalation.running_under_pytest():
        return None
    slug = (ghread.canonical_slug(root, runner=runner) or "").strip()
    if not _SLUG_RX.match(slug):
        return None
    if runner is None:
        import cli_gh_rate
        if cli_gh_rate.current_gh_backoff(status=cli_gh_rate._load_cache()) > 0:
            return None
    import cli_gh_rate_cost
    owner, name = slug.split("/", 1)
    out = {}
    for i in range(0, len(numbers), CHUNK):
        chunk = numbers[i:i + CHUNK]
        rc, raw, _err = ghread._run(
            ["gh", "api", "graphql", "-f", "query=" + _query(chunk), "-f", "owner=" + owner,
             "-f", "name=" + name], root, 30, None if runner else ghread._gh_env(), runner)
        try:
            data = json.loads(raw) if rc == 0 else None
        except ValueError:
            data = None
        cli_gh_rate_cost.record_query_cost("w-triage", data)
        rows = parse_rows(data, chunk)
        if rows is None:
            return None
        out.update(rows)
    return out


def run_job(now, state, panes, *, run, sleep_fn, projects_dir, dry_run=False, handled=None,
            budget_left=None):
    """The production wiring: the shared `watchdog.idle_pane` gates and delivery,
    resolved at call time (every `watchdog.<name>` seam stays effective); held
    while the sweep has less than MIN_BUDGET_S left."""
    import watchdog
    from watchdog import idle_pane, session_status
    left = budget_left() if budget_left is not None else None
    if left is not None and left < MIN_BUDGET_S:
        return []
    jl = []
    ready_deps, deliver, mark_sent = idle_pane.production_deps(
        now, state, NUDGE_KIND, run=run, sleep_fn=sleep_fn, budget_left=budget_left, journal=jl)

    def ready(pid, cwd):
        return idle_pane.pane_ready(pid, cwd, NUDGE_KIND, projects_dir=projects_dir,
                                    handled=handled, state=state, now=now, **ready_deps)

    def self_login():
        import cli_quals
        return cli_quals._stream_self_login()

    return triage_job(
        now, state, panes, scope=lambda cwd: cache_of(cwd, now=now), ready=ready,
        armed=lambda tpath: session_status.goal_armed_from_transcript(tpath) is True,
        fetch=fetch_rows, self_login=self_login, deliver=deliver, mark_sent=mark_sent,
        nudges_enabled=watchdog.nudges_enabled, handled=handled, dry_run=dry_run) + jl
