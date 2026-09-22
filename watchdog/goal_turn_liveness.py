"""#1110 — transcript-liveness gating for goal-arm delivery.

`deliver_goal` (watchdog/goal.py) must never type a `/goal` payload into a
session whose turn is STILL RUNNING. A live turn renders a bare `❯` input box
between tool rounds — byte-identical to a genuinely idle prompt, so the render /
spinner gates cannot see it (#1104's frame-agnostic spinner belt only matches a
spinner ABOVE the box; between tool rounds there is none). The Enter is absorbed
into the running turn, the goal never arms, and three such mis-timed keystrokes
exhaust `GOAL_DELIVERY_ATTEMPT_CAP` and DROP the request permanently while the
session is alive and about to go idle (dev1 songplayer 22.9.2026, sid 4d877a2a:
`skip:verify-failed` ×3 → `drop:attempt-cap`, then the pane went idle with
nothing pending).

The structured truth (#486 direction — structured state over pane heuristics) is
the session TRANSCRIPT: a running turn appends an assistant/tool entry at every
tool round (measured on dev1: 2–8 s gaps). This leaf reads the transcript's mtime
FRESHNESS — the SAME `age = now − stat.st_mtime` liveness formula
`watchdog.session_status.read_status` (and `find_active_transcript`) already use,
applied to the transcript path — never a second, differently-behaving mtime
convention. It is STAT-ONLY: it NEVER reads the transcript's (potentially
hundreds-of-MB) content.

Two consumers in `deliver_goal`:

  * PRE-keystroke gate: transcript younger than `GOAL_TURN_LIVE_WINDOW_S`
    → the turn is live → `skip:busy-transcript`, zero keystrokes (a
    NON-terminal, non-counting defer; the next sweep re-evaluates once the
    turn ends — within one sweep of the printing turn's end, since the window
    is shorter than the 60 s sweep cadence).

  * POST-keystroke confirm split: after a keystroke that did NOT arm, re-read
    the transcript age; if it advanced during the confirm window
    (`classify_confirm_fail` → "live") → `skip:verify-failed-live`, which is
    deliberately NOT in `_GOAL_KEYSTROKE_SKIPS`, so `goal_sweep` never counts it
    toward the STRICT #731 cap and the request stays pending. The advance is
    unfalsifiable, though: it may be a FOREIGN live turn (a mis-timed keystroke)
    OR our OWN submit read as a plain prompt (#720 silent-'sent' tail) — so
    `goal_sweep` bounds `skip:verify-failed-live` with a SEPARATE, looser
    `GOAL_DELIVERY_LIVE_ATTEMPT_CAP` (drop+ping after N) to keep the give-up
    guarantee for the accept-as-plain-prompt livelock. A genuinely quiet
    confirm-fail (the #731 swallowed-submit class) stays `skip:verify-failed` and
    counts on the strict cap.

Fail-safe throughout: a missing / unreadable / future-dated transcript is NOT a
liveness signal — the gate falls through to the existing render gate (never a new
wedge), and the confirm split defaults to the existing counted `verify-failed`.
"""
import os

# Default live window: a transcript written within this many seconds of `now`
# means the turn is (probably) still running. Chosen SHORTER than the ~60 s job-9
# sweep cadence so a genuinely-idle session is never permanently deferred: the
# sweep after the printing turn ends reads an age past the window and delivers.
GOAL_TURN_LIVE_WINDOW_S = 45

# Env override floor: the window may be raised via
# AIRULESET_GOAL_TURN_LIVE_WINDOW_S but never dropped below this — a window
# under a few seconds would let the sweep race a mid-turn write.
_GOAL_TURN_LIVE_WINDOW_FLOOR_S = 15

# A transcript mtime slightly AHEAD of `now` (age a little negative) is a
# same-machine clock jitter around a write that just happened — still LIVE.
# But a wildly-negative age (mtime seconds+ in the future) is NOT a real
# same-clock liveness signal (it is what a test injecting a small synthetic
# `now` against a real-wall-clock transcript produces), so the live check floors
# the age here: below −FUTURE_SKEW is treated as NOT live (fall through), never
# as "written in the future = live".
_GOAL_TURN_FUTURE_SKEW_S = 5


