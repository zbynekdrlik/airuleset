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
             It is the ONLY origin (#1324, owner 14.11.2026: "no goal
             visible" = autopilot off; the watchdog never arms a goal on its
             own). Every automatic origin the watchdog once wrote is RETIRED
             (`_GOAL_RETIRED_ORIGINS`): leftovers are purged, never typed.

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
             structurally-always-refuses bug, not a safety net (#752: nor
             is the owner's keyboard presence a reason to defer it).

  RE-ARM  -- only a genuine `/autopilot` invocation (the SAME `goal-arm
             --self` callback, called fresh). #478-#1143 let the watchdog
             re-arm a dark / auth-cleared / fulfilled / answered / stream
             loop; #1324 (owner 14.11.2026) RETIRED all of them -- a goal the
             owner switched off was re-typed during a production event.
             `goal_dark_watch()` now only JOURNALS a lost goal (an auth clear,
             or a dark footer CONFIRMED by the #524 run) once per episode, plus
             the keystroke-free (notify-suppressed) #459 report.

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
             `_janitor_clear_box`).

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

import copy
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
from watchdog import queue_arrival_own as _queue_own  # #1178 (ended supervisor pane)
from watchdog import bounce_verdict_recheck as _bounce_verdict  # #1066 (bounce)
from watchdog import u_freshness as _u_freshness             # #797 (U reconcile)
from watchdog import nudge_gate as _nudge_gate               # #797 (cadence gate)
from watchdog import roster as _roster                       # #804 (armed roster)
from watchdog import goal_turn_liveness as _turn_liveness     # #1110 (transcript liveness)
from watchdog import goal_arm_confirm as _arm_confirm         # #1265 (transcript arm-confirm)
from watchdog.goal_arm_confirm import transcript_arm_landed as _transcript_arm_landed, scan_with_confirm_offset as _scan_with_confirm_offset  # noqa: E501
from watchdog import gk_stall_notice as _gk_stall_notice      # #1109 (gk role-pane stall notice)
from watchdog import send_outcome as _send_outcome            # #1157 (delivery outcome)
from watchdog import goal_arm_failure as _arm_failure         # #1181 (visible failed arm)
from watchdog import watch_triggers as _wt                    # #1163 (steer=watch windows)
from watchdog import owner_stop as _owner_stop                # #1257 (owner-stop latch)
from watchdog import collapsed_box as _cbox                  # #1381 (collapsed input box)
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


# The ONE writer of the goal-request store: the owner's own `goal-arm --self`
# callback (origin "self-callback", produced ONLY by the CLI after a real
# `/autopilot`). #1324 (owner 14.11.2026): "no goal visible" = autopilot off --
# the watchdog NEVER arms or re-arms a `/goal` on its own.
_GOAL_SELF_CALLBACK_ORIGIN = "self-callback"
# Origins no longer PRODUCED. A leftover on-disk request of one is purged at the
# top of `goal_sweep`, dropped terminally by `deliver_goal` (`drop:<origin>-
# retired`) and never read as live by `request_is_live`; `record_goal_request`
# refuses to write one. stale-rearm #623 (retired #1113: it typed a REPLACE into
# an ALIVE loop), declared-virgin #1038 (retired #1260: it STARTED a goal), and
# #1324: dark-rearm #478, auth-rearm #675, fulfilled-rearm #764, answer-rearm
# #890, stream-migrate #1128/#1143 (a goal the owner switched off was re-typed,
# iemmixer/dasio, during a production event).
_GOAL_RETIRED_ORIGINS = ("stale-rearm", "declared-virgin", "dark-rearm",
                         "auth-rearm", "fulfilled-rearm", "answer-rearm",
                         "stream-migrate")
# #1324 -- the watchdog-state stores of the retired re-arm machinery (rate limits,
# the 🏁 proof cache, the stream-migrate gap); popped by `goal_dark_watch`.
_GOAL_RETIRED_STATE_KEYS = ("goal_dark_rearm_attempts", "goal_fulfilled_rearm",
                            "goal_auth_rearm_attempts",
                            "goal_answer_rearm_attempts", "goal_fulfilled_proof",
                            "goal_stream_migrate", "goal_stream_answered_memo",
                            "auth_rearm_hold")


def _not_owner_origin(origin):
    """#1324 -- a NAMED origin that is not the owner's (retired or unknown): only
    ever dropped. An empty origin is the legacy owner form."""
    return bool(origin) and origin not in _GOAL_USER_CALLBACK_ORIGINS


def request_is_live(req):
    """#1260/#1324 -- True for a pending request delivery will act on. A
    non-owner leftover is only ever dropped, so `status` never reads it as
    "arming" (on a box with the goal kill-switch, `run_once` never runs the
    sweep that purges it)."""
    return isinstance(req, dict) and bool(req) \
        and not _not_owner_origin(req.get("origin"))


# #757 -- the genuine USER-callback origins: a request written because a HUMAN
# typed `/autopilot`. `record_goal_request` refreshes the #400 age-cap anchor ONLY
# for these (a human action is needed each time, so it can never drive the #400
# refresh-forever shape); an unknown/empty origin PRESERVES the anchor
# (fail-safe ALLOWLIST).
_GOAL_USER_CALLBACK_ORIGINS = (_GOAL_SELF_CALLBACK_ORIGIN,)
# #675 -- bumped whenever the marker PARSER gains a new recognizer. A persisted
# `state["goal_mark"]` entry stamped with an OLDER version is RESEEDED (first-
# sight reverse-scan) on the next dark_watch sweep, so a marker the old parser
# skipped-past (its incremental offset already advanced beyond it) is re-read by
# the new recognizer instead of the stale value persisting forever (#618 class).
_GOAL_MARK_PARSER_VERSION = 3   # 3: #1309 goal_status end record


def record_goal_request(session, cwd, text, authority, now=None, path=None,
                        origin=None, moff=None):
    """Record a pending `/goal` arm request for `session`. The one writer is the
    owner's `goal-arm --self` callback (origin "self-callback", #478). Any other
    NAMED origin -- a retired one or an unknown one (#1324: the watchdog never arms
    a goal on its own) -- is REFUSED (False, nothing written): an ALLOWLIST, so no
    future caller can add an automatic arm. An empty origin is the legacy form.
    Overwrites any earlier pending request for the SAME session.

    `ts` is the #400 age-cap anchor: set ONCE on create and preserved on every
    re-record EXCEPT a genuine USER-callback one (`new_origin in
    _GOAL_USER_CALLBACK_ORIGINS`, #757): the owner re-running `/autopilot` is a
    genuinely new request and gets a FRESH ts, so its own 30-min window is never
    judged against a stale earlier anchor. It cannot drive the #400
    refresh-forever shape because a user callback needs a HUMAN action each
    time. This mirrors `_bump_goal_delivery_fail`, which RESETS `dl_fails` on the
    same re-record. An ALLOWLIST: an unknown/empty origin preserves the anchor.

    `cwd`/`origin`/`authority`/`text` otherwise take the newest call's
    values. Fail-safe (never raises). Returns True on success."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    new_origin = str(origin or "").strip()
    if _not_owner_origin(new_origin):
        _log_goal_sync("REFUSE record origin=%s sid=%s cwd=%s (#1324: only the "
                       "owner arms a /goal)" % (new_origin, session, cwd))
        return False
    d = load_goal_requests(path)
    prior = d.get(session)

    # #757 -- a genuine USER-callback re-record gets a FRESH ts anchor; every
    # unknown/empty origin preserves the #400 anti-refresh-forever anchor.
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
        **({"moff": moff} if moff is not None else {}),   # #1265 record-time transcript offset
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
    cap. There is no mechanical re-record (#1324 retired every automatic arm),
    so only the owner's own re-run resets it; carrying dl_fails across a
    self-callback re-record would prematurely cap a genuinely fresh user arm,
    so it is deliberately NOT carried forward. #757 aligned the
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
    `logs` list (`_default_rearm_fn` does) and an oversize template appends
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


def goal_template_for(authority, cwd, role=None, path=None, logs=None):
    """#998 — the `/goal` line for `authority` in the pane's resolved role.
    When `role` is None it is resolved from `cwd` via
    `cli_concurrency.resolve_role` (the SINGLE resolver every consumer reads).
    No role delegates to `goal_template_for_authority` (fresh SKILL.md read,
    drift-locked == the renderer's no-role line); a ROLE variant is composed
    via the SAME `goal_registry.render_goal_line` the SKILL.md lines are
    generated from. None on any failure OR over the cap (never a
    wrong-authority / oversize arm), logged LOUD like the sibling."""
    authority = str(authority or "").strip()
    if not authority:
        return None
    if _wt.watch_window_for(cwd) is not None:   # #1163: steer=watch arms NO /goal
        if isinstance(logs, list):
            logs.append("goal-template none: %s is a watch-steered window (#1163)"
                        % cwd)
        return None
    if role is None:
        try:
            import cli_concurrency
            role = cli_concurrency.resolve_role(cwd)
        except Exception as e:  # noqa: BLE001
            if isinstance(logs, list):
                logs.append("goal-template role-resolve-error (%r) — falling "
                            "back to the no-role line" % e)
            role = None
    if not role:
        return goal_template_for_authority(authority, path=path, logs=logs)
    try:
        import goal_registry
        line = goal_registry.render_goal_line(authority, role)
    except Exception as e:  # noqa: BLE001
        if isinstance(logs, list):
            logs.append("goal-template render_goal_line(%s,%s) failed: %r"
                        % (authority, role, e))
        return None
    if len(line) > GOAL_ARM_CHAR_CAP:
        if isinstance(logs, list):
            logs.append("goal-template REFUSED oversize authority=%s role=%s "
                        "len=%d cap=%d (never typed)"
                        % (authority, role, len(line), GOAL_ARM_CHAR_CAP))
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
# escalation) or any zero-keystroke defer (`undeterminable`/`busy`/
# `busy-transcript`/`in-mode`/... -- counting those would starve a
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
    refused. #1381: a collapsed box showing our own tail still holds `text`."""
    for i in range(GOAL_TYPE_SETTLE_POLLS):
        landed = _cbox.holds(watchdog.capture_pane(pid, run, lines=40), text)
        if landed is want:
            return landed
        if i < GOAL_TYPE_SETTLE_POLLS - 1:
            sleep_fn(GOAL_TYPE_SETTLE_S)
    return not want


def _await_goal_armed(pid, run, sleep_fn, tpath=None, mark_off=None):
    """#720 -- bounded render-settle poll (like `_await_typed`) until the arm is
    CONFIRMED; False when the window elapses. Box-cleared / transcript-advanced is
    NOT proof: a submit CC read as a plain prompt clears the box and appends a
    `user` turn but never arms (the #720 silent-'sent' tail). On False the request
    stays pending. Shared by all three arm routes (bare / stash / stranded); #1113:
    no origin types past an armed footer, so a True read is our fresh arm.
    #1265 -- STRUCTURED STATE FIRST (#486): with `tpath` + `mark_off` (the
    pre-keystroke marker-scan offset) each poll asks the transcript first (a
    `Goal set:` written after the keystroke, `watchdog.goal_arm_confirm`), then the
    `◎ /goal` footer -- CC 2.1.311 covers the glyph with the arm's own turn
    (dev1 camera-box: 6 landed arms retyped). Without them: pane-only."""
    for i in range(GOAL_TYPE_SETTLE_POLLS):
        if _transcript_arm_landed(tpath, mark_off, log=_log_goal_sync):
            return True
        if watchdog.pane_goal_armed(watchdog.capture_pane(pid, run, lines=40)) is True:
            return True
        if i < GOAL_TYPE_SETTLE_POLLS - 1:
            sleep_fn(GOAL_TYPE_SETTLE_S)
    return False


def _send_goal_verified(pid, text, run, captured=None, sleep_fn=None, logs=None,
                        verify_armed=True, nudge="goal-sweep", out=None, tpath=None, mark_off=None):
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
    # #1104/#1381 -- `out["typed"]` (set by the primitive the moment a key reached
    # the box, verified or not) lets a caller book the per-pane budget on a real
    # send and record a first-person own-text leftover; no flag = NOTHING typed.
    if not watchdog._type_literal_verified(pid, run, text, sleep_fn,
                                           kind="goal", nudge=nudge, logs=logs, out=out):
        _log("goal-verify-abort: type-not-verified")
        return False                       # not byte-exact -- never submit it
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
    if _await_goal_armed(pid, run, sleep_fn, tpath, mark_off):   # #1265 structured first
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
                              now, sleep_fn, logs, nudge="goal-sweep", owner_arm=False,
                              out=None):
    """#566 case (a) -- when the input box ALREADY holds our OWN swallowed
    COMPLETE `/goal <text>` (a prior attempt typed it but the Enter was
    swallowed/raced), COMPLETE the submit in place, never via `deliver_with_stash`
    (it would park our own /goal into the single slot forever). True on a
    confirmed submit, else False (the caller's stash path; never a collapsed box).

    Three gates, ALL required before the one Enter keystroke: (1) janitor
    PROVENANCE (the mark `deliver_goal` just set, or a durable park); (2) the
    recent-human gate (never keystroke a human-active pane); (3) EXACT-payload
    completeness -- head row a leading and tail row a trailing substring of `text`
    (a truncated type fails the tail; a foreign draft matches neither end). `submit_own_goal_verified` re-verifies completeness against a
    FRESH capture right before the keystroke. #1381: (3) for a COLLAPSED box (no
    `❯` row, head hidden) = this pane's first-person record of typing exactly
    `text` (`_cbox.note_typed`, head-checkpointed) AND its rows row-exact the end
    of `text` with the `↑ N` count `text` wraps to; (2) with a record = NO human
    attach or key in the session since it (`_cbox.input_since`: no human can have
    edited the hidden head; the rows hidden between the checkpoint and the view
    stay unproven, the #746 residual). Without a record the owner's own arm
    (`owner_arm`) keeps only the 5-min client-input signal: the 30-min
    transcript/presence window is the request's whole life, which his own
    `/autopilot` prompt always filled (#752). `out["entered"]`: an Enter was sent."""
    if not _janitor_provenance(state, pid, now):
        return False
    head = watchdog._input_box_head_text(captured)    # cheap pre-check first
    tail = watchdog._input_line_text(captured)
    since = _cbox.typed_at(state, pid, text, now)        # #1381: our own type's record
    if not ((head and head.startswith("/goal ") and text.startswith(head)
             and tail and text.endswith(tail)) or (since is not None and _cbox.own_tail(
                 captured, text, cols=_send_outcome.pane_width(pid, run) or 0))):
        return False
    if (_cbox.input_since(pid, run, since) if since is not None
            else watchdog._tmux_client_recent_input(pid, run, now)[0]) or (
            not owner_arm and _recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run)):
        _log_goal_sync("SKIP recover-swallowed recent-human sid=%s cwd=%s"
                       % (sid, cwd))
        return False
    return watchdog.submit_own_goal_verified(pid, text, run=run, sleep_fn=sleep_fn,
                                             logs=logs, nudge=nudge, out=out)  # #1038


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
    and must be a prefix of the template with all whitespace removed (#1303:
    a hard-broken token), >= `GOAL_STRANDED_MIN_MATCH` non-space chars and NOT
    the complete text (a complete own draft is `_submit_stranded_own_goal`'s
    job). A user would have to paste the exact first 200+ chars of the ~3.8k
    template with zero edits -- and the recent-human gate still VETOES then.

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
    # #737 -- the head-first whole-box reconstruction (single-sourced in
    # `_box_norm_from_capture`, never a second inline copy).
    box_norm = watchdog._box_norm_from_capture(captured)
    if not box_norm:
        return [], False
    # #1303 -- no whitespace: a row join inside a CC hard-broken token adds a
    # space; a non-space prefix is still byte-exact, so an edited paste fails.
    box_ns = "".join(box_norm.split())
    text_ns = "".join(text.split())
    if not (len(box_ns) >= GOAL_STRANDED_MIN_MATCH
            and len(box_ns) < len(text_ns)
            and text_ns.startswith(box_ns)):
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


