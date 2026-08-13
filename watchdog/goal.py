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

  RE-ARM  -- ONLY by another genuine `/autopilot` invocation (the SAME
             `goal-arm --self` callback, called fresh). A session that goes
             dark, dies, or gets its goal cleared is NEVER automatically
             re-armed -- see `goal_dark_watch()` below, which replaces
             essentially all of job 20's old body with a single, debounced,
             keystroke-free Discord ping.

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


def record_goal_request(session, cwd, text, authority, now=None, path=None,
                        origin=None):
    """Record a pending `/goal` arm request for `session` -- called ONLY by
    the one proven origin (`goal-arm --self`). Overwrites any earlier
    pending request for the SAME session, except its `ts`: `ts` is set
    ONCE, on the call that CREATES the entry, and preserved UNCONDITIONALLY
    on every later re-record for the same still-pending session
    (`cwd`/`origin`/`authority`/`text` always take the newest call's
    values) -- the SAME #400 non-refreshable age-cap invariant
    `compact.record_compact_request` established, for the identical reason:
    a request whose anchor can be refreshed by an ordinary re-record never
    actually ages out. Fail-safe (never raises). Returns True on success."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    d = load_goal_requests(path)
    prior = d.get(session)
    ts = prior.get("ts") if isinstance(prior, dict) and prior.get("ts") is not None \
        else int(now)
    d[session] = {
        "cwd": str(cwd or ""),
        "ts": ts,
        "origin": str(origin or "").strip(),
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
# template's own condition (A) already refuses to let the loop proceed
# past an unanswered ❓ regardless of whether it's armed -- so arming
# during ❓/⏳ is harmless, never unsafe.


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
))


def deliver_goal(sid, cwd, text, authority, run=None, projects_dir=None,
                 now=None, state=None, request_ts=None, send_fn=None,
                 dry_run=False, sleep_fn=None, logs=None):
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
      "skip:<reason>"               -- not safe right now; the caller
                                        LEAVES the request pending for the
                                        next periodic sweep.

    Deliberately does NOT check `_goal_autoarm_recent_human_activity` --
    see this module's own header docstring for why that would be a
    structurally-always-refuses bug here, not a safety net."""
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
        ok = watchdog.deliver_with_stash(pid, text, run, captured=captured,
                                         logs=logs, sleep_fn=sleep_fn)
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
                        sleep_fn=sleep_fn)
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
                            dry_run=dry_run, sleep_fn=sleep_fn)
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
# DARK-WATCH -- job 20's new body. NO keystrokes, ever. Cross-checks each
# session's transcript marker (INTENT) against CC's own footer indicator
# (REALITY) and, on a genuine, DEBOUNCED mismatch, sends ONE Discord ping
# telling the user to re-run `/autopilot` -- never re-types the payload
# back in. Also runs the shared janitor recovery (#372) at the top of its
# per-pane loop, since it is the one sweep that still visits every live
# pane every tick regardless of pending requests.
# --------------------------------------------------------------------------- #

