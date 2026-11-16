"""#1309 (dasio 11.-12.11.) -- CC ends a /goal with a structured `goal_status`
attachment (`met: true`, or `failed: true`). The marker scanner must read that
record as the END of the goal, exactly like CC itself does when it restores a
goal on resume (2.1.325: the newest `goal_status` with met/failed = no goal).

Before the fix the scanner knew only the arm sentence and the
`<local-command-stdout>Goal set/cleared:` / error-clear shapes, so a completed
goal kept reading `set` forever: one-glance said `armed=yes src=goal_mark` for a
dasio session whose goal had ended at 23:15:58Z via (A), and the armed riders
ran instead of the ended-goal one.
"""

import json
import os
import tempfile
import time
import unittest
import unittest.mock as m
from pathlib import Path

import watchdog as wd
from watchdog import goal, goal_scan, session_status

from _goal_arm_helpers import (
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

COND = ("STOP CONDITIONS — the loop is DONE the moment EITHER holds, both "
        "checkable from the transcript: (A) BLOCKED ON MY ANSWER; (B) BACKLOG EMPTY.")


def _arm(ts):
    content = (wd.GOAL_ARM_ACTIVE_PREFIX + COND + '". Briefly acknowledge the '
               "goal, then immediately start working toward it.")
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": content}}


def _status(ts, **fields):
    att = {"type": "goal_status", "condition": COND}
    att.update(fields)
    return {"type": "attachment", "timestamp": ts, "attachment": att}


class GoalStatusEndMarker(unittest.TestCase):
    def _transcript(self, entries):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        self.addCleanup(lambda: (os.remove(p), os.rmdir(d)))
        return p

    def _mark(self, entries):
        _off, mark = goal_scan.scan_goal_markers(self._transcript(entries))
        return mark

    def test_met_status_ends_the_goal(self):
        mark = self._mark([
            _arm("2026-11-11T20:49:49.000Z"),
            _status("2026-11-11T20:49:49.995Z", met=False, sentinel=True),
            _status("2026-11-11T22:00:00.000Z", met=False),
            _status("2026-11-11T23:15:58.209Z", met=True, iterations=8,
                    reason="Condition (A) is satisfied."),
        ])
        self.assertEqual(mark["state"], "cleared")
        self.assertEqual(mark["clear_kind"], "met")
        self.assertEqual(mark["payload"], COND)
        self.assertIsNotNone(mark["ts"])

    def test_failed_status_ends_the_goal(self):
        mark = self._mark([_arm("2026-11-11T20:49:49.000Z"),
                           _status("2026-11-11T21:00:00.000Z", met=False,
                                   failed=True)])
        self.assertEqual((mark["state"], mark["clear_kind"]), ("cleared", "failed"))

    def test_not_met_evaluation_keeps_the_arm(self):
        mark = self._mark([_arm("2026-11-11T20:49:49.000Z"),
                           _status("2026-11-11T20:49:49.995Z", met=False,
                                   sentinel=True),
                           _status("2026-11-11T21:00:00.000Z", met=False)])
        self.assertEqual(mark["state"], "set")

    def test_rearm_after_met_is_set_again(self):
        mark = self._mark([_arm("2026-11-11T20:49:49.000Z"),
                           _status("2026-11-11T23:15:58.000Z", met=True),
                           _arm("2026-11-12T05:00:00.000Z")])
        self.assertEqual(mark["state"], "set")

    def test_text_quoting_the_record_is_not_state(self):
        # A user prompt or tool result that merely CONTAINS the JSON is content.
        quoted = json.dumps(_status("2026-11-11T23:15:58.000Z", met=True))
        mark = self._mark([
            _arm("2026-11-11T20:49:49.000Z"),
            {"type": "user", "timestamp": "2026-11-11T23:20:00.000Z",
             "message": {"role": "user", "content": quoted}},
            {"type": "user", "timestamp": "2026-11-11T23:21:00.000Z",
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "content": quoted}]}},
        ])
        self.assertEqual(mark["state"], "set")

    def test_sidechain_status_is_not_the_main_goal(self):
        side = _status("2026-11-11T23:15:58.000Z", met=True)
        side["isSidechain"] = True
        mark = self._mark([_arm("2026-11-11T20:49:49.000Z"), side])
        self.assertEqual(mark["state"], "set")

    def test_heartbeat_reads_not_armed_after_met(self):
        p = self._transcript([_arm("2026-11-11T20:49:49.000Z"),
                              _status("2026-11-11T23:15:58.000Z", met=True)])
        self.assertIs(session_status.goal_armed_from_transcript(p), False)

    def test_seed_scan_sees_the_end(self):
        p = self._transcript([_arm("2026-11-11T20:49:49.000Z"),
                              _status("2026-11-11T23:15:58.000Z", met=True)])
        _off, mark, status = goal_scan.seed_goal_marker(p)
        self.assertEqual(status, "found")
        self.assertEqual(mark["clear_kind"], "met")


class DarkWatchQuietOnMet(unittest.TestCase):
    """A met goal is a normal end: no goal-lost journal line, no report, no
    arm request, no keystroke (#1324 stands: the watchdog never re-arms)."""

    CWD = "/home/newlevel/devel/goalmet1309"

    def setUp(self):
        _isolate_goal_state(self)
        p = m.patch.object(goal, "record_goal_request",
                           side_effect=AssertionError("recorded an arm"))
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = Path(d.name)

    def test_met_goal_ends_quietly(self):
        sid = "sid-goal-met-1309"
        _write_marker_transcript(self.proj, self.CWD, sid)
        _write_goal_marker(self.proj, self.CWD, sid, "Goal set: %s" % COND,
                           ts_epoch=500)
        tp = self.proj / wd.encode_project_dir(self.CWD) / (sid + ".jsonl")
        with open(tp, "a", encoding="utf-8") as f:
            f.write(json.dumps(_status("1970-01-01T00:10:00.000Z", met=True)) + "\n")
        aged = time.time() - 3600
        os.utime(tp, (aged, aged))
        state, sends, lines = {}, [], []
        for i in range(14):           # long enough for a dark confirmation run
            tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                       GOAL_IDLE_CAP)
            lines += goal.goal_dark_watch(
                100000 + 90 * i, run=tmux, state=state, projects_dir=self.proj,
                send_fn=lambda msg, **k: sends.append((msg, k)),
                sleep_fn=lambda s: None,
                rearm_fn=lambda cwd: ("/goal " + COND, "full"))
            self.assertEqual(tmux.sent, [])
        self.assertEqual(state["goal_mark"][sid]["mark"]["clear_kind"], "met")
        self.assertFalse(any("goal-lost" in ln for ln in lines), lines)
        self.assertEqual(sends, [])


if __name__ == "__main__":
    unittest.main()
