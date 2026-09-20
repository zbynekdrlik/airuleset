"""watchdog/compact.py — the compaction OBSERVATION + pane-resolution helpers.

WHAT THIS FILE IS NOW (#1084 L2, owner ROZHODNUTE 2026-09-19). Machine-triggered
`/compact` is REMOVED for good — "uz tie compacty vobec nechcem ... samotne
deploye na targety by to mali zabezpecit". L1 (v0.1.351) turned the delivery off
in code (`compact_sweep` early-returns a single removed journal line, the sole
recorder + both notify-compact hooks were deleted, `compact-request` became a
no-op stub); L2 (this lane) DELETES the orphaned machinery outright:
`deliver_compact`, the whole request/delivered/queued store, the submit-verify +
sync-attempt helpers, the cooldown/boundary/live-bg gates, and the
`compact-request` CLI command. Claude Code's own threshold autocompact is the
ONLY compaction left.

WHAT SURVIVES, and WHY — every symbol below is read by a job OTHER than the
retired job 14:
  * `compact_sweep(now, ...)` — the job-14 slot stays ADDRESSABLE (the #132/#102/
    #402 kept-number precedent) and journals ONE removed line each sweep, so
    `journalctl` proves the fleet-wide guarantee on every box with no per-box
    marker to miss (the forestshop-dev slip that filed #1084).
  * `pending_compact_hold(sid, ...)` — the bounded hold gate the work-pushing
    riders (`watchdog/goal.py`, `lane_reconcile.py`, `u_freshness.py`,
    `queue_arrival_recheck.py`, `ops_wait_recheck.py`) consult before pushing
    work. Its request store is gone, so no machine compact is EVER pending now:
    it returns False unconditionally (the exact behaviour it already produced
    once L1 stopped anything writing the store — fail-open, never wedges a
    writer). Kept as a symbol so those riders need no change.
  * `_find_pane_for_session` / `resolve_self_pane` / `resolve_declared_window_pane`
    — pane resolvers used by `watchdog/goal.py` and `airuleset.py goal-arm` /
    `status`; never compact-specific.
  * `_COMPACT_COMPLETION_HEADING_RX` / `_compact_recent_human_activity` /
    `compact_sync_log_path` — read by `watchdog/cards.py` (job 25): the canonical
    Work-Complete heading regex, the dual-signal recent-human primitive, and the
    self-callback sync-log path.

The compaction OBSERVATION helpers proper (`_pane_compacting`,
`COMPACTING_MARKER`, `_QUEUED_COMPACT_RX`, the transcript compaction records
lane-reconcile / the goal jobs read) live in `watchdog/long_turn.py`
(re-exported via `watchdog/__init__.py`), NOT here — untouched by this lane.

MODULE-IMPORT SAFETY. `watchdog/__init__.py` never imports this file at its own
module level (the `notify`/`burn` convention) — callers reach it via a LAZY
`from watchdog import compact` inside a function body. So this file's own
`import watchdog` (never `from watchdog import <name>`) is always safe:
`watchdog/__init__.py` has finished executing before any lazy import here can be
called. A bare `import watchdog` also keeps a test's `patch.object(watchdog,
"capture_pane", ...)` working (a `from` binding would silently stop seeing the
patch).
"""

import logging
import os
import re
from pathlib import Path

import watchdog

_log = logging.getLogger(__name__)


# #1084 L2 KEEP: the live-worker freshness window (15 min). Its former compact
# consumers (`_live_bg_tasks_detail` / `_session_has_live_bg_tasks`, the #848
# live-tasks delivery veto) were deleted with the machinery, but a DIFFERENT live
# subsystem imports it as its single source: `gates/lanefill.py` (the lane-fill
# Stop gate, #1078/#1089) reads it for `watchdog.count_live_workers(...)`. Kept
# here so that gate keeps working — deleting it silently fails-open that gate
# fleet-wide (the #1084-L2-review BLOCKER this restores).
COMPACT_LIVE_WORKER_FRESHNESS_S = 15 * 60