def _recovery_pane_ready(sid, cwd, run, projects_dir, now, pid=None, human_gate=True):
    """#731 -- the SHARED guard core extracted from `_resolve_stash_abort_livelock`
    (#566) so the attempt-cap drop cleanup reuses the IDENTICAL pre-keystroke
    guards, never a parallel set. Resolves the pane (unless `pid` given) and, in
    the SAME order the livelock resolver already used, checks: copy-mode, the
    fail-closed recent-human gate (which now ALSO carries the #731 tmux-client-
    input signal 3), an open dialog. Returns `(pid, captured, loc)` when the pane
    is safe to act on, or `(None, <reason-str>, None)` when a guard vetoes
    (`reason` in {no-pane,in-mode,recent-human,dialog-open}; `human_gate=False`
    skips recent-human -- the #1181 owner-arm notice, #752). A CLEAN idle input
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
    if human_gate and _recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run):
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
                  OR the shared `_box_is_own_leftover` proof: a >= 80-char
                  contiguous run of the request's OWN frozen `text`, or a
                  whitespace-stripped run of a shipped template variant (#1303).
      "other"  -- non-empty box content that is none of the above: a FOREIGN
                  draft, left completely untouched.
    Ownership is UNION-of-proofs so BOTH render shapes are recognised (the literal
    `/goal ...` a short text renders AND the placeholder a large one collapses to);
    the frozen-text match is the proof against a human paste. #1381: a COLLAPSED
    box (`↑ N more`, no `❯` row) is ours/other by its rows, never "empty"."""
    occupied = watchdog.STASH_MARKER in (captured or "")
    head = watchdog._input_box_head_text(captured)
    if not head and not occupied:
        return _cbox.box_kind(captured, text) or "empty"
    if occupied or watchdog._looks_like_own_stuck_content(head):
        return "ours"
    # #737/#1303 -- the ONE shared proof (arm-confirm cleanup + janitor use it
    # too); its template branch survives a CC hard-broken token (a pane under
    # ~67 cols, the 13.11. gk `box=other`). A short/foreign draft never matches.
    if watchdog._box_is_own_leftover(captured, text,
                                     watchdog.GOAL_ARM_LEFTOVER_MIN_SUBSTR,
                                     match_templates=True):
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


