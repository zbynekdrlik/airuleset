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

  INPUT   -- exactly ONE proven origin creates a pending request:
             `record_goal_request(...)`, called from `airuleset.py
             goal-arm --self` -- the /autopilot skill's OWN Step 2, as its
             last tool call right after printing the `/goal` line for the
             user. Unlike compact's TWO origins (a per-TICKET-boundary
             SubagentStop hook plus a self-callback), goal-arming is a
             ONCE-PER-SESSION bootstrap event, not a per-ticket one -- there
             is no ticket-boundary-shaped signal that means "please arm my
             goal" the way a completed autopilot-worker ticket means
             "please compact." One origin is honest, not a shortcut.

  DELIVERY -- ONE function, `deliver_goal()`. It checks, in order: the
             owner kill-switch; a hard, non-refreshable age cap (an expired
             request pings once -- "arm failed, re-run /autopilot" -- since
             a silently-undeliverable arm request is a dark-autopilot
             failure, not a harmless drop like compact's); pane resolution;
             copy-mode; an open dialog; a request-scoped CLEAR-SUPPRESSION
             check (the newest transcript marker is `cleared` with a
             timestamp NEWER than this request -- drop, never retry: #170's
             guard, now trivial because there is no heuristic re-arm left
             to fight); a TRI-STATE
             already-armed check (`True` -> nothing to do, drop; `None` ->
             undeterminable, leave pending; `False` -> proceed -- this is
             what makes a race between the callback and the user's own
             manual paste of the printed line benign, and protects a
             foreign manually-armed goal from being clobbered); then the
             pane's boundary is classified and the payload is delivered via
             the SAME shared primitives every other keystroke-sending job
             in this file already uses (`deliver_with_stash` for a foreign
             draft, `_send_goal_verified` -- moved here verbatim, together
             with its `_await_typed` helper -- for a bare box).

             Deliberately NOT gated on recent-human-activity
             (`_goal_autoarm_recent_human_activity`, #392/#398): the
             request's own origin IS the user having just typed
             `/autopilot`, so applying that 30-minute window to arm
             delivery would refuse essentially every legitimate arm for
             the first 30 minutes of every single invocation -- a
             structurally-always-refuses bug, not a safety net. That gate
             stays exactly where it already earns its keep: the
             lane-occupancy nudge below, a genuinely watchdog-INITIATED
             action.

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
             free. See `goal_dark_watch()` below.

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

import json
import os
import time
from pathlib import Path

import watchdog
from watchdog import compact as _compact
from watchdog import one_glance as _one_glance          # #486 G3
from watchdog import session_status as _session_status  # #486 G3 (reaper)


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
    frozen module-level constant (mirrors `compact.compact_requests_path`)."""
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


def record_goal_request(session, cwd, text, authority, now=None, path=None,
                        origin=None):
    """Record a pending `/goal` arm request for `session`. TWO writers since
    #478: the user's `goal-arm --self` callback (origin "self-callback") AND
    `goal_dark_watch`'s auto-re-arm (origin "dark-rearm"). Overwrites any
    earlier pending request for the SAME session, with two protections a
    single-writer store never needed (#478 adversarial-review MAJOR,
    mirroring the identical `compact.record_compact_request` #402-review
    MAJOR-1 fix):

      * DOWNGRADE REFUSED — a watchdog `dark-rearm` NEVER overwrites a
        still-pending entry from any OTHER origin. A pending request means a
        delivery is already being attempted; clobbering the user's own
        `self-callback` arm with the watchdog's guess would replace its
        text/authority AND subject the user's explicit arm to the
        recent-human gate (which the active user always trips) -> silent
        expiry of the user's arm. The prior entry is kept entirely intact.
      * `ts` is otherwise the #400 non-refreshable age-cap anchor: set ONCE
        on create, preserved on every same-origin re-record (a request whose
        anchor can be refreshed by an ordinary automatic re-record never
        ages out). The ONE exception is an UPGRADE from `dark-rearm` to a
        real user callback: an explicit user `/autopilot` is a genuinely new
        request and gets a FRESH ts, so it is never judged expired against
        the stale watchdog anchor -- and it cannot drive the #400
        refresh-forever shape because it needs a human action each time.

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

    # DOWNGRADE REFUSED: never let a dark-rearm clobber a still-pending entry
    # of a different (user/self-callback) origin.
    if prior is not None and new_origin == _GOAL_REARM_ORIGIN \
            and prior_origin != _GOAL_REARM_ORIGIN:
        return True                              # user's arm stands, untouched

    # UPGRADE (dark-rearm -> a real user callback) gets a FRESH ts anchor;
    # every other re-record preserves the #400 non-refreshable anchor.
    upgrade_from_rearm = (prior_origin == _GOAL_REARM_ORIGIN
                          and new_origin and new_origin != _GOAL_REARM_ORIGIN)
    if isinstance(prior, dict) and prior.get("ts") is not None \
            and not upgrade_from_rearm:
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


def goal_template_for_authority(authority, path=None):
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
    would be rejected by Claude Code itself must never be typed at all)."""
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
                return None
            return line
    return None


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
    decision -- the ONE call site (`deliver_goal`) that ever writes here.
    Never raises. Bounded to the last `GOAL_SYNC_LOG_LINES_MAX` lines.
    Collapses an identical repeat of the log's own last line (content only,
    ignoring the timestamp) into a timestamp refresh instead of a duplicate
    append."""
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


def _send_goal_verified(pid, text, run, captured=None, sleep_fn=None, logs=None):
    """Type a LONG `/goal ...` into a BARE input box and submit it,
    verifying every step against a fresh capture -- the same protocol
    `deliver_with_stash` uses for its own type/submit steps, minus the
    stash (there is no draft here).

    NEVER presses Enter after a type-verify failure. NEVER sends two
    consecutive Escapes (#35). Returns True only when the box is provably
    empty again after the submit.

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
    if watchdog._strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    fresh = watchdog.capture_pane(pid, run, lines=40)
    if watchdog._input_line_text(fresh) != "":
        watchdog._draft_rescue_persist(pid, fresh, logs=logs)
        _log("goal-verify-abort: raced-busy")
        return False                       # raced -- a draft appeared since the caller's own check
    watchdog._type_literal(pid, run, text, sleep_fn)
    if not _await_typed(pid, text, run, sleep_fn, want=True):
        if watchdog._pane_shows_collapsed_paste(
                watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=40))):
            _log("goal-verify-abort: collapsed-paste")
        return False                       # never rendered -- never submit it
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    if _await_typed(pid, text, run, sleep_fn, want=False):
        # STILL in the box after the same bounded settle window -- a
        # genuinely swallowed submit. ONE corrective Escape+Enter, never a
        # second bare Enter, never two Escapes.
        run(["tmux", "send-keys", "-t", pid, "Escape"])
        run(["tmux", "send-keys", "-t", pid, "Enter"])
        if _await_typed(pid, text, run, sleep_fn, want=False):
            watchdog._undo_and_release_slot(pid, run, text, False, _log,
                                            "goal-verify-abort: "
                                            "swallowed-submit-not-recovered",
                                            sleep_fn=sleep_fn)
            return False
    return True


# --------------------------------------------------------------------------- #
# The ONE delivery function.
# --------------------------------------------------------------------------- #

_GOAL_TERMINAL_WORDS = frozenset((
    "sent", "expired", "drop:cleared-after-request", "drop:already-armed",
    "drop:stale-rearm",   # #524 -- a dark-rearm too old to type (delivery gate)
))


def deliver_goal(sid, cwd, text, authority, run=None, projects_dir=None,
                 now=None, state=None, request_ts=None, send_fn=None,
                 dry_run=False, sleep_fn=None, logs=None, origin=None):
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
      "drop:already-armed"          -- nothing to do; some goal (this
                                        request's own earlier delivery, a
                                        manual paste, or a foreign one) is
                                        already armed.
      "drop:stale-rearm"            -- #524: a `dark-rearm`-origin request
                                        older than GOAL_DARK_REARM_STALE_S;
                                        the dark read it acted on has gone
                                        stale, so never type it late.
      "skip:<reason>"               -- not safe right now; the caller
                                        LEAVES the request pending for the
                                        next periodic sweep.

    Deliberately does NOT check `_goal_autoarm_recent_human_activity` for
    the normal (user-`/autopilot`) origin -- see this module's own header
    docstring for why that would be a structurally-always-refuses bug here,
    not a safety net. The #478 auto-re-arm origin (`origin=="dark-rearm"`)
    is the exception: it IS watchdog-initiated, so it DOES honour that gate
    (`skip:recent-human`) exactly like the lane nudge."""
    now = now if now is not None else time.time()
    if watchdog._owner_disabled("goal"):
        _log_goal_sync("SKIP disabled-by-owner sid=%s cwd=%s" % (sid, cwd))
        return "skip:disabled"
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    sleep_fn = sleep_fn or time.sleep

    # Hard age cap -- checked first, no pane resolution needed. Unlike
    # compact, an expired goal-arm is not harmless: PING once (deduped on
    # session+request-ts, so a later fresh request gets its own chance).
    if request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > GOAL_REQUEST_MAX_AGE_S:
            _log_goal_sync("SKIP expired sid=%s cwd=%s" % (sid, cwd))
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

    # #524 -- a dark-rearm decision goes stale FAST: goal_dark_watch recorded it
    # from a dark READ, and the loop's dark/idle state changes within a sweep or
    # two. A dark-rearm not delivered within GOAL_DARK_REARM_STALE_S is acting on
    # outdated darkness -> DROP (terminal), never type it late (this is the H1
    # "stale request delivered late" concern from #524, closed on the ONE origin
    # that can spontaneously TYPE). The 30-min age cap above stays the generic
    # backstop; this is the tighter, dark-rearm-only freshness gate.
    if origin == _GOAL_REARM_ORIGIN and request_ts is not None:
        age = _safe_age(now, request_ts)
        if age is not None and age > GOAL_DARK_REARM_STALE_S:
            _log_goal_sync("DROP-AT-DELIVERY:stale-request sid=%s cwd=%s age=%ds"
                           % (sid, cwd, int(age)))
            return "drop:stale-rearm"

    pid = _compact._find_pane_for_session(sid, cwd, run=run, projects_dir=projects_dir)
    if not pid:
        _log_goal_sync("SKIP no-pane sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-pane"
    if watchdog.pane_in_mode(pid, run):
        _log_goal_sync("SKIP in-mode sid=%s cwd=%s" % (sid, cwd))
        return "skip:in-mode"
    captured = watchdog.capture_pane(pid, run, lines=40)
    if watchdog.pane_waiting_on_user(captured):
        _log_goal_sync("SKIP dialog-open sid=%s cwd=%s" % (sid, cwd))
        return "skip:dialog-open"

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
        # already required an active transcript).
        if origin == _GOAL_REARM_ORIGIN:
            recent, reason = watchdog._goal_autoarm_recent_human_activity(
                sid, tpath, now)
            if recent:
                _log_goal_sync("SKIP recent-human(dark-rearm) sid=%s cwd=%s -> %s"
                               % (sid, cwd, reason))
                return "skip:recent-human"
    elif origin == _GOAL_REARM_ORIGIN:
        # #478 review MINOR — no active transcript (a delete/archive race
        # between pane resolution's own transcript match and this re-query)
        # means the recent-human gate cannot run. For the ONE watchdog-
        # INITIATED origin, refuse on unprovable state rather than type
        # blind. Non-terminal "skip:" -> stays pending; a later sweep (or the
        # 30-min age cap) resolves it.
        _log_goal_sync("SKIP no-transcript(dark-rearm) sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-transcript"

    # Tri-state already-armed check.
    armed = watchdog.pane_goal_armed(captured)
    if armed is True:
        _log_goal_sync("DROP already-armed sid=%s cwd=%s" % (sid, cwd))
        return "drop:already-armed"
    if armed is None:
        _log_goal_sync("SKIP undeterminable sid=%s cwd=%s" % (sid, cwd))
        return "skip:undeterminable"

    kind, draft = watchdog._classify_boundary(captured)
    if kind == "no-input-line":
        _log_goal_sync("SKIP no-input-line sid=%s cwd=%s" % (sid, cwd))
        return "skip:no-input-line"
    if kind == "busy":
        _log_goal_sync("SKIP busy sid=%s cwd=%s" % (sid, cwd))
        return "skip:busy"

    if draft:
        # Mark provenance BEFORE the attempt (regardless of outcome) so
        # the shared janitor (#372) can recover a stuck stash send for
        # THIS pane -- mirrors the bare-box branch immediately below
        # (#403-review MAJOR M1: this branch used to mark only on success
        # and never clear, exactly backwards).
        watchdog._janitor_mark_watch(state, pid, now)
        # #488: thread `state` so deliver_with_stash can DURABLY record a park
        # it definitively creates (STASH_PARKED) -> the shared janitor reclaims
        # it after ANY delay, not just the 6h generic-mark window (the gk
        # `(1d)` gap). The record is written ONLY on an unambiguously-ours park
        # (slot was free before our own C-s), never a pre-existing foreign one,
        # and deliver_with_stash clears it on its own verified success.
        ok = watchdog.deliver_with_stash(pid, text, run, captured=captured,
                                         logs=logs, sleep_fn=sleep_fn,
                                         state=state)
        if ok:
            watchdog._janitor_clear_watch(state, pid)
            _log_goal_sync("SEND stash sid=%s cwd=%s" % (sid, cwd))
            return "sent"
        _log_goal_sync("SKIP stash-abort sid=%s cwd=%s" % (sid, cwd))
        return "skip:stash-abort"

    # Bare box -- verified typed send. Mark provenance BEFORE typing so the
    # shared janitor (#372) can recover a stuck send for THIS pane.
    watchdog._janitor_mark_watch(state, pid, now)
    ok = _send_goal_verified(pid, text, run, captured=captured,
                             sleep_fn=sleep_fn, logs=logs)
    if ok:
        watchdog._janitor_clear_watch(state, pid)
        _log_goal_sync("SEND typed sid=%s cwd=%s" % (sid, cwd))
        return "sent"
    _log_goal_sync("SKIP verify-failed sid=%s cwd=%s" % (sid, cwd))
    return "skip:verify-failed"


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


def goal_sweep(now, run=None, dry_run=False, projects_dir=None,
              requests_path=None, state=None, handled=None, send_fn=None,
              sleep_fn=None):
    """The periodic re-evaluation of every PENDING goal-arm request (job
    9's new body -- replaces the old arm-question viewport scan and virgin-
    candidate heuristic entirely). Re-checks each still-pending request's
    SAME unmodified conditions every sweep; nothing here overrides a hard
    condition. A request that keeps failing a condition simply sits until
    it clears (delivered next sweep) or the age cap discards it.

    `handled` (optional, a `set()`): the SAME per-sweep set job 14/20's
    lane-occupancy nudge populate -- a sid already sent a keystroke burst
    THIS sweep (by /compact or the lane nudge) is skipped, never double-
    typed into."""
    logs = []
    if watchdog._owner_disabled("goal"):
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
        return logs
    reqs = load_goal_requests(requests_path)
    for sid, entry in list(reqs.items()):
        if not isinstance(entry, dict):
            continue
        cwd = entry.get("cwd", "")
        text = entry.get("text", "")
        authority = entry.get("authority", "")
        if not text:
            # malformed/legacy entry -- nothing to type; drop rather than
            # retry forever on an empty payload.
            clear_goal_request(sid, path=requests_path)
            continue
        if handled is not None and sid in handled:
            logs.append("SKIP (goal-sweep) sid=%s -> handled this sweep already"
                        % sid)
            continue
        if dry_run:
            logs.append("DRY-RUN goal-sweep would evaluate sid=%s" % sid)
            continue
        word = deliver_goal(sid, cwd, text, authority, run=run,
                            projects_dir=projects_dir, now=now, state=state,
                            request_ts=entry.get("ts"), send_fn=send_fn,
                            dry_run=dry_run, sleep_fn=sleep_fn,
                            origin=entry.get("origin"))
        if word in _GOAL_TERMINAL_WORDS:
            clear_goal_request(sid, path=requests_path)
        if word == "sent":
            logs.append("OK (goal-sweep) sid=%s -> sent" % sid)
            if handled is not None:
                handled.add(sid)
        elif word == "expired":
            logs.append("LAPSE (goal-sweep) sid=%s (age > cap, discarded)" % sid)
        else:
            logs.append("SKIP (goal-sweep) sid=%s -> %s" % (sid, word))
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
# The FIRST ping stays byte-for-byte as #403 shipped it; a persistently-dark
# goal is then RE-pinged on a widening schedule, but ONLY while the per-cwd
# tickets-status obligation cache proves work still remains (open > 0) -- an
# ACHIEVED loop is transcript-indistinguishable from a stalled one (both
# mark=set / footer dark; achievement persists NO marker, measured over 8329
# transcripts), so an ungated re-ping would nag every completed backlog, the
# exact noise class the user purged. No positive confirmation -> stay silent
# (the first ping already went out). Zero keystrokes, ever. Tunable; mirrors
# the #399/#353 staged-alarm constant style.
GOAL_DARK_REPING_SCHEDULE_S = (3600, 3 * 3600, 6 * 3600, 24 * 3600)
GOAL_DARK_REPING_MAX = 10               # hard cap on total pings per dark episode
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
GOAL_DARK_REARM_MAX_PER_DAY = 2         # attempt cap: max auto-types per sid / 24h
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


def _dark_rearm_attempt_ok(attempts, now):
    """Pure per-sid attempt cap (#524): at most GOAL_DARK_REARM_MAX_PER_DAY
    auto-types per session per rolling 24 h, so a typed `/goal` that does not
    stick can never become a keystorm. `attempts` is the prior list of type
    timestamps (a JSON list; any non-number entry is dropped). Returns
    `(ok, pruned)` -- `ok` False once the cap is hit; `pruned` is the list with
    entries older than 24 h removed (the caller appends `now` only on a real
    type). The cap counts CONFIRMED RECORDS, not landed keystrokes: a record
    that `deliver_goal` later drops as `drop:stale-rearm` still consumed a slot
    without any `/goal` typed -- this fails SAFE (fewer real re-arms than the
    cap allows, so the ping escalates sooner) and correlates with a present
    human, so it is deliberate, not a defect."""
    day = 24 * 3600
    pruned = [t for t in (attempts or [])
              if isinstance(t, (int, float)) and 0 <= (now - t) <= day]
    return (len(pruned) < GOAL_DARK_REARM_MAX_PER_DAY), pruned


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
    attempts_state[sid] = list(attempts_state.get(sid) or []) + [now]
    confirm_state.pop(sid, None)       # run consumed by the type
    pinged_state.pop(sid, None)        # episode resolved by the type
    record_goal_request(sid, cwd, text, auth, now=now,
                        origin=_GOAL_REARM_ORIGIN, path=requests_path)
    return ("dark-watch %s sid=%s -> CONFIRMED-DEAD: recording re-arm "
            "(open=%s authority=%s reads=%s span=%ss attempt=%d)"
            % (loc, sid, open_n, auth, reads, span, len(attempts_state[sid])))


def _goal_dark_reping_due(prev, now, schedule):
    """Pure staged-schedule check for a dark-goal RE-ping (#459), mirroring
    the `_gkreq_reping_due` SHAPE (#353/#352) as an INDEPENDENT function
    reusing that proven pattern per this repo's own 'same shape, own
    vocabulary' precedent, never a cross-job call. `prev` is a prior ping
    record with `count`>=1 and `last`. Returns `(due, next_count)`: too soon
    -> `(False, count)`; the schedule interval for this stage cleared ->
    `(True, count + 1)`. Holds at the final stage forever. A record with no
    readable `last` re-pings once (fail toward reminding), never silently
    sticks."""
    count = int(prev.get("count") or 1)
    last = prev.get("last")
    if last is None:
        return True, count + 1
    step = min(count - 1, len(schedule) - 1)
    if (now - last) < schedule[step]:
        return False, count
    return True, count + 1


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
        text = goal_template_for_authority(authority)
    except Exception:
        text = None
    return (text or None), authority


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
    falls to the #459 ping. This function STILL types nothing -- it only WRITES
    the request; the keystroke + its recent-human + delivery-freshness gates
    live in `deliver_goal`. A user-CLEARED goal (mark != "set") never reaches
    this branch at all."""
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
        jlogs = watchdog._janitor_recover(run, jrec, pid, cwd, captured, loc,
                                          send_fn, dry_run, sleep_fn,
                                          state=state, now=now)
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

        rec = off_state.get(sid)
        off = rec.get("off") if isinstance(rec, dict) else None
        prior_mark = rec.get("mark") if isinstance(rec, dict) else None
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
        off_state[sid] = {"off": new_off, "mark": mark, "tmtime": tmtime}

        # #522 -- honour the disarm-on-question veto (see `_qdisarm_veto`): never
        # re-arm / accumulate / ping a loop just deliberately cleared for a stuck ❓.
        vetoed, vlog = _qdisarm_veto(qveto, sid, tpath, now, human_ts_fn, loc)
        if vlog:
            logs.append(vlog)
        if vetoed:
            continue

        armed = watchdog.pane_goal_armed(captured)

        if mark is None or mark.get("state") != "set":
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
        if workable:
            rearm_text, rearm_auth = (rearm_fn or _default_rearm_fn)(cwd)
            attempt_ok, attempts_state[sid] = _dark_rearm_attempt_ok(
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
            logs.append(
                "dark-watch %s sid=%s -> ATTEMPT-CAP: %d auto-types in 24h, "
                "ping only" % (loc, sid, GOAL_DARK_REARM_MAX_PER_DAY))

        # #459 ping FALLBACK (staged schedule): reached for a NON-workable dark
        # backlog, an unresolvable template, OR an exhausted attempt cap -- the
        # cases the watchdog cannot self-heal, so the human must act. The FIRST
        # ping fires ALWAYS; a LATER re-ping needs a fresh workable cache -- else
        # stay SILENT (an achieved loop is transcript-identical to a stall).
        prec = pinged_state.get(sid)
        is_first = not (isinstance(prec, dict) and prec.get("mark_ts") == mark_ts)
        if is_first:
            count = 1
        else:
            count = int(prec.get("count") or 1)
            if count >= GOAL_DARK_REPING_MAX:
                continue                          # hard cap -- stop for this episode
            due, count = _goal_dark_reping_due(prec, now,
                                               GOAL_DARK_REPING_SCHEDULE_S)
            if not due:
                continue                          # too soon this stage

        if not is_first and not workable:
            continue                              # can't confirm work -- no nag
        pinged_state[sid] = {"mark_ts": mark_ts, "count": count, "last": now}
        logs.append(
            "dark-watch %s sid=%s -> %s" % (
                loc, sid,
                "goal died silently, pinging" if count == 1
                else "goal still dark, re-pinging #%d" % count))
        if send_fn is not None and not dry_run:
            from notify import stream_redirect
            proj = watchdog.project_label(cwd)
            if count == 1:
                msg = ("\U0001f480 **%s** — /goal loop zomrelo potichu "
                       "(transkript hovorí armovaný, footer nie). "
                       "Spústi prosím `/autopilot` znova." % proj)
            else:
                msg = ("\U0001f480 **%s** — /goal loop je STÁLE mŕtvy "
                       "(pripomienka #%d; transkript hovorí armovaný, footer "
                       "nie). Spústi prosím `/autopilot` znova." % (proj, count))
            # The FIRST ping keeps #403's exact dedup_key (goal-dark:sid:mark) so
            # a legacy disk marker written by pre-#459 code never yields a
            # duplicate first ping across the deploy boundary; re-pings append
            # :count so each staged reminder delivers on its own.
            dkey = ("goal-dark:%s:%d" % (sid, int(mark_ts or 0)) if count == 1
                    else "goal-dark:%s:%d:%d" % (sid, int(mark_ts or 0), count))
            send_fn(
                msg,
                owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                dedup_key=dkey,
                dry_run=dry_run)

    if not dry_run:   # #519 -- prune goal_mark for gone+aged sessions (dry-run: no state mutation)
        _prune_goal_mark_orphans(off_state, visited_sids, now)
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


def _deliver_goal_clear(pid, text, run, captured, state, now, sleep_fn, logs):
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
    `_QDISARM_TRANSIENT_SKIPS`); 'sent'/'skip:verify-failed' mean a type was
    attempted (consumes an attempt-cap slot -- the #524 fail-safe)."""
    kind, draft = watchdog._classify_boundary(captured)
    if kind == "no-input-line":
        return "skip:no-input-line"
    if kind == "busy":
        return "skip:busy"
    if draft:
        return "skip:draft"          # user is composing -- never disturb, retry next sweep
    watchdog._janitor_mark_watch(state, pid, now)
    ok = _send_goal_verified(pid, text, run, captured=captured,
                             sleep_fn=sleep_fn, logs=logs)
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

        # (4) CONFIRMED stuck -- gate the keystroke.
        recent, reason = watchdog._goal_autoarm_recent_human_activity(
            sid, tpath, now)
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
            logs.append("qrepoke %s sid=%s -> CONFIRMED stuck but ATTEMPT-CAP "
                        "(%d/24h) -- ping-free skip" % (loc, sid,
                                                        GOAL_QDISARM_MAX_PER_DAY))
            continue
        if dry_run:
            logs.append("qrepoke %s sid=%s -> CONFIRMED stuck (%d re-pokes) -- "
                        "would disarm (dry-run)" % (loc, sid, streak))
            continue
        word = _deliver_goal_clear(pid, GOAL_CLEAR_TEXT, run, captured, state,
                                   now, sleep_fn, logs)
        if word in _QDISARM_TRANSIENT_SKIPS:
            logs.append("qrepoke %s sid=%s -> disarm deferred (%s), retry next "
                        "sweep" % (loc, sid, word))
            continue
        attempts[sid] = pruned + [now]        # a type was attempted -> consume a slot
        if word == "sent":
            qveto[sid] = {"disarmed_ts": now, "streak": streak}
            logs.append("qrepoke %s sid=%s -> DISARMED: /goal clear typed "
                        "(%d re-pokes, attempt=%d/%d)"
                        % (loc, sid, streak, len(attempts[sid]),
                           GOAL_QDISARM_MAX_PER_DAY))
        else:
            logs.append("qrepoke %s sid=%s -> disarm delivery FAILED (%s, "
                        "slot consumed)" % (loc, sid, word))
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
GOAL_LANE_INTERVAL_S = 15 * 60
GOAL_LANE_MAX_NUDGES = 2
GOAL_LANE_LIVE_WINDOW_S = 15 * 60

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
# annoyance stays bounded by GOAL_LANE_INTERVAL_S + GOAL_LANE_MAX_NUDGES
# regardless.
GOAL_LANE_LIVE_CONVO_S = 3 * 60

# #442-review F2 -- bound on CONSECUTIVE zero-progress stash aborts. A
# "transient, retry next sweep" abort that recurs every ~60s forever (the
# classic shape: the stash slot is occupied by the user's OWN parked
# draft, which no janitor provenance will ever clear) is the repo's
# known bounded-consecutive-occurrence class: without a bound, the
# give-up ping is structurally unreachable (the nudge counter only
# advances on SUCCESS), so a permanently-aborting lane would silently
# retry -- and for keystroke-bearing abort shapes, retype -- forever.
# Past this many consecutive aborts the existing give-up branch fires
# its one-shot ping and stops attempting; the counter clears on any
# successful delivery and on the session-active idle reset.
GOAL_LANE_MAX_STASH_ABORTS = 5

# #479 -- escalating backoff for a lane whose stash delivery keeps ABORTING
# against the SAME persistently-parked live draft. The single reactions were
# already correct (never overwrite a live draft, rescue before any keystroke);
# what was missing was REPETITION damping. The abort branch only bumped the
# consecutive-abort counter and returned "retry next sweep", so a stash slot
# held by the user's OWN parked draft was re-typed + re-rescued every ~60s
# sweep for hours (the 2026-08-14 storm: 15:18->15:19->15:20->15:21) until the
# give-up at MAX aborts. A stash-abort now PARKS the next attempt for a
# widening window in durable `rec['lnpark']`; within it the nudge skips
# WITHOUT touching the pane at all. Mirrors the repo's staged-schedule PATTERN
# (`WORKING_RESPONDED_BACKOFF_SCHEDULE_S`, `_gkreq_reping_due`) -- an explicit
# tuple of widening intervals, `min(n-1, len-1)` indexing, holding at the cap
# stage forever. The refusal itself is NEVER weakened: deliver_with_stash
# still refuses the live draft, the give-up ping is still reached (just over
# elapsed time, not once per sweep), and the park clears on any successful
# delivery and on the session-active idle reset.
GOAL_LANE_STASH_ABORT_BACKOFF_S = (120, 300, 900, 1800)


def _lane_stash_abort_backoff(aborts):
    """Seconds to park the lane's next stash-delivery attempt after its
    `aborts`-th consecutive abort (1-indexed). Widens with each abort and
    holds at the final stage forever -- see GOAL_LANE_STASH_ABORT_BACKOFF_S."""
    sched = GOAL_LANE_STASH_ABORT_BACKOFF_S
    idx = min(max(int(aborts), 1) - 1, len(sched) - 1)
    return sched[idx]


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


# #442 -- the text TEACHES the fleet-dispatch doctrine
# (skills/autopilot/SKILL.md: parallel worktree workers, the account-wide
# concurrent-agent cap of 8, serialize-only integration), never just a
# bare "dispatch more workers" poke.
GOAL_LANE_NUDGE_TEXT = (
    "lane-check: backlog=%d OTVORENÝCH tiketov (nie všetky musia byť hneď "
    "rozpracovateľné — zadržané zelené vetvy, časť v cudzom repe či zastrešujúce "
    "NErátaj; dispatchni len naozaj workable), no BEŽÍ 0 dispatched "
    "workerov (žiadny live subagent transcript; waiterov beží: %d). "
    "Prázdne implementačné lány nie sú dôvod na nečinnosť ani počas "
    "čakania na CI/waiter. Postupuj podľa FLEET doktríny skills/autopilot "
    "SKILL.md: dispatchni TERAZ ďalšie batchnuté tickety ako PARALELNÝCH "
    "isolation:\"worktree\" autopilot-worker subagentov "
    "(run_in_background), drž ACCOUNT-WIDE cap 8 súbežných agentov "
    "(workeri + validatory + review dispatche SPOLU) a integruj výhradne "
    "SÉRIOVO — jeden merge/CI/push za celé kolo."
)

# #442 re-fix 2 (REOPEN č.2 + owner directives 2026-08-14) -- the COUNT-based
# fill-the-cap widening. The empty-lane nudge above (0 workers) fires as before;
# this threshold adds the UNDER-SATURATED case (some workers, but fewer than the
# fill floor, while any backlog still waits). Owner (#456 "DOPLNENIE
# ROZHODNUTIA"): "kludne ovela viac subagentov moze paralelne pracovat" -- so the
# floor is 5, not a small 3, and the nudge TEXT frames saturation as WORK-DRIVEN
# (every independent workable ticket/review/release step earns a lane; a
# CI-waiting worker never blocks further dispatch), not a fixed target.
# #481: the fill floor is `min(GOAL_LANE_SATURATION_WORKERS, backlog)` -- a
# small-but-real backlog (2-10 workable) with idle lanes is filled up to the
# backlog, not just once it passes a fixed 10-ticket gate, matching the owner's
# `active_workers < min(5, workable_backlog)`. GOAL_LANE_SATURATION_WORKERS is a
# pragmatic UPPER cap on WHEN the nudge stops firing, never a "fill exactly this
# many" target.
GOAL_LANE_SATURATION_WORKERS = 5      # fill floor caps here: >= min(5, backlog) live workers -> saturated

# #442 re-fix 2 (owner directive 2026-08-14: "vsak ak na to mas watcher tak ten
# si vie aj pamet na boxe overit") -- the fill-lanes nudge (which tells the
# supervisor to dispatch MORE parallel workers) fires only when the box has real
# memory headroom. Below this many MB of MemAvailable, another worktree worker
# risks tipping a memory-tight box into the #448 pressure-reap zone (a reaped bg
# shell / OOM-killed worker is worse than a briefly under-filled lane), so the
# guard stays silent and journals the measured value. ~1.5 GB: comfortably above
# a single worker's steady-state footprint plus headroom for its subprocesses,
# and well clear of the pressure zone where #448's reaper starts culling. The
# 0-worker EMPTY-lane nudge is UNAFFECTED -- a fully stalled box must always be
# nudged regardless of memory.
GOAL_LANE_MIN_MEM_AVAIL_MB = 1536

# #442 re-fix 2 -- the UNDER-SATURATED (non-zero worker) nudge text. Distinct
# from GOAL_LANE_NUDGE_TEXT (which says "0 dispatched workerov"): here 1-4 workers
# ARE running, so the text names the real count and frames saturation as
# work-driven, not a fixed number. #481: also names the target lane floor
# (min(5, backlog)) alongside the seen worker count. Args: (live_workers, floor,
# backlog_n, waiters).
GOAL_LANE_UNDERSAT_NUDGE_TEXT = (
    "lane-check: beží len %d z cieľových %d lán (floor=min(5, backlog)), no "
    "čaká %d OTVORENÝCH tiketov (nie všetky hneď rozpracovateľné — zadržané "
    "zelené vetvy / cudzí repo / zastrešujúce NErátaj; dispatchni len workable) "
    "(waiterov beží: %d). Lány nie sú plné podľa "
    "PRÁCE: každý nezávislý "
    "workable tiket / review / release krok má dostať vlastnú PARALELNÚ lane, a "
    "worker čakajúci na CI NEblokuje ďalší dispatch. Nikdy nenechaj 1-2 workerov, "
    "kým sedí veľký backlog. Podľa FLEET doktríny skills/autopilot SKILL.md "
    "dispatchni TERAZ ďalšie batchnuté tickety ako PARALELNÝCH "
    "isolation:\"worktree\" autopilot-worker subagentov (run_in_background) — "
    "nasýtenie je podľa PRÁCE, nie fixného počtu, až po ACCOUNT-WIDE hard strop "
    "8 súbežných agentov (workeri + validatory + review dispatche SPOLU) — a "
    "integruj výhradne SÉRIOVO, jeden merge/CI/push za celé kolo."
)


def _mem_available_mb():
    """MemAvailable from /proc/meminfo in MEGABYTES, or None if it can't be
    read/parsed (#442 re-fix 2). Fail-OPEN (None -> the caller does NOT block the
    nudge): this guard's whole purpose is that a stalled box is too SILENT, and
    every managed box is Linux with /proc/meminfo -- an unreadable meminfo is an
    anomaly that must not re-silence the guard, so only the memory PROTECTION is
    skipped, never the nudge itself."""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return None
    return None


# #509 -- SURPLUS FLOOR for the UNDER-SATURATED (fill-more-lanes) nudge. The
# workable-backlog count (core-quals/slice-quals) over-represents genuinely-
# DISPATCHABLE units -- held green branches awaiting CI, umbrella/tracking
# tickets, ops-wait / evidence-gated items (e.g. #499) the count cannot
# structurally distinguish yet -- so `live_workers < min(5, backlog)` is true even
# when there is nothing real to lift (the live incident: the guard pushing for a
# 5th lane against a workable count that could not fill it, ~8 nudges/night). Push
# for MORE lanes only when the backlog exceeds the running lanes by this margin:
# with 1-4 workers this lands the absolute floor at 6-9, matching the owner's
# "~10+ open for the target" intuition, while NEVER silencing the stalled 0-worker
# EMPTY-lane branch (anti-silence -- it ignores this floor entirely). The
# effectiveness backoff below handles the residual (a real surplus that still
# can't be lifted).
GOAL_LANE_UNDERSAT_SURPLUS = 5

# #509 -- effectiveness (feedback) backoff for the UNDER-SATURATED fill nudge. The
# fixed 15-min cooldown re-nudged "fill more lanes" every 15 min for HOURS even
# when every nudge produced NO new lane (the supervisor answering "everything
# covered, nothing to lift" -- the live incident). A nudge is EFFECTIVE iff the
# STRUCTURED live-worker count (`count_live_workers` -- wedged-excluding, #486 G2,
# NEVER pane text) ROSE since it fired; an ineffective nudge widens the NEXT
# interval along this schedule, holding at the cap FOREVER (re-probes at the widest
# stage, never permanently silent -- #134 anti-silence). Mirrors the repo's
# staged-schedule PATTERN (GOAL_LANE_STASH_ABORT_BACKOFF_S / #502 limit-backoff):
# an explicit tuple of widening intervals, min(streak, len-1) indexing. The FIRST
# stage equals GOAL_LANE_INTERVAL_S so the first repeat is unchanged; the streak
# resets the moment a nudge DID produce a lane (the worker count ROSE) or the
# backlog GREW. A bare lane DROP does NOT reset -- a worker completing on an
# un-liftable backlog with nothing to replace it is the normal "nothing to lift"
# churn, and resetting on it would re-open the burn (#509 adversarial review). NO
# phone ping (unlike the
# stash-abort give-up): a fleet as full as its workable backlog allows is the
# healthy steady state, not an error -- pinging it would be the exact noise this
# fix removes; the journalled decision line every sweep is the anti-silence.
GOAL_LANE_INEFFECTIVE_BACKOFF_S = (15 * 60, 30 * 60, 60 * 60, 120 * 60)


def _lane_effective_interval(ineffective_streak):
    """#509 -- cooldown seconds before the next UNDER-SATURATED fill nudge, given
    the count of consecutive nudges that produced no new lane. Widens with the
    streak and holds at the final stage forever -- see
    GOAL_LANE_INEFFECTIVE_BACKOFF_S."""
    sched = GOAL_LANE_INEFFECTIVE_BACKOFF_S
    idx = min(max(int(ineffective_streak), 0), len(sched) - 1)
    return sched[idx]


def _lane_effectiveness(rec, eff_workers, backlog_n):
    """#509 -- did the LAST under-saturated nudge make PROGRESS worth re-probing?
    None when there is no comparable prior nudge (rec carries no baseline). Else
    True when a lane genuinely APPEARED (the structured live-worker count ROSE --
    the nudge worked) OR the backlog GREW (genuine new work): reset the streak.
    False otherwise -- workers flat, OR a lane DROPPED, AND the backlog did not
    grow -> the nudge produced no new lane, keep backing off.

    A bare DROP deliberately does NOT reset (#509 adversarial review, both
    reviewers converged): on a large un-liftable backlog a worker COMPLETING with
    nothing to replace it (count N->N-1) is the normal "nothing to lift" churn, so
    resetting on it would re-open the every-15-min burn this fix exists to kill.
    Genuinely-new work is caught by the backlog-grow arm; a freed lane the
    supervisor keeps declining to fill is not new dispatchable work, and the
    120-min cap re-probe bounds how long a newly-liftable backlog waits."""
    prev_w = rec.get("lnw")
    prev_b = rec.get("lnb")
    if prev_w is None or prev_b is None:
        return None
    return (eff_workers > prev_w) or (backlog_n > prev_b)


def _lane_cooldown_decision(rec, now, under_saturated, eff_workers, backlog_n,
                            loc, live_workers, waiters):
    """#509 -- effectiveness-aware cooldown gate. Returns (skip, logline, moved):
    whether to hold this sweep, its decision line, and the prior nudge's
    effectiveness verdict (`_lane_effectiveness`, handed back so the CALLER
    advances the streak ONLY on a real delivery, never a delivery abort).

    Empty-lane (not under_saturated): the original fixed GOAL_LANE_INTERVAL_S
    cooldown, unchanged -- that branch keeps its own GOAL_LANE_MAX_NUDGES give-up.

    Under-saturated: the interval widens along GOAL_LANE_INEFFECTIVE_BACKOFF_S per
    consecutive ineffective nudge; a nudge that MOVED the fleet resets the streak
    to 0 HERE (so the interval narrows back at once), holding at the cap forever --
    never permanently silent."""
    last = rec.get("llast")
    if last is None:
        return False, None, None
    if not under_saturated:
        if (now - last) < GOAL_LANE_INTERVAL_S:
            return True, ("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                          "skip:cooldown remaining=%ds"
                          % (loc, live_workers, waiters, backlog_n,
                             int(GOAL_LANE_INTERVAL_S - (now - last)))), None
        return False, None, None
    moved = _lane_effectiveness(rec, eff_workers, backlog_n)
    if moved is True:
        rec["lineff"] = 0
    streak = rec.get("lineff", 0)
    interval = _lane_effective_interval(streak)
    if (now - last) < interval:
        return True, ("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                      "skip:ineffective-backoff remaining=%ds "
                      "(streak=%d interval=%dm eff_workers=%d)"
                      % (loc, live_workers, waiters, backlog_n,
                         int(interval - (now - last)), streak, interval // 60,
                         eff_workers)), moved
    return False, None, moved


def _lane_record_nudge(rec, under_saturated, eff_workers, backlog_n, moved, n, now):
    """#479/#509 -- commit a LANDED nudge's state. Clears the abort-backoff (#479),
    and for the under-saturated fill nudge advances the #509 ineffective streak
    when the PREVIOUS nudge produced no new lane (`moved is False`) and records
    this nudge's effectiveness baseline (eff_workers/backlog for the next sweep to
    measure against), then stamps the nudge counter + cooldown clock. The advance
    lives HERE (a real delivery), never in the cooldown decision, so a delivery
    ABORT never over-advances the effectiveness streak (that has its own `lna`)."""
    rec.pop("lna", None)
    rec.pop("lnpark", None)
    # #511 -- a LANDED nudge means the delivery-mechanics failure that drove the
    # stash-abort give-up has genuinely cleared, so drop the one-shot give-up
    # ping flag: a genuinely-new future abort storm must be able to re-escalate
    # to the owner, never re-probe silently forever behind a stale lpinged.
    rec.pop("lpinged", None)
    if under_saturated:
        if moved is False:
            rec["lineff"] = rec.get("lineff", 0) + 1
        rec["lnw"] = eff_workers
        rec["lnb"] = backlog_n
    rec["ln"] = n + 1
    rec["llast"] = now


def _lane_clear_effectiveness(rec):
    """#509 -- drop the effectiveness-backoff baseline/streak. Called when the box
    is NOT under-saturated (fully saturated, or drained to 0 workers): the streak
    is an under-saturated concept, and a lane-count change to either extreme is
    itself a reset condition."""
    for k in ("lineff", "lnw", "lnb"):
        rec.pop(k, None)


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


def _lane_giveup_decision(rec, count_gaveup, aborts, loc, live_workers, waiters,
                          backlog_n, idle, cwd, sid, tmtime, pid, run, send_fn,
                          dry_run):
    """#442-review F2 / #511 -- the give-up branch, extracted to keep
    `goal_lane_occupancy_nudge` under its function-line cap (the #509
    "never grow the capped function" rule). Returns `(skip, logs)`:

    * The one-shot owner ping fires on the `lpinged` False->True transition for
      EITHER give-up kind (a permanently-aborting / fully-stalled lane must
      escalate to a human once).
    * `count_gaveup` (0-worker EMPTY-lane, fully-stalled box): a genuine
      PERMANENT give-up -> `skip=True` every sweep. Its counters DO reset at the
      reachable 0-worker idle branch once the box goes quiet, and a dead box
      needs a human.
    * stash-abort give-up (already pinged): `skip=False` -- the caller FALLS
      THROUGH to the #479 abort-backoff park, which re-probes delivery on the
      capped (30-min) schedule. This is the #511 fix: the stash-abort give-up's
      only reset (the 0-worker idle branch) is unreachable on an under-saturated
      box that never drains to 0 live workers, so the pre-#511 unconditional
      `return` left it permanently silent even after the wedged draft cleared
      and a huge surplus opened (gk 2026-08-16: lna=5/lpinged, park elapsed 10h,
      I 20 vs 2 workers, skip:gave-up every sweep for hours incl. across backlog
      GROWTH). Re-probing restores the #479/#502/#509 "hold at cap, re-probe
      forever, never permanently silent" invariant. The every-sweep decision
      contract is preserved downstream by the park's own skip:abort-backoff log
      (still parked) or the delivery attempt's log (park elapsed)."""
    logs = []
    if count_gaveup:
        why = ("ani po %d štúchnutiach sa lány nezaplnili"
               % GOAL_LANE_MAX_NUDGES)
        gave = "GAVE UP after %d nudges" % GOAL_LANE_MAX_NUDGES
    else:
        why = ("%d pokusov o doručenie štuchnutia za sebou zlyhalo "
               "(stash abort)" % aborts)
        gave = "GAVE UP after %d consecutive stash aborts" % aborts
    if not rec.get("lpinged"):
        if send_fn is not None and not dry_run:
            rec["lpinged"] = True
            from notify import stream_redirect
            # #442 re-fix 2: the give-up is now reachable in the
            # UNDER-SATURATED state (1-4 workers), so the text names the
            # real count instead of the old "nebeží ani jeden worker".
            send_fn("⚠️ **%s** — backlog=%d otvorených (nie všetky "
                    "rozpracovateľné), "
                    "`/goal` armovaný, ale %d min sa lány nezaplnili na "
                    "fill cap (beží len %d workerov, waiterov: %d) a %s "
                    "(%s). Pozri sa na reláciu, prosím."
                    % (watchdog.project_label(cwd), backlog_n,
                       int(idle // 60), live_workers, waiters, why, loc),
                    owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                    dedup_key="lanestall:%s:%d" % (sid, int(tmtime or 0)),
                    dry_run=dry_run)
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d "
                    "idle=%dm -> %s"
                    % (loc, live_workers, waiters, backlog_n, idle // 60, gave))
        return True, logs
    # The one-shot owner ping has already fired (`lpinged`).
    if count_gaveup:
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                    "skip:gave-up (already escalated, holding)"
                    % (loc, live_workers, waiters, backlog_n))
        return True, logs
    # #511 stash-abort give-up: re-probe -> fall through (skip=False).
    return False, logs


def _lane_pre_send_race(ok, fresh_armed, loc):
    """#486 G6 -- the pre-send race re-check over the FRESH capture, taken right
    before the keystroke. The STRUCTURED signal already gated this pane armed;
    this final RENDER read only VETOES a genuine change SINCE the sweep. Returns
    ``(should_skip, log_lines)`` (`log_lines` is a 0/1-element list).

    A readable footer that lost the glyph (`is False` = a real clear right now,
    the render is the freshest truth there) or a lost/unreadable input box
    (`not ok`) vetoes. An UNREADABLE footer (`None`, the #486 obscured case) NO
    LONGER vetoes: re-vetoing on the same undeterminable footer the structured
    gate just overrode is what re-silenced the incident one layer down. It
    proceeds, logged. The `is False` veto is also the safety net for a 60s-stale
    goal_mark "set" (a goal cleared THIS sweep leaves the footer readable without
    the glyph) and a stale heartbeat-True -- defense in depth."""
    if not ok:
        return True, ["skip raced (lane-occupancy) %s -> pane moved since "
                      "the sweep" % loc]
    if fresh_armed is False:
        return True, ["skip raced (lane-occupancy) %s -> footer readable, goal "
                      "cleared since the sweep" % loc]
    if fresh_armed is None:
        return False, ["race-check (lane-occupancy) %s -> render footer "
                       "unreadable; structured-armed stands" % loc]
    return False, []


def goal_lane_occupancy_nudge(now, run, rec, sid, cwd, pid, captured, tpath,
                              tmtime, loc, send_fn, dry_run, handled,
                              projects_dir, backlog_fetch=None, state=None,
                              sleep_fn=None):
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
    if authority != "full":
        # #475 deliberately silent: lane occupancy fills PARALLEL WORKTREE lanes,
        # which only a full-authority box has -- a reduced-authority sub-dev
        # stream has no lanes to fill, so this is structurally N/A, not a
        # decision. Logging it for every armed sub-dev pane every sweep would be
        # pure noise.
        return logs, False
    idle = now - (tmtime or now)
    # #442 THIRD GAP -- the old top-of-function idle gate returned HERE with
    # EMPTY logs whenever the transcript was fresh -- which a BUSY
    # under-saturated session ALWAYS is (turns spinning -> mtime fresh) -- so
    # it never reached the fill-the-cap decision and journalled nothing (the
    # reopen-3 root cause: gk 2 workers, I 32, guard silent 20+ min). The idle
    # requirement is now applied PER BRANCH below, and the session-active
    # give-up reset moved WITH it into the 0-worker branch: a busy
    # under-saturated session must NOT re-arm every sweep (#442-review F1 --
    # the reset here zeroed the stash-abort streak before it could reach its
    # cap, so the give-up was structurally unreachable on exactly the
    # population this fix newly enables).
    marker = watchdog.transcript_last_marker(tpath)
    if marker == "❓":
        _lane_skip(logs, loc, "skip:awaiting-user (❓ marker -- session blocked "
                              "on a question, never nudge)")
        return logs, False
    # #442 re-fix 2 (REOPEN č.2): worker PRESENCE is now a COUNT decision made
    # below (`live_workers`), so it can distinguish an UNDER-SATURATED box (1-4
    # workers + big backlog -> still nudge to fill the cap) from a saturated one
    # -- the whole point of the reopen. The old early-skip on
    # `_pane_has_bg_agent(captured)` made that impossible: with ANY visible worker
    # in the agent strip it returned True and skipped the entire path BEFORE
    # `live_workers` was even counted, so the count widening alone would never fire
    # on a live box (the exact reopen-2 root cause -- 2 visible workers -> strip
    # shows `◯ ...` rows -> skip here, never reaching the count check). It is folded
    # into the count via `_count_live_subagents` + a pane-strip corroboration floor
    # below. `pane_waiting_on_user` STAYS -- it is a genuine delivery-safety gate (a
    # blocking dialog occupies the input area, so there is no free prompt to deliver
    # into); worker presence is not. The `_boundary_ok` idle-prompt gate below still
    # refuses to type into a non-idle pane, and the 15-min supervisor-idle gate
    # above already prevents nudging right after a dispatch, so folding worker
    # presence into the count is safe.
    if watchdog.pane_waiting_on_user(captured):
        _lane_skip(logs, loc, "skip:blocking-dialog (a dialog/prompt occupies "
                              "the input area -- no free prompt to deliver into)")
        return logs, False
    if watchdog._pane_compacting(captured):
        _lane_skip(logs, loc, "skip:compacting (pane is mid-/compact, transient)")
        return logs, False
    if handled is not None and sid in handled:
        _lane_skip(logs, loc, "skip:already-handled (another sweep job already "
                              "delivered to this session this cycle)")
        return logs, False
    waiters = watchdog._pane_live_task_count(captured)
    if marker == "⏳" and waiters <= 0:
        logs.append("lane-occupancy %s waiters=%d -> skip:working-no-tasks "
                    "(⏳ marker but 0 live tasks -- session claims working, "
                    "defer)" % (loc, waiters))
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
    # (#486-G2 dangerous direction). It is the SAME structured count the #509
    # effectiveness signal already uses below, so the two now agree by construction.
    live_workers, _ev = watchdog.count_live_workers(
        projects_dir, cwd, sid, now, GOAL_LANE_LIVE_WINDOW_S)
    backlog_n = watchdog._cached_backlog_count(cwd, backlog_fetch, state, now)
    if not isinstance(backlog_n, int) or backlog_n <= 0:
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%r -> "
                    "no measurable open backlog, skip"
                    % (loc, live_workers, waiters, backlog_n))
        return logs, False
    # #442 re-fix 2 + #481 -- the fill-the-cap widening, on the PURE COUNTS the
    # guard already reads (live worker count + open backlog), no
    # transcript-content heuristic. Two nudge-worthy states:
    #   * EMPTY lanes (live_workers == 0) -- fires on ANY open backlog, exactly as
    #     before this widening; memory-exempt (a fully stalled box must always be
    #     nudged) and idle-gated (wait for quiet) below.
    #   * UNDER-SATURATED lanes (0 < live_workers < floor) with genuine memory
    #     headroom -- 1-4 workers while workable tickets sit unallocated should be
    #     filling the empty parallel worktree lanes up to the floor.
    # #481: floor = min(GOAL_LANE_SATURATION_WORKERS, backlog) -- the owner's
    # `active_workers < min(5, workable_backlog)`. This unifies the old fixed
    # `>= 5` cap + the `backlog > 10` hard gate: a small-but-real backlog (2-10
    # workable) with idle lanes is now filled up to the backlog, not just once it
    # passes 10. backlog_n is already >= 1 (the `<= 0` guard above returned), so
    # floor >= 1 and workers==0 is always < floor (the empty-lane branch stays
    # reachable). live_workers (dispatched SUBAGENT transcripts) already EXCLUDES
    # `waiters` (CC's bg-shell/monitor badge, a separate population) from the
    # count; a CI-waiting or finished-but-recent subagent still writes its
    # transcript, so live_workers is an honest OVER-estimate of genuinely-working
    # lanes -- which makes the guard CONSERVATIVE (nudges less, never over), the
    # safe direction. Anti-flap (#481 design): the 15-min per-fire cooldown + the
    # 3-min recent-human gate + the 15-min live-window's own completion-recency
    # (a just-merged worker stays counted through its integration window) + the
    # empty-lane idle gate -- no new debounce, reusing only state the guard reads.
    # At or above the floor = saturated = silent.
    mem_mb = None
    floor = min(GOAL_LANE_SATURATION_WORKERS, backlog_n)
    if live_workers >= floor:
        _lane_clear_effectiveness(rec)   # #509: filled -> re-probe fresh on next dip
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                    "saturated (>= %d workers), skip"
                    % (loc, live_workers, waiters, backlog_n, floor))
        return logs, False
    under_saturated = live_workers > 0
    if not under_saturated:
        _lane_clear_effectiveness(rec)   # #509: a lane-drop reset condition
    elif (backlog_n - live_workers) < GOAL_LANE_UNDERSAT_SURPLUS:
        # #509 SURPLUS FLOOR (under-saturated only; see GOAL_LANE_UNDERSAT_SURPLUS).
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                    "skip:surplus-floor (backlog-workers=%d < %d)"
                    % (loc, live_workers, waiters, backlog_n,
                       backlog_n - live_workers, GOAL_LANE_UNDERSAT_SURPLUS))
        return logs, True
    if under_saturated:
        # The fill-lanes nudge dispatches MORE parallel workers -- only fire when
        # the box has real memory headroom, else another worktree worker risks the
        # #448 pressure-reap zone on a memory-tight box. Measured on the box the
        # guard runs on (owner directive). The 0-worker empty-lane nudge is exempt
        # (a fully stalled box must always be nudged).
        mem_mb = _mem_available_mb()
        if mem_mb is not None and mem_mb < GOAL_LANE_MIN_MEM_AVAIL_MB:
            logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                        "skip:low-mem MemAvailable=%dMB (< %dMB)"
                        % (loc, live_workers, waiters, backlog_n, mem_mb,
                           GOAL_LANE_MIN_MEM_AVAIL_MB))
            return logs, True
    # #442 THIRD GAP -- the idle gate, now applied PER BRANCH (was a silent
    # top-of-function early-return that structurally excluded every busy
    # under-saturated session). The 0-worker EMPTY-lane branch keeps the
    # original 15-min idle requirement -- a box being actively typed into may
    # be mid-dispatch, so wait for quiet -- but now LOGS the skip with numbers
    # instead of returning empty logs. The UNDER-SATURATED fill-the-cap branch
    # has NO idle floor: firing exactly when the supervisor is spinning with
    # too few workers is the entire point, and delivery is still gated by
    # _boundary_ok (idle prompt), the two-capture draft-diff, the recent-human
    # window and the per-fire cooldown below (so a fresh transcript never spams
    # -- it just stops SILENTLY excluding the busy state).
    if not under_saturated and idle < GOAL_LANE_IDLE_S:
        # #442-review F1: the session-active give-up reset lives HERE now
        # (0-worker branch only), never at the top -- a BUSY under-saturated
        # session must NOT re-arm every sweep, or its stash-abort streak never
        # reaches its cap and the give-up stays unreachable (the give-up is only
        # ever evaluated at the idle prompt anyway, which is exactly here).
        if rec.get("ln") or rec.get("lna"):
            rec.update({"ln": 0, "lpinged": False, "lna": 0})
            rec.pop("lnpark", None)   # #479 -- clear abort backoff on reset
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d idle=%ds "
                    "-> skip:idle (empty-lane, < %dm since last transcript write)"
                    % (loc, live_workers, waiters, backlog_n, int(idle),
                       GOAL_LANE_IDLE_S // 60))
        return logs, False
    n = rec.get("ln", 0)
    aborts = rec.get("lna", 0)
    # #442 THIRD GAP -- the nudge-count give-up (GOAL_LANE_MAX_NUDGES) applies
    # ONLY to the 0-worker empty-lane branch: a truly stalled box gets a
    # bounded number of pokes then one give-up ping. The UNDER-SATURATED
    # fill-the-cap branch has NO permanent give-up -- a session that stays
    # under-saturated for hours must keep being pushed every
    # GOAL_LANE_INTERVAL_S (constant #365 anti-annoyance give-up structurally
    # disabled saturation enforcement). The stash-abort give-up (a delivery
    # that permanently FAILS) stays for BOTH branches -- it is a
    # delivery-mechanics bound, not a "stop nudging" decision.
    count_gaveup = (not under_saturated) and n >= GOAL_LANE_MAX_NUDGES
    stash_gaveup = aborts >= GOAL_LANE_MAX_STASH_ABORTS
    if count_gaveup or stash_gaveup:
        giveup_skip, giveup_logs = _lane_giveup_decision(
            rec, count_gaveup, aborts, loc, live_workers, waiters, backlog_n,
            idle, cwd, sid, tmtime, pid, run, send_fn, dry_run)
        logs += giveup_logs
        if giveup_skip:
            return logs, True
        # #511 -- the STASH-ABORT give-up did NOT return: after its one-shot
        # escalation ping it FALLS THROUGH to the #479 abort-backoff park below,
        # which re-probes delivery on the capped (30-min) schedule instead of
        # latching on `skip:gave-up` forever. See _lane_giveup_decision.
    # #479 -- abort-backoff park (next to the cooldown gate, after the
    # give-up check so a MAX-abort lane still escalates). A stash-abort
    # against a persistently-parked live draft parks the NEXT attempt for a
    # widening window; within it, skip WITHOUT capture/keystroke/rescue --
    # this is the sole damping of the ~60s retry hammer. The park clears on
    # success and on the idle reset; it "resumes" naturally when the draft
    # goes (existing gates take the send_continue path) or the window elapses.
    park = rec.get("lnpark")
    if park is not None and now < park:
        logs.append("lane-occupancy %s workers=%d waiters=%d backlog=%d -> "
                    "skip:abort-backoff remaining=%ds (%d aborts, park until %d)"
                    % (loc, live_workers, waiters, backlog_n,
                       int(park - now), aborts, int(park)))
        return logs, True
    # #509 -- STRUCTURED effectiveness signal + effectiveness-aware cooldown.
    # #518: live_workers IS count_live_workers now (the gating count converted),
    # so eff_workers == live_workers by construction -- the pre-#518
    # under-saturated re-sample of the identical call (a redundant per-sweep
    # subagents disk scan) is dropped.
    eff_workers = live_workers
    cd_skip, cd_log, cd_moved = _lane_cooldown_decision(
        rec, now, under_saturated, eff_workers, backlog_n, loc, live_workers,
        waiters)
    if cd_log:
        logs.append(cd_log)
    if cd_skip:
        return logs, True
    # #442: the SAME shared check job 9's virgin-arm gate uses, but through
    # the lane path's OWN short window (never the 30-min default -- see
    # GOAL_LANE_LIVE_CONVO_S above). The `window_s` seam already exists on
    # the shared function, so job 9's own default-window semantics stay
    # byte-identical and arm delivery stays exempt entirely.
    recent, reason = watchdog._goal_autoarm_recent_human_activity(
        sid, tpath, now, window_s=GOAL_LANE_LIVE_CONVO_S)
    if recent:
        logs.append("SKIP-TRANSIENT (lane-occupancy) %s -> %s -- recent "
                    "human activity, never overwrite a live conversation"
                    % (loc, reason))
        return logs, True
    if dry_run:
        logs.append("READY (lane-occupancy) %s workers=%d waiters=%d "
                    "backlog=%d idle=%dm" % (loc, live_workers, waiters,
                                             backlog_n, idle // 60))
        return logs, True
    fresh = watchdog.capture_pane(pid, run, lines=40)
    ok, kind, fresh_draft = _lane_boundary_ok(fresh)
    raced, rlog = _lane_pre_send_race(ok, watchdog.pane_goal_armed(fresh), loc)
    logs += rlog
    if raced:
        return logs, True
    if fresh_draft != draft:
        # #442-review F1: the box CONTENT changed between the sweep-top
        # capture and this pre-send one -- someone is COMPOSING right
        # now. Un-submitted typing stamps NEITHER recent-activity signal
        # (the presence marker only ever gets stamped on a prompt
        # SUBMIT), so this two-capture diff is the one direct evidence
        # of live composition this function can get -- refuse while it
        # is still free to refuse, consume nothing, retry next sweep.
        logs.append("SKIP-TRANSIENT (lane-occupancy) %s -> draft changed "
                    "between captures -- human composing right now" % loc)
        return logs, True
    # #442 re-fix 2: EMPTY-lane vs UNDER-SATURATED text. The empty-lane text says
    # "0 dispatched workerov"; the under-saturated text names the real count
    # (mem_mb was already read above in the under-saturated branch).
    if live_workers == 0:
        text = GOAL_LANE_NUDGE_TEXT % (backlog_n, waiters)
    else:
        text = GOAL_LANE_UNDERSAT_NUDGE_TEXT % (live_workers, floor, backlog_n, waiters)
    if fresh_draft:
        # #442 -- deliver INTO the held draft via the stash protocol (the
        # primitive re-verifies idle-with-draft itself and undoes its own
        # keystrokes on any failed verify). Provenance is marked BEFORE
        # the attempt so the shared janitor can recover a stuck stash send
        # for THIS pane, and cleared only on success -- the same shape
        # `deliver_goal`'s own draft branch uses.
        watchdog._janitor_mark_watch(state, pid, now)
        # #501 -- recognize the held draft as our OWN previously-swallowed
        # nudge (a pre-#490 blind Enter stranded it) and FINISH it by
        # SUBMITTING the existing draft in place, transcript-verified, instead
        # of stashing around it and retyping a fresh copy -- which aborts
        # forever against the persistent swallow that stranded it (the live
        # cam-box zbynek-4:0.0 incident: stash-abort 1/5 -> ... -> give-up,
        # nudge never delivered). ONLY the UNAMBIGUOUS machine-diagnostic
        # prefixes (`_own_nudge_submit_prefix`: lane-check/bounce/gkreq -- a
        # human PROVABLY never types them) are submitted on content alone; a
        # FOREIGN draft (and the human-typeable `/goal `/`/compact`) stays on
        # today's `deliver_with_stash` path BYTE-FOR-BYTE (HARD CONSTRAINT a --
        # the foreign-draft protection is never weakened). Recognition reads the
        # box HEAD row (`_input_box_head_text`), NOT `fresh_draft` (which is the
        # TAIL for a WRAPPED box): every real own nudge is 289-720 chars and
        # WRAPS, so its prefix is on the head and never the tail -- keying on
        # `fresh_draft` made this branch DEAD against exactly the wrapped drafts
        # the incident is about (#501 adversarial review).
        own_head = watchdog._input_box_head_text(fresh)
        if watchdog._own_nudge_submit_prefix(own_head):
            if not watchdog.submit_own_draft_verified(pid, own_head, run,
                                                      tpath, sleep_fn=sleep_fn,
                                                      logs=logs):
                # A recognized own draft that will not submit-verify is a
                # genuinely wedged pane -- advance the SAME consecutive-abort
                # streak + backoff park the foreign stash-abort uses, so it
                # still reaches the give-up ping ("look at the session")
                # instead of retrying silently forever (#442-review F2). The
                # own draft is NEVER backspaced/retyped -- it is left in place.
                rec["lna"] = rec.get("lna", 0) + 1
                back = _lane_stash_abort_backoff(rec["lna"])
                rec["lnpark"] = now + back
                logs.append("lane-occupancy %s own-draft submit-unverified "
                            "(%d/%d) -> backoff %ds, park until %d"
                            % (loc, rec["lna"], GOAL_LANE_MAX_STASH_ABORTS,
                               back, int(rec["lnpark"])))
                return logs, True
            watchdog._janitor_clear_watch(state, pid)
            mode = "own-submit"
        # #488: thread `state` (same as deliver_goal's draft branch) so
        # deliver_with_stash durably records a park it definitively creates and
        # clears it on its own verified success.
        elif not watchdog.deliver_with_stash(pid, text, run, captured=fresh,
                                             logs=logs, sleep_fn=sleep_fn,
                                             state=state):
            # The abort typed nothing (or provably undid itself) --
            # transient, retried next sweep, and it must NOT consume the
            # ln/llast budget (a refused attempt is not a nudge). It DOES
            # advance the consecutive-abort streak, so a permanently-
            # aborting lane eventually reaches the give-up ping above
            # (#442-review F2) instead of retrying silently forever.
            rec["lna"] = rec.get("lna", 0) + 1
            # #479 -- park the NEXT attempt for a widening window instead of
            # re-typing + re-rescuing this same live draft every ~60s sweep.
            back = _lane_stash_abort_backoff(rec["lna"])
            rec["lnpark"] = now + back
            logs.append("lane-occupancy %s stash-abort (%d/%d) -> backoff %ds, "
                        "park until %d"
                        % (loc, rec["lna"], GOAL_LANE_MAX_STASH_ABORTS,
                           back, int(rec["lnpark"])))
            return logs, True
        else:
            watchdog._janitor_clear_watch(state, pid)
            mode = "stash"
    else:
        # #490 -- verified transcript-proof send (the piece the raw
        # `send_continue` never had): a swallowed Enter must NOT be booked as
        # delivered, and its text must be restored off the user's box. Mark
        # janitor provenance BEFORE the send (like the stash branch above) so
        # a residual stuck send is reclaimable, cleared only on success -- the
        # bare branch never did this, a second reason the live incident sat.
        watchdog._janitor_mark_watch(state, pid, now)
        if not watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                      logs=logs):
            # Unverified submit -- transient, retried next sweep, and it must
            # NOT consume the ln/llast budget (a refused attempt is not a
            # nudge). It DOES advance the consecutive-abort streak, so a
            # permanently-unverified lane still reaches the give-up ping above
            # -- the SAME escalation shape the stash-abort branch uses
            # (#442-review F2).
            rec["lna"] = rec.get("lna", 0) + 1
            back = _lane_stash_abort_backoff(rec["lna"])
            rec["lnpark"] = now + back
            logs.append("lane-occupancy %s submit-unverified (%d/%d) -> backoff "
                        "%ds, park until %d"
                        % (loc, rec["lna"], GOAL_LANE_MAX_STASH_ABORTS,
                           back, int(rec["lnpark"])))
            return logs, True
        watchdog._janitor_clear_watch(state, pid)
        mode = "typed"
    # #479/#509 -- commit the LANDED nudge; see _lane_record_nudge.
    _lane_record_nudge(rec, under_saturated, eff_workers, backlog_n, cd_moved, n,
                       now)
    # #442 re-fix 2: log the real worker count and (for the under-saturated fill
    # nudge) the measured MemAvailable, so an under-saturated firing is diagnosable.
    mem_suffix = "" if mem_mb is None else " MemAvailable=%dMB" % mem_mb
    # #442 THIRD GAP: the give-up counter only bounds the 0-worker branch, so
    # only that branch logs "(n/MAX)"; the under-saturated fill-the-cap branch
    # repeats with the cooldown and has no MAX, so it logs "(fill)".
    prog = "fill" if under_saturated else "%d/%d" % (n + 1, GOAL_LANE_MAX_NUDGES)
    logs.append("lane-occupancy nudge (%s) %s workers=%d floor=%d%s waiters=%d "
                "backlog=%d idle=%dm (%s)" % (mode, loc, live_workers, floor,
                                              mem_suffix, waiters, backlog_n,
                                              idle // 60, prog))
    return logs, True


def goal_lane_sweep(now, run=None, dry_run=False, projects_dir=None,
                    state=None, handled=None, backlog_fetch=None,
                    send_fn=None, sleep_fn=None, time_fn=None,
                    sweep_deadline=None):
    """The lane-occupancy driver -- the second half of job 20's new body.
    For every candidate pane whose goal is genuinely ARMED right now, runs
    `goal_lane_occupancy_nudge`. Owns its own small per-sid state namespace
    (`state['goal_lane']`), distinct from the deleted `goal_rearm`'s giant
    `rec` dict.

    `time_fn`/`sweep_deadline`: the SAME #172/#255 wall-clock self-bound
    `goal_dark_watch` carries, for the identical reason -- this loop also
    walks every live candidate pane, unbounded by anything except the
    box's own pane count. Optional, default None -> unbounded; checked as
    the first statement of each pane's iteration, mirroring
    `bounce_backstop`'s placement exactly."""
    logs = []
    # #486 G3 -- once-per-sweep hygiene: reap heartbeat files of long-dead
    # sessions (G3 is the CONSUMER of these files, so it owns their retention).
    # Runs BEFORE the disable/unwired early returns below so heartbeat-dir
    # retention is independent of whether the goal lane is enabled on this box.
    # Age-gated (7d), regular-files-only, never raises; the session-status dir is
    # env-isolated in BOTH test runners (conftest autouse + cmd_push test_env),
    # so this never touches a real developer home.
    logs += _session_status.reap_stale_status(now=now)
    if watchdog._owner_disabled("goal"):
        return logs
    if backlog_fetch is None:
        return logs
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    state = state if state is not None else {}
    recs = state.setdefault("goal_lane", {})
    # #486 G6 -- dark_watch's tail-proof `state["goal_mark"]` marker (populated
    # BEFORE this job in the same run_once, sharing `state`) is the authoritative
    # structured armed signal. Read-only here: dark_watch owns its lifecycle.
    gmarks = state.get("goal_mark", {})

    for pid, cwd, _cmd in watchdog._reconcile_candidate_panes(run):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            logs.append("lane-sweep-budget-exceeded — deferring remaining "
                        "panes to next sweep")
            break
        if watchdog.pane_in_mode(pid, run):
            continue
        tinfo = watchdog.find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, tmtime = tinfo
        sid = tpath.stem
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
            continue
        rec = recs.get(sid)
        if not isinstance(rec, dict):
            rec = {}
        llogs, _owns = goal_lane_occupancy_nudge(
            now, run, rec, sid, cwd, pid, captured, tpath, tmtime, loc,
            send_fn, dry_run, handled, projects_dir,
            backlog_fetch=backlog_fetch, state=state, sleep_fn=sleep_fn)
        recs[sid] = rec
        logs += llogs
        if handled is not None and any(ln.startswith("lane-occupancy nudge")
                                       for ln in llogs):
            handled.add(sid)
    return logs