def _find_pane_for_session(sid, cwd, run=None, projects_dir=None):
    """Resolve the SINGLE current live pane hosting session `sid` — used by the
    `--record` origin and by the periodic sweep, neither of which has
    `$TMUX_PANE` available.

    Cheap pass: the pane whose cwd's NEWEST transcript stem == `sid`. Correct and
    unambiguous for the overwhelmingly common one-live-session-per-cwd case.

    #645 — when TWO claude panes share ONE project cwd (marek + zbynek both in
    presenter-dev2), `find_active_transcript` (cwd-keyed) resolves the SAME
    newest transcript for both, so the cheap pass sees the sid MATCH on BOTH and
    used to return None → `skip:no-pane` forever. It also missed the OPPOSITE
    shape: `sid` is NOT its cwd's newest (an OLDER session sharing the cwd), so
    the cheap pass sees ZERO matches. Both are resolved per-PANE by the claude
    PROCESS start time → a RESUME BOUNDARY (a quiet gap before + a startup burst
    after) in `sid`'s transcript — the only signal that holds (fd/env/cmdline
    carry no sid; the transcript BIRTH is the original session's, months old for
    a `-c` loop; all measured live on dev1+dev2). Ambiguous even there (0 or >1
    boundary owner, an unreadable /proc) → None, the pre-existing safe skip,
    retried next sweep."""
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    # Defensive dedup by pane_id: grouped tmux sessions list one physical pane
    # once per group member (the live presenter case listed one pane 3×).
    # `list_claude_panes` already dedups, but keeping it here makes the match
    # self-contained — a duplicated pane_id can never manufacture false ambiguity.
    panes, seen = [], set()
    for pid, pcwd in watchdog.list_claude_panes(run):
        if pid in seen:
            continue
        seen.add(pid)
        panes.append((pid, pcwd))
    cwd_matches = []
    for pid, pcwd in panes:
        tinfo = watchdog.find_active_transcript(projects_dir, pcwd)
        if tinfo and tinfo[0].stem == sid:
            cwd_matches.append(pid)
    if len(cwd_matches) == 1:
        return cwd_matches[0]
    # Ambiguous per cwd — disambiguate per-PANE via the resume boundary. The
    # DISTINCT skip reasons are debug-logged (#486 "silent suppression ->
    # explicit decision log"): the caller only sees one `SKIP no-pane`, so the
    # next #645-class triage reads WHY here instead of re-investigating.
    tpath = Path(projects_dir) / watchdog.encode_project_dir(cwd) / (sid + ".jsonl")
    if not tpath.exists():
        _log.debug("find-pane %s: no-transcript (cwd_matches=%d)", sid, len(cwd_matches))
        return None
    key = watchdog.encode_project_dir(cwd)
    owners = []
    for pid, pcwd in panes:
        if watchdog.encode_project_dir(pcwd) != key:
            continue      # sid's owner is same-cwd; never boundary-check others
        start = watchdog._pane_claude_start_epoch(pid, run=run)
        if start is None:
            continue
        if watchdog._transcript_resume_boundary_at(tpath, start):
            owners.append(pid)
    if len(owners) == 1:
        return owners[0]
    _log.debug("find-pane %s: %s (owners=%s)", sid,
               "no-boundary-owner" if not owners else "ambiguous-boundary", owners)
    return None

def resolve_self_pane(run=None, projects_dir=None, pane_env=None):
    """Resolve the EXACT pane/cwd/sid of the CALLING session for the
    `--self` entry point — no ambiguity to resolve, `$TMUX_PANE` names it
    directly. Returns `(pane_id, cwd, sid)`; any unresolved element is
    `""`. A blank `sid` means total failure — nothing safe to record or
    deliver without one."""
    run = run or watchdog._default_run
    pane_id = (pane_env if pane_env is not None
              else os.environ.get("TMUX_PANE", "")).strip()
    if not pane_id:
        return "", "", ""
    cwd = ""
    for pid, pcwd in watchdog.list_claude_panes(run):
        if pid == pane_id:
            cwd = pcwd
            break
    if not cwd:
        return pane_id, "", ""
    pdir = projects_dir or watchdog.PROJECTS_DIR
    tinfo = watchdog.find_active_transcript(pdir, cwd)
    if not tinfo:
        return pane_id, cwd, ""
    tpath, _mtime = tinfo
    return pane_id, cwd, tpath.stem

