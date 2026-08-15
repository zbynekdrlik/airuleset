"""Decision / parsing / state-file core of the api-watchdog.

Extracted verbatim from ``watchdog/__init__.py`` as item G step 4 of the
definitive module split (issue #433). Two originally non-contiguous blocks move
here as one module: the two live-background-task pane readers
(:func:`_pane_live_shell_evidence`, :func:`_pane_live_task_count`) and the whole
usage-cap / 5-hour-session-limit / reset-epoch-parse / stuck-check-decision /
state-file cluster (:func:`is_usage_cap`, :func:`pane_session_limited`,
:func:`parse_reset_epoch` and its shared parse core, :func:`session_user_stopped`,
:func:`_human_clock`, :func:`decide`, :func:`declared_wait_until`,
:func:`decide_working`, :func:`load_state`, :func:`save_state`) plus every regex
/ constant those functions own. All 27 names are re-exported into the
``watchdog`` namespace by the single positional facade import in ``__init__.py``
(placed at the earlier block's position, line ~253), so every existing
``watchdog.<name>`` seam (run_once jobs 1/4/6, goal / compact / cross_stream /
janitor, hooks, tests) keeps resolving unchanged.

Direction: back-reference module. Cross-references to any name that was a
top-level ``watchdog`` name go through the package namespace at call time
(``import watchdog`` at module top; ``watchdog.<name>(...)`` in bodies), which is
what keeps ``monkeypatch``/``patch.object(watchdog, ...)`` seams effective:
- ``watchdog._above_input_box`` -- the step-4 C5 grep found it patched
  (``mock.patch.object(watchdog, "_above_input_box", ...)`` in the pane-text
  split test), and ``pane_session_limited`` / ``parse_reset_epoch`` both call it,
  so those go through the package namespace, never a from-import (the name is
  re-exported BELOW this module's own import position, so a from-import would
  also fail import-order anyway).
- ``watchdog._iter_jsonl_tail`` (transcripts.py, re-exported), ``watchdog._is_bottom_chrome``
  (pane_classify.py, re-exported), and the intra-module co-moved cross-call
  ``watchdog._reset_epoch_from_scanned_text`` (``parse_reset_epoch`` and
  ``parse_reset_epoch_from_error_text`` both call it) -- all call-time, the C3
  convention even between two functions moved into the SAME module.
- ``watchdog._LIVE_BG_TASK_RX`` -- a regex that STAYS resident in ``__init__.py``
  (used only by the two pane readers here), read call-time exactly like
  ``pane_text.py`` reads the resident ``_QUEUED_COMPACT_RX``.

Def-time defaults (:func:`decide`, :func:`decide_working`) bind the six retry /
grace constants that stay resident in ``__init__.py`` -- ``from watchdog import``
at module top is legal because all six are bound in ``__init__`` above this
module's own import position (C4). ``WORKING_RESPONDED_BACKOFF_SCHEDULE_S`` is
co-moved here, so ``decide_working`` binds it module-locally.

NAME-SHADOW GOTCHA (unique to this step): this module is ``decide`` AND it
exports a function named ``decide``. The facade re-export
(``from watchdog.decide import ..., decide as decide, ...``) binds the FUNCTION
onto ``watchdog.decide`` AFTER the package sets the submodule there, so
``watchdog.decide`` (attribute) resolves to the function -- which is exactly the
seam every ``watchdog.decide(...)`` caller wants, and is harmless at runtime (no
code accesses the submodule via that attribute). Reach the MODULE object only
through ``sys.modules["watchdog.decide"]``; ``import watchdog.decide as x`` binds
the function, not the module. This is why the split test asserts re-export
identity against ``sys.modules["watchdog.decide"]``, not ``watchdog.decide``.
"""

import json
import os
import re
import time
from pathlib import Path

import watchdog
from watchdog import (
    GRACE_SECONDS,
    RETRY_INTERVAL_SECONDS,
    MAX_NUDGES,
    BACKOFF_CAP_SECONDS,
    WORKING_RETRY_INTERVAL_SECONDS,
    MAX_WORKING_NUDGES,
)


