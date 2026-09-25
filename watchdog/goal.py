"""watchdog/goal.py -- the collapsed `/goal` arming callback model (#403,
mirroring #402's `watchdog/compact.py`).

WHY THIS FILE EXISTS. Before #403 the equivalent logic was ~5000 lines
spread across `watchdog/__init__.py` (job 9's arm-question viewport-scan +
"virgin candidate" heuristic; job 20's ~1070-line `goal_rearm`, the single
biggest function in the file, orchestrating template-drift re-sync,
outage-vs-clear forensics, achieved-marker verification against a live `gh`
backlog, and a bounded attempt-cap re-arm loop) plus a matching ~8300-line
test suite. Every one of those pieces existed to compensate for the ARM
TRIGGER being a GUESSED boundary (a pane-content heuristic) rather than an
EXPLICIT callback from the one entity that actually knows "a real
`/autopilot` invocation just printed this goal line": #402 (2026-08-12)
proved the identical shape for `/compact`, and this file is what is left
once the same collapse is applied to `/goal`.

THE MODEL (owner's own words, #403): "prompt only when a real /autopilot
slash command actually ran and printed the goal line ... should also go
through a callback in the /autopilot command." Concretely:

  INPUT   -- the ONE HUMAN origin that creates a pending request is
             `record_goal_request(...)`, called from `airuleset.py
             goal-arm --self` -- the /autopilot skill's OWN Step 2, as its
             last tool call right after printing the `/goal` line for the
             user. Unlike compact's TWO origins (a per-TICKET-boundary
             SubagentStop hook plus a self-callback), goal-arming is a
             ONCE-PER-SESSION bootstrap event, not a per-ticket one -- there
             is no ticket-boundary-shaped signal that means "please arm my
             goal" the way a completed autopilot-worker ticket means
             "please compact." One HUMAN origin is honest, not a shortcut.
             (The watchdog LATER added AUTOMATIC re-arm origins that write the
             SAME store through the SAME deliver_goal gates: `dark-rearm`
             #478, `auth-rearm` #675, `fulfilled-rearm` #764, `answer-rearm`
             #890, `declared-virgin` #1038 -- see `_GOAL_WATCHDOG_REARM_ORIGINS`;
             none is a human arm, and EVERY one targets a DARK footer only. The
             `stale-rearm` #623 origin -- which alone typed a REPLACE into an
             ALIVE, armed loop -- is RETIRED (#1113, the >10x david1-3
             regression): an active loop is never touched by a keystroke; a
             template change waits for the next natural arm.
             Each remaining origin recovers a specific loop state the human would otherwise
             have to re-`/autopilot` by hand.)

  DELIVERY -- ONE function, `deliver_goal()`. It checks, in order: the
             owner kill-switch; a hard age cap non-refreshable by any
             AUTOMATIC path (#757: only a fresh human `/autopilot` re-record
             replaces the request with a fresh anchor, via
             `record_goal_request`'s user-callback allowlist) (an expired
             request pings once -- "arm failed, re-run /autopilot" -- since
             a silently-undeliverable arm request is a dark-autopilot
             failure, not a harmless drop like compact's); pane resolution;
             copy-mode; an open dialog; a request-scoped CLEAR-SUPPRESSION
             check (the newest transcript marker is `cleared` with a
             timestamp NEWER than this request -- drop, never retry: #170's
             guard, now trivial because there is no heuristic re-arm left
             to fight); a TRI-STATE
             already-armed check (`True` -> nothing to do, drop -- #1113
             UNCONDITIONAL for every origin, a keystroke NEVER reaches an
             armed footer; `None` -> undeterminable, leave pending; `False`
             -> proceed -- this is
             what makes a race between the callback and the user's own
             manual paste of the printed line benign, and protects a
             foreign manually-armed goal from being clobbered); a #1110
             TRANSCRIPT-LIVENESS gate (`watchdog.goal_turn_liveness`)
             BEFORE the render boundary -- a transcript written within
             GOAL_TURN_LIVE_WINDOW_S means the turn is still running (the
             render's bare box is a mid-turn frame, byte-identical to idle),
             so DEFER with zero keystrokes (`skip:busy-transcript`) rather
             than type a /goal into a live turn (the dev1 songplayer 22.9.
             swallowed-Enter + attempt-cap DROP); then the pane's boundary
             is classified and the payload is delivered via the SAME shared
             primitives every other keystroke-sending job in this file
             already uses (`deliver_with_stash` for a foreign draft,
             `_send_goal_verified` -- moved here verbatim, together with its
             `_await_typed` helper -- for a bare box). #1110: a keystroke
             whose arm never confirms while the transcript advanced during
             the confirm window is `skip:verify-failed-live` -- a mis-timed
             keystroke that never counts toward the attempt cap.

             Deliberately NOT gated on the TRANSCRIPT recent-human check
             (`_goal_autoarm_recent_human_activity`'s signals 1/2, #392/#398):
             the request's own origin IS the user having just typed
             `/autopilot`, so applying that 30-minute window to arm
             delivery would refuse essentially every legitimate arm for
             the first 30 minutes of every single invocation -- a
             structurally-always-refuses bug, not a safety net. That
             transcript gate stays exactly where it already earns its keep:
             the watchdog-INITIATED re-arm origins + the lane-occupancy
             nudge below. #731 added a pre-keystroke defer -- an ATTACHED
             tmux client's OWN input within a SEPARATE 5-min window (not
             the 30-min transcript one), the montalu4 blind spot -- but
             #752 SCOPED it to the SAME watchdog-INITIATED re-arm origins
             (owner ruling 2026-08-30): a `self-callback` arm is the owner
             having just typed `/autopilot`, so their keyboard presence is
             NEVER a reason to defer it, and it is NOT client-active
             vetoed here (`_goal_client_active_skip` is called only for
             `_GOAL_WATCHDOG_REARM_ORIGINS`). It is a zero-keystroke soft
             defer, never counts toward the attempt cap.

  RE-ARM  -- a genuine `/autopilot` invocation (the SAME `goal-arm --self`
             callback, called fresh), OR -- since #478/#524 -- the watchdog
             itself for a GENUINELY DEAD loop only. A user-CLEARED goal is
             NEVER re-armed (the mark != "set" gate). #403 originally left a
             dark/dead loop to a keystroke-free ping; #478 (owner, 2026-08-15)
             let `goal_dark_watch()` AUTO-RE-ARM a dark-DIED loop with a
             workable backlog by RECORDING a goal-arm request for job 9 to
             type; #524 (owner decision B, 2026-08-17) HARDENED that auto-type
             so it fires ONLY on a CONFIRMED death -- K clean-dark footer reads
             over >= MIN_SPAN, ANY armed/mtime-advanced read VETOING the run,
             under a 24h attempt cap -- and NEVER on an idle-but-ALIVE session
             (glyph merely flickering; montalu 2026-08-16). An idle/dark loop
             the watchdog cannot self-heal still gets the #459 ping, keystroke-
             free -- EXCEPT a 🏁-PROVEN achieved loop with a fresh open==0 cache
             (#766), which is FULFILLED, not dead, so the ping is VETOed. See
             `goal_dark_watch()` below.

  JANITOR -- the shared stuck-stash-slot recovery driver (`#372`,
             `watchdog._janitor_recover` -- renamed from
             `_goal_janitor_recover`, since it was NEVER goal-specific: job
             14's own `/compact` sends mark the SAME provenance dict this
             recovers, and had NO other caller) is still called, from the
             top of `goal_dark_watch()`'s own per-pane loop -- the one
             sweep still guaranteed to visit every live pane every ~60s
             regardless of whether any goal-arm request is pending, exactly
             matching job 20's old visit cadence. Deleting job 20's loop
             wholesale without keeping SOME caller of the recovery driver
             would have silently regressed job 14's own recovery too.

  CLEAR   -- `_clear_stranded_truncated_goal()` (#617), also called from
             `goal_dark_watch()`'s per-pane loop: it clears a STRANDED,
             TRUNCATED own `/goal` draft that the provenance-gated JANITOR
             above refuses (a later successful send cleared its watch mark).
             Ownership is proven by a byte-exact CONTIGUOUS-prefix content
             match against the pane's own template (>= GOAL_STRANDED_MIN_MATCH
             chars, reconstructed from every wrapped row -- NOT head+tail),
             gated on a clean input boundary + the fail-closed recent-human
             check + a bounded give-up. This is the ONE keystroke path in
             `goal_dark_watch()` itself (Escape+BSpace via
             `_janitor_clear_box`); the RE-ARM path only WRITES a request.

WHAT WAS DELETED, not "kept as dead code" (see the design comment on issue
#403 for the full function-by-function accounting): `goal_rearm` itself
(968 lines) and everything it orchestrated for the heuristic re-arm --
`_goal_stall_nudge`, `_goal_question_park_nudge`, `_goal_recover_untracked`,
`_goal_cleared_stale`, `_goal_dark_died_by_outage`, `_goal_blocked_on_
unanswered_question`, `_needs_you_block_ts`, `_goal_question_delivered_ts`;
job 9's own guessing machinery -- `_goal_autoarm_virgin_candidate`,
`_goal_template_drift`, `_goal_was_cleared_by_user`, `_goal_user_exit_ts`,
`_goal_autopilot_reinvoked_after`, `_foreign_transcript_goal`,
`_transcript_goal_line`, `_transcript_recently_asked_to_arm`,
`_viewport_goal_wrapped`, `_goal_never_armed`, `_entry_asks_to_arm`,
`_ARM_QUESTION_RX`; and the four template-DRIFT-only helpers
(`goal_template_norm`, `goal_template_hash`, `goal_template_variant`,
`load_goal_templates`, `_GOAL_TEMPLATE_RX`) -- replaced by ONE new function,
`goal_template_for_authority()`, since nothing needs "which of these N
templates does this text resemble" any more, only "give me the exact line
for authority X." The two `#402`-flagged compatibility stubs
(`compact_claim_active`/`compact_claim_set`) are also removed once nothing
calls them any more.

WHAT SURVIVES UNCHANGED in `watchdog/__init__.py` (shared, cross-job
infra, never goal-specific despite some of it living under a `goal_`-
prefixed name): `goal_templates_path` (the installed SKILL.md resolver),
`pane_goal_armed`, `scan_goal_markers`, `_goal_marker_content`,
`_parse_goal_marker` (the marker READER, still the primary source of
"intent" for the dark-watch), `_goal_autoarm_recent_human_activity`
(`watchdog.compact` itself delegates to this for ITS OWN keystroke-safety
gate), `deliver_with_stash`, `_owner_disabled`, and every plain pane/tmux
primitive (`capture_pane`, `pane_in_mode`, `pane_waiting_on_user`,
`_classify_boundary`, `_reconcile_candidate_panes`, `find_active_
transcript`, `send_continue`, `pane_owner`, `project_label`,
`_pane_location`, `_janitor_watch_seen`/`_janitor_mark_watch`/
`_janitor_clear_watch`/`_janitor_recover`).

MODULE-IMPORT SAFETY. Exactly like `watchdog/compact.py`, this module never
gets imported at `watchdog/__init__.py`'s own module level -- callers reach
it with a LAZY `from watchdog import goal` inside a function body
(`run_once`, `cmd_goal_arm`), only at the point they actually need it. This
file's own `import watchdog` (never `from watchdog import <name>`) is
always safe for the same reason `compact.py`'s is: by the time anything
imports `watchdog.goal`, `watchdog/__init__.py` has already finished
executing top to bottom.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import watchdog
from watchdog import compact as _compact
from watchdog import one_glance as _one_glance          # #486 G3
from watchdog import session_status as _session_status  # #486 G3 (reaper)
from watchdog import ops_wait_recheck as _ops_wait_recheck  # #547 (W re-check)
from watchdog import release_gap as _release_gap             # #616 (release gap)
from watchdog import queue_arrival_recheck as _queue_arrival  # #733 (gk arrival)
from watchdog import bounce_verdict_recheck as _bounce_verdict  # #1066 (bounce)
from watchdog import u_freshness as _u_freshness             # #797 (U reconcile)
from watchdog import lane_reconcile as _lane_reconcile       # #844 (post-compact lane reconcile)
from watchdog import nudge_gate as _nudge_gate               # #797 (cadence gate)
from watchdog import roster as _roster                       # #804 (armed roster)
from watchdog import resurrect as _resurrect                 # #804 (mode-5 relaunch)
from watchdog import goal_turn_liveness as _turn_liveness     # #1110 (transcript liveness)
from watchdog import gk_stall_notice as _gk_stall_notice      # #1109 (gk role-pane stall notice)
from watchdog import stream_migrate as _stream_migrate        # #1143 (dark stream loop re-arm)
from watchdog import send_outcome as _send_outcome            # #1157 (delivery outcome)
from watchdog.send_outcome import (  # noqa: E402 -- #1157: moved, ONE shared undo
    janitor_undo_if_own_stranded as _janitor_undo_if_own_stranded)


# --------------------------------------------------------------------------- #
# State -- two files. `goal-requests.json`: the pending arm request per
# session (carries the exact frozen payload text). No delivered-ts store is
# needed (unlike compact's 30-min cooldown) -- the tri-state already-armed
# check in `deliver_goal` already makes a double-send harmless: once a real
# arm lands, `pane_goal_armed` reads True and every later evaluation of the
# same (or a stale re-recorded) request drops as `already-armed`.
# --------------------------------------------------------------------------- #

def goal_requests_path():
    """`~/.claude/goal-requests.json`, resolved at CALL time -- never a
    frozen module-level constant, so a relocated `$HOME`/override is honoured on
    every call (the established resolve-at-call idiom across the watchdog)."""
    return Path.home() / ".claude" / "goal-requests.json"


def load_goal_requests(path=None):
    """{session_id: {"cwd", "ts", "origin", "authority", "text"}} -- the
    pending `/goal` arm requests. {} on any error or missing file; never
    raises."""
    path = path or goal_requests_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_goal_requests(d, path=None):
    path = path or goal_requests_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# The two proven writers of the goal-request store: the user's own
# `goal-arm --self` callback (origin "self-callback") and #478's
# `goal_dark_watch` auto-re-arm (this weak, watchdog-INITIATED origin).
_GOAL_REARM_ORIGIN = "dark-rearm"
_GOAL_SELF_CALLBACK_ORIGIN = "self-callback"
# #623 (RETIRED by #1113) -- once a watchdog-INITIATED re-arm of an ALIVE, armed
# loop whose stored condition had DRIFTED from the shipped template: it typed a
# fresh /goal into the LIVE box to REPLACE the stale one. The owner ruling
# 22.9.2026 (>10x reported): an ACTIVE loop is NEVER touched by a machine
# keystroke -- a template change waits for the NEXT NATURAL arm (session death /
# dark footer / the owner's own `/autopilot`). So the typing path is DELETED:
# `_stale_rearm_decide` now only OBSERVES the drift (one journal line), never
# records a request; this origin joins NEITHER `_GOAL_WATCHDOG_REARM_ORIGINS`
# NOR `_GOAL_USER_CALLBACK_ORIGINS` (it is no longer PRODUCED). The constant
# survives ONLY so `deliver_goal` can recognise a leftover on-disk request
# recorded before the retire and DROP it `drop:stale-rearm-retired`, never typed.
_GOAL_STALE_REARM_ORIGIN = "stale-rearm"
# #675 -- a watchdog-INITIATED re-arm of a loop CC cleared on a TRANSIENT auth
# failure (marker clear_kind="auth"). Delivered by the SAME channel + gates as
# dark-rearm, BUT its 30-min expiry is SILENT (owner ruling #662/#676: auth blips
# are NORMAL -> silence + mechanical recovery, never a "arm failed" ping); a
# genuinely-dead-loop dark-rearm still pings on expiry, which is a DIFFERENT
# class (a dead autopilot the owner must re-run), not an auth blip.
_GOAL_AUTH_REARM_ORIGIN = "auth-rearm"
# #764 -- a watchdog-INITIATED re-arm of a FULFILLED (stop-(B) completed) loop
# whose backlog has REFILLED: footer dark, mark=="set", a `🏁 BACKLOG EMPTY:`
# proof somewhere in the bounded tail (#767 backward scan / per-episode cache --
# the missing "fulfilled" marker CC never persists), obligation cache fresh with
# open>0. The cross-stream ping-pong re-entry the
# dead-loop self-heal (8 clean reads / 2-per-day) was never built for. Delivered
# by the SAME goal_sweep/deliver_goal channel + recent-human + all pane-safety
# gates -- NEVER a free-text nudge (owner directive #764). Rate-limited (min gap
# + daily cap per sid) since the 🏁 proof replaces the dark-duration confirmation.
_GOAL_FULFILLED_REARM_ORIGIN = "fulfilled-rearm"
# #890 -- a watchdog-INITIATED re-arm after the OWNER ANSWERED a `❓ NEEDS YOU`-
# blocked turn in a session that had an armed `/goal` (stop (A) disarmed it; CC
# never writes a `cleared` marker for this, so `mark` is still "set" with
# `armed is False`). The answer is UNAMBIGUOUS (a user message after the ❓ turn),
# so it shares the recovery-class relaxations (own rate state, compact-hold-exempt,
# stale-cache-tolerant). Delivered by the SAME goal_sweep/deliver_goal channel.
_GOAL_ANSWER_REARM_ORIGIN = "answer-rearm"
# #1038 -- a watchdog-INITIATED VIRGIN arm of a DECLARED managed window (gk
# review, gk-infra, d3 today — any box that declares `windows` in cli_fleet)
# that came back FRESH after a
# reboot: idle at its first prompt, DARK (never armed), no `Goal set:`/`cleared`
# marker, no pending request. Unlike every OTHER re-arm origin (which needs a
# PRIOR armed goal), this one bootstraps a NEVER-armed declared window so the
# owner never digs up / pastes a goal text after a reboot. `_declared_virgin_
# scan` (job 9) RECORDS it, `goal_sweep`'s per-request loop DELIVERS it the
# SAME sweep, and `deliver_goal` types it via the ALWAYS-ON `goal-arm` recovery
# nudge (a declared window is a session-revival surface, #1023). Honours the
# recent-human gate + silent expiry (a present owner is never pinged; the scan
# re-records next idle sweep). Rate-floored per sid (`state["goal_virgin_arm"]`).
_GOAL_DECLARED_VIRGIN_ORIGIN = "declared-virgin"
# The watchdog-INITIATED re-arm origins that honour deliver_goal's recent-human
# gate (never type into a pane a human just touched) — as opposed to the user's
# own `self-callback` arm, whose origin IS the user.
_GOAL_WATCHDOG_REARM_ORIGINS = (_GOAL_REARM_ORIGIN,
                                _GOAL_AUTH_REARM_ORIGIN,
                                _GOAL_FULFILLED_REARM_ORIGIN,
                                _GOAL_ANSWER_REARM_ORIGIN,
                                _GOAL_DECLARED_VIRGIN_ORIGIN,
                                _stream_migrate.ORIGIN)       # #1128
# #890 -- RECOVERY-class origins: events that are PROVEN (not guessed) — an auth
# clear is CC saying so, an answered-❓ is the transcript saying so. These are
# EXEMPT from the dead-dark attempt cap (they have their OWN rate states) and from
# the compact-pending hold (they only RECORD a request — a file write, not a
# keystroke; the hold exists to prevent a work-pushing nudge, not a recovery
# recording). A subset of `_GOAL_WATCHDOG_REARM_ORIGINS`.
_GOAL_RECOVERY_ORIGINS = (_GOAL_AUTH_REARM_ORIGIN, _GOAL_ANSWER_REARM_ORIGIN)
# #757 -- the genuine USER-callback origins: a request written because a HUMAN
# typed `/autopilot` (the only such origin today is `self-callback`, produced
# ONLY by the CLI `goal-arm --self` -- never auto-refired in a loop). This is
# the SYMMETRIC counterpart of `_GOAL_WATCHDOG_REARM_ORIGINS`: together the two
# tuples partition every origin into "a human made this request" vs "an
# automatic watchdog re-arm made it". `record_goal_request` refreshes the #400
# age-cap anchor ONLY for a user-callback origin (a human action is needed each
# time, so it can never drive the #400 refresh-forever shape); every automatic
# re-arm AND every unknown/empty origin PRESERVES the anchor (fail-safe: this
# is an ALLOWLIST, so a future automatic origin nobody classified defaults to
# preserve, never to refresh). RULE: every NEW origin string MUST be added to
# exactly ONE of these two tuples -- the ONE exception is a RETIRED origin no
# longer PRODUCED (`_GOAL_STALE_REARM_ORIGIN`, #1113): it is recognised only to
# DROP a leftover on-disk request, so it joins neither tuple and defaults to the
# fail-safe "preserve the anchor" branch above, exactly like an unknown origin.
_GOAL_USER_CALLBACK_ORIGINS = (_GOAL_SELF_CALLBACK_ORIGIN,)
# #766 -- a distinguishable THIRD return state from `_fulfilled_rearm_decide`
# (alongside handled True/False): a 🏁-PROVEN achieved loop with a FRESH open==0
# cache (backlog genuinely drained) is FULFILLED, not silently-dead, so the sweep
# site VETOes the #459 dead-loop ping instead of falling through to it. NOT an
# origin -- it never becomes a `record_goal_request` origin string, so it joins
# NEITHER partition tuple above (the "every NEW origin joins exactly one tuple"
# RULE does not apply to a sweep-site return sentinel). A truthy value so the
# caller must test `is _FULFILLED_SILENT` BEFORE the generic `if handled:`.
_FULFILLED_SILENT = "fulfilled-silent"
# #675 -- bumped whenever the marker PARSER gains a new recognizer. A persisted
# `state["goal_mark"]` entry stamped with an OLDER version is RESEEDED (first-
# sight reverse-scan) on the next dark_watch sweep, so a marker the old parser
# skipped-past (its incremental offset already advanced beyond it) is re-read by
# the new recognizer instead of the stale value persisting forever (#618 class).
_GOAL_MARK_PARSER_VERSION = 2


# #921 residual — the origin→state-dict mapping for attempt recording AFTER
# verified delivery. Each rearm origin records its attempts in a SEPARATE
# state dict; only "sent" deliveries are counted so undelivered attempts
# (blocked by busy-waiting starvation) never fill the rate cap.
_GOAL_ATTEMPTS_STATE_KEYS = {
    _GOAL_AUTH_REARM_ORIGIN: "goal_auth_rearm_attempts",
    _GOAL_ANSWER_REARM_ORIGIN: "goal_answer_rearm_attempts",
    _GOAL_REARM_ORIGIN: "goal_dark_rearm_attempts",
    # #1113 -- the stale-rearm origin is RETIRED (never typed, so it never
    # records a delivered attempt); its former "goal_dark_rearm_attempts" entry
    # is removed with the typing path.
    _GOAL_FULFILLED_REARM_ORIGIN: "goal_fulfilled_rearm",
}


def _record_delivered_attempt(state, origin, sid, now):
    """#921 residual: record a successfully-delivered attempt in the
    appropriate *_attempts state dict. Called from goal_sweep AFTER
    deliver_goal returns "sent" — never at decision/request time.

    A non-tracked origin (self-callback, etc.) is a no-op."""
    key = _GOAL_ATTEMPTS_STATE_KEYS.get(origin)
    if not key or not isinstance(state, dict):
        return
    d = state.setdefault(key, {})
    _day = 24 * 3600
    existing = d.get(sid)
    if isinstance(existing, list):
        pruned = [t for t in existing
                  if isinstance(t, (int, float))
                  and not isinstance(t, bool)
                  and 0 <= (now - t) <= _day]
    else:
        pruned = []
    d[sid] = pruned + [now]


def record_goal_request(session, cwd, text, authority, now=None, path=None,
                        origin=None):
    """Record a pending `/goal` arm request for `session`. MULTIPLE writers:
    the user's `goal-arm --self` callback (origin "self-callback", #478) AND
    the watchdog auto-re-arms — `dark-rearm` (#478), `auth-rearm` (#675),
    `fulfilled-rearm` (#764), `answer-rearm` (#890), `declared-virgin` (#1038).
    (#1113: `stale-rearm` #623 is RETIRED -- it is no longer recorded; a
    template drift on an ACTIVE loop is only observed, never re-typed.) Overwrites any earlier
    pending request for the SAME session, with two protections a single-writer
    store never needed (#478 adversarial-review MAJOR, mirroring the identical
    fix once applied to `compact.record_compact_request` — deleted by #1084):

      * DOWNGRADE REFUSED — a watchdog GUESS re-arm (`dark-rearm` #478, or
        `fulfilled-rearm` #764) NEVER overwrites a still-pending entry from a
        DIFFERENT origin. A pending request means a delivery is already being
        attempted; clobbering the user's own `self-callback` arm (or a sibling
        re-arm already in flight) with the watchdog's guess would replace its
        text/authority AND subject the user's explicit arm to the recent-human
        gate (which the active user always trips) -> silent expiry of the
        user's arm. The prior entry is kept entirely intact; a re-record of the
        SAME origin still updates in place.
      * `ts` is otherwise the #400 age-cap anchor: set ONCE on create and
        preserved on every AUTOMATIC (watchdog) re-record (a request whose
        anchor could be refreshed by an ordinary automatic re-record would
        never age out -- the anti-refresh-forever invariant). The exception
        (#757, generalizing the earlier dark->user UPGRADE) is any genuine
        USER-callback re-record (`new_origin in _GOAL_USER_CALLBACK_ORIGINS`):
        an explicit user `/autopilot` -- whether it lands on a pending
        watchdog `dark-rearm` OR on the user's own still-pending
        `self-callback` (the owner re-running /autopilot) -- is a genuinely
        new request and gets a FRESH ts, so its own 30-min window is never
        judged against a stale earlier anchor. It cannot drive the #400
        refresh-forever shape because a user callback needs a HUMAN action
        each time (the origin is produced ONLY by the CLI `goal-arm --self`,
        never auto-refired in a loop). This mirrors `_bump_goal_delivery_fail`,
        which already RESETS `dl_fails` on the same self->self re-record for
        the identical "a genuine fresh /autopilot is a NEW episode" reason.
        This is an ALLOWLIST (user-callback origins refresh; everything else
        -- every watchdog re-arm AND every unknown/empty origin -- preserves),
        so it fails SAFE for a future origin nobody classified. Note it is
        also STRICTER than the old `dark-rearm`-exclusion rule in one
        practically-unreachable direction: an `auth-rearm`/unknown (or a
        pre-#1113 leftover `stale-rearm`) origin landing over a `dark-rearm`
        prior used to get a fresh ts and now PRESERVES -- the correct safe
        direction (those are all automatic re-arms, and auth-rearm already
        defers to any pending request, so this path is not normally reached).

    `cwd`/`origin`/`authority`/`text` otherwise take the newest call's
    values. Fail-safe (never raises). Returns True on success (INCLUDING a
    refused downgrade, which is a successful no-op — the pending entry
    stands)."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    d = load_goal_requests(path)
    prior = d.get(session)
    new_origin = str(origin or "").strip()
    prior_origin = (prior.get("origin") if isinstance(prior, dict) else "") or ""

    # DOWNGRADE REFUSED: never let a WATCHDOG-guess re-arm (dark-rearm, or
    # #764 fulfilled-rearm) clobber a still-pending entry of a DIFFERENT origin
    # (the user's self-callback, or a sibling watchdog re-arm already being
    # delivered). A pending request means a delivery is in flight; clobbering it
    # would replace its text/authority AND re-subject a user's explicit arm to
    # the recent-human gate the user always trips -> silent expiry. Re-recording
    # the SAME origin (prior_origin == new_origin) still updates in place.
    if prior is not None \
            and new_origin in (_GOAL_REARM_ORIGIN, _GOAL_FULFILLED_REARM_ORIGIN,
                               _GOAL_AUTH_REARM_ORIGIN,
                               _GOAL_ANSWER_REARM_ORIGIN,
                               _GOAL_DECLARED_VIRGIN_ORIGIN,
                               _stream_migrate.ORIGIN) \
            and prior_origin != new_origin:
        return True                              # pending arm stands, untouched

    # #757 -- a genuine USER-callback re-record (dark->user UPGRADE OR
    # self->self owner-re-run-of-/autopilot) gets a FRESH ts anchor; every
    # AUTOMATIC watchdog re-arm AND every unknown/empty origin preserves the
    # #400 anti-refresh-forever anchor. ALLOWLIST => fails safe to preserve.
    new_is_user_callback = new_origin in _GOAL_USER_CALLBACK_ORIGINS
    if isinstance(prior, dict) and prior.get("ts") is not None \
            and not new_is_user_callback:
        ts = prior.get("ts")
    else:
        ts = int(now)
    d[session] = {
        "cwd": str(cwd or ""),
        "ts": ts,
        "origin": new_origin,
        "authority": str(authority or "").strip(),
        "text": str(text or ""),
    }
    return _save_goal_requests(d, path)


def clear_goal_request(session, path=None):
    """Remove one handled/stale request. Fail-safe. Returns True iff a
    request for `session` existed and was removed."""
    session = str(session or "").strip()
    if not session:
        return False
    d = load_goal_requests(path)
    if session in d:
        d.pop(session, None)
        return _save_goal_requests(d, path)
    return False


def _bump_goal_delivery_fail(session, word, path=None, field="dl_fails"):
    """#731 -- record ONE failed keystroke delivery on the pending entry: bump
    `field` (default `dl_fails`; #1110-review passes `dl_live_fails` for the
    separate verify-failed-live give-up counter) (INT-guarded across the JSON
    boundary, #714 -- a string value
    read back from a hand-edited/legacy store must never crash the sweep) and
    stamp `dl_last` with the skip word (the incident trail + the drop line's
    `last=<word>`). Owns the store mutation like `clear_goal_request`; the
    counter zaniká with the request and survives a watchdog restart. Fail-safe;
    returns the new count (0 if the entry vanished between load and bump).

    #731-review -- the counter is PER-REQUEST and a re-record RESETS it
    (`record_goal_request` rebuilds the entry dict without dl_fails). That is
    correct: a genuine fresh `/autopilot` is a NEW episode that must get its own
    cap. The only mechanical re-record (a dark-rearm; #1113 retired stale-rearm)
    is bounded 24h/2 AND
    gated on a CONFIRMED-dead read, and the cap fires in ~CAP sweeps (~3.5-4.6
    min) BEFORE a re-record can happen mid-livelock -- so a re-record never
    defeats the cap for the montalu4 case; carrying dl_fails across a same-origin
    self-callback re-record would instead risk prematurely capping a genuinely
    fresh user arm, so it is deliberately NOT carried forward. #757 aligned the
    #400 age-cap `ts` anchor with this same "new episode" model: a same-origin
    self->self re-record now ALSO resets `ts` (via `record_goal_request`'s
    user-callback allowlist), so `dl_fails` and `ts` are no longer
    inconsistent -- both reset on a genuine fresh /autopilot re-run."""
    session = str(session or "").strip()
    if not session:
        return 0
    d = load_goal_requests(path)
    entry = d.get(session)
    if not isinstance(entry, dict):
        return 0
    try:
        n = int(entry.get(field, 0)) + 1
    except (TypeError, ValueError):
        n = 1
    entry[field] = n
    entry["dl_last"] = word
    _save_goal_requests(d, path)
    return n


# --------------------------------------------------------------------------- #
# Template resolution -- replaces the four template-DRIFT-only helpers
# (`goal_template_norm`/`goal_template_hash`/`goal_template_variant`/
# `load_goal_templates`) that existed purely to answer "does this text
# resemble a known template" (#64), a question the collapse no longer needs
# to ask at all: the request already carries the exact frozen text.
# --------------------------------------------------------------------------- #

# Claude Code's own hard `/goal` condition cap (#169) -- a template whose
# extracted line exceeds this is refused at RESOLVE time, never typed and
# discovered rejected after the fact.
GOAL_ARM_CHAR_CAP = 4000

_GOAL_AUTHORITY_BLOCK_RX = __import__("re").compile(
    # #403-review: the goal LINE group used to be a greedy DOTALL `.+`,
    # which does not stop at the nearest closing fence -- it backtracks
    # from the END of the whole file, so the FIRST authority block
    # (`full`) swallowed all three templates as one 18k-char capture
    # (correctly refused by the char cap below, but for the wrong
    # reason: EVERY authority silently failed to resolve). `[^\n]+`
    # is deliberately non-DOTALL for this one group -- a real `/goal`
    # line is always exactly one physical line by construction, so this
    # is both the fix and a self-documenting invariant, immune to the
    # same greedy-DOTALL trap regardless of the outer `re.S` flag.
    r"\*\*AUTHORITY:\s*(\S+)\*\*.*?```\n(/goal [^\n]+)\n```", __import__("re").S)


def goal_template_for_authority(authority, path=None, logs=None):
    """The exact `/goal ...` line shipped for authority profile `authority`
    (full / branch-merge / fork-no-merge), read fresh from the INSTALLED
    autopilot SKILL.md (`watchdog.goal_templates_path()`) every call --
    never a stale copy, never guessed. Anchored on the `**AUTHORITY: <x>**`
    heading immediately preceding each template's own fenced code block in
    `skills/autopilot/SKILL.md`'s Step 2 -- robust against the three
    templates being reordered in the file, unlike the old drift machinery's
    file-order-index assumption.

    None if the file is unreadable, no block for that authority exists, OR
    the extracted line exceeds `GOAL_ARM_CHAR_CAP` (#169 -- a template that
    would be rejected by Claude Code itself must never be typed at all).

    #617 -- an over-cap refusal is now LOUD, not silent: pass an optional
    `logs` list (the dark-rearm path does) and an oversize template appends
    one line NAMING the cap breach before returning None, so a re-grown
    template (the #169 regression) surfaces in the watchdog log instead of
    silently disabling the whole autopilot loop. A pure resolution call
    (tests, the footer, every non-arm caller) passes nothing and is exactly
    byte-identical to the prior behaviour."""
    authority = str(authority or "").strip()
    if not authority:
        return None
    path = path or watchdog.goal_templates_path()
    try:
        with open(str(path), encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return None
    for m in _GOAL_AUTHORITY_BLOCK_RX.finditer(body):
        if m.group(1) == authority:
            line = m.group(2).strip()
            if len(line) > GOAL_ARM_CHAR_CAP:
                if isinstance(logs, list):
                    logs.append(
                        "goal-template REFUSED oversize authority=%s len=%d "
                        "cap=%d (#169 recurrence -- template re-grew over "
                        "Claude Code's /goal cap; never typed)"
                        % (authority, len(line), GOAL_ARM_CHAR_CAP))
                return None
            return line
    return None


def goal_template_for(authority, cwd, role=None, mode=None, path=None,
                      logs=None):
    """#998 — the `/goal` line for `authority` in the pane's resolved
    (mode, role). When `mode`/`role` are None they are resolved from `cwd`
    via `cli_concurrency.resolve_concurrency` (the SINGLE resolver every
    consumer reads). The DEFAULT (parallel, no role) delegates to
    `goal_template_for_authority` (fresh SKILL.md read, drift-locked ==
    the renderer's default); a VARIANT (sequential mode or infra role) is
    composed via the SAME `goal_registry.render_goal_line` the SKILL.md
    lines are generated from. None on any failure OR over the cap (never a
    wrong-authority / oversize arm), logged LOUD like the sibling."""
    authority = str(authority or "").strip()
    if not authority:
        return None
    if mode is None or role is None:
        try:
            import cli_concurrency
            r_mode, r_role, _src = cli_concurrency.resolve_concurrency(cwd)
        except Exception as e:  # noqa: BLE001
            if isinstance(logs, list):
                logs.append("goal-template concurrency-resolve-error (%r) — "
                            "falling back to default (parallel)" % e)
            r_mode, r_role = "parallel", None
        mode = r_mode if mode is None else mode
        role = r_role if role is None else role
    if mode == "parallel" and not role:
        return goal_template_for_authority(authority, path=path, logs=logs)
    try:
        import goal_registry
        line = goal_registry.render_goal_line(authority, mode, role)
    except Exception as e:  # noqa: BLE001
        if isinstance(logs, list):
            logs.append("goal-template render_goal_line(%s,%s,%s) failed: %r"
                        % (authority, mode, role, e))
        return None
    if len(line) > GOAL_ARM_CHAR_CAP:
        if isinstance(logs, list):
            logs.append("goal-template REFUSED oversize authority=%s mode=%s "
                        "role=%s len=%d cap=%d (never typed)"
                        % (authority, mode, role, len(line), GOAL_ARM_CHAR_CAP))
        return None
    return line


# --------------------------------------------------------------------------- #
# #623 -- STALE-ARMED-CONDITION classifier. A pure COMPARISON (never a
# heuristic): the stored marker `payload` vs the currently-shipped template
# line. The `header` clause opens every autopilot /goal condition, is identical
# across all three authority profiles, and was unchanged across the #621
# migration -- so a payload that normalizes to START with it IS one of our
# autopilot goals (of some version); one that does NOT is a FOREIGN goal the
# user armed by hand and must NEVER be clobbered. Signature drift-locked in
# tests against `goal_registry.render`.
# --------------------------------------------------------------------------- #

_GOAL_LINE_PREFIX = "/goal "
_AUTOPILOT_GOAL_SIGNATURE = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"


def _goal_condition_norm(s):
    """Whitespace-canonical form of a /goal condition for a STRUCTURAL equality
    compare: collapse every run of whitespace (incl. any CC soft-wrap newline)
    to one space, strip, and drop a leading `/goal ` if present. Deterministic
    and idempotent -- verified live (#623) that real stored markers carry NO
    wrapping, so this is exact today and defends against a future soft-wrap;
    NOT fuzzy -- a one-character clause change still compares unequal."""
    s = " ".join((s or "").split())
    if s.startswith(_GOAL_LINE_PREFIX):
        s = s[len(_GOAL_LINE_PREFIX):]
    return s


def _classify_armed_condition(payload, template_line):
    """Classify a session's ARMED /goal condition (`payload`, the stored marker
    text) against the currently-shipped template line -> one of:

      "current" -- normalizes byte-equal to the shipped condition; nothing to do.
      "stale"   -- an AUTOPILOT condition (opens with the signature) that DIFFERS
                   from the shipped one -> re-arm.
      "foreign" -- NOT an autopilot condition (a goal armed by hand) -> NEVER touch.
      "unknown" -- payload/template missing, or the shipped template itself does
                   not open with the signature (our signature drifted from the
                   template) -> disable detection, fail safe.

    A pure COMPARISON, not a heuristic: exact normalized equality decides
    current-vs-stale; the signature prefix decides ours-vs-foreign."""
    if not payload or not template_line:
        return "unknown"
    np = _goal_condition_norm(payload)
    nt = _goal_condition_norm(template_line)
    if not nt.startswith(_AUTOPILOT_GOAL_SIGNATURE):
        return "unknown"          # template self-check: signature has drifted
    if np == nt:
        return "current"
    if np.startswith(_AUTOPILOT_GOAL_SIGNATURE):
        return "stale"
    return "foreign"


# --------------------------------------------------------------------------- #
# Decision log -- the ONE forensic trail for every SEND/SKIP/DROP, mirroring
# `compact._log_compact_sync` exactly (same bounded-append, same repeat-
# collapse behaviour).
# --------------------------------------------------------------------------- #

GOAL_SYNC_LOG_LINES_MAX = 2000


def goal_sync_log_path():
    """`~/.claude/goal-sync.log`, resolved at CALL time."""
    return Path.home() / ".claude" / "goal-sync.log"


def _log_goal_sync(line, path=None):
    """Best-effort append-only log line for every `/goal` arm delivery/skip
    decision -- written by `deliver_goal` and, for the #617 over-cap refusal,
    by `_default_rearm_fn`. Never raises. Bounded to the last
    `GOAL_SYNC_LOG_LINES_MAX` lines. Collapses an identical repeat of the
    log's own LAST line (content only, ignoring the timestamp) into a
    timestamp refresh -- so a persistently-over-cap template logs one line
    while it stays the last line; an interleaved decision from another pane
    breaks that collapse, but the whole log is bounded to 2000 lines."""
    path = path or goal_sync_log_path()
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
    existing = existing[-GOAL_SYNC_LOG_LINES_MAX:]
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
# The hard age cap.
# --------------------------------------------------------------------------- #

GOAL_TEXT = "/goal"  # unused as a literal payload (goal's own text is the
                      # request's own frozen `text`) -- kept only as a
                      # documentation anchor for grep parity with compact's
                      # own `COMPACT_TEXT`.
GOAL_REQUEST_MAX_AGE_S = 30 * 60   # a request older than this is DISCARDED
                                   # -- and, unlike compact's, PINGED once:
                                   # an undeliverable arm request is a
                                   # silent-dead-autopilot failure class.

# #566 -- N consecutive IDENTICAL `stash-abort: slot occupied` aborts from the
# goal path is a LIVELOCK (our own park stale-occupies the single stash slot),
# not N independent transients: goal_sweep orders OWNED janitor recovery once
# the count reaches this, so the request never lapses in silence (montalu3
# 2026-08-19: 28 identical aborts over ~30 min, then LAPSE). Deliberately small
# -- a debounce against a passing state (a live turn, a momentary draft), not a
# long wait; the janitor's own provenance + own-content + recent-human gates
# make each ordered recovery safe.
GOAL_STASH_ABORT_LIVELOCK = 3
# #921 residual: the ESCALATION threshold for the persistent foreign-slot
# stash-abort livelock — after this many consecutive slot-occupied aborts
# (preserved across request lifetimes), emit a LOUD escalation log and stop
# re-ordering recovery (the janitor already tried and failed). Never auto-
# clear a possibly-human stash; only surface the problem for the owner.
GOAL_STASH_ABORT_ESCALATION = 6

# #731 -- the SHARED per-request delivery-attempt cap. The montalu4 retype
# livelock: `skip:verify-failed` / `skip:stash-abort` alternate with NO shared
# cap (the #566 `(n/3)` counter covers ONLY `slot-occupied`), so every sweep
# re-types ~3.4 kB into the prompt forever. This cap counts the KEYSTROKE-
# delivering skip WORDS below and DROPS the request terminally at the cap
# (cleaning up any leftover it stranded, B). Small, like #566's own debounce:
# 3 failed keystroke deliveries is a livelock, not 3 independent transients.
GOAL_DELIVERY_ATTEMPT_CAP = 3
# #1110-review -- a SEPARATE, LOOSER bound on `skip:verify-failed-live`. A
# keystroke whose arm never confirms while the transcript advanced during the
# confirm window is deliberately NOT counted toward the STRICT cap above (it may
# be a mis-timed keystroke into a genuinely live turn). But our OWN submit read
# as a plain prompt also advances the transcript identically (the #720
# silent-'sent' tail: box clears, a `user` turn is appended, the goal never
# arms), and that is a real failed delivery -- so a run of verify-failed-live
# must still hit a give-up bound, or an accept-as-plain-prompt livelock re-types
# a junk /goal every idle cycle until the 30-min age cap. > the strict cap so a
# genuine quiet+live-turn race gets more benefit-of-the-doubt (the pre-gate
# defers a live turn on most sweeps, so verify-failed-live is sparse for a real
# live turn but repeats for the accept-as-prompt class).
GOAL_DELIVERY_LIVE_ATTEMPT_CAP = 6
# The skip words that entered the TYPING protocol (real keystrokes, or the
# zero-keystroke stash-abort sub-tvars whose counting errs toward NOT typing --
# the #524-sanctioned "count confirmed records, fail SAFE"). NEVER counts
# `skip:stash-abort-slot-occupied` (owned by #566's own counter + janitor
# escalation) or any zero-keystroke defer (`undeterminable`/`busy`/`recent-
# human`/`client-active`/`in-mode`/... -- counting those would starve a
# legitimate delivery, #611). A structured return word, never a log-string match.
_GOAL_KEYSTROKE_SKIPS = frozenset(("skip:verify-failed", "skip:stash-abort"))

# REMOVED (#403-review CRITICAL C1): `_GOAL_NON_BOUNDARY_MARKERS` used to
# refuse to arm while the session's last transcript marker was a question
# or working marker. But the /autopilot bootstrap turn that RECORDS the
# arm request is itself what SETS that marker -- a real-corpus scan found
# it lands on one of those two shapes 98% of the time -- and the session
# then sits idle at that exact marker forever, so the gate refused its own
# trigger permanently: goal_sweep's periodic re-evaluation saw the SAME
# stale marker on every later sweep too, never just once. The gate
# protected nothing real: `_classify_boundary`/`pane_waiting_on_user`/
# `pane_in_mode` (below) already cover every genuinely unsafe pane state
# (can't locate the input box, an open dialog, copy-mode), and the
# template's own condition (A) is meant to refuse to let the loop proceed
# past an unanswered ❓ regardless of whether it's armed -- so arming
# during ❓/⏳ is harmless.
#
# #522 UPDATE (the premise "never unsafe" was too strong): the native `/goal`
# evaluator is an LLM and can IGNORE condition (A), re-poking an unanswered
# `❓ NEEDS YOU` turn-after-turn (the 17+ re-poke incident) -- so an armed loop
# at a ❓ IS a real failure mode, not "never unsafe". It is now BACKSTOPPED, not
# re-gated at arm time: `goal_question_repoke_watch` reads the AUTHORITATIVE
# transcript for N consecutive byte-identical re-pokes and DISARMS the loop
# (`/goal clear`), and `goal_dark_watch` honours the resulting `goal_disarmed_q`
# veto. Arming during ❓/⏳ stays allowed (this removed gate was still the right
# call); the newly-recognized unsafe case is caught after the fact, never by
# refusing to arm.


def _safe_age(now, ts):
    """`now - ts` as a float, or None when either side is not a genuine
    number. Mirrors `compact._safe_age` -- kept as a local copy (not an
    import) so `goal.py` has no hard dependency on `compact.py`'s own
    private helpers beyond the pane-resolution functions it deliberately
    reuses."""
    try:
        return float(now) - float(ts)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# The delivery primitives moved here verbatim from `watchdog/__init__.py`
# (`_send_goal_verified` + its `_await_typed` helper) -- their #35/#36/#176/
# #271/#306-hardened multi-step verify-type-submit-verify protocol is NOT
# simplified by this collapse, only relocated.
# --------------------------------------------------------------------------- #

GOAL_TYPE_SETTLE_POLLS = 8          # bounded: CC needs a moment to INGEST a
GOAL_TYPE_SETTLE_S = 1              # multi-KB paste before it renders it


def _await_typed(pid, text, run, sleep_fn, want=True):
    """Poll (bounded) until the pane's input box shows evidence of `text`
    (`want=True`) or has stopped showing it (`want=False`), returning the
    final verdict. A render-settle poll, not a blind timeout: it returns
    the instant the box agrees, and a type that never appears is still
    refused."""
    for i in range(GOAL_TYPE_SETTLE_POLLS):
        landed = watchdog._typed_landed(text, watchdog._input_line_text(
            watchdog.capture_pane(pid, run, lines=40)))
        if landed is want:
            return landed
        if i < GOAL_TYPE_SETTLE_POLLS - 1:
            sleep_fn(GOAL_TYPE_SETTLE_S)
    return not want


def _await_goal_armed(pid, run, sleep_fn):
    """#720 -- poll (bounded) until the pane footer shows the `◎ /goal` armed
    glyph (`pane_goal_armed is True`). A render-settle poll like `_await_typed`:
    CC arms a submitted /goal a moment after Enter, so the glyph lags the box
    clearing. Returns True the instant it reads armed, False if the bounded
    window elapses -- a submit CC read as a plain prompt clears the box (and
    appends a `user` turn) but never arms, so box-cleared / transcript-confirmed
    is NOT proof the goal armed (the #720 silent-'sent' tail). On False the
    caller keeps the request pending; the next sweep's own `armed is True` check
    turns a render-lag false-negative into `drop:already-armed`, never a
    double-arm. Shared by all three arm routes (bare / stash / stranded).

    (#1113: the old #720-review blind spot -- a stale-rearm REPLACE typing past
    an ALREADY-armed footer, where this confirm could not tell old-armed from
    new-armed -- is GONE: no origin types past an armed footer any more, so every
    delivery this confirms started from a DARK footer, and a True read here is
    genuinely our fresh arm.)"""
    for i in range(GOAL_TYPE_SETTLE_POLLS):
        if watchdog.pane_goal_armed(watchdog.capture_pane(pid, run, lines=40)) is True:
            return True
        if i < GOAL_TYPE_SETTLE_POLLS - 1:
            sleep_fn(GOAL_TYPE_SETTLE_S)
    return False


def _send_goal_verified(pid, text, run, captured=None, sleep_fn=None, logs=None,
                        verify_armed=True, nudge="goal-sweep", out=None):
    """Type a LONG `/goal ...` into a BARE input box and submit it,
    verifying every step against a fresh capture -- the same protocol
    `deliver_with_stash` uses for its own type/submit steps, minus the
    stash (there is no draft here).

    NEVER presses Enter after a type-verify failure. NEVER sends two
    consecutive Escapes (#35).

    #720 -- the type is verified HEAD-INCLUSIVELY via the #670 shared primitive
    `_type_literal_verified` (undo+retype a first-byte swallow, abort a HOLD box
    with ZERO keystrokes), REPLACING the head-BLIND tail-only `_await_typed`
    check that submitted a head-swallowed /goal CC then read as a plain prompt.
    And when `verify_armed` (the ARM path), the box clearing after Enter is NOT
    enough -- `pane_goal_armed` is CONFIRMED before returning True, so a submit
    read as a plain prompt (box clears, goal never arms) returns False instead
    of a silent "sent". The `/goal clear` DISARM caller passes verify_armed=False
    (a successful disarm leaves pane_goal_armed False -- not a failure).
    Returns True only when the box is provably empty again after the submit
    (and, on the arm path, the goal is confirmed armed).

    A SECOND, FRESH capture is taken immediately before typing (job 20's
    own re-capture-right-before-send pattern, #176-F3) -- the race this
    primitive guards against is a draft appearing AFTER the caller's own
    check but BEFORE this function's real type keystroke, several tmux
    round-trips later."""
    run = run or watchdog._default_run

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    sleep_fn = sleep_fn or time.sleep
    cap = captured if captured is not None else watchdog.capture_pane(pid, run, lines=40)
    if watchdog._input_line_text(cap) != "":
        watchdog._draft_rescue_persist(pid, cap, logs=logs)
        _log("goal-verify-abort: not-bare")
        return False                       # not a bare box -- caller's problem
    # #1002 -- goal-arm is machine text; the strip-deselect Escape carries the
    # "goal" kind, so the ONE `keys` primitive gates it: at OFF it fires ZERO
    # keystrokes (keys suppresses + returns False) and this helper bails
    # keystroke-free, exactly as the type below (routed through `keys`) does.
    if watchdog._strip_selected(cap):
        if not watchdog.keys(pid, "Escape", kind="goal", nudge=nudge, run=run,
                             logs=logs):
            return False
    fresh = watchdog.capture_pane(pid, run, lines=40)
    if watchdog._input_line_text(fresh) != "":
        watchdog._draft_rescue_persist(pid, fresh, logs=logs)
        _log("goal-verify-abort: raced-busy")
        return False                       # raced -- a draft appeared since the caller's own check
    # #720 -- HEAD-INCLUSIVE verified type (the #670 `_type_literal_verified`
    # shared primitive): it head+tail-verifies the box, undoes+retypes a
    # first-byte swallow (CORRUPT, box left BARE on give-up -- the #617
    # never-leave-a-poison invariant), and on an unreadable/collapsed box (HOLD)
    # aborts with ZERO keystrokes (the #372 janitor backstops any residue,
    # #670-review R2). This REPLACES the head-BLIND tail-only `_await_typed`
    # check that passed a head-swallowed /goal to Enter -- CC then read the text
    # as a plain prompt and the goal never armed (#720). Never a submit on False.
    if not watchdog._type_literal_verified(pid, run, text, sleep_fn,
                                           kind="goal", nudge=nudge, logs=logs):
        _log("goal-verify-abort: type-not-verified")
        return False                       # not byte-exact -- never submit it
    # #1104 -- the type keystrokes LANDED (the box holds our text): a keystroke
    # was ACTUALLY sent. Surface it so a re-arm caller books the per-pane budget
    # ONLY on a real send, never on an OFF-suppressed / raced-abort no-keystroke
    # bail above (mirrors send_verified's `out["attempted"]`). A False return with
    # no `out["typed"]` therefore means NOTHING was typed.
    if isinstance(out, dict):
        out["typed"] = True
    # #1002 -- reached only when ON (a suppressed type returned False above); the
    # submit Enter + corrective go through the ONE `keys` primitive.
    watchdog.keys(pid, "Enter", kind="goal", nudge=nudge, run=run, logs=logs)
    if _await_typed(pid, text, run, sleep_fn, want=False):
        # STILL in the box after the same bounded settle window -- a
        # genuinely swallowed submit. ONE corrective Escape+Enter, never a
        # second bare Enter, never two Escapes.
        watchdog.keys(pid, "Escape", kind="goal", nudge=nudge, run=run, logs=logs)
        watchdog.keys(pid, "Enter", kind="goal", nudge=nudge, run=run, logs=logs)
        if _await_typed(pid, text, run, sleep_fn, want=False):
            watchdog._undo_and_release_slot(pid, run, text, False, _log,
                                            "goal-verify-abort: "
                                            "swallowed-submit-not-recovered",
                                            sleep_fn=sleep_fn)
            return False
    if not verify_armed:
        return True                        # `/goal clear` disarm -- box-cleared IS the signal (#522)
    # #720 -- box-cleared does NOT prove the goal ARMED (a submit CC read as a
    # plain prompt also clears it -- the incident's "sent"-but-dark tail).
    if _await_goal_armed(pid, run, sleep_fn):
        return True
    _log("goal-verify-abort: not-armed-after-submit")
    return False


# --------------------------------------------------------------------------- #
# #566 -- OWNED recovery of a stranded goal delivery. Coupled to the janitor's
# EXISTING ownership proof (`_janitor_watch_seen` / `_janitor_park_seen`
# provenance + own-content shape), never a parallel detector; every keystroke
# sits behind that PROVEN-own-state gate AND the recent-human gate.
# --------------------------------------------------------------------------- #

def _recovery_recent_human(sid, cwd, tpath, now, pid=None, run=None):
    """The recent-human gate for a recovery keystroke: True (VETO) when a human
    just touched this pane, so we never keystroke into a human-active pane. An
    UNREADABLE / missing transcript fails SAFE toward VETO (unprovable-quiet is
    treated as "may be active") -- the recovery is an OPPORTUNISTIC self-heal, so
    refusing on an unreadable pane only defers it a sweep, never a data loss.

    #566-review A1: `_goal_autoarm_recent_human_activity` returns `(False, "")`
    for BOTH "read succeeded, no recent human" AND "read FAILED" (its underlying
    `_last_human_prompt_ts` swallows a read error to None) -- so a missing/
    unreadable transcript FILE would read not-recent and let the recovery
    PROCEED, contradicting the fail-safe intent. Probe the file itself first and
    VETO on any read failure (a genuinely-empty/new transcript still reads
    readable-and-quiet, which is correctly not-recent).

    #731 -- `pid`/`run` (optional) thread the pane target so the gate ALSO
    honours signal 3 (an attached tmux client's own input, the montalu4 blind
    spot the transcript-based signals cannot see -- a human deleting a stranded
    paste). Omitted, it is byte-identical to the pre-#731 two-signal gate."""
    if not tpath:
        return True
    try:
        os.path.getsize(tpath)          # exists + readable stat
    except OSError:
        return True                     # unprovable -> VETO (fail-safe)
    recent, _reason = watchdog._goal_autoarm_recent_human_activity(
        sid, tpath, now, pane_target=pid, run=run)
    return bool(recent)


def _janitor_provenance(state, pid, now):
    """True when the janitor's OWN proof shows a watchdog delivery job touched
    this pane -- the 6h generic mark OR the durable, age-unbounded park record.
    Reused verbatim, never re-derived (#486)."""
    return bool(watchdog._janitor_watch_seen(state, pid, now)
                or watchdog._janitor_park_seen(state, pid))


def _submit_stranded_own_goal(sid, cwd, text, pid, captured, tpath, run, state,
                              now, sleep_fn, logs, nudge="goal-sweep"):
    """#566 case (a) -- when the input box ALREADY holds our OWN swallowed
    COMPLETE `/goal <text>` (a prior attempt typed it but the Enter was
    swallowed/raced), COMPLETE the submit in place rather than routing it into
    `deliver_with_stash`, which would park our own /goal into the single slot and
    abort forever. Returns True on a transcript-confirmed submit, False/None
    otherwise (the caller then falls through to the ordinary stash path).

    Three gates, ALL required before the one Enter keystroke: (1) janitor
    PROVENANCE (a watchdog job touched this pane -- the mark `deliver_goal` just
    set, or a durable park); (2) the recent-human gate (never keystroke a
    human-active pane); (3) EXACT-payload completeness -- the box head is a
    leading substring of `text` AND the box tail is a trailing substring, proving
    the box holds the WHOLE literal `/goal <text>` (a truncated type fails the
    tail check and is refused). A foreign draft matches neither end and is left
    untouched. `submit_own_goal_verified` re-verifies completeness against a
    FRESH capture right before the keystroke."""
    if not _janitor_provenance(state, pid, now):
        return False
    # cheap content pre-check on the capture we already hold, before the
    # transcript read the recent-human gate does -- a foreign draft bails here.
    head = watchdog._input_box_head_text(captured)
    tail = watchdog._input_line_text(captured)
    if not (head and head.startswith("/goal ") and text.startswith(head)
            and tail and text.endswith(tail)):
        return False
    if _recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run):
        _log_goal_sync("SKIP recover-swallowed recent-human sid=%s cwd=%s"
                       % (sid, cwd))
        return False
    return watchdog.submit_own_goal_verified(pid, text, run=run,
                                             sleep_fn=sleep_fn, logs=logs,
                                             nudge=nudge)   # #1038 declared -> goal-arm


# #617 -- a stranded truncated /goal type always FAR exceeds this (montalu1's
# was 674 chars; a real arm is ~3.8k); a human hand-typing the short `/goal
# STOP CONDITIONS` opening (21 chars) or pasting a small snippet never reaches
# it. The floor is what makes the byte-exact-prefix proof unforgeable in
# practice (#617-review 🔴).
GOAL_STRANDED_MIN_MATCH = 200
# #617 -- one loud give-up on a box that never converges (a genuinely busy
# pane), never a forever-retry that also starves the pane's dark-watch (#566
# livelock class).
GOAL_STRANDED_CLEAR_GIVEUP = 3


def _clear_stranded_truncated_goal(sid, cwd, captured, tpath, pid, run, state,
                                   now, sleep_fn, dry_run, rearm_fn, loc):
    """#617 -- clear a STRANDED, TRUNCATED own `/goal` draft left in a pane's
    input box. A partial /goal type (a send-keys chunk interrupted mid-arm)
    leaves a byte-exact PREFIX of THIS pane's own template in the box; the
    next sweep can neither SUBMIT it (`_submit_stranded_own_goal` needs the
    WHOLE literal text) nor stash around it, and once a LATER successful send
    clears the janitor watch mark the generic `_janitor_recover` refuses it
    for lack of provenance -- the live montalu1 674-char stuck draft no
    watchdog job would touch. Runs REGARDLESS of arm state (the poison is
    orthogonal to whether a goal is currently armed).

    Ownership is proven by CONTENT, not provenance -- and the proof is a
    byte-exact CONTIGUOUS prefix of THIS pane's own template, NOT head+tail
    (which cannot establish contiguity: a paste of the template start + an
    edited middle + a template-substring tail matches BOTH ends -- the
    #617-review 🔴, and the #372 CRITICAL-1 "content alone can't prove
    ownership for /goal" because of the documented manual paste-the-template
    arm flow). The WHOLE box content is reconstructed from every wrapped row
    and must be a whitespace-normalised prefix of the template, at least
    `GOAL_STRANDED_MIN_MATCH` chars and NOT the complete text (a complete own
    draft is `_submit_stranded_own_goal`'s job). A user would have to paste
    the exact first 200+ chars of the ~3.8k template with zero edits -- and
    the recent-human gate still VETOES if they just did.

    Gates, all required: a CLEAN idle input boundary (`_classify_boundary`
    == "input" -- never Escape a spinner / dialog / non-boundary); the
    FAIL-CLOSED recent-human check (`_recovery_recent_human` -- an unreadable
    transcript VETOES, unlike the raw gate, #566-review A1); a bounded
    give-up (one loud escalation, never a forever-retry). The clear itself is
    the SAME `_janitor_clear_box` the provenance-gated janitor already uses on
    own `/goal` content, so the keystroke safety profile matches it.

    Cheap: the (SKILL.md-reading) template resolve happens ONLY once the box
    head starts with the distinctive `/goal STOP CONDITIONS` opening -- rare.

    Returns `(logs, cleared)`: `cleared` is True ONLY on a verified box-clear
    (the caller re-evaluates the pane next sweep off a fresh capture); False
    on no-stranded-draft / VETO / give-up / non-convergence, so the caller
    does NOT skip the pane's ordinary dark-watch work.

    RESIDUAL (honest): a routine airuleset push that REWORDS the template
    between the poisoning and this sweep makes the box no longer a prefix of
    the NEW template -> the stranded draft is not auto-cleared here (it falls
    back to pre-#617 stuck behaviour + the eventual #459 ping)."""
    seen = state.setdefault("trunc_clear", {}) if state is not None else {}
    head = watchdog._input_box_head_text(captured)
    if not (head and head.startswith("/goal STOP CONDITIONS")):
        return [], False
    # a CLEAN idle input boundary only -- never Escape a spinner / dialog.
    kind, _t = watchdog._classify_boundary(captured)
    if kind != "input":
        return [], False
    text, _auth = (rearm_fn or _default_rearm_fn)(cwd)
    if not text:
        return [], False
    # #737 -- the SAME head-first whole-box reconstruction `_goal_box_kind` uses
    # (single-sourced in `_box_norm_from_capture`, never a second inline copy).
    box_norm = watchdog._box_norm_from_capture(captured)
    if not box_norm:
        return [], False
    text_norm = " ".join(text.split())
    if not (len(box_norm) >= GOAL_STRANDED_MIN_MATCH
            and len(box_norm) < len(text_norm)
            and text_norm.startswith(box_norm)):
        return [], False
    # PROVEN a stranded truncated own /goal. The give-up rec is keyed on the
    # content signature so a DIFFERENT/gone draft starts a fresh episode.
    rec = seen.get(sid) or {}
    if rec.get("sig") != box_norm[:80]:
        rec = {"sig": box_norm[:80]}
    if rec.get("gaveup"):
        seen[sid] = rec
        return [], False   # already escalated -> never re-attempt, never starve
    if _recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run):
        return (["dark-watch %s sid=%s -> stranded truncated /goal draft, "
                 "recent-human VETO" % (loc, sid)], False)
    if dry_run:
        return (["dark-watch %s sid=%s -> would CLEAR stranded truncated "
                 "/goal draft (#617)" % (loc, sid)], False)
    logs = []
    watchdog._draft_rescue_persist(pid, captured, logs=logs)   # snapshot first
    if watchdog._janitor_clear_box(pid, run, sleep_fn, logs.append):
        seen.pop(sid, None)
        logs.append("dark-watch %s sid=%s -> CLEARED stranded truncated /goal "
                    "draft (poisoned, #617)" % (loc, sid))
        return logs, True
    rec["fails"] = int(rec.get("fails", 0)) + 1
    seen[sid] = rec
    if rec["fails"] >= GOAL_STRANDED_CLEAR_GIVEUP:
        rec["gaveup"] = True
        logs.append("dark-watch %s sid=%s -> stranded truncated /goal clear "
                    "FAILED %d× -- giving up (human must clear it)"
                    % (loc, sid, rec["fails"]))
    else:
        logs.append("dark-watch %s sid=%s -> stranded truncated /goal clear "
                    "did not converge (%d), retry next sweep"
                    % (loc, sid, rec["fails"]))
    return logs, False


def _recovery_pane_ready(sid, cwd, run, projects_dir, now, pid=None):
    """#731 -- the SHARED guard core extracted from `_resolve_stash_abort_livelock`
    (#566) so the attempt-cap drop cleanup reuses the IDENTICAL pre-keystroke
    guards, never a parallel set. Resolves the pane (unless `pid` given) and, in
    the SAME order the livelock resolver already used, checks: copy-mode, the
    fail-closed recent-human gate (which now ALSO carries the #731 tmux-client-
    input signal 3), an open dialog. Returns `(pid, captured, loc)` when the pane
    is safe to act on, or `(None, <reason-str>, None)` when a guard vetoes
    (`reason` in {no-pane,in-mode,recent-human,dialog-open}). A CLEAN idle input
    BOUNDARY (`_classify_boundary=="input"`, not the "Waiting for N agents"
    swallowed-submit render) is the CALLER's responsibility -- the #566 caller
    orders `_janitor_recover` (which no-ops on an unreadable box), the #731 cap-
    drop caller checks it explicitly before its own clear."""
    if pid is None:
        pid = _compact._find_pane_for_session(sid, cwd, run=run,
                                              projects_dir=projects_dir)
    if not pid:
        return None, "no-pane", None
    if watchdog.pane_in_mode(pid, run):
        return None, "in-mode", None
    tinfo = watchdog.find_active_transcript(projects_dir, cwd)
    tpath = tinfo[0] if tinfo else None
    if _recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run):
        return None, "recent-human", None
    captured = watchdog.capture_pane(pid, run, lines=40)
    if watchdog.pane_waiting_on_user(captured):
        return None, "dialog-open", None
    loc = watchdog._pane_location(pid, run) or pid
    return pid, captured, loc


def _goal_box_kind(captured, text):
    """#731 -- classify what OUR request's leftover looks like in the pane, for
    the cap-drop cleanup verdict AND the arm-confirm-fail diagnostic `box=` field.
    Returns one of:
      "empty"  -- no input-box content and no occupied stash slot (nothing to
                  clean; a bare `❯`, or no box located at all).
      "ours"   -- the occupied #35 stash slot, OR own-shaped box content
                  (`_looks_like_own_stuck_content`: our `/goal `/`/compact` prefix
                  or the collapsed-paste placeholder a multi-KB own type renders),
                  OR a whitespace-normalized #617 whole-box match of the request's
                  OWN frozen `text` (>= GOAL_STRANDED_MIN_MATCH; exact or a prefix
                  of a truncated type).
      "other"  -- non-empty box content that is none of the above: a FOREIGN
                  draft, left completely untouched.
    Ownership is UNION-of-proofs so BOTH render shapes are recognised (the literal
    `/goal ...` a short text renders AND the placeholder a large one collapses to);
    the frozen-text match is the tighter #617 proof against a human paste."""
    occupied = watchdog.STASH_MARKER in (captured or "")
    head = watchdog._input_box_head_text(captured)
    if not head and not occupied:
        return "empty"
    if occupied or watchdog._looks_like_own_stuck_content(head):
        return "ours"
    box_norm = watchdog._box_norm_from_capture(captured)
    text_norm = " ".join((text or "").split())
    if box_norm and text_norm:
        if (len(box_norm) >= GOAL_STRANDED_MIN_MATCH
                and (box_norm == text_norm or text_norm.startswith(box_norm))):
            return "ours"
        # #737 -- a SCROLLED long /goal renders only its TAIL rows, so the
        # reconstructed whole-box content is a contiguous SUBSTRING of the
        # payload (not a prefix -- the head with the `/goal ` marker scrolled
        # off). >= GOAL_ARM_LEFTOVER_MIN_SUBSTR contiguous normalized chars of
        # the request's OWN frozen `text` is the missing proof; a short human
        # draft (below the floor) or a foreign draft (never a substring of the
        # template) can never false-positive.
        if (len(box_norm) >= watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR
                and box_norm in text_norm):
            return "ours"
    return "other"


def _resolve_stash_abort_livelock(sid, cwd, run, projects_dir, state, now,
                                  send_fn, dry_run, sleep_fn, own_payload=None):
    """#566 case (b) -- a PENDING goal request has hit
    `GOAL_STASH_ABORT_LIVELOCK` consecutive identical `stash-abort: slot
    occupied` aborts: order the shared janitor recovery NOW (from job 9, which
    is NOT budget-deferred like job 20's dark-watch), so the stale own stash slot
    is resolved BEFORE the request's age cap can lapse it in silence.

    Re-resolves the pane and takes a FRESH capture right before the keystroke,
    then delegates to `watchdog._janitor_recover` -- the SAME provenance +
    own-content-shape gated driver job 20 uses (pop / clear-and-pop for our own
    stranded content; a genuine foreign occupant is left COMPLETELY untouched;
    one loud owner ping on a recovery failure). Adds the recent-human gate AND
    the copy-mode / open-dialog pre-guards on top (never keystroke a human-active
    or non-boundary pane). Returns log lines for goal_sweep.

    #566-review F2: once the janitor has ESCALATED (pinged the owner) for this
    pane, the recovery FAILED and the owner was told ONCE -- STOP re-ordering the
    recovery keystrokes every sweep (clause 2: one loud escalation, never
    infinite retries; job 20's dark-watch retains its own per-sweep retry). A
    later verified success clears `janitor_pinged`, so a genuinely-fresh livelock
    re-attempts. This also bounds the rare same-sweep overlap with job 20's own
    `_janitor_recover` to the pre-escalation sweeps only."""
    logs = []
    pid = _compact._find_pane_for_session(sid, cwd, run=run,
                                          projects_dir=projects_dir)
    if not pid:
        return logs
    jrec = state.setdefault("janitor_pinged_rec", {}).setdefault(pid, {}) \
        if state is not None else {}
    if jrec.get("janitor_pinged"):
        logs.append("stash-abort-livelock ALREADY-escalated sid=%s (%s) -> job 20 "
                    "retains its own retry" % (sid, cwd))
        return logs
    # #566-review F4 / #731 -- the SAME shared pre-keystroke guards (copy-mode,
    # fail-closed recent-human incl. the client-input signal, an open dialog),
    # via `_recovery_pane_ready` so the attempt-cap drop cleanup and this livelock
    # resolver can never drift apart. The pane was resolved above (for the latch);
    # pass it through so the guard core does not re-resolve it.
    pid, captured, loc = _recovery_pane_ready(sid, cwd, run, projects_dir,
                                              now, pid=pid)
    if pid is None:
        logs.append("stash-abort-livelock SKIP %s sid=%s (%s)"
                    % (captured, sid, cwd))
        return logs
    logs.append("stash-abort-livelock ORDER janitor recovery sid=%s (%s) loc=%s"
                % (sid, cwd, loc))
    jlogs = watchdog._janitor_recover(run, jrec, pid, cwd, captured, loc,
                                      send_fn, dry_run, sleep_fn,
                                      state=state, now=now,
                                      own_payload=own_payload)  # #737
    logs += jlogs
    if not dry_run and any(ln.startswith("RECOVERED (janitor)") for ln in jlogs):
        state.get("janitor_watch", {}).pop(pid, None)
    return logs


def _goal_cap_drop(sid, cwd, text, origin, dl_fails, request_ts, run,
                   projects_dir, state, now, send_fn, dry_run, sleep_fn):
    """#731 -- the terminal attempt-cap DROP action: clean up any leftover /goal
    text this request stranded in the pane, then ping the owner (origin-gated
    EXACTLY like the 30-min expiry ping). Returns `(logs, leftover, loc)` where
    `leftover` in {cleared,not-ours,escalated,none,skipped} for the journal and
    `loc` is the family pane loc (falls back to the cwd label).

    Cleanup REUSES the shared owned-recovery machinery, never a parallel set: the
    same guard core (`_recovery_pane_ready`) + a CLEAN idle input-boundary check
    (#731-review: never Escape/clear a live turn's render) + the same
    `_janitor_recover` driver the #566 livelock resolver uses. It clears ONLY our
    OWN leftover (proven by `_goal_box_kind` -- own-shape / #35 stash slot / #617
    frozen-text match); a FOREIGN draft is left completely untouched. The
    `leftover` word is READ BACK from the janitor's own verdict (#731-review /
    #134/#726 honesty bar -- never asserted "cleared" for a box the janitor
    actually left untouched or failed to clear). Never types a NEW /goal."""
    logs = []
    pid = _compact._find_pane_for_session(sid, cwd, run=run,
                                          projects_dir=projects_dir)
    loc = (watchdog._pane_location(pid, run) or pid) if pid else \
        watchdog.project_label(cwd)
    leftover = "skipped"
    if pid and not dry_run:
        pid2, captured, loc2 = _recovery_pane_ready(sid, cwd, run, projects_dir,
                                                    now, pid=pid)
        if pid2 is None:
            logs.append("attempt-cap: cleanup SKIP %s (%s)" % (captured, loc))
        else:
            loc = loc2
            # #731-review -- a CLEAN idle input boundary only (design B's
            # `_classify_boundary=="input"`), never a spinner / the #714/#720
            # "Waiting for N agents" swallowed-submit render: Escaping/clearing
            # there would interrupt a live turn. _recovery_pane_ready already
            # ruled out copy-mode / recent-human / an open dialog.
            bkind, _bd = watchdog._classify_boundary(captured)
            # #1023: idle-pane only — a busy "Waiting for N agents" pane always
            # defers (the #921 aged override is gone).
            if bkind != "input" or _ops_wait_recheck._pane_busy_waiting(captured):
                logs.append("attempt-cap: cleanup SKIP non-input-boundary (%s)"
                            % loc)
            else:
                kind = _goal_box_kind(captured, text)
                if kind == "ours":
                    jrec = state.setdefault("janitor_pinged_rec", {}) \
                        .setdefault(pid2, {}) if state is not None else {}
                    jlogs = watchdog._janitor_recover(run, jrec, pid2, cwd,
                                                      captured, loc, send_fn,
                                                      dry_run, sleep_fn,
                                                      state=state, now=now,
                                                      own_payload=text)  # #737
                    logs += jlogs
                    # READ the verdict back, never assert it (#731-review): a
                    # RECOVERED line means the box was cleared; an ESCALATED line
                    # means the clear FAILED (owner pinged, box still dirty);
                    # NEITHER means the janitor left it untouched (a foreign
                    # occupant in the #35 slot, or unprovable provenance).
                    if any(ln.startswith("RECOVERED (janitor)") for ln in jlogs):
                        if state is not None:
                            state.get("janitor_watch", {}).pop(pid2, None)
                        leftover = "cleared"
                    elif any(ln.startswith("ESCALATED (janitor)") for ln in jlogs):
                        leftover = "escalated"
                    else:
                        leftover = "not-ours"
                elif kind == "other":
                    logs.append("attempt-cap: leftover not-ours, untouched (%s)"
                                % loc)
                    leftover = "not-ours"
                else:
                    leftover = "none"
    # origin-gated ping -- the SAME gate as the expiry ping (normal / dark-rearm
    # only; auth-rearm + #764 fulfilled-rearm + answer-rearm are SILENT: each is a
    # loop that is ALIVE / self-healing, so a "re-run /autopilot" ping would go
    # to the very human whose presence deferred it — a fulfilled-rearm most often
    # fails delivery on skip:recent-human, dark_watch re-detects it next sweep
    # under the fulfilled min-gap/cap). Deduped on sid + the REQUEST TS (like the
    # expiry ping, #731-review): dl_fails is always exactly the cap at drop time,
    # so keying on it would silence a later capped episode for the SAME sid for
    # the full 14-day dedup TTL (the #134 silence class); request_ts gives each
    # episode its own ping.
    if (send_fn is not None and not dry_run
            and origin not in (_GOAL_AUTH_REARM_ORIGIN,
                               _GOAL_FULFILLED_REARM_ORIGIN,
                               _GOAL_ANSWER_REARM_ORIGIN,
                               _stream_migrate.ORIGIN)):
        from notify import stream_redirect
        owner = (stream_redirect(watchdog.pane_owner(pid, run))
                 if pid else None)
        send_fn(
            "⚠️ **%s** — /goal sa opakovane nepodarilo automaticky "
            "nastaviť (%d× po sebe) a bol zrušený. Spústi prosím "
            "`/autopilot` znova." % (watchdog.project_label(cwd), dl_fails),
            owner=owner or None,
            dedup_key="goalarm-attempt-cap:%s:%d"
            % (sid, int(request_ts) if request_ts is not None else 0),
            dry_run=dry_run)
    return logs, leftover, loc


def _log_arm_confirm_fail(sid, cwd, text, pid, run, sleep_fn=None,
                          state=None, now=None, tpath=None):
    """#731 D -- ONE structured diagnostic line at an arm-confirm failure, from a
    FRESH TALLER capture (the 40-row delivery capture may not show a busy render
    a row above the box). The next incident's discriminator between the #720
    swallowed-submit class and a CC mid-turn QUEUE (reopen trigger named on the
    ticket). Also draft-rescue-snapshots the box so a stranded /goal is never
    lost. Best-effort: the only failure-prone step is the extra capture (the pure
    classifiers below run on a "" fallback, `_log_goal_sync`/`_draft_rescue_
    persist` are themselves fail-safe), so a capture error is LOGGED, never
    swallowed silently (script-failure-policy).

    #731-review -- `boundary` is (re)classified from the FRESH 80-row capture,
    NOT the pre-type `kind`: the pre-type boundary is structurally always "input"
    at all three call sites (delivery only proceeds past the no-input-line/busy
    checks), a constant zero-information field. The FRESH boundary (input+empty =
    consumed-as-a-plain-prompt vs busy = a mid-turn queue) IS the (i)/(ii)
    discriminator this diagnostic exists for."""
    try:
        cap = watchdog.capture_pane(pid, run, lines=80)
    except Exception as e:                       # narrow: only the extra capture
        _log_goal_sync("ARM-CONFIRM-FAIL sid=%s cwd=%s diag-capture-failed=%r"
                       % (sid, cwd, e))
        cap = ""
    boundary, _bd = watchdog._classify_boundary(cap)
    busy = _ops_wait_recheck._pane_busy_waiting(cap)
    armed = watchdog.pane_goal_armed(cap)
    box = _goal_box_kind(cap, text)
    # #1110 -- the transcript age at the keystroke: the one field that would have
    # explained the dev1 songplayer class (`boundary=input box=empty` while the
    # turn was live). A None age (missing/unreadable transcript) renders `tage=?`.
    _tage = _turn_liveness.transcript_age_s(
        tpath, now if now is not None else time.time())
    tage = "?" if _tage is None else "%d" % int(_tage)
    _log_goal_sync("ARM-CONFIRM-FAIL sid=%s cwd=%s boundary=%s busywait=%s "
                   "armed=%s box=%s tage=%s"
                   % (sid, cwd, boundary, busy, armed, box, tage))
    watchdog._draft_rescue_persist(pid, cap)
    # #737 A -- DELIVERY-TIME self-cleanup: a verify-failed / arm-confirm-fail
    # arm can strand our OWN /goal in the box (a swallowed submit whose len-based
    # undo did not converge, the montalu6/montalu3/gk leftover). Clean it NOW so
    # it never becomes an unrecognizable "foreign draft" that jams the single
    # stash slot forever. FIRST-PERSON provenance makes this safe without the
    # janitor's `_janitor_watch_seen` gate: the delivery proved the box BARE
    # seconds ago and typed our own text into it. Own-leftover proof = the box is
    # a >= 80-char contiguous SUBSTRING of `text` (a SCROLLED /goal) OR the
    # own-shape head (`_looks_like_own_stuck_content`: `/goal ` prefix / collapsed
    # placeholder). A CLEAN idle input boundary only (never Escape/clear a live
    # turn's render). Every outcome is falsifiable in goal-sync.log (design D).
    head = watchdog._input_box_head_text(cap)
    # #1113 -- the BARE-BOX delivery path sets `_janitor_mark_watch(state, pid,
    # now)` immediately before the type and never clears it on failure, so on the
    # regression path (a swallowed submit that strands a TRUNCATED / grid-wrapped
    # own /goal -- the >10x david1-3 report) provenance holds here and the payload
    # is proven ours by the tail + whitespace-stripped body substring (the
    # un-foolable teeth), not only the #737 clean substring / the `/goal ` head
    # prefix. The two STASH/stranded-submit confirm-fail callers CLEAR the watch
    # on their verified submit before reaching here, so the provenance branch is
    # inert on those paths -- deliberately SAFE: a verified stash/stranded submit
    # left the box empty, so there is nothing to clear (a truncated leftover only
    # arises when the bare-box type itself failed to converge, which keeps its
    # mark). `state=None`/no mark -> the provenance branch stays inert and the
    # #737 clean-substring / head-shape fallback governs (a foreign draft, never
    # a >=80-char substring of our payload, is untouched either way).
    _now = now if now is not None else time.time()
    # #1113 recurrence -- `match_templates=True` recognises the box as OUR leftover
    # when it is a verbatim run of ANY rendered /goal template variant, not only
    # this request's `text`: the 23.9 `cleanup=declined` was a fork-no-merge tail
    # whose payload-specific tail proof missed because the handed-in payload was a
    # different variant. Verbatim template text needs no provenance.
    own = (watchdog._box_is_own_leftover(
               cap, text, watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR,
               provenance=watchdog._janitor_watch_seen(state, pid, _now),
               match_templates=True)
           or watchdog._looks_like_own_stuck_content(head))
    if not own:
        _log_goal_sync("ARM-CONFIRM-FAIL sid=%s cwd=%s cleanup=declined "
                       "(box not our own leftover)" % (sid, cwd))
        return
    if boundary != "input" or busy:
        # #1104 -- a LIVE turn (busy / non-input boundary) holds our OWN /goal
        # stranded in the box. We MUST NOT Escape-clear it now (that interrupts
        # the running turn -- the never-Escape-a-live-turn rule). Instead DEFER
        # the cleanup by setting the #372 janitor watch: the shared janitor
        # (`_janitor_recover`, sweep top) then recovers the stranded /goal on a
        # SUBSEQUENT sweep -- subject to its OWN provenance + own-content gates,
        # not only after the hourly floor that a watch-less verify-failed would
        # have waited for (the montalu1 incident's text sat unsent for the whole
        # background-agent wait). A no-op when `state` is None (a caller/test not
        # threading it).
        watchdog._janitor_mark_watch(state, pid,
                                     now if now is not None else time.time())
        _log_goal_sync("ARM-CONFIRM-FAIL sid=%s cwd=%s "
                       "cleanup=deferred(live-turn)" % (sid, cwd))
        return
    cleared = watchdog._janitor_clear_box(
        pid, run, sleep_fn or time.sleep,
        lambda _r: None)   # janitor-internal log line -> swallowed here
    _log_goal_sync("ARM-CONFIRM-CLEANUP sid=%s cwd=%s cleared=%s"
                   % (sid, cwd, cleared))


def _goal_client_active_skip(sid, cwd, pid, run, now, out):
    """#731 -- the pre-keystroke client-input DEFER, factored out of deliver_goal
    (architecture-first: keep the delivery function lean). An ATTACHED human
    typing/deleting in this pane NOW vetoes the type even when they submit
    nothing -- the montalu4 blind spot the transcript-based recent-human gate
    cannot see. Since #752 this is called ONLY for the watchdog-INITIATED
    re-arm origins (`_GOAL_WATCHDOG_REARM_ORIGINS`), the SAME partition the
    transcript recent-human gate uses: a `self-callback` /autopilot arm is
    never client-active vetoed (owner ruling 2026-08-30). For a re-arm it is
    the SEPARATE 5-min client window (not the 30-min transcript one), a
    zero-keystroke DEFER that never counts toward the attempt cap.
    Returns "skip:client-active" (logged, out['detail'] set) or None (proceed)."""
    cactive, creason = watchdog._tmux_client_recent_input(pid, run, now)
    if not cactive:
        return None
    _log_goal_sync("SKIP client-active sid=%s cwd=%s -> %s" % (sid, cwd, creason))
    if out is not None:
        out["detail"] = creason
    return "skip:client-active"


def _structured_goal_mark_state(sid, state, with_mark=False):
    """#1113 recurrence -- the watchdog's STRUCTURED, transcript-derived `/goal`
    mark state for `sid` ("set" / "cleared" / None), the #486 single-source
    `one_glance.resolve_goal_armed` reads. Prefers the in-memory sweep `state`
    (freshest -- `goal_dark_watch` populates `state["goal_mark"]` EARLIER this
    same sweep, before `goal_sweep` -> `deliver_goal` runs) and falls back to the
    persisted `state["goal_mark"]` on disk (`watchdog.persisted_goal_mark`, #1089)
    for the CLI `_goal_sync_attempt` caller whose `state` may not carry the map.

    A pane glyph / render is NEVER consulted -- the footer render can read dark
    while the loop is armed (the `◎ /goal` glyph scrolled off behind a long
    stranded draft: the >10x david1-3 regression). Returns None on any
    missing / malformed record (fail-safe: no structured proof -> do NOT refuse
    on this basis, the pane-based `drop:already-armed` belt still governs).

    DESIGN-OWNED CONSEQUENCE of the deliver_goal refusal that reads this (design
    5790100100 Approach-2 rejection: "(a) keeps exactly [the virgin/declared
    window] and nothing else"): answer-rearm (#890) and fulfilled-rearm (#764)
    both FIRE ONLY when mark=="set" (a stop-(A) `❓`-disarm / a stop-(B) fulfilled
    loop leaves mark "set", CC never writing a `cleared` marker), and a dark-rearm
    of a silently-dead loop also leaves mark "set" -- so the refusal INTENTIONALLY
    supersedes their keystroke recovery for the mark-set case. Recovery of a
    genuinely dead / answered / fulfilled loop is by the NEXT natural arm (session
    death -> a virgin/declared arm, or the owner's `/autopilot`), never a machine
    type into a possibly-live armed loop. `goal_dark_watch` still RECORDS those
    requests in its `armed is False, mark=="set"` branch; they now drop at the
    refusal. A full tombstone of the now-superseded answer-rearm/fulfilled-rearm
    recording paths is a cross-cutting follow-up (the #764/#707 origin-retirement
    checklist), out of the (a)+(b)+(c) recurrence-fix scope. Because a mark-SET
    watchdog re-arm returns at the refusal, deliver_goal's downstream expiry-ping
    / recent-human / `drop:stale-rearm` freshness / client-active / pane-budget
    gates now govern ONLY the NOT-set cases for a watchdog origin (auth-rearm mark
    "cleared", declared-virgin no mark, or a mark absent/unreadable) -- plus the
    ONE owner-ruled exemption: a `stream-migrate` request on an idle (>= 10 min)
    dark stream loop the owner did not end, with no open `❓ NEEDS YOU` after the
    arm (#1143) -- `stream_migrate.delivery_ok`. `with_mark=True` returns the
    mark dict (`payload` + `ts`) instead of the state, from the SAME read."""
    rec = None
    if isinstance(state, dict):
        gm = state.get("goal_mark")
        if isinstance(gm, dict):
            rec = gm.get(sid)
    if not isinstance(rec, dict):
        try:
            rec = watchdog.persisted_goal_mark(sid)
        except Exception:                    # noqa: BLE001 -- fail-safe to None
            rec = None
    if not isinstance(rec, dict):
        return None
    mark = rec.get("mark")
    if not isinstance(mark, dict):
        return None
    st = mark.get("state")
    return (st if st in ("set", "cleared") else None) if not with_mark else mark


# --------------------------------------------------------------------------- #
# The ONE delivery function.
# --------------------------------------------------------------------------- #

_GOAL_TERMINAL_WORDS = frozenset((
    "sent", "expired", "drop:cleared-after-request", "drop:already-armed",
    "drop:stale-rearm",   # #524 -- a dark-rearm too old to type (delivery gate)
    "drop:stale-rearm-retired",  # #1113 -- a leftover stale-rearm request, never
                                 # typed (the origin is retired); cleared in one
                                 # sweep. (The #623 `drop:already-current` word is
                                 # removed with the stale-rearm REPLACE path.)
))


def _verify_fail_word(tpath, age_before, now):
    """#1110 -- classify a keystroke that did NOT arm. Re-read the transcript age
    (the SAME stat source as the pre-keystroke busy-transcript gate) and, if it
    ADVANCED since `age_before` (the age captured before the keystroke) ->
    `skip:verify-failed-live` (NOT in `_GOAL_KEYSTROKE_SKIPS`, so goal_sweep never
    counts it toward the STRICT GOAL_DELIVERY_ATTEMPT_CAP and the request stays
    pending). The advance is unfalsifiable -- a FOREIGN live turn (a mis-timed
    keystroke) OR our OWN submit read as a plain prompt (#720 silent-'sent' tail,
    a real failed delivery) -- so goal_sweep bounds verify-failed-live with the
    SEPARATE looser GOAL_DELIVERY_LIVE_ATTEMPT_CAP (#1110-review). A quiet
    transcript is the #731 swallowed-submit class -> `skip:verify-failed`
    (counted on the strict cap). All classification logic lives in the leaf; this
    is the call-site adapter that maps the leaf verdict to the disposition word."""
    age_after = _turn_liveness.transcript_age_s(tpath, now)
    if _turn_liveness.classify_confirm_fail(age_before, age_after) == "live":
        return "skip:verify-failed-live"
    return "skip:verify-failed"


def _declared_window_nudge(cwd):
    """#1038 -- the keystroke NUDGE identity for arming `cwd`'s pane, derived from
    WHETHER `cwd` is a DECLARED managed window (gk review, gk-infra, d3 today —
    any box that declares `windows` in cli_fleet), NOT from the origin. A
    declared window is a session-
    revival surface, so ANY arm delivered into it (a fresh `declared-virgin`
    bootstrap, a manual `self-callback`, or a `dark-rearm`) rides the ALWAYS-ON
    `goal-arm` recovery nudge and is never suppressed by the #1023 machine-nudge
    OFF switch -- the owner's declared windows come back armed after a reboot
    with zero staging. Every OTHER (non-declared) box keeps the staged PRIORITY
    `goal-sweep` identity, byte-identical to before. `source == "role"` is the
    ONE declared-window signal (`_match_window` matched the pane cwd to a
    box_windows entry). Fail-safe toward `goal-sweep` on any resolver error --
    never a wrongly-always-on non-declared pane."""
    try:
        import cli_concurrency
        source = cli_concurrency.resolve_concurrency(cwd)[2]
    except Exception as e:  # noqa: BLE001 -- any resolver failure keeps the staged default
        _log_goal_sync("declared-window-nudge resolve-error cwd=%s (%r) "
                       "-> goal-sweep" % (cwd, e))
        return "goal-sweep"
    return "goal-arm" if source == "role" else "goal-sweep"


def deliver_goal(sid, cwd, text, authority, run=None, projects_dir=None,
                 now=None, state=None, request_ts=None, send_fn=None,
                 dry_run=False, sleep_fn=None, logs=None, origin=None, out=None):
    """Evaluate every arm-delivery condition for `sid` ONCE and act. Called
    from BOTH `_goal_sync_attempt` (the CLI's own immediate synchronous
    attempt) AND `goal_sweep` (the periodic re-evaluation of a still-
    pending request).

    Returns:
      "sent"                        -- `/goal <text>` was typed.
      "expired"                     -- the request is older than
                                        `GOAL_REQUEST_MAX_AGE_S`; a
                                        deduped Discord ping fires (unlike
                                        compact's silent expiry -- an
                                        undeliverable goal-arm is a
                                        dark-autopilot failure).
      "drop:cleared-after-request"  -- the #170 clear-suppression guard:
                                        the newest marker is `cleared` and
                                        postdates this request.
      "drop:already-armed"          -- #1113 UNCONDITIONAL: the footer shows
                                        `◎ /goal` armed, so NOTHING is typed for
                                        ANY origin (this request's own earlier
                                        delivery, a manual paste, a foreign goal,
                                        or an alive loop carrying a stale
                                        condition -- a template change waits for
                                        the next natural arm, never a keystroke).
      "drop:stale-rearm"            -- #524: a `dark-rearm`-origin request
                                        older than GOAL_DARK_REARM_STALE_S;
                                        the dark read it acted on has gone
                                        stale, so never type it late.
      "drop:stale-rearm-retired"    -- #1113: a leftover on-disk `stale-rearm`
                                        request (recorded before that typing path
                                        was removed); dropped terminally before
                                        any pane work, never typed.
      "skip:<reason>"               -- not safe right now; the caller LEAVES
                                        the request pending for the next sweep
                                        -- BUT #731: a KEYSTROKE-delivering skip
                                        (verify-failed / stash-abort) counts
                                        toward GOAL_DELIVERY_ATTEMPT_CAP, so it
                                        re-types at most that many times before
                                        goal_sweep drops it; `skip:client-
                                        active` (an attached human typing NOW)
                                        is a zero-keystroke defer, never counted.
      "skip:busy-transcript"        -- #1110: the session TRANSCRIPT was written
                                        within GOAL_TURN_LIVE_WINDOW_S (the turn
                                        is running), so the render's bare box is
                                        a mid-turn frame, not idle. A
                                        zero-keystroke, non-counting defer
                                        evaluated BEFORE the render gate.
      "skip:verify-failed-live"     -- #1110: a keystroke WAS typed but the arm
                                        never confirmed AND the transcript
                                        advanced during the confirm window (a
                                        FOREIGN live turn OR our own accepted
                                        submit). NOT in `_GOAL_KEYSTROKE_SKIPS`,
                                        so it never counts toward the STRICT cap
                                        (unlike a quiet `skip:verify-failed`); the
                                        request stays pending -- bounded by the
                                        separate looser
                                        `GOAL_DELIVERY_LIVE_ATTEMPT_CAP`
                                        (#1110-review) so an accept-as-plain-
                                        prompt livelock still gives up.

    Deliberately does NOT check `_goal_autoarm_recent_human_activity` for
    the normal (user-`/autopilot`) origin -- see this module's own header
    docstring for why that would be a structurally-always-refuses bug here,
    not a safety net. The #478 auto-re-arm origin (`origin=="dark-rearm"`)
    is the exception: it IS watchdog-initiated, so it DOES honour that gate
    (`skip:recent-human`) exactly like the lane nudge.

    `out` (#624, optional): when a dict is passed, `deliver_goal` records the
    journal-facing observability the flat return word cannot carry -- `out["loc"]`
    (the family-canonical `watchdog._pane_location` = the `montalu1:0.0` key every
    sibling goal-family line uses, set once after pid resolution) and, where a skip
    has detail beyond its word, `out["detail"]` (currently the recent-human
    `presence marker Ns old` -- otherwise the word is self-complete). `goal_sweep`
    reads these to render a loc-keyed, self-describing decision line; `out=None`
    (the `_goal_sync_attempt` CLI caller) is byte-identical to before. The same
    opt-in-out-dict shape #594 gave `send_verified`."""
    now = now if now is not None else time.time()
    if watchdog._owner_disabled("goal"):
        _log_goal_sync("SKIP disabled-by-owner sid=%s cwd=%s" % (sid, cwd))
        return "skip:disabled"
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    sleep_fn = sleep_fn or time.sleep

    # #1113 -- the stale-rearm typing path is REMOVED: a machine /goal keystroke
    # NEVER reaches an ACTIVE, armed loop (the >10x david1-3 regression). A
    # leftover on-disk `stale-rearm` request (recorded before the retire) is
    # dropped TERMINALLY here, before any pane resolution / keystroke, so
    # goal_sweep clears it and it is never typed. `goal_dark_watch` no longer
    # records this origin, so on a settled fleet this is only ever a one-sweep
    # cleanup of a pre-#1113 request.
    if origin == _GOAL_STALE_REARM_ORIGIN:
        _log_goal_sync("DROP stale-rearm-retired sid=%s cwd=%s" % (sid, cwd))
        return "drop:stale-rearm-retired"

    # #1113 RECURRENCE -- STRUCTURED-armed refusal, before ANY pane work or
    # keystroke. The >10x david1-3 regression (23.9): a `dark-rearm` typed a
    # 3.7 kB /goal into an ACTIVE, armed loop because the PANE render read dark
    # (the `◎ /goal` glyph scrolled off behind the stranded draft) while the
    # watchdog's STRUCTURED goal_mark state was "set" (one-glance logged
    # `armed=yes src=goal_mark`). A pane glyph / render absence can NEVER on its
    # own authorise typing a /goal over the structured truth (the #486 direction:
    # structured state over pane heuristics). So EVERY watchdog-originated re-arm
    # origin (`_GOAL_WATCHDOG_REARM_ORIGINS`: dark/auth/fulfilled/answer-rearm +
    # declared-virgin) is refused with ZERO keystrokes while the transcript-
    # derived goal_mark state for this sid is "set" -- a template / condition
    # change waits for the next NATURAL arm (session death -> a new sid, the
    # owner's own `/autopilot`), never a re-typed keystroke. The `self-callback`
    # (owner-typed /autopilot) origin is NOT a watchdog re-arm and is never
    # refused here (the owner is at the keyboard). A VIRGIN arm (declared-virgin)
    # legitimately needs NO prior goal, so it proceeds only when NO goal_mark
    # record exists (state None) -- a genuinely fresh / crashed-and-restarted
    # session. STRUCTURED belt above the pane-based `drop:already-armed` check
    # below (which still governs when goal_mark is absent/stale but the pane
    # clearly renders armed). The DESIGN-OWNED consequence -- this refusal
    # supersedes answer-rearm (#890) / fulfilled-rearm (#764) keystroke recovery
    # (both fire ONLY at mark=="set"), the downstream gates then govern only the
    # NOT-set cases, and a full origin tombstone is a cross-cutting follow-up --
    # is documented on `_structured_goal_mark_state`.
    if origin in _GOAL_WATCHDOG_REARM_ORIGINS \
            and _structured_goal_mark_state(sid, state) == "set":
        _mig_ok, _mig_why = _stream_migrate.delivery_ok(  # #1143 stream rule only
            origin, authority, lambda: _structured_goal_mark_state(
                sid, state, with_mark=True),
            lambda: watchdog.find_active_transcript(projects_dir, cwd), now, sid)
        if not _mig_ok:
            _log_goal_sync("REFUSE structured-armed sid=%s cwd=%s origin=%s "
                           "(refuse:structured-armed goal_mark=set%s)"
                           % (sid, cwd, origin, ("; stream-migrate: %s"
                                                 % _mig_why) if _mig_why else ""))
            return "drop:already-armed"
        _log_goal_sync("PASS structured-armed sid=%s cwd=%s origin=%s (%s)"
                       % (sid, cwd, origin, _mig_why))

    # #1038 -- the keystroke NUDGE identity, derived from WHETHER this cwd is a
    # DECLARED managed window (see `_declared_window_nudge`): a declared window
    # rides the ALWAYS-ON `goal-arm` recovery nudge (never suppressed by the
    # #1023 machine-nudge switch) regardless of origin; every other box keeps
    # the staged PRIORITY `goal-sweep`, byte-identical to before.
    _nudge = ("goal-arm" if origin == _stream_migrate.ORIGIN  # #1128: owner-
              else _declared_window_nudge(cwd))  # authorized always-on watcher

    # Hard age cap -- checked first, no pane resolution needed. Unlike
    # compact, an expired goal-arm is not harmless: PING once (deduped on
    # session+request-ts, so a later fresh request gets its own chance).
    if request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > GOAL_REQUEST_MAX_AGE_S:
            _log_goal_sync("SKIP expired sid=%s cwd=%s origin=%s"
                           % (sid, cwd, origin))
            # #675-review -- SILENT expiry for the auth-rearm AND #764
            # fulfilled-rearm origins (#1113: the stale-rearm origin is retired
            # and dropped `drop:stale-rearm-retired` far above, so it never
            # reaches this expiry gate). auth-rearm: an auth blip is
            # owner-ruled NORMAL, silence + mechanical recovery only (#662/#676).
            # fulfilled-rearm: a COMPLETED loop whose delivery was deferred (most
            # often skip:recent-human) is neither dead nor abandoned, and its
            # request is re-recorded next fulfilled sweep under its own min-gap/
            # cap — a "re-run /autopilot" ping (esp. to the very human whose
            # PRESENCE deferred it) is exactly the #675 banned shape, and after
            # each expiry a FRESH request would give a FRESH goalarm-expired: key
            # -> a present owner pinged ~every 31 min up to the 12/day cap. Only
            # a dark-rearm (a genuinely DEAD autopilot) / normal origin pings.
            # #1038 declared-virgin is ALSO silent: a present owner deferring the
            # virgin arm of their own declared window is not a dark autopilot, and
            # `_declared_virgin_scan` re-records it next idle sweep -- a "re-run
            # /autopilot" ping to the very owner sitting in the window is the same
            # #675 banned shape. dark_watch/the virgin scan re-detect the silenced
            # origins next sweep. Still returns "expired" -> goal_sweep clears it.
            if (send_fn is not None and not dry_run
                    and origin not in (_GOAL_AUTH_REARM_ORIGIN,
                                       _GOAL_FULFILLED_REARM_ORIGIN,
                                       _GOAL_ANSWER_REARM_ORIGIN,
                                       _GOAL_DECLARED_VIRGIN_ORIGIN,
                                       _stream_migrate.ORIGIN)):
                from notify import stream_redirect
                pid_for_owner = _compact._find_pane_for_session(
                    sid, cwd, run=run, projects_dir=projects_dir)
                owner = (stream_redirect(watchdog.pane_owner(pid_for_owner, run))
                         if pid_for_owner else None)
                send_fn(
                    "⚠️ **%s** — /goal sa nepodarilo automaticky "
                    "nastaviť (požiadavka vypršala). Spústi "
                    "prosím `/autopilot` znova."
                    % watchdog.project_label(cwd),
                    owner=owner or None,
                    dedup_key="goalarm-expired:%s:%d" % (sid, int(request_ts)),
                    dry_run=dry_run)
            return "expired"

    # #675 -- the tighter dark-rearm freshness gate (#524) moved BELOW the
    # recent-human check (see its new position after that check).

    pid = _compact._find_pane_for_session(sid, cwd, run=run, projects_dir=projects_dir)
    if not pid:
        _log_goal_sync("SKIP no-pane sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-pane"
    # #624 -- surface the family loc for goal_sweep's journal line, from the
    # SAME `_pane_location` every sibling goal line uses (no parallel derivation).
    if out is not None:
        out["loc"] = watchdog._pane_location(pid, run) or pid
    if watchdog.pane_in_mode(pid, run):
        _log_goal_sync("SKIP in-mode sid=%s cwd=%s" % (sid, cwd))
        return "skip:in-mode"
    captured = watchdog.capture_pane(pid, run, lines=40)
    if watchdog.pane_waiting_on_user(captured):
        _log_goal_sync("SKIP dialog-open sid=%s cwd=%s" % (sid, cwd))
        return "skip:dialog-open"

    tpath = None            # #566: defined for the case-(a) recovery below even
                            # when there is no active transcript (normal origin)
    mark = None             # the newest marker from the 4 MB tail, read below
                            # when a transcript exists; used ONLY for the
                            # cleared-after-request check (#170). (#1113: the
                            # #623 stale-rearm REPLACE that re-read via
                            # seed_goal_marker is removed.)
    tinfo = watchdog.find_active_transcript(projects_dir, cwd)
    if tinfo:
        tpath, _tmtime = tinfo
        _off, mark = watchdog.scan_goal_markers(tpath)
        if mark is not None and mark.get("state") == "cleared":
            mts = mark.get("ts")
            if mts is not None and request_ts is not None and mts > request_ts:
                _log_goal_sync("DROP cleared-after-request sid=%s cwd=%s"
                               % (sid, cwd))
                return "drop:cleared-after-request"
        # #478 -- a watchdog-INITIATED auto-re-arm (origin="dark-rearm") must
        # honour the recent-human gate at the keystroke point, UNLIKE the
        # user's own /autopilot callback (whose origin IS the user). Never
        # type /goal into a pane a human just touched -- they may have
        # deliberately stopped the loop without a `/goal clear`. Left pending
        # (a "skip:" word) so a later sweep re-tries once the human leaves,
        # or the 30-min age cap eventually expires it with the "arm failed"
        # ping. `tpath` is guaranteed defined here (pane resolution above
        # already required an active transcript). #675 -- auth-rearm is ALSO
        # watchdog-initiated, so it honours the SAME gate.
        if origin in _GOAL_WATCHDOG_REARM_ORIGINS:
            # #675 -- delivery passes the SMALL future clock-skew tolerance (not
            # the full window): a grossly-future presence marker vetoing this
            # re-arm for ~30 min is the exact starve this ticket fixes. Every
            # OTHER caller (incl. the destructive clears) keeps the symmetric
            # default -- see `_goal_autoarm_recent_human_activity`.
            recent, reason = watchdog._goal_autoarm_recent_human_activity(
                sid, tpath, now, future_skew_s=watchdog.GOAL_PRESENCE_FUTURE_SKEW_S,
                pane_target=pid, run=run)   # #731 -- + the client-input signal
            if recent:
                _log_goal_sync("SKIP recent-human(%s) sid=%s cwd=%s -> %s"
                               % (origin, sid, cwd, reason))
                if out is not None:
                    out["detail"] = reason   # #624 -- the `presence marker Ns old`
                return "skip:recent-human"
    elif origin in _GOAL_WATCHDOG_REARM_ORIGINS:
        # #478 review MINOR — no active transcript (a delete/archive race
        # between pane resolution's own transcript match and this re-query)
        # means the recent-human gate cannot run. For a watchdog-INITIATED
        # origin, refuse on unprovable state rather than type blind. Non-terminal
        # "skip:" -> stays pending; a later sweep (or the 30-min age cap) resolves
        # it. #675: auth-rearm honours the SAME gate.
        _log_goal_sync("SKIP no-transcript(%s) sid=%s cwd=%s" % (origin, sid, cwd))
        return "skip:no-transcript"

    # #524/#675 -- the tighter dark/auth-rearm freshness gate, REACHED ONLY AFTER
    # the recent-human check above. A dark-rearm / auth-rearm older than
    # GOAL_DARK_REARM_STALE_S was recorded from a dark READ that has since gone
    # stale -> DROP (terminal), never type it late (the H1 concern from #524). It
    # fires ONLY for a request that RESOLVED a live idle pane + transcript with NO
    # human present (the at-rest idle-dark case) — a request the owner's PRESENCE
    # is deferring returns skip:recent-human above, and one stuck on
    # no-pane/in-mode/dialog/no-transcript returns its own skip BEFORE reaching
    # here; those survive to the 30-min GOAL_REQUEST_MAX_AGE_S cap instead (where
    # a dark-rearm pings and an auth-rearm expires SILENTLY).
    # #764 ACCEPTED RESIDUAL (review 🔵): fulfilled-rearm is DELIBERATELY NOT in
    # this stale gate. Its trigger read (🏁 + fresh open>0) could be delivered up
    # to GOAL_REQUEST_MAX_AGE_S (30 min) stale, by which time another box may have
    # drained the backlog -- but the outcome is benign (the re-armed loop proves 0
    # and stops), and adding it would DROP terminally at 300s then re-record every
    # 600s (min-gap) = MORE churn than the silent 30-min expiry + re-record the
    # fulfilled lane already has. The min-gap/daily cap bound it, not this gate.
    if origin in (_GOAL_REARM_ORIGIN, _GOAL_AUTH_REARM_ORIGIN) and request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > GOAL_DARK_REARM_STALE_S:
            _log_goal_sync("DROP-AT-DELIVERY:stale-request sid=%s cwd=%s age=%ds"
                           % (sid, cwd, int(age)))
            return "drop:stale-rearm"

    # Tri-state already-armed check.
    armed = watchdog.pane_goal_armed(captured)
    if armed is True:
        # #1113 -- UNCONDITIONAL for EVERY origin: a machine /goal keystroke NEVER
        # reaches a `◎ /goal` armed footer. The #623 stale-rearm carve-out (a
        # RE-VERIFY-then-REPLACE into the live box) is DELETED -- it typed a
        # 3.7 kB payload into david1-3's ACTIVE loops (the >10x regression); a
        # template change now waits for the next NATURAL arm. Every other re-arm
        # origin only targets a DARK footer by its own gate, so this is a belt.
        _log_goal_sync("DROP already-armed sid=%s cwd=%s" % (sid, cwd))
        return "drop:already-armed"
    if armed is None:
        _log_goal_sync("SKIP undeterminable sid=%s cwd=%s" % (sid, cwd))
        return "skip:undeterminable"

    # #752 -- the pre-keystroke client-input DEFER is now ORIGIN-SCOPED, on the
    # SAME partition as the recent-human gate above. The user's OWN
    # `self-callback` /autopilot arm IS the owner having just typed the command,
    # so their presence at the keyboard is NEVER a reason to defer it (owner
    # ruling 2026-08-30) -- the request proceeds on the first idle sweep;
    # safety is carried by the idle-box/busy gate below + deliver_with_stash
    # draft protection (#35, stashes+restores the owner's just-typed text) +
    # #737/#746 verified typing. ACCEPTED RESIDUAL (owner-ruled): an owner
    # typing CONCURRENTLY with this self-callback type gets interleaved
    # keystrokes, and a corrupt-type undo (len-based backspaces) can eat the
    # human's interleaved chars -- stash+verified-typing BOUND, not eliminate,
    # that window; the owner accepts it (never deferring their own arm).
    # A WATCHDOG-initiated re-arm (dark/stale/auth)
    # keeps the 5-min veto: an unexpected /goal keystroke into a human-active
    # pane is the injection hazard (the gk login-cancel class). This is NOT
    # dead for those origins -- it is a LATER re-read of `#{client_activity}`
    # closest to the keystroke, catching a human who began typing AFTER the
    # recent-human gate passed but BEFORE the keystroke (the #731 rationale).
    if origin in _GOAL_WATCHDOG_REARM_ORIGINS:
        _cskip = _goal_client_active_skip(sid, cwd, pid, run, now, out)
        if _cskip:
            return _cskip

    # #1092 (e) -- a watchdog RE-ARM types into an ACTIVE (idle) stream pane, so it
    # goes through the SAME per-pane typing budget as a nudge: at most
    # PANE_ATTEMPT_BUDGET typing attempts into this pane per rolling hour, across
    # ALL kinds. deliver_goal delivers via _send_goal_verified / deliver_with_stash
    # (not send_verified), so the budget is enforced HERE, right before the
    # keystroke and after the human-active guards above. Refused BEFORE any
    # keystroke -> a zero-keystroke, non-terminal defer (retry next sweep once the
    # rolling hour frees a slot, or the 30-min age cap expires it silently). The
    # user's OWN `self-callback` arm is NOT a watchdog re-arm and is never gated
    # here (the owner at the keyboard). This is the second writer the addendum
    # names: a template change can no longer storm a pane's prompt via re-arms.
    if origin in _GOAL_WATCHDOG_REARM_ORIGINS:
        if not _nudge_gate.pane_budget_ok(state, pid, now):
            _log_goal_sync("SKIP pane-budget(%s) sid=%s cwd=%s" % (origin, sid, cwd))
            if out is not None:
                out["detail"] = _nudge_gate.pane_budget_hold_reason(state, pid, now)
            return "skip:pane-budget"

    # #1110 -- TRANSCRIPT-LIVENESS gate, evaluated BEFORE the render gate. A
    # live turn renders a bare `❯` box between tool rounds, byte-identical to a
    # genuinely idle prompt (the #1104 spinner belt cannot see it: there is no
    # spinner row between tool calls), so the render alone is NOT proof the turn
    # ended. The session TRANSCRIPT is the structured truth (#486): a running
    # turn appends an entry at every tool round. When it was written within the
    # live window, DEFER with ZERO keystrokes -- never type a /goal into a
    # running turn (the swallowed-Enter + attempt-cap DROP the dev1 songplayer
    # 22.9. hit). A zero-keystroke, non-terminal, non-counting defer (not in
    # `_GOAL_KEYSTROKE_SKIPS`): the next sweep re-evaluates once the turn ends. A
    # missing/unreadable transcript is NOT a liveness signal -> no defer here
    # (fall through to the render gate, never a new wedge). `_tage` (the
    # pre-keystroke age) is reused by the confirm split (`_verify_fail_word`).
    _tage = _turn_liveness.transcript_age_s(tpath, now)
    if _turn_liveness.turn_live(_tage):
        # #1110-review -- clamp a small-negative live age (a write a moment ahead
        # of the frozen sweep `now`, within the future-skew floor) to 0 so the
        # human-facing "advanced Ns ago" never renders a negative "advanced -3s".
        _tage_ago = max(0, int(_tage))
        _log_goal_sync("SKIP busy-transcript sid=%s cwd=%s tage=%d"
                       % (sid, cwd, _tage_ago))
        if out is not None:
            out["detail"] = "transcript advanced %ds ago" % _tage_ago
        return "skip:busy-transcript"

    kind, draft = watchdog._classify_boundary(captured)
    if kind == "no-input-line":
        _log_goal_sync("SKIP no-input-line sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-input-line"
    # `_pane_busy_waiting`: a "Waiting for N background agents" pane reads
    # kind="input" (bare `❯`, spinner a row above) so a submit is swallowed and
    # the /goal parks orphaned -- defer, no keystroke (#720/#714 primitive).
    # #1023: idle-pane only — a busy Waiting pane ALWAYS defers (the #921 aged
    # override that typed into a long-busy pane is removed).
    if kind == "busy":
        _log_goal_sync("SKIP busy sid=%s cwd=%s" % (sid, cwd))
        return "skip:busy"
    if _ops_wait_recheck._pane_busy_waiting(captured):
        _log_goal_sync("SKIP busy sid=%s cwd=%s" % (sid, cwd))
        return "skip:busy"

    if draft:
        # Mark provenance BEFORE the attempt (regardless of outcome) so
        # the shared janitor (#372) can recover a stuck stash send for
        # THIS pane -- mirrors the bare-box branch immediately below
        # (#403-review MAJOR M1: this branch used to mark only on success
        # and never clear, exactly backwards).
        watchdog._janitor_mark_watch(state, pid, now)
        # #566 -- if the box ALREADY holds our OWN swallowed COMPLETE /goal (a
        # prior attempt typed it but the Enter was swallowed / raced), SUBMIT it
        # in place rather than re-stashing our own /goal into the single slot and
        # aborting forever (the #501 lane-nudge lesson, one payload class over).
        # OWNED recovery, coupled to the janitor's own proof: provenance (the
        # mark just set / a durable park) + an EXACT head+tail completeness match
        # against `text` + the recent-human gate -- so a foreign draft or a
        # truncated own type is NEVER submitted, and a human-active pane vetoes.
        if _submit_stranded_own_goal(sid, cwd, text, pid, captured, tpath,
                                     run, state, now, sleep_fn, logs,
                                     nudge=_nudge):
            # #1092 (e) -- recovering our OWN stranded /goal was a keystroke into
            # the pane; count it against the per-pane budget for a watchdog re-arm.
            if origin in _GOAL_WATCHDOG_REARM_ORIGINS:
                _nudge_gate.mark_pane_attempt(state, pid, now)
            watchdog._janitor_clear_watch(state, pid)
            if not _await_goal_armed(pid, run, sleep_fn):   # #720 same arm-confirm
                _log_goal_sync("SKIP not-armed(stranded) sid=%s cwd=%s" % (sid, cwd))
                _log_arm_confirm_fail(sid, cwd, text, pid, run,
                                      sleep_fn=sleep_fn, tpath=tpath,
                                      state=state, now=now)  # #731 D + #737 A + #1104 defer + #1110 tage
                # #1110 -- a keystroke that did NOT arm: verify-failed-live (the
                # transcript advanced during the confirm window = a live turn,
                # uncounted) vs verify-failed (quiet = the #731 counted class).
                return _verify_fail_word(tpath, _tage, now)
            _log_goal_sync("SEND recover-swallowed sid=%s cwd=%s" % (sid, cwd))
            return "sent"
        # #1092 (e) -- a watchdog RE-ARM types ONLY into an EMPTY box at the idle
        # prompt, NEVER stash-around a FOREIGN draft (the owner's own text): the
        # incident's second writer was the re-arm typing its whole /goal payload
        # into an active pane. So for a watchdog re-arm origin (dark/stale/auth/
        # fulfilled/answer-rearm AND declared-virgin, all in _GOAL_WATCHDOG_REARM_
        # ORIGINS), once the box holds a foreign draft (our own stranded /goal was
        # already submitted above), DEFER -- a zero-keystroke, non-terminal defer,
        # retried on the next idle sweep (a fresh-after-reboot declared-virgin
        # window is normally EMPTY, so it rarely reaches here; when it does hold a
        # human draft, deferring is the SAFE choice, never type over it). ONLY the
        # user's OWN `self-callback` arm (the owner having just typed /autopilot)
        # keeps the stash-around below (it protects the owner's just-typed draft,
        # #35) -- it is NOT a watchdog re-arm origin.
        if origin in _GOAL_WATCHDOG_REARM_ORIGINS:
            watchdog._janitor_clear_watch(state, pid)   # nothing typed -> release
            _log_goal_sync("SKIP pane-busy-draft(%s) sid=%s cwd=%s"
                           % (origin, sid, cwd))
            if out is not None:
                out["detail"] = "deferred (pane busy/draft)"
            return "skip:pane-busy-draft"
        # #488: thread `state` so deliver_with_stash can DURABLY record a park
        # it definitively creates (STASH_PARKED) -> the shared janitor reclaims
        # it after ANY delay, not just the 6h generic-mark window (the gk
        # `(1d)` gap). The record is written ONLY on an unambiguously-ours park
        # (slot was free before our own C-s), never a pre-existing foreign one,
        # and deliver_with_stash clears it on its own verified success.
        ok = watchdog.deliver_with_stash(pid, text, run, captured=captured,
                                         logs=logs, sleep_fn=sleep_fn,
                                         state=state, nudge_kind="goal",
                                         nudge=_nudge)   # #1038 declared-window -> goal-arm
        if ok:
            watchdog._janitor_clear_watch(state, pid)
            if not _await_goal_armed(pid, run, sleep_fn):   # #720 same arm-confirm
                _log_goal_sync("SKIP not-armed(stash) sid=%s cwd=%s" % (sid, cwd))
                _log_arm_confirm_fail(sid, cwd, text, pid, run,
                                      sleep_fn=sleep_fn, tpath=tpath,
                                      state=state, now=now)  # #731 D + #737 A + #1104 defer + #1110 tage
                # #1110 -- a keystroke that did NOT arm: verify-failed-live (the
                # transcript advanced during the confirm window = a live turn,
                # uncounted) vs verify-failed (quiet = the #731 counted class).
                return _verify_fail_word(tpath, _tage, now)
            _log_goal_sync("SEND stash sid=%s cwd=%s" % (sid, cwd))
            return "sent"
        _log_goal_sync("SKIP stash-abort sid=%s cwd=%s" % (sid, cwd))
        # #566 -- distinguish the PERSISTENT slot-occupied livelock (our own park
        # stale-occupies the single slot) from a TRANSIENT abort, so goal_sweep
        # orders owned recovery only for the livelock, never for a passing state.
        if isinstance(logs, list) and any(
                ln == "stash-abort: slot occupied" for ln in logs):
            return "skip:stash-abort-slot-occupied"
        return "skip:stash-abort"

    # Bare box -- verified typed send. Mark provenance BEFORE typing so the
    # shared janitor (#372) can recover a stuck send for THIS pane.
    watchdog._janitor_mark_watch(state, pid, now)
    _send_out = {}
    ok = _send_goal_verified(pid, text, run, captured=captured,
                             sleep_fn=sleep_fn, logs=logs,
                             nudge=_nudge, out=_send_out)   # #1038 declared-window -> goal-arm
    # #1092 (e) -- the type keystroke into the (empty) box was a typing attempt
    # into an active pane; count it against the per-pane budget for a watchdog
    # re-arm -- but ONLY when a keystroke was ACTUALLY sent (#1104). A swallowed
    # re-arm (typed, submit unconfirmed) DID keystroke and still counts (`typed`
    # set); an OFF-suppressed re-arm (`_send_goal_verified` bailed keystroke-free
    # through the #1002 kill switch) OR a raced-abort (box no longer bare) typed
    # NOTHING (`typed` unset) and must NOT consume the pane budget -- else
    # switching the kind ON later inherits a spent budget with zero real
    # keystrokes (the montalu1 16:04 `hold:pane-budget` with no keystrokes).
    # `out["typed"]` is the real "a keystroke was emitted" signal, not the
    # kill-switch proxy (#1104-review: the proxy over-books on a raced abort).
    if origin in _GOAL_WATCHDOG_REARM_ORIGINS and _send_out.get("typed"):
        _nudge_gate.mark_pane_attempt(state, pid, now)

    if ok:
        watchdog._janitor_clear_watch(state, pid)
        _log_goal_sync("SEND typed sid=%s cwd=%s" % (sid, cwd))
        return "sent"
    _log_goal_sync("SKIP verify-failed sid=%s cwd=%s" % (sid, cwd))
    _log_arm_confirm_fail(sid, cwd, text, pid, run,
                          sleep_fn=sleep_fn, tpath=tpath,
                          state=state, now=now)  # #731 D + #737 A + #1104 defer + #1110 tage
    # #1110 -- bare-box confirm split: verify-failed-live (transcript advanced
    # during the confirm window = a mis-timed keystroke into a live turn,
    # uncounted) vs verify-failed (quiet = the #731 swallowed-submit class).
    return _verify_fail_word(tpath, _tage, now)


def _goal_sync_attempt(sid, cwd, text, authority, origin, run=None,
                       projects_dir=None, requests_path=None, state=None,
                       now_fn=None, sleep_fn=None, send_fn=None, dry_run=False):
    """The ONE synchronous delivery attempt `goal-arm --self` makes, right
    after recording -- records the request, then ONE immediate
    `deliver_goal` call. Deliberately holds NO bounded wait (unlike
    compact's own sync attempt): the calling pane is BUSY for the ENTIRE
    duration of this CLI call, since the call itself is part of the
    session's own current turn -- a synchronous delivery can therefore
    structurally never succeed until the CLI process exits and the turn
    ends. Retrying-until-sent here would be a deadlock (the CLI waits for
    an at-rest pane; the pane only rests once the CLI exits). The REAL
    delivery path is the periodic sweep (`goal_sweep`, job 9's new body),
    which re-evaluates this SAME still-pending request every ~60s once the
    pane genuinely goes idle.

    Returns the disposition word `deliver_goal` returns, or
    `"skip:no-session"` if recording itself failed. Clears the request on
    any TERMINAL word. Prints nothing -- the caller owns stdout."""
    now_fn = now_fn or time.time
    ok = record_goal_request(sid, cwd, text, authority, now=now_fn(),
                             path=requests_path, origin=origin)
    if not ok:
        return "skip:no-session"
    entry = load_goal_requests(requests_path).get(sid) or {}
    req_ts = entry.get("ts")
    word = deliver_goal(sid, cwd, entry.get("text", text),
                        entry.get("authority", authority), run=run,
                        projects_dir=projects_dir, now=now_fn(), state=state,
                        request_ts=req_ts, send_fn=send_fn, dry_run=dry_run,
                        sleep_fn=sleep_fn, origin=entry.get("origin", origin))
    if word in _GOAL_TERMINAL_WORDS:
        clear_goal_request(sid, path=requests_path)
    return word


def _declared_virgin_scan(now, run=None, dry_run=False, projects_dir=None,
                          requests_path=None, state=None, rearm_fn=None):
    """#1038 -- job 9's post-reboot VIRGIN-arm pre-pass. For every live candidate
    pane whose cwd resolves to a DECLARED managed window on THIS box
    (`cli_concurrency.resolve_concurrency(cwd)` source=="role" == the pane cwd
    matched a `box_windows` entry) that is at an idle prompt, DARK
    (`pane_goal_armed` is False), NEVER-armed (NO `Goal set:`/`cleared` marker
    anywhere in the transcript tail -- #170-safe: a user-cleared OR
    armed-then-dark session carries a marker and is left to `deliver_goal`'s
    cleared-guard / `goal_dark_watch`), with NO pending request, and past the
    per-sid virgin rate floor, RECORD a `declared-virgin` goal-arm request with
    the variant resolved by the SAME renderer (`_default_rearm_fn` ->
    `goal_template_for` -> `render_goal_line`, never a hand-written text).
    `goal_sweep`'s own per-request loop DELIVERS it the SAME sweep, via the
    always-on `goal-arm` recovery nudge (`deliver_goal` derives that from the
    declared cwd), so the owner's declared windows come back armed after a reboot
    with zero staging.

    Records ONLY -- NEVER types (the keystroke + every pane-safety gate, incl. the
    recent-human hold, lives in `deliver_goal`). A dry-run logs would-arm lines
    and mutates no state. `rearm_fn(cwd) -> (text, authority)` is the injected
    variant source (default `_default_rearm_fn`, the SAME seam `goal_dark_watch`
    uses)."""
    logs = []
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    rf = rearm_fn or _default_rearm_fn
    try:
        import cli_concurrency
        import cli_fleet
    except Exception:  # noqa: BLE001 -- resolver unimportable at the watchdog path
        return logs
    # #1038-review — resolve THIS box's declared windows ONCE (a cheap in-memory
    # read over REMOTE_HOSTS) and EARLY-RETURN when there are none: every
    # non-declared box (montalu/miva/...) then pays ZERO per-pane cost, and the
    # per-pane declared-window CHECK below REUSES this list (never re-derived per
    # pane). NOTE: the variant TEXT resolution (`rf` -> `goal_template_for` ->
    # `resolve_concurrency`) does re-read box_windows, but ONLY for a pane
    # actually being virgin-armed (rare) -- the hot path (the gate) is single-read.
    try:
        box_wins = cli_fleet.box_windows(cli_concurrency._current_user())
    except Exception:  # noqa: BLE001 -- unresolvable user/table -> treat as no declared windows
        box_wins = []
    if not box_wins:
        return logs
    # per-sid virgin rate-floor state (min-gap + anti-keystorm strop). Reap dead
    # entries by age: a sid that armed stops being scanned (armed/marker skip
    # below), so its list would otherwise leak (the #519/#764 reaper pattern).
    vstate = (state.setdefault("goal_virgin_arm", {})
              if (state is not None and not dry_run) else {})
    _day = 24 * 3600
    for _vsid in [k for k, v in list(vstate.items())
                  if not (isinstance(v, list)
                          and any(isinstance(t, (int, float))
                                  and 0 <= (now - t) <= _day for t in v))]:
        vstate.pop(_vsid, None)
    reqs = load_goal_requests(requests_path)
    try:
        panes = watchdog._reconcile_candidate_panes(run)
    except Exception:  # noqa: BLE001 -- a tmux read failure yields no candidates
        return logs
    for pid, cwd, _cmd in panes:
        if not cwd:
            continue
        # #1038-review — per-pane BODY guard. This scan runs FIRST in goal_sweep,
        # so an unexpected error in ANY per-pane primitive must not abort the
        # remaining panes NOR the per-request DELIVERY loop that follows: one bad
        # pane is skipped + logged, never propagated (the goal_dark_watch per-pane
        # discipline).
        try:
            # DECLARED-window gate. `resolve_concurrency` gives (mode, role) via
            # the ONE resolver, but its match is by CONTAINMENT (a subdir inherits
            # the window's mode). The virgin arm needs the STRICTER question — is
            # this pane THE declared window itself, not a subdir of one — so it
            # ALSO requires an EXACT cwd match (`is_exact_declared_window`,
            # #1038-review): a human sub-pane cd'd into a subdirectory of the
            # checkout (a worktree, an ad-hoc sub-session) is NEVER given an
            # unsolicited /goal. Reuses the box's windows resolved once above.
            mode, role, source = cli_concurrency.resolve_concurrency(
                cwd, windows=box_wins)
            if source != "role":
                continue
            if not cli_concurrency.is_exact_declared_window(cwd, windows=box_wins):
                continue                      # a SUBDIR of a declared window -> never virgin-arm (only THE window's own pane)
            tinfo = watchdog.find_active_transcript(projects_dir, cwd)
            if not tinfo:
                continue
            tpath, _tmtime = tinfo
            sid = tpath.stem
            if sid in reqs:
                continue                      # a request is already pending -> the per-request loop owns it
            loc = watchdog._pane_location(pid, run) or cwd
            if watchdog.pane_in_mode(pid, run):
                continue                      # copy-mode -> unreadable, skip silently
            captured = watchdog.capture_pane(pid, run, lines=40)
            armed = watchdog.pane_goal_armed(captured)
            if armed is not False:
                continue                      # True = armed, None = undeterminable -> never virgin-arm on doubt
            # #170-safe VIRGIN proof: PROVABLY never-armed only when
            # `seed_goal_marker` read the WHOLE transcript (status "none-bof") and
            # found NO marker. A marker present (armed-then-dark = dark-rearm's
            # job, OR user-cleared = never re-arm, #170) is NOT virgin; and
            # "unknown-past-cap" (a marker MAY sit deeper than the 32 MB seed cap)
            # is UNDETERMINABLE, so it is skipped exactly like armed=None -- never
            # virgin-arm on doubt (#1038-review: ignoring the seed status re-armed
            # a user-cleared window whose clear had scrolled past the seed cap).
            _soff, mark, _sst = watchdog.seed_goal_marker(tpath)
            if mark is not None:
                continue                      # a real marker -> armed-then-dark or user-cleared, not virgin
            if _sst != "none-bof":
                logs.append("SKIP (virgin-arm) %s sid=%s -> skip:marker-%s"
                            % (loc, sid, _sst))
                continue
            # per-sid rate floor (min-gap + strop): bounds the cap-drop re-record
            # livelock; the COMMON case never reaches it (armed/marker skip above).
            ok, pruned, reason = _recovery_rearm_ok(
                vstate.get(sid), now, GOAL_VIRGIN_REARM_MIN_GAP_S,
                GOAL_VIRGIN_REARM_MAX_PER_DAY)
            if not ok:
                logs.append("HOLD (virgin-arm) %s sid=%s -> hold:rate-%s"
                            % (loc, sid, reason))
                continue
            text, authority = rf(cwd)
            if not text:
                logs.append("SKIP (virgin-arm) %s sid=%s -> skip:no-template"
                            % (loc, sid))
                continue
            variant = cli_concurrency.goal_variant_label(mode, role)
            if dry_run:
                logs.append("DRY-RUN virgin-arm %s sid=%s would record declared-virgin %s"
                            % (loc, sid, variant))
                continue
            record_goal_request(sid, cwd, text, authority, now=now,
                                path=requests_path,
                                origin=_GOAL_DECLARED_VIRGIN_ORIGIN)
            vstate[sid] = pruned + [now]
            # #1038 item (2): the variant is NAMED here (journal) + in `status` --
            # NOT typed into the pane (a 2nd line = a spurious conversation message)
            # and NOT baked into render_goal_line (would break goal-inventory --check).
            logs.append("RECORD (virgin-arm) %s sid=%s -> declared-virgin armed: %s"
                        % (loc, sid, variant))
        except Exception as e:  # noqa: BLE001 -- one bad pane never aborts the scan / the delivery loop
            logs.append("SKIP (virgin-arm) %s -> skip:pane-error (%r)"
                        % (watchdog.project_label(cwd), e))
            continue
    return logs


def goal_sweep(now, run=None, dry_run=False, projects_dir=None,
              requests_path=None, state=None, handled=None, send_fn=None,
              sleep_fn=None, rearm_fn=None):
    """The periodic re-evaluation of every PENDING goal-arm request (job
    9's new body -- replaces the old arm-question viewport scan and virgin-
    candidate heuristic entirely). #1038 -- a DECLARED-window VIRGIN-arm
    pre-pass (`_declared_virgin_scan`) runs FIRST and RECORDS a request for a
    fresh post-reboot declared window; the per-request loop below then DELIVERS
    it the SAME sweep. Re-checks each still-pending request's
    SAME unmodified conditions every sweep. A request that keeps failing a
    condition sits until it clears (delivered next sweep) or the age cap
    discards it -- with ONE bound (#731): a request whose KEYSTROKE deliveries
    (`_GOAL_KEYSTROKE_SKIPS`: verify-failed / stash-abort) have failed
    `GOAL_DELIVERY_ATTEMPT_CAP` times is DROPPED terminally BEFORE a further
    keystroke, so a never-arming request can no longer re-type into the prompt
    forever (the montalu4 retype livelock). A zero-keystroke defer
    (undeterminable / busy / recent-human / client-active / ...) never counts.

    `handled` (optional, a `set()`): the SAME per-sweep set job 14/20's
    lane-occupancy nudge populate -- a sid already sent a keystroke burst
    THIS sweep (by /compact or the lane nudge) is skipped, never double-
    typed into."""
    logs = []
    if watchdog._owner_disabled("goal"):
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
        return logs
    # #1038 -- VIRGIN-arm pre-pass FIRST: record a `declared-virgin` request for
    # any fresh post-reboot DECLARED window, so the per-request loop below
    # delivers it the SAME sweep. Records only; every keystroke/gate is below.
    logs += _declared_virgin_scan(now, run=run, dry_run=dry_run,
                                  projects_dir=projects_dir,
                                  requests_path=requests_path, state=state,
                                  rearm_fn=rearm_fn)
    reqs = load_goal_requests(requests_path)
    # #566 -- the per-sid consecutive `slot occupied` livelock counter. Reap any
    # sid no longer pending (an episode-end pop, not a rolling window): the store
    # can never outlive its request, which itself has the 30-min age cap, so this
    # is bounded with no separate age reaper needed.
    # #516 (#566-review A2) -- a dry-run must not mutate persisted state, not
    # even the benign `setdefault` of an empty dict: use a throwaway local on a
    # dry-run (the counter is never read or written on a dry-run sweep anyway).
    aborts = (state.setdefault("goal_stash_abort", {})
              if (state is not None and not dry_run) else {})
    for _dead in [k for k in aborts if k not in reqs]:
        aborts.pop(_dead, None)
    for sid, entry in list(reqs.items()):
        if not isinstance(entry, dict):
            # #624-review -- a corrupt non-dict entry is malformed like the
            # empty-text one below: NAME the drop (no dict -> no cwd, so no loc)
            # and CLEAR it, so it is neither silently re-skipped every sweep nor
            # re-logged forever.
            logs.append("DROP (goal-sweep) sid=%s -> drop:non-dict-entry" % sid)
            aborts.pop(sid, None)
            clear_goal_request(sid, path=requests_path)
            continue
        cwd = entry.get("cwd", "")
        text = entry.get("text", "")
        authority = entry.get("authority", "")
        if not text:
            # malformed/legacy entry -- nothing to type; drop rather than
            # retry forever on an empty payload. #624 -- name the drop, never
            # a SILENT branch (no pane resolved -> the cwd-derived label).
            logs.append("DROP (goal-sweep) %s sid=%s -> drop:malformed-empty "
                        "(no text)" % (watchdog.project_label(cwd), sid))
            aborts.pop(sid, None)
            clear_goal_request(sid, path=requests_path)
            continue
        if handled is not None and sid in handled:
            logs.append("SKIP (goal-sweep) %s sid=%s -> handled this sweep already"
                        % (watchdog.project_label(cwd), sid))
            continue
        if dry_run:
            logs.append("DRY-RUN goal-sweep %s would evaluate sid=%s"
                        % (watchdog.project_label(cwd), sid))
            continue
        # #731 -- the per-request delivery-attempt CAP, checked BEFORE deliver_goal
        # so a request that already failed CAP keystroke deliveries is DROPPED
        # terminally (never a CAP+1 keystroke): clean up any leftover /goal text
        # it stranded in the prompt, clear it, and ping (origin-gated). This is
        # the montalu4 retype-livelock exit -- `dl_fails` is INT-guarded across
        # the JSON boundary (#714).
        try:
            dl_fails = int(entry.get("dl_fails", 0))
        except (TypeError, ValueError):
            dl_fails = 0
        # #1110-review -- the SEPARATE looser bound on verify-failed-live (our own
        # accept-as-plain-prompt submit advances the transcript identically to a
        # live turn, so the confirm split cannot count it toward the strict cap;
        # this restores the #731 give-up guarantee for that livelock). INT-guarded
        # across the JSON boundary exactly like `dl_fails` (#714).
        try:
            dl_live_fails = int(entry.get("dl_live_fails", 0))
        except (TypeError, ValueError):
            dl_live_fails = 0
        if (dl_fails >= GOAL_DELIVERY_ATTEMPT_CAP
                or dl_live_fails >= GOAL_DELIVERY_LIVE_ATTEMPT_CAP):
            dl_last = entry.get("dl_last", "")
            _dl_count = max(dl_fails, dl_live_fails)   # the count that tripped the cap
            clog, leftover, loc = _goal_cap_drop(
                sid, cwd, text, entry.get("origin"), _dl_count,
                entry.get("ts"), run, projects_dir, state, now, send_fn,
                dry_run, sleep_fn)
            logs += clog
            aborts.pop(sid, None)
            clear_goal_request(sid, path=requests_path)
            # #921 residual (review M4): a drop:attempt-cap means keystrokes
            # WERE typed (just not verified). Record one attempt so the origin's
            # rate limit counts typed episodes, not only verified arms.
            _record_delivered_attempt(state, entry.get("origin"), sid, now)
            logs.append("DROP (goal-sweep) %s sid=%s -> drop:attempt-cap "
                        "(%d keystroke deliveries failed, last=%s; leftover=%s)"
                        % (loc, sid, _dl_count, dl_last, leftover))
            if handled is not None:
                handled.add(sid)
            continue
        # #741 WRITER-SIDE LATCH: a pending /compact for this session HOLDS the
        # goal-arm keystroke -- never push a new batch's /goal into the pane while
        # a drained-boundary compact is still waiting for its quiet window. Placed
        # AFTER the #731 cap-drop (which CLEANS a stranded /goal draft out of the
        # prompt -- a pane-UNBLOCKING keystroke that pushes no work, exactly like
        # goal_dark_watch's janitor/stranded-clear before ITS latch): the cleanup
        # frees the pane so the pending compact can DELIVER, then this holds the
        # actual re-arm keystroke. Leave the request PENDING (goal_sweep re-tries
        # once the compact clears); log the hold, never a silent skip.
        if _compact.pending_compact_hold(sid, now):   # #848 bounded
            logs.append("HOLD (goal-sweep) %s sid=%s -> hold:compact-pending "
                        "(pending /compact; no goal-arm keystroke until it "
                        "delivers)" % (watchdog.project_label(cwd), sid))
            continue
        # a fresh per-request log list so the stash-abort REASON is derivable
        # (deliver_goal returns `skip:stash-abort-slot-occupied` only for the
        # slot-occupied livelock, never a transient abort).
        call_logs = []
        # #624 -- `out` carries deliver_goal's family loc + skip detail back for
        # a loc-keyed, self-describing journal line (falls back to the cwd label
        # for an early return that never resolved a pane).
        _out = {}
        word = deliver_goal(sid, cwd, text, authority, run=run,
                            projects_dir=projects_dir, now=now, state=state,
                            request_ts=entry.get("ts"), send_fn=send_fn,
                            dry_run=dry_run, sleep_fn=sleep_fn,
                            origin=entry.get("origin"), logs=call_logs, out=_out)
        loc = _out.get("loc") or watchdog.project_label(cwd)
        dsuf = (" (%s)" % _out["detail"]) if _out.get("detail") else ""
        prior_aborts = aborts.get(sid, 0)
        if word in _GOAL_TERMINAL_WORDS:
            # #921 residual: preserve abort counter across request lifetimes.
            # Only "sent" (slot freed) pops the counter. On "expired" /
            # "drop:stale-rearm" etc. the slot is still occupied — keeping the
            # counter lets the next dark-watch-created request inherit the
            # accumulated abort history (fixes the drop+re-create ping-pong
            # that reset the counter to 0 every request lifetime).
            if word == "sent":
                aborts.pop(sid, None)
            clear_goal_request(sid, path=requests_path)
        if word == "sent":
            # #921 residual: record the delivered attempt in the origin's
            # *_attempts state dict. Only "sent" deliveries count — an
            # undelivered attempt (skip:busy etc.) never fills the cap.
            _record_delivered_attempt(state, entry.get("origin"), sid, now)
            logs.append("OK (goal-sweep) %s sid=%s -> sent" % (loc, sid))
            if handled is not None:
                handled.add(sid)
        elif word == "expired":
            # #566 -- a request must not lapse in SILENCE while its delivery was
            # provably stuck by our own state: name the blocking state.
            if prior_aborts >= GOAL_STASH_ABORT_LIVELOCK:
                logs.append("LAPSE (goal-sweep) %s sid=%s (age > cap, discarded; "
                            "blocked %d sweeps on stash-abort: slot occupied)"
                            % (loc, sid, prior_aborts))
            else:
                logs.append("LAPSE (goal-sweep) %s sid=%s (age > cap, discarded)"
                            % (loc, sid))
        elif word == "skip:stash-abort-slot-occupied":
            # #566 -- count the identical livelock and, at the threshold, ORDER
            # owned janitor recovery (job 9 is not budget-deferred like job 20),
            # so the stale own stash slot is resolved BEFORE the age cap lapses.
            n = prior_aborts + 1
            aborts[sid] = n
            # #921 residual: past the ESCALATION threshold the recovery already
            # tried and failed (foreign slot) — stop re-ordering and log LOUD.
            if n >= GOAL_STASH_ABORT_ESCALATION:
                logs.append("ESCALATION (goal-sweep) %s sid=%s -> %s "
                            "PERSISTENT foreign-slot livelock (%d aborts, "
                            "recovery exhausted — manual intervention needed)"
                            % (loc, sid, word, n))
            else:
                logs.append("SKIP (goal-sweep) %s sid=%s -> %s (%d/%d)"
                            % (loc, sid, word, n, GOAL_STASH_ABORT_LIVELOCK))
                if n >= GOAL_STASH_ABORT_LIVELOCK:
                    logs += _resolve_stash_abort_livelock(
                        sid, cwd, run, projects_dir, state, now, send_fn,
                        dry_run, sleep_fn, own_payload=text)  # #737
                    if handled is not None:
                        handled.add(sid)
        else:
            aborts.pop(sid, None)
            # #731 -- a keystroke-delivering skip (verify-failed / stash-abort)
            # counts toward the per-request attempt cap; every zero-keystroke
            # defer (undeterminable / busy / busy-transcript / recent-human /
            # client-active / ...) does NOT (counting those would starve a
            # legitimate delivery, #611). #1110 -- `skip:verify-failed-live` is a
            # keystroke that DID type but the transcript advanced during the
            # confirm window: NOT in `_GOAL_KEYSTROKE_SKIPS`, so it never counts
            # toward the STRICT cap (it may be a mis-timed keystroke into a
            # genuinely live turn -- the dev1 songplayer fix: three such can no
            # longer exhaust the cap while the session is about to go idle). But
            # #1110-review -- our OWN accept-as-plain-prompt submit advances the
            # transcript identically, and that IS a real failed delivery; so it
            # counts toward the SEPARATE, looser `dl_live_fails` bound instead,
            # restoring the give-up guarantee for that livelock.
            if (entry.get("origin") == _stream_migrate.ORIGIN and (
                    word in _GOAL_KEYSTROKE_SKIPS
                    or word == "skip:verify-failed-live")):
                # #1128 guard 5: ONE typed attempt per stream-migrate request
                # (the next is the next hour's decision), never 3-6 retypes.
                clear_goal_request(sid, path=requests_path)
                dsuf += " -> dropped (stream-migrate: one typed attempt)"
            elif word in _GOAL_KEYSTROKE_SKIPS:
                _bump_goal_delivery_fail(sid, word, path=requests_path)
            elif word == "skip:verify-failed-live":
                _bump_goal_delivery_fail(sid, word, path=requests_path,
                                         field="dl_live_fails")
            logs.append("SKIP (goal-sweep) %s sid=%s -> %s%s"
                        % (loc, sid, word, dsuf))
    return logs


# --------------------------------------------------------------------------- #
# DARK-WATCH -- job 20's new body. Cross-checks each session's transcript
# marker (INTENT) against CC's own footer indicator (REALITY). On a genuine,
# DEBOUNCED mismatch it either RE-ARMS a CONFIRMED-dead loop (#478/#524 --
# records a goal-arm request for job 9 to TYPE, ONLY after K clean-dark reads
# over >= MIN_SPAN with any armed/mtime-advanced read vetoing the run, under a
# 24h attempt cap; an idle-but-ALIVE flicker never reaches it) or, when it
# cannot self-heal the loop (not workable / no template / cap hit / not yet
# confirmed), sends ONE keystroke-free Discord ping telling the user to re-run
# `/autopilot`. This function itself types NOTHING -- the keystroke happens at
# the delivery point (`deliver_goal`, origin-gated + delivery-freshness gated).
# Also runs the shared janitor recovery (#372) at the top of its per-pane loop,
# since it is the one sweep that visits every live pane every tick regardless
# of pending requests.
# --------------------------------------------------------------------------- #

# #459 -- STAGED dark-goal re-ping. Root cause (CC research, binary 2.1.232
# + upstream anthropics/claude-code issues 82546/58373/50920): /compact never
# CLEARS the goal, but the PROCESS-BOUND /goal loop can silently stop firing
# turns through/around a compaction while the transcript keeps reporting
# armed (on montalu -- this repo's own issue 76 -- the footer ALSO loses the
# glyph, which is the shape dark-watch detects). Pre-#403 the old goal_rearm
# backstop healed ~93% (14/15 measured) of these within ~2 min; #403 deleted
# it, so a compact-stalled loop now gets exactly ONE ping and, if the away
# user misses it (the 02:59-into-a-sleeping-user incident), no follow-up.
# The FIRST ping stays byte-for-byte as #403 shipped it. #804 item 4 DELETED
# the staged RE-ping (the old `GOAL_DARK_REPING_SCHEDULE_S` widening schedule +
# `GOAL_DARK_REPING_MAX` cap + `_goal_dark_reping_due`): every dark ping is
# notify-SUPPRESSED (goal-dark in SUPPRESSED_ALERT_PREFIXES, #704) AND the #795
# daily re-ask is retired, so a staged re-ping only composed a message notify
# drops -- dead code (removing it is net-LOC-down, the owner's #804 constraint).
# A dark episode now pings AT MOST ONCE; `pinged_state` is a per-episode
# already-pinged LATCH (mark_ts-keyed), still popped by the confirmed re-arm /
# FULFILLED-SILENT paths above so a genuine re-arm / fresh episode re-pings once.
# GOAL_DARK_CACHE_MAX_AGE_S stays the freshness bound the #459 first-ping gate +
# every re-arm path read against the per-cwd tickets-status obligation cache.
GOAL_DARK_CACHE_MAX_AGE_S = 3 * 24 * 3600   # ignore an obligation cache older than this

# #524 (owner decision B, 2026-08-17) -- HARDENED death-confirmation for the
# #478 auto-re-arm. The KEYSTROKE re-arm (the harmful action) may fire ONLY on
# a genuinely CONFIRMED dead loop: K consecutive clean-dark footer reads AND a
# >= MIN_SPAN unbroken run, with ANY armed read OR a liveness proof (transcript
# mtime advanced) VETOING the whole run. An idle-but-alive session (glyph
# flickers back within ~3 min, montalu 2026-08-16) never accumulates the run,
# so it is NEVER auto-typed -- the timely #459 ping still fires (owner never
# objected to the ping; a 75-min-idle loop SHOULD prompt the human, #403
# philosophy "watchdog pings, human decides"), only the TYPE is gated. Root
# cause: montalu typed /goal over a 75-min-idle-but-alive session after a
# SINGLE-sweep debounce. Structured facts can only VETO a re-arm, never CONFIRM
# one -- the only positive death evidence is persistent, unanimous glyph
# absence (heartbeat/mark/mtime are all shared by dead AND idle; the heartbeat
# even read goal_armed=no for a session whose transcript mark was `set`).
GOAL_DARK_CONFIRM_MIN_READS = 8         # K consecutive clean-dark reads to TYPE
GOAL_DARK_CONFIRM_MIN_SPAN_S = 600      # AND the run must span >= 10 min
GOAL_DARK_REARM_MAX_PER_DAY = 2         # base cap: fast auto-types per sid / 24h
# #804 mode-2 -- past the base cap the dark/stale/auth re-arm is NEVER
# silent-until-midnight (montalu2 "sam sa vypne a uz nezapne"). It keeps
# re-arming on an ESCALATING backoff from the last attempt (30m -> 1h -> 3h ->
# 6h, holding at 6h), bounded by a hard daily STROP (anti-keystorm). This
# mirrors the repo's escalating-backoff PATTERN for bounded resurrection (NOT the
# settled design's nudge_gate resurrect-family) for consistency with shipped reality + a
# lower blast radius. At 6h spacing only ~4 attempts fit a rolling 24h, so the
# strop is a safety ceiling the backoff practically never reaches -- the loop
# gets a bounded resurrection attempt every ~6h forever instead of dying at 2.
GOAL_DARK_REARM_BACKOFF_S = (30 * 60, 60 * 60, 3 * 3600, 6 * 3600)
GOAL_DARK_REARM_HARD_CAP_PER_DAY = 12   # anti-keystorm daily strop past the backoff
# #764 -- the FULFILLED-REARM rate limits (evaluated every sweep; the 🏁 proof
# replaces the dark-duration confirmation, so there is NO 8-read/600s ramp).
# MIN_GAP: >= this many seconds between fulfilled-rearms for one sid, so a
# not-yet-delivered request (a recent-human defer, a delivery race) cannot churn
# a fresh record every 60s sweep. MAX_PER_DAY: the daily cap -- higher than the
# dead-loop cap (2) because a legitimate ping-pong backlog refills and completes
# many times a day; both tunable, with a why here.
GOAL_FULFILLED_REARM_MIN_GAP_S = 600    # >= 10 min between fulfilled-rearms / sid
GOAL_FULFILLED_REARM_MAX_PER_DAY = 12   # cap: max fulfilled-rearms per sid / 24h
# #890 -- RECOVERY-class rate limits. Auth-rearm is PROVEN (CC auth event, not a
# guess), so it has its OWN rate state separate from the dead-dark 2/24h cap.
# MIN_GAP: 300s (5 min) — an auth clear + recovery happens in seconds, but a
# flapping credential (expired token, provider outage) can fire many clears/min,
# so min-gap prevents a keystroke livelock into a broken session. MAX_PER_DAY: 12
# (the fulfilled precedent) — a normal fleet day can have several account switches
# (claudy drain passes, manual evacuations; #890 evidence: 2 in one afternoon).
GOAL_AUTH_REARM_MIN_GAP_S = 300         # >= 5 min between auth-rearms / sid
GOAL_AUTH_REARM_MAX_PER_DAY = 12        # cap: max auth-rearms per sid / 24h
# #890 -- answer-rearm rate limits. The owner answers a handful of ❓ questions a
# day; 6/day bounds a pathological ❓→answer→❓ ping-pong.
GOAL_ANSWER_REARM_MIN_GAP_S = 600      # >= 10 min between answer-rearms / sid
GOAL_ANSWER_REARM_MAX_PER_DAY = 6      # cap: max answer-rearms per sid / 24h
# #1038 -- declared-window VIRGIN-arm rate floor. The COMMON case never touches
# it (a virgin window arms once and its armed footer + marker then skip every
# later scan); the floor bounds the ONE pathological loop -- a genuinely virgin
# declared pane whose keystroke deliveries keep failing (the per-request
# GOAL_DELIVERY_ATTEMPT_CAP drops the request, then the scan would re-record
# next sweep). MIN_GAP = 10 min between virgin RE-records per sid. MAX_PER_DAY is
# a HIGH anti-keystorm STROP, deliberately NOT a low cap: the owner's priority is
# that a declared window ALWAYS arms without manual digging, so a low daily cap
# that could leave a declared window dark for the rest of a day is wrong here --
# the min-gap is the real bound, the strop is only the safety ceiling.
GOAL_VIRGIN_REARM_MIN_GAP_S = 600      # >= 10 min between virgin re-records / sid
GOAL_VIRGIN_REARM_MAX_PER_DAY = 100    # anti-keystorm strop (min-gap is the real bound)
# #524-review: ~4 sweeps, not ~2.5 -- a single delayed/missed sweep (120s
# TimeoutStartSec, #365 contention, a memory-pressure reap) between the record
# sweep and job 9's first delivery must not false-drop an otherwise-fresh
# rearm and re-cost the full ~11-min confirmation. Still tight enough that a
# genuinely stale dark read is never typed (and delivery's own already-armed
# check catches a loop that recovered inside the window).
GOAL_DARK_REARM_STALE_S = 300           # drop a dark-rearm request older than this
GOAL_DARK_CONFIRM_STATE_TTL_S = 24 * 3600   # reap a confirm window untouched this long
# #519 -- orphan-prune TTL for state["goal_mark"] (off_state). The visited-this-
# sweep gate is the PRIMARY protection (a live pane is never reaped); this age
# floor is only the secondary safety for a not-visited-this-sweep entry (a
# budget-DEFERRED live pane), set WELL above the sweep interval so such a pane is
# never reaped before it is re-visited.
GOAL_MARK_ORPHAN_TTL_S = 24 * 3600

# #522 -- backstop for a `/goal` loop STUCK re-poking an unanswered `❓ NEEDS YOU`
# (the native evaluator ignoring stop-condition (A) -- the 17+ re-poke incident,
# with the only historical mechanical guard `_goal_blocked_on_unanswered_question`
# deleted in #403). Unlike #524's death-CONFIRMATION run (which sweep-accumulates
# because the render footer flickers), the STREAK here is read from the
# AUTHORITATIVE transcript (`question_repoke_run`) -- N byte-identical consecutive
# re-pokes with no genuine human answer between them is a sound confirmation in a
# SINGLE read. The disarm keystroke (`/goal clear`, the symmetric inverse of the
# arm keystroke, via the SAME `_send_goal_verified`/`deliver_with_stash`
# primitives) is still recent-human-gated and 24h-capped exactly like #524's
# re-arm. A successful disarm writes a `goal_disarmed_q` veto that `goal_dark_watch`
# HONOURS (never re-arm a goal we just deliberately cleared) until the transcript
# shows a genuine human answer AFTER the disarm.
GOAL_QUESTION_REPOKE_MIN = 5            # consecutive ❓ NEEDS YOU re-pokes to disarm
GOAL_QDISARM_MAX_PER_DAY = 2           # attempt cap: max /goal-clear auto-types per sid / 24h
GOAL_QDISARM_STATE_TTL_S = 24 * 3600   # reap a disarm veto / attempts entry untouched this long
GOAL_CLEAR_TEXT = "/goal clear"        # CC writes a `Goal cleared:` marker for this
# #1063 — the `/goal clear` disarm is a RECOVERY nudge (a damage-control action
# that must fire even when every machine-nudge kind is staged OFF). Before #1063
# it rode `_send_goal_verified`'s DEFAULT machine kind `goal-sweep`, which the
# #1023 per-kind staging has OFF on every box, so the #522 backstop was silently
# disabled fleet-wide. As a member of `RECOVERY_NUDGE_KINDS` this identity is
# exempt from the kill switch, the per-kind floor and the total cap; the backstop
# keeps its OWN bounds (a proven 5-streak, recent-human, the 24h/2 attempt cap).
GOAL_DISARM_NUDGE = "goal-disarm"


def _qdisarm_attempt_ok(attempts, now):
    """#522 per-sid attempt cap, sibling of `_dark_rearm_attempt_ok`: at most
    GOAL_QDISARM_MAX_PER_DAY `/goal clear` auto-types per session per rolling 24 h,
    so a swallowed disarm can never become a keystorm. `attempts` is the prior
    list of type timestamps (a JSON list; non-number entries dropped). Returns
    `(ok, pruned)` -- `ok` False once the cap is hit; `pruned` is the list with
    entries older than 24 h removed. Counts ATTEMPTS that reached the type step
    (sent OR swallowed), not just landed ones -- the #524 fail-safe (fewer real
    disarms than the cap allows) that keeps a persistently-wedged box bounded."""
    day = 24 * 3600
    pruned = [t for t in (attempts or [])
              if isinstance(t, (int, float)) and 0 <= (now - t) <= day]
    return (len(pruned) < GOAL_QDISARM_MAX_PER_DAY), pruned


def _qdisarm_veto(qveto, sid, tpath, now, human_ts_fn, loc):
    """#522 re-entry veto, called from `goal_dark_watch`'s per-sid path. `qveto`
    is the shared `state["goal_disarmed_q"]` dict. Returns `(vetoed, logline)`:
      * vetoed=True  -- a disarm veto is ACTIVE for `sid` and NO genuine human
                        answer has landed since it (`disarmed_ts`); dark_watch must
                        NOT re-arm / accumulate a death run / ping for this sid.
      * vetoed=False -- either no veto, or a genuine human answer landed AFTER the
                        disarm -> the veto is POPPED here (re-entry) and the
                        standard re-arm path resumes.
    Pure except the one `qveto.pop` on re-entry; never raises (a read failure
    from `human_ts_fn` returns None -> veto stays active, the safe direction)."""
    vrec = qveto.get(sid)
    if not isinstance(vrec, dict):
        return False, None
    d_ts = vrec.get("disarmed_ts")
    try:
        hts = human_ts_fn(tpath)
    except Exception:
        hts = None
    if hts is not None and isinstance(d_ts, (int, float)) and hts > d_ts:
        qveto.pop(sid, None)
        return False, ("dark-watch %s sid=%s -> #522 disarm veto CLEARED "
                       "(human answered after disarm) -- standard re-arm resumes"
                       % (loc, sid))
    return True, ("dark-watch %s sid=%s -> #522 disarm veto ACTIVE (goal was "
                  "cleared on a stuck ❓; no re-arm until the user answers)"
                  % (loc, sid))


def _prune_goal_mark_orphans(off_state, visited_sids, now,
                             ttl_s=GOAL_MARK_ORPHAN_TTL_S):
    """#519 -- age/live-gated orphan prune for `state["goal_mark"]` (dark_watch's
    `off_state`, keyed on `sid = tpath.stem`). G6 made goal_mark LOAD-BEARING
    (`resolve_goal_armed` / the lane gate read it), so a gone session's entry
    must not leak forever -- yet the marker-gone backstop only pops
    seen/pinged/confirm for a VISITED pane, never `off_state`.

    Reap an entry ONLY when BOTH: (1) its sid was NOT a live candidate pane THIS
    sweep (`visited_sids` -- session gone / superseded by a newer transcript),
    AND (2) it is malformed OR its stored transcript mtime is older than
    `ttl_s`. The visited gate is PRIMARY: a live pane whose loop body reaches
    `sid = tpath.stem` -- INCLUDING a silently-dead-loop pane dark_watch is still
    confirming, whose transcript mtime is legitimately STALE -- is added to
    `visited_sids` and never reaped, so its tail-proof persisted mark (the
    #517/#486-G6 signal) is safe from an age check. The two live paths that
    DON'T reach that line this sweep -- the janitor-recover `continue` and the
    sweep-budget `break` (a deferred pane) -- fall to `tmtime` (the SECONDARY
    safety); harmless and self-healing: reaping additionally needs `tmtime`
    >= `ttl_s` (24h) stale, by which point a dead loop's #459/#524 episode has
    long resolved, `confirm_state` (untouched here) preserves death-detection
    continuity, and a wrongly-reaped entry is simply re-seeded via #517 on the
    next clean sweep. An entry with a FUTURE mtime (clock skew) is kept (the
    safe direction). A reaper (run once per sweep), never a per-episode pop;
    never raises. Mirrors `_janitor_prune_parks` / the #524 confirm reaper."""
    if not isinstance(off_state, dict):
        return
    for sid in [k for k, v in list(off_state.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("tmtime"), (int, float))
                         and (now - v["tmtime"]) < ttl_s)]:
        off_state.pop(sid, None)


def _seed_or_scan_marker(tpath, off, loc, sid):
    """#517 -- resolve a session's newest `/goal` marker for dark_watch. FIRST
    SIGHT (`off is None`: state loss / fresh install / >tail-downtime) uses the
    bounded reverse-scan seed so an arm deeper than the 4 MB tail is still
    captured; every later sweep resumes incrementally from the stored offset.
    Returns `(new_off, new_mark, log_or_None)` -- the log is the
    deduped-per-sid `unknown-past-cap` observability line (an arm deeper than the
    seed cap: observability only, NEVER a silent not-armed and never a fabricated
    armed marker). Dedup is by construction: the seed runs only at first sight,
    then `off` is a real offset and this takes the incremental path."""
    if off is not None:
        new_off, new_mark = watchdog.scan_goal_markers(tpath, off=off)
        return new_off, new_mark, None
    new_off, new_mark, seed_status = watchdog.seed_goal_marker(tpath)
    log = None
    if seed_status == "unknown-past-cap":
        log = ("dark-watch %s sid=%s -> armed=? src=unknown-past-cap (no /goal "
               "marker within %d bytes of EOF at first sight; an arm deeper than "
               "the seed cap is not seedable -- observability only, treated "
               "not-armed)" % (loc, sid, watchdog.GOAL_MARK_SEED_CAP_BYTES))
    return new_off, new_mark, log


def _dark_confirm_advance(win, mark_ts, now):
    """Pure #524 death-confirmation advance for ONE session, called ONLY on a
    genuinely clean-dark sweep (`pane_goal_armed is False`, `mark == "set"`,
    and NO liveness veto -- the armed/None/mtime-advanced cases are handled by
    the caller ABOVE and never reach here). `win` is the persisted window dict
    (or None/malformed for a fresh episode):
        {"mark_ts", "clean_run", "run_start", "last"}
    Returns `(confirmed, new_win)`:
        confirmed=True  -- >= K clean reads AND run span >= MIN_SPAN -> TYPE
        confirmed=False -- still accumulating -> the #459 ping path, never TYPE
    A DIFFERENT `mark_ts` (a fresh arm) OR a malformed `win` restarts the run at
    1 (fail toward MORE observation, never a fast false confirm). JSON round-trip
    safe: `win` is a flat dict of scalars, indexed by key, never unpacked."""
    same = isinstance(win, dict) and win.get("mark_ts") == mark_ts
    run = win.get("clean_run") if same else 0
    run = (run + 1) if isinstance(run, int) and run >= 0 else 1
    start = win.get("run_start") if same else None
    if not isinstance(start, (int, float)):
        start = now
    new_win = {"mark_ts": mark_ts, "clean_run": run,
               "run_start": start, "last": now}
    confirmed = (run >= GOAL_DARK_CONFIRM_MIN_READS
                 and (now - start) >= GOAL_DARK_CONFIRM_MIN_SPAN_S)
    return confirmed, new_win


def _dark_rearm_backoff(i):
    """#804 mode-2 -- the ith re-arm-backoff window past the base cap
    (`GOAL_DARK_REARM_BACKOFF_S`, clamped to the last value so it holds at 6h
    forever). `i` is the 0-based stage (attempts
    already recorded PAST `GOAL_DARK_REARM_MAX_PER_DAY`)."""
    sched = GOAL_DARK_REARM_BACKOFF_S
    j = i if i < len(sched) else len(sched) - 1
    return sched[max(0, j)]


def _dark_rearm_attempt_ok(attempts, now):
    """Pure per-sid re-arm gate (#524 base cap, #804 mode-2 escalating backoff).
    `attempts` is the prior list of type timestamps (a JSON list; any non-number
    entry is dropped). Returns `(ok, pruned, wait_s)`:
      * `(True, pruned, None)`  -- allowed: either under the fast base cap
        (`GOAL_DARK_REARM_MAX_PER_DAY`), OR past it with the escalating backoff
        window (`_dark_rearm_backoff`) since the last attempt ELAPSED;
      * `(False, pruned, wait_s>0)` -- #804 mode-2 BACKOFF not yet elapsed: retry
        in `wait_s` seconds. The loop is being actively resurrected, NOT abandoned
        (the pre-#804 flat cap went silent-until-midnight here -- montalu2);
      * `(False, pruned, None)` -- the hard daily STROP
        (`GOAL_DARK_REARM_HARD_CAP_PER_DAY`) is reached (anti-keystorm; at 6h
        backoff spacing this is practically unreachable within a rolling 24h).
    `pruned` is the 24h-pruned list (the caller appends `now` only on a real
    type). The cap counts CONFIRMED RECORDS, not landed keystrokes: a record
    `deliver_goal` later drops as `drop:stale-rearm` still consumed a slot
    without any `/goal` typed -- this fails SAFE (fewer real re-arms than allowed,
    so the next attempt is eligible sooner) and correlates with a present human,
    so it is deliberate, not a defect."""
    day = 24 * 3600
    pruned = [t for t in (attempts or [])
              if isinstance(t, (int, float)) and 0 <= (now - t) <= day]
    n = len(pruned)
    if n < GOAL_DARK_REARM_MAX_PER_DAY:
        return True, pruned, None                # fast base-cap slot
    if n >= GOAL_DARK_REARM_HARD_CAP_PER_DAY:
        return False, pruned, None               # daily strop (anti-keystorm)
    back = _dark_rearm_backoff(n - GOAL_DARK_REARM_MAX_PER_DAY)
    elapsed = now - max(pruned)                   # >=0 (pruned filters future ts)
    if elapsed >= back:
        return True, pruned, None                # backoff window elapsed -> re-arm
    return False, pruned, int(back - elapsed)     # backoff not elapsed -> retry later


def _fulfilled_rearm_ok(recs, now):
    """#764 -- pure per-sid rate limiter for the fulfilled-rearm lane, the
    sibling of `_dark_rearm_attempt_ok` with the min-gap the fast (no dark-
    duration confirmation) cadence needs. `recs` is the prior list of record
    timestamps (any non-number dropped). Returns `(ok, pruned, reason)`:
      * `reason == "gap"`  -- < GOAL_FULFILLED_REARM_MIN_GAP_S since the newest
        record: a request just went out (or is still being delivered), so hold
        this sweep and retry the next (a TRANSIENT hold, the loop is not dead);
      * `reason == "cap"`  -- GOAL_FULFILLED_REARM_MAX_PER_DAY records already in
        the rolling 24 h: stop THIS lane's fast re-arms and fall through to the
        dead-loop machinery (a still-workable loop then escalates via its own
        slower confirmed dark-rearm, 2/day; a non-workable one gets the #459
        ping -- UNLESS it is a 🏁-proven, freshly-drained achieved loop, which
        the #766 FULFILLED-SILENT veto catches upstream of this rate limiter);
      * `("", ok=True)`    -- record allowed.
    `pruned` is the 24h-pruned list; the caller appends `now` only on a real
    record. Same fail-safe posture as the dark cap (records, not landed
    keystrokes -- a deliver_goal drop still consumes a slot, escalating sooner)."""
    day = 24 * 3600
    pruned = [t for t in (recs or [])
              if isinstance(t, (int, float)) and 0 <= (now - t) <= day]
    if pruned and (now - max(pruned)) < GOAL_FULFILLED_REARM_MIN_GAP_S:
        return False, pruned, "gap"
    if len(pruned) >= GOAL_FULFILLED_REARM_MAX_PER_DAY:
        return False, pruned, "cap"
    return True, pruned, ""


def _recovery_rearm_ok(recs, now, min_gap, max_per_day):
    """#890 -- pure per-sid rate limiter for RECOVERY-class origins (auth-rearm,
    answer-rearm). Mirrors `_fulfilled_rearm_ok` exactly but takes the gap +
    cap as PARAMETERS so each recovery origin can configure its own cadence.
    Returns `(ok, pruned, reason)`:
      * `reason == "gap"`  -- < min_gap since the newest record;
      * `reason == "cap"`  -- max_per_day records already in the rolling 24h;
      * `("", ok=True)`    -- record allowed.
    `pruned` is the 24h-pruned list; the caller appends `now` only on a real
    record. Same fail-safe posture as `_fulfilled_rearm_ok`."""
    day = 24 * 3600
    pruned = [t for t in (recs or [])
              if isinstance(t, (int, float)) and 0 <= (now - t) <= day]
    if pruned and (now - max(pruned)) < min_gap:
        return False, pruned, "gap"
    if len(pruned) >= max_per_day:
        return False, pruned, "cap"
    return True, pruned, ""


def _stream_seams(sid, cwd, now, rearm_fn, requests_path, state,
                  episode_states, dry_run):
    """#1143 -- `stream_migrate.seams` bound to THIS module's request
    store (the module-global `record_goal_request` / `load_goal_requests`, so a
    test patching either still observes the write/read)."""
    return _stream_migrate.seams(
        sid, cwd, now, dry_run, state, episode_states, rearm_fn or _default_rearm_fn,
        lambda *a, **k: record_goal_request(*a, path=requests_path, **k),
        lambda: load_goal_requests(requests_path))


def _stream_rearm(logs, sid, cwd, tpath, mark, now, loc, dry_run, state,
                  rearm_fn, requests_path, episode_states, pane, run):
    """#1143 -- dark-watch's ONE stream rule (`stream_migrate.dark_watch`: a
    dark stream loop the owner did not end is re-armed), fed this module's
    seams lazily, the SAME #524 `confirm_state` (`episode_states[2]`) the
    armed/None/mtime vetoes reset, and the process-tree read of `pane`'s claude
    (`stream_migrate.claude_children`, resolved at call time = the test seam)."""
    return _stream_migrate.dark_watch(
        logs, sid, cwd, tpath, mark, now, loc, dry_run, state,
        lambda: _stream_seams(sid, cwd, now, rearm_fn, requests_path, state,
                              episode_states, dry_run),
        _stream_migrate.confirm_run(episode_states[2], sid, now, dry_run,
                                    _dark_confirm_advance),
        lambda: _stream_migrate.claude_children(pane, run))


def _fulfilled_rearm_decide(sid, cwd, tpath, mark_ts, now, loc, dry_run,
                            rearm_fn, obligation_fn, requests_path,
                            fulfilled_state, fulfilled_proof, seen_state,
                            pinged_state, confirm_state):
    """#764 -- for a footer-DARK, mark=="set" loop (`goal_dark_watch`'s
    `armed is False` branch), decide whether it is a FULFILLED (stop-(B)
    completed) loop whose backlog REFILLED and, if so, RECORD a `fulfilled-rearm`
    request (goal_sweep/deliver_goal then delivers it through the SAME recent-
    human + pane-safety gates). Extracted to a module-level helper (the #502/#511
    pattern, sibling of `_dark_record_rearm`/`_stale_rearm_decide`) so
    `goal_dark_watch` stays under its function ceiling.

    Returns `(logline_or_None, handled)`:
      * handled=True  -- the caller must `continue` (a re-arm recorded, a dry-run
        would-record, or a TRANSIENT min-gap hold -- in every case the loop is
        NOT dead, so the dead-loop debounce/confirmation below must not run);
      * handled=_FULFILLED_SILENT -- #766: a 🏁-PROVEN achieved loop with a FRESH
        open==0 cache (backlog genuinely DRAINED, the correct final state). NOT a
        dead loop: the caller VETOes the #459 dead-loop ping (clears the ping/
        confirm escalation state, logs FULFILLED-SILENT once, and `continue`s)
        instead of falling through to the unconditional fallback. A truthy
        sentinel, so the caller MUST test it BEFORE the generic `if handled:`;
      * handled=False -- FALL THROUGH to the dead-loop machinery: not fulfilled
        (no 🏁 / 🏁 predates the arm), not workable AND not proven-drained (a
        STALE/UNREADABLE cache -- a FRESH open==0 is the FULFILLED-SILENT sentinel
        above, #766), no template, OR the daily cap is exhausted (12 fast
        fulfilled-rearms in 24h
        -> hand to the dead-loop machinery, which escalates a still-workable loop
        via its slower confirmed dark-rearm and pings a non-workable one).

    All state mutations are guarded on `not dry_run`. Never raises via the pure
    helpers it calls; a `record_goal_request` / rearm_fn / obligation_fn failure
    degrades to a fall-through (the safe, no-keystroke direction). A stream
    loop the ONE `stream_migrate` rule (#1143) HANDLES never reaches this lane
    (`_stream_rearm` runs first), so an idle old-template 🏁 loop migrates
    whatever the cache; one it does not handle (not idle, an open ❓, no
    template) still falls through to here as before."""
    # #767 -- BACKWARD-scan the bounded tail (scan_back=True) so a genuine 🏁 is
    # not SHADOWED by later non-🏁 post-achieve chore turns (the live gk failure:
    # a completed loop kept working ~18 min after 🏁 and its newest turn hid the
    # proof forever). Then fall back to the per-episode PROOF CACHE: heavy
    # post-achieve output can scroll the 🏁 clean out of the 2 MB / 200-entry
    # window, but a proof recorded on an EARLIER sweep of the SAME arm still
    # counts. A cache entry from a DIFFERENT arm (mark_ts mismatch) is IGNORED,
    # so there is no cross-episode re-arm leak; the reader tolerates a malformed
    # entry (read fail -> as if no cache, the safe direction).
    scan_bts = watchdog.transcript_last_backlog_empty_ts(tpath, scan_back=True)
    cached = fulfilled_proof.get(sid)
    cbts = None
    if (isinstance(cached, dict) and cached.get("mark_ts") == mark_ts
            and isinstance(cached.get("bts"), (int, float))):
        cbts = cached["bts"]
    proofs = [x for x in (scan_bts, cbts) if isinstance(x, (int, float))]
    if not proofs:
        return None, False                       # no 🏁 proof (found nor cached)
    bts = max(proofs)
    # The 🏁 MUST be provably AFTER the current arm. Fail CLOSED: an unparseable
    # `mark_ts` (`_newest_marker` sets ts=None on any timestamp parse failure)
    # means the ordering is UNPROVEN, so do NOT re-arm on a possibly-stale 🏁
    # from a previous episode -- the SAME safe direction transcript_last_backlog
    # _empty_ts takes for its own unparseable-ts case (never a false re-arm).
    if not (isinstance(mark_ts, (int, float)) and bts >= mark_ts):
        return None, False                       # 🏁 unproven-after-arm -> no re-arm
    # #767 -- PERSIST the proof for THIS arm so a later 🏁-scrolled-out sweep can
    # still see it (the veto AND rearm branches BOTH reach here). Guarded on not
    # dry_run: an honest dry-run mutates no state (mirrors the fulfilled_state
    # write below). `seen` = the last IN-WINDOW sighting (refreshed ONLY when the
    # scan itself re-found the 🏁 this sweep, NEVER on a cache-only hit -- else a
    # permanently-scrolled-out proof would refresh forever and never age out); a
    # cache-only sweep PRESERVES the frozen `seen`, so goal_dark_watch's reaper
    # (which reaps on `seen`) prunes the proof 24h after the 🏁 last appeared in
    # the window, not 24h after the immutable 🏁 timestamp itself (review 🟡).
    if not dry_run:
        if scan_bts is not None:
            seen = now
        elif isinstance(cached, dict) and isinstance(cached.get("seen"),
                                                     (int, float)):
            seen = cached["seen"]                 # cache-only hit -> freeze `seen`
        else:
            seen = now                            # first sighting w/o a prior seen
        fulfilled_proof[sid] = {"mark_ts": mark_ts, "bts": bts, "seen": seen}

    open_n, cts = (obligation_fn or _default_obligation_fn)(cwd)
    fresh = (cts is not None and 0 <= (now - cts) <= GOAL_DARK_CACHE_MAX_AGE_S)
    if isinstance(open_n, int) and open_n == 0 and fresh:
        # #766 -- 🏁 proven AFTER the arm AND a FRESH cache reading open==0: the
        # backlog is genuinely DRAINED (the correct achieved final state). This
        # is a FULFILLED loop, NOT a silently-dead one, so return the
        # distinguishable FULFILLED-SILENT sentinel -> the sweep site VETOes the
        # #459 dead-loop ping (which "fires ALWAYS" otherwise, misreading the
        # achieved loop as dead ~70 s after completion). NEVER a re-arm (the
        # backlog is empty), NEVER a keystroke -- a pure ping veto.
        return None, _FULFILLED_SILENT
    if not (isinstance(open_n, int) and open_n > 0 and fresh):
        # 🏁 seen but the cache is STALE / UNREADABLE (open==0-but-not-fresh, or
        # unreadable) -> UNPROVABLE achieved. FAIL SAFE: hand to the dead-loop
        # machinery below -- today's behavior (a genuinely dark loop without a
        # PROVEN drained backlog still pings; #766 vetoes ONLY the proven case
        # above). No per-sweep log here: this is the steady idle state of a
        # completed loop, and logging it every sweep would flood the journal.
        return None, False

    # #921 residual (review H1): defer to ANY pending request — a fulfilled-
    # rearm must not re-record while the prior one is undelivered (without the
    # decision-time attempt record, the min-gap alone no longer blocks re-entry
    # every 60 s sweep). Mirrors auth/answer/stale at :2573/:2725/:2932.
    if isinstance(load_goal_requests(requests_path).get(sid), dict):
        return None, False

    text, auth = (rearm_fn or _default_rearm_fn)(cwd)
    if not text:
        return ("dark-watch %s sid=%s -> fulfilled-rearm SKIP:no-template "
                "(🏁 seen, open=%s)" % (loc, sid, open_n)), False

    ok, pruned, why = _fulfilled_rearm_ok(fulfilled_state.get(sid), now)
    if not ok:
        line = ("dark-watch %s sid=%s -> fulfilled-rearm SKIP:%s "
                "(min-gap %ds / cap %d per 24h, %d in window)"
                % (loc, sid, why, GOAL_FULFILLED_REARM_MIN_GAP_S,
                   GOAL_FULFILLED_REARM_MAX_PER_DAY, len(pruned)))
        if why == "gap":
            # transient: a request just went out; keep the dead-loop machinery
            # from treating this still-fresh fulfilled loop as dead, retry next.
            if not dry_run:
                seen_state.pop(sid, None)
                pinged_state.pop(sid, None)
                confirm_state.pop(sid, None)
            return line, True
        return line, False                       # cap -> fall through to #459 ping

    if dry_run:
        return ("dark-watch %s sid=%s -> FULFILLED-REARM would record "
                "(dry-run, open=%s authority=%s)"
                % (loc, sid, open_n, auth)), True

    # #921 residual: attempt recording moved to goal_sweep after verified
    # delivery ("sent").  Same class as auth-rearm.
    seen_state.pop(sid, None)              # not a dead loop -> reset its state
    pinged_state.pop(sid, None)
    confirm_state.pop(sid, None)
    record_goal_request(sid, cwd, text, auth, now=now,
                        origin=_GOAL_FULFILLED_REARM_ORIGIN, path=requests_path)
    return ("dark-watch %s sid=%s -> FULFILLED-REARM: recording re-arm "
            "(open=%s authority=%s)"
            % (loc, sid, open_n, auth)), True


def _fulfilled_silent_veto(sid, mark_ts, loc, dry_run,
                           seen_state, pinged_state, confirm_state):
    """#766 -- apply the FULFILLED-SILENT ping veto for a 🏁-PROVEN achieved loop
    (fresh open==0, the `_FULFILLED_SILENT` sentinel from `_fulfilled_rearm_
    decide`). Clears the ping + death-CONFIRMATION escalation state (its
    accumulation IS the #459 trigger) so the caller's `continue` skips the ping
    FALLBACK, and MARKS the episode FULFILLED in `seen_state` so the decision
    logs ONCE, not every 60 s sweep for the HOURS a completed loop sits idle (the
    #764 journal-flood concern) -- mirroring the transition-only "first
    observation" / VETO-ALIVE logging, deliberately NOT the workable branch's
    blanket `seen_state.pop` (which, re-derived every sweep, would flood). A
    later backlog refill re-flips `_fulfilled_rearm_decide` to the workable
    re-arm; if unarmable, the dead-loop machinery re-detects from a FRESH confirm
    run (confirm_state is popped here). Returns the decision logline (once per
    episode) or None; all mutations guarded on `not dry_run` (the #502/#511
    module-level-helper pattern, keeping goal_dark_watch under its ceiling).

    NOTE (review 🔵, the set-marker deviation's bounded consequence): the
    `{"mark_ts": …, "fulfilled": True}` marker satisfies the dead-loop debounce's
    `prior.get("mark_ts") == mark_ts` check, so if the SAME episode later goes
    fresh->STALE (cache ages past GOAL_DARK_CACHE_MAX_AGE_S) the loop skips the
    "first observation, debouncing" sweep and reaches the #459 fallback one sweep
    (~60 s) sooner than a pristine episode; and because this veto pops
    `pinged_state` on every fresh sweep, a fresh<->stale oscillation re-fires a
    "first" ping each cycle, bounded in production only by the notify-layer dedup
    on the constant `goal-dark:sid:mark` key. Negligible live (the cache max age
    is 3 days) -- documented so a future reader does NOT "restore" the pop."""
    prior = seen_state.get(sid)
    fresh_episode = not (isinstance(prior, dict) and prior.get("fulfilled")
                         and prior.get("mark_ts") == mark_ts)
    if not dry_run:
        seen_state[sid] = {"mark_ts": mark_ts, "fulfilled": True}
        pinged_state.pop(sid, None)
        confirm_state.pop(sid, None)
    if fresh_episode:
        return ("dark-watch %s sid=%s -> FULFILLED-SILENT (achieved, open=0)"
                % (loc, sid))
    return None


def _dark_awaiting_user_veto(tpath):
    """#737 C -- True when the session's LAST real assistant turn ended with a
    ❓ marker: it is PARKED on a question to the owner, ALIVE-waiting, not a dead
    loop. `goal_dark_watch`'s mtime-advance liveness veto cannot see it (the
    transcript is STATIC until the owner answers), so this tail-marker read is
    the only awaiting-user signal. The montalu6 incident: one-glance classified
    the pane awaiting-user while dark-watch declared CONFIRMED-DEAD and re-armed a
    truncated /goal into it. The caller invokes this BEFORE every re-arm path
    (auth-rearm AND the armed True/None/False branches), so an awaiting-user
    session is never re-armed by any of them (#1113: the armed-True branch no
    longer re-arms at all -- it only observes a template drift -- so this guard
    now protects the dark/auth/fulfilled/answer re-arm paths). Bounded
    tail read (#599): a parked-on-❓ session has that turn as its LAST real
    assistant message, well inside the 2 MB tail (a false-negative would only be a
    single >2 MB entry sitting AFTER the ❓ turn -- vanishingly rare)."""
    return watchdog.transcript_last_marker_bounded(tpath) == "❓"


def _dark_record_rearm(sid, cwd, text, auth, now, loc, open_n, dry_run,
                       confirm_state, pinged_state, attempts_state,
                       requests_path):
    """#524 -- perform a CONFIRMED-dead auto-re-arm and return its ONE log
    line. Extracted to a module-level helper (the #502/#511 pattern) so
    `goal_dark_watch` stays under its line ceiling. On a dry-run it ONLY logs
    "would record" (no state mutation, no attempt slot -- #478 honest dry-run);
    otherwise it consumes a 24h attempt slot, resets the confirmation run + ping
    episode, and records the dark-rearm request for job 9 to type. Calls the
    module-global `record_goal_request` so a test patching it still observes the
    write."""
    win = confirm_state.get(sid) or {}
    reads = win.get("clean_run")
    span = int(now - (win.get("run_start") or now))
    if dry_run:
        return ("dark-watch %s sid=%s -> CONFIRMED-DEAD would record "
                "(dry-run, open=%s authority=%s reads=%s span=%ss)"
                % (loc, sid, open_n, auth, reads, span))
    # #921 residual: attempt recording moved to goal_sweep after verified
    # delivery ("sent").  Same class as auth-rearm.
    confirm_state.pop(sid, None)       # run consumed by the type
    pinged_state.pop(sid, None)        # episode resolved by the type
    record_goal_request(sid, cwd, text, auth, now=now,
                        origin=_GOAL_REARM_ORIGIN, path=requests_path)
    return ("dark-watch %s sid=%s -> CONFIRMED-DEAD: recording re-arm "
            "(open=%s authority=%s reads=%s span=%ss)"
            % (loc, sid, open_n, auth, reads, span))


def _default_obligation_fn(cwd):
    """Real obligation source for `goal_dark_watch`'s re-ping gate: the
    per-cwd tickets-status cache via `statusbar.obligation_count`. Lazily
    imported and FULLY guarded — any failure (statusbar unimportable at the
    watchdog's runtime path, a corrupt cache) degrades to `(None, None)`,
    which the caller reads as 'cannot confirm work remains' -> stay silent
    (fail toward no-nag)."""
    try:
        import statusbar
        return statusbar.obligation_count(cwd)
    except Exception:
        return None, None


def _default_rearm_fn(cwd):
    """Real (text, authority) source for `goal_dark_watch`'s #478 auto-re-arm:
    the installed `/goal` template for this cwd's authority profile. Mirrors
    `_default_obligation_fn` — lazily imported and FULLY guarded; any failure
    degrades to a None `text`, which the caller reads as 'template unresolved'
    and falls back to a ping (never a blank arm).

    #478 review MINOR — an UNEXPECTED `resolve_authority` failure fails toward
    the PING (returns no template), NEVER UP to `full`: typing a full-
    authority (merge-to-main) `/goal` template into a reduced-authority
    stream box would be far worse than one extra ping. `resolve_authority`'s
    OWN deliberate default for an unmapped user stays authoritative when it
    returns normally; a falsy authority makes `goal_template_for_authority`
    return None on its own, which also falls back to the ping."""
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception:
        return None, None
    try:
        # #617 -- pass a logs sink so an OVER-CAP template (the #169
        # regression) surfaces LOUDLY in the goal-sync forensic trail
        # instead of silently degrading to a ping with no stated reason.
        # `_log_goal_sync` collapses identical repeats, so a persistently
        # over-cap template logs one line, not a per-sweep flood.
        _logs = []
        # #998 — resolve the pane's (mode, role) from cwd via the SAME renderer
        # (goal_template_for delegates to the default SKILL.md read for a
        # parallel/no-role pane, byte-identical to before).
        text = goal_template_for(authority, cwd, logs=_logs)
        for _ln in _logs:
            _log_goal_sync(_ln)
    except Exception:
        text = None
    return (text or None), authority


def _stale_rearm_decide(sid, cwd, mark, now, loc, dry_run, rearm_fn,
                        tmpl_seen_state=None):
    """#623 (RETIRED by #1113) -- for a LIVE, ARMED loop (`goal_dark_watch`'s
    `armed is True` branch), OBSERVE whether its stored condition has DRIFTED
    from the shipped template. It NO LONGER records a re-arm request / types a
    keystroke: the owner ruling 22.9.2026 (>10x reported) is that an ACTIVE loop
    is NEVER touched by a machine keystroke -- a template change applies at the
    NEXT NATURAL arm (session death / dark footer / the owner's own `/autopilot`),
    never by re-typing 3.7 kB into a live box (the david1-3 regression). So this
    is now a pure OBSERVATION: return ONE journal line naming the drift the FIRST
    time it is seen for a (session, template) pair (deduped via the existing
    `goal_stale_rearm_tmpl` stamp so a template deploy does not re-log every
    sweep), or None for the non-drift cases (current / foreign / unknown) and for
    an already-observed drift. A dry-run logs the drift but never stamps."""
    payload = mark.get("payload") if isinstance(mark, dict) else None
    text, _authority = (rearm_fn or _default_rearm_fn)(cwd)
    if _classify_armed_condition(payload, text) != "stale":
        return None
    # #1113 -- dedup the OBSERVATION per (session, template version) via the same
    # `state["goal_stale_rearm_tmpl"]` stamp the record path used, so a template
    # deploy logs the drift ONCE per armed session, not every ~30 min sweep.
    _tmpl_hash = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]
    if isinstance(tmpl_seen_state, dict) and tmpl_seen_state.get(sid) == _tmpl_hash:
        return None
    if not dry_run and isinstance(tmpl_seen_state, dict):
        tmpl_seen_state[sid] = _tmpl_hash
    return ("stale-drift %s sid=%s -> condition predates the shipped template; "
            "waits for the next natural arm (never re-typed into an active loop, "
            "#1113)" % (loc, sid))


def _goal_guard_decide(sid, payload, template_line, state, now, loc,
                       mark_state=None):
    """#878 — for a LIVE, ARMED loop with a FOREIGN condition that lacks the
    `❓ NEEDS YOU` detection token while a LASTQF exists (a delivered unanswered
    question sitting), decide whether to emit a `goal-guard:` nudge. Returns a
    decision-log line when the nudge SHOULD be attempted, or None (no action).
    Pure decision helper — the CALLER handles nudge_gate, delivery, and dry-run.

    `mark_state` defaults to `"set"` (armed); `"cleared"` skips entirely (#170)."""
    if mark_state is not None and mark_state != "set":
        return None
    verdict = _classify_armed_condition(payload, template_line)
    if verdict != "foreign":
        return None
    norm = _goal_condition_norm(payload)
    if norm and "NEEDS YOU" in norm:
        return None
    lastqf = Path("/tmp/claude-discord-lastq-%s" % sid)
    if not lastqf.exists():
        return None
    return ("goal-guard %s sid=%s -> FOREIGN condition without blocked-❓ "
            "capability, LASTQF present" % (loc, sid))


_GOAL_GUARD_TEXT = (
    "goal-guard: your armed /goal condition lacks the blocked-on-❓ (A) "
    "clause — a delivered unanswered question will not hold the loop. "
    "If this is the autopilot loop, re-arm the canonical template via "
    "`python3 ~/devel/airuleset/airuleset.py goal-arm --self`. "
    "If the short goal is deliberate, say so."
)


def _goal_guard_deliver(sid, pid, captured, cwd, state, now, loc, run,
                        sleep_fn, dry_run, projects_dir, batch_collect=None):
    """#878 — delivery half of the goal-guard rider. Called when
    `_goal_guard_decide` returned a truthy decision. Handles nudge_gate,
    recent-human, boundary classification, keystroke delivery, and mark_sent.
    Returns a list of decision-log lines (may be empty)."""
    logs = []
    # #923 BATCH MODE: gate_ok is handled once by the caller.
    if batch_collect is None:
        if not watchdog.nudges_enabled("goal-guard"):   # #1023 per-kind switch
            logs.append("goal-guard %s sid=%s -> skip:kind-off (goal-guard)"
                        % (loc, sid))
            return logs
        if not _nudge_gate.gate_ok(state, sid, "goal-guard", now):
            logs.append("goal-guard %s sid=%s -> %s"
                        % (loc, sid,
                           _nudge_gate.floor_hold_reason(state, sid, "goal-guard", now)))
            return logs
    if dry_run:
        logs.append("goal-guard %s sid=%s -> would-send (dry-run)"
                     % (loc, sid))
        return logs
    tinfo = watchdog.find_active_transcript(projects_dir, cwd)
    tpath = tinfo[0] if tinfo else None
    if tpath:
        rh, _rr = watchdog._goal_autoarm_recent_human_activity(
            sid, tpath, now, window_s=GOAL_LANE_LIVE_CONVO_S,
            pane_target=pid, run=run)
        if rh:
            logs.append("goal-guard %s sid=%s -> skip:recent-human"
                         % (loc, sid))
            return logs
    kind, draft = watchdog._classify_boundary(captured)
    if kind == "no-input-line":
        logs.append("goal-guard %s sid=%s -> skip:stopped-pane"
                     % (loc, sid))
        return logs
    if kind == "busy":
        logs.append("goal-guard %s sid=%s -> skip:busy" % (loc, sid))
        return logs
    # #1023: idle-pane only — a busy Waiting pane defers (aged override gone)
    if _ops_wait_recheck._pane_busy_waiting(captured):
        logs.append("goal-guard %s sid=%s -> skip:busy" % (loc, sid))
        return logs
    if draft:
        logs.append("goal-guard %s sid=%s -> skip:draft" % (loc, sid))
        return logs
    # #923 BATCH COLLECT: contribute text, defer delivery+state to caller.
    if batch_collect is not None:
        # goal-guard's post-delivery is just mark_sent (handled by batch path).
        batch_collect.append(("goal-guard", _GOAL_GUARD_TEXT, None))
        logs.append("goal-guard %s sid=%s -> batch-collected" % (loc, sid))
        return logs
    ok = _send_goal_verified(pid, _GOAL_GUARD_TEXT, run,
                             captured=captured, sleep_fn=sleep_fn,
                             verify_armed=False, nudge="goal-guard")
    if ok:
        _nudge_gate.mark_sent(state, sid, "goal-guard", now)
        logs.append("goal-guard %s sid=%s -> sent" % (loc, sid))
    else:
        logs.append("goal-guard %s sid=%s -> send-failed" % (loc, sid))
    return logs


def _auth_rearm_decide(sid, cwd, mark, armed, now, loc, dry_run, rearm_fn,
                       obligation_fn, requests_path, auth_attempts_state):
    """#675/#890 -- re-arm a loop CC cleared on a TRANSIENT auth failure (the
    newest marker is `cleared` with `clear_kind=="auth"`; the caller gates on
    that, the #170 boundary -- a USER/`error` clear is NEVER re-armed).
    Returns ONE explicit decision-log line, or None for the non-actionable cases.

    #890 CHANGES from the original #675: auth-rearm is a RECOVERY-class origin
    (PROVEN, not guessed), so it has its OWN rate state (`auth_attempts_state`,
    separate from the dead-dark 2/24h `attempts_state`) with a generous but
    capped cadence (min-gap 300s + 12/day, the #764 fulfilled-rearm precedent).
    It also TOLERATES a STALE obligation cache (the session being recovered is
    the only thing that refreshes this cache — demanding freshness from a dead
    session is circular; a wrong re-arm on a stale-but-drained backlog costs one
    loop launch → 🏁 → fulfilled machinery). A MISSING cache (`cts is None`) or
    `open == 0` still skips — that stays the fail-safe floor.

    Unlike the dead-dark path it needs NO 8-read death CONFIRMATION: the auth
    clear is UNAMBIGUOUS (CC explicitly cleared it), so the only question is "is
    the session ALIVE again to receive it" -- answered by `armed is False`, a
    readable, dark, idle footer. `armed is None` (busy / no input box = dead or
    undeterminable) or `armed is True` (already re-armed) -> nothing to do.

    FOREIGN-goal guard (#675-review): the cleared payload is classified via
    `_classify_armed_condition` and re-armed ONLY when it was an AUTOPILOT
    condition (opens with the signature) -- a hand-armed FOREIGN goal auth-cleared
    is NEVER re-typed with the autopilot template (the #623 "foreign is NEVER
    touched" doctrine; #170). CC truncates the cleared condition, but the
    signature lives at its OPENING, so a truncated payload still classifies.
    NO marker-age bound is imposed: an auth clear left un-rearmed for hours (box
    down, then back with a still-live idle pane + workable backlog) SHOULD still
    recover -- late recovery of the owner's autopilot is desired, and the
    workable-backlog + rate-limit + recent-human + foreign gates already bound
    it (a deliberate owner clear is a `user` marker, never re-armed).

    The keystroke + its recent-human + freshness gates live in `deliver_goal`."""
    if armed is not False:
        return None
    # defer to ANY pending request (goal_sweep is already delivering it) -- never
    # clobber a self-callback / dark-rearm (or a pre-#1113 leftover stale-rearm
    # request, which deliver_goal drops as retired).
    if isinstance(load_goal_requests(requests_path).get(sid), dict):
        return None
    text, authority = (rearm_fn or _default_rearm_fn)(cwd)
    if not text:
        return ("auth-rearm %s sid=%s -> cleared-by-auth but NO template "
                "resolved -- skip" % (loc, sid))
    payload = mark.get("payload") if isinstance(mark, dict) else None
    if _classify_armed_condition(payload, text) not in ("stale", "current"):
        return ("auth-rearm %s sid=%s -> cleared-by-auth but the cleared goal "
                "is FOREIGN / unknown (not an autopilot condition) -- never "
                "re-armed (#170)" % (loc, sid))
    open_n, cts = (obligation_fn or _default_obligation_fn)(cwd)
    # #890 -- STALE-TOLERANT: accept open_n > 0 even when the cache is stale.
    # A dead session is the only thing that refreshes this cache; demanding
    # freshness from a dead session is circular (the 22h gap's exact mechanism).
    # A wrong re-arm on a stale-but-drained backlog costs one launch → 🏁 → stop
    # (handled by #764/#766 fulfilled machinery). MISSING cache (cts is None)
    # or open == 0 still skips — the fail-safe floor.
    has_cache = cts is not None
    if not (isinstance(open_n, int) and open_n > 0 and has_cache):
        return ("auth-rearm %s sid=%s -> cleared-by-auth but backlog not "
                "workable (open=%s, cache=%s) -- skip"
                % (loc, sid, open_n,
                   "missing" if not has_cache else "present"))
    # #890 -- own rate state, separate from the dead-dark 2/24h cap.
    ok, pruned, reason = _recovery_rearm_ok(
        auth_attempts_state.get(sid), now,
        GOAL_AUTH_REARM_MIN_GAP_S, GOAL_AUTH_REARM_MAX_PER_DAY)
    if not ok:
        return ("auth-rearm %s sid=%s -> cleared-by-auth but RATE-LIMIT "
                "(%s, %d recorded) -- skip"
                % (loc, sid, reason, len(pruned)))
    if dry_run:
        return ("auth-rearm %s sid=%s -> cleared-by-auth would record re-arm "
                "(dry-run, open=%s authority=%s)" % (loc, sid, open_n, authority))
    # #921 residual: attempt recording moved to goal_sweep after verified
    # delivery ("sent").  Recording at decision time counted UNDELIVERED
    # attempts toward the cap — the m1 12-starved-auth-rearm incident.
    record_goal_request(sid, cwd, text, authority, now=now,
                        origin=_GOAL_AUTH_REARM_ORIGIN, path=requests_path)
    return ("auth-rearm %s sid=%s -> cleared-by-auth: recording re-arm (open=%s "
            "authority=%s)"
            % (loc, sid, open_n, authority))


def _answer_rearm_check_transcript(tpath, mark_ts):
    """#890 -- read the bounded transcript tail to detect the ANSWERED-❓ pattern:
    the newest REAL assistant turn has a `❓` marker (line-start, same `_MARKER_RX`
    as `transcript_last_marker_bounded`), AND a genuine human USER prompt (not a
    tool_result, not a known machine prompt) follows it.

    Returns `(q_idx, answered, owner_touched_goal)`:
      * `q_idx`: the INDEX in the entries list of the ❓-marked assistant turn,
        or None if no such turn found.
      * `answered`: True when a human user message follows the ❓ turn.
      * `owner_touched_goal`: True when the owner's post-❓ text contains `/goal`
        (the #170 belt — the owner touching goal state ⇒ abstain).

    BOUNDED read: uses `_read_jsonl_byte_tail` (the #764 perf class), never a
    whole-file scan. The ❓ turn is detected by `_MARKER_RX.match` on the last
    ≤3 non-blank lines — the IDENTICAL walk `_last_marker_line_from_entries`
    uses (review C1 fix: not `text.endswith("❓")`). mark_ts is used to reject
    a stale ❓ from a previous arm episode (fail-closed on unparseable mark_ts,
    the #764 point-4 shape).

    MACHINE-PROMPT FILTERING (review M1): a plain-text user entry that matches
    known machine-injected prefixes (watchdog keystroke nudges, continue prompts)
    is NOT counted as a human answer — prevents the montalu6-class bulldoze."""
    import re
    from watchdog.transcripts import _read_jsonl_byte_tail, _entry_text
    from watchdog import _SENTINELS
    _marker_rx = re.compile(r"^\s*(⏳|✅|❓)")
    entries = _read_jsonl_byte_tail(tpath, 2_000_000, 200)
    if not entries:
        return None, False, False
    # Walk backward to find the newest REAL assistant turn with a ❓ marker.
    q_idx = None
    q_ts = None
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            break  # an API error is not a real turn
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue  # synthetic/bookkeeping entry
        # Found the newest real assistant turn — check for ❓ marker.
        nonblank = [ln for ln in text.splitlines() if ln.strip()]
        found_q_marker = False
        for ln in reversed(nonblank[-3:]):
            m = _marker_rx.match(ln)
            if m and m.group(1) == "❓":
                found_q_marker = True
                break
            elif m:
                break  # a different marker (⏳/✅) — not a ❓ turn
        if found_q_marker:
            q_idx = i
            # Extract the timestamp for the episode guard.
            ts_str = entry.get("timestamp")
            if isinstance(ts_str, str):
                try:
                    from datetime import datetime
                    q_ts = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    q_ts = None
            elif isinstance(ts_str, (int, float)):
                q_ts = ts_str
        break  # stop at the newest real assistant turn either way

    if q_idx is None or q_ts is None:
        return None, False, False

    # #764 point-4: fail CLOSED on unparseable mark_ts — a stale ❓ from a
    # previous arm episode.
    if not (isinstance(mark_ts, (int, float)) and q_ts >= mark_ts):
        return None, False, False

    # Walk FORWARD from the ❓ entry (by INDEX, not timestamp — review C2 fix)
    # to find a genuine human user message.
    answered = False
    owner_touched_goal = False
    # Known machine-injected user-entry prefixes (M1 fix: a watchdog nudge or
    # a /compact command is NOT a human answer).
    _machine_prefixes = (
        "continue", "lane-check:", "stuck-check:", "/compact",
        "recheck:", "nudge:", "UNPARK-AUDIT:",
    )
    for entry in entries[q_idx + 1:]:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "user":
            continue
        # A user entry with tool_result is NOT a human prompt.
        msg = entry.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result"
                   for b in content):
                continue  # tool_result — not a human prompt
        # A plain-text user entry — check for machine shapes.
        text = (_entry_text(entry) or "").strip()
        if not text:
            continue
        # Filter known machine-injected prefixes.
        is_machine = False
        for pfx in _machine_prefixes:
            if text.startswith(pfx):
                is_machine = True
                break
        if is_machine:
            continue
        # Check for /goal in the user's text (#170 belt).
        if text.lstrip().startswith("/goal"):
            owner_touched_goal = True
        answered = True
        break

    return q_idx, answered, owner_touched_goal


def _answer_rearm_decide(sid, cwd, tpath, mark, mark_ts, armed, now, loc,
                         dry_run, rearm_fn, obligation_fn, requests_path,
                         answer_attempts_state):
    """#890 -- re-arm a loop after the OWNER ANSWERED a `❓ NEEDS YOU`-blocked
    turn. CC's stop condition (A) disarmed the goal when the ❓ marker was
    present; the owner's answer resumes the session, but the goal is gone.
    This rider detects the answered-❓ pattern from the transcript and records
    an `answer-rearm` request.

    Trigger (ALL must hold):
      1. `mark == "set"` AND `armed is False` — the stop-(A) shape.
      2. Newest real assistant turn ends with `❓ NEEDS YOU` at epoch Q >= mark_ts.
      3. A human USER message at U > Q — the answer arrived.
      4. The cleared goal is an AUTOPILOT condition (foreign guard, #170).
      5. Workable backlog (stale-tolerant, like auth-rearm).
      6. Rate limit (own `answer_attempts_state`, min-gap 600s + 6/day).
      7. Once-per-episode dedup: `answer_handled_state[sid]` stores the last Q.

    #170 belts: (i) if `mark.state == "cleared"` (any kind), this code path is
    unreachable — the caller only enters this branch on `mark.state == "set"`;
    (ii) a `/goal` prefix in the user's post-❓ text ⇒ ABSTAIN (the owner
    touching goal state is NEVER overridden).

    Returns ONE decision-log line, or None (silent) for non-actionable cases.
    The keystroke + its recent-human + freshness gates live in `deliver_goal`."""
    if armed is not False:
        return None
    # Only for mark == "set" (the caller already gates on this).
    if mark is None or mark.get("state") != "set":
        return None

    # Detect the answered-❓ pattern from the transcript.
    q_idx, answered, owner_touched_goal = _answer_rearm_check_transcript(
        tpath, mark_ts)
    if q_idx is None:
        return None  # no ❓ turn found, or stale ❓ from prior episode
    if not answered:
        return None  # ❓ not yet answered — the awaiting-user veto should hold
    # #170 belt: the owner typed /goal after answering — abstain.
    if owner_touched_goal:
        return ("answer-rearm %s sid=%s -> answered-❓ but owner typed /goal "
                "after answer -- never re-armed (#170)" % (loc, sid))

    # defer to ANY pending request (goal_sweep is already delivering it).
    if isinstance(load_goal_requests(requests_path).get(sid), dict):
        return None

    text, authority = (rearm_fn or _default_rearm_fn)(cwd)
    if not text:
        return ("answer-rearm %s sid=%s -> answered-❓ but NO template "
                "resolved -- skip" % (loc, sid))
    # Foreign guard: only re-arm an autopilot condition.
    payload = mark.get("payload") if isinstance(mark, dict) else None
    if _classify_armed_condition(payload, text) not in ("stale", "current"):
        return ("answer-rearm %s sid=%s -> answered-❓ but the goal is "
                "FOREIGN / unknown -- never re-armed (#170)" % (loc, sid))
    # Workable backlog (stale-tolerant, like auth-rearm #890).
    open_n, cts = (obligation_fn or _default_obligation_fn)(cwd)
    has_cache = cts is not None
    if not (isinstance(open_n, int) and open_n > 0 and has_cache):
        return ("answer-rearm %s sid=%s -> answered-❓ but backlog not "
                "workable (open=%s, cache=%s) -- skip"
                % (loc, sid, open_n,
                   "missing" if not has_cache else "present"))
    # Own rate limit.
    ok, pruned, reason = _recovery_rearm_ok(
        answer_attempts_state.get(sid), now,
        GOAL_ANSWER_REARM_MIN_GAP_S, GOAL_ANSWER_REARM_MAX_PER_DAY)
    if not ok:
        return ("answer-rearm %s sid=%s -> answered-❓ but RATE-LIMIT "
                "(%s, %d recorded) -- skip"
                % (loc, sid, reason, len(pruned)))
    if dry_run:
        return ("answer-rearm %s sid=%s -> answered-❓ would record re-arm "
                "(dry-run, open=%s authority=%s)" % (loc, sid, open_n, authority))
    # Record the re-arm request. Dedup is handled by the DOWNGRADE-REFUSED
    # mechanism (a pending request of ANY origin blocks re-recording) + the
    # rate limiter's min-gap (M2 fix: no consumed-epoch dedup that would lose
    # a dropped/expired request forever).
    # #921 residual: attempt recording moved to goal_sweep after verified
    # delivery ("sent").  Same class as auth-rearm — undelivered attempts
    # must not fill the rate cap.
    record_goal_request(sid, cwd, text, authority, now=now,
                        origin=_GOAL_ANSWER_REARM_ORIGIN, path=requests_path)
    return ("answer-rearm %s sid=%s -> answered-❓: recording re-arm (open=%s "
            "authority=%s)"
            % (loc, sid, open_n, authority))


def goal_dark_watch(now, run=None, state=None, send_fn=None, dry_run=False,
                    projects_dir=None, sleep_fn=None, time_fn=None,
                    sweep_deadline=None, obligation_fn=None, rearm_fn=None,
                    requests_path=None, human_ts_fn=None):
    """#403 STEP 0 confirmed #172's own sweep_deadline/tail_deadline
    budget-sharing mechanism must survive this collapse -- unlike
    `goal_sweep` (bounded by the tiny pending-arm-request count),
    this function's per-pane loop walks EVERY live candidate pane
    (`_reconcile_candidate_panes`), the same unbounded-by-repo-count shape
    `bounce_backstop` (#255 Fix 1) already guards. `time_fn`/`sweep_deadline`
    mirror that function's contract exactly: both optional, default
    None -> unbounded (today's pre-fix behavior for any caller/test that
    doesn't pass them), checked as the very FIRST statement of each pane's
    iteration -- a pane already being processed always finishes; only a
    NOT-YET-STARTED pane is deferred to the next sweep, and nothing is
    written for it (off_state/seen_state/pinged_state stay untouched), so
    it is retried next sweep exactly like an unvisited pane always is.

    `obligation_fn(cwd) -> (open, ts)` (#459; default `_default_obligation_fn`,
    the per-cwd tickets-status cache) is the injected death-vs-achievement
    source for the staged re-ping gate below -- only a positive, fresh
    `open > 0` lets a persistently-dark goal be RE-pinged past its first
    ping.

    `rearm_fn(cwd) -> (text, authority)` / `requests_path` (#478; defaults
    `_default_rearm_fn` and `goal_requests_path()`) drive the auto-re-arm of
    the dark-DIED branch. #524 (owner decision B) HARDENS it: on a genuinely
    WORKABLE cache the loop is only RE-ARMED (a goal-arm request WRITTEN for
    job 9 to type) once a death-CONFIRMATION run completes -- K clean-dark
    footer reads over >= MIN_SPAN, with ANY armed read / advancing transcript
    mtime / undeterminable read VETOING the run -- and under a 24h attempt cap;
    an idle-but-ALIVE session whose glyph merely flickers never reaches it
    (montalu 2026-08-16). A workable loop the watchdog cannot yet confirm (or
    cannot self-heal: no template / cap exhausted) accumulates SILENTLY or
    falls to the #459 ping. For the RE-ARM path this function types nothing --
    it only WRITES the request; that keystroke + its recent-human + delivery-
    freshness gates live in `deliver_goal`. (The #617 stranded-truncated-/goal
    CLEAR, run first via `_clear_stranded_truncated_goal`, is the one keystroke
    path here -- Escape+BSpace, gated on a clean boundary + fail-closed
    recent-human + a byte-exact-prefix content proof + a bounded give-up.) A
    USER-cleared goal (clear_kind="user") or a non-auth `error` clear is NEVER
    re-armed (#170); the ONLY cleared shape that re-arms is CC's transient-auth
    clear (clear_kind="auth"), via `_auth_rearm_decide` in the mark!="set"
    branch, and only when the pane is alive again (armed False, #675)."""
    logs = []
    if watchdog._owner_disabled("goal"):
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
        return logs
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    state = state if state is not None else {}
    off_state = state.setdefault("goal_mark", {})
    seen_state = state.setdefault("goal_dark_seen", {})
    pinged_state = state.setdefault("goal_dark_pinged", {})
    janitor_recs = state.setdefault("janitor_pinged_rec", {})
    # #524 -- the death-CONFIRMATION run per sid (clean-dark reads + span) and
    # the per-sid 24h auto-type attempt cap. Reap a confirm window untouched
    # beyond its TTL so a session that vanished mid-accumulation cannot leak an
    # entry forever (the #486-G5 dedup-dict-leak lesson).
    confirm_state = state.setdefault("goal_dark_confirm", {})
    attempts_state = state.setdefault("goal_dark_rearm_attempts", {})
    # #1092 (e) / #1113 -- the per-sid stale-drift OBSERVATION latch: once a
    # session's template drift has been LOGGED for a given shipped-template hash,
    # do not re-LOG it until the template changes (originally a re-RECORD dedup;
    # #1113 retired the record, so the same stamp now dedups the observation so a
    # template deploy no longer floods the journal every ~30 min sweep).
    stale_tmpl_state = state.setdefault("goal_stale_rearm_tmpl", {})
    # #764 -- the per-sid fulfilled-rearm record timestamps (rate limiter), a
    # JSON list per sid; reaped below exactly like `attempts_state`.
    fulfilled_state = state.setdefault("goal_fulfilled_rearm", {})
    # #890 -- per-sid recovery-class rate states (own dicts, separate from the
    # dead-dark `attempts_state`). Reaped below exactly like `fulfilled_state`.
    auth_attempts_state = state.setdefault("goal_auth_rearm_attempts", {})
    answer_attempts_state = state.setdefault("goal_answer_rearm_attempts", {})
    # #767 -- the per-episode 🏁 PROOF cache `{sid: {"mark_ts", "bts", "seen"}}`.
    # Written on ANY 🏁-after-mark sighting (both the fulfilled-rearm AND the #766
    # veto branch) so heavy post-achieve output that scrolls the 🏁 out of the
    # 2 MB tail cannot erase the proof; used only when its mark_ts matches the
    # current arm (no cross-episode leak). `seen` (last in-window sighting) is the
    # reaper key -- see the reaper below.
    fulfilled_proof = state.setdefault("goal_fulfilled_proof", {})
    # #522 -- the disarm-on-question veto (written by goal_question_repoke_watch,
    # READ + re-entry-popped here). Reaped by that job; setdefault only so a pop
    # below always targets the real state dict even on the first sweep.
    qveto = state.setdefault("goal_disarmed_q", {})
    human_ts_fn = human_ts_fn or watchdog._last_human_prompt_ts
    for _csid in [k for k, v in list(confirm_state.items())
                  if not (isinstance(v, dict)
                          and isinstance(v.get("last"), (int, float))
                          and 0 <= (now - v["last"]) <= GOAL_DARK_CONFIRM_STATE_TTL_S)]:
        confirm_state.pop(_csid, None)
    # #524-review -- the attempt-cap store leaks the SAME way (a `[]` is written
    # on every workable-dark sweep, never popped) -> reap a sid whose newest
    # attempt ts is older than the 24h cap window, and any empty/malformed
    # entry. A REAPER, never a pop-on-episode-end (that would reset the rolling
    # cap); a live capped sid refreshes its newest ts, so it is never reaped.
    _day = 24 * 3600
    for _asid in [k for k, v in list(attempts_state.items())
                  if not (isinstance(v, list)
                          and any(isinstance(t, (int, float))
                                  and 0 <= (now - t) <= _day for t in v))]:
        attempts_state.pop(_asid, None)
    # #764 -- same REAPER for the fulfilled-rearm record store (a `[]` grows on
    # every fulfilled sweep, never popped); reap a sid whose newest record is
    # older than the 24h window, and any empty/malformed entry. A live capped
    # sid refreshes its newest ts, so it is never reaped.
    for _fsid in [k for k, v in list(fulfilled_state.items())
                  if not (isinstance(v, list)
                          and any(isinstance(t, (int, float))
                                  and 0 <= (now - t) <= _day for t in v))]:
        fulfilled_state.pop(_fsid, None)
    # #890 -- same REAPER for the recovery-class rate states (auth + answer).
    for _rsid in [k for k, v in list(auth_attempts_state.items())
                  if not (isinstance(v, list)
                          and any(isinstance(t, (int, float))
                                  and 0 <= (now - t) <= _day for t in v))]:
        auth_attempts_state.pop(_rsid, None)
    for _rsid in [k for k, v in list(answer_attempts_state.items())
                  if not (isinstance(v, list)
                          and any(isinstance(t, (int, float))
                                  and 0 <= (now - t) <= _day for t in v))]:
        answer_attempts_state.pop(_rsid, None)
    # #767 -- same 24h REAPER for the proof cache, keyed on `seen` (the last
    # IN-WINDOW 🏁 sighting), NOT the immutable `bts`. A live fulfilled loop whose
    # 🏁 is still in the bounded tail refreshes `seen`=now every sweep, so it is
    # never reaped; once the 🏁 scrolls out the frozen `seen` ages the entry out
    # 24h later (rate limits deliver the re-arm long before that). Reap a sid
    # whose `seen` is missing/malformed or outside the window (a legacy pre-#767
    # entry carried no `seen` -> reaped as malformed, the safe direction: the scan
    # re-finds the 🏁 if still in-window, else no false re-arm).
    for _psid in [k for k, v in list(fulfilled_proof.items())
                  if not (isinstance(v, dict)
                          and isinstance(v.get("seen"), (int, float))
                          and 0 <= (now - v["seen"]) <= _day)]:
        fulfilled_proof.pop(_psid, None)

    # #488 review-1 -- GC the age-unbounded stash_parks records for panes that
    # no longer exist. The per-pane marker-gone backstop below only sees panes
    # STILL in the candidate set; this covers the ones that LEFT it, restoring
    # the "no stale provenance forever" bound the park record's age-
    # unboundedness removes. FAIL-SAFE: a failed/empty `tmux list-panes` read
    # yields no ids -> `_janitor_prune_parks` prunes NOTHING, so a transient
    # tmux error never wipes a valid fresh record. Dry-run mutates no state.
    if not dry_run:
        try:
            live_pids = (run(["tmux", "list-panes", "-a",
                              "-F", "#{pane_id}"]) or "").split()
        except Exception:
            live_pids = []
        watchdog._janitor_prune_parks(state, live_pids)

    visited_sids = set()   # #519 -- live candidate sids kept by the orphan prune below
    for pid, cwd, _cmd in watchdog._reconcile_candidate_panes(run):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            logs.append("dark-watch-budget-exceeded — deferring remaining "
                        "panes to next sweep")
            break
        if watchdog.pane_in_mode(pid, run):
            continue
        captured = run(["tmux", "capture-pane", "-p", "-t", pid]) or ""
        loc = watchdog._pane_location(pid, run) or pid

        jrec = janitor_recs.setdefault(pid, {})
        # #737 -- heal an EXISTING scrolled /goal leftover (montalu6/montalu3
        # after re-enable) even with no pending request: pass the current /goal
        # TEMPLATE as `own_payload` so the janitor recognizes a scrolled leftover
        # (box is a >= 80-char SUBSTRING of the template) and clears/pops it. The
        # template (never a rescue snapshot) keeps "foreign draft nikdy": a human
        # draft is never a contiguous substring of the /goal template. Resolved
        # ONLY when the box is non-bare (a possible leftover) so a bare pane pays
        # nothing extra; the provenance gate inside `_janitor_recover` is
        # unchanged and still decides WHETHER to act.
        _own = None
        if watchdog._input_box_head_text(captured):
            _own = (rearm_fn or _default_rearm_fn)(cwd)[0]
        jlogs = watchdog._janitor_recover(run, jrec, pid, cwd, captured, loc,
                                          send_fn, dry_run, sleep_fn,
                                          state=state, now=now, own_payload=_own)
        if jlogs:
            logs += jlogs
            if (not dry_run and any(ln.startswith("RECOVERED (janitor)")
                                    for ln in jlogs)):
                state.get("janitor_watch", {}).pop(pid, None)
            continue

        tinfo = watchdog.find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, tmtime = tinfo
        sid = tpath.stem
        visited_sids.add(sid)   # #519 -- live this sweep -> never orphan-reaped

        # #617 -- clear a STRANDED, TRUNCATED own /goal draft the provenance-
        # gated `_janitor_recover` above refuses (see the helper's docstring).
        # Only a VERIFIED clear (stale capture) skips the rest of this pane's
        # sweep; a VETO / give-up / non-convergence lets dark-watch proceed.
        clogs, _cleared = _clear_stranded_truncated_goal(
            sid, cwd, captured, tpath, pid, run, state, now, sleep_fn,
            dry_run, rearm_fn, loc)
        logs += clogs
        if _cleared:
            continue

        # #890 -- marker reading + vetoes + auth-rearm are ABOVE the compact-
        # pending hold (moved from below it). RECOVERY-class origins (auth-rearm,
        # answer-rearm) only RECORD a request (a file write, not a keystroke); the
        # hold exists to prevent a work-pushing NUDGE from landing before /compact,
        # not to block a recovery recording. The dead-loop machinery (confirmation
        # runs + dark-rearm, which goal_sweep types) stays BELOW the hold.

        rec = off_state.get(sid)
        off = rec.get("off") if isinstance(rec, dict) else None
        prior_mark = rec.get("mark") if isinstance(rec, dict) else None
        # #675 -- DEPLOYED-BUT-INERT guard: a PRE-fix sweep advanced `off` PAST an
        # auth-clear line the old pre-filter skipped, so the stored `mark` would
        # stay a stale "set" forever (one_glance armed=yes, auth-rearm never fires
        # — exactly the sessions #675 targets). When the persisted entry predates
        # the current marker-parser version, force a first-sight RESEED (reverse-
        # scan from EOF via `_seed_or_scan_marker(off=None)`) so the NEW recognizer
        # re-reads the whole tail once. Self-healing, no operator action (#618 class).
        if not (isinstance(rec, dict)
                and rec.get("pv") == _GOAL_MARK_PARSER_VERSION):
            off = None
            prior_mark = None
        # #524 -- the transcript mtime from the PRIOR sweep. An advance is a
        # structured LIVENESS proof (the session wrote a turn) -> VETO a
        # death-confirmation run: never type /goal into a loop that is alive.
        prior_tmtime = rec.get("tmtime") if isinstance(rec, dict) else None
        new_off, new_mark, _seedlog = _seed_or_scan_marker(tpath, off, loc, sid)
        if _seedlog:
            logs.append(_seedlog)   # #517 -- deduped-per-sid unknown-past-cap
        # `scan_goal_markers`'s incremental contract means a sweep that
        # produced no NEW appended lines legitimately returns `None` even
        # when the transcript's real newest marker is still the one an
        # EARLIER sweep already found -- CC writes a `Goal set:` marker
        # ONCE, at arm time, never again while the loop keeps (silently)
        # dying, so a caller re-deriving "still armed per transcript" on
        # every sweep from ONLY the incremental delta would see the marker
        # exactly once and then lose it forever. Persist the last KNOWN
        # marker across sweeps and fall back to it whenever this sweep's
        # own delta is empty -- the offset still only ever advances, so the
        # cost stays the same one-small-read-per-sweep this function's own
        # docstring promises.
        mark = new_mark if new_mark is not None else prior_mark
        off_state[sid] = {"off": new_off, "mark": mark, "tmtime": tmtime,
                          "pv": _GOAL_MARK_PARSER_VERSION}   # #675 reseed stamp

        # #522 -- honour the disarm-on-question veto (see `_qdisarm_veto`): never
        # re-arm / accumulate / ping a loop just deliberately cleared for a stuck ❓.
        vetoed, vlog = _qdisarm_veto(qveto, sid, tpath, now, human_ts_fn, loc)
        if vlog:
            logs.append(vlog)
        if vetoed:
            continue

        # #737 C / #890 -- a session PARKED on a ❓ question is ALIVE-waiting.
        # The ORIGINAL #737 veto held unconditionally when the last assistant
        # marker was ❓. #890 makes it ANSWER-AWARE: when the ❓ is present but
        # a genuine human user message FOLLOWED it, the veto LIFTS — the owner
        # answered, so the answer-rearm rider (below) should fire. The veto
        # still holds for all NON-answer-rearm paths (auth-rearm, fulfilled-rearm,
        # dead-loop) when the ❓ is unanswered (#1113: the armed-True branch no
        # longer re-arms -- it only observes a template drift -- so there is no
        # stale-rearm path for this veto to protect any more).
        _awaiting_user = _dark_awaiting_user_veto(tpath)
        if _awaiting_user:
            # #890 -- peek at whether the ❓ was answered. If so, DON'T veto:
            # let the flow proceed to the answer-rearm check below.
            # (Use mark_ts from the current mark, which may not be read yet
            # for the non-"set" branch; for the "set" branch, mark.get("ts").)
            _ans_mark_ts = mark.get("ts") if isinstance(mark, dict) else None
            _q_idx, _q_answered, _ = _answer_rearm_check_transcript(
                tpath, _ans_mark_ts)
            if not _q_answered:
                if confirm_state.pop(sid, None) is not None:
                    logs.append("dark-watch %s sid=%s -> hold:awaiting-user "
                                "(❓ marker, confirmation run reset)"
                                % (loc, sid))
                seen_state.pop(sid, None)
                pinged_state.pop(sid, None)
                continue
            # ❓ was answered — lift the veto, let the flow proceed.

        armed = watchdog.pane_goal_armed(captured)

        if mark is None or mark.get("state") != "set":
            # #675 -- CC cleared the goal on a TRANSIENT auth failure
            # (clear_kind=="auth"): the session resumes in seconds, so re-arm it
            # via the SAME dark-rearm channel (deliver_goal's recent-human +
            # freshness gates apply). A USER `/goal clear` (or a non-auth `error`
            # clear) is NEVER re-armed (#170) and just resets the ping/confirm
            # state below.
            # #890 -- this block is now ABOVE the compact-pending hold, so auth-
            # rearm records its request regardless of a pending /compact.
            if (mark is not None and mark.get("state") == "cleared"
                    and mark.get("clear_kind") == "auth"):
                ar = _auth_rearm_decide(sid, cwd, mark, armed, now, loc, dry_run,
                                        rearm_fn, obligation_fn, requests_path,
                                        auth_attempts_state)
                if ar:
                    logs.append(ar)
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            confirm_state.pop(sid, None)   # #524 -- episode over (clear/no marker)
            continue
        mark_ts = mark.get("ts")

        # #741/#890 WRITER-SIDE LATCH: a pending /compact for this session HOLDS
        # the dead-loop RE-ARM below (a re-arm WRITE schedules a next-batch /goal
        # for job 9 to type). Moved DOWN from its prior position so these run
        # ABOVE it: auth-rearm, the stale-DRIFT observation (armed=True, #1113 --
        # LOGS only, never records/types), fulfilled-rearm + answer-rearm. The
        # RECORD paths only write a request file (never a keystroke) and the
        # stale-drift path only logs; the hold was built for work-pushing nudges,
        # not recordings. The dead-loop confirmation + dark-rearm stays BELOW.

        if armed is True:
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            # #524 -- the glyph is present: the loop is ALIVE. If a death-
            # confirmation run was accumulating, this is a VETO-ALIVE reset
            # (the montalu idle-alive flicker) -- logged only when a run
            # actually existed, so a healthy armed pane is silent every sweep.
            if confirm_state.pop(sid, None) is not None:
                logs.append("dark-watch %s sid=%s -> VETO-ALIVE:render-armed "
                            "(glyph present, confirmation run reset)"
                            % (loc, sid))
            # #623/#1113 -- an ALIVE armed loop can carry a STALE condition
            # (armed before the last SKILL.md deploy). We NEVER re-type it (the
            # owner's >10x regression): OBSERVE the drift once, and let the next
            # NATURAL arm apply the new template. A keystroke into an active loop
            # is deleted here.
            sr = _stale_rearm_decide(sid, cwd, mark, now, loc, dry_run,
                                     rearm_fn, tmpl_seen_state=stale_tmpl_state)
            if sr:
                logs.append(sr)
            # #878 — goal-guard rider: an ALIVE armed loop with a FOREIGN
            # condition that lacks the blocked-❓ capability while a LASTQF
            # exists gets a one-time nudge (≤1/24h). Never auto-types /goal.
            payload = mark.get("payload") if isinstance(mark, dict) else None
            gg = _goal_guard_decide(sid, payload,
                                    (rearm_fn or _default_rearm_fn)(cwd)[0],
                                    state, now, loc,
                                    mark_state=(mark.get("state")
                                                if isinstance(mark, dict)
                                                else None))
            if gg:
                logs.append(gg)
                # #923: collect into state["nudge_batch"] for goal_lane_sweep
                # to compose; goal-guard's gate_ok skipped here, checked by
                # batch_eligible in the sweep. Single-slot write (Y2 fix) —
                # no unbounded append per sweep.
                _gg_batch = []
                logs += _goal_guard_deliver(
                    sid, pid, captured, cwd, state, now, loc, run,
                    sleep_fn, dry_run, projects_dir,
                    batch_collect=_gg_batch)
                if _gg_batch:
                    state.setdefault("nudge_batch", {})[sid] = _gg_batch
            continue
        if armed is None:
            # #524 -- undeterminable footer (busy / chrome / dialog -> None):
            # never a clean-dark read, so it breaks the CONSECUTIVE run. Reset
            # only an already-accumulating run (never mints a new entry for a
            # never-suspected pane). No type, no new ping -- retry next sweep.
            crec = confirm_state.get(sid)
            if isinstance(crec, dict) and crec.get("clean_run"):
                crec["clean_run"] = 0
                crec["run_start"] = None
                crec["last"] = now
            continue

        # armed is False, mark == "set" -- the silently-dead-loop shape.
        # #524 LIVENESS VETO: the transcript mtime advanced since the prior
        # sweep -> the session wrote a turn -> it is ALIVE, only its footer read
        # dark this sweep. Reset the confirmation run and never type.
        if (isinstance(prior_tmtime, (int, float))
                and isinstance(tmtime, (int, float)) and tmtime > prior_tmtime):
            if confirm_state.pop(sid, None) is not None:
                logs.append("dark-watch %s sid=%s -> VETO-ALIVE:mtime-advanced "
                            "(session wrote a turn, confirmation run reset)"
                            % (loc, sid))
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            continue
        if _stream_rearm(logs, sid, cwd, tpath, mark, now, loc, dry_run, state, rearm_fn,
                         requests_path, (seen_state, pinged_state, confirm_state), pid, run):
            continue   # #1143 -- a dark stream loop not owner-ended (held / re-armed)

        # #764 FULFILLED-REARM lane: a stop-(B) COMPLETED loop (🏁 proof in the
        # bounded tail / proof cache, AFTER the arm -- #767) whose backlog
        # REFILLED is re-armed FAST via
        # the structured channel -- distinct from the silently-dead machinery
        # below (a fulfilled loop is transcript-identical to a dead one EXCEPT
        # the 🏁 line, so the dead-loop confirmation was never built for it). A
        # stop-(A) ❓-blocked completion prints NO 🏁 -> this never fires on it.
        # NEVER a free-text nudge (owner directive #764) -- it only WRITES a
        # request; the keystroke + all its gates live in deliver_goal. Placed
        # AFTER the mtime liveness veto (never re-arm a still-writing loop) and
        # BEFORE the dead-loop debounce (a fulfilled+refilled loop re-arms fast,
        # never through the 8-read/2-per-day confirmation). #766: the SAME decide
        # call also detects a fulfilled loop whose backlog is genuinely DRAINED
        # (fresh open==0) and returns the FULFILLED-SILENT sentinel -> the branch
        # below VETOes the #459 dead-loop ping for it (an achieved final state is
        # not a dead loop), so a legitimately-completed loop is never pinged.
        _frline, _frhandled = _fulfilled_rearm_decide(
            sid, cwd, tpath, mark_ts, now, loc, dry_run, rearm_fn,
            obligation_fn, requests_path, fulfilled_state, fulfilled_proof,
            seen_state, pinged_state, confirm_state)
        if _frline:
            logs.append(_frline)
        if _frhandled is _FULFILLED_SILENT:
            # #766 -- a 🏁-proven achieved loop (fresh open==0) is FULFILLED, not
            # silently-dead: VETO the #459 ping (the FALLBACK below fires it
            # ALWAYS otherwise) and `continue` BEFORE the debounce -- never a
            # ping, never a keystroke. Logs once per episode (helper, #764 flood).
            _vline = _fulfilled_silent_veto(sid, mark_ts, loc, dry_run,
                                            seen_state, pinged_state,
                                            confirm_state)
            if _vline:
                logs.append(_vline)
            continue
        if _frhandled:
            continue

        # #890 ANSWER-REARM lane: a stop-(A) ❓-blocked loop whose owner
        # ANSWERED. The awaiting-user veto (above) holds while the ❓ is
        # unanswered; once the owner answers, the veto lifts, `armed is False` +
        # `mark == "set"` reaches here, and this rider detects the answered-❓
        # pattern from the transcript. Like auth-rearm, it only RECORDS a
        # request (a file write) — the keystroke + all its gates live in
        # deliver_goal. Placed ABOVE the compact-pending hold.
        _arline = _answer_rearm_decide(
            sid, cwd, tpath, mark, mark_ts, armed, now, loc, dry_run,
            rearm_fn, obligation_fn, requests_path,
            answer_attempts_state)
        if _arline:
            logs.append(_arline)
            # If a re-arm was recorded (not just a skip log), move on.
            if "recording re-arm" in _arline:
                continue

        # #741/#890 -- compact-pending hold, MOVED DOWN from its prior position
        # (above marker reading). Recovery-class origins (auth-rearm above,
        # fulfilled-rearm above, answer-rearm above) already ran;
        # this holds ONLY the dead-loop confirmation + dark-rearm machinery below.
        if _compact.pending_compact_hold(sid, now):   # #848 bounded
            logs.append("dark-watch %s sid=%s -> hold:compact-pending "
                        "(pending /compact; no dead-loop re-arm until it "
                        "delivers)" % (loc, sid))
            continue

        prior = seen_state.get(sid)
        if not isinstance(prior, dict) or prior.get("mark_ts") != mark_ts:
            seen_state[sid] = {"mark_ts": mark_ts, "first_seen": now}
            logs.append("dark-watch %s sid=%s -> first observation, debouncing"
                        % (loc, sid))
            continue
        # #459/#478/#524 -- CONFIRMED silently-dead loop (full rationale in this
        # module's header docstring + #524's design comment). Advance the
        # death-CONFIRMATION run FIRST -- BEFORE the ping-backoff below -- so it
        # accumulates on EVERY clean-dark sweep, not only ping-due ones.
        confirmed, confirm_state[sid] = _dark_confirm_advance(
            confirm_state.get(sid), mark_ts, now)
        open_n, cts = (obligation_fn or _default_obligation_fn)(cwd)
        fresh = (cts is not None
                 and 0 <= (now - cts) <= GOAL_DARK_CACHE_MAX_AGE_S)
        workable = isinstance(open_n, int) and open_n > 0 and fresh

        # Can the watchdog SELF-HEAL this loop via an auto-type? Only a workable
        # backlog + a resolvable /goal template + an un-exhausted 24h cap. A
        # self-healing loop accumulates SILENTLY toward a CONFIRMED type (no
        # spurious #459 ping -- montalu); one that CANNOT self-heal falls to the
        # ping so the human is told.
        rearm_text = rearm_auth = None
        attempt_ok = False
        attempt_wait = None
        # #921 residual (review H1): defer to ANY pending request — a dark-
        # rearm must not re-record while the prior one is undelivered.
        _has_pending = isinstance(
            load_goal_requests(requests_path).get(sid), dict)
        if workable and not _has_pending:
            rearm_text, rearm_auth = (rearm_fn or _default_rearm_fn)(cwd)
            attempt_ok, attempts_state[sid], attempt_wait = _dark_rearm_attempt_ok(
                attempts_state.get(sid), now)
        can_self_heal = bool(workable and rearm_text and attempt_ok)

        # TYPE only on a CONFIRMED-dead, self-healing run (the montalu flicker
        # never reaches confirmation). NEVER a keystroke otherwise.
        if confirmed and can_self_heal:
            logs.append(_dark_record_rearm(
                sid, cwd, rearm_text, rearm_auth, now, loc, open_n, dry_run,
                confirm_state, pinged_state, attempts_state, requests_path))
            continue

        # A self-healing loop that is not yet CONFIRMED accumulates SILENTLY --
        # no spurious ping (the montalu case). EXPLICIT decision log (#486
        # direction, never a silent suppression), at the same density as the
        # "first observation, debouncing" line above.
        if can_self_heal:
            _win = confirm_state.get(sid) or {}
            logs.append(
                "dark-watch %s sid=%s -> ACCUMULATING (workable, reads=%s/%d "
                "span=%ss/%ds — silent until CONFIRMED, no ping)"
                % (loc, sid, _win.get("clean_run"), GOAL_DARK_CONFIRM_MIN_READS,
                   int(now - (_win.get("run_start") or now)),
                   GOAL_DARK_CONFIRM_MIN_SPAN_S))
            continue

        if workable and rearm_text and not attempt_ok:
            if attempt_wait is not None:
                # #804 mode-2 -- past the base cap but within the daily strop: the
                # loop keeps re-arming on an ESCALATING backoff, so it is NEVER
                # silent-until-midnight (montalu2). This is an ACTIVE retry, not an
                # abandonment -- log the DECISION + next-eligible and `continue`,
                # NOT a premature #459 ping (the ping fallback below is for a loop
                # the watchdog cannot self-heal, which is not the case here).
                logs.append(
                    "dark-watch %s sid=%s -> re-arm BACKOFF (next in %ss, %d "
                    "attempts/24h — never silent)"
                    % (loc, sid, attempt_wait, len(attempts_state.get(sid) or [])))
                continue
            logs.append(
                "dark-watch %s sid=%s -> ATTEMPT-CAP: %d auto-types in 24h, "
                "ping only" % (loc, sid, GOAL_DARK_REARM_HARD_CAP_PER_DAY))

        # #459 ping FALLBACK (staged schedule): reached for a NON-workable dark
        # backlog, an unresolvable template, OR an exhausted attempt cap -- the
        # cases the watchdog cannot self-heal, so the human must act. The FIRST
        # ping fires ALWAYS; a LATER re-ping needs a fresh workable cache -- else
        # stay SILENT (a NON-🏁 dark loop is transcript-identical to a stall).
        # #766: a 🏁-PROVEN achieved loop (fresh open==0) is DISTINGUISHABLE and
        # never reaches here -- it is vetoed FULFILLED-SILENT above; only a dark
        # loop WITHOUT a proven drained backlog falls through to this ping.
        # #804 item 4 -- a dark episode pings AT MOST ONCE. The staged re-ping
        # (count>1, GOAL_DARK_REPING_SCHEDULE_S) is DELETED: the ping is
        # notify-SUPPRESSED (goal-dark, #704) and the #795 re-ask is retired, so
        # a re-ping only composed a dropped message. `pinged_state` is now a
        # per-episode already-pinged LATCH (mark_ts-keyed) -- popped by the
        # confirmed re-arm / FULFILLED-SILENT paths above, so a genuine re-arm or
        # a fresh episode (new mark_ts) still pings once.
        prec = pinged_state.get(sid)
        if isinstance(prec, dict) and prec.get("mark_ts") == mark_ts:
            continue                              # already pinged this episode
        pinged_state[sid] = {"mark_ts": mark_ts, "last": now}
        logs.append("dark-watch %s sid=%s -> goal died silently, pinging"
                    % (loc, sid))
        if send_fn is not None and not dry_run:
            from notify import stream_redirect
            proj = watchdog.project_label(cwd)
            # #403's exact dedup_key (goal-dark:sid:mark) so a legacy on-disk
            # marker from pre-#459 code never yields a duplicate first ping
            # across the deploy boundary.
            send_fn(
                "\U0001f480 **%s** — /goal loop zomrelo potichu "
                "(transkript hovorí armovaný, footer nie). "
                "Spústi prosím `/autopilot` znova." % proj,
                owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                dedup_key="goal-dark:%s:%d" % (sid, int(mark_ts or 0)),
                dry_run=dry_run)

    if not dry_run:   # #519 -- prune goal_mark for gone+aged sessions (dry-run: no state mutation)
        _prune_goal_mark_orphans(off_state, visited_sids, now)
        # #1092 (e) / #1113 -- reap the stale-drift OBSERVATION latch for GONE
        # sessions (a sid with no live transcript this sweep), the PRIMARY
        # live-gate the sibling per-sid dedup dicts use (#486-G5 leak lesson): the
        # value is a bare template hash with no ts, so it cannot age-reap. A
        # session that resolved a live transcript this sweep is in `visited_sids`
        # (added at transcript resolution above). CAVEAT (accepted): a live
        # session TRANSIENTLY skipped BEFORE that add-point (pane-in-mode /
        # janitor-recover / sweep-budget-break / transcript-miss continue) is not
        # in `visited_sids`, so its latch CAN be dropped that sweep -- UNLIKE
        # `_prune_goal_mark_orphans` (#519) which pairs the live-gate with an age
        # secondary. The impact is now trivial (#1113 removed the record/keystroke
        # the pre-#1113 note reasoned about): a dropped latch re-opens at most ONE
        # extra `stale-drift` JOURNAL LINE next sweep -- no request, no keystroke.
        # A returning session simply re-classifies (current -> silent).
        for _gsid in [k for k in list(stale_tmpl_state.keys())
                      if k not in visited_sids]:
            stale_tmpl_state.pop(_gsid, None)
    return logs


# --------------------------------------------------------------------------- #
# #522 QUESTION-REPOKE DISARM -- the backstop for a `/goal` loop STUCK re-poking
# an unanswered `❓ NEEDS YOU` (the native evaluator ignoring stop-condition (A)).
# Watchdog-INITIATED keystroke, mirroring the LANE nudge's own architecture
# (recent-human gate + 24h cap + explicit decision log), NOT dark_watch's
# keystroke-free record-a-request shape -- the disarm is a bounded, self-limiting
# `/goal clear` typed directly via the shared verified-delivery primitives.
# --------------------------------------------------------------------------- #

_QDISARM_TRANSIENT_SKIPS = frozenset(
    ("skip:busy", "skip:no-input-line", "skip:draft"))


def _deliver_goal_clear(pid, text, run, captured, state, now, sleep_fn, logs,
                        sid=None):
    """#522 -- deliver the `/goal clear` disarm keystroke to a BARE input box via
    a verified typed send (`_send_goal_verified`, the 'symmetric inverse' of the
    arm keystroke), with the shared janitor (#372) provenance mark so a stuck send
    is recoverable (`/goal ` is already an own-prefix; the 11-char payload never
    wraps, so a swallowed send is backed off by `_undo_and_release_slot`).

    A FOREIGN DRAFT is DEFERRED, never stash-parked: a genuinely stuck away-user
    loop waits at a BARE box, so a draft means the user is actively composing an
    answer RIGHT NOW (the streak's `_is_genuine_human_prompt` only breaks on a
    LANDED turn, not mid-composition) -- disarming then is both pointless (they
    are about to resolve the ❓) and a keystroke into an active pane, which this
    codebase forbids. Unlike `deliver_goal`'s arm (which DOES park a draft), the
    disarm defers and retries once the box is bare.

    Returns 'sent' | 'skip:busy' | 'skip:no-input-line' | 'skip:draft' |
    'skip:verify-failed'. The three transient skips (busy / no-input-line / draft)
    mean NO keystroke was attempted (retry next sweep, free -- see
    `_QDISARM_TRANSIENT_SKIPS`). 'sent' and a GENUINE 'skip:verify-failed' (a
    keystroke typed but not verified) EACH consume an attempt-cap slot -- the
    #524 fail-safe the caller (`goal_question_repoke_watch`) records, so a
    persistently unverifiable pane stops after GOAL_QDISARM_MAX_PER_DAY (#1063
    addendum; #921 had narrowed the record to sent-only). A 'skip:verify-failed'
    returned only because the #1002 kill switch SUPPRESSED the keystroke typed
    NOTHING, so the caller does NOT count that toward the cap -- it keeps
    flagging the suppression instead (defensive: goal-disarm is a RECOVERY kind,
    never staged off)."""
    kind, draft = watchdog._classify_boundary(captured)
    if kind == "no-input-line":
        return "skip:no-input-line"
    # #720/#714 -- the disarm keystroke path shares the arm path's busy-Waiting
    # gap: never submit `/goal clear` into a "Waiting for N background agents"
    # pane (the submit is swallowed). Defer, retry next sweep.
    # #1023: idle-pane only — a busy Waiting pane always defers (aged override gone)
    if kind == "busy":
        return "skip:busy"
    if _ops_wait_recheck._pane_busy_waiting(captured):
        return "skip:busy"
    if draft:
        return "skip:draft"          # user is composing -- never disturb, retry next sweep
    watchdog._janitor_mark_watch(state, pid, now)
    # verify_armed=False -- a `/goal clear` DISARMS, so the #720 arm-confirm must
    # NOT run (a successful disarm leaves pane_goal_armed False, not a failure).
    # nudge=GOAL_DISARM_NUDGE (#1063) -- a RECOVERY kind, so the #1002 kill switch
    # never suppresses the disarm even when every machine nudge is staged OFF. The
    # pre-#1063 DEFAULT `goal-sweep` (a MACHINE kind) silently disabled the #522
    # backstop fleet-wide.
    ok = _send_goal_verified(pid, text, run, captured=captured,
                             sleep_fn=sleep_fn, logs=logs, verify_armed=False,
                             nudge=GOAL_DISARM_NUDGE)
    if ok:
        watchdog._janitor_clear_watch(state, pid)
        return "sent"
    return "skip:verify-failed"


def _reap_qdisarm_state(qveto, attempts, now, ttl_s=GOAL_QDISARM_STATE_TTL_S):
    """#522/#486-G5 -- age-gated reaper for BOTH per-sid dicts this job writes, so
    a session that vanished cannot leak an entry forever. `qveto` entries carry a
    `disarmed_ts`; reap one untouched beyond `ttl_s` (a stale veto is moot -- the
    goal is long cleared -- and a wrongly-reaped one just means the standard
    re-arm path is no longer vetoed, which after 24h is correct). `attempts`
    entries are rolling-window lists; reap one whose newest ts is older than 24h
    OR is empty/malformed (a REAPER, never a per-episode pop, so a live capped sid
    -- which refreshes its newest ts -- is never reaped). Never raises."""
    day = 24 * 3600
    for sid in [k for k, v in list(qveto.items())
                if not (isinstance(v, dict)
                        and isinstance(v.get("disarmed_ts"), (int, float))
                        and 0 <= (now - v["disarmed_ts"]) <= ttl_s)]:
        qveto.pop(sid, None)
    for sid in [k for k, v in list(attempts.items())
                if not (isinstance(v, list)
                        and any(isinstance(t, (int, float))
                                and 0 <= (now - t) <= day for t in v))]:
        attempts.pop(sid, None)


def goal_question_repoke_watch(now, run=None, state=None, send_fn=None,
                               dry_run=False, projects_dir=None, sleep_fn=None,
                               time_fn=None, sweep_deadline=None, human_fn=None,
                               human_ts_fn=None, repoke_fn=None):
    """#522 -- disarm a `/goal` loop STUCK re-poking an unanswered `❓ NEEDS YOU`.

    Per live candidate pane (`_reconcile_candidate_panes`, budget-shared exactly
    like `goal_dark_watch`): resolve the transcript, and

      1. HONOUR / re-enter an existing disarm veto (`state["goal_disarmed_q"]`):
         if a genuine human answer landed after the disarm the veto is cleared
         (re-entry, log); otherwise this sid is already disarmed -> skip (log).
      2. Only an ARMED pane (`pane_goal_armed is True`) is a candidate -- a
         non-armed / obscured pane, and a served (non-`/goal`) session that merely
         ended one turn on `❓ NEEDS YOU`, are skipped (the latter also never
         reaches the N-consecutive-repoke threshold anyway).
      3. Read the STREAK via `repoke_fn` (`question_repoke_streak`): N consecutive
         byte-identical re-pokes with no human answer between. Below N -> log the
         accumulation (never silent), no action.
      4. At/above N: gate the keystroke on recent-human (never type into a pane a
         human just touched) and a 24h/2 attempt cap, then deliver `/goal clear`
         via `_deliver_goal_clear`. A landed disarm writes the veto (1).

    `human_fn`/`human_ts_fn`/`repoke_fn` are injected for tests; the defaults are
    the real `_is_genuine_human_prompt` / `_last_human_prompt_ts` /
    `question_repoke_streak`. Returns the decision-log lines."""
    logs = []
    if watchdog._owner_disabled("goal"):
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
        return logs
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    sleep_fn = sleep_fn or time.sleep
    state = state if state is not None else {}
    human_fn = human_fn or watchdog._is_genuine_human_prompt
    human_ts_fn = human_ts_fn or watchdog._last_human_prompt_ts
    repoke_fn = repoke_fn or watchdog.question_repoke_streak
    qveto = state.setdefault("goal_disarmed_q", {})
    attempts = state.setdefault("goal_qdisarm_attempts", {})
    _reap_qdisarm_state(qveto, attempts, now)

    for pid, cwd, _cmd in watchdog._reconcile_candidate_panes(run):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            logs.append("qrepoke-budget-exceeded — deferring remaining panes "
                        "to next sweep")
            break
        if watchdog.pane_in_mode(pid, run):
            continue
        tinfo = watchdog.find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, _tmtime = tinfo
        sid = tpath.stem
        loc = watchdog._pane_location(pid, run) or pid

        # (1) an existing veto short-circuits detection (already disarmed / re-entry).
        #     `_qdisarm_veto` always returns a logline for a dict vrec (ACTIVE or
        #     CLEARED); relabel its "dark-watch" prefix to this job's "qrepoke".
        if isinstance(qveto.get(sid), dict):
            _vetoed, vlog = _qdisarm_veto(qveto, sid, tpath, now, human_ts_fn, loc)
            logs.append(vlog.replace("dark-watch", "qrepoke", 1))
            continue

        captured = watchdog.capture_pane(pid, run, lines=40)
        # (2) only an armed goal loop is disarmable -- a served session that asked
        # once (streak 1) or a cleared/obscured footer is not our target.
        if watchdog.pane_goal_armed(captured) is not True:
            continue

        # (3) authoritative transcript streak.
        streak, _qline = repoke_fn(tpath, human_fn)
        if streak < GOAL_QUESTION_REPOKE_MIN:
            if streak > 0:
                logs.append("qrepoke %s sid=%s -> %d/%d re-pokes, accumulating "
                            "(no action)" % (loc, sid, streak,
                                             GOAL_QUESTION_REPOKE_MIN))
            continue

        # (4) CONFIRMED stuck -- gate the keystroke (#731: + the client-input signal).
        recent, reason = watchdog._goal_autoarm_recent_human_activity(
            sid, tpath, now, pane_target=pid, run=run)
        if recent:
            logs.append("qrepoke %s sid=%s -> CONFIRMED stuck (%d re-pokes) but "
                        "recent human (%s) -- skip" % (loc, sid, streak, reason))
            continue
        # The rolling 24h cap is computed from `pruned` (the age-pruned window);
        # it is only WRITTEN BACK on a real slot-consume below, so a transient
        # skip (busy / draft) never leaves a spurious empty/stale entry (the
        # top-of-sweep reaper drops fully-aged ones).
        ok_cap, pruned = _qdisarm_attempt_ok(attempts.get(sid), now)
        if not ok_cap:
            logs.append("qrepoke %s sid=%s -> disarm attempt cap reached "
                        "(%d/24h)" % (loc, sid, GOAL_QDISARM_MAX_PER_DAY))
            continue
        # #741 WRITER-SIDE LATCH: a pending /compact for this session HOLDS the
        # `/goal clear` disarm keystroke -- keep the pane pristine so job 14 can
        # deliver the boundary /compact; the stuck-❓ disarm resumes once the
        # compact clears (or ages out at the ❓ not-a-boundary, then this re-fires).
        if _compact.pending_compact_hold(sid, now):   # #848 bounded
            logs.append("qrepoke %s sid=%s -> hold:compact-pending "
                        "(pending /compact; disarm deferred)" % (loc, sid))
            continue
        if dry_run:
            logs.append("qrepoke %s sid=%s -> CONFIRMED stuck (%d re-pokes) -- "
                        "would disarm (dry-run)" % (loc, sid, streak))
            continue
        word = _deliver_goal_clear(pid, GOAL_CLEAR_TEXT, run, captured, state,
                                   now, sleep_fn, logs, sid=sid)
        if word in _QDISARM_TRANSIENT_SKIPS:
            logs.append("qrepoke %s sid=%s -> disarm deferred (%s), retry next "
                        "sweep" % (loc, sid, word))
            continue
        if word == "sent":
            # A landed disarm records an attempt-cap slot AND writes the re-entry
            # veto (the goal is actually cleared).
            attempts[sid] = pruned + [now]
            qveto[sid] = {"disarmed_ts": now, "streak": streak}
            logs.append("qrepoke %s sid=%s -> DISARMED: /goal clear typed "
                        "(%d re-pokes, attempt=%d/%d)"
                        % (loc, sid, streak, len(attempts[sid]),
                           GOAL_QDISARM_MAX_PER_DAY))
        elif not watchdog.nudges_enabled(GOAL_DISARM_NUDGE):
            # #1063 journal honesty: a disarm SUPPRESSED by the kill switch typed
            # NOTHING (keys() bailed before the send), so it is NOT an attempt and
            # consumes NO cap slot -- keep flagging the suppression every sweep so
            # a future re-staging is never silent, and read as the kill switch,
            # not the misleading generic `skip:verify-failed`. Defensive:
            # goal-disarm is a RECOVERY kind (always-on), so this is dead in
            # production -- it only fires if a future edit re-stages the disarm
            # off (the exact regression the static guard forbids).
            logs.append("qrepoke %s sid=%s -> disarm suppressed: nudges OFF for "
                        "kind %s" % (loc, sid, GOAL_DISARM_NUDGE))
        else:
            # #1063 addendum: a GENUINE `skip:verify-failed` DID attempt a
            # keystroke that never landed -- consume an attempt-cap slot (the
            # #524 fail-safe the `_deliver_goal_clear` / `_qdisarm_attempt_ok`
            # docstrings describe, that #921 had narrowed to sent-only). NO veto:
            # the goal was not cleared. After GOAL_QDISARM_MAX_PER_DAY such
            # failures the cap gate above stops the ~60 s re-type storm on an
            # unverifiable pane.
            attempts[sid] = pruned + [now]
            logs.append("qrepoke %s sid=%s -> disarm delivery FAILED (%s)"
                        % (loc, sid, word))
    return logs


# --------------------------------------------------------------------------- #
# LANE-OCCUPANCY NUDGE (#365, owner directive 2026-08-11) -- the ONE
# watchdog-INITIATED action the owner explicitly named as surviving this
# collapse. Migrated here from job 20's old `_goal_lane_occupancy_nudge`;
# #442 then un-suppressed it in place: the recent-human-activity gate keeps
# firing (this one is a real watchdog-initiated action, unlike arm delivery
# above) but through the lane path's OWN short live-conversation window
# below, and an at-rest draft is DELIVERED via `deliver_with_stash` instead
# of being a "skip draft" dead end.
# --------------------------------------------------------------------------- #

GOAL_LANE_IDLE_S = 15 * 60
# #530 -- 1h reference window. Originally the empty-lane nudge's HARD HOURLY CAP;
# #1089 retired the lane-occupancy keystroke DELIVERY and #1096 deleted the whole
# delivery-cadence gate (`_lane_cooldown_decision`/`skip:hourly-cap`/`llast`), so
# this constant now serves ONLY as the idle threshold in
# `_lane_effective_min_backlog` (a loop idle > 1h over 1-2 workable tickets drops
# its min-backlog floor to 1). Refill-nudge cadence is bounded by the shared
# per-kind `_nudge_gate.gate_ok` gate now, not by this cap.
GOAL_LANE_INTERVAL_S = 60 * 60
# #937-review C1 -- agent_type values that represent IMPLEMENTATION workers
# whose finished state means "ticket in integration" (coverage). Non-worker
# subagents (validator/Explore/an ad-hoc review consult) review/validate, not
# implement. (#991: the pinned tier-agent types are gone — the lane worker is
# the single autopilot-worker type; the working model chooses its model.)
_LANE_WORKER_AGENT_TYPES = frozenset({"autopilot-worker"})


def _lane_effective_min_backlog(idle):
    """#804 mode-4 -- the empty-lane min-backlog floor, idle-aware. `GOAL_LANE_
    MIN_BACKLOG` (3) is the #530 anti-storm floor for a FRESHLY-idle box; a loop
    that has STOOD idle > GOAL_LANE_INTERVAL_S (1h) over just 1-2 workable tickets
    is a stuck loop, not fresh churn (#791 "stojí navždy by design"), so the floor
    drops to 1. Used in BOTH the empty-lane gate AND the #611 working-no-tasks
    escalation clamp (a shared derivation so the two can never drift). `idle=None`
    (a legacy caller / a test) keeps the static floor -- byte-identical pre-#804."""
    return 1 if (isinstance(idle, (int, float)) and idle > GOAL_LANE_INTERVAL_S) \
        else GOAL_LANE_MIN_BACKLOG
# #530 -- EMPTY-LANE MIN-BACKLOG floor: a fully-stalled box (0 dispatched
# workers) is nudged only with at least this many genuinely-workable open
# tickets. A lone open umbrella epic / 1-2 held-or-foreign items reads as
# "workable" for core-quals but is not dispatchable, and nudging it produced the
# reported gk storm (nudge -> "nič workable" -> nudge ...). #726: this is now the
# ONLY backlog floor -- the under-saturated surplus floor was retired with the
# fill nudge (a running batch is never refilled under batch mode). #804 mode-4:
# this is the FRESHLY-idle floor only -- once a box has stood idle > 1h the floor
# drops to 1 (`_lane_effective_min_backlog`), so a long-stuck loop over 1-2
# workable tickets is poked instead of parked forever.
GOAL_LANE_MIN_BACKLOG = 3
GOAL_LANE_LIVE_WINDOW_S = 15 * 60

# #804 -- how often the DEAD-SESSION roster census re-surfaces a persistently-
# dead expected-armed stream: at most ONE verdict line per dead cwd per this
# window, so a stream that stays dark does not flood the journal every 60s sweep
# (the #766 once-per-episode latch lesson). A stream that comes back live clears
# its own latch so a FUTURE death re-surfaces.
GOAL_ROSTER_CENSUS_S = 60 * 60


def _roster_age_desc(now, ts):
    """A short human age ('3h12m' / '48m' / '?') for a roster entry's armed_ts
    in a DEAD-SESSION census line. `?` on a missing/corrupt ts (never raises)."""
    if not isinstance(ts, (int, float)):
        return "?"
    secs = int(now - ts)
    if secs < 0:
        return "0m"
    h, m = secs // 3600, (secs % 3600) // 60
    return ("%dh%02dm" % (h, m)) if h else ("%dm" % m)


def _resurrect_dead_entry(dcwd, dentry, dloc, now, run, projects_dir, dry_run):
    """#804 mode-5 -- evaluate + (opt-in) fire a RESURRECT for ONE dead roster
    entry. Returns `(log_lines, dirty)`; mutates `dentry`'s resurrect anchors
    (rgts/ratt/rfails). Extracted from goal_lane_sweep's census loop to keep it
    under the function-line cap (#502/#511) and to isolate the irreversible
    keystroke path into one place; the decision semantics live in `resurrect.py`.

    A relaunch fires ONLY behind the opt-in AIRULESET_RESURRECT_ACTION flag AND
    all of decide()'s gates (a bare-idle shell pane, no recent human, not
    dry-run). The live keystroke is opt-in-until the supervisor verifies it live
    (design M5); `rgts` bounds an attempt to once per RESURRECT_CADENCE_S so a
    persistent no-pane/disabled/veto state never floods the journal."""
    rdue, _ = _resurrect.due(dentry, now)
    if not rdue:
        return [], False
    logs = []
    # #805 interface -- a relaunch that fired last due-cycle (`ratt`) yet the
    # stream is STILL dead now is a FAILED attempt; count it so launch_cmd
    # escalates --continue -> fresh after RESURRECT_MAX_FAILS (a ballooned-context
    # session that dies straight after --continue never livelocks). A cwd that
    # came back live resets rfails/ratt (the sweep's visited-cwd loop).
    if dentry.get("ratt"):
        rf = dentry.get("rfails", 0)
        dentry["rfails"] = (rf if isinstance(rf, (int, float)) else 0) + 1
    rpane = _resurrect.find_pane(dcwd, run)
    # mode-4 -- the HARD recent-human veto (all 3 signals: presence marker for the
    # dead sid, its last transcript human prompt, and the attached tmux client's
    # input on this pane's SESSION -- max client_activity across attached
    # clients). A dead session's transcript is static, so signal 3 (a human at the
    # shell pane NOW) is the operative one; signals 1/2 are threaded for
    # completeness. `pane_is_bare_idle` is the SEPARATE structural guard against a
    # stale half-typed command the 5-min signal-3 window no longer sees. Both are
    # evaluated only when a relaunch pane exists.
    rhuman, rreason, rbare = (False, "", False)
    if rpane is not None:
        rbare = _resurrect.pane_is_bare_idle(rpane, run)
        rtinfo = watchdog.find_active_transcript(projects_dir, dcwd)
        rhuman, rreason = watchdog._goal_autoarm_recent_human_activity(
            dentry.get("sid", ""), rtinfo[0] if rtinfo else None, now,
            pane_target=rpane, run=run)
    rlog, ract = _resurrect.decide(
        dentry, dloc, rpane, rbare, rhuman, rreason,
        _resurrect.action_enabled(), dry_run)
    logs.append(rlog)
    dentry["rgts"] = now
    dentry["ratt"] = bool(ract)   # did we fire a relaunch THIS cycle?
    if ract and not _resurrect.relaunch(
            rpane, _resurrect.launch_cmd(dentry), run):
        logs.append("resurrect %s -> relaunch send FAILED (retry next cadence "
                    "window)" % dloc)
    return logs, True


# #442 -- the lane-fill path's OWN "live conversation" definition. The
# shared check's default window (`GOAL_AUTOARM_RECENT_HUMAN_S`, 30 min) is
# calibrated for the VIRGIN-ARM decision -- irreversibly arming a whole
# loop into a possibly-live conversation -- and blanket-applying it here
# made the nudge structurally self-suppressing on any box the owner merely
# GLANCES at every ~20-30 min (gk journal: "SKIP-TRANSIENT ... presence
# marker 1331-1628s old" on every single attempt). The lane nudge is a
# rate-limited reminder into an ALREADY-armed session, so a much shorter
# window suffices -- with its meaning stated honestly (#442-review F1):
# the presence marker is stamped ONLY on UserPromptSubmit
# (`clear-question-dedup.sh`), i.e. a prompt SUBMIT, never composition
# keystrokes -- so this window means "a prompt was SUBMITTED within the
# last ~3 min = a genuinely live exchange" (and it comfortably covers the
# mid-sweep race where a submit lands after the sweep's own `now` was
# captured, via the shared check's symmetric clamp). Un-submitted
# COMPOSITION stamps neither signal and is caught separately, by the
# two-capture draft-diff check at the send point below. Worst-case
# annoyance stays bounded by the shared per-kind cadence gate (and #1089 retired
# the lane-occupancy keystroke DELIVERY entirely — only the decision line remains).
GOAL_LANE_LIVE_CONVO_S = 3 * 60

# #531 -- orphan-reap TTL for state["goal_lane"] per-sid records. Same 24h
# magnitude as GOAL_MARK_ORPHAN_TTL_S, deliberately well above the 1h nudge
# interval, so a live armed pane -- whose rec is re-stamped (`lts`) on every ~60s
# sweep it is visited-and-armed -- is never reaped by the SECONDARY age gate; only
# a genuinely gone session's aged, not-visited entry is.
GOAL_LANE_ORPHAN_TTL_S = 24 * 3600


# #502 -- account-limit back-off HARD cap. When the supervisor's transcript shows a
# recent account-level dispatch block (`is_account_dispatch_block`), the lane nudge
# backs off until the parsed reset time -- but ALWAYS at most this long from
# `first_seen`, so a mis-parsed far-future reset OR a no-reset cap (a monthly-spend
# / org-disable that only human action clears) can never silence the guard beyond
# one bounded window: past the cap it re-probes ONCE and re-arms, so a persistent
# cap fires at most once per this window (never the 90-min storm the ticket
# reports) yet is never permanently silent (and every sweep still logs a decision).
# ~6h: comfortably covers a full 5h session reset honoured exactly (min() below),
# short enough that a genuine multi-day weekly cap still re-probes a few times a day.
ACCOUNT_LIMIT_BACKOFF_MAX_S = 6 * 3600


def _account_limit_release_at(first_seen, resets_at):
    """Wall-clock instant the #502 account-limit back-off releases: the parsed
    reset time when it is known AND in the future, else `first_seen`+MAX; ALWAYS
    capped at `first_seen`+`ACCOUNT_LIMIT_BACKOFF_MAX_S` so a far-future or missing
    reset can never silence the lane nudge for longer than one bounded window."""
    cap = first_seen + ACCOUNT_LIMIT_BACKOFF_MAX_S
    if resets_at and resets_at > first_seen:
        return min(resets_at, cap)
    return cap


def _account_limit_decision(rec, now, err, loc, waiters):
    """#502 -- the lane nudge's account-limit back-off decision from the
    supervisor's CURRENT transcript error text `err`. Mutates `rec['alim']` (the
    caller persists `rec`); returns `(back_off, logline, notify)`:

      * NOT an account-level dispatch block (`is_account_dispatch_block` False --
        never a transient throttle) -> clears any episode, `(False, None, False)`:
        the moment genuine progress recovers, `transcript_last_error` returns '' and
        the nudge resumes automatically (structured state, #486 -- never pane text).
      * A block, still inside the bounded window -> `(True, <skip decision log>,
        notify)`: do NOT dispatch into the dead cap.
      * A block, but the bounded window ELAPSED -> re-probe ONCE, re-arm the
        episode for the NEXT window, `(False, <re-probe log>, False)`: a persistent
        cap fires at most once per ACCOUNT_LIMIT_BACKOFF_MAX_S (never the 90-min
        storm the ticket reports) yet the guard is NEVER permanently silent, and
        every sweep still journals a decision line.

    `notify` is True ONLY on the FIRST detection (episode seed) of a block that
    job 6's own session-limit ping does NOT cover -- i.e. `is_account_dispatch_block`
    is True but `is_usage_cap` is False, exactly the MONTHLY-SPEND / ORG-DISABLE
    shapes this fix newly recognizes (#502 review 🟡). Weekly/session caps
    (`is_usage_cap` True) are pinged by job 6, so the lane guard stays silent for
    them to avoid a double ping; but a no-reset spend/org block otherwise had NO
    surviving phone-ping path at all (the #134 anti-silence class), so the caller
    pings the owner once when `notify` is set.

    A background subagent dying on the account's cap surfaces as the PARENT
    session's own next `isApiErrorMessage` (decide.py's
    `parse_reset_epoch_from_error_text` docstring -- the montalu2 case), which is
    exactly what `transcript_last_error` reads, so this covers subagent deaths too."""
    if not (err and watchdog.is_account_dispatch_block(err)):
        rec.pop("alim", None)
        return False, None, False
    alim = rec.get("alim")
    notify = False
    if not isinstance(alim, dict):
        alim = {"first_seen": now,
                "resets_at": watchdog.parse_reset_epoch_from_error_text(err, now)}
        rec["alim"] = alim
        # First detection of an episode job 6 will NOT ping (not a usage cap) ->
        # the lane guard is the only surviving notifier for it.
        notify = not watchdog.is_usage_cap(err)
    release = _account_limit_release_at(alim.get("first_seen", now),
                                        alim.get("resets_at"))
    if now < release:
        ra = alim.get("resets_at")
        when = watchdog._human_clock(ra, now=now) if ra else "neznámy reset"
        return True, ("lane-occupancy %s waiters=%d -> skip:account-limit "
                      "(dispatch by teraz zomrel na strope účtu; back-off do %s, "
                      "zostáva %ds)" % (loc, waiters, when, int(release - now))), notify
    # Window elapsed -> re-probe ONCE and re-arm for the next window. (A re-arm is
    # never a new episode, so it never re-notifies.)
    alim["first_seen"] = now
    alim["resets_at"] = watchdog.parse_reset_epoch_from_error_text(err, now)
    return False, ("lane-occupancy %s -> account-limit back-off elapsed, "
                   "re-probing once (re-armed)" % loc), notify


def _account_limit_notify_owner(send_fn, pid, run, sid, cwd, dry_run,
                                first_seen, loc):
    """#502 review 🟡 -- one-shot owner ping for a MONTHLY-SPEND / ORG-DISABLE
    account block: the shapes job 6's `is_usage_cap` does NOT cover, so nothing
    else pings them, and both need HUMAN action (no auto-reset). Backing off
    silently for them would be the #134 anti-silence class, so the lane guard is
    their only surviving notifier. Deduped per episode on `first_seen`. Returns a
    decision log line for the caller to journal."""
    from notify import stream_redirect
    send_fn("⛔ **%s** — účet je zablokovaný (mesačný spend limit alebo vypnutý "
            "prístup k predplatnému) — dispatch nových workerov je pozastavený a "
            "toto NEMÁ automatický reset, treba tvoj zásah. Skontroluj prosím účet."
            % watchdog.project_label(cwd),
            owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
            dedup_key="acctblock:%s:%d" % (sid, int(first_seen)),
            dry_run=dry_run)
    return ("lane-occupancy %s -> account-block owner PINGED "
            "(no-reset cap, job-6-unhandled)" % loc)


# #970 -- resource-aware lane cap.  Implementation extracted to
# watchdog/lane_resources.py (fable-review LOW finding #3: split instead of
# raising the ratchet).  Re-import here for backward compat.
from watchdog.lane_resources import (  # noqa: E402,F401
    _LANE_RESOURCE_FILE,  # noqa: F401 -- backward compat re-export
    _LANE_NEEDS_FILE,  # noqa: F401 -- backward compat re-export
    GOAL_LANE_SATURATION_WORKERS,
    lane_resource_cap,  # noqa: F401 -- backward compat re-export
    lane_resource_caps,
)

# #442/#481/#848 -- the lane ceiling (up to 5 parallel lanes).  The constant
# GOAL_LANE_SATURATION_WORKERS is now defined in watchdog/lane_resources.py
# and imported above.  The saturation boundary is
# `floor = min(effective_cap, backlog)` in goal_lane_occupancy_nudge —
# `live_workers >= floor` logs "saturated" and SKIPS; below it, refill
# continuously.  #970: per-resource caps extend the flat cap.

# #729 -- the whole low-mem OOM subsystem (GOAL_LANE_MIN_MEM_AVAIL_MB +
# GOAL_LANE_LOWMEM_SURFACE_STREAK + _mem_available_mb + _lane_min_mem_avail_mb +
# _lane_lowmem_skip + _lane_lowmem_reset + one_glance.lane_low_mem_surface_
# decision) gated the #726-RETIRED under-saturated fill nudge and was kept DORMANT
# by #726 pending one design question: should the empty-lane "start a NEW batch"
# nudge gate on memory headroom? #729 ROZHODNUTÉ: NO. The empty-lane nudge stays
# memory-EXEMPT (a fully stalled 0-worker box has no worker RAM pressure to
# protect against and MUST always be nudged), and memory back-off WITHIN a batch
# is already delegated to the supervisor (the nudge text: "back off len na REÁLNY
# resource signál -- ... memory pressure boxu ..."). So the subsystem is DELETED,
# not re-wired -- net-LOC down (#486), zero change to the nudge's runtime behavior.

# #571 -- max CONSECUTIVE working-no-tasks defers (a ⏳ marker with 0 render task
# badges AND 0 structured live lanes) before the branch STOPS deferring and
# proceeds to the gated empty-lane nudge path. Bounds the pre-#571 unbounded
# identical skip loop (the #566 livelock class) without nudging a genuinely-idle
# ⏳ box before a few sweeps confirm it. Each sweep is ~60-70s, so 3 ~= 3-4 min.
GOAL_LANE_WNT_MAX_DEFERS = 3
# #662 -- consecutive one-glance `stuck` sweeps (armed /goal + 0 workers +
# backlog + idle over GOAL_LANE_IDLE_S) before the ONE per-episode alert record
# fires (deduped once per episode). #688: that record is now MACHINE-CHANNEL
# only (journal + `suppressed` delivery-log line) -- `stuckalert:` was owner-
# ruled spam and added to SUPPRESSED_ALERT_PREFIXES, so send() drops the Discord
# PING. By then the session has stayed dark for that whole window WITHOUT
# reviving (the bounded lane-nudge keystroke recovery ran what it could and did
# not bring it back). A fresh
# episode (any non-stuck decider verdict, or a definite goal-clear) resets the
# streak, so a transient lull never alarms. THE DEFAULT; the effective value is
# read at CALL time by `_stuck_alert_streak()` so a malformed env value can
# never crash `import watchdog.goal` (the #545/#574 read-at-call-time rule -- a
# bare module-level `int(env)` raised ValueError fleet-wide on garbage input, and
# `-2` fired on the FIRST sweep).
GOAL_LANE_STUCK_ALERT_STREAK = 8


def _stuck_alert_streak():
    """#662 -- the effective stuck-alert streak, read at CALL time with a
    malformed-value fallback (the #545/#574 read-at-call-time rule): a
    garbage `AIRULESET_GOAL_LANE_STUCK_ALERT_STREAK` never crashes import, and a
    non-positive value can never disable the transient-stuck guard (floors at
    the GOAL_LANE_STUCK_ALERT_STREAK default)."""
    try:
        v = int(os.environ.get("AIRULESET_GOAL_LANE_STUCK_ALERT_STREAK") or
                GOAL_LANE_STUCK_ALERT_STREAK)
    except (TypeError, ValueError):
        v = GOAL_LANE_STUCK_ALERT_STREAK
    return v if v >= 1 else GOAL_LANE_STUCK_ALERT_STREAK


def _lane_boundary_ok(cap):
    """#509 -- extracted from the nested `_boundary_ok` (keeps
    `goal_lane_occupancy_nudge` under its size cap). Returns (ok, kind, draft): is
    `cap` a deliverable input boundary this sweep? An AT-REST draft is deliverable
    (`deliver_with_stash` parks it -- single slot, auto-restores once the delivered
    turn completes), so it stopped being a reason to skip; at-rest-ness for a draft
    is the draft-admitting free-prompt shape (`bare_only=False`), the same
    precondition deliver_with_stash re-verifies internally before its first
    keystroke. A bare box must be settled at an idle prompt."""
    kind, draft = watchdog._classify_boundary(cap)
    if kind != "input":
        return False, kind, draft
    if draft:
        return watchdog._has_free_prompt(cap, bare_only=False), kind, draft
    return watchdog.pane_at_idle_prompt(cap), kind, draft


def _lane_skip(logs, loc, reason):
    """#475: append a lane-occupancy DECISION line for a previously-silent
    early-return path, mirroring the existing `lane-occupancy <pane> ... ->
    <decision>` format so every sweep journals WHY no nudge fired (the #442c
    every-sweep logging contract). The early skips below run before
    `live_workers`/`backlog_n` are counted, so they name the gate, not counts."""
    logs.append("lane-occupancy %s -> %s" % (loc, reason))


def _lane_wnt_gate(rec, marker, waiters, projects_dir, cwd, sid, now,
                   backlog_fetch, state, loc, dry_run, idle=None):
    """#571 -- the STRUCTURED live-lane gate + working-no-tasks decision,
    extracted so the capped ``goal_lane_occupancy_nudge`` does not grow (the
    #509/#530/#511 "never grow the capped function, extract the new branch"
    mechanic).

    Resolves the live-lane count + evidence and the backlog ONCE (both RETURNED
    for the saturation gate to REUSE -- one ``count_live_workers`` pass per
    sweep), then runs the working-no-tasks decision on the #565 EVIDENCE
    predicate (``lane_has_live_evidence`` -- any non-stale lane), NEVER the
    flapping render ``waiters`` badge. Persists the defer streak in
    ``rec['wntd']`` (rides in the existing goal_lane rec, so the #531 orphan
    reaper already covers it -- no new state namespace) -- but ONLY on a REAL
    sweep; ``dry_run`` mutates NO persisted state (#516). Returns
    ``(defer, log, live_workers, backlog_n, finished_workers, ev)``.
    #937: ``finished_workers`` counts ``state=="finished"`` lanes (recently completed,
    in integration). #619: the #611 ``escalated`` flag
    is retired -- the 15-min idle floor it bypassed is gone, so the escalate
    branch simply stops deferring (``defer=False``) and the flow reaches the
    nudge like any other empty-lane sweep."""
    live_workers, ev = watchdog.count_live_workers(
        projects_dir, cwd, sid, now, GOAL_LANE_LIVE_WINDOW_S)
    backlog_n = watchdog._cached_backlog_count(cwd, backlog_fetch, state, now)
    wnt = _one_glance.lane_working_no_tasks_decision(
        marker=marker, render_waiters=waiters,
        structured_live=watchdog.lane_has_live_evidence(ev),
        backlog=(backlog_n if isinstance(backlog_n, int) and backlog_n >= _lane_effective_min_backlog(idle) else 0),  # #611: sub-FLOOR never escalates (would only skip:min-backlog); #804 mode-4: floor is idle-aware so a >1h-idle box with 1-2 tickets IS reachable
        defer_streak=rec.get("wntd", 0), max_defers=GOAL_LANE_WNT_MAX_DEFERS)
    if not dry_run:
        rec["wntd"] = wnt.streak
    log = None
    if wnt.log:
        log = ("lane-occupancy %s waiters=%d workers=%d -> %s"
               % (loc, waiters, live_workers, wnt.log))
    # #937 -- count recently-finished IMPLEMENTATION workers (in integration)
    # from evidence. Non-worker subagents (ticket-validator, Explore, an ad-hoc
    # review consult) are excluded — they review/validate, not implement
    # tickets, so their presence is not coverage (#937-review C1).
    finished_workers = sum(1 for w in ev if w.state == "finished"
                          and w.agent_type in _LANE_WORKER_AGENT_TYPES)
    return wnt.defer, log, live_workers, backlog_n, finished_workers, ev


# #729 -- _lane_lowmem_reset + _lane_lowmem_skip (the low-mem CAPACITY-CAPPED
# surface episode + the OOM skip:low-mem handler) are DELETED with the rest of the
# memory OOM subsystem: they were reachable only from the #726-retired
# under-saturated fill nudge, and the empty-lane batch-start nudge stays
# memory-EXEMPT (see the GOAL_LANE_SATURATION_WORKERS block above). A pre-#729 rec
# carrying stale `lms`/`lmsurf` keys is harmless (never read; they ride inertly
# until the session's rec is eventually orphan-reaped, #531).


def _cached_dispatchable(cwd, dispatchable_fetch, state, now):
    """#993 item 3 — the per-cwd TTL cache over the dispatchable-candidate fetch.
    `dispatchable_fetch(cwd)` returns a ONE-element list `[{"count": N, "reason":
    r}]` (never `[]`) or None; cached via the SAME generic `_cached_member_fetch`
    the backlog/ops-wait/queue caches use. Returns the inner dict, or None
    (unmeasurable)."""
    # #993 review 2 set a 5-min TTL for BOTH outcomes because a failed read used
    # to re-run the O(deps) `--count-dispatchable` subprocess. #1067 slice 1d made
    # the fetch a read of the detached quals snapshot (its OWN 5-min TTL + failure
    # backoff throttle the derivation), so this memo is 1 min either way: a cold
    # snapshot is picked up next minute, and staleness never stacks to 10 min.
    lst = _ops_wait_recheck._cached_member_fetch(
        cwd, dispatchable_fetch, state, now, "dispatchable_cache",
        ttl=60, fail_ttl=60)
    if isinstance(lst, list) and lst and isinstance(lst[0], dict):
        return lst[0]
    return None


def _lane_dispatchable_decision(dispatchable_fetch, cwd, state, now, loc,
                                live_workers, waiters, backlog_n):
    """#993 item 3 — `(skip, logline, candidate_n)`. `dispatchable_fetch` None
    (unwired / legacy tests) → `(False, None, None)`: NO gating, the nudge fires
    as before. A wired fetch returns the dispatchable-candidate count + reason:
    count 0 → `skip:dep-wait` (deps hold everything back — the class-based
    infra-serial reason was removed in round 2b), NO keystroke; an UNMEASURABLE
    count (fetch None / malformed) → `skip:dispatchable-unknown` (safe: never
    push dispatch we cannot justify); count > 0 → `(False, None, count)` and the
    caller names `candidate_n` in the nudge text.

    #1067 slice 1d REMOVED the #1041 sweep-budget guard (`hold:budget`) that
    stood here: it existed only because a cache-miss fetch ran the blocking
    `--count-dispatchable` subprocess (up to 90 s) into the unit's 120 s kill.
    The fetch is now a read of the detached quals snapshot; its worst sweep-side
    cost is the ≤10 s systemd-run client call on a spawn, well inside budget."""
    if dispatchable_fetch is None:
        return False, None, None
    res = _cached_dispatchable(cwd, dispatchable_fetch, state, now)
    count = res.get("count") if isinstance(res, dict) else None
    if not isinstance(count, int) or isinstance(count, bool):
        # #1021: when the fetch carries a REASON (a failed dependency-meta read
        # → `meta read failed`), journal it so an inert nudge says WHY, not only
        # `unmeasurable`; a reasonless None (unwired/malformed) keeps the legacy
        # phrasing. Either way it is fail-safe — never a nudge we cannot justify.
        reason = res.get("reason") if isinstance(res, dict) else None
        detail = reason if reason else "candidate count unmeasurable"
        return True, ("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                      "skip:dispatchable-unknown (%s)"
                      % (loc, live_workers, waiters, backlog_n, detail)), None
    if count <= 0:
        reason = res.get("reason") if isinstance(res, dict) else None
        # #993 review 12: only label the KNOWN reason; a missing reason (a rare
        # cache-disagreement) is `skip:no-candidate`, never mis-attributed.
        if reason == "dep-wait":
            word = "skip:dep-wait"
        else:
            word = "skip:no-candidate"
        return True, ("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                      "%s (free slot but NO dispatchable candidate; %s)"
                      % (loc, live_workers, waiters, backlog_n, word,
                         reason or "reason-unknown")), 0
    return False, None, count


def goal_lane_occupancy_nudge(now, run, rec, sid, cwd, pid, captured, tpath,
                              tmtime, loc, send_fn, dry_run, handled,
                              projects_dir, backlog_fetch=None, state=None,
                              sleep_fn=None, batch_collect=None,
                              dispatchable_fetch=None):
    """The lane-occupancy branch (#365). Mutates `rec` (the caller
    persists it); returns `(logs, owns)` -- `owns` is the explicit
    ownership signal set from the moment `live_workers`/`backlog_n` are
    both known and neither escape fires, on EVERY return path from there
    on, so a caller never has to re-derive it from log text (#365-review
    M2)."""
    sleep_fn = sleep_fn or time.sleep
    logs = []
    if backlog_fetch is None or state is None:
        # #475 deliberately silent: a wiring/injection guard -- both are always
        # passed by the driver in production; unwired means a test or a degraded
        # call, never a real lane decision worth journalling.
        return logs, False
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception:
        authority = None
    if authority is None:
        # #618 deliberately silent: an UNRESOLVABLE authority (resolve_authority
        # raised) is a degraded/unknown box, not a lane decision. A RESOLVED
        # reduced-authority stream (branch-merge/fork-no-merge) DOES fleet parallel
        # worktree lanes under /autopilot (SKILL fleet default), so it gets the
        # nudge like full authority (was `!= "full"`, a stale full-only assumption).
        return logs, False
    # #998 -- a SEQUENTIAL-mode pane is ONE unit at a time, NO refill: the
    # lane-occupancy refill nudge NEVER fires for it (subagents/consults are
    # NOT gated -- only the refill push). Resolved by the single resolver; a
    # resolver error is treated as non-sequential (today's behaviour), logged
    # (never a silent swallow). Placed after the authority gate, before any
    # count/keystroke work.
    try:
        import cli_concurrency
        _seq_mode = cli_concurrency.resolve_mode(cwd)
    except Exception as e:  # noqa: BLE001
        _seq_mode = None
        _lane_skip(logs, loc, "concurrency-resolve-error (%r) -- treating as "
                              "non-sequential" % e)
    if _seq_mode == "sequential":
        _lane_skip(logs, loc, "skip:sequential-mode (one unit at a time, no "
                              "refill -- the sequential target caps lanes at 1)")
        return logs, False
    idle = now - (tmtime or now)
    # #442 THIRD GAP / #619 -- the old top-of-function idle gate returned HERE
    # with EMPTY logs whenever the transcript was fresh -- which a BUSY
    # under-saturated session ALWAYS is (turns spinning -> mtime fresh) -- so it
    # never reached the fill-the-cap decision and journalled nothing (gk 2
    # workers, I 32, guard silent 20+ min). #619 removed the empty-lane idle
    # floor ENTIRELY (it was structurally self-suppressing on a busy-solo box);
    # `idle` below is now logging-only. #1089 retired the keystroke DELIVERY and
    # #1096 deleted the give-up/cooldown/abort-backoff cadence gates, so the nudge
    # only journals its decision line now — the surviving cadence bound is the
    # shared per-kind `_nudge_gate.gate_ok` gate below.
    marker = watchdog.transcript_last_marker(tpath)
    if marker == "❓":
        _lane_skip(logs, loc, "skip:awaiting-user (❓ marker -- session blocked "
                              "on a question, never nudge)")
        return logs, False
    # #442 re-fix 2 (REOPEN č.2) / #848: worker PRESENCE is a COUNT decision made
    # below (`live_workers`), so it can distinguish a SATURATED box (>= floor
    # lanes -> skip) from one with ROOM (< floor lanes -> refill nudge). The old
    # early-skip on
    # `_pane_has_bg_agent(captured)` made that impossible: with ANY visible worker
    # in the agent strip it returned True and skipped the entire path BEFORE
    # `live_workers` was even counted, so the count widening alone would never fire
    # on a live box (the exact reopen-2 root cause -- 2 visible workers -> strip
    # shows `◯ ...` rows -> skip here, never reaching the count check). It is folded
    # into the count via `_count_live_subagents` + a pane-strip corroboration floor
    # below. `pane_waiting_on_user` STAYS -- it is a genuine delivery-safety gate (a
    # blocking dialog occupies the input area, so there is no free prompt to deliver
    # into); worker presence is not. The `_boundary_ok` idle-prompt gate below still
    # refuses to type into a non-idle pane (so a mid-dispatch spinning pane is
    # skipped there), and the shared per-kind cadence gate bounds re-nudging, so
    # folding worker presence into the count is safe even without the (removed,
    # #619) idle floor.
    if watchdog.pane_waiting_on_user(captured):
        _lane_skip(logs, loc, "skip:blocking-dialog (a dialog/prompt occupies "
                              "the input area -- no free prompt to deliver into)")
        return logs, False
    if watchdog._pane_compacting(captured):
        _lane_skip(logs, loc, "skip:compacting (pane is mid-/compact, transient)")
        return logs, False
    if _compact.pending_compact_hold(sid, now):   # #848 bounded #741 latch
        _lane_skip(logs, loc, "hold:compact-pending (pending /compact -- hold "
                              "the refill nudge until it delivers, bounded #848)")
        return logs, False
    if handled is not None and sid in handled:
        _lane_skip(logs, loc, "skip:already-handled (another sweep job already "
                              "delivered to this session this cycle)")
        return logs, False
    waiters = watchdog._pane_live_task_count(captured)
    _wnt_defer, _wnt_log, live_workers, backlog_n, finished_workers, _wnt_ev = _lane_wnt_gate(
        rec, marker, waiters, projects_dir, cwd, sid, now, backlog_fetch, state,
        loc, dry_run, idle=idle)   # #571 -- structured live-lane gate; counts
    #   reused below. #804 mode-4: idle threaded so the #611 escalation clamp uses
    #   the SAME idle-aware floor as the empty-lane gate (a >1h-idle box with 1-2
    #   tickets is reachable through BOTH, never blocked at working-no-tasks).
    if _wnt_log:
        logs.append(_wnt_log)
    if _wnt_defer:
        return logs, False
    # #502 -- ACCOUNT-LIMIT BACK-OFF (extracted helper, keeps this function small).
    # When the supervisor's OWN transcript shows a recent account-level dispatch
    # block, dispatching a fresh worker is a certain loss (it dies on the SAME cap
    # at Step 0). `_account_limit_decision` reads the signal FRESH from the
    # transcript, mutates rec['alim'] (persisted by the caller), and returns a
    # bounded skip / re-probe / clear decision -- see its docstring.
    back_off, alim_log, alim_notify = _account_limit_decision(
        rec, now, watchdog.transcript_last_error(tpath), loc, waiters)
    if alim_log:
        logs.append(alim_log)
    if alim_notify and send_fn is not None and not dry_run:
        logs.append(_account_limit_notify_owner(
            send_fn, pid, run, sid, cwd, dry_run,
            rec.get("alim", {}).get("first_seen", now), loc))
    if back_off:
        return logs, False

    ok, kind, draft = _lane_boundary_ok(captured)
    if not ok:
        if kind != "input" or draft:
            logs.append("skip %s (lane-occupancy) %s"
                        % (draft and "draft" or kind, loc))
        else:
            # #475: the ONE previously-silent boundary sub-case -- an input box,
            # empty (no draft), but not settled at an idle prompt this sweep
            # (busy/unsettled). Every OTHER not-ok shape (busy/no-input-line, or
            # a non-at-rest draft) already logs above; this one returned empty.
            _lane_skip(logs, loc, "skip:not-idle-prompt (input box present, no "
                                  "draft, but not settled at an idle prompt "
                                  "this sweep)")
        return logs, False
    # #518 -- the gating worker count is the STRUCTURED G2 `count_live_workers`
    # (transcripts.py), replacing the render-dependent `_count_live_subagents`
    # primary count + its `_pane_has_bg_agent(captured)` render floor. G2 EXCLUDES
    # both silent-death modes (api-error + text-toolcall-stall), so a box whose
    # "workers" are all dead reads 0 and fires the empty-lane recovery nudge --
    # the render floor's stale-strip over-count used to SUPPRESS exactly that
    # (#486-G2 dangerous direction). #1096: live_workers now feeds only the
    # saturation floor + #937 coverage (the #509/#670 cadence consumers are gone).
    # #571 -- live_workers/backlog_n resolved ABOVE (reused, one pass per sweep).
    if not isinstance(backlog_n, int) or backlog_n <= 0:
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%r -> "
                    "no measurable open backlog, skip"
                    % (loc, live_workers, waiters, backlog_n))
        return logs, False
    # #848 CONTINUOUS REFILL: ROOM to refill when live_workers < floor.
    # floor = min(effective_cap, backlog); backlog >= 1 (guard above).
    # #970 fix-forward: per-resource caps (lane_resource_caps).
    caps, cap_reason = lane_resource_caps(cwd)
    effective_cap = caps["total"]
    if cap_reason:
        logs.append("lane-resources %s INVALID %s -> default %d"
                    % (loc, cap_reason, effective_cap))
    # #1089: the per-resource USAGE read (count_resource_usage) fed only the
    # retired nudge TEXT; the saturation FLOOR below uses effective_cap directly.
    floor = min(effective_cap, backlog_n)
    if live_workers >= floor:
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d "
                    "effective_cap=%d -> saturated (>= %d lanes), skip"
                    % (loc, live_workers, waiters, backlog_n,
                       effective_cap, floor))
        return logs, False
    # #937 OCCUPANCY HONESTY: live workers alone don't cover all tickets, but
    # live + recently-finished (in integration) workers do — no room to dispatch.
    if finished_workers > 0 and live_workers + finished_workers >= backlog_n:
        logs.append("lane-occupancy %s workers=%d finished=%d backlog=%d -> "
                    "skip:covered (live+integration covers all workable)"
                    % (loc, live_workers, finished_workers, backlog_n))
        return logs, False
    # #993 item 3: a free slot with NO dispatchable candidate (dep-wait) is the
    # DAMAGE (#992) — skip, never nudge (helper extracted to keep this capped
    # function small; unwired → no gating; candidate_n names the count in the
    # text when it fires).
    disp_skip, disp_log, candidate_n = _lane_dispatchable_decision(
        dispatchable_fetch, cwd, state, now, loc, live_workers, waiters, backlog_n)
    if disp_log:
        logs.append(disp_log)
    if disp_skip:
        return logs, True
    # #848 CONTINUOUS REFILL (retiring #726/#723 batch mode): any
    # live_workers < min(5, backlog) means there is ROOM to refill, so the nudge
    # fires for BOTH an empty box (live_workers==0) AND a partially-full box
    # (0 < live_workers < floor) — refill a returned lane's slot up to 5. The old
    # `skip:batch-running` (NO refill while a batch runs) branch is REMOVED; the
    # 0<lw<floor case now falls through to the same nudge path. (The #530
    # min-backlog floor below + the shared per-kind `_nudge_gate.gate_ok` gate
    # bound the refill-nudge cadence; the #670 dedup / hourly / #929 starved caps
    # were deleted with the delivery-cadence machinery, #1096.)
    # #530 refill floor: a lone/tiny backlog is not worth a fresh lane for a
    # FRESHLY-idle box (the anti-storm gate against nudge->"nič workable"->nudge).
    # #804 mode-4: but a loop that has STOOD idle > 1h over just 1-2 workable
    # tickets is NOT freshly-idle churn -- it is a stuck loop the batch-worthiness
    # gate was parking FOREVER (#530's "1-2 held/foreign items read as workable"),
    # in direct conflict with 24/7 (#791: "loop with 1-2 workable tickets stojí
    # navždy by design"). Drop the floor to 1 once idle > GOAL_LANE_INTERVAL_S, so
    # such a box is poked; the shared per-kind cadence gate (below) bounds how
    # often the DECISION line re-journals (the keystroke DELIVERY is retired, #1089).
    min_backlog = _lane_effective_min_backlog(idle)
    if backlog_n < min_backlog:
        _lane_skip(logs, loc, "skip:min-backlog (backlog=%d < %d)"
                   % (backlog_n, min_backlog))
        return logs, False
    # #619 -- the empty-lane fill nudge is NO LONGER gated on the 15-min idle
    # floor. A continuously serially-working under-saturated session writes a
    # turn every few min, so `idle` almost never reached 15m and the nudge was
    # structurally unreachable (114x skip:idle/9h, 0 fill nudge on montalu1; the
    # #611 escalated-bypass was dead because the marker flaps ⏳<->non-⏳ and the
    # 3-consecutive-sweep streak never accumulated). #1096: the give-up / cooldown
    # / abort-backoff DELIVERY-cadence gates that used to sit here are DELETED --
    # they only governed WHEN to re-DELIVER a keystroke, and #1089 retired the
    # keystroke delivery entirely (the gates/lanefill.py Stop gate is the refill
    # lever now). What remains is the DECISION line below + the shared per-kind
    # cadence gate; keystroke safety is moot because no keystroke is typed.
    # #442: the SAME shared check job 9's virgin-arm gate uses, but through
    # the lane path's OWN short window (never the 30-min default -- see
    # GOAL_LANE_LIVE_CONVO_S above). The `window_s` seam already exists on
    # the shared function, so job 9's own default-window semantics stay
    # byte-identical and arm delivery stays exempt entirely.
    recent, reason = watchdog._goal_autoarm_recent_human_activity(
        sid, tpath, now, window_s=GOAL_LANE_LIVE_CONVO_S, pane_target=pid, run=run)
    if recent:
        logs.append("SKIP-TRANSIENT (lane-occupancy) %s -> %s -- recent "
                    "human activity, never overwrite a live conversation"
                    % (loc, reason))
        return logs, True
    # #1089 F-4 -- DELIVERY RETIRED. The lane-fill Stop gate (gates/lanefill.py)
    # is the refill lever now: it BLOCKS the turn end on an under-filled parallel
    # box WITHOUT typing into the pane, so this nudge no longer delivers a
    # keystroke. It typed 0x in 24h on gk (starved by the #1023 3h cross-kind
    # cap) -- pure journal noise and no refill. The DECISION line stays
    # (observability: the SAME workers/backlog facts the owner reads), but every
    # keystroke-delivery primitive (send_verified / deliver_with_stash /
    # submit_own_draft_verified / _try_stash_nudge) AND the batch-collect
    # contribution are removed -- the AST lock test_lanefill_enforce_1089
    # .test_kind_never_calls_a_delivery_primitive enforces it. #1089 F-4: emit it
    # HERE, ABOVE the now-vestigial keystroke cadence gate, so the retirement
    # decision is CONSISTENTLY logged (it was previously reachable only when the
    # gate OPENED -- cadence-gated -- inconsistent with the Stop-gate
    # decisions.log being the authoritative per-Stop observability). The cadence
    # gate below still runs (its hold:floor/hold:total-cap lines are the #1023
    # dead-machinery F-1 follow-up, deferred). Recorded on #1023 as the
    # kinds-rollout decision for lane-occupancy.
    logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                "would-refill; DELIVERY RETIRED (#1089 -- Stop gate enforces)"
                % (loc, live_workers, waiters, backlog_n))
    # #797/#1023 SHARED CADENCE GATE: `gate_ok` DEFERS this lane nudge (no
    # keystroke, no `_lane_record_nudge`/park/streak change) when the per-pane-
    # per-KIND floor holds (a recent lane-occupancy) OR the #1023 cross-kind TOTAL
    # cap holds (a DIFFERENT priority kind — u-freshness/partition-audit/release-
    # gap/queue-arrival — within NUDGE_TOTAL_GAP_S), killing the burst. Journal
    # token (`hold:floor`/`hold:total-cap`) via `floor_hold_reason`; the `dry_run`
    # READY below never sends, so the gate sits before it.
    # #923 BATCH MODE: gate_ok is handled once by the caller.
    if batch_collect is None:
        if not watchdog.nudges_enabled("lane-occupancy"):   # #1023 per-kind switch
            logs.append("lane-occupancy %s -> skip:kind-off (lane-occupancy)" % loc)
            return logs, True
        # #1023 (2nd-review 🔴): idle-pane only — a busy "Waiting for N background
        # agents" pane defers (`_lane_boundary_ok` returns (True,'input','') for it,
        # so without this the submit is swallowed into the running turn and the
        # /goal parks orphaned, deliver_goal:1702). Mirrors every sibling rider
        # (queue_arrival). The batch path is already covered by `_b_busy` in
        # goal_lane_sweep. Deferred WITHOUT a keystroke; retries the next idle tick.
        if _ops_wait_recheck._pane_busy_waiting(captured):
            logs.append("lane-occupancy %s -> hold:busy (waiting on background "
                        "agents — deferred to next idle tick)" % loc)
            return logs, True
        if not _nudge_gate.gate_ok(state, sid, "lane-occupancy", now):
            logs.append("lane-occupancy %s -> %s; retry next sweep"
                        % (loc, _nudge_gate.floor_hold_reason(
                            state, sid, "lane-occupancy", now)))
            return logs, True
    if dry_run:
        logs.append("READY (lane-occupancy) %s workers=%d waiters=%d "
                    "backlog=%d idle=%dm" % (loc, live_workers, waiters,
                                             backlog_n, idle // 60))
        return logs, True
    # DELIVERY RETIRED — the observability line is emitted above (before the
    # cadence gate, #1089 F-4); nothing is typed here.
    return logs, True


def _prune_goal_lane_orphans(recs, visited_sids, now,
                             ttl_s=GOAL_LANE_ORPHAN_TTL_S):
    """#531 -- age/live-gated orphan prune for `state["goal_lane"]` (the per-sid
    `recs` dict, keyed on `sid = tpath.stem`). `goal_lane_sweep` writes
    `recs[sid] = rec` for every ARMED candidate pane but never popped it, so a
    gone session's entry leaked forever (the #519/#524 per-sid-dict-leak class).

    Reap an entry ONLY when BOTH: (1) its sid was NOT a live candidate pane THIS
    sweep (`visited_sids` -- session gone / superseded), AND (2) it is malformed
    OR its stored write-time `lts` is older than `ttl_s`. The visited gate is
    PRIMARY, exactly as in `_prune_goal_mark_orphans` (#519): a live pane that
    reaches `sid = tpath.stem` this sweep -- INCLUDING an ARMED one whose nudge
    early-returns AND a temporarily not-armed one that keeps a dormant rec -- is
    added to `visited_sids` and never reaped. The two live paths that DON'T
    reach that line this sweep (the `sweep_deadline` budget `break` and the
    `pane_in_mode` `continue`, both budget-deferred / transient) fall to the
    `lts` SECONDARY safety: a goal_lane rec has no guaranteed timestamp of its
    own (an early-return nudge persists `{}`, unlike goal_mark's always-present
    `tmtime`), so the sweep stamps `lts = now` at every persist, giving a
    guaranteed age anchor -- "when the sweep last saw this rec as an armed
    candidate". Reaping additionally needs `lts >= ttl_s` (24h) stale, by which
    point any live budget-deferred pane has long been re-visited-and-stamped,
    and a wrongly-reaped entry is simply re-seeded on the next armed sweep
    (goal_lane state is not death-detection-critical; losing it only resets the
    nudge counter/cooldown). An entry with a FUTURE `lts` (clock skew) is kept
    (`< ttl_s`, the safe direction, matching #519). A reaper (run once per
    sweep), never a per-episode pop; never raises. Mirrors
    `_prune_goal_mark_orphans` / the #524 `_reap_qdisarm_state`."""
    if not isinstance(recs, dict):
        return
    for sid in [k for k, v in list(recs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        recs.pop(sid, None)


def _lane_stuck_owner_alert(now, run, rec, glance, sid, cwd, pid, loc,
                            send_fn, dry_run):
    """#662 -- route a PERSISTENT structural one-glance `stuck` verdict to a
    real OWNER ALERT (never another pane keystroke). SILENCE B of the montalu6
    9,5h outage: the lane-occupancy nudge above no longer types a keystroke
    (delivery retired, #1089) — the lane-fill Stop gate is the refill lever — so
    when the pane stays `stuck` across
    `_stuck_alert_streak()` sweeps the session has NOT revived (a dead /
    login-dialog-covered session a `continue` cannot bring back). Records ONE
    per-episode signal (dedup_key `stuckalert:`), reusing the ALREADY-cached
    `glance` (ZERO new fetch). #688 (owner ruling 2026-08-25) added `stuckalert:`
    to `SUPPRESSED_ALERT_PREFIXES`: the structural `stuck` verdict is a heuristic
    that fires on many non-human-needed states, so the send() drops the Discord
    PING and keeps only the machine-channel signal (this journal line + the
    `suppressed` delivery-log line) -- the #546/#676 audience split. `acctblock:`
    is the one escalation class that still POSTs.
    Episode state (`soa` streak, `soalert` fired-flag, `soa_ts` anchor) rides
    the goal_lane `rec` (#531-reaped -- NO new namespace). `dry_run` mutates NO
    persisted state (#516). Returns log lines only when the alert fires (the
    per-sweep `stuck` verdict is ALREADY journalled by the one-glance line, so a
    silent accumulation adds no per-sweep noise).

    LATCH DISCIPLINE (#134/#551): `soalert` is set True ONLY on a real delivery
    (`send_fn` present AND the send returned a delivered status -- which since
    #688 INCLUDES "suppressed", a machine-channel delivery: send() logged it but
    dropped the PING), so a `no-config` / `error` send RETRIES next sweep instead
    of permanently consuming the one per-episode record with no trace; a
    `send_fn is None` (test / degraded call) never latches nor claims a line.

    Accepted residuals (documented, not gaps): a session whose heartbeat file is
    ABSENT reads `warming` forever (never `stuck`) so Fix B cannot fire for a
    broken-heartbeat box (inherited one_glance behaviour); and a supervisor
    legitimately waiting >window on a long bg-bash CI with 0 lanes + backlog
    draws ONE (bounded) stuckalert -- one_glance does not consult
    `session_live_bg_bash`."""
    prev_streak = rec.get("soa", 0)
    dec = _one_glance.stuck_owner_alert_decision(
        verdict=glance.verdict, streak=prev_streak,
        max_streak=_stuck_alert_streak(),
        already_alerted=bool(rec.get("soalert")))
    if dry_run:
        return (["one-glance %s -> STUCK owner-alert DUE (dry-run, %d sweeps)"
                 % (loc, dec.streak)] if dec.alert else [])
    # anchor the episode timestamp when the streak first starts (stable key)
    if prev_streak == 0 and dec.streak == 1:
        rec["soa_ts"] = int(now)
    rec["soa"] = dec.streak
    if not dec.alert:
        rec["soalert"] = dec.alerted   # propagate reset (False) / stay-latched
        return []
    # dec.alert is True. Latch + ALERTED log ONLY on a real delivery so a failed
    # send retries next sweep; a send_fn-less/degraded call never consumes the
    # episode nor claims it alerted.
    if send_fn is None:
        return []
    anchor = int(rec.get("soa_ts", now))
    from notify import compose_stuck_owner_alert, stream_redirect
    status = send_fn(compose_stuck_owner_alert(watchdog.project_label(cwd), loc,
                                               dec.streak),
                     owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                     dedup_key="stuckalert:%s:%d" % (sid, anchor),
                     dry_run=dry_run)
    # "suppressed" is a DELIVERED decision (#688: stuckalert is now #546-owner-
    # suppressed, so send() POSTs nothing but records the machine-channel signal
    # -- the journal line here + the `suppressed` delivery-log line). It MUST
    # latch the episode exactly like "sent"/"dedup"/"dry-run", or the send()
    # re-fires every sweep and never records the episode as handled (#134/#551).
    if status in (None, "sent", "dedup", "dry-run", "suppressed"):   # delivered / already-claimed
        rec["soalert"] = True
        # #688: this is now a machine-channel record, not a Discord ping (the
        # send()-layer suppression drops the PING); the journal line is the
        # per-episode signal the owner-facing alarm used to be.
        return ["one-glance %s -> STUCK episode recorded (%d sweeps, session did "
                "not revive; machine-channel per #688) [stuckalert:%s:%d]"
                % (loc, dec.streak, sid, anchor)]
    return ["one-glance %s -> STUCK owner alert send FAILED (%s) -- will retry "
            "next sweep [stuckalert:%s:%d]" % (loc, status, sid, anchor)]


def _deliver_batch(collect, pid, sid, loc, tpath, run, state, now, handled,
                   persist, sleep_fn, logs, budget_left_fn):
    """#923 BATCH DELIVERY (moved out of `goal_lane_sweep` by #1157): compose the
    collected rider texts into ONE prompt, deliver it via `send_verified`, and
    journal the REAL outcome (#486): delivered / held / a typed attempt named by
    its `send_outcome` kind (typed-undone, typed-stranded, swallowed, ...) /
    `deferred (not typed ...)` only when no keystroke was typed."""
    _bt, _incl = _nudge_gate.compose_batch(
        [(c, t) for c, t, _ in collect], max_chars=_nudge_gate.BATCH_MAX_CHARS)
    if not _bt:
        return
    watchdog._janitor_mark_watch(state, pid, now)
    send_out = {}
    # #1023 timeout-race — SWEEP-RELATIVE confirm budget: when too little sweep
    # budget remains for send_verified's ~10s transcript confirm-wait, skip it
    # (the keystroke still lands; `delivered-unconfirmed` is an accepted state that
    # stamps the floor), so the confirm-wait never runs the sweep into the unit's
    # TimeoutStartSec=2min kill. The pre-Enter type-settle keeps its real sleep.
    _b_left = _queue_arrival._budget_left(budget_left_fn)
    _b_skip_confirm = (_b_left is not None and
                       _b_left < _queue_arrival.QUEUE_ARRIVAL_CONFIRM_MIN_BUDGET_S)
    _bok = watchdog.send_verified(
        pid, _bt, run, tpath, sleep_fn=sleep_fn, logs=logs, out=send_out,
        nudge=(_incl[0] if _incl else None), state=state,
        skip_confirm=_b_skip_confirm,
        now=now)  # #1022 wedge / #1023 budget / #1092 one-clock pane budget
    # #1157 -- the outcome word; a legacy bool stub that reports `attempted` is a
    # swallow (the pre-#1157 contract).
    _kind = getattr(_bok, "kind", None) or (
        _send_outcome.SWALLOWED if send_out.get("attempted")
        else _send_outcome.NOT_TYPED)
    if _bok or send_out.get("delivered_unconfirmed"):
        _incl_set = set(_incl)
        # #1023 🔵6: queue-arrival's baseline advances ONLY on a CONFIRMED submit —
        # on a delivered-unconfirmed batch skip its callback (baseline OLD, janitor
        # watch LEFT SET) and re-confirm later; other kinds keep terminal-on-
        # unconfirmed. The FLOOR (mark_batch_sent) stamps ALL included (🟡4).
        _qa_unconfirmed = (not _bok) and "queue-arrival" in _incl_set
        if not _qa_unconfirmed:
            watchdog._janitor_clear_watch(state, pid)
        _nudge_gate.mark_batch_sent(state, sid, _incl, now)
        if handled is not None:
            handled.add(sid)
        for _bc, _, _bfn in collect:
            if _bc not in _incl_set or _bfn is None:
                continue
            if _bc == "queue-arrival" and not _bok:
                continue   # #1023 🔵6: non-terminal on unconfirmed
            _bfn()
        # #1023 timeout-race — WRITE-THROUGH: the batch floor mark + every advanced
        # baseline are durable before a later systemd kill can un-record them.
        _queue_arrival._persist(persist, logs)
        logs.append("batch-nudge %s -> %d section(s): %s%s"
                    % (loc, len(_incl), ", ".join(_incl),
                       "" if _bok else " (delivered-unconfirmed)"))
        # #1092 (b) -- a delivered-UNCONFIRMED submit may not have cleared the box:
        # run the janitor UNDO so no stranded batch-nudge is left.
        if not _bok:
            _janitor_undo_if_own_stranded(pid, run, _bt, loc, sleep_fn, logs,
                                          state=state)
    elif send_out.get("pane_budget_held"):
        # #1092 (c) -- the per-pane budget refused this BEFORE any keystroke: never
        # stamp the floor or run the undo (the storm brake).
        logs.append("batch-nudge %s -> held (pane-budget, not typed)" % loc)
    elif _kind in _send_outcome.TYPED_NOT_DELIVERED:
        # #1092 (a)+(b) / #1157 -- a TYPED attempt that was not delivered (a swallow,
        # or a verify-failed type send_verified already undid / reported stranded)
        # IS a delivery attempt: stamp the per-kind FLOOR for ALL included kinds +
        # WRITE-THROUGH, so the SAME batch never re-types on the next ~70s sweep.
        _nudge_gate.mark_batch_sent(state, sid, _incl, now)
        _queue_arrival._persist(persist, logs)
        logs.append("batch-nudge %s -> %s (%d section(s)); floor stamped for %s"
                    % (loc, _kind, len(_incl), ", ".join(_incl)))
        # send_verified already ran the undo for a verify-failed type; a swallow /
        # an unconfirmed residue gets the janitor UNDO here.
        if _kind in (_send_outcome.SWALLOWED, _send_outcome.UNCONFIRMED):
            _janitor_undo_if_own_stranded(pid, run, _bt, loc, sleep_fn, logs,
                                          state=state)
    else:
        # #1092 (d) -- a PRE-TYPE abort (box busy / raced / spinner / kill switch
        # OFF): no keystroke was typed, so never stamp the floor; retry next sweep.
        # The reason is the `send-verified abort: ...` line just above (#1157).
        logs.append("batch-nudge %s -> deferred (not typed; reason in the "
                    "send-verified line)" % loc)


def goal_lane_sweep(now, run=None, dry_run=False, projects_dir=None,
                    state=None, handled=None, backlog_fetch=None,
                    send_fn=None, sleep_fn=None, time_fn=None,
                    sweep_deadline=None, ops_wait_fetch=None,
                    release_state_fetch=None, queue_fetch=None,
                    queue_classify=None, dispatchable_fetch=None,
                    u_fetch=None, reconcile_fetch=None,
                    deploy_state_fetch=None, infra_queue_fetch=None,
                    resolve_role_fn=None, persist=None,
                    bounce_unhandled_fetch=None):
    """The lane-occupancy driver -- the second half of job 20's new body.
    For every candidate pane whose goal is genuinely ARMED right now, runs
    `goal_lane_occupancy_nudge`. Owns its own small per-sid state namespace
    (`state['goal_lane']`), distinct from the deleted `goal_rearm`'s giant
    `rec` dict.

    `time_fn`/`sweep_deadline`: the SAME #172/#255 wall-clock self-bound
    `goal_dark_watch` carries -- this loop also walks every live candidate pane,
    bounded only by the box's pane count. Optional, default None -> unbounded;
    checked first in each pane iteration, mirroring `bounce_backstop`."""
    logs = []
    # #486 G3 -- once-per-sweep hygiene: reap heartbeat files of long-dead
    # sessions (G3 CONSUMES them, so it owns their retention). Runs BEFORE the
    # disable/unwired early returns so retention is independent of the goal lane
    # being enabled. Age-gated (7d), regular-files-only, never raises; the
    # session-status dir is env-isolated in BOTH test runners, so it never
    # touches a real developer home.
    logs += _session_status.reap_stale_status(now=now)
    if watchdog._owner_disabled("goal"):
        return logs
    if backlog_fetch is None:
        return logs
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    state = state if state is not None else {}
    # #1023 timeout-race — the SWEEP-RELATIVE remaining wall-clock, for the
    # queue-arrival rider's fetch/confirm budget guards (skip a fetch/confirm that
    # would run the sweep into the unit's `TimeoutStartSec=2min` kill). None when
    # the caller wired no `sweep_deadline` (legacy / a test) => the rider applies
    # no budget guard. Same `time_fn`/`sweep_deadline` the pane loop already uses.
    _budget_left_fn = ((lambda: sweep_deadline - time_fn())
                       if sweep_deadline is not None else None)
    recs = state.setdefault("goal_lane", {})
    # Each job-20 rider rides this SAME armed-pane loop (ZERO new pane walk) with
    # its OWN per-sid state namespace, distinct from `goal_lane`: #547 ops-wait,
    # #616 release-gap, #733 gk queue-arrival.
    wrecs = state.setdefault("ops_wait_recheck", {}) if ops_wait_fetch else {}
    rrecs = state.setdefault("release_gap", {}) if release_state_fetch else {}
    # #1029 — the queue-arrival rider serves BOTH the review union (queue_fetch)
    # and the infra queue (infra_queue_fetch), so its per-sid state exists when
    # EITHER is wired.
    qrecs = state.setdefault("queue_arrival", {}) \
        if (queue_fetch or infra_queue_fetch) else {}
    # #797 -- U-freshness reconcile: a LOCAL tickets-status cache read (ZERO gh),
    # so unlike the gh-subprocess riders it is gated only on its seam being wired
    # (`u_fetch`, default the statusbar-backed reader in run_once).
    urecs = state.setdefault("u_freshness", {}) if u_fetch is not None else {}
    # #1066 lane B -- BOUNCE-verdict rider (reduced-authority panes): a LOCAL
    # tickets-status cache read (ZERO gh, like #797), so gated only on its
    # `bounce_unhandled_fetch` seam being wired.
    brecs = (state.setdefault("bounce_verdict", {})
             if bounce_unhandled_fetch is not None else {})
    # #844 -- post-compact lane reconcile: keyed on the transcript's observed
    # compaction (ZERO gh unless a compaction is actually observed), gated only on
    # its `reconcile_fetch` seam being wired (default the git/gh reader in run_once).
    lrecs = state.setdefault("lane_reconcile", {}) if reconcile_fetch is not None else {}
    # #486 G6 -- dark_watch's tail-proof `state["goal_mark"]` marker (populated
    # BEFORE this job in the same run_once, sharing `state`) is the authoritative
    # structured armed signal. Read-only here: dark_watch owns its lifecycle.
    gmarks = state.get("goal_mark", {})
    visited_sids = set()   # #531 -- live candidate sids kept by the orphan prune below
    stuck_seen = set()     # #662 -- sids the stuck-alert decider ran on THIS sweep
    #                         (two panes sharing one cwd resolve the SAME sid, #645
    #                         -- guard the streak against a double-advance/sweep)
    # #804 -- the durable EXPECTED-ARMED roster + the set of cwds with a live
    # claude candidate pane THIS sweep. A rostered cwd absent from `visited_cwds`
    # is a DEAD-SESSION (mode 5: the session died and fell off the census); a cwd
    # confirmed ARMED refreshes its roster entry. Loaded ONCE (after the disable /
    # unwired early returns above, so a disabled box never touches it), saved once
    # at the end iff mutated.
    visited_cwds = set()
    roster_reg = _roster.load_roster()
    roster_dirty = False
    sweep_budget_broke = False   # #804-review 🟡: the budget break leaves the
    #   remaining panes' cwds OUT of visited_cwds, so the DEAD-SESSION census must
    #   NOT run this sweep (it would falsely flag every deferred LIVE stream).

    for pid, cwd, _cmd in watchdog._reconcile_candidate_panes(run):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            logs.append("lane-sweep-budget-exceeded — deferring remaining "
                        "panes to next sweep")
            sweep_budget_broke = True
            break
        # #804 -- a live claude candidate pane exists for this cwd (ARMED or not),
        # so it is NOT a mode-5 dead session. Recorded BEFORE the in-mode / no-
        # transcript continues so a momentarily-busy live pane never false-flags.
        visited_cwds.add(cwd)
        if watchdog.pane_in_mode(pid, run):
            continue
        tinfo = watchdog.find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, tmtime = tinfo
        sid = tpath.stem
        visited_sids.add(sid)   # #531 -- live this sweep -> never orphan-reaped
        captured = run(["tmux", "capture-pane", "-p", "-t", pid]) or ""
        loc = watchdog._pane_location(pid, run) or pid
        # #486 G6 -- the ARMED action gate is the STRUCTURED one-glance verdict,
        # NOT the render footer. `resolve_goal_armed` (inside evaluate) keys on
        # dark_watch's tail-proof `state["goal_mark"]` marker first (persisted
        # past the 4 MB tail the render footer AND the heartbeat's own single-
        # shot `goal_armed` scan BOTH go blind on -- the exact gk incident: a
        # day-old arm), the heartbeat only as fallback. Every candidate pane gets
        # ONE decision line (is_informative gates pure not-armed noise) -- the
        # deliberately-SILENT `skip:armed-undeterminable` render skip this
        # redesign removed. Guarded so a reader fault can never crash the sweep;
        # on a (contractually impossible) raise, skip this pane this sweep,
        # logged, never silent.
        try:
            glance, gline = _one_glance.evaluate(
                now, sid, cwd, projects_dir, state, backlog_fetch,
                gmarks.get(sid), loc,
                read_status=watchdog.read_status,
                count_live_workers=watchdog.count_live_workers,
                cached_backlog_count=watchdog._cached_backlog_count,
                idle_threshold_s=GOAL_LANE_IDLE_S,
                freshness_s=GOAL_LANE_LIVE_WINDOW_S)
        except Exception as _e:
            logs.append("one-glance %s -> error (skipping pane this sweep): %s"
                        % (loc, _e))
            continue
        if _one_glance.is_informative(glance):
            logs.append(gline)
        if glance.goal_armed is not True:
            # #662 -- a DEFINITE goal-clear (armed is False, not the transient
            # `None` can't-tell) ENDS any stuck episode, so a later re-arm starts
            # fresh and never inherits `already_alerted`=True (the Silence-B
            # recurrence class) nor a stale streak/anchor. `None` (armed-unknown)
            # deliberately does NOT reset -- a transient unreadable heartbeat
            # must not churn a genuine dark episode's streak. Residual: a
            # clear+re-arm landing entirely BETWEEN two sweeps (no intervening
            # non-stuck sweep) keeps goal_mark "set" throughout, so the episode
            # is not reset -- but the re-alert it suppresses is for a session
            # that never recovered, i.e. the SAME outage the owner already heard.
            if glance.goal_armed is False and not dry_run:
                r = recs.get(sid)
                if isinstance(r, dict):
                    for _k in ("soa", "soalert", "soa_ts"):
                        r.pop(_k, None)
                # #804-review 🔴 (reviewer B): a DEFINITE goal-clear (armed is
                # False, not the transient None) means this stream is no longer
                # EXPECTED-armed -- drop it from the roster so a deliberately
                # retired / cleared stream is NOT falsely flagged dead once its
                # session later exits (and never mis-drives a future resurrect).
                # A transient None (armed-unknown) does NOT drop (the roster entry
                # rides until a definite clear or a mode-5 death while armed).
                if _roster.drop(roster_reg, cwd):
                    roster_dirty = True
            # #1029 GATE 1 -- an INFRA-role pane is owner-present with NO /goal
            # BY DESIGN (cli_fleet role=infra, sequential), so the armed gate
            # above would forever skip it and the infra session stays blind to
            # FLOW hand-offs (the owner's „gk-infra o tom nevie"). Run ONLY the
            # queue-arrival rider (its infra path) for a non-armed INFRA-role
            # pane; every other armed-only rider stays gated (a non-armed
            # review/other pane still just `continue`s, byte-identical). The
            # role gate here keeps the review path untouched — a non-armed
            # review pane must NOT get a review nudge (that is for parked-armed
            # sessions only).
            if infra_queue_fetch is not None and resolve_role_fn is not None:
                try:
                    _pane_role = resolve_role_fn(cwd)
                except Exception:  # noqa: BLE001 — resolver fault => skip (safe)
                    _pane_role = None
                if _pane_role == "infra":
                    logs += _queue_arrival.goal_queue_arrival_recheck(
                        now, run, qrecs, sid, cwd, pid, tpath, loc, dry_run,
                        handled, queue_fetch=queue_fetch,
                        infra_queue_fetch=infra_queue_fetch,
                        resolve_role_fn=resolve_role_fn, state=state,
                        sleep_fn=sleep_fn, captured=captured,
                        persist=persist, budget_left_fn=_budget_left_fn,  # #1023 timeout-race
                        receipt_post_fn=_queue_arrival._default_hub_receipt_post(cwd))  # #1109
            continue
        # #804 -- this stream is CONFIRMED armed this sweep (the STRUCTURED
        # one-glance verdict, not a render guess): refresh its durable roster
        # entry so the DEAD-SESSION census + resurrect ladder have an accurate
        # "expected-armed" fact. armed_ts is preserved (a re-observation is not a
        # re-arm); sid/authority/last_seen refresh (a resurrected session's id
        # changes). resolve_authority fails toward "full" (#478 direction).
        import airuleset as _al
        try:
            _authority = _al.resolve_authority(cwd)
        except Exception:
            _authority = "full"
        _roster.upsert(roster_reg, cwd, sid, _authority or "full", now)
        roster_dirty = True
        rec = recs.get(sid)
        if not isinstance(rec, dict):
            rec = {}
        # #923 BATCHING: check batch_eligible ONCE for this sid. If eligible,
        # each rider contributes text to batch_collect instead of delivering
        # individually. Eligible categories from goal_dark_watch (goal-guard)
        # are already in state["nudge_batch"][sid].
        _eligible = _nudge_gate.batch_eligible(state, sid, now)
        # #1023-review BLOCKER-1: the batch composes MULTIPLE kinds into ONE
        # keystroke on a SINGLE identity, so the primitive cannot per-kind-gate a
        # mixed batch. Filter to ENABLED kinds HERE — a disabled kind never enters
        # the batch (falls to its individual path, suppressed there): no leak, no
        # drop. This IS the per-kind staging for the batch path.
        _eligible = [c for c in _eligible if watchdog.nudges_enabled(c)]
        _batch_collect = None
        if _eligible and (handled is None or sid not in handled) and not dry_run:
            # R2 (#923 review): common delivery guards checked ONCE.
            from watchdog import compact as _compact_mod
            from watchdog import ops_wait_recheck as _owr_mod
            _b_compact = _compact_mod.pending_compact_hold(sid, now)
            # #1023: idle-pane only — a busy Waiting pane defers (aged override gone)
            _b_busy = _owr_mod._pane_busy_waiting(captured)
            if not _b_compact and not _b_busy:
                # Pick up any goal-guard contribution from dark_watch.
                _nb = state.get("nudge_batch", {}).get(sid, [])
                _batch_collect = [entry for entry in _nb
                                  if entry[0] in _eligible] if _nb else []
        llogs, _owns = goal_lane_occupancy_nudge(
            now, run, rec, sid, cwd, pid, captured, tpath, tmtime, loc,
            send_fn, dry_run, handled, projects_dir,
            backlog_fetch=backlog_fetch, state=state, sleep_fn=sleep_fn,
            dispatchable_fetch=dispatchable_fetch,   # #993 item 3
            batch_collect=(_batch_collect if _batch_collect is not None
                           and "lane-occupancy" in _eligible else None))
        rec["lts"] = now   # #531 -- write-time age anchor for the orphan reaper
        recs[sid] = rec
        logs += llogs
        if handled is not None and any(ln.startswith("lane-occupancy nudge")
                                       for ln in llogs):
            handled.add(sid)
        # #662 -- route a PERSISTENT structural `stuck` verdict to an owner
        # ALERT (SILENCE B of the montalu6 9,5h outage), using the ALREADY-cached
        # `glance` (no fetch). A Discord SEND not a keystroke, so it never
        # conflicts with a `handled` keystroke and is authority-agnostic; `rec`'s
        # `soa` streak persists via `recs`. `stuck_seen` guards the #645
        # two-panes-one-cwd double-advance (same sid twice per sweep).
        if sid not in stuck_seen:
            stuck_seen.add(sid)
            logs += _lane_stuck_owner_alert(now, run, rec, glance, sid, cwd, pid,
                                            loc, send_fn, dry_run)
            # #1109 — the owner-scoped exception: a DECLARED gk role pane
            # (review/infra/quality) stuck >= 20 min at the 5 h session limit
            # gets ONE owner notice per episode (the 22.9 gk-infra 2 h silence).
            # Reuses the ALREADY-cached one-glance `glance` + `captured` (ZERO new
            # fetch); episode state rides the same goal_lane `rec`.
            logs += _gk_stall_notice.gk_stall_notice(
                now, rec, glance, captured, cwd, sid, pid, loc, send_fn, dry_run,
                run=run)
        # #547 W→I + #552 I→W/U -- partition-audit re-check for this armed pane.
        if ops_wait_fetch is not None:
            logs += _ops_wait_recheck.goal_ops_wait_recheck(
                now, run, wrecs, sid, cwd, pid, tpath, loc, dry_run, handled,
                ops_wait_fetch=ops_wait_fetch, state=state, sleep_fn=sleep_fn,
                i_count=glance.backlog, captured=captured,
                release_state_fetch=release_state_fetch,
                batch_collect=(_batch_collect if _batch_collect is not None
                               and "partition-audit" in _eligible else None),
                deploy_state_fetch=deploy_state_fetch,
                budget_left_fn=_budget_left_fn)   # #1041 sweep-budget guard
        # #616 -- release-gap re-check for this armed pane.
        if release_state_fetch is not None:
            logs += _release_gap.goal_release_gap_recheck(
                now, run, rrecs, sid, cwd, pid, tpath, loc, dry_run, handled,
                release_state_fetch=release_state_fetch, state=state,
                sleep_fn=sleep_fn, captured=captured,
                batch_collect=(_batch_collect if _batch_collect is not None
                               and "release-gap" in _eligible else None))
        # #733 -- gk queue-ARRIVAL watcher for this armed pane (#1029: also an
        # armed INFRA pane, when only infra_queue_fetch is wired).
        if queue_fetch is not None or infra_queue_fetch is not None:
            logs += _queue_arrival.goal_queue_arrival_recheck(
                now, run, qrecs, sid, cwd, pid, tpath, loc, dry_run, handled,
                queue_fetch=queue_fetch, state=state, sleep_fn=sleep_fn,
                captured=captured, classify_builder=queue_classify,   # #993 item 4
                infra_queue_fetch=infra_queue_fetch,   # #1029 role-aware
                resolve_role_fn=resolve_role_fn,       # #1029 role-aware
                persist=persist, budget_left_fn=_budget_left_fn,   # #1023 timeout-race
                receipt_post_fn=_queue_arrival._default_hub_receipt_post(cwd),  # #1109
                batch_collect=(_batch_collect if _batch_collect is not None
                               and "queue-arrival" in _eligible else None))
        # #797 -- U-freshness reconcile for this armed pane.
        if u_fetch is not None:
            logs += _u_freshness.goal_u_freshness_recheck(
                now, run, urecs, sid, cwd, pid, tpath, loc, dry_run, handled,
                u_fetch=u_fetch, state=state, sleep_fn=sleep_fn,
                captured=captured,
                batch_collect=(_batch_collect if _batch_collect is not None
                               and "u-freshness" in _eligible else None))
        # #844 -- post-compact lane reconcile for this armed pane.
        if reconcile_fetch is not None:
            logs += _lane_reconcile.goal_lane_reconcile_recheck(
                now, run, lrecs, sid, cwd, pid, tpath, loc, dry_run, handled,
                reconcile_fetch=reconcile_fetch, state=state, sleep_fn=sleep_fn,
                captured=captured, budget_left_fn=_budget_left_fn,   # #1041
                batch_collect=(_batch_collect if _batch_collect is not None
                               and "lane-reconcile" in _eligible else None))
        # #923 BATCH DELIVERY: compose + deliver all collected texts as ONE prompt.
        if _batch_collect and not dry_run:
            _deliver_batch(_batch_collect, pid, sid, loc, tpath, run, state, now,
                           handled, persist, sleep_fn, logs, _budget_left_fn)
        # #1066 lane B -- BOUNCE-verdict rider for this armed REDUCED-authority
        # pane. Runs AFTER the batch delivery + every #733 rider so the shared
        # per-sweep `handled` set (at most ONE keystroke per pane per sweep) is
        # honoured: a pane any earlier rider/batch already typed this sweep is
        # deferred (base kept OLD, re-detects next sweep). Direct-send (not
        # batched); its own authority gate skips FULL-authority panes, so it
        # never collides with the full-only queue-arrival rider.
        if bounce_unhandled_fetch is not None:
            logs += _bounce_verdict.goal_bounce_verdict_recheck(
                now, run, brecs, sid, cwd, pid, tpath, loc, dry_run, handled,
                bounce_unhandled_fetch=bounce_unhandled_fetch, state=state,
                sleep_fn=sleep_fn, captured=captured,
                persist=persist, budget_left_fn=_budget_left_fn)
        # Clear the dark_watch batch entry for this sid (consumed or empty).
        state.get("nudge_batch", {}).pop(sid, None)
    # #804 -- DEAD-SESSION census: a rostered EXPECTED-armed stream with NO live
    # claude candidate pane this sweep is a mode-5 death (the session died and
    # fell off the radar). Surface ONE verdict line per dead stream, cadenced at
    # GOAL_ROSTER_CENSUS_S so a persistently-dead stream never floods the journal
    # (#766 latch). A cwd that is live again clears its own latch so a FUTURE
    # death re-surfaces. Emitted even in dry_run (a read-only log line); the
    # roster is only PERSISTED when not dry_run. #804-review 🟡: SKIPPED entirely
    # when the sweep budget cut the pane loop short (visited_cwds is incomplete,
    # so every DEFERRED live stream would be falsely flagged dead).
    for _dcwd, _dentry in ([] if sweep_budget_broke
                           else _roster.dead_entries(roster_reg, visited_cwds)):
        _dloc = watchdog.project_label(_dcwd)
        # #804 mode-5 -- evaluate + (opt-in) fire a RESURRECT for this dead entry
        # (own RESURRECT_CADENCE_S, independent of the hourly census line);
        # extracted to a helper to keep goal_lane_sweep under its function cap.
        _rlogs, _rdirty = _resurrect_dead_entry(
            _dcwd, _dentry, _dloc, now, run, projects_dir, dry_run)
        logs.extend(_rlogs)
        if _rdirty:
            roster_dirty = True
        _last = _dentry.get("census_ts")
        if isinstance(_last, (int, float)) and (now - _last) < GOAL_ROSTER_CENSUS_S:
            continue
        _dentry["census_ts"] = now
        roster_dirty = True
        logs.append(
            "one-glance %s -> dead-session (expected armed, no live session; "
            "sid=%s authority=%s last armed %s ago)"
            % (_dloc, _dentry.get("sid", "?"),
               _dentry.get("authority", "?"),
               _roster_age_desc(now, _dentry.get("armed_ts"))))
    for _lcwd in visited_cwds:
        _e = roster_reg.get(_lcwd)
        # A cwd that is live again clears its census-flood latch AND its resurrect
        # state (rgts/rfails/ratt) so a FUTURE death re-surfaces + re-resurrects
        # from a clean slate (a resurrect that brought it back must never leave a
        # stale fail count behind -- #805 escalation resets on success).
        if isinstance(_e, dict) and any(
                k in _e for k in ("census_ts", "rgts", "rfails", "ratt")):
            for _k in ("census_ts", "rgts", "rfails", "ratt"):
                _e.pop(_k, None)
            roster_dirty = True
    if roster_dirty and not dry_run and not _roster.save_roster(roster_reg):
        # A persistently-failing save loses the rgts/census_ts anchors each
        # reload -> the 30-min resurrect cadence + hourly census would degrade to
        # every sweep; surface it (machine-channel) instead of silently churning.
        logs.append("roster save FAILED (unwritable ~/.claude?) -- resurrect/"
                    "census cadence not persisted this sweep")
    if not dry_run:   # #531 -- prune each rider namespace for gone+aged sessions
        _prune_goal_lane_orphans(recs, visited_sids, now)
        _ops_wait_recheck._prune_ops_wait_orphans(wrecs, visited_sids, now)   # #547
        _release_gap._prune_release_gap_orphans(rrecs, visited_sids, now)     # #616
        # #1023: the #921 `busy_first_seen` prune is gone with the aged override —
        # no rider writes that state any more.
        _queue_arrival._prune_queue_arrival_orphans(qrecs, visited_sids, now)  # #733
        if bounce_unhandled_fetch is not None:
            _bounce_verdict._prune_bounce_verdict_orphans(brecs, visited_sids, now)  # #1066
        if u_fetch is not None:
            _u_freshness._prune_u_freshness_orphans(urecs, visited_sids, now)  # #797
        if reconcile_fetch is not None:
            _lane_reconcile._prune_lane_reconcile_orphans(lrecs, visited_sids, now)  # #844
        _nudge_gate.prune(state, visited_sids, now)   # #797 shared cadence gate
    return logs
