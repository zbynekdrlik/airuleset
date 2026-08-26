"""watchdog/compact.py — the collapsed `/compact` callback-delivery model
(#402, riding on #400's safety baseline).

WHY THIS FILE EXISTS. Before #402 the equivalent logic was ~4500 lines
spread across `watchdog/__init__.py` (46 functions: a claim/lock file with
process-fingerprint liveness, a "substantiality" heuristic, a "thin
context" gate, two independent grace-window overrides, a retry-until-hold
loop, a stash-around-a-draft delivery variant, a msg-hash dedup layer, and
a dedicated stall-watch job) plus a matching multi-thousand-line test
suite. Every one of those pieces existed to compensate for the trigger
being an IMPLICIT, re-derived boundary GUESS (context size, idle
duration, then a Stop-hook text-sniff) rather than an EXPLICIT, deliberate
callback from the one entity that actually knows a ticket boundary
occurred. #400 (2026-08-12) proved there were exactly two such entities —
the session itself (`compact-request --self`) and the SubagentStop event on
an autopilot-worker return (that second producer was RETIRED by #610 — a
worker return is not the supervisor's ticket boundary under the fleet model;
see the INPUT note below, self-callback is now the sole producer) — and it
retired the third (text-sniffing) outright. Once the boundary is always
TRUSTWORTHY, the guessing-era scaffolding is dead weight: this file is what
is left once it is removed.

THE MODEL (owner's own words): "session zavolá, systém overí, napíše
/compact, zaloguje" — the session calls, the system verifies, it types
`/compact`, it logs the outcome. Concretely:

  INPUT   — the `self-callback` origin is now the SOLE production producer of
            a pending request: `record_compact_request(..., origin=
            "self-callback")` from `airuleset.py compact-request --self` (and
            the equivalent `--record --origin self-callback` fired by
            `hooks/stop-check-prose-violations.sh` at a `## ✅ Work Complete`
            report, issue 411's Stop-hook backstop). #610 RETIRED the
            `subagent-stop` PRODUCER: `hooks/notify-compact-subagent-boundary.sh`
            no longer records anything — under the FLEET model (issues 317/456)
            a worker RETURN is not the SUPERVISOR's ticket boundary (the serial
            integration is), so a per-return compact fired mid-flow (montalu6:
            5 mid-flow compacts, 0 Work-Complete between them). The delivery
            machinery below still RECOGNISES `subagent-stop` as a proven origin
            (harmless — no producer emits it any more; the delivery tests use it
            generically), so a re-enable is a one-line hook restore if the model
            ever reverts to workers that merge + report their own tickets.

  DELIVERY — ONE function, `deliver_compact()`. It checks, in order:
            (a) the pane is idle, with no unsent draft and no open dialog;
            (b) the session has no live background tasks of its own — neither a
                dispatched worker lane (`count_live_workers`) NOR a live
                `run_in_background` Bash job (#599: a START with no completion
                notification in the transcript tail; a `/compact` would orphan
                its completion notification, the #29193 class);
            (c) the session's last real turn is not a `❓` marker (blocked on
                the user — #333/#228; the `⏳` marker NO LONGER blocks, #599),
                AND is not stuck on an unread API error (#188 — a proven
                boundary whose worker result the supervisor has
                demonstrably not consumed yet);
            (d) at least `COMPACT_MIN_DELIVERY_INTERVAL_S` (30 min) has
                passed since the last REAL `/compact` delivered to this
                session;
            (e) the request itself is not older than
                `COMPACT_REQUEST_MAX_AGE_S`, whose `ts` REFRESHES both on every
                re-record (#599 supersede, REVERSING #400's non-refreshable
                anchor) AND on every structured live-own-task veto during the
                periodic sweep (#727 hold-extend), so the cap measures "time
                since the claim was last JUSTIFIED" (a genuine boundary OR a
                measured live own-task hold) — a busy/mid-batch loop holds until
                delivered, a gone-quiet session ages out after 30 min.
            Two more checks ride along, both closing real previously-live
            production incidents scoped to the two proven origins, not
            merely the 5 named above: the user must not be actively
            engaged with this session right now (#377), and — on the
            record-time synchronous attempt only — the request must be at
            least `COMPACT_MIN_REQUEST_AGE_S` (2s) old, a fixed floor (NOT
            a loop) against the same-turn-dispatch race (#238): a sibling
            worker dispatched in the very same turn can be briefly
            invisible to the live-tasks signals.
            Every condition above is an UNCONDITIONAL hard skip — none has a
            TIME-BOXED override. #599 removed condition (c)'s `⏳` veto
            ENTIRELY (and with it the self-callback-only #425 exemption and its
            `_compact_self_reported_*` machinery): a recorded boundary request
            already PROVES a boundary occurred, and a 24/7 loop moves on to `⏳`
            within seconds, so the marker is no longer a delivery proxy — the
            DIRECT live-work conditions ((a)/(b)) are. (#565 had already removed
            the same exemption from condition (b)'s live-tasks veto — a
            per-ticket boundary must never override a DETECTED live sibling
            lane; #599 finishes the job on (c).) `❓` is NEVER exempted, under
            any origin or content, ever (#333/#228 — the session is
            mid-decision; the pending question + the in-flight ticket the
            user's answer needs would be lost). All pass → type
            `/compact`, log `SEND`, clear the request. Any check fails →
            log `SKIP reason=<x>`, and the request is LEFT PENDING for the
            next periodic sweep (`compact_sweep`, below) to re-evaluate —
            except an EXPIRED request (condition e) or one that is already
            otherwise handled (already-queued, in cooldown), which is
            DISCARDED outright. "No infinite waiting" is satisfied by the
            hard age cap itself, not by refusing to re-evaluate — a
            request that arrives while a sibling task is still finishing
            (the common case on an autopilot batch) still gets compacted
            once the sweep observes a quiet moment, rather than being lost
            the instant the first synchronous attempt happens to race a
            live worker.

  LOGGING — every decision (SEND or SKIP) is appended to `compact-sync.log`
            via `_log_compact_sync`, unconditionally, from the ONE place
            `deliver_compact` calls it (the SEND line IMMEDIATELY after the
            keystrokes, before any other state write, so a later exception can
            never leave a real send unlogged — the #400-review MINOR-6 bug).

WHAT WAS DELETED, not "kept as dead code" (full function-by-function
accounting: the #402 design comment): the claim/lock file + its
process-fingerprint liveness; the "substantiality" and "thin context" gates;
the dedicated stall-watch job; the two grace-window overrides named above; the
retry-until-hold loop + its six tuning constants; the stash-around-a-draft
delivery variant; and the msg-hash delivered-dedup layer (all compensating for
the IMPLICIT-boundary-guess era the two proven origins replaced).

MODULE-IMPORT SAFETY. `watchdog/__init__.py` never imports this file at its
own module level (the `notify`/`burn` convention) — callers reach it via a
LAZY `from watchdog import compact` inside a function body. So this file's own
`import watchdog` (never `from watchdog import <name>`) is always safe:
`watchdog/__init__.py` has finished executing before any lazy import here can
be called. A bare `import watchdog` also keeps a test's
`patch.object(watchdog, "capture_pane", ...)` working (a `from` binding would
silently stop seeing the patch).
"""