def resolve_declared_window_pane(cwd, run=None, projects_dir=None):
    """#1038 follow-up — resolve the pane whose CURRENT PATH is `cwd`
    (realpath equality — the `cli_concurrency.is_exact_declared_window`
    semantics, never containment), for `airuleset.py status`'s goal row run
    OUTSIDE a tmux pane (over ssh, no `$TMUX_PANE`, so `resolve_self_pane`
    yields nothing). Uses the SAME `watchdog._reconcile_candidate_panes`
    enumeration seam job 9's virgin scan uses (`tmux list-panes -a -F
    '#{pane_id}\\t#{pane_current_command}\\t#{pane_current_path}'`).

    Returns `(pane_id, cwd, sid)` — like `resolve_self_pane`, so the status
    command can treat both resolvers uniformly. When several live panes share
    the cwd, the one running `claude` (a live claude session) wins over a bare
    node/bun; `("", "", "")` when no pane's current path equals `cwd` (no
    claude session at that cwd, or the cwd is not a live pane at all — the
    caller then reports the row `unmeasurable`, never a fabricated NOT-armed).

    READ-ONLY: enumerates panes and reads the transcript for the sid; it
    NEVER types (no keystroke primitive is reachable from here). A tmux read
    failure yields `("", "", "")` — fail-safe toward "unmeasurable", never a
    false verdict.

    KNOWN GAP (#1038-review, fail-safe): `_reconcile_candidate_panes` only
    matches panes whose foreground command is claude/node/bun, and a
    sudo-hosted stream pane (the subdev `sudo su - <stream>` shape) reports the
    sudo-root cwd, not the stream's checkout — so `status` run over ssh for such
    a stream resolves NO pane and the row reads `unmeasurable` rather than the
    real armed state. That is honest (never a false verdict), just a visibility
    gap on the shared-stream boxes; widening the seam's command filter here
    would make an unrelated node/bun pane look like a live claude session to
    every OTHER consumer of it, so the filter is deliberately left narrow."""
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    try:
        cwd_real = os.path.realpath(cwd) if cwd else ""
    except OSError:
        cwd_real = cwd or ""
    if not cwd_real:
        return "", "", ""
    try:
        panes = watchdog._reconcile_candidate_panes(run)
    except Exception:  # noqa: BLE001 — a tmux read failure -> no match (unmeasurable)
        return "", "", ""
    match = None                                   # (pane_id, pane_cwd)
    for pid, pcwd, cmd in panes:
        if not pcwd:
            continue
        try:
            preal = os.path.realpath(pcwd)
        except OSError:
            preal = pcwd
        if preal != cwd_real:
            continue
        if cmd == "claude":
            match = (pid, pcwd)                     # a live claude session wins outright
            break
        if match is None:
            match = (pid, pcwd)                     # a node/bun pane -> keep looking for claude
    if match is None:
        return "", "", ""
    pid, pcwd = match
    sid = ""
    tinfo = watchdog.find_active_transcript(projects_dir, pcwd)
    if tinfo:
        sid = tinfo[0].stem
    return pid, pcwd, sid

# The SAME canonical heading `hooks/stop-check-prose-violations.sh`'s own
# `IS_COMPLETION_HEADING` classifier anchors on (`^## ✅ Work Complete|^✅
# Work Complete`). KEPT after #599 (compact itself no longer reads it — the
# `⏳` exemption that used it is gone), because `watchdog/cards.py::
# report_boundary_after` (job 25) still imports it FROM HERE so a genuine
# report is read by the identical rule that enforces it must be genuine, never
# a parallel, independently-drifting spelling.
_COMPACT_COMPLETION_HEADING_RX = re.compile(
    r"(?m)^(?:## )?✅ Work Complete\b")

# #1084 L2: the compact REQUEST store this constant used to age out is DELETED —
# it is NO LONGER a request TTL. Its ONLY surviving role is the upper CLAMP
# ceiling for `_compact_recent_human_window` (below): a `AIRULESET_COMPACT_RECENT_
# HUMAN_S` env value >= this is clamped to `this - 1` so a misconfigured window
# can't lapse the recent-human veto. 30 min is retained as that clamp bound.
COMPACT_REQUEST_MAX_AGE_S = 30 * 60

# #377 — never deliver into a live human Q&A window with THIS session.
# Reuses job 9's own dual-signal primitive rather than duplicating it.
COMPACT_RECENT_HUMAN_ACTIVITY_S = 120   # env AIRULESET_COMPACT_RECENT_HUMAN_S

_COMPACT_DISCORD_ANSWER_PREFIXES = (
    "Odpoveď z Discordu:", "Odpoveď užívateľa na tvoju otázku")