def _pane_live_shell_evidence(captured):
    """True if the pane's own GENUINELY CURRENT mode-hint line (`⏵⏵ …`)
    shows CC's live background-shell/monitor badge. (#352 F1, adversarial
    review round 1: scanning the WHOLE bounded capture for ANY
    `⏵⏵`-prefixed line was WRONG — a completion report or playbook excerpt
    quoted verbatim inside the SAME capture window can contain that exact
    text as scrollback, sitting above the real conversation, and would be
    misread as live evidence even with a badge-free CURRENT footer, proven
    by execution.) Fixed the same way every other footer reader in this
    file resolves "what is the pane's OWN trailing chrome right now": walk
    UP from the bottom, peeling only rows `_is_bottom_chrome` accepts as
    genuinely trailing chrome (agent strip, statusline, mode hint, border
    rules — the identical bounded walk `_above_input_box` already uses),
    and only ever look for the badge WITHIN that walk. The walk stops dead
    at the first non-chrome row (an ordinary input-box `❯` line, real
    conversation prose) — a quoted scrollback line sitting ABOVE that
    boundary is structurally unreachable, never merely unlikely to match."""
    lines = str(captured or "").splitlines()
    i = len(lines)
    n = 0
    while i > 0 and watchdog._is_bottom_chrome(lines[i - 1].strip()) and n < 40:
        i -= 1
        n += 1
        s = lines[i].strip()
        if s.startswith("⏵⏵") and watchdog._LIVE_BG_TASK_RX.search(s):
            return True
    return False


def _pane_live_task_count(captured):
    """Sum of CC's own live background-shell/monitor badge counts (`⏵⏵ … ·
    N shells` / `· M monitors`) read from the pane's CURRENT trailing chrome
    (#365) -- the counting sibling of `_pane_live_shell_evidence` above:
    that function only answers "is the badge showing at all", this answers
    "how many background Bash tasks does it claim". Reuses the IDENTICAL
    bounded peel-walk (never scans quoted scrollback above the chrome
    boundary -- the same #352 F1 lesson). Returns 0 when the badge is
    absent or unparseable -- never a guess, and never negative."""
    lines = str(captured or "").splitlines()
    i = len(lines)
    n = 0
    total = 0
    while i > 0 and watchdog._is_bottom_chrome(lines[i - 1].strip()) and n < 40:
        i -= 1
        n += 1
        s = lines[i].strip()
        if s.startswith("⏵⏵"):
            for m in watchdog._LIVE_BG_TASK_RX.finditer(s):
                try:
                    total += int(m.group(0).split()[0])
                except (ValueError, IndexError):
                    continue
    return total


# A subscription / quota USAGE cap is time-based — `continue` cannot fix it (only
# the reset clock can), so it is classified separately and only PINGED, never
# nudged. Kept narrow so a transient 529 / "rate limited" / overloaded (which a
# retry CAN clear) is NOT caught here and still gets the 3×continue lifecycle.
# (#175 F2) The WEEKLY cap ("You've hit your weekly limit …") and the BARE cap
# ("You've hit your limit …", no qualifier word at all) used to be invisible
# here — only "session"/"usage" were recognized before "limit", and the literal
# space required between "limit" and "reached/resets" never matched Claude
# Code's real rendering, which separates them with a MIDDLE DOT ("limit ·
# resets 11am …"), not a space. Both gaps let a real weekly/bare cap fall
# through to the generic nudge path and get `continue`d every ~30 min for the
# WHOLE cap window (days), instead of staying bounded (ping once, wait for the
# reset). `[\s·]*` accepts any run of whitespace and/or the middle-dot
# separator between "limit" and the reset wording; `(?:session|usage|weekly)?`
# is now optional so the bare "hit your limit" shape matches too.
_USAGE_CAP_RX = re.compile(
    r"usage limit|quota|limit[\s·]*(?:reached|will reset|resets)|reset at|reached your"
    r"|hit your (?:(?:session|usage|weekly)\s+)?limit", re.I)
# Transient SERVER-side throttles — a retry / `continue` CAN clear these, so they
# must NOT be read as a quota cap. Checked FIRST. Critically this catches
# "(not your usage limit)" — Claude Code's transient rate-limit banner literally
# CONTAINS the words "usage limit", which would otherwise false-match above.
_TRANSIENT_RX = re.compile(
    r"not your usage limit|temporarily limiting|rate.?limit|overloaded|\b529\b|try again", re.I)


def is_usage_cap(text):
    """True ONLY for a real subscription/quota cap (time-based → `continue` can't
    fix it → ping only). A transient server throttle returns False so it still gets
    the 3×`continue` lifecycle."""
    if not text or _TRANSIENT_RX.search(text):
        return False
    return bool(_USAGE_CAP_RX.search(text))