import json
import logging
import os
import re
import time
from pathlib import Path

import watchdog

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# State — two files. `compact-requests.json`: the pending request per
# session. `compact-delivered.json`: the last REAL send time per session
# (condition (d)'s own store). Nothing else is needed.
# --------------------------------------------------------------------------- #

def compact_requests_path():
    """`~/.claude/compact-requests.json`, resolved at CALL time — never a
    frozen module-level constant (Path.home() must reflect the CURRENT
    $HOME at the moment of the call, not at import time)."""
    return Path.home() / ".claude" / "compact-requests.json"


def load_compact_requests(path=None):
    """{session_id: {"cwd":..., "ts":..., "origin":...}} — the pending
    `/compact` requests. {} on any error or missing file; never raises."""
    path = path or compact_requests_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_compact_requests(d, path=None):
    path = path or compact_requests_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def record_compact_request(session, cwd, now=None, path=None, origin=None):
    """Record / SUPERSEDE the pending `/compact` request for `session` — in
    production called ONLY via the `self-callback` proven origin
    (`compact-request --self` and the issue-411 `--record --origin
    self-callback` Stop-hook backstop); the `subagent-stop` PRODUCER was retired
    by #610, though the function and the delivery machinery still accept that
    origin generically (see the INPUT note in the module docstring). Overwrites
    any earlier pending request for the SAME session, INCLUDING its `ts` AND its
    `bts`: both are set to `now` on EVERY record (the #599 SUPERSEDE rule,
    REVERSING #400's non-refreshable anchor). `bts` is the ORIGINAL-boundary
    anchor for OBSERVABILITY only — a genuine re-record resets it (a new
    boundary), the #727 hold-extend `ts` refresh does NOT touch it, so the HOLD
    log line can report how long a claim has been held. Dedup is
    1-pending-per-session. `cwd` and `origin` take the newest call's value.
    Fail-safe (never raises). Returns True on success.

    WHY #400 IS REVERSED (design comment on #599). #400 made the anchor
    non-refreshable because the delivery logic then relied on the boundary
    MARKER still being present as a proxy for "still an appropriate moment".
    #599 replaces that proxy with the DIRECT delivery conditions (pane idle, no
    live worker, no live bg-bash, no draft, no recent-human, not `❓`), so an
    appropriate moment is checked AT DELIVERY and the age cap no longer has to be
    the safety mechanism. A recorded boundary request is now a STANDING claim: it
    HOLDS until delivered or SUPERSEDED, not discarded by an arbitrary timeout a
    24/7 loop's boundaries never align with (cambox: 244 SKIP / 0 SEND). The
    30-min cap stays but with `ts` refreshing (every genuine boundary #599, AND
    every structured live-own-task veto #727) it measures "time since the claim
    was last JUSTIFIED": a busy/mid-batch loop never expires (holds until
    delivered), a GONE-QUIET session ages out after 30 min. The #400 text-sniff
    trigger that could refresh forever is structurally gone (a permanent no-op);
    the only re-records now are genuine boundaries, which SHOULD supersede.

    ORIGIN takes the newest value (#599): the #402-era "origin never downgrades"
    protection is REMOVED with the #425 `⏳` exemption it guarded. Both record
    sites always pass a proven origin, so the stored origin stays proven and
    `_compact_session_unresumed`'s proven-origin gate keeps working across a
    self-callback <-> subagent-stop supersede (both proven)."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    d = load_compact_requests(path)
    d[session] = {"cwd": str(cwd or ""), "ts": int(now), "bts": int(now),
                  "origin": str(origin or "").strip()}
    return _save_compact_requests(d, path)


def clear_compact_request(session, path=None):
    """Remove one handled/stale request. Fail-safe. Returns True iff a
    request for `session` existed and was removed."""
    session = str(session or "").strip()
    if not session:
        return False
    d = load_compact_requests(path)
    if session in d:
        d.pop(session, None)
        return _save_compact_requests(d, path)
    return False


def _touch_compact_request_ts(sid, now, path=None):
    """#727 hold-extend: refresh ONLY the `ts` of `sid`'s existing pending
    request to `now` — `cwd`/`origin`/`bts` are left untouched, so the age cap
    measures "time since the claim was last JUSTIFIED", not "time since the last
    boundary". Fail-safe: a vanished entry (delivered/superseded between the
    sweep's delivery verdict and this call) is a no-op. Returns True iff an
    entry existed and the refreshed dict was written."""
    sid = str(sid or "").strip()
    if not sid:
        return False
    now = time.time() if now is None else now
    d = load_compact_requests(path)
    entry = d.get(sid)
    if not isinstance(entry, dict):
        return False
    entry["ts"] = int(now)
    return _save_compact_requests(d, path)


def compact_delivered_path():
    """`~/.claude/compact-delivered.json`, resolved at CALL time (same
    reasoning as `compact_requests_path()` above)."""
    return Path.home() / ".claude" / "compact-delivered.json"


def _load_compact_delivered(path=None):
    """{session_id: last_real_send_ts}. {} on any error/missing file."""
    path = path or compact_delivered_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_compact_delivered(d, path=None):
    path = path or compact_delivered_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def mark_compact_delivery_ts(session, now=None, path=None):
    """Record the wall-clock instant a REAL `/compact` send happened for
    `session` — called from the ONE place a send actually occurs
    (`deliver_compact`), immediately after the SEND log line. A blank
    `session` is a no-op. Fail-safe; returns True on success."""
    session = str(session or "").strip()
    if not session:
        return False
    now = now if now is not None else time.time()
    d = _load_compact_delivered(path)
    d[session] = float(now)
    return _save_compact_delivered(d, path)


COMPACT_MIN_DELIVERY_INTERVAL_S = 1800   # 30 min; env AIRULESET_COMPACT_MIN_DELIVERY_INTERVAL_S
COMPACT_MIN_DELIVERY_INTERVAL_MAX_S = 6 * 3600   # clamp ceiling


def _compact_min_delivery_interval(interval=None):
    """An explicit `interval=` (test/caller override) is returned verbatim.
    The CONSTANT/ENV-derived value is clamped to `[1,
    COMPACT_MIN_DELIVERY_INTERVAL_MAX_S]` so a misconfigured env var can
    never silently disable the throttle (0/negative) or make a busy
    session never compact again (an absurdly large value)."""
    if interval is not None:
        return interval
    try:
        raw = int(os.environ.get("AIRULESET_COMPACT_MIN_DELIVERY_INTERVAL_S",
                                 COMPACT_MIN_DELIVERY_INTERVAL_S))
    except ValueError:
        raw = COMPACT_MIN_DELIVERY_INTERVAL_S
    if raw < 1:
        return 1
    if raw > COMPACT_MIN_DELIVERY_INTERVAL_MAX_S:
        return COMPACT_MIN_DELIVERY_INTERVAL_MAX_S
    return raw


def compact_delivery_in_cooldown(session, now, path=None, interval=None):
    """True while `session`'s last REAL `/compact` send is still within
    `_compact_min_delivery_interval(interval)` of `now`. No recorded
    delivery at all reads as UNMEASURABLE → False (the throttle only ever
    engages once a real send has genuinely been observed)."""
    session = str(session or "").strip()
    if not session:
        return False
    ts = _load_compact_delivered(path).get(session)
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return False
    age = _safe_age(now, ts)
    if age is None:
        return False
    return age < _compact_min_delivery_interval(interval)


# --------------------------------------------------------------------------- #
# Decision log — the ONE forensic trail for every SEND/SKIP.
# --------------------------------------------------------------------------- #

COMPACT_SYNC_LOG_LINES_MAX = 2000


def compact_sync_log_path():
    """`~/.claude/compact-sync.log`, resolved at CALL time."""
    return Path.home() / ".claude" / "compact-sync.log"


def _log_compact_sync(line, path=None):
    """Best-effort append-only log line for every `/compact`
    delivery/skip decision — the ONE call site (`deliver_compact`) that
    ever writes here, so the SEND/SKIP trail can never drift between two
    different senders' own copies of this log again. Never raises.
    Bounded to the last `COMPACT_SYNC_LOG_LINES_MAX` lines. Collapses an
    identical repeat of the log's own last line (content only, ignoring
    the timestamp) into a timestamp refresh instead of a duplicate append
    — a bounded-retry caller hitting the SAME decision repeatedly should
    not spend the log's forensic window on copies of one fact."""
    path = path or compact_sync_log_path()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    existing = []
    try:
        existing = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    if existing and existing[-1].partition(" ")[2] == line:
        existing[-1] = "%s %s" % (ts, line)
    else:
        existing.append("%s %s" % (ts, line))
    existing = existing[-COMPACT_SYNC_LOG_LINES_MAX:]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(existing) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# The two proven origins, and the "not a safe boundary" checks (condition c).
