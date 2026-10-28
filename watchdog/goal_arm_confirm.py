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

`tpath` is the NEWEST transcript in the cwd's project dir
(`find_active_transcript`), which is not always session `sid`'s own file when
two sessions share a cwd. A marker in ANOTHER session's file must never confirm
our arm (a `sent` is terminal: the owner's request would be lost), so the
confirm offset is only handed out when the file's stem IS `sid`
(`scan_with_confirm_offset`); otherwise the confirm stays pane-only.
"""
import os
from pathlib import Path

import watchdog


def scan_with_confirm_offset(tpath, sid):
    """`deliver_goal`'s ONE pre-keystroke marker scan: returns
    `(confirm_off, newest_mark)` where `newest_mark` is exactly
    `scan_goal_markers(tpath)`'s marker (the #170 cleared-after-request input)
    and `confirm_off` is its resume offset, or None when `tpath` is not session
    `sid`'s own transcript (stem mismatch -> pane-only confirm)."""
    off, mark = watchdog.scan_goal_markers(tpath)
    return (off if Path(str(tpath)).stem == sid else None), mark


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
        log("ARM-CONFIRMED src=transcript sid=%s" % Path(str(tpath)).stem)
    return True


def _own_transcript(projects_dir, cwd, sid):
    """Session `sid`'s OWN transcript path (`<projects>/<encoded cwd>/<sid>.jsonl`),
    addressed by sid -- never "the newest in the folder"."""
    base = projects_dir or watchdog.PROJECTS_DIR
    return str(Path(base) / watchdog.encode_project_dir(cwd) / (str(sid) + ".jsonl"))


SELF_CALLBACK_ORIGIN = "self-callback"   # == goal._GOAL_SELF_CALLBACK_ORIGIN (locked by a test)


def request_mark_offset(projects_dir, cwd, sid, origin):
    """#1265 follow-up -- the byte size of `sid`'s own transcript at the moment a
    `self-callback` request is recorded (stored as the request's `moff`), or None
    for any other origin or when it cannot be read. Bytes past it were written
    after the request."""
    if origin != SELF_CALLBACK_ORIGIN:
        return None
    try:
        return os.path.getsize(_own_transcript(projects_dir, cwd, sid))
    except OSError:
        return None


def landed_since_request(projects_dir, cwd, sid, moff):
    """#1265 follow-up (supervisor ROZHODNUTÉ) -- True iff `sid`'s own transcript
    has a newest `/goal` marker `set` written after the request's `moff`: the arm
    landed after the 8 s confirm window, so a pending owner arm must not be
    retyped (`drop:already-armed`). A marker that predates the request never
    counts (#1113 keeps the owner's arm). A missing / non-int `moff` (a legacy
    request), a missing transcript, a newest `cleared` marker or a file shorter
    than `moff` -> False, today's behaviour. Clock-free, like the confirm."""
    if isinstance(moff, bool) or not isinstance(moff, int) or moff < 0:
        return False
    return transcript_arm_landed(_own_transcript(projects_dir, cwd, sid), moff)


def late_arm_drop(entry, sid, cwd, projects_dir):
    """`goal_sweep`'s pre-delivery check: the `DROP ... drop:already-armed` log
    line when a pending owner (`self-callback`) request's arm landed after it was
    recorded (`landed_since_request`), else None. The caller clears the request
    terminally and types nothing."""
    if entry.get("origin") != SELF_CALLBACK_ORIGIN:
        return None
    if not landed_since_request(projects_dir, cwd, sid, entry.get("moff")):
        return None
    return ("DROP (goal-sweep) %s sid=%s -> drop:already-armed (Goal set: in the "
            "transcript after the request, #1265)" % (watchdog.project_label(cwd), sid))