# --- 5-HOUR SESSION LIMIT (a distinct, TIME-BASED cap) --------------------------
# Claude Code's session-limit banner shows in the PANE, e.g.
#   "You've hit your session limit · resets 6:10pm (Europe/Prague)"
#   "/usage-credits to finish what you're working on."
# It is NOT a transient 529 and NOT reliably an `isApiErrorMessage` transcript
# entry — it lives on screen. Unlike a server throttle, `continue` BEFORE the
# reset is a no-op that just re-hits the limit (the incident: repeated `continue`
# → "You've hit your session limit"). So job (6) reads it from the PANE, PINGS
# ONCE with the reset time, does NOTHING until the reset clock, then sends ONE
# `continue` AFTER it — never before.
# (#175 F2) Claude Code also renders a WEEKLY cap ("You've hit your weekly
# limit · resets Jul 31, 9pm (Europe/Prague)") and a BARE one ("You've hit
# your limit · resets 11am (Europe/Prague)"), with no "session"/"usage" word
# at all — this regex used to require one, so both shapes fell straight
# through to job 1's generic nudge path and got `continue`d every ~30 min for
# the whole cap window instead of getting job 6's bounded ping-once-then-wait
# treatment. `(?:session|usage|weekly)?` is now optional.
#
# (#172, carried over from #175/#176's own closing pass) A weekly cap's
# "resets Jul 31, 9pm" clock names an explicit CALENDAR DATE ahead of the
# time-of-day — `_RESET_TIME_RX` used to require a digit immediately after
# "resets "/"resets at ", so this dated form matched `is_usage_cap` (bounded,
# per #175 F2 above) but `parse_reset_epoch` returned None: job 6 could ping
# once but never compute a resume instant, so a weekly-capped session pinged
# once and then never auto-resumed even after the real reset passed — the
# user had to type `continue` by hand. The optional `(?:MONTH DAY,? )?`
# group below captures the date too, and `parse_reset_epoch` uses it (rather
# than assuming "today") when present — assuming today would compute an
# epoch DAYS too early for a multi-day-out weekly reset, and job 6 would
# retry-resume long before the real reset, immediately re-hitting the limit
# (exactly what this whole mechanism exists to prevent). The bare
# clock-time-only forms (`resets 11:20pm`, `resets 12pm`, `resets at 18:10`)
# are unaffected — the date group is optional and simply doesn't match them.
_SESSION_LIMIT_RX = re.compile(
    r"hit your (?:(?:session|usage|weekly)\s+)?limit|/usage-credits to finish", re.I)
# "resets 6:10pm" / "resets 6pm" / "resets at 18:10" / "resets Jul 31, 9pm"
# -- capture an optional MONTH + DAY ahead of the clock, then the clock.
_RESET_TIME_RX = re.compile(
    r"reset(?:s|ting)?\s+(?:at\s+)?(?:([A-Za-z]{3,9})\s+(\d{1,2}),?\s+)?"
    # #183 finding 2: the hour group is `(?!\d)`-guarded so it can never be
    # a TRUNCATED PREFIX of a longer digit run — without it, a 4-digit year
    # ("resets Jul 31, 2026 9pm") silently absorbed its first two digits as
    # the hour (epoch one hour early) instead of the whole match failing
    # (the previously fail-safe None a 2-digit year / reversed-order form
    # still correctly returns).
    r"(\d{1,2})(?!\d)(?::(\d{2}))?\s*([ap]m)?", re.I)