# --------------------------------------------------------------------------- #

_COMPACT_PROVEN_BOUNDARY_ORIGIN = "subagent-stop"
_COMPACT_SELF_CALLBACK_ORIGIN = "self-callback"
_COMPACT_PROVEN_BOUNDARY_ORIGINS = frozenset(
    (_COMPACT_PROVEN_BOUNDARY_ORIGIN, _COMPACT_SELF_CALLBACK_ORIGIN))

# The ONLY marker that still vetoes condition (c) after #599: `❓` (blocked on
# the user) is genuinely undurable state — the session is mid-decision, and the
# pending question + the in-flight ticket the user's answer needs would be lost
# by a compaction (the #228 hazard) — so it is NEVER relaxed, under any origin
# or content, ever (#333's own live forensic evidence for why an earlier
# per-origin relaxation was reversed). The `⏳` marker NO LONGER vetoes (#599):
# a recorded boundary request already PROVES a boundary occurred (durable state
# in git/GitHub), and a 24/7 loop moves on to `⏳` within seconds; safety at the
# delivery instant is held by the DIRECT live-work conditions (pane idle/busy,
# no live worker, no live bg-bash, no draft, recent-human), not by this marker
# proxy — which only ever additionally blocked the idle-after-`⏳` case, exactly
# the case the owner wants compacted. Dropping the `⏳` veto for ALL origins
# unifies the former self-callback-only #425 exemption (point 4); its
# `_compact_self_reported_*` machinery is deleted with it.
_COMPACT_BLOCKING_MARKER = "❓"

# The SAME canonical heading `hooks/stop-check-prose-violations.sh`'s own
# `IS_COMPLETION_HEADING` classifier anchors on (`^## ✅ Work Complete|^✅
# Work Complete`). KEPT after #599 (compact itself no longer reads it — the
# `⏳` exemption that used it is gone), because `watchdog/cards.py::
# report_boundary_after` (job 25) still imports it FROM HERE so a genuine
# report is read by the identical rule that enforces it must be genuine, never
# a parallel, independently-drifting spelling.
_COMPACT_COMPLETION_HEADING_RX = re.compile(
    r"(?m)^(?:## )?✅ Work Complete\b")


def _compact_not_at_boundary(cwd, sid, projects_dir=None, origin=None):
    """Condition (c), first half — True ONLY when the session's CURRENT last
    real turn carries a `❓` marker (blocked on the user). #599: the `⏳` marker
    NO LONGER vetoes — a recorded boundary request PROVES a boundary occurred
    (durable state in git/GitHub), so delivery must not require the last turn to
    STILL be that boundary; a 24/7 loop moves on to `⏳` within seconds, and the
    idle-after-`⏳` case is exactly what the owner wants compacted. Safety at the
    delivery instant is held by the DIRECT live-work conditions (pane busy/idle,
    no live worker, no live bg-bash, no draft, recent-human), not by this marker
    proxy. `❓` STAYS an UNCONDITIONAL veto for every origin (#333/#228 — the
    session is mid-decision and the pending question + the in-flight ticket the
    user's answer needs would be lost by a compaction). Dropping the `⏳` veto
    for ALL origins unifies the former self-callback-only #425 exemption (point
    4); `origin` is kept for signature parity but no longer consulted here.
    Bounded read (`transcript_last_marker_bounded`, #599 perf — the whole-file
    marker read measured 1.17s on cambox's 670 MB transcript, fired every
    Work-Complete hook). Unmeasurable (no transcript) never blocks."""
    pdir = projects_dir or watchdog.PROJECTS_DIR
    tpath = watchdog._transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    return (watchdog.transcript_last_marker_bounded(str(tpath))
            == _COMPACT_BLOCKING_MARKER)