def _compact_recent_human_window(window_s=None):
    """An explicit `window_s=` is returned verbatim. The CONSTANT/ENV
    default is clamped to `[1, COMPACT_REQUEST_MAX_AGE_S)` so a
    misconfigured env var can neither silently disable the veto (0/
    negative) nor recreate a lapse-before-clear starvation (a value at or
    above the request TTL)."""
    if window_s is not None:
        return window_s
    try:
        raw = int(os.environ.get("AIRULESET_COMPACT_RECENT_HUMAN_S",
                                 COMPACT_RECENT_HUMAN_ACTIVITY_S))
    except ValueError:
        raw = COMPACT_RECENT_HUMAN_ACTIVITY_S
    if raw < 1:
        return 1
    if raw >= COMPACT_REQUEST_MAX_AGE_S:
        return COMPACT_REQUEST_MAX_AGE_S - 1
    return raw

def _compact_recent_human_activity(cwd, sid, now, projects_dir=None, window_s=None):
    """True when the user has been active on THIS session within
    `window_s` seconds. Delegates to `_goal_autoarm_recent_human_activity`
    (a general, goal-owned dual-signal primitive: the UserPromptSubmit
    presence marker OR the transcript's own last human-prompt timestamp),
    passing the Discord-answer prefixes so a relayed answer counts as
    recent human activity here too. Unmeasurable never blocks."""
    pdir = projects_dir or watchdog.PROJECTS_DIR
    tpath = watchdog._transcript_for_session(pdir, sid, cwd)
    win = _compact_recent_human_window(window_s)
    recent, _reason = watchdog._goal_autoarm_recent_human_activity(
        sid, tpath, now, window_s=win,
        extra_human_prefixes=_COMPACT_DISCORD_ANSWER_PREFIXES)
    return recent

def compact_sync_log_path():
    """`~/.claude/compact-sync.log`, resolved at CALL time."""
    return Path.home() / ".claude" / "compact-sync.log"

def pending_compact_hold(sid, now=None, hold_s=None, path=None):
    """#1084 L2: machine-triggered compacts are REMOVED, so NO `/compact` is ever
    pending — this bounded writer-side latch (the gate the work-pushing riders in
    `watchdog/goal.py`, `lane_reconcile.py`, `u_freshness.py`,
    `queue_arrival_recheck.py`, `ops_wait_recheck.py` consult before pushing
    work) now returns False UNCONDITIONALLY. The request store it used to read is
    deleted; before L2 it already returned False for every session once L1 left
    the store unwritten, so this is fail-open exactly as before — a rider never
    HOLDS on a compact that can no longer exist. Kept as a symbol (signature
    unchanged: `now`/`hold_s`/`path` accepted and ignored) so those riders need
    no change. Historical detail: the #741 bounded-latch / #848 seconds-bound /
    #921 owner-flag-fail-open behaviour is subsumed by "there is no store"."""
    return False

def compact_sweep(now, run=None, dry_run=False, projects_dir=None):
    """REMOVED (#1084, 2026-09-19, owner ROZHODNUTE): machine-triggered compacts
    are gone for good -- "uz tie compacty vobec nechcem ... samotne deploye na
    targety by to mali zabezpecit". No pending request is ever delivered, no
    owner flag is read, no enable path exists; Claude Code's own threshold
    autocompact is the ONLY compaction left. The job-14 slot stays addressable
    and journals ONE removed line each sweep, so `journalctl` proves the
    fleet-wide guarantee on every box with no per-box marker to miss (the
    forestshop-dev slip that filed this ticket).

    L2 (#1084, this lane) DELETED `deliver_compact` and the whole
    request/delivered/queued store this early return orphaned, and trimmed the
    now-dead `requests_path`/`delivered_path`/`state`/`handled` params off this
    signature (the sole caller, `run_once`'s `_job_compact_sweep`, and the test
    callers all pass only `now`/`run`/`projects_dir`). The compaction OBSERVATION
    helpers (`_pane_compacting`, `COMPACTING_MARKER`, the transcript compaction
    records lane-reconcile / the goal jobs read) live in `watchdog/long_turn.py`
    and are untouched.
    """
    return ["compact: machine compacts removed (owner 2026-09-19, #1084) "
            "— native autocompact only"]
