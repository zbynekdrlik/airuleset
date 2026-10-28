"""#1265 -- confirm a `/goal` arm from the session TRANSCRIPT (structured state,
#486), not only from the pane render.

`deliver_goal` (watchdog/goal.py) types `/goal <cond>` and then confirms the arm
in `_await_goal_armed`, shared by the bare-box, stash and stranded-own routes.
That confirm used to read only the pane footer (`pane_goal_armed`, the `◎ /goal`
glyph). On CC 2.1.311 a successful arm starts its own turn at once and the
spinner/tool output covers the glyph, so a LANDED arm read `armed=False`; the
transcript had advanced, so `_verify_fail_word` said `skip:verify-failed-live`
(uncounted) and `goal_sweep` retyped the request up to
`GOAL_DELIVERY_LIVE_ATTEMPT_CAP` (dev1 camera-box 28.10.2026: six real arms).

CC writes `<local-command-stdout>Goal set: …` (or, for an arm drained from the
type-ahead queue, its arm instruction) for every arm. `deliver_goal` already
scans the transcript BEFORE any keystroke (`scan_goal_markers(tpath)`), and that
scan returns the byte offset after the last complete line. Bytes past that
offset were written after the keystroke by construction, so no clock is compared
(the sweep `now` is frozen at sweep start; dev1 logged the arm's write 14 s after
it). A stale `Goal set:` sits before the offset and is never read. A `cleared`
marker after it is the newest one and refuses. A submit CC reads as a plain
prompt writes a `user` turn but no marker, so the #720 protection holds.
"""
import os

import watchdog


def transcript_arm_landed(tpath, off, log=None):
    """True iff the newest `/goal` marker in the bytes appended to `tpath` after
    `off` (the pre-keystroke `scan_goal_markers` offset) is `state == "set"`.
    `log` (optional) gets one `ARM-CONFIRMED src=transcript` line on a confirm.

    Fail-safe False (the caller falls back to the pane check, then to today's
    verify-failed / verify-failed-live split): no path or no offset, an
    unreadable file, or a file SHORTER than `off` (truncated/rotated; the scan
    would fall back to its tail bootstrap and could return a stale marker).
    Never raises."""
    if not tpath or off is None:
        return False
    try:
        if os.path.getsize(tpath) < off:
            return False
    except OSError:
        return False
    _new_off, mark = watchdog.scan_goal_markers(tpath, off=off)
    if not mark or mark.get("state") != "set":
        return False
    if log is not None:
        log("ARM-CONFIRMED src=transcript tpath=%s" % os.path.basename(str(tpath)))
    return True
