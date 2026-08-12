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
occurred. #400 (2026-08-12) proved there are exactly two such entities —
the session itself (`compact-request --self`) and the SubagentStop event
when an autopilot-worker returns — and retired the third (text-sniffing)
outright. Once the boundary is always TRUSTWORTHY, the guessing-era
scaffolding is dead weight: this file is what is left once it is removed.

THE MODEL (owner's own words): "session zavolá, systém overí, napíše
/compact, zaloguje" — the session calls, the system verifies, it types
`/compact`, it logs the outcome. Concretely:

  INPUT   — exactly two origins create a pending request (below):
            `record_compact_request(..., origin="self-callback")` from
            `airuleset.py compact-request --self`, and
            `record_compact_request(..., origin="subagent-stop")` from
            `hooks/notify-compact-subagent-boundary.sh`. Nothing else.

  DELIVERY — ONE function, `deliver_compact()`. It checks, in order:
            (a) the pane is idle, with no unsent draft and no open dialog;
            (b) the session has no live background tasks of its own;
            (c) the session's last real turn is not a `⏳`/`❓` marker, AND
                is not stuck on an unread API error (#188 — a proven
                boundary whose worker result the supervisor has
                demonstrably not consumed yet);
            (d) at least `COMPACT_MIN_DELIVERY_INTERVAL_S` (30 min) has
                passed since the last REAL `/compact` delivered to this
                session;
            (e) the request itself is not older than
                `COMPACT_REQUEST_MAX_AGE_S`, anchored at FIRST-seen —
                never refreshed by a later re-record (the exact #400 fix:
                a refreshable anchor is how an 11-hour-stale request once
                survived).
            Two more checks ride along, both closing real previously-live
            production incidents scoped to the two proven origins, not
            merely the 5 named above: the user must not be actively
            engaged with this session right now (#377), and — on the
            record-time synchronous attempt only — the request must be at
            least `COMPACT_MIN_REQUEST_AGE_S` (2s) old, a fixed floor (NOT
            a loop) against the same-turn-dispatch race (#238): a sibling
            worker dispatched in the very same turn can be briefly
            invisible to the live-tasks signals.
            Every condition above is an UNCONDITIONAL hard skip — none of
            them has a TIME-BOXED override any more (the #400 fix this
            collapse builds on removed the two that used to: the
            live-tasks defer's "deliver anyway past grace" escape, and the
            `⏳`/`❓` marker's "hold-grace" escape). Conditions (b) and (c)
            DO carry one narrow, EVIDENCE-based (never time-boxed)
            exemption (#425): when `origin` is `self-callback` AND the
            SAME last-real-turn text that carries the `⏳` marker ALSO
            contains the canonical `## ✅ Work Complete` heading, both
            checks stand down for THAT evaluation — see
            `_compact_self_reported_complete`'s own docstring for the full
            reasoning (a fable-advisor-reviewed decision) and its
            deliberately-accepted residual. `❓` is NEVER exempted, under
            any origin or any content, ever (#333). All pass → type
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

  LOGGING — every decision (SEND or SKIP) is appended to
            `compact-sync.log` via `_log_compact_sync`, unconditionally,
            from the ONE place `deliver_compact` makes the call — never
            two different call sites that could drift out of sync with
            each other (the #400-review MINOR-6 bug this module does not
            re-introduce: the SEND line is written IMMEDIATELY after the
            keystrokes are typed, before any other state write, so a
            later exception can never leave a real send unlogged).

WHAT WAS DELETED, not "kept as dead code" (see the design comment on
issue #402 for the full function-by-function accounting): the claim/lock
file and its process-fingerprint liveness check (protected against
MULTIPLE senders racing a pane — impossible once exactly two,
mutually-exclusive call sites exist); the "substantiality" gate (did >=1
git commit land — a heuristic guess at a boundary the two origins already
prove structurally); the "thin context" gate; a dedicated stall-watch
job (it only ever watched the claim file, which no longer exists); the
two grace-window overrides named above; the retry-until-hold loop and its
six tuning constants; the stash-around-a-draft delivery variant; and the
msg-hash-based delivered-dedup layer (it only ever deduped repeated
TEXT-SNIFFED fires of an unchanged message — that trigger channel is
gone).

MODULE-IMPORT SAFETY. `watchdog/__init__.py` never imports this file at
ITS OWN module level — matching the existing `notify`/`burn` convention
this codebase already uses for every sibling package, callers reach this
module with a LAZY `from watchdog import compact` (or `import
watchdog.compact`) inside a function body (`run_once`, `cmd_compact_
request`), only at the point they actually need it. Because of that, this
file's own `import watchdog` (never `from watchdog import <name>`) is
always safe: by the time anything imports `watchdog.compact` at all,
`watchdog/__init__.py` has already finished executing top to bottom (its
own module-level code has to run to completion before any of ITS function
bodies — the only place a lazy import lives — can even be called). A bare
`import watchdog` here also keeps a test's
`unittest.mock.patch.object(watchdog, "capture_pane", ...)` working
exactly as it always has — a `from watchdog import capture_pane` binding
would silently stop seeing such a patch.
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
    """Record a pending `/compact` request for `session` — called ONLY by
    the two proven origins (`compact-request --self` /
    `compact-request --record --origin subagent-stop`). Overwrites any
    earlier pending request for the SAME session, except its `ts`: `ts` is
    set ONCE, on the call that CREATES the entry, and preserved
    UNCONDITIONALLY on every later re-record for the same still-pending
    session (`cwd` always takes the newest call's value). This is
    the #400 non-refreshable age-cap invariant — a request whose anchor
    can be refreshed by an ordinary re-record never actually ages out, no
    matter how stale it genuinely is. Fail-safe (never raises). Returns
    True on success.

    ORIGIN NEVER DOWNGRADES FROM `self-callback` (#402-review MAJOR-1).
    The SubagentStop hook fires under the SUPERVISOR's own sid on every
    worker return — routine in #317's parallel-round shape — and can
    re-record a STILL-PENDING self-callback request with
    origin="subagent-stop" (or a blank/None origin) before it is ever
    delivered. `origin` otherwise "always takes the newest call's value"
    (per the paragraph above, unchanged for every OTHER pairing), which
    would silently strip the #425 exemption from a turn that genuinely
    earned it (own "## Work Complete" heading + trailing "more workers
    still dispatched" tail) — live-confirmed on this box's own real
    `~/.claude/compact-requests.json`. A later self-callback re-record
    still correctly UPGRADES a pending subagent-stop/blank origin (this
    is unaffected — the newest-value rule still applies in that
    direction); only a self-callback -> anything-else transition on an
    ALREADY-pending entry is refused.

    #402-review MINOR-2 was CONSIDERED and REJECTED here, deliberately: a
    re-record landing on an already-EXPIRED prior entry (>
    `COMPACT_REQUEST_MAX_AGE_S` old) does briefly inherit that dead
    anchor and get discarded on its OWN synchronous evaluation without
    ever really being evaluated — but only within the narrow window
    before `deliver_compact`'s condition (e) is next checked (a hard,
    unconditional, no-pane-resolution-needed FIRST check, in both the
    synchronous attempt and `compact_sweep`'s own ~60s cadence), which
    clears the stale entry outright. The NEXT genuine boundary event
    after that lands on a cleared entry and gets a genuinely fresh `ts`.
    A gated "reset ts when the prior entry is already expired" fix was
    drafted and then reverted: it directly reproduces a WEAKER form of
    the #400 bug it was meant to avoid (a session producing boundary
    events more often than `COMPACT_REQUEST_MAX_AGE_S` apart, forever,
    can keep resetting the anchor just past each expiry and never let
    condition (e) actually fire) and collides with this function's own
    pre-existing, deliberately-locked #400 regression test
    (`test_ts_is_never_refreshed_across_a_re_record`, which the fix
    cannot pass without weakening). The residual (a single record call's
    own synchronous attempt occasionally reporting a spurious "expired"
    inside that <=60s window) is self-healing and narrower than the
    reset would have been unsafe."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    d = load_compact_requests(path)
    prior = d.get(session)
    ts = prior.get("ts") if isinstance(prior, dict) and prior.get("ts") is not None \
        else int(now)
    new_origin = str(origin or "").strip()
    prior_origin = prior.get("origin") if isinstance(prior, dict) else None
    if prior_origin == _COMPACT_SELF_CALLBACK_ORIGIN \
            and new_origin != _COMPACT_SELF_CALLBACK_ORIGIN:
        new_origin = prior_origin
    d[session] = {"cwd": str(cwd or ""), "ts": ts, "origin": new_origin}
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

# `⏳` (still working) and `❓` (blocked on the user) both positively say
# "this is not a completed-ticket boundary" — `❓` is NEVER relaxed for
# either origin, at any content, ever (genuinely undurable state either
# way; see #333's own live forensic evidence for why an earlier per-origin
# relaxation was reversed). `⏳` gets exactly ONE narrow, evidence-based
# exemption — see `_compact_self_reported_complete` below (#425).
_COMPACT_NON_BOUNDARY_MARKERS = ("❓", "⏳")

# The SAME canonical heading `hooks/stop-check-prose-violations.sh`'s own
# `IS_COMPLETION_HEADING` classifier anchors on (`^## ✅ Work Complete|^✅
# Work Complete`) — reused here so a session's own report is read by the
# identical rule that enforces it must be genuine, never a parallel,
# independently-drifting spelling.
_COMPACT_COMPLETION_HEADING_RX = re.compile(
    r"(?m)^(?:## )?✅ Work Complete\b")


def _compact_transcript_completion_heading(tpath):
    """True when `tpath`'s own LAST REAL assistant turn (the same walk
    `transcript_last_marker` uses) carries the canonical `## ✅ Work
    Complete` heading anywhere in its full text — not just the tail 1-3
    lines `transcript_last_marker_line` trims to, since the heading sits
    at the TOP of a real completion report while the `⏳` tail this
    function is paired against sits at the BOTTOM of the SAME turn.
    Unreadable/missing transcript -> False, never guessed positive."""
    text = watchdog.transcript_last_assistant_text(str(tpath))
    return bool(_COMPACT_COMPLETION_HEADING_RX.search(text))


def _compact_self_reported_complete(origin, tpath):
    """The #425 exemption gate, shared by BOTH condition (c)'s `⏳` veto
    and condition (b)'s live-tasks veto (never condition (c)'s `❓` veto,
    which this function is never even consulted for — see
    `_compact_not_at_boundary` below).

    ROOT CAUSE. A supervisor session legitimately, per
    `message-status-marker.md`'s own contract, ends a turn with BOTH the
    canonical `## ✅ Work Complete` heading for a ticket that just
    genuinely finished (its own state already durable — a merged PR, a
    closed issue, nothing living only in this session's context) AND a
    trailing `⏳ WORKING: <N more workers still dispatched>` tail, because
    OTHER, INDEPENDENT parallel `autopilot-worker` subagents — each its
    own worktree, its own transcript, its own context that survives this
    session's own compaction untouched — are still running (#317's
    worktree-isolation model). The pre-#425 unconditional `⏳` veto
    (#333) could not tell that turn apart from a genuinely-unfinished
    mid-work `⏳` and deferred it FOREVER (repeated `SKIP not-a-boundary`
    every ~60s sweep until the 30-min request-age cap silently discarded
    it) — reproduced live on two boxes, 8x each.

    CHOSEN APPROACH (fable-advisor-reviewed, gate OPEN, #425): exempt
    BOTH condition (b) and condition (c) together, under the SAME single,
    narrow, EVIDENCE-based boolean — `origin == self-callback` AND the
    transcript's own LAST REAL turn (the exact turn carrying the `⏳`
    being evaluated) ALSO contains the completion heading. This is
    deliberately NOT the #394-style time-boxed "hold-grace, then deliver
    anyway" escape #402 removed — every operand is a byte-level fact of
    the transcript AS IT EXISTS at evaluation time, so the exemption is
    self-deactivating the moment the session's last real turn moves on to
    something without the heading. `origin` is checked FIRST (a cheap
    comparison) so a non-self-callback caller (`subagent-stop`, or the
    periodic sweep's own blank/`None` origin) never even pays for the
    transcript-text read — the exemption is trusted ONLY for the one
    caller whose own durability claim it is built to honour; a
    `subagent-stop` boundary is proven a completely different, unrelated
    way and never reads this narrow trust.

    Condition (b)'s two live-task signals (CC's own "Waiting for N
    background agents" pane text; a dispatched sibling's own
    `subagents/*.jsonl` freshness) are agent-dispatch SPECIFIC — they
    structurally cannot see, and never could, a generic background Bash
    job (e.g. a `run_in_background` CI-wait). So relaxing (b) here does
    not weaken any protection against that shape; it was never provided.
    The always-unconditional ~2s `COMPACT_MIN_REQUEST_AGE_S` floor against
    the #238 same-turn-dispatch race is UNTOUCHED by this exemption.

    REJECTED ALTERNATIVE: a content classifier trying to additionally
    distinguish "the ⏳ narrates an INDEPENDENT worker" from "the ⏳
    narrates THIS SAME reported ticket's own still-running work" (the
    "montalu3-class" case named in #425's own mandate) — e.g. sniffing
    the `⏳` text for a matching ticket number or words like "CI"/
    "verify". Rejected because it is BOTH unnecessary and unreliable: (a)
    that bad shape can only ever arise from a genuinely-BACKGROUNDED wait
    (a foreground poll could not have finished generating the report text
    at all, since the turn would not yet have ended), and for exactly
    that shape `ci-monitoring.md`'s own already-accepted doctrine already
    states the notification linkage MAY be lost across a compaction
    boundary and the established recovery is a routine re-derivation from
    the durable resource (e.g. `gh run view`) on the next turn — a known,
    bounded, ALREADY-accepted risk this repo treats as normal elsewhere,
    never as something needing new prevention machinery; (b) condition
    (b) never protected this specific sub-shape ANYWAY (agent-dispatch
    specific, as above), so the blanket pre-#425 `⏳` veto that DID defend
    it was a side effect of the very bug being fixed here, not a
    deliberate policy; and (c) free-prose keyword-sniffing (Slovak or
    English, arbitrary phrasing) is exactly the guessing-era scaffolding
    #402 deliberately removed — a sniff's false negatives would silently
    reopen #425 for real completed-ticket-with-parallel-workers turns,
    while its true positives buy nothing the existing recovery path
    doesn't already provide. So this fix DELIBERATELY delivers on the
    montalu3-class shape too — an explicit, test-locked, ACCEPTED
    trade-off, not an oversight (see
    `test_montalu3_class_same_ticket_background_wait_still_delivers_by_
    design` in `tests/test_compact.py`)."""
    if origin != _COMPACT_SELF_CALLBACK_ORIGIN:
        return False
    if tpath is None:
        return False
    return _compact_transcript_completion_heading(tpath)


def _compact_not_at_boundary(cwd, sid, projects_dir=None, origin=None):
    """Condition (c), first half — True when the session's CURRENT last
    real turn carries a `⏳`/`❓` marker. Unmeasurable (no transcript)
    never blocks. `❓` is UNCONDITIONAL — the #425 exemption below is
    never even consulted for it. `⏳` gets the narrow #425 exemption via
    `_compact_self_reported_complete` (see its own docstring)."""
    pdir = projects_dir or watchdog.PROJECTS_DIR
    tpath = watchdog._transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    marker = watchdog.transcript_last_marker(tpath)
    if marker not in _COMPACT_NON_BOUNDARY_MARKERS:
        return False
    if marker == "⏳" and _compact_self_reported_complete(origin, tpath):
        return False
    return True


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

_LIVE_BG_TASK_WINDOW_S = 120


def _session_has_live_bg_tasks(pid, sid, cwd, run, projects_dir=None, now=None,
                               captured=None, origin=None):
    """True when EITHER of two independent signals says this session
    still has background work in flight: (a) the pane's own capture shows
    CC's ambient "Waiting for N background agents" row, or (b) a sibling
    worker's own subagent transcript was written within the last
    `_LIVE_BG_TASK_WINDOW_S` seconds. Neither signal readable → False
    (deferral is an optimization of an already-real safety property,
    never itself a new way to block on "we don't know"). `captured`
    (optional): reuse an already-known capture rather than issuing a
    fresh tmux round-trip.

    #425: a genuine positive from EITHER signal is still subject to the
    SAME narrow, evidence-based exemption `_compact_not_at_boundary`
    applies to the `⏳` marker — see `_compact_self_reported_complete`'s
    own docstring for the full reasoning. The exemption check is
    deliberately consulted ONLY once a live task is already found (never
    up front) so the common no-live-tasks case pays nothing extra."""
    pdir = projects_dir or watchdog.PROJECTS_DIR

    def _resolve_tpath():
        try:
            return watchdog._transcript_for_session(pdir, sid, cwd)
        except Exception:
            _log.debug("compact: _transcript_for_session failed for sid %s", sid,
                      exc_info=True)
            return None

    live = False
    if pid:
        cap = captured
        if cap is None:
            try:
                cap = watchdog.capture_pane(pid, run, lines=40)
            except Exception:
                _log.debug("compact: capture_pane failed for pane %s", pid,
                          exc_info=True)
                cap = None
        if cap and watchdog._BG_AGENTS_WAIT_RX.search(cap):
            live = True

    tpath = None
    if not live:
        tpath = _resolve_tpath()
        if tpath is not None:
            now_ts = now if now is not None else time.time()
            live = bool(watchdog.subagent_active(str(tpath), now_ts,
                                                 _LIVE_BG_TASK_WINDOW_S))

    if not live:
        return False

    if tpath is None:
        tpath = _resolve_tpath()
    if tpath is not None and _compact_self_reported_complete(origin, tpath):
        return False
    return True


# --------------------------------------------------------------------------- #
# Condition (e), the hard age cap — and the small (2s) same-turn-dispatch
# race floor for the synchronous record-time attempt only (#238).
# --------------------------------------------------------------------------- #

COMPACT_TEXT = "/compact"
COMPACT_REQUEST_MAX_AGE_S = 30 * 60   # a request older than this is DISCARDED

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
    `_session_has_live_bg_tasks` already read False. A missing
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
    """Resolve the SINGLE current live pane hosting session `sid` — used
    by the `--record` (SubagentStop) origin and by the periodic sweep,
    neither of which has `$TMUX_PANE` available. Matches by TRANSCRIPT
    STEM, never by cwd alone. Ambiguous (0 or >1 matching pane) → None,
    so the caller leaves the request pending for the next sweep rather
    than risk typing into the wrong pane."""
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    matches = []
    for pid, pcwd in watchdog.list_claude_panes(run):
        tinfo = watchdog.find_active_transcript(projects_dir, pcwd)
        if not tinfo:
            continue
        tpath, _tmtime = tinfo
        if tpath.stem == sid:
            matches.append(pid)
    return matches[0] if len(matches) == 1 else None


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
# The ONE delivery function.
# --------------------------------------------------------------------------- #

def deliver_compact(sid, cwd, origin=None, run=None, projects_dir=None,
                    delivered_path=None, now=None, state=None,
                    request_ts=None):
    """Evaluate every delivery condition for `sid` ONCE and act. Called
    from BOTH entry points' own immediate synchronous attempt (`--record`/
    `--self`) AND from the periodic sweep (`compact_sweep`) — both thread
    the request's own non-refreshable `ts` anchor through as `request_ts`,
    which drives BOTH condition (e) (the hard age cap — REQUIRED, or a
    request evaluated only by the periodic sweep would never expire) and
    the #238 too-young floor (a no-op in practice at the sweep's own ~60s
    cadence, since real elapsed time almost always already clears it).

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
    projects_dir = projects_dir or watchdog.PROJECTS_DIR

    # Condition (e) — the hard, non-refreshable age cap. Checked first: an
    # expired request needs no pane resolution at all.
    if request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > COMPACT_REQUEST_MAX_AGE_S:
            _log_compact_sync("SKIP expired sid=%s cwd=%s" % (sid, cwd))
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

    # Condition (b) — no live background tasks of this session's own.
    if _session_has_live_bg_tasks(pid, sid, cwd, run, projects_dir=projects_dir,
                                  captured=captured, origin=origin):
        _log_compact_sync("SKIP live-tasks sid=%s cwd=%s" % (sid, cwd))
        return "skip:live-tasks"
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
    if _session_has_live_bg_tasks(pid, sid, cwd, run, projects_dir=projects_dir,
                                  captured=fresh, origin=origin):
        _log_compact_sync("SKIP live-tasks-raced sid=%s cwd=%s" % (sid, cwd))
        return "skip:live-tasks-raced"

    # Mark provenance BEFORE typing so the shared janitor (#372) can
    # recover a stuck send for THIS pane — a delivering job's own
    # bookkeeping is the janitor's only proof it may act on this pane's
    # content at all.
    watchdog._janitor_mark_watch(state, pid, now)
    watchdog.send_continue(pid, COMPACT_TEXT, run)
    # Log the send IMMEDIATELY, before any other write — an exception in
    # mark_compact_delivery_ts below must never leave a real send unlogged.
    _log_compact_sync("SEND sid=%s cwd=%s origin=%s" % (sid, cwd, origin or "-"))
    mark_compact_delivery_ts(sid, now=now, path=delivered_path)
    return "sent"


# The dispositions that fully HANDLE a request — the caller clears it
# rather than leaving it pending for the next sweep.
_COMPACT_TERMINAL_WORDS = frozenset(("sent", "expired", "already-queued", "cooldown"))

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

    WHY THE WAIT EXISTS AT ALL (#402-review MAJOR-1). The record and this
    attempt happen in the SAME call, so a request's age is provably ~0 at
    the instant `deliver_compact` would check it — without the wait, the
    min-age floor refuses EVERY fresh request unconditionally, silently
    deferring it to job 14's ~60s poll and reverting a previously-fixed,
    previously-MEASURED regression: the pre-#402 code's own
    `deliver_compact_record` docstring records "18 of 87 real sends on
    this box alone" took that slow path for want of exactly this (#238's
    own adversarial review, finding 🔴1). The floor's OWN purpose is real
    — a same-turn-dispatched sibling worker can be briefly invisible to
    `_session_has_live_bg_tasks` — and this wait is what gives that
    signal a genuine chance to catch up, exactly like the old retry loop
    did, without resurrecting the loop itself: the wait is computed from
    the REQUEST's own recorded `ts` (via a fresh `load_compact_requests`
    read, since a re-record may have preserved an already-old-enough
    anchor), so a re-record whose anchor already clears the floor sleeps
    zero.

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
                           state=state, request_ts=req_ts)
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
        # `request_ts` is the entry's own non-refreshable anchor -- REQUIRED
        # here so condition (e), the hard age cap, is actually enforced by
        # the sweep (a bug caught by this ticket's own tests: passing None
        # unconditionally would silently disable expiry for every request
        # that only ever gets evaluated by the periodic sweep). The #238
        # too-young floor derived from the SAME value is a no-op in
        # practice at ~60s sweep cadence, never a reason to withhold it.
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
            logs.append("LAPSE (compact-sweep) sid=%s (age > cap, discarded)" % sid)
        else:
            logs.append("SKIP (compact-sweep) sid=%s -> %s" % (sid, word))
    return logs