def goal_turn_live_window_s(env=None):
    """Resolve the live-window seconds: default ``GOAL_TURN_LIVE_WINDOW_S`` (45),
    raisable via ``AIRULESET_GOAL_TURN_LIVE_WINDOW_S``, floored at
    ``_GOAL_TURN_LIVE_WINDOW_FLOOR_S`` (15). An unset / unparseable / non-integer
    env value falls back to the default; a value below the floor is raised TO the
    floor. Never raises."""
    env = env if env is not None else os.environ
    raw = env.get("AIRULESET_GOAL_TURN_LIVE_WINDOW_S")
    if raw is None:
        val = GOAL_TURN_LIVE_WINDOW_S
    else:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = GOAL_TURN_LIVE_WINDOW_S
    return max(_GOAL_TURN_LIVE_WINDOW_FLOOR_S, val)


def transcript_age_s(tpath, now):
    """Seconds since the transcript at ``tpath`` was last written (its mtime),
    via the SAME ``age = now − stat.st_mtime`` freshness formula
    ``session_status.read_status`` / ``find_active_transcript`` use — STAT-ONLY,
    never a content read. Returns ``None`` when ``tpath`` is falsy, missing, or
    unstattable (a missing / unreadable transcript is not a liveness signal — the
    caller falls through to the render gate, never a new wedge)."""
    if not tpath:
        return None
    try:
        mtime = os.stat(tpath).st_mtime
    except OSError:
        return None
    return now - mtime


def turn_live(age, window_s=None, env=None):
    """True iff ``age`` (from ``transcript_age_s``) proves the turn is running
    RIGHT NOW: the transcript was written within the live window
    (``−FUTURE_SKEW ≤ age < window``). A ``None`` age (missing / unreadable
    transcript) → False (no defer). An age below ``−_GOAL_TURN_FUTURE_SKEW_S``
    (a transcript dated well into the future relative to ``now`` — not a
    same-clock liveness signal) → False, so the gate never defers on an
    inconsistent clock. On a heavily-loaded box the sweep's frozen ``now`` can
    lag the stat by more than the skew, so a genuinely-fresh live transcript can
    read past the floor and NOT defer here — the post-keystroke confirm split is
    the second belt that keeps that from becoming a permanent drop (it returns
    the uncounted ``skip:verify-failed-live`` when the transcript advanced during
    the confirm window), so the worst case is one wasted keystroke, never the
    dropped arm #1110 fixes."""
    if age is None:
        return False
    win = window_s if window_s is not None else goal_turn_live_window_s(env)
    return -_GOAL_TURN_FUTURE_SKEW_S <= age < win


def classify_confirm_fail(age_before, age_after):
    """After a keystroke that did NOT arm: did the transcript advance DURING the
    confirm window? ``age_before`` is the transcript age captured before the
    keystroke (measured against the SAME ``now`` as ``age_after``, so a delta
    reflects an mtime change alone, not the elapsed wall clock); ``age_after`` is
    the age after the confirm window.

    A write during the window moves the mtime FORWARD, so the age SHRINKS
    (``age_after < age_before``) → ``"live"``. NOTE the mtime source is
    unfalsifiable: the write may be a FOREIGN live turn (a genuinely mis-timed
    keystroke) OR our OWN submit read as a plain prompt (box clears, a ``user``
    turn is appended, the goal never arms — the #720 silent-'sent' tail). Both
    produce ``"live"`` identically; the caller does NOT count it toward the
    STRICT #731 cap but DOES bound it with the separate looser
    ``GOAL_DELIVERY_LIVE_ATTEMPT_CAP`` so the accept-as-plain-prompt livelock
    still gives up (see ``goal.py``). No write → the age is unchanged (same
    ``now``, same mtime) → ``"quiet"`` — the #731 swallowed-submit class, counted
    on the strict cap. A ``None`` on either side (no transcript to compare) →
    ``"quiet"``: default to the existing counted behaviour, never a new
    non-counting escape hatch."""
    if age_before is None or age_after is None:
        return "quiet"
    return "live" if age_after < age_before else "quiet"