def goal_dark_watch(now, run=None, state=None, send_fn=None, dry_run=False,
                    projects_dir=None, sleep_fn=None, time_fn=None,
                    sweep_deadline=None):
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
    it is retried next sweep exactly like an unvisited pane always is."""
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
        tpath, _tmtime = tinfo
        sid = tpath.stem

        rec = off_state.get(sid)
        off = rec.get("off") if isinstance(rec, dict) else None
        prior_mark = rec.get("mark") if isinstance(rec, dict) else None
        new_off, new_mark = watchdog.scan_goal_markers(tpath, off=off)
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
        off_state[sid] = {"off": new_off, "mark": mark}

        armed = watchdog.pane_goal_armed(captured)

        if mark is None or mark.get("state") != "set":
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            continue
        mark_ts = mark.get("ts")

        if armed is True:
            seen_state.pop(sid, None)
            pinged_state.pop(sid, None)
            continue
        if armed is None:
            continue   # undeterminable -- never guess, retry next sweep

        # armed is False, mark == "set" -- the silently-dead-loop shape.
        prior = seen_state.get(sid)
        if not isinstance(prior, dict) or prior.get("mark_ts") != mark_ts:
            seen_state[sid] = {"mark_ts": mark_ts, "first_seen": now}
            logs.append("dark-watch %s sid=%s -> first observation, debouncing"
                        % (loc, sid))
            continue
        if pinged_state.get(sid) == mark_ts:
            continue   # already pinged for this exact episode
        pinged_state[sid] = mark_ts
        logs.append("dark-watch %s sid=%s -> goal died silently, pinging"
                    % (loc, sid))
        if send_fn is not None and not dry_run:
            from notify import stream_redirect
            send_fn(
                "\U0001f480 **%s** — /goal loop zomrelo potichu "
                "(transkript hovorí armovaný, footer nie). "
                "Spústi prosím `/autopilot` znova."
                % watchdog.project_label(cwd),
                owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                dedup_key="goal-dark:%s:%d" % (sid, int(mark_ts or 0)),
                dry_run=dry_run)
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
# rate-limited reminder into an ALREADY-armed session, so it only needs to
# avoid typing into an exchange a human is actively MID-SENTENCE in: live
# typing stamps the presence marker every minute or two, so ~3 min covers
# "typing right now" while a periodic glance no longer suppresses it.
# Worst-case annoyance stays bounded by GOAL_LANE_INTERVAL_S +
# GOAL_LANE_MAX_NUDGES regardless.
GOAL_LANE_LIVE_CONVO_S = 3 * 60

# #442 -- the text TEACHES the fleet-dispatch doctrine
# (skills/autopilot/SKILL.md: parallel worktree workers, the account-wide
# concurrent-agent cap of 8, serialize-only integration), never just a
# bare "dispatch more workers" poke.
GOAL_LANE_NUDGE_TEXT = (
    "lane-check: backlog=%d otvorených tiketov, no BEŽÍ 0 dispatched "
    "workerov (žiadny live subagent transcript; waiterov beží: %d). "
    "Prázdne implementačné lány nie sú dôvod na nečinnosť ani počas "
    "čakania na CI/waiter. Postupuj podľa FLEET doktríny skills/autopilot "
    "SKILL.md: dispatchni TERAZ ďalšie batchnuté tickety ako PARALELNÝCH "
    "isolation:\"worktree\" autopilot-worker subagentov "
    "(run_in_background), drž ACCOUNT-WIDE cap 8 súbežných agentov "
    "(workeri + validatory + review dispatche SPOLU) a integruj výhradne "
    "SÉRIOVO — jeden merge/CI/push za celé kolo."
)


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
        return logs, False
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception:
        authority = None
    if authority != "full":
        return logs, False
    idle = now - (tmtime or now)
    if idle < GOAL_LANE_IDLE_S:
        if rec.get("ln"):
            rec.update({"ln": 0, "lpinged": False})
        return logs, False
    marker = watchdog.transcript_last_marker(tpath)
    if marker == "❓":
        return logs, False
    if watchdog._pane_has_bg_agent(captured) or watchdog.pane_waiting_on_user(captured):
        return logs, False
    if watchdog._pane_compacting(captured):
        return logs, False
    if handled is not None and sid in handled:
        return logs, False
    waiters = watchdog._pane_live_task_count(captured)
    if marker == "⏳" and waiters <= 0:
        return logs, False

    def _boundary_ok(cap):
        kind, draft = watchdog._classify_boundary(cap)
        if kind != "input":
            return False, kind, draft
        if draft:
            # #442: an AT-REST draft is deliverable -- `deliver_with_stash`
            # parks it (single slot, auto-restores once the delivered turn
            # completes), so it stopped being a reason to skip. At-rest-ness
            # for a draft is the draft-admitting free-prompt shape
            # (`bare_only=False`), the same precondition deliver_with_stash
            # re-verifies internally before its first keystroke.
            return watchdog._has_free_prompt(cap, bare_only=False), kind, draft
        return watchdog.pane_at_idle_prompt(cap), kind, draft

    ok, kind, draft = _boundary_ok(captured)
    if not ok:
        if kind != "input" or draft:
            logs.append("skip %s (lane-occupancy) %s"
                        % (draft and "draft" or kind, loc))
        return logs, False
    live_workers = watchdog._count_live_subagents(tpath, now, GOAL_LANE_LIVE_WINDOW_S)
    if live_workers > 0:
        logs.append("lane-occupancy %s workers=%d waiters=%d -> occupied, "
                    "skip" % (loc, live_workers, waiters))
        return logs, False
    backlog_n = watchdog._cached_backlog_count(cwd, backlog_fetch, state, now)
    if not isinstance(backlog_n, int) or backlog_n <= 0:
        logs.append("lane-occupancy %s workers=0 waiters=%d backlog=%r -> "
                    "no measurable open backlog, skip"
                    % (loc, waiters, backlog_n))
        return logs, False
    n = rec.get("ln", 0)
    if n >= GOAL_LANE_MAX_NUDGES:
        if not rec.get("lpinged"):
            if send_fn is not None and not dry_run:
                rec["lpinged"] = True
                from notify import stream_redirect
                send_fn("⚠️ **%s** — backlog=%d otvorených, "
                        "`/goal` armovaný, ale %d min nebeží ani "
                        "jeden worker (waiterov: %d) a ani po %d "
                        "štúchnutiach sa lány nezaplnili (%s). "
                        "Pozri sa na reláciu, prosím."
                        % (watchdog.project_label(cwd), backlog_n,
                           int(idle // 60), waiters, GOAL_LANE_MAX_NUDGES, loc),
                        owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                        dedup_key="lanestall:%s:%d" % (sid, int(tmtime or 0)),
                        dry_run=dry_run)
            logs.append("lane-occupancy %s workers=0 waiters=%d backlog=%d "
                        "idle=%dm -> GAVE UP after %d nudges"
                        % (loc, waiters, backlog_n, idle // 60,
                           GOAL_LANE_MAX_NUDGES))
        return logs, True
    last = rec.get("llast")
    if last is not None and (now - last) < GOAL_LANE_INTERVAL_S:
        logs.append("lane-occupancy %s workers=0 waiters=%d backlog=%d -> "
                    "rate-limited, skip" % (loc, waiters, backlog_n))
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
        logs.append("READY (lane-occupancy) %s workers=0 waiters=%d "
                    "backlog=%d idle=%dm" % (loc, waiters, backlog_n,
                                             idle // 60))
        return logs, True
    fresh = watchdog.capture_pane(pid, run, lines=40)
    ok, kind, draft = _boundary_ok(fresh)
    if not ok or watchdog.pane_goal_armed(fresh) is not True:
        logs.append("skip raced (lane-occupancy) %s -> pane moved since "
                    "the sweep" % loc)
        return logs, True
    text = GOAL_LANE_NUDGE_TEXT % (backlog_n, waiters)
    if draft:
        # #442 -- deliver INTO the held draft via the stash protocol (the
        # primitive re-verifies idle-with-draft itself and undoes its own
        # keystrokes on any failed verify). Provenance is marked BEFORE
        # the attempt so the shared janitor can recover a stuck stash send
        # for THIS pane, and cleared only on success -- the same shape
        # `deliver_goal`'s own draft branch uses.
        watchdog._janitor_mark_watch(state, pid, now)
        if not watchdog.deliver_with_stash(pid, text, run, captured=fresh,
                                           logs=logs, sleep_fn=sleep_fn):
            # The abort typed nothing (or provably undid itself) --
            # transient, retried next sweep, and it must NOT consume the
            # ln/llast budget (a refused attempt is not a nudge).
            logs.append("lane-occupancy %s stash-abort -> transient, "
                        "retry next sweep" % loc)
            return logs, True
        watchdog._janitor_clear_watch(state, pid)
        mode = "stash"
    else:
        watchdog.send_continue(pid, text, run)
        mode = "typed"
    rec["ln"] = n + 1
    rec["llast"] = now
    logs.append("lane-occupancy nudge (%s) %s workers=0 waiters=%d backlog=%d "
                "idle=%dm (%d/%d)" % (mode, loc, waiters, backlog_n,
                                      idle // 60, n + 1, GOAL_LANE_MAX_NUDGES))
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
    if watchdog._owner_disabled("goal"):
        return logs
    if backlog_fetch is None:
        return logs
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    state = state if state is not None else {}
    recs = state.setdefault("goal_lane", {})

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
        armed = watchdog.pane_goal_armed(captured)
        if armed is not True:
            continue
        loc = watchdog._pane_location(pid, run) or pid
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