_RESET_MONTH_NUM = {name: i + 1 for i, name in enumerate((
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec"))}
# The tz the banner names — "(Europe/Prague)" OR a bare zone word like
# "(UTC)"/"(GMT)". Broadened from Area/City-only after the gk incident
# (2026-07-24): the gk box runs UTC, its banner reads "resets 4:40pm (UTC)",
# and the narrower Area/City-only regex never matched it — silently falling
# through to the Europe/Bratislava default and computing a reset epoch 2h
# EARLY (a nonsense past reset time on the Discord ping).
_RESET_TZ_RX = re.compile(r"\(([A-Za-z]+(?:/[A-Za-z_]+)?)\)")
# Job 6's bounded post-reset resume retry (FIX C, gk incident 2026-07-24) —
# see the `elif ra and now >= ra:` branch in run_once for the full story.
SESSLIMIT_RETRY_S = 5 * 60
SESSLIMIT_MAX_TRIES = 4
# #183: how stale a DATED reset target (this year's occurrence) may be
# before it must mean NEXT year's occurrence instead. The bare-clock
# branch's OWN 6h window is sized for a 5-HOUR session-limit banner, which
# can only ever be a few hours stale — far too tight for the WEEKLY-cap
# banner `parse_reset_epoch` ALSO parses (the same function, the dated
# branch), whose date can legitimately be up to ~7 days out. Comfortably
# wider than one full weekly cycle so a genuinely-this-week date is never
# mistaken for "must be next year".
DATED_RESET_STALE_GRACE_S = 8 * 86400


def pane_session_limited(captured):
    """True if the pane's BOTTOM shows Claude Code's 5-hour session-limit
    banner — scoped to the last 10 lines of the region ABOVE the input box
    (falling back to the raw capture's last 10 lines when no input box is
    located at all, e.g. a busy/spinner pane with no `❯` boundary).

    A dead BACKGROUND WORKER can leave a `⎿ You've hit your session limit …`
    ECHO line sitting HIGH in the transcript output, with many later
    `● pokracujem v praci`-style lines scrolling underneath it for hours — a
    whole-capture search kept the episode "limited" long after a real resume
    already happened (gk incident 2026-07-24). Bottom-scoping means only a
    banner that is still the FRESHEST thing on screen counts."""
    if not captured:
        return False
    region = watchdog._above_input_box(captured)
    lines = [ln for ln in region.splitlines() if ln.strip()]
    if not lines:
        lines = [ln for ln in captured.splitlines() if ln.strip()]
    return bool(_SESSION_LIMIT_RX.search("\n".join(lines[-10:])))


def parse_reset_epoch(captured, now):
    """Parse 'resets <clock>' (optionally 'resets <Month> <day>, <clock>')
    from the banner. The BARE-CLOCK form (a 5-hour session-limit reset)
    always returns an epoch >= now (rolled to tomorrow if already past by
    more than 6h). The DATED form (also used for a WEEKLY-cap banner, whose
    date can legitimately be up to ~7 days out) returns the parsed target
    AS-IS whenever it is within `DATED_RESET_STALE_GRACE_S` of now — INCLUDING
    slightly in the past, which means "the reset already happened" and is
    correct, not an error: job 6 treats any `resets_at <= now` as "resume
    immediately". Only once THIS YEAR's occurrence is stale by more than
    that grace does it roll to next year's occurrence. Either way, returns
    None whenever the banner cannot be read with confidence — see the
    per-branch notes below — so job 6 pings but leaves the episode
    refinable rather than locking in a wrong epoch.

    The clock is read in the tz the banner names: "UTC"/"GMT" literally, an
    "Area/City" name via ZoneInfo, any other bare parenthesized word (e.g. a
    stray "(debug)" elsewhere in the pane) falls back to the
    Europe/Bratislava default (same offset as Prague). The tz is searched
    ONLY in the ~80 chars starting at the TIME match, never the whole
    capture: a global search would hijack on ANY parenthesized word
    anywhere in the pane, however far from the clock (gk incident
    2026-07-24). Fail-safe: any parse/tz error returns None (job 6 then
    pings but cannot auto-resume — the user handles it).

    #172 (carried over from #175/#176's own closing pass): when the banner
    names an explicit calendar date ("resets Jul 31, 9pm"), that date is
    used for the target — NOT "today". Assuming today for a multi-day-out
    weekly reset would compute an epoch DAYS too early, and job 6 would
    retry-resume long before the real reset (immediately re-hitting the
    limit — the one thing `continue`-before-reset must never do).

    #183 finding 3: the search is BOTTOM-SCOPED to the same last-10-lines
    region `pane_session_limited` itself uses, never the whole capture — a
    STALE reset-time echo higher on screen (a dead background worker's old
    output, or last episode's own banner) must never beat a fresher banner
    lower down; before this the parse searched globally while the detector
    that gates it was already deliberately bottom-scoped (the exact
    stale-echo shape `pane_session_limited`'s own docstring documents).

    #336: the box-scoping happens ONLY here — the actual clock/timezone/
    date parse below is shared with `parse_reset_epoch_from_error_text`
    (job 1's own PLAIN error-message text, which needs no pane/box scoping
    at all) via `_reset_epoch_from_scanned_text`."""
    try:
        region = watchdog._above_input_box(captured)
        lines = [ln for ln in region.splitlines() if ln.strip()]
        if not lines:
            lines = [ln for ln in (captured or "").splitlines() if ln.strip()]
        scoped = "\n".join(lines[-10:])
    except Exception:
        return None
    return watchdog._reset_epoch_from_scanned_text(scoped, now)


def _reset_epoch_from_scanned_text(scoped, now):
    """The shared clock/timezone/date-parsing core of `parse_reset_epoch` —
    `scoped` is ALREADY the text to search (a pane's bottom-scoped region
    for the pane-based caller, or a plain error-message string for
    `parse_reset_epoch_from_error_text`). See `parse_reset_epoch`'s own
    docstring for the full parsing contract; this function does not repeat
    it. Fail-safe: any parse/tz error returns None."""
    try:
        # The LAST match, not the first: `.search()` would still pick a
        # STALE echo sitting higher in the scoped window over a FRESHER
        # banner below it (#183 finding 3's exact reproduction — bottom
        # scoping alone narrows the window, it doesn't reorder within it).
        matches = list(_RESET_TIME_RX.finditer(scoped))
        if not matches:
            return None
        m = matches[-1]
        month_name, day_s, hh_s, mm_s, ap_s = m.groups()
        hh = int(hh_s)
        mm = int(mm_s or 0)
        ap = (ap_s or "").lower()
        if ap == "pm" and hh != 12:
            hh += 12
        elif ap == "am" and hh == 12:
            hh = 0
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        from datetime import datetime, timedelta
        tz = None
        try:
            from zoneinfo import ZoneInfo
            seg = scoped[m.start():m.start() + 80]
            tzm = _RESET_TZ_RX.search(seg)
            if tzm:
                name = tzm.group(1)
                if name in ("UTC", "GMT"):
                    tz = ZoneInfo("UTC")
                elif "/" in name:
                    tz = ZoneInfo(name)
                else:
                    tz = ZoneInfo("Europe/Bratislava")
            else:
                tz = ZoneInfo("Europe/Bratislava")
        except Exception:
            tz = None
        base = datetime.fromtimestamp(now, tz)
        month = _RESET_MONTH_NUM.get((month_name or "")[:3].lower())
        if month_name and not month:
            # #183 finding 1: the date group MATCHED (a month-shaped word +
            # a day both present) but the word is not a recognised month
            # (e.g. a weekday, "Thu 31" — the regex only requires 3-9
            # letters, it never validates the word itself). Falling through
            # to the bare-clock branch below would silently reuse TODAY's
            # date with this banner's clock, computing an epoch DAYS too
            # early — the exact "resumes before the real reset, immediately
            # re-hits the limit" outcome this whole function exists to
            # prevent. An unrecognised month must return None, not guess.
            return None
        if month and day_s:
            # An explicit calendar date -- use IT, not "today" (see the
            # docstring above).
            try:
                target = base.replace(month=month, day=int(day_s), hour=hh,
                                      minute=mm, second=0, microsecond=0)
            except ValueError:
                return None       # e.g. day out of range for the month
            # #183 findings 4/5: NOT the bare-clock branch's 6h window (see
            # DATED_RESET_STALE_GRACE_S's own comment) -- a dated target
            # slightly in the past (including a small negative delta) is
            # simply returned as-is; only real staleness beyond one weekly
            # cycle means "must be next year".
            if target.timestamp() <= now - DATED_RESET_STALE_GRACE_S:
                target = target.replace(year=target.year + 1)
            return target.timestamp()
        target = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        ts = target.timestamp()
        # The 5-hour reset window is short. A clock only SLIGHTLY in the past means
        # the reset just happened (or the banner is momentarily stale) → resume NOW,
        # don't wait a whole day. Only a clock > 6h in the past is really a next-day
        # time (e.g. a late-night "resets 12:10am" seen at 23:50) → roll to tomorrow.
        if ts <= now - 6 * 3600:
            ts = (target + timedelta(days=1)).timestamp()
        return ts
    except Exception:
        return None


def parse_reset_epoch_from_error_text(text, now):
    """Same clock/timezone/date-parsing as `parse_reset_epoch`, run directly
    over a PLAIN error-message STRING (job 1's own `transcript_last_error()`
    output) instead of a captured tmux pane — no box-scoping, no agent-strip
    chrome to strip first, since the transcript's own `isApiErrorMessage`
    text is already just the message.

    #336: this is what lets a session-limit hit that NEVER renders its
    banner on the live pane (a background Agent/subagent dying on the
    account's 5h limit, whose failure only ever shows up in the parent
    session's OWN next `isApiErrorMessage` entry, never as pane chrome —
    the montalu2 incident) still get a resume time parked from the error
    TEXT itself, instead of depending on job 6's live, continuously
    re-scanned pane detection, which structurally cannot see an error that
    was never rendered as the pane's bottom-most content in the first
    place."""
    return watchdog._reset_epoch_from_scanned_text(text or "", now)


def session_user_stopped(tpath, since_ts=None):
    """True if the user explicitly told THIS session to stop (`/exit`) at
    or after `since_ts`. The narrow, session-limit-scoped counterpart of
    #335's own general user-stop invariant (`_goal_was_cleared_by_user`,
    itself deleted along with the rest of the heuristic re-arm machinery
    by #403 rather than ever reconciled with this function — the two
    stayed independent implementations of a similar idea for their whole
    overlapping lifetime; #336's own auto-resume mechanism never depended
    on that reconciliation landing).

    A session the user deliberately exited must NEVER be auto-resumed by
    delivering `continue`, even once its parked reset time has passed and
    even if the SAME transcript is later reattached (`claude -c`) — the
    user's own explicit `/exit`, issued after the limit hit, is a stronger
    signal than "the reset clock passed" and always wins.

    Scans the transcript's recent tail for a top-level, plain-STRING
    `/exit` command entry — Claude Code's own literal marker for the user's
    `/exit` command, the same top-level shape #335's own design comment
    names. Matched by PREFIX (`<command-name>/exit</command-name>`), never
    strict equality: a real `/exit` entry's `message.content` is a
    COMPOSITE string, e.g.
    `"<command-name>/exit</command-name>\n            <command-message>exit`
    `</command-message>\n            <command-args></command-args>"`
    (verified against real Claude Code transcripts, #336's own adversarial
    review, finding F1) — a strict-equality check against the bare marker
    alone NEVER matches a real transcript, which made this whole predicate
    inert against every genuine `/exit`. The closing `</command-name>` tag
    is part of the required prefix, so a DIFFERENT command name that merely
    starts with the same letters (never a real Claude Code shape, but
    checked defensively) is still correctly refused. `timestamp` must be
    `>= since_ts` (no lower bound at all when `since_ts` is None).

    Fail-SAFE in the direction that never strands a healthy session: an
    unreadable/missing transcript, or any parse error, returns False —
    "can't tell" must never be read as "the user stopped it", or a merely-
    unreadable transcript would strand a session that was never actually
    exited."""
    try:
        from datetime import datetime
        for entry in watchdog._iter_jsonl_tail(tpath, max_lines=400):
            if not isinstance(entry, dict) or entry.get("type") != "user":
                continue
            msg = entry.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, str):
                continue
            if not content.lstrip().startswith("<command-name>/exit</command-name>"):
                continue
            if since_ts is None:
                return True
            try:
                ts = datetime.fromisoformat(
                    str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue          # unparseable timestamp — this is an ANY
                                  # over the whole window (oldest-to-newest
                                  # file order, not reversed), so a single
                                  # bad timestamp just moves on to the next
                                  # candidate entry, in either direction
            if ts >= since_ts:
                return True
        return False
    except Exception:
        return False


def _human_clock(epoch, now=None):
    """Epoch → 'HH:MM' in Europe/Bratislava, for the ping text — but only
    when the reset falls on TODAY's local date (relative to `now`, default
    the real wall clock). #183 finding 6: `parse_reset_epoch` started
    successfully parsing a multi-day-out WEEKLY-cap banner without this
    consumer ever being updated to match — a cap five days out read as
    "Reset o 21:00", telling the user it resumes TONIGHT when it actually
    resumes on a later date. A reset on a different day renders
    'DD.MM HH:MM' instead."""
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Europe/Bratislava")
        except Exception:
            tz = None
        dt = datetime.fromtimestamp(epoch, tz)
        today = datetime.fromtimestamp(time.time() if now is None else now, tz)
        if dt.date() == today.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return "?"


def decide(state, key, err_hash, now, grace=GRACE_SECONDS,
           interval=RETRY_INTERVAL_SECONDS, max_nudges=MAX_NUDGES, first_seen_seed=None,
           backoff_cap=BACKOFF_CAP_SECONDS):
    """Pure decision for ONE stalled session. Returns (action, entry) where action
    is 'nudge' | 'wait'. `entry` is the updated state record (caller persists
    state[key] = entry).

    The grace is tracked HERE, from `first_seen` (the moment the session's last
    reply became an api-error), NOT from transcript mtime — Claude Code's own
    retries + queue/snapshot writes keep touching the transcript, so an mtime-idle
    gate never trips for a rate-limited session (that bug left `presenter`
    unnudged). On first sighting `first_seen = first_seen_seed` (the caller seeds it
    with `now - idle` so an already-stale stall counts from when it really began);
    if that is already >= grace old the first `continue` goes out NOW, else we
    `wait` and let Claude Code recover on its own for `grace` first. Thereafter a
    nudge fires every `interval`, for the first `max_nudges` attempts.

    PAST `max_nudges` the policy no longer gives up (#175 — a multi-hour upstream
    529 storm used to strand a session after ~15-20 min of silence even with a
    healthy watchdog, and the hash-stability rule made it worse: a REPEATED
    identical error is exactly the case that never re-arms). Nudging CONTINUES
    INDEFINITELY, but the retry interval WIDENS each attempt (doubling from
    `interval`, capped at `backoff_cap`) so a long outage is covered cheaply
    (one `continue` per interval) instead of hammering a dead endpoint: attempts
    #1-#3 at `interval` (300s) spacing, then 600 / 1200 / 1800 / 1800 / ... The
    one-shot "gave up" Discord ping still fires exactly once — the caller detects
    it by `entry['escalated']` flipping False -> True on the attempt that FIRST
    crosses `max_nudges` (the (max_nudges + 1)-th nudge) and fires its own ping
    then; every later call leaves `escalated` True with no further ping. A
    different err_hash (a new error) restarts the whole cycle from scratch,
    including a fresh one-shot escalation.

    A caller-forced `entry['dormant']` (used for a usage/quota cap, where only
    the external reset clock — not `continue` — can fix it) makes THIS CALL
    return 'wait' regardless of the schedule above. (#175 F4 correction: a new
    err_hash is the COMMON way the flag goes away, not the ONLY one — the
    caller's own state-cleanup pass can drop `state[key]` entirely, e.g. once
    the session's pane is no longer visible. That is harmless here: the caller
    re-derives `dormant` from `is_usage_cap(err_text)` on every sweep that
    reaches a fresh 'nudge', so a rebuilt entry is immediately re-marked
    dormant from the SAME live error text — no `continue` is ever typed into a
    genuinely-capped session either way. What CAN differ is the ping: a
    rebuilt entry reseeds `first_seen`, which changes the alert's dedup key, so
    a wipe-and-rebuild can cost a second, otherwise-redundant ping — never a
    keystroke.)"""
    e = state.get(key)
    if e is None or e.get("hash") != err_hash:
        fs = int(first_seen_seed) if first_seen_seed is not None else int(now)
        entry = {"hash": err_hash, "first_seen": fs, "nudges": [], "escalated": False}
        if (now - fs) >= grace:           # already stuck >= grace → first continue now
            entry["nudges"] = [int(now)]
            return "nudge", entry
        return "wait", entry              # fresh → give Claude Code `grace` to recover
    if e.get("dormant"):
        return "wait", e                 # permanently held (usage cap) until a new hash
    nudges = list(e.get("nudges", []))
    last = nudges[-1] if nudges else e.get("first_seen", now)
    n = len(nudges)
    if n < max_nudges:
        needed = grace if not nudges else interval
    else:
        step = n - max_nudges + 1        # 1, 2, 3, ... widening back-off step
        needed = min(interval * (2 ** step), backoff_cap)
    if (now - last) < needed:
        return "wait", e
    e2 = dict(e)
    e2["nudges"] = nudges + [int(now)]
    if n >= max_nudges and not e.get("escalated"):
        e2["escalated"] = True           # one-shot: caller fires the give-up ping now
    return "nudge", e2


# --- Stuck-check sensitivity (2026-07-20, codex-bridge drilling incident) ----
# A session honestly waiting on a SCHEDULED event ("čakám na 14:15 auto-sync")
# got nudged every cycle until pressured into premature work. Two valves:
# declared_wait_until() (respect an explicit future clock in the ⏳ marker) and
# the responded-backoff in decide_working (answered nudges space out
# exponentially and never escalate — escalation is for a DEAD process only).
DECLARED_WAIT_GRACE_S = 20 * 60      # nudge only this long AFTER the declared time
DECLARED_WAIT_MAX_S = 12 * 3600      # a "future" time further than this is noise
# (#352) A session that keeps ANSWERING the self-check nudge (genuinely alive,
# still legitimately waiting) is re-checked on a widening, EXPLICIT schedule
# rather than an unbounded-feeling exponential — the user's own concrete ask
# after a live incident of hourly re-checks each burning a full context turn
# purely to re-prove liveness: 1h, then 3h, then 6h, holding at 6h for any
# further round. Never nudges MORE often than this even for a session that
# keeps answering forever, and — paired with `_pane_live_shell_evidence`
# above, which skips the check ENTIRELY while the pane already shows proof
# of life — this schedule is now the fallback for the case that check can't
# see (a shell alive but not pane-visible), not the primary defense.
WORKING_RESPONDED_BACKOFF_SCHEDULE_S = (3600, 3 * 3600, 6 * 3600)
_CLOCK_RX = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def declared_wait_until(marker_line, now, tz="Europe/Bratislava"):
    """Epoch until which the ⏳ marker line's DECLARED future clock time
    suppresses the stuck-check (latest declared time + grace), or 0 when the
    line names no usable future time. A time already past resolves to its next
    occurrence; anything further than DECLARED_WAIT_MAX_S away is ignored
    (a mentioned historical time, not a wait declaration)."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        local = datetime.fromtimestamp(now, ZoneInfo(tz))
    except Exception:
        local = datetime.fromtimestamp(now)
    best = 0.0
    for m in _CLOCK_RX.finditer(str(marker_line or "")):
        h, mi = int(m.group(1)), int(m.group(2))
        cand = local.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand.timestamp() <= now:
            cand += timedelta(days=1)
        delta = cand.timestamp() - now
        if 0 < delta <= DECLARED_WAIT_MAX_S:
            best = max(best, cand.timestamp())
    return best + DECLARED_WAIT_GRACE_S if best else 0


def decide_working(state, wkey, now, idle, interval=WORKING_RETRY_INTERVAL_SECONDS,
                   max_nudges=MAX_WORKING_NUDGES, responded=False,
                   backoff_schedule=WORKING_RESPONDED_BACKOFF_SCHEDULE_S):
    """Pure decision for ONE `⏳ WORKING`-stalled session (job 4). Returns
    (action, entry) where action is 'nudge' | 'wait' | 'escalate' | 'noop'; the
    caller persists state[wkey] = entry. Called ONLY after the caller has already
    confirmed `⏳` marker + idle >= threshold + no advancing subagent, so the FIRST
    sighting nudges immediately (the threshold IS the grace).

    Unlike job 1's `decide` (api-error, where CC keeps writing the transcript so the
    timer is state-based), a job-4 nudge that LANDS resets the transcript idle below
    the threshold — so the caller simply stops invoking this for that session and the
    episode is cleaned up by last_seen. We only get here AGAIN if the prior nudge
    produced no transcript write within `interval` (the Claude process is itself
    wedged), so a retry is the right escalation. After `max_nudges` no-response nudges
    it escalates ONCE (the single user-facing ping), then noops."""
    e = state.get(wkey)
    if e is None:
        e = {"first_seen": int(now - idle), "nudges": [], "escalated": False}
    e["last_seen"] = int(now)
    if e.get("escalated"):
        return "noop", e
    nudges = list(e.get("nudges", []))
    if not nudges:                         # first sighting past the threshold → nudge now
        e["nudges"] = [int(now)]
        return "nudge", e
    if responded:
        # The session ANSWERED the previous nudge — it is ALIVE, just waiting.
        # Space repeats out on the EXPLICIT staged schedule (#352: 1h → 3h →
        # 6h, holding at the last step) and never let answered checks count
        # toward the 'wedged' escalation (the drilling incident: 3 answered
        # nudges fired a false wedged ping and the session got pressured into
        # premature work).
        answered = int(e.get("answered", 0)) + 1
        e["answered"] = answered
        e["noresp"] = 0
        step = min(answered - 1, len(backoff_schedule) - 1)
        gap_needed = backoff_schedule[step]
        if (now - nudges[-1]) < gap_needed:
            return "wait", e
        e["nudges"] = nudges + [int(now)]
        return "nudge", e
    noresp = int(e.get("noresp", len(nudges)))
    if noresp >= max_nudges:               # MAX no-response nudges → give up, ping once
        e["escalated"] = True
        return "escalate", e
    if (now - nudges[-1]) >= interval:     # still wedged `interval` later → re-nudge
        e["nudges"] = nudges + [int(now)]
        e["noresp"] = noresp + 1
        return "nudge", e
    return "wait", e                       # within the retry interval → hold


def load_state(state_path):
    try:
        with open(state_path) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state_path, state):
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(state_path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, state_path)
    except OSError:
        pass