def _goal_cap_drop(sid, cwd, text, dl_fails, request_ts, run,
                   projects_dir, state, now, send_fn, dry_run, sleep_fn):
    """#731 -- the terminal attempt-cap DROP action: clean up any leftover /goal
    text this request stranded in the pane, then ping the owner (like the
    30-min expiry ping). Returns `(logs, leftover, loc)` where
    `leftover` in {cleared,not-ours,escalated,none,skipped} for the journal and
    `loc` is the family pane loc (falls back to the cwd label).

    Cleanup REUSES the shared owned-recovery machinery, never a parallel set: the
    same guard core (`_recovery_pane_ready`) + a CLEAN idle input-boundary check
    (#731-review: never Escape/clear a live turn's render) + the same
    `_janitor_recover` driver the #566 livelock resolver uses. It clears ONLY our
    OWN leftover (proven by `_goal_box_kind` -- own-shape / #35 stash slot /
    `_box_is_own_leftover`); a FOREIGN draft is left completely untouched. The
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
    # The owner's arm failed -> ping (#1324: the only arm left is the owner's,
    # so no origin is silent). Deduped on sid + the REQUEST TS (#731-review):
    # dl_fails is always exactly the cap at drop time, so keying on it would
    # silence a later capped episode for the SAME sid for the full 14-day dedup
    # TTL (the #134 silence class); request_ts gives each episode its own ping.
    if send_fn is not None and not dry_run:
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
    boundary, _bd = _cbox.boundary(cap, text)    # #1381: own collapsed box = input
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
    if _cbox.rows(cap) is not None:   # #1381: no clear key can read a collapsed box;
        _log_goal_sync("ARM-CONFIRM-FAIL sid=%s cwd=%s cleanup=%s (collapsed box)" % (
            sid, cwd, "kept (own tail: the stranded submit may take it)"
            if _cbox.own_tail(cap, text) else "declined"))
        return
    # #737 A -- DELIVERY-TIME self-cleanup: a verify-failed arm can strand our OWN
    # /goal (a swallowed submit whose undo did not converge); clean it NOW so it
    # never jams the single stash slot as an unrecognisable "foreign draft".
    # First-person provenance (the delivery proved the box BARE seconds ago, then
    # typed our text): own = a >= 80-char contiguous SUBSTRING of `text` (a
    # SCROLLED /goal) or the own-shape head (`/goal ` prefix / paste placeholder).
    # A CLEAN idle input boundary only; every outcome is logged (design D).
    head = watchdog._input_box_head_text(cap)
    # #1113 -- the bare-box path marks the janitor watch before typing and never
    # clears it on failure, so provenance holds here and a TRUNCATED / grid-wrapped
    # own /goal (the >10x david1-3 report) is proven by tail + whitespace-stripped
    # body substring. The stash/stranded callers clear the watch on a verified
    # submit (box empty, nothing to clear), so that branch is inert there; no mark
    # -> the #737 substring / head-shape proof governs (a foreign draft, never a
    # >= 80-char substring of our payload, is untouched either way).
    _now = now if now is not None else time.time()
    # #1113 recurrence -- `match_templates=True`: a verbatim run of ANY rendered
    # /goal variant is ours (the 23.9 fork-no-merge tail carried another variant).
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


# --------------------------------------------------------------------------- #
# The ONE delivery function.
# --------------------------------------------------------------------------- #

_GOAL_TERMINAL_WORDS = frozenset((
    "sent", "expired", "drop:cleared-after-request", "drop:already-armed",
    "drop:nudge-off",     # #1181 -- the owner's per-kind switch withholds it
    "drop:owner-stop",    # #1257 -- the owner stopped/paused/left this session
    "drop:non-owner-origin",   # #1324 -- a named origin that is not the owner's
)) | frozenset("drop:%s-retired" % _o for _o in _GOAL_RETIRED_ORIGINS)  # #1113/#1260/#1324


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


def _declared_window_nudge(cwd, origin=None):
    """The keystroke NUDGE identity of a `/goal` arm into `cwd`'s pane. The
    ALWAYS-ON `goal-arm` recovery kind (never withheld by the #1023 per-kind
    switch) for the owner's OWN `self-callback` arm on ANY pane (#1181 -- they
    just typed /autopilot, #752; the staged kind, OFF on every box, withheld every
    keystroke of it on dev1 iemmixer/fohmixer 29.9.). Any other origin (a legacy
    empty one) keeps the staged `goal-sweep`; the automatic origins that once
    rode `goal-arm` are retired (#1324). `cwd` is kept for the callers' signature."""
    if origin == _GOAL_SELF_CALLBACK_ORIGIN:
        return "goal-arm"
    return "goal-sweep"


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
      "drop:<origin>-retired"       -- #1113/#1260/#1324: a leftover on-disk
                                        request of a RETIRED origin (every
                                        automatic arm); dropped terminally
                                        before any pane work, never typed.
      "drop:non-owner-origin"       -- #1324: any other NAMED origin that is
                                        not the owner's (allowlist).
      "skip:<reason>"               -- not safe right now; the caller LEAVES
                                        the request pending for the next sweep
                                        -- BUT #731: a KEYSTROKE-delivering skip
                                        (verify-failed / stash-abort) counts
                                        toward GOAL_DELIVERY_ATTEMPT_CAP, so it
                                        re-types at most that many times before
                                        goal_sweep drops it; a zero-keystroke
                                        defer is never counted.
      "drop:nudge-off" (#1181): the kind is OFF; nothing typed.
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

    Deliberately does NOT check `_goal_autoarm_recent_human_activity`: the
    only live origin is the owner's own `/autopilot` -- see this module's own
    header docstring for why that would be a structurally-always-refuses bug.

    `out` (#624, optional): when a dict is passed, `deliver_goal` records the
    journal-facing observability the flat return word cannot carry -- `out["loc"]`
    (the family-canonical `watchdog._pane_location` = the `montalu1:0.0` key every
    sibling goal-family line uses, set once after pid resolution) and, where a skip
    has detail beyond its word, `out["detail"]` (the busy-transcript age, a
    nudge-off note -- otherwise the word is self-complete). `goal_sweep`
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

    # #1113/#1260/#1324 -- a leftover on-disk request of a RETIRED origin (every
    # automatic arm) is dropped TERMINALLY before any pane work, never typed.
    if origin in _GOAL_RETIRED_ORIGINS:
        _log_goal_sync("DROP %s-retired sid=%s cwd=%s" % (origin, sid, cwd))
        return "drop:%s-retired" % origin
    if _not_owner_origin(origin):   # #1324 allowlist
        _log_goal_sync("DROP non-owner-origin sid=%s cwd=%s origin=%s"
                       % (sid, cwd, origin))
        return "drop:non-owner-origin"
    # #1257 -- a session the owner stopped/paused/left is NEVER armed, any origin.
    _ostop = _owner_stop.sid_latch(projects_dir, cwd, sid, state)
    if _ostop:
        _log_goal_sync("DROP owner-stop sid=%s cwd=%s origin=%s fact=%s"
                       % (sid, cwd, origin, _ostop.get("fact")))
        return "drop:owner-stop"

    # #1181 -- keystroke NUDGE identity (see `_declared_window_nudge`).
    _nudge = _declared_window_nudge(cwd, origin)

    # Hard age cap -- checked first, no pane resolution needed. Unlike
    # compact, an expired goal-arm is not harmless: PING once (deduped on
    # session+request-ts, so a later fresh request gets its own chance).
    if request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > GOAL_REQUEST_MAX_AGE_S:
            _log_goal_sync("SKIP expired sid=%s cwd=%s origin=%s"
                           % (sid, cwd, origin))
            # The owner's arm expired undelivered -> ping (#1324: no origin is
            # silent any more). Still returns "expired" -> goal_sweep clears it.
            if send_fn is not None and not dry_run:
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

    if watchdog._keystroke_suppressed("goal", False, _nudge):   # #1181: no pane read
        return _arm_failure.nudge_off(sid, cwd, text, _nudge, logs, out, _log_goal_sync)
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

    tpath = _mark_off = None  # #566: defined for the case-(a) recovery even with no transcript
    mark = None             # newest marker in the 4 MB tail; ONLY for the #170 cleared-after-request check
    tinfo = watchdog.find_active_transcript(projects_dir, cwd)
    if tinfo:
        tpath, _tmtime = tinfo
        _mark_off, mark = _scan_with_confirm_offset(tpath, sid)   # #1265 keystroke boundary
        if mark is not None and mark.get("state") == "cleared":
            mts = mark.get("ts")
            if mts is not None and request_ts is not None and mts > request_ts:
                _log_goal_sync("DROP cleared-after-request sid=%s cwd=%s"
                               % (sid, cwd))
                return "drop:cleared-after-request"
    # Tri-state already-armed check.
    armed = watchdog.pane_goal_armed(captured)
    if armed is True:
        # #1113 -- UNCONDITIONAL: a /goal keystroke NEVER reaches a `◎ /goal`
        # armed footer (the owner's own paste landed, or a foreign goal runs).
        _log_goal_sync("DROP already-armed sid=%s cwd=%s" % (sid, cwd))
        return "drop:already-armed"
    if armed is None:
        _log_goal_sync("SKIP undeterminable sid=%s cwd=%s" % (sid, cwd))
        return "skip:undeterminable"

    # #752 -- no client-input defer: the owner's OWN `/autopilot` arm IS the owner
    # at the keyboard (ruling 2026-08-30); safety is the idle-box/busy gates below
    # + deliver_with_stash draft protection (#35) + #737/#746 verified typing.
    # ACCEPTED RESIDUAL (owner-ruled): concurrent owner typing may interleave.

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

    kind, draft = _cbox.boundary(captured, text)   # #1381: own collapsed /goal = a draft
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
        # Mark provenance BEFORE the attempt, regardless of outcome, so the shared
        # janitor (#372) can recover a stuck stash send here (#403-review M1).
        watchdog._janitor_mark_watch(state, pid, now)
        # #566 -- our OWN swallowed COMPLETE /goal already in the box is SUBMITTED in
        # place, never re-stashed into the single slot (the #501 lesson): exact
        # completeness against `text` (head+tail, or #1381 a collapsed box with a
        # first-person record + row proof) + the recent-human gate (for the owner's
        # own arm only its 5-min client-input signal, #752) -- see the helper.
        _sub = {}
        if _submit_stranded_own_goal(sid, cwd, text, pid, captured, tpath,
                                     run, state, now, sleep_fn, logs, nudge=_nudge, out=_sub,
                                     owner_arm=origin == _GOAL_SELF_CALLBACK_ORIGIN):
            watchdog._janitor_clear_watch(state, pid)
            _cbox.forget_typed(state, pid)
            if not _await_goal_armed(pid, run, sleep_fn, tpath, _mark_off):  # #720/#1265
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
        if _cbox.rows(captured) is not None:     # #1381: never stash a collapsed box
            if _sub.get("entered"):              # an Enter went in: the counted class
                return _verify_fail_word(tpath, _tage, now)
            _log_goal_sync("SKIP collapsed-unsubmitted sid=%s cwd=%s" % (sid, cwd))
            return "skip:collapsed-unsubmitted"
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
            if not _await_goal_armed(pid, run, sleep_fn, tpath, _mark_off):  # #720/#1265
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
    _typed = {}
    ok = _send_goal_verified(pid, text, run, captured=captured, sleep_fn=sleep_fn,
                             logs=logs, nudge=_nudge, tpath=tpath, mark_off=_mark_off,
                             out=_typed)  # #1038/#1265
    if ok:
        watchdog._janitor_clear_watch(state, pid)
        _cbox.forget_typed(state, pid)
        _log_goal_sync("SEND typed sid=%s cwd=%s" % (sid, cwd))
        return "sent"
    if _typed.get("typed"):   # #1381: our head-checkpointed text may be left in the box
        _cbox.note_typed(state, pid, text, now)
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
    ok = record_goal_request(sid, cwd, text, authority, now=now_fn(), path=requests_path, origin=origin,
                             moff=_arm_confirm.request_mark_offset(projects_dir, cwd, sid, origin))  # #1265
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


def goal_sweep(now, run=None, dry_run=False, projects_dir=None,
              requests_path=None, state=None, handled=None, send_fn=None,
              sleep_fn=None):
    """The periodic re-evaluation of every PENDING goal-arm request (job
    9's new body -- replaces the old arm-question viewport scan and virgin-
    candidate heuristic entirely). It never RECORDS a request: the one writer is
    the owner's `/autopilot` (#1260 retired the virgin arm, #1324 every
    automatic re-arm; their leftovers are purged first). Re-checks each still-pending request's
    SAME unmodified conditions every sweep. A request that keeps failing a
    condition sits until it clears (delivered next sweep) or the age cap
    discards it -- with ONE bound (#731): a request whose KEYSTROKE deliveries
    (`_GOAL_KEYSTROKE_SKIPS`: verify-failed / stash-abort) have failed
    `GOAL_DELIVERY_ATTEMPT_CAP` times is DROPPED terminally BEFORE a further
    keystroke, so a never-arming request can no longer re-type into the prompt
    forever (the montalu4 retype livelock). A zero-keystroke defer
    (undeterminable / busy / busy-transcript / ...) never counts.

    `handled` (optional, a `set()`): the SAME per-sweep set job 14/20's
    riders populate -- a sid already sent a keystroke burst THIS sweep is
    skipped, never double-typed into."""
    logs = []
    # #1260 -- a RETIRED-origin request leaves the store first, before the owner
    # kill-switch and the #731 cap: never typed, never pinged, never a phantom
    # "arming" status on a paused box (david3's 25.10. 14:56 leftover).
    for _rsid, _req in sorted(load_goal_requests(requests_path).items()):
        _rorig = (_req or {}).get("origin") if isinstance(_req, dict) else None
        if _not_owner_origin(_rorig) and not dry_run:   # #1324: any non-owner
            _word = ("%s-retired" % _rorig if _rorig in _GOAL_RETIRED_ORIGINS
                     else "non-owner-origin")
            clear_goal_request(_rsid, path=requests_path)
            _log_goal_sync("DROP %s sid=%s (sweep purge)" % (_word, _rsid))
            logs.append("DROP (goal-sweep) sid=%s -> %s" % (_rsid, _word))
    if state is not None and not dry_run:
        state.pop("goal_virgin_arm", None)     # #1260: the retired #1038 rate-floor store
    if watchdog._owner_disabled("goal"):
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
        return logs
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
        _drop = ("drop:malformed-empty (no text)" if not text else
                 "drop:watch-window (steer=watch arms no /goal, #1163)"
                 if _wt.watch_window_for(cwd) is not None else "")
        if _drop:
            # malformed/legacy entry, or a request recorded before its window
            # became watch-steered -- drop rather than retry forever. #624 --
            # name the drop, never a SILENT branch (the cwd-derived label).
            logs.append("DROP (goal-sweep) %s sid=%s -> %s"
                        % (watchdog.project_label(cwd), sid, _drop))
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
        _late = _arm_confirm.late_arm_drop(entry, sid, cwd, projects_dir, _log_goal_sync)  # #1265
        if _late:                                   # an owner arm landed late: never retype it
            logs.append(_late)
            aborts.pop(sid, None)                   # same bookkeeping as `sent`
            _arm_failure.clear(state, sid)
            clear_goal_request(sid, path=requests_path)
            continue
        # #731 -- the per-request delivery-attempt CAP, checked BEFORE deliver_goal: a request
        # that already failed CAP keystroke deliveries is DROPPED terminally (never CAP+1): clean
        # up its stranded /goal text, clear it, ping (origin-gated). The montalu4 retype-livelock
        # exit; `dl_fails` is INT-guarded across the JSON boundary (#714).
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
                sid, cwd, text, _dl_count,
                entry.get("ts"), run, projects_dir, state, now, send_fn,
                dry_run, sleep_fn)
            logs += clog
            aborts.pop(sid, None)
            clear_goal_request(sid, path=requests_path)
            logs.append("DROP (goal-sweep) %s sid=%s -> drop:attempt-cap "
                        "(%d keystroke deliveries failed, last=%s; leftover=%s)"
                        % (loc, sid, _dl_count, dl_last, leftover))
            if entry.get("origin") == _GOAL_SELF_CALLBACK_ORIGIN:   # #1181 (c)
                logs += _arm_failure.on_self_arm_capped(
                    sid, cwd, dl_last, _dl_count, run, projects_dir, state, now,
                    sleep_fn)
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
        # actual arm keystroke. Leave the request PENDING (goal_sweep re-tries
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
            # Only "sent" (slot freed) pops the counter. On "expired" etc. the
            # slot is still occupied — keeping the counter lets the owner's
            # next request inherit the accumulated abort history.
            if word == "sent":
                aborts.pop(sid, None)
            clear_goal_request(sid, path=requests_path)
        if word == "sent":
            _arm_failure.clear(state, sid)                     # #1181: armed now
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
            # defer (undeterminable / busy / busy-transcript / ...) does NOT
            # (counting those would starve a
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
            if word in _GOAL_KEYSTROKE_SKIPS:
                _bump_goal_delivery_fail(sid, word, path=requests_path)
            elif word == "skip:verify-failed-live":
                _bump_goal_delivery_fail(sid, word, path=requests_path,
                                         field="dl_live_fails")
            logs.append("SKIP (goal-sweep) %s sid=%s -> %s%s"
                        % (loc, sid, word, dsuf))
    return logs


# --------------------------------------------------------------------------- #
# DARK-WATCH -- job 20's new body. Cross-checks each session's transcript
# marker (INTENT) against CC's own footer indicator (REALITY). A goal that is
# LOST -- an auth clear, a CC `failed` end, or a dark footer CONFIRMED by the #524
# run -- is only JOURNALLED, once per episode (`goal-lost:<why> ... NOT re-armed`;
# a CC `met` end logs `goal-ended:met`, #1309), plus ONE
# keystroke-free #459 report for the dark case. #1324 (owner 14.11.2026): the
# watchdog NEVER re-arms a goal; "no goal visible" = autopilot off, and the
# owner re-arms with `/autopilot`. Also runs the shared janitor recovery (#372)
# at the top of its per-pane loop, since it is the one sweep that visits every
# live pane every tick regardless of pending requests.
# --------------------------------------------------------------------------- #

# #459 -- the dark-goal report. /compact never CLEARS the goal, but the
# PROCESS-BOUND /goal loop can silently stop firing turns while the transcript
# keeps reporting armed (montalu issue 76: the footer loses the glyph, the shape
# dark-watch detects). #804 item 4: a dark episode reports AT MOST ONCE
# (`pinged_state` is a per-episode, mark_ts-keyed latch); the send is
# notify-SUPPRESSED (goal-dark, #704), so it lands as a delivery-log line.
#
# #524 (owner decision B, 2026-08-17) -- the death-CONFIRMATION run: K
# consecutive clean-dark footer reads AND a >= MIN_SPAN unbroken run, with ANY
# armed read OR a liveness proof (transcript mtime advanced) VETOING the whole
# run, so an idle-but-alive session whose glyph flickers (montalu 2026-08-16) is
# never journalled lost. The only positive death evidence is persistent,
# unanimous glyph absence (heartbeat/mark/mtime are shared by dead AND idle).
GOAL_DARK_CONFIRM_MIN_READS = 8         # K consecutive clean-dark reads
GOAL_DARK_CONFIRM_MIN_SPAN_S = 600      # AND the run must span >= 10 min
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
# HONOURS (never report a goal we just deliberately cleared) until the transcript
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
    """#522 per-sid attempt cap: at most
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
                        NOT accumulate a death run / report for this sid.
      * vetoed=False -- either no veto, or a genuine human answer landed AFTER the
                        disarm -> the veto is POPPED here (re-entry) and the
                        standard dark-watch resumes.
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
                       "(human answered after disarm) -- standard dark-watch resumes"
                       % (loc, sid))
    return True, ("dark-watch %s sid=%s -> #522 disarm veto ACTIVE (goal was "
                  "cleared on a stuck ❓; nothing reported until the user answers)"
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
        confirmed=True  -- >= K clean reads AND run span >= MIN_SPAN -> lost
        confirmed=False -- still accumulating (journalled, nothing reported)
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


def _dark_watch_window_skip(logs, sid, cwd, loc, seen_state, pinged_state,
                            confirm_state):
    """#1163 -- True when `cwd` is a steer=watch window: dark-watch must not
    treat its dark goal as a lost loop (no confirmation run, no #459 report) --
    its triggers are Job 52's. Journals ONE line per episode
    (the `seen_state` watch mark) and clears the dead-loop bookkeeping."""
    if _wt.watch_window_for(cwd) is None:
        return False
    if not (seen_state.get(sid) or {}).get("watch"):
        logs.append("dark-watch %s sid=%s -> skip:watch-window (steer=watch; its "
                    "triggers are Job 52's, #1163)" % (loc, sid))
    seen_state[sid] = {"watch": True}
    pinged_state.pop(sid, None)
    confirm_state.pop(sid, None)
    return True


def _dark_awaiting_user_veto(tpath):
    """#737 C -- True when the session's LAST real assistant turn ended with a
    ❓ marker: it is PARKED on a question to the owner, ALIVE-waiting, not a dead
    loop. `goal_dark_watch`'s mtime-advance liveness veto cannot see it (the
    transcript is STATIC until the owner answers), so this tail-marker read is
    the only awaiting-user signal. The montalu6 incident: one-glance classified
    the pane awaiting-user while dark-watch declared CONFIRMED-DEAD and re-armed a
    truncated /goal into it. Since #1324 nothing re-arms; the veto keeps a
    ❓-parked session from being journalled/reported as a lost goal. Bounded
    tail read (#599): a parked-on-❓ session has that turn as its LAST real
    assistant message, well inside the 2 MB tail (a false-negative would only be a
    single >2 MB entry sitting AFTER the ❓ turn -- vanishingly rare)."""
    return watchdog.transcript_last_marker_bounded(tpath) == "❓"


def _default_rearm_fn(cwd):
    """Real (text, authority) source of the installed `/goal` template for this
    cwd's authority profile -- the OWN-CONTENT reference dark-watch's janitor,
    #617 stranded clear, #623 drift observation and #878 goal-guard compare
    against (never typed as an arm since #1324). Lazily imported and FULLY
    guarded; any failure degrades to a None `text` ('template unresolved').
    An UNEXPECTED `resolve_authority` failure returns no template, NEVER falls
    UP to `full` (#478 review MINOR)."""
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
        # #998 — resolve the pane's role from cwd via the SAME renderer
        # (goal_template_for delegates to the SKILL.md read for a no-role pane).
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
    """#878 — delivery half of the goal-guard rider (after a truthy decide):
    nudge_gate + the pane's daily budget, recent-human, boundary, keystroke,
    mark_sent. Returns the decision-log lines."""
    logs = []
    # #923 BATCH MODE: gate_ok is handled once by the caller.
    if batch_collect is None:
        if not watchdog.nudges_enabled("goal-guard"):   # #1023 per-kind switch
            logs.append("goal-guard %s sid=%s -> skip:kind-off (goal-guard)"
                        % (loc, sid))
            return logs
        if not _nudge_gate.gate_ok(state, sid, "goal-guard", now):
            if _nudge_gate.first_gate_hold(state, sid, "goal-guard", now):
                logs.append("goal-guard %s sid=%s -> %s" % (loc, sid, (
                    _nudge_gate.floor_hold_reason(state, sid, "goal-guard", now))))
            return logs
        if not _nudge_gate.pane_budget_ok(state, pid, now):   # B9: one per pane/day
            if _nudge_gate.first_pane_hold(state, pid, "goal-guard", now):
                logs.append("goal-guard %s sid=%s -> %s" % (
                    loc, sid, _nudge_gate.pane_budget_hold_reason(state, pid, now)))
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
    _gout = {}
    ok = _send_goal_verified(pid, _GOAL_GUARD_TEXT, run,
                             captured=captured, sleep_fn=sleep_fn,
                             verify_armed=False, nudge="goal-guard", out=_gout)
    if _gout.get("typed"):
        _nudge_gate.mark_pane_attempt(state, pid, now)
    if ok:
        _nudge_gate.mark_sent(state, sid, "goal-guard", now)
        logs.append("goal-guard %s sid=%s -> sent" % (loc, sid))
    else:
        logs.append("goal-guard %s sid=%s -> send-failed" % (loc, sid))
    return logs


def _goal_lost_line(sid, loc, mark_ts, why, latch=None):
    """#1324 -- the explicit decision line for a LOST goal. With a `latch` it is
    returned ONCE per episode (`latch[sid] == mark_ts` suppresses a repeat). The
    watchdog never re-arms (owner 14.11.2026: "no goal visible" = autopilot
    off)."""
    if latch is not None:
        if sid in latch and latch.get(sid) == mark_ts:
            return []
        latch[sid] = mark_ts
    return ["dark-watch %s sid=%s -> goal-lost:%s NOT re-armed (#1324: the "
            "watchdog never arms a /goal; the owner re-arms with /autopilot)"
            % (loc, sid, why)]


def _dark_episode_line(pinged_state, sid, loc, mark_ts, now, word):
    """#459/#804/#1324 -- the ONE decision line of a dark episode (mark_ts-keyed
    `pinged_state` latch): `word` (e.g. a fulfilled end) or, when None, the
    `goal-lost:dark` line. [] when this episode was already journalled."""
    prec = pinged_state.get(sid)
    if isinstance(prec, dict) and prec.get("mark_ts") == mark_ts:
        return []
    pinged_state[sid] = {"mark_ts": mark_ts, "last": now}
    if word is None:
        return _goal_lost_line(sid, loc, mark_ts, "dark")
    return ["dark-watch %s sid=%s -> %s" % (loc, sid, word)]


def _report_dark_goal(send_fn, sid, cwd, pid, run, mark_ts, dry_run):
    """#403/#459 -- the ONE keystroke-free report of a lost dark goal, with
    #403's exact dedup_key (goal-dark:sid:mark); notify-suppressed (#704)."""
    from notify import stream_redirect
    send_fn("\U0001f480 **%s** — /goal loop zomrelo potichu "
            "(transkript hovorí armovaný, footer nie). "
            "Spústi prosím `/autopilot` znova." % watchdog.project_label(cwd),
            owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
            dedup_key="goal-dark:%s:%d" % (sid, int(mark_ts or 0)),
            dry_run=dry_run)


def goal_dark_watch(now, run=None, state=None, send_fn=None, dry_run=False,
                    projects_dir=None, sleep_fn=None, time_fn=None,
                    sweep_deadline=None, rearm_fn=None, human_ts_fn=None):
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

    #1324 (owner 14.11.2026) -- OBSERVE-only for a lost goal: the watchdog never
    arms or re-arms a `/goal`. A CC transient-auth clear (clear_kind="auth") and
    a dark footer CONFIRMED dead by the #524 run (K clean-dark reads over >=
    MIN_SPAN, ANY armed / undeterminable read or advancing transcript mtime
    VETOING the run) are journalled ONCE per episode (`_goal_lost_line`), the
    dark one plus ONE keystroke-free #459 report. `rearm_fn(cwd) -> (text,
    authority)` (default `_default_rearm_fn`) is only the own-template reference
    for the janitor / #617 clear / #623 drift / #878 goal-guard. The #617
    stranded-truncated-/goal CLEAR (`_clear_stranded_truncated_goal`) is the
    one keystroke path here -- Escape+BSpace, gated on a clean boundary +
    fail-closed recent-human + a byte-exact-prefix content proof + a bounded
    give-up."""
    logs = []
    state = state if state is not None else {}
    if not dry_run:   # #1324 -- drop the retired re-arm machinery's state stores
        for _key in _GOAL_RETIRED_STATE_KEYS:   # (before the kill switch, like
            state.pop(_key, None)               # goal_sweep's request purge)
    if watchdog._owner_disabled("goal"):
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
        return logs
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    off_state = state.setdefault("goal_mark", {})
    seen_state = state.setdefault("goal_dark_seen", {})
    pinged_state = state.setdefault("goal_dark_pinged", {})
    janitor_recs = state.setdefault("janitor_pinged_rec", {})
    # #524 -- the death-CONFIRMATION run per sid (clean-dark reads + span). Reap
    # a confirm window untouched beyond its TTL so a session that vanished
    # mid-accumulation cannot leak an entry forever (the #486-G5 lesson).
    confirm_state = state.setdefault("goal_dark_confirm", {})
    # #1324 -- the per-sid lost-goal journal latch {sid: mark_ts} (auth clear).
    lost_state = state.setdefault("goal_lost_seen", {})
    # #1092 (e) / #1113 -- the per-sid stale-drift OBSERVATION latch: once a
    # session's template drift has been LOGGED for a given shipped-template hash,
    # do not re-LOG it until the template changes (originally a re-RECORD dedup;
    # #1113 retired the record, so the same stamp now dedups the observation so a
    # template deploy no longer floods the journal every ~30 min sweep).
    stale_tmpl_state = state.setdefault("goal_stale_rearm_tmpl", {})
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

        rec = off_state.get(sid)
        off = rec.get("off") if isinstance(rec, dict) else None
        prior_mark = rec.get("mark") if isinstance(rec, dict) else None
        # #675 -- DEPLOYED-BUT-INERT guard: a PRE-fix sweep advanced `off` PAST an
        # auth-clear line the old pre-filter skipped, so the stored `mark` would
        # stay a stale "set" forever (one_glance armed=yes — exactly the sessions
        # #675 targets). When the persisted entry predates
        # the current marker-parser version, force a first-sight RESEED (reverse-
        # scan from EOF via `_seed_or_scan_marker(off=None)`) so the NEW recognizer
        # re-reads the whole tail once. Self-healing, no operator action (#618 class).
        if not (isinstance(rec, dict)
                and rec.get("pv") == _GOAL_MARK_PARSER_VERSION):
            off = None
            prior_mark = None
        # #524 -- the transcript mtime from the PRIOR sweep. An advance is a
        # structured LIVENESS proof (the session wrote a turn) -> VETO a
        # death-confirmation run: never report a loop that is alive as lost.
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
        if _dark_watch_window_skip(logs, sid, cwd, loc, seen_state, pinged_state, confirm_state):
            continue   # #1163 steer=watch: a dark goal is that window's normal state

        # #522 -- honour the disarm-on-question veto (see `_qdisarm_veto`): never
        # accumulate / report a loop just deliberately cleared for a stuck ❓.
        vetoed, vlog = _qdisarm_veto(qveto, sid, tpath, now, human_ts_fn, loc)
        if vlog:
            logs.append(vlog)
        if vetoed:
            continue

        # #737 C -- a session PARKED on a ❓ question is ALIVE-waiting, never a
        # lost goal: reset the episode bookkeeping and move on.
        if _dark_awaiting_user_veto(tpath):
            if confirm_state.pop(sid, None) is not None:
                logs.append("dark-watch %s sid=%s -> hold:awaiting-user "
                            "(❓ marker, confirmation run reset)" % (loc, sid))
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            continue

        armed = watchdog.pane_goal_armed(captured)

        if mark is None or mark.get("state") != "set":
            # #675/#1309/#1324 -- CC ended the goal itself (auth clear, met, failed):
            # JOURNAL it once, never re-arm (the owner re-arms with /autopilot). A
            # USER `/goal clear` or a non-auth `error` clear just ends the episode.
            from watchdog import goal_status_mark as _gsm
            _end = _gsm.cc_end_lines(sid, loc, mark, lost_state, _goal_lost_line)
            if _end is not None:
                logs += _end
            else:
                lost_state.pop(sid, None)
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            confirm_state.pop(sid, None)   # #524 -- episode over (clear/no marker)
            continue
        mark_ts = mark.get("ts")

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
            # never-suspected pane). Nothing reported -- retry next sweep.
            crec = confirm_state.get(sid)
            if isinstance(crec, dict) and crec.get("clean_run"):
                crec["clean_run"] = 0
                crec["run_start"] = None
                crec["last"] = now
            continue

        # armed is False, mark == "set" -- the silently-dead-loop shape.
        # #524 LIVENESS VETO: the transcript mtime advanced since the prior
        # sweep -> the session wrote a turn -> it is ALIVE, only its footer read
        # dark this sweep. Reset the confirmation run.
        if (isinstance(prior_tmtime, (int, float))
                and isinstance(tmtime, (int, float)) and tmtime > prior_tmtime):
            if confirm_state.pop(sid, None) is not None:
                logs.append("dark-watch %s sid=%s -> VETO-ALIVE:mtime-advanced "
                            "(session wrote a turn, confirmation run reset)"
                            % (loc, sid))
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            continue
        # #766/#767/#1324 -- a loop with a `🏁 BACKLOG EMPTY` proof AFTER its
        # arm FINISHED (CC writes no achieved marker; later chores or owner chat
        # never shadow the proof): journal it once, never report it as lost.
        _bts = watchdog.transcript_last_backlog_empty_ts(tpath)
        if _bts is not None and isinstance(mark_ts, (int, float)) \
                and _bts >= mark_ts:
            confirm_state.pop(sid, None)
            logs += _dark_episode_line(pinged_state, sid, loc, mark_ts, now,
                                       "goal-ended:fulfilled (🏁, not reported)")
            continue
        prior = seen_state.get(sid)
        if not isinstance(prior, dict) or prior.get("mark_ts") != mark_ts:
            seen_state[sid] = {"mark_ts": mark_ts, "first_seen": now}
            logs.append("dark-watch %s sid=%s -> first observation, debouncing"
                        % (loc, sid))
            continue
        # #459/#524 -- a possibly silently-dead loop: advance the death-
        # CONFIRMATION run on EVERY clean-dark sweep.
        confirmed, confirm_state[sid] = _dark_confirm_advance(
            confirm_state.get(sid), mark_ts, now)
        if not confirmed:
            # EXPLICIT decision log (#486), never a silent suppression.
            _win = confirm_state.get(sid) or {}
            logs.append(
                "dark-watch %s sid=%s -> ACCUMULATING (reads=%s/%d span=%ss/%ds)"
                % (loc, sid, _win.get("clean_run"), GOAL_DARK_CONFIRM_MIN_READS,
                   int(now - (_win.get("run_start") or now)),
                   GOAL_DARK_CONFIRM_MIN_SPAN_S))
            continue
        # #459/#804/#1324 -- a CONFIRMED lost goal: journal + report ONCE per
        # episode (`pinged_state`, mark_ts-keyed); never re-armed.
        _lost = _dark_episode_line(pinged_state, sid, loc, mark_ts, now, None)
        logs += _lost
        if _lost and send_fn is not None and not dry_run:
            _report_dark_goal(send_fn, sid, cwd, pid, run, mark_ts, dry_run)

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
        # #1324 -- the per-episode latches live as long as the session's
        # goal_mark (its #519 visited+age rule), so a transient skip (copy-mode,
        # janitor, sweep budget) never re-opens an episode's journal line.
        for _latch in (lost_state, pinged_state, seen_state):
            for _gsid in [k for k in list(_latch) if k not in off_state]:
                _latch.pop(_gsid, None)
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
    goal is long cleared -- and a wrongly-reaped one just means dark-watch
    is no longer vetoed, which after 24h is correct). `attempts`
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
# JOB 20 PER-PANE STATE (#365 lane sweep). #1137 (owner 25.10.2026) removed
# the lane-occupancy refill branch; the sweep keeps the one-glance verdict,
# the roster census, the stuck alert and the account-block owner ping.
# --------------------------------------------------------------------------- #

GOAL_LANE_IDLE_S = 15 * 60
GOAL_LANE_LIVE_WINDOW_S = 15 * 60
# #442 -- the goal-guard rider's OWN "live conversation" window: a prompt
# SUBMITTED within the last ~3 min (the presence marker is stamped only on
# UserPromptSubmit) = a genuinely live exchange, never interrupted. The shared
# check's 30-min default is calibrated for the virgin-arm decision.
GOAL_LANE_LIVE_CONVO_S = 3 * 60

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


# #531 -- orphan-reap TTL for state["goal_lane"] per-sid records (24h, like
# GOAL_MARK_ORPHAN_TTL_S): a live armed pane re-stamps `lts` every sweep, so only
# a genuinely gone session's aged, not-visited entry is reaped.
GOAL_LANE_ORPHAN_TTL_S = 24 * 3600


# #502 -- account-limit back-off HARD cap. When the supervisor's transcript shows a
# recent account-level dispatch block (`is_account_dispatch_block`), the episode
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
    reset can never silence the guard for longer than one bounded window."""
    cap = first_seen + ACCOUNT_LIMIT_BACKOFF_MAX_S
    if resets_at and resets_at > first_seen:
        return min(resets_at, cap)
    return cap


def _account_limit_decision(rec, now, err, loc, waiters):
    """#502 -- the account-limit back-off decision from the
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
        return True, ("account-limit %s waiters=%d -> skip:account-limit "
                      "(dispatch by teraz zomrel na strope účtu; back-off do %s, "
                      "zostáva %ds)" % (loc, waiters, when, int(release - now))), notify
    # Window elapsed -> re-probe ONCE and re-arm for the next window. (A re-arm is
    # never a new episode, so it never re-notifies.)
    alim["first_seen"] = now
    alim["resets_at"] = watchdog.parse_reset_epoch_from_error_text(err, now)
    return False, ("account-limit %s -> back-off elapsed, "
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
            "prístup k predplatnému), session nemôže pracovať, kým sa blok nezruší. "
            "Nemá automatický reset, treba tvoj zásah." % watchdog.project_label(cwd),
            owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
            dedup_key="acctblock:%s:%d" % (sid, int(first_seen)),
            dry_run=dry_run)
    return ("account-limit %s -> account-block owner PINGED "
            "(no-reset cap, job-6-unhandled)" % loc)


def goal_account_limit_check(now, run, rec, sid, cwd, pid, tpath, loc,
                             send_fn, dry_run, captured=""):
    """#502 -- the account-block OWNER PING for one armed pane (run by
    `goal_lane_sweep`): `_account_limit_decision` reads the supervisor's own
    transcript error and keeps the bounded episode in `rec['alim']`; the FIRST
    detection of a no-reset block (monthly spend / org disable, which job 6 does
    not ping) pings the owner once per episode. Never types into the pane. A dry
    run decides on a copy (run_once saves state on a dry run too)."""
    _back_off, alim_log, alim_notify = _account_limit_decision(
        copy.deepcopy(rec) if dry_run else rec, now,
        watchdog.transcript_last_error(tpath), loc,
        watchdog._pane_live_task_count(captured or ""))
    logs = [alim_log] if alim_log else []
    if alim_notify and send_fn is not None and not dry_run:
        logs.append(_account_limit_notify_owner(
            send_fn, pid, run, sid, cwd, dry_run,
            rec.get("alim", {}).get("first_seen", now), loc))
    return logs


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
    9,5h outage: no lane nudge types a keystroke (the lane-occupancy branch is
    gone, #1137), so when the pane stays `stuck` across
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
    _bt, _incl = _nudge_gate.compose_batch([(c, t) for c, t, _ in collect])
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
            _blines = _bfn()          # a rider may journal its delivery (#1109)
            if isinstance(_blines, list):
                logs.extend(_blines)
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
        # stamp the floor or run the undo (the storm brake); journalled once per
        # day episode, like send_verified's own hold line (B9 fix).
        if not send_out.get("hold_quiet"):
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
                    queue_classify=None, u_fetch=None,
                    deploy_state_fetch=None, infra_queue_fetch=None,
                    resolve_role_fn=None, persist=None,
                    bounce_unhandled_fetch=None):
    """The armed-pane driver -- the second half of job 20's body. For every
    candidate pane whose goal is genuinely ARMED right now it runs the
    account-block check (`goal_account_limit_check`), the stuck alert and the
    per-pane riders. Owns its own small per-sid state namespace
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
        tpath, _tmtime = tinfo
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
        # #1257: the owner stopped this pane -- its RIDERS neither collect nor
        # type (every baseline kept); the bookkeeping below still runs.
        _hold = _nudge_gate.pre_send_hold(state, pid, "lane-sweep", now, budget=False)
        if _hold and _hold[1]:
            logs.append("lane-sweep %s -> hold:%s: no rider runs, every baseline "
                        "kept" % (loc, _hold[0]))
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
                # session later exits.
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
            _pane_role = None
            if infra_queue_fetch is not None and resolve_role_fn is not None:
                try:
                    _pane_role = resolve_role_fn(cwd)
                except Exception:  # noqa: BLE001 — resolver fault => skip (safe)
                    _pane_role = "unresolved"
                if _pane_role == "infra" and not _hold:
                    logs += _queue_arrival.goal_queue_arrival_recheck(
                        now, run, qrecs, sid, cwd, pid, tpath, loc, dry_run,
                        handled, queue_fetch=queue_fetch,
                        infra_queue_fetch=infra_queue_fetch,
                        resolve_role_fn=resolve_role_fn, state=state,
                        sleep_fn=sleep_fn, captured=captured,
                        persist=persist, budget_left_fn=_budget_left_fn,  # #1023 timeout-race
                        receipt_post_fn=_queue_arrival._default_hub_receipt_post(cwd))  # #1109
            # #1178 (b) — a supervisor whose /goal ENDED (cleared, last turn ✅)
            # learns of new tickets in its own I; delivery held unless idle +
            # human-quiet. A resolver fault never guesses (skip).
            if _pane_role != "infra" and not _hold:   # role re-checked in ended_pane_recheck
                logs += _queue_own.ended_pane_recheck(
                    glance, now, run, qrecs, sid, cwd, pid, tpath, loc, dry_run,
                    handled, queue_fetch, state, captured=captured,
                    sleep_fn=sleep_fn, classify_builder=queue_classify,
                    resolve_role_fn=resolve_role_fn, persist=persist,
                    budget_left_fn=_budget_left_fn)
            continue
        # #804 -- this stream is CONFIRMED armed this sweep (the STRUCTURED
        # one-glance verdict, not a render guess): refresh its durable roster
        # entry so the DEAD-SESSION census has an accurate
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
        # #502 -- the account-block owner ping (a Discord send, never a
        # keystroke); its bounded episode rides `rec['alim']`.
        logs += goal_account_limit_check(now, run, rec, sid, cwd, pid, tpath,
                                         loc, send_fn, dry_run, captured=captured)
        rec["lts"] = now   # #531 -- write-time age anchor for the orphan reaper
        recs[sid] = rec
        # #662 -- route a PERSISTENT structural `stuck` verdict to an owner
        # ALERT (SILENCE B of the montalu6 9,5h outage), using the ALREADY-cached
        # `glance` (no fetch). A Discord SEND not a keystroke, so it never
        # conflicts with a `handled` keystroke and is authority-agnostic; `rec`'s
        # `soa` streak persists via `recs`. `stuck_seen` guards the #645
        # two-panes-one-cwd double-advance (same sid twice per sweep).
        # #1257: a pause OF `sid` (the cwd's newest) ENDS its stuck / gk-stall
        # episodes; a pane held by another session's latch touches none.
        _mine = _owner_stop.latch_sid(pid) == sid
        _own_stop = bool(_hold) and _mine
        if _own_stop and not dry_run:
            for _k in ("soa", "soalert", "soa_ts", "gks_ts", "gks_alert"):
                rec.pop(_k, None)
        if sid not in stuck_seen and not _own_stop:
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
        if _hold:      # #1257: the riders below hold; the pane's batch entry goes
            if _mine:
                state.get("nudge_batch", {}).pop(sid, None)
            continue
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
        # #733/#1178 -- queue-ARRIVAL watcher (own workable set) for this armed
        # pane (#1029: also an armed INFRA pane, the infra queue).
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
        # #923 BATCH DELIVERY: compose + deliver all collected texts as ONE prompt.
        if _batch_collect and not dry_run:
            _deliver_batch(_batch_collect, pid, sid, loc, tpath, run, state, now,
                           handled, persist, sleep_fn, logs, _budget_left_fn)
        # #1066 lane B -- BOUNCE-verdict rider for this armed REDUCED-authority
        # pane. Runs AFTER the batch delivery + every #733 rider so the shared
        # per-sweep `handled` set (at most ONE keystroke per pane per sweep) is
        # honoured: a pane any earlier rider/batch already typed this sweep is
        # deferred (base kept OLD, re-detects next sweep). Direct-send (not
        # batched); its own authority gate skips FULL-authority panes; the
        # #1178 queue-arrival own set drops a stream's bounce rows (no double).
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
        # A cwd that is live again clears its census-flood latch so a FUTURE
        # death re-surfaces (rgts/rfails/ratt: the deleted relaunch's legacy
        # anchors, cleaned off old entries).
        if isinstance(_e, dict) and any(
                k in _e for k in ("census_ts", "rgts", "rfails", "ratt")):
            for _k in ("census_ts", "rgts", "rfails", "ratt"):
                _e.pop(_k, None)
            roster_dirty = True
    if roster_dirty and not dry_run and not _roster.save_roster(roster_reg):
        # A persistently-failing save loses the census_ts anchor each reload ->
        # the hourly census would degrade to every sweep; surface it
        # (machine-channel) instead of silently churning.
        logs.append("roster save FAILED (unwritable ~/.claude?) -- census "
                    "cadence not persisted this sweep")
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
        _nudge_gate.prune(state, visited_sids, now)   # #797 shared cadence gate
    return logs
