"""#1309 -- CC's own END-of-goal record, read as a `/goal` marker.

When a `/goal` loop finishes, Claude Code appends a top-level transcript entry
`{"type": "attachment", "attachment": {"type": "goal_status", "met": true, ...}}`
(or `"failed": true` when it gave up) and drops the goal. It writes no
`Goal cleared:` line for that. CC restores a goal on resume from exactly this
record (2.1.325: the newest `goal_status` whose `met` or `failed` is set means
no goal), so the watchdog reads it the same way.

The dasio incident (11.-12.11.2026): the default goal ended at 23:15:58Z via
stop condition (A) on a `❓ NEEDS YOU`; the scanner, which knew only the arm
sentence and the `Goal set/cleared:` shapes, kept the arm as the newest marker,
so one-glance said `armed=yes src=goal_mark` all night and the armed riders ran
instead of the ended-goal one.

Only a STRUCTURAL record counts: a top-level `attachment` entry of the main
thread. A prompt or tool result that quotes the JSON is content, and a
sidechain (subagent) entry is not the session's goal. A `met: false` record
(every not-met evaluation, and the arm sentinel) is ignored: arming is decided
by the arm sentence alone, so this module can only END a goal, never start one.
"""

# Byte pre-filter for `goal_scan._newest_marker` (raw JSON line, before parse).
GOAL_STATUS_PROBE = b'"goal_status"'


def goal_status_end_marker(entry):
    """`{"state": "cleared", "clear_kind": "met"|"failed", "payload": cond}` for
    a main-thread `goal_status` attachment that ends the goal, else None."""
    if not isinstance(entry, dict) or entry.get("type") != "attachment":
        return None
    if entry.get("isSidechain") is True:
        return None
    att = entry.get("attachment")
    if not isinstance(att, dict) or att.get("type") != "goal_status":
        return None
    if att.get("met") is True:
        kind = "met"
    elif att.get("failed") is True:
        kind = "failed"
    else:
        return None
    cond = att.get("condition")
    return {"state": "cleared", "clear_kind": kind,
            "payload": cond if isinstance(cond, str) and cond else None}


# clear_kind -> the dark-watch journal word, for every end CC made ITSELF.
_CC_END_WORDS = {"auth": "auth-clear", "failed": "failed"}


def cc_end_lines(sid, loc, mark, latch, lost_line):
    """dark-watch's decision line for a goal CC ended itself, once per episode
    (`latch`, keyed by the marker ts), or None when `mark` is no such end (a
    user `/goal clear`, an `error` clear, no marker: the caller just ends the
    episode).

    `auth` (#675) and `failed` (CC gave up) are journalled as a LOST goal via
    `lost_line` (goal._goal_lost_line); `met` is a normal end and gets its own
    `goal-ended:met` line, so a finished loop is never silent (#486: every
    decision is logged). Nothing here re-arms (#1324)."""
    if not isinstance(mark, dict) or mark.get("state") != "cleared":
        return None
    kind = mark.get("clear_kind")
    ts = mark.get("ts")
    if kind in _CC_END_WORDS:
        return lost_line(sid, loc, ts, _CC_END_WORDS[kind], latch=latch)
    if kind != "met":
        return None
    if sid in latch and latch.get(sid) == ts:
        return []
    latch[sid] = ts
    return ["dark-watch %s sid=%s -> goal-ended:met (CC: the condition held; "
            "NOT re-armed, #1324)" % (loc, sid)]