def _compact_session_unresumed(cwd, sid, projects_dir=None, origin=None):
    """Condition (c), second half (#188) — True when a PROVEN boundary's
    result has demonstrably not been consumed yet, because the session's
    last real turn died on an unread API error. Scoped to the proven-
    boundary origins on purpose: every other origin's request was
    justified by the supervisor's OWN `✅ DONE` turn, already consumed and
    reported. Self-healing: job 1's own auto-resume clears the error and
    the next sweep re-evaluates. Unmeasurable never blocks."""
    if origin not in _COMPACT_PROVEN_BOUNDARY_ORIGINS:
        return False
    pdir = projects_dir or watchdog.PROJECTS_DIR
    tpath = watchdog._transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    return bool(watchdog.transcript_last_error(tpath))


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


# --------------------------------------------------------------------------- #
# Condition (b) — no live background tasks of this session's own.
# --------------------------------------------------------------------------- #

# The freshness window for the STRUCTURED live-worker count (signal (b)).
# Mirrors the lane consumer's `GOAL_LANE_LIVE_WINDOW_S` (15 min) VALUE and its
# documented reasoning verbatim (#518): a live worker sitting in ONE long
# foreground tool call (a ~9-min CI-wait; a long test run) writes nothing to
# its subagent transcript for the duration, so the window must comfortably
# exceed the longest single tool call (the 10-min Bash timeout cap) or a busy
# worker is misread as dead. The pre-#565 value HERE was 120s — shorter than a
# single CI poll, which is how a saturated supervisor's ~10 live lanes read as
# zero and got auto-compacted (#565). Independently tunable, never the NAME.
COMPACT_LIVE_WORKER_FRESHNESS_S = 15 * 60


def _live_bg_tasks_detail(sid, cwd, projects_dir=None, now=None):
    """The compact ``agent-id(state)`` label(s) of this session's LIVE worker
    lane(s), joined by ",", or "" when none — condition (b) of `deliver_compact`:
    a fresh subagent lane the supervisor still owns means a `/compact` would
    orphan it (#565), and the DETAIL names it for the decision log (#605 thread
    3 — the incident was blind-diagnosed twice from a bare `SKIP live-tasks`).

    #605: the STRUCTURED `count_live_workers` signal is now the ONLY signal — the
    pane `_BG_AGENTS_WAIT_RX` scrape (former signal (a)) was REMOVED (no
    timestamp -> false positives at an idle prompt: the 02:50 incident on sid
    2d02a127, 30 min of false SKIP live-tasks with ZERO fresh lanes). It lost no
    coverage — a genuinely-live agent always has a fresh subagent transcript
    inside the 15-min window (> the 10-min Bash cap, #518) — and is the #486
    direction: replace a pane-render heuristic with structured transcript state.
    This same freshness partition is the #727 hold-extend WEDGE-BOUND: a wedged
    lane whose transcript stops growing goes stale -> stops reading live -> the
    veto stops firing -> the held claim stops refreshing -> the age cap resumes.

    Vetoes on ANY FRESH lane via the SAME `_LANE_NOT_LIVE_STATES` partition
    `lane_has_live_evidence` uses (`live_lane_labels`, single source of truth):
    a `live`/`wedged`/`unreadable` lane counts live (#565-review: a wedged lane
    pending job-1 auto-resume is recoverable in-flight work), a `finished` lane
    is EXCLUDED (#587). Unreadable → [] → "" (a deferral, never a new block on
    "we don't know").

    `count_live_workers` is disk-only and blind to a generic background Bash
    CI-wait — that is a SEPARATE compact-only signal (`_compact_live_bg_bash`,
    #599/#604), never folded in here (#565/#571 shared-primitive isolation)."""
    now_ts = now if now is not None else time.time()
    pdir = projects_dir or watchdog.PROJECTS_DIR
    # `count_live_workers` never raises (fail-safe to no lanes); its `on_warn`
    # is routed to debug log (not the default stderr).
    _count, evidence = watchdog.count_live_workers(
        pdir, cwd, sid, now_ts, COMPACT_LIVE_WORKER_FRESHNESS_S,
        on_warn=lambda msg: _log.debug("compact: count_live_workers: %s", msg))
    return ",".join(watchdog.live_lane_labels(evidence))


def _session_has_live_bg_tasks(sid, cwd, projects_dir=None, now=None):
    """True iff `_live_bg_tasks_detail` names any live worker lane — the bool
    form of condition (b). #605: STRUCTURED-only (the pane signal (a) was
    removed); see `_live_bg_tasks_detail` for the full reasoning."""
    return bool(_live_bg_tasks_detail(sid, cwd, projects_dir=projects_dir, now=now))


# --------------------------------------------------------------------------- #
# Condition (b), SECOND signal (#599) — no live `run_in_background` Bash job.
# --------------------------------------------------------------------------- #

# The bounded transcript-tail window for the bg-bash liveness read. TIGHT on
# purpose (200 entries ≈ the death-detector sibling window): an ABANDONED older
# bg job scrolls out of the window and stops vetoing (self-healing), while a
# genuinely-recent live job is still caught — measured on cambox, a trailing
# 200-entry window is free of open bg jobs ~71% of the time, so this veto never
# permanently blocks compaction. Byte-bounded (1MB seek) so the read stays
# ~0.005s even on a 670MB transcript. Both env-tunable (via
# ~/.claude/watchdog.env, #574), each floored so a misconfigured value can
# never silently disable the veto.
COMPACT_BG_BASH_TAIL_BYTES = 1_000_000   # env AIRULESET_COMPACT_BG_BASH_TAIL_BYTES
COMPACT_BG_BASH_MAX_ENTRIES = 200        # env AIRULESET_COMPACT_BG_BASH_MAX_ENTRIES


def _compact_bg_bash_window():
    """(tail_bytes, max_entries) for the bg-bash read — the constants or their
    env overrides, each floored to a positive minimum so a malformed / disabling
    value (0 / negative / unparseable) falls back to the constant, never off."""
    def _pos(env, const):
        try:
            v = int(os.environ.get(env, const))
        except ValueError:
            return const
        return v if v > 0 else const
    return (_pos("AIRULESET_COMPACT_BG_BASH_TAIL_BYTES", COMPACT_BG_BASH_TAIL_BYTES),
            _pos("AIRULESET_COMPACT_BG_BASH_MAX_ENTRIES", COMPACT_BG_BASH_MAX_ENTRIES))


def _compact_live_bg_bash(cwd, sid, projects_dir=None):
    """The live `run_in_background` Bash bgid(s) of the session's OWN transcript
    (a START with no later completion/kill in the bounded tail), joined by ",",
    or "" when none — so a `/compact` never orphans a live bg job's completion
    (#29193). #605: returns the DETAIL (not a bare bool) so the SKIP live-bg-bash
    decision log NAMES the job (thread 3), via `session_live_bg_bash_ids` (the
    id-returning sibling of the `session_has_live_bg_bash` bool). DELIBERATELY
    SEPARATE from the worker-lane `count_live_workers` signal
    (`_live_bg_tasks_detail`): a bg-bash job is NOT a worker lane, so the
    lane-occupancy (#571) / gk-fill consumers whose danger is UNDER-counting a
    live lane must never see it — keeping this in its own compact-only reader is
    what leaves them structurally unaffected. Unmeasurable (no transcript) → ""."""
    pdir = projects_dir or watchdog.PROJECTS_DIR
    tpath = watchdog._transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return ""
    tail_bytes, max_entries = _compact_bg_bash_window()
    return ",".join(watchdog.session_live_bg_bash_ids(
        str(tpath), tail_bytes=tail_bytes, max_entries=max_entries))


# --------------------------------------------------------------------------- #
# Condition (e), the hard age cap — and the small (2s) same-turn-dispatch
# race floor for the synchronous record-time attempt only (#238).
# --------------------------------------------------------------------------- #

COMPACT_TEXT = "/compact"
# A request whose `ts` is older than this is DISCARDED. KEPT at 30 min; its
# SEMANTICS measure "time since the claim was last JUSTIFIED" (NOT "time since
# first-seen" — #400's non-refreshable anchor is reversed). `ts` REFRESHES on
# a genuine boundary re-record (#599 supersede — `record_compact_request`) AND
# on a structurally-measured live own-task hold during the sweep (#727
# hold-extend — `_touch_compact_request_ts`). So a busy/mid-batch loop NEVER
# bites (the claim HOLDS until it delivers at the first safe moment, fixing
# cambox's 244 SKIP / 0 SEND and #727's mid-batch expiry); a GONE-QUIET session
# (neither fires) ages out after 30 min; a WEDGED lane goes stale -> the veto
# stops -> the hold stops -> expiry resumes (the wedge-bound). The "late
# inappropriate moment" hazard #400 guarded is now handled by the DIRECT
# delivery conditions (pane idle, no live worker/bg-bash, no draft/recent-human,
# not `❓`), never by this cap.
COMPACT_REQUEST_MAX_AGE_S = 30 * 60

COMPACT_MIN_REQUEST_AGE_S = 2.0   # env AIRULESET_COMPACT_MIN_REQUEST_AGE_S


def _safe_age(now, ts):
    """`now - ts` as a float, or None when either side is not a genuine
    number — the shared "unmeasurable, never guess" helper every age-based
    gate below uses instead of its own bare try/except."""
    try:
        return float(now) - float(ts)
    except (TypeError, ValueError):
        return None


def _compact_min_request_age(min_age=None):
    """An explicit `min_age=` is returned verbatim. The CONSTANT/ENV
    default falls back to the constant on a non-positive/unparseable
    override (a misconfigured env var must never silently disable this
    gate)."""
    if min_age is not None:
        return min_age
    try:
        raw = float(os.environ.get("AIRULESET_COMPACT_MIN_REQUEST_AGE_S",
                                   COMPACT_MIN_REQUEST_AGE_S))
    except ValueError:
        raw = COMPACT_MIN_REQUEST_AGE_S
    return raw if raw > 0 else COMPACT_MIN_REQUEST_AGE_S


def _compact_request_too_young(request_ts, now, min_age=None):
    """True when `request_ts` is too fresh for a "no live tasks" verdict
    to be trusted yet — closes the #238 same-turn-dispatch race (a
    sibling worker's own liveness signals can lag ~100ms behind its
    dispatch). Applied ONLY on the branch where
    `_live_bg_tasks_detail` already read no live lane. A missing
    `request_ts` (the periodic sweep, well past this floor by
    construction) is "not too young" — a complete no-op there."""
    if request_ts is None:
        return False
    age = _safe_age(now if now is not None else time.time(), request_ts)
    if age is None:
        return False
    return age < _compact_min_request_age(min_age)


# --------------------------------------------------------------------------- #
# Pane resolution for the two entry points.
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Submit verification (#375 part 2) — the compact counterpart of the
# swallowed-submit recovery goal/stash already have.
# --------------------------------------------------------------------------- #

# A bounded render-SETTLE poll, never a blind timeout — it returns the instant
# the box agrees. Generous on purpose: a FALSE "swallowed" reading (concluding
# the submit failed when it actually landed and CC just hadn't re-rendered yet)
# would fire the corrective Escape into a `/compact` that ALREADY started
# compacting, so the window must comfortably outlast the submit render-lag.
COMPACT_SUBMIT_SETTLE_POLLS = 6
COMPACT_SUBMIT_SETTLE_S = 0.3


def _compact_still_in_box(pid, run, sleep_fn):
    """True while `/compact` is STILL sitting in the input box after a submit
    (a swallowed Enter), False once it has left (submitted). Mirrors
    `goal._await_typed(want=False)`; reproduced here rather than cross-imported
    to keep compact.py's `import watchdog`-only module boundary (#433) — the
    same tiny render-settle idiom `_await_stash_settled` / `_undo_typed_text`
    already repeat."""
    for i in range(COMPACT_SUBMIT_SETTLE_POLLS):
        itext = watchdog._input_line_text(
            watchdog.capture_pane(pid, run, lines=40))
        if not watchdog._typed_landed(COMPACT_TEXT, itext):
            return False                       # gone -> submitted
        if i < COMPACT_SUBMIT_SETTLE_POLLS - 1:
            sleep_fn(COMPACT_SUBMIT_SETTLE_S)
    return True                                # still there -> swallowed


def _compact_submit_verified(pid, run, sleep_fn, log_fn):
    """Type `/compact` and submit it, VERIFYING the submit actually landed —
    the piece `send_continue` (type + Enter, no post-send read) never had, so a
    swallowed Enter (the agent-strip selector / menu overlay grabbing it, #36
    class) used to be reported "sent".

    Returns one of:
      "sent"        — the box no longer shows `/compact` (submitted).
      "swallowed"   — the Enter was swallowed even after ONE corrective
                       Escape+Enter (never a second Escape #35, never a second
                       bare Enter — the SAME shape `_send_goal_verified` /
                       `deliver_with_stash` use); the own typed text is
                       backspaced off the box and the caller LEAVES the request
                       pending for a clean retry instead of waiting on the
                       janitor.
      "raced-busy"  — a draft appeared in the box AFTER `deliver_compact`'s own
                       fresh recapture but BEFORE our type keystroke; NOTHING is
                       typed and the draft is rescued.

    The pre-type bare re-check mirrors `_send_goal_verified` (goal.py): the
    caller proved the box bare a moment ago, but a few tmux round-trips pass
    before the real type keystroke lands (the janitor mark, then
    `send_continue`'s own strip capture), and `send_continue` itself only checks
    strip-selection, never bare — so a draft that raced into that window would
    otherwise be typed over and, on a persistent double-swallow, later
    backspaced. Re-verifying + `_draft_rescue_persist` here narrows that window
    to APPROXIMATELY the sibling's — `_send_goal_verified` types via
    `_type_literal` immediately after its own bare re-check, whereas this routes
    through `send_continue` (one more `capture_pane` round-trip before the type),
    so compact's residual race window is marginally wider, still net-protected by
    the draft rescue + the janitor backstop. `_draft_rescue_persist`'s own
    docstring names this primitive's callers (`deliver_with_stash`,
    `_send_goal_verified`) as the ones that must call it first.

    `/compact` is 8 chars, far below `GOAL_TYPE_CHUNK_THRESHOLD` and sent by
    `send_continue` as one `-l --` burst, so the collapsed-paste shapes
    `_send_goal_verified` guards against cannot arise here. `log_fn(reason)`
    receives the raced-busy reason and/or the persistent-swallow undo result,
    mirroring `_undo_and_release_slot`'s own logging."""
    cap = watchdog.capture_pane(pid, run, lines=40)
    if watchdog._input_line_text(cap) != "":
        # Forward the rescue's OWN log lines through log_fn (parity with
        # `_send_goal_verified`, which passes `logs=logs`) — a rescue that FAILS
        # with a real draft in hand must never be silent (#271/#360).
        rescue_logs = []
        watchdog._draft_rescue_persist(pid, cap, logs=rescue_logs)
        for r in rescue_logs:
            log_fn(r)
        log_fn("compact-submit raced-busy: box not bare pre-send, not typed")
        return "raced-busy"
    watchdog.send_continue(pid, COMPACT_TEXT, run)
    if not _compact_still_in_box(pid, run, sleep_fn):
        return "sent"
    # Swallowed submit (#36 agent-strip-selector class) -- ONE corrective
    # Escape+Enter. The box holds ONLY our own `/compact` (verified bare above),
    # and a single Escape never deletes a CC draft (#35), so this only deselects
    # the strip / closes a menu, leaving the text for the Enter.
    run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    if not _compact_still_in_box(pid, run, sleep_fn):
        return "sent"
    # Still stuck. Backspace our own text off the bare-verified box so the next
    # sweep retries from a clean prompt; the janitor (job 20, provenance already
    # marked by the caller) is the backstop if this undo itself fails.
    watchdog._undo_and_release_slot(pid, run, COMPACT_TEXT, False, log_fn,
                                    "compact-submit swallowed", sleep_fn=sleep_fn)
    return "swallowed"


# --------------------------------------------------------------------------- #
# The ONE delivery function.
# --------------------------------------------------------------------------- #

def deliver_compact(sid, cwd, origin=None, run=None, projects_dir=None,
                    delivered_path=None, now=None, state=None,
                    request_ts=None, sleep_fn=None):
    """Evaluate every delivery condition for `sid` ONCE and act. Called
    from BOTH entry points' own immediate synchronous attempt (`--record`/
    `--self`) AND from the periodic sweep (`compact_sweep`) — both thread
    the request's `ts` anchor through as `request_ts`, which drives BOTH
    condition (e) (the hard age cap — REQUIRED, or a request evaluated only
    by the periodic sweep would never expire) and the #238 too-young floor
    (a no-op at the sweep's ~60s cadence, since real elapsed time clears it).

    Returns:
      "sent"            — `/compact` was typed.
      "expired"         — condition (e): the request is older than
                           `COMPACT_REQUEST_MAX_AGE_S`. Discard.
      "already-queued"  — the pane already holds an unexecuted `/compact`
                           (from an earlier attempt); nothing new sent,
                           but the request is fully handled. Discard.
      "cooldown"        — condition (d): a real send already happened for
                           this session too recently. A delayed send would
                           only ever fire STALE — discard, never hold.
      "skip:<reason>"   — not safe right now; the caller LEAVES the
                           request pending for the next periodic sweep.

    Every decision is logged via `_log_compact_sync` from this ONE call
    site, immediately after any real send and BEFORE any other state
    write, so a later exception can never leave a genuine send unlogged."""
    now = now if now is not None else time.time()
    if watchdog._owner_disabled("compact"):
        _log_compact_sync("SKIP disabled-by-owner sid=%s cwd=%s" % (sid, cwd))
        return "skip:disabled"
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep
    projects_dir = projects_dir or watchdog.PROJECTS_DIR

    # Condition (e) — the hard age cap. Checked first: an expired request
    # needs no pane resolution at all.
    if request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > COMPACT_REQUEST_MAX_AGE_S:
            # #523: name the ORIGIN on the discard record — post-#610 the sole
            # producer is `self-callback`, so a lapse now means a gone-quiet
            # session (no new boundary in 30 min AND no #727 live-task hold);
            # the #486 explicit-decision-log guardrail, logging-only (no
            # delivery decision changed).
            _log_compact_sync("SKIP expired sid=%s cwd=%s origin=%s"
                              % (sid, cwd, origin or "-"))
            return "expired"

    pid = _find_pane_for_session(sid, cwd, run=run, projects_dir=projects_dir)
    if not pid:
        _log_compact_sync("SKIP no-pane sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-pane"
    if watchdog.pane_in_mode(pid, run):
        _log_compact_sync("SKIP in-mode sid=%s cwd=%s" % (sid, cwd))
        return "skip:in-mode"
    captured = watchdog.capture_pane(pid, run, lines=40)
    if watchdog.pane_waiting_on_user(captured):
        _log_compact_sync("SKIP dialog-open sid=%s cwd=%s" % (sid, cwd))
        return "skip:dialog-open"

    # Condition (c) — not a safe boundary right now.
    if _compact_not_at_boundary(cwd, sid, projects_dir=projects_dir, origin=origin):
        _log_compact_sync("SKIP not-a-boundary sid=%s cwd=%s" % (sid, cwd))
        return "skip:not-a-boundary"
    if _compact_session_unresumed(cwd, sid, projects_dir=projects_dir, origin=origin):
        _log_compact_sync("SKIP unresumed-session sid=%s cwd=%s" % (sid, cwd))
        return "skip:unresumed-session"
    if _compact_recent_human_activity(cwd, sid, now, projects_dir=projects_dir):
        _log_compact_sync("SKIP recent-human sid=%s cwd=%s" % (sid, cwd))
        return "skip:recent-human"

    kind, draft = watchdog._classify_boundary(captured)
    if kind == "no-input-line":
        _log_compact_sync("SKIP no-input-line sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-input-line"
    if draft:
        _log_compact_sync("SKIP draft sid=%s cwd=%s" % (sid, cwd))
        return "skip:draft"
    if kind == "busy":
        # A short send-keys DOES queue reliably even into a busy pane, but
        # the queued `/compact` only DRAINS at whatever LATER turn's Stop
        # is first accepted — under an active loop that is almost always
        # either a real completion or a `❓`/`⏳`-blocked turn, exactly the
        # boundary this whole function exists to refuse (#333).
        _log_compact_sync("SKIP busy sid=%s cwd=%s" % (sid, cwd))
        return "skip:busy"
    if watchdog._pane_has_queued_compact(captured):
        _log_compact_sync("SKIP already-queued sid=%s cwd=%s" % (sid, cwd))
        return "already-queued"

    # Condition (b) — no live background tasks of this session's own. NOT
    # exempted by a Work Complete heading (#565): a per-ticket boundary does not
    # imply the sibling lanes finished. #605: STRUCTURED-only (the pane
    # `_BG_AGENTS_WAIT_RX` scrape was removed — it false-positived on stale
    # scrollback at an idle prompt), and the log NAMES the live lane(s) so the
    # veto is never blind-diagnosed again (thread 3).
    lanes = _live_bg_tasks_detail(sid, cwd, projects_dir=projects_dir)
    if lanes:
        _log_compact_sync("SKIP live-tasks sid=%s cwd=%s lanes=%s"
                          % (sid, cwd, lanes))
        return "skip:live-tasks"
    # Condition (b), SECOND signal (#599) — a live `run_in_background` Bash job
    # (invisible to `count_live_workers`) whose completion `/compact` would
    # orphan (#29193). Distinct reason + bgid so the forensic log keeps the two
    # apart AND names the job (#605 thread 3).
    bgjobs = _compact_live_bg_bash(cwd, sid, projects_dir=projects_dir)
    if bgjobs:
        _log_compact_sync("SKIP live-bg-bash sid=%s cwd=%s jobs=%s"
                          % (sid, cwd, bgjobs))
        return "skip:live-bg-bash"
    if _compact_request_too_young(request_ts, now):
        _log_compact_sync("SKIP too-young sid=%s cwd=%s" % (sid, cwd))
        return "skip:too-young"

    # Condition (d) — the 30-min per-session cooldown.
    if compact_delivery_in_cooldown(sid, now, path=delivered_path):
        _log_compact_sync("SKIP cooldown sid=%s cwd=%s" % (sid, cwd))
        return "cooldown"

    # Re-verify against a FRESH capture immediately before typing (#333) —
    # everything above spent real wall-clock time (a marker re-read, the
    # human-activity/live-tasks checks); the "only ever typed when the
    # pane is observably at rest RIGHT NOW" claim must be true at the
    # actual moment of the send, not just at this call's own entry.
    fresh = watchdog.capture_pane(pid, run, lines=40)
    fresh_kind, fresh_draft = watchdog._classify_boundary(fresh)
    if fresh_kind != "input" or fresh_draft:
        _log_compact_sync("SKIP raced sid=%s cwd=%s" % (sid, cwd))
        return "skip:raced"
    lanes = _live_bg_tasks_detail(sid, cwd, projects_dir=projects_dir)
    if lanes:
        _log_compact_sync("SKIP live-tasks-raced sid=%s cwd=%s lanes=%s"
                          % (sid, cwd, lanes))
        return "skip:live-tasks-raced"
    bgjobs = _compact_live_bg_bash(cwd, sid, projects_dir=projects_dir)
    if bgjobs:
        _log_compact_sync("SKIP live-bg-bash-raced sid=%s cwd=%s jobs=%s"
                          % (sid, cwd, bgjobs))
        return "skip:live-bg-bash-raced"

    # Mark provenance BEFORE typing so the shared janitor (#372) can
    # recover a stuck send for THIS pane — a delivering job's own
    # bookkeeping is the janitor's only proof it may act on this pane's
    # content at all.
    watchdog._janitor_mark_watch(state, pid, now)
    outcome = _compact_submit_verified(
        pid, run, sleep_fn,
        lambda reason: _log_compact_sync("%s sid=%s cwd=%s" % (reason, sid, cwd)))
    if outcome != "sent":
        # The submit was swallowed (agent-strip selector / menu overlay grabbed
        # the Enter, #36) or a draft raced into the box pre-send — either way the
        # `/compact` did NOT execute. Do NOT mark delivered or start the 30-min
        # cooldown (which would block the retry); LEAVE the request pending (a
        # non-terminal `skip:` word, so the caller does not clear it) for the
        # next sweep. On a swallow the helper already backspaced its own text
        # (janitor mark above is the backstop if that undo failed); on a race
        # nothing was typed at all.
        _log_compact_sync("NOT-DELIVERED sid=%s cwd=%s origin=%s reason=%s"
                          % (sid, cwd, origin or "-", outcome))
        return "skip:submit-%s" % outcome
    # Log the send IMMEDIATELY, before any other write — an exception in
    # mark_compact_delivery_ts below must never leave a real send unlogged.
    _log_compact_sync("SEND sid=%s cwd=%s origin=%s" % (sid, cwd, origin or "-"))
    mark_compact_delivery_ts(sid, now=now, path=delivered_path)
    return "sent"


# The dispositions that fully HANDLE a request — the caller clears it
# rather than leaving it pending for the next sweep.
_COMPACT_TERMINAL_WORDS = frozenset(("sent", "expired", "already-queued", "cooldown"))

# #727 hold-extend: the four STRUCTURED live-own-task veto words. While one is
# the SWEEP's verdict, the session is PROVABLY mid-batch (a measured live worker
# lane or a live run_in_background bash job vetoed delivery, correctly — CC
# #29193), NOT gone-quiet, so `compact_sweep` REFRESHES the request's `ts`. NEVER
# on `skip:busy` (a busy pane with no live task is a NEW turn whose boundary
# supersedes) and NEVER on a pane-render signal. A wedged lane goes stale -> the
# veto stops firing -> ts stops refreshing -> the age cap resumes (wedge-bound).
_COMPACT_HOLD_EXTEND_WORDS = frozenset((
    "skip:live-tasks", "skip:live-bg-bash",
    "skip:live-tasks-raced", "skip:live-bg-bash-raced"))

# A small margin over `COMPACT_MIN_REQUEST_AGE_S` so a `time.sleep()` that
# very slightly undershoots its requested duration (never observed on this
# stack, but no reason to shave it exactly to the boundary) still clears
# the floor on the very next `deliver_compact` check.
COMPACT_SYNC_ATTEMPT_MARGIN_S = 0.1


def _compact_sync_attempt(sid, cwd, origin, run=None, projects_dir=None,
                          requests_path=None, delivered_path=None,
                          state=None, now_fn=None, sleep_fn=None,
                          min_age=None):
    """The ONE synchronous delivery attempt BOTH `compact-request --self`
    and `--record` make, right after recording — records the request,
    then a single BOUNDED one-shot wait (never a retry loop; #402's
    collapse deliberately deleted the six-constant `_compact_retry_until`
    machinery), sized to just clear `deliver_compact`'s own
    `COMPACT_MIN_REQUEST_AGE_S` floor, then ONE call to `deliver_compact`.

    WHY THE WAIT EXISTS AT ALL (#402-review MAJOR-1). Record + attempt happen
    in the SAME call, so a request's age is ~0 when `deliver_compact` checks it;
    without the wait the min-age floor refuses EVERY fresh request and defers it
    to the ~60s sweep (the pre-#402 regression: "18 of 87 real sends" took that
    slow path). The floor's purpose is real — a same-turn-dispatched sibling
    worker can be briefly invisible to `_live_bg_tasks_detail` — and this wait
    gives that signal a chance to catch up without resurrecting the retry loop.
    The wait is computed from the REQUEST's own recorded `ts`; #599 sets `ts`
    to `now` on every record, so `req_ts` is ~now here and the fresh boundary
    sleeps the ~2s floor once (the #238 same-turn-dispatch race protection).

    Returns the disposition word `deliver_compact` returns (or
    `"skip:no-session"` if recording itself failed — a disk-write
    failure, not a blank session, which the caller has already refused
    before ever reaching here). Clears the request on any TERMINAL word.
    Prints nothing — the caller owns stdout."""
    now_fn = now_fn or time.time
    sleep_fn = sleep_fn or time.sleep
    ok = record_compact_request(sid, cwd, now=now_fn(), path=requests_path,
                                origin=origin)
    if not ok:
        return "skip:no-session"
    entry = load_compact_requests(requests_path).get(sid) or {}
    req_ts = entry.get("ts")
    if req_ts is None:
        req_ts = now_fn()
    floor = _compact_min_request_age(min_age) + COMPACT_SYNC_ATTEMPT_MARGIN_S
    age = _safe_age(now_fn(), req_ts)
    if age is not None and age < floor:
        sleep_fn(floor - age)
    word = deliver_compact(sid, cwd, origin=origin, run=run,
                           projects_dir=projects_dir,
                           delivered_path=delivered_path, now=now_fn(),
                           state=state, request_ts=req_ts, sleep_fn=sleep_fn)
    if word in _COMPACT_TERMINAL_WORDS:
        clear_compact_request(sid, path=requests_path)
    return word


def compact_sweep(now, run=None, dry_run=False, projects_dir=None,
                  requests_path=None, delivered_path=None, state=None,
                  handled=None):
    """The periodic re-evaluation of every PENDING request (the thinned
    replacement for the old job 14) — wired at the SAME `run_once()` slot.
    Re-checks each still-pending request's SAME unmodified conditions
    every sweep; nothing here overrides a hard condition, nothing mutates
    the request except via `deliver_compact`'s own clear-on-handled. A
    request that keeps failing a condition simply sits until either it
    clears (delivered next sweep) or the age cap (condition e) discards
    it — "no infinite waiting" is the age cap's job, not this loop's.

    `handled` (optional, a `set()`): every sid this sweep actually SENT to
    is added — job 20 (goal re-arm) reads this so it never types a
    keystroke burst into a pane that just received `/compact` this same
    sweep."""
    logs = []
    if watchdog._owner_disabled("compact"):
        logs.append("compact jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-compact (rm to re-enable)")
        return logs
    reqs = load_compact_requests(requests_path)
    for sid, entry in list(reqs.items()):
        if not isinstance(entry, dict):
            continue
        cwd = entry.get("cwd", "")
        origin = entry.get("origin") or None
        if dry_run:
            logs.append("DRY-RUN compact-sweep would evaluate sid=%s" % sid)
            continue
        # `request_ts` is the entry's own `ts` anchor -- REQUIRED here so
        # condition (e), the hard age cap, is actually enforced by the sweep
        # (passing None would silently disable expiry for every request only
        # ever evaluated by the periodic sweep). `ts` is REFRESHABLE (#599
        # supersede + #727 hold-extend); the sweep re-reads it each call, so the
        # hold branch below advances it. The #238 too-young floor derived from
        # the SAME value is a no-op at ~60s sweep cadence.
        word = deliver_compact(sid, cwd, origin=origin, run=run,
                               projects_dir=projects_dir,
                               delivered_path=delivered_path, now=now,
                               state=state, request_ts=entry.get("ts"))
        if word in _COMPACT_TERMINAL_WORDS:
            clear_compact_request(sid, path=requests_path)
        if word == "sent":
            logs.append("OK (compact-sweep) sid=%s -> sent" % sid)
            if handled is not None:
                handled.add(sid)
        elif word == "expired":
            # #523: name the ORIGIN on the LAPSE journal line too (the
            # `deliver_compact` sync-log record above already does). Post-#610
            # the sole producer is `self-callback`; a lapse now means a
            # gone-quiet session (no new boundary in 30 min, #599), not a
            # failure — the origin is what lets triage tell the two apart.
            logs.append("LAPSE (compact-sweep) sid=%s origin=%s "
                        "(age > cap, discarded)" % (sid, origin or "-"))
        elif word in _COMPACT_HOLD_EXTEND_WORDS:
            # #727 hold-extend: a structured live-own-task veto PROVES the
            # session is mid-batch -> REFRESH `ts` so the 30-min cap never
            # expires a still-held boundary out from under a >30-min batch.
            # `bts` (the ORIGINAL boundary) is untouched, so the line reports
            # how long the boundary has been held; a refresh WRITE failure (or a
            # vanished entry) logs HOLD-FAIL and behaves as an ordinary SKIP --
            # the next sweep re-tries the refresh.
            held = _safe_age(now, entry.get("bts"))
            held_s = "?" if held is None else "%d" % int(held)
            if _touch_compact_request_ts(sid, now, path=requests_path):
                logs.append("HOLD (compact-sweep) sid=%s -> %s "
                            "(ts refreshed, boundary held %ss)"
                            % (sid, word, held_s))
            else:
                logs.append("HOLD-FAIL (compact-sweep) sid=%s -> %s "
                            "(ts NOT refreshed)" % (sid, word))
        else:
            logs.append("SKIP (compact-sweep) sid=%s -> %s" % (sid, word))
    return logs
