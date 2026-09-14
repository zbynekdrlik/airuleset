"""#1023 — idle-pane-only delivery: the #921 AGED busy-waiting override is
REMOVED, so a machine nudge is delivered ONLY into an idle pane. A pane that has
been busy-waiting for the "Waiting for N background agents" state — for ANY
duration, including hours — must DEFER with `hold:busy`, never type (the
queued-into-a-busy-pane spam the owner reported).

RED against the pre-#1023 tree: `_busy_waiting_with_age` returns `(True, True)`
once the wait has aged past BUSY_WAITING_AGE_BOUND_S (10 min), and every rider's
`if _X_busy and not _X_aged` override then TYPES into the busy pane. GREEN once
the aged override is gone (a busy pane always defers).
"""
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import ops_wait_recheck as ow  # noqa: E402
from watchdog import goal as goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_ARMED_CAP, _encode, _write_marker_transcript)

NOW = 1_000_000
DAY = 24 * 3600
BUSY_CAP = "Waiting for 2 background agents to finish\n❯ "
# an ARMED /goal pane that is ALSO busy-waiting on background agents — passes
# _lane_boundary_ok (`(True,'input','')`) yet must NEVER be typed into (req-2).
BUSY_ARMED_CAP = ("● Predošlá práca hotová.\n"
                  "Waiting for 2 background agents to finish\n❯ \n"
                  "  ctx ███░  caveman:lite  ◎ /goal active\n")


class TestBusyWaitingHasNoAgedOverride(unittest.TestCase):
    def test_busy_state_still_detected(self):
        # the plain busy-waiting reader stays — a Waiting pane reads busy.
        self.assertTrue(ow._pane_busy_waiting(BUSY_CAP))
        self.assertFalse(ow._pane_busy_waiting("❯ "))   # idle prompt

    def test_aged_override_machinery_removed(self):
        # #1023: the whole aged-override machinery is DELETED, not left as dead code.
        self.assertFalse(hasattr(ow, "_busy_waiting_with_age"))
        self.assertFalse(hasattr(ow, "BUSY_WAITING_AGE_BOUND_S"))


class TestQueueArrivalIdleOnly(unittest.TestCase):
    CWD = "/home/newlevel/devel/idleonly"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-1023-idle")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, tmux, state):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return qa.goal_queue_arrival_recheck(
                NOW, tmux, {self.sid: {"base": [1], "first_seen": NOW - DAY}},
                self.sid, self.CWD, "%9", self.tpath, "sess:0", False, set(),
                queue_fetch=lambda cwd: [1, 2], state=state,
                sleep_fn=lambda *a, **k: None, captured=BUSY_CAP)

    def test_long_busy_pane_defers_zero_keystrokes(self):
        # busy-waiting for 3 h: NO keystroke, hold:busy, base kept (re-detect).
        tmux = self._tmux()
        state = {"busy_first_seen": {self.sid: NOW - 3 * 3600}}
        logs = self._run(tmux, state)
        self.assertEqual(tmux.typed_texts(), [],
                         "a long-busy pane must NEVER be typed into (aged override gone)")
        self.assertTrue(any("hold:busy" in ln for ln in logs), logs)


class TestLaneOccupancyIdleOnly(unittest.TestCase):
    """#1023 🔴 (review #2): the lane-occupancy REFILL rider must ALSO defer on a
    busy-waiting pane — it had NO `_pane_busy_waiting` gate (every sibling rider
    has one), so a busy pane fell through to a keystroke, reintroducing the exact
    spam #1023 kills (a submit swallowed into a running turn, the /goal orphaned).
    In goal_lane_sweep a busy pane forces the INDIVIDUAL path (batch_collect=None),
    which is precisely where the gate was missing.

    RED against the pre-fix tree: with a busy-ARMED capture + backlog + 0 workers,
    the rider reaches the send and types. GREEN once it emits `hold:busy` and types
    nothing."""
    CWD = "/home/newlevel/devel/laneidle"
    SID = "sess-lane-idle-1"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_busy_armed_pane_defers_zero_keystrokes(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   BUSY_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", BUSY_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={}, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [],
                         "a busy-waiting pane must NEVER be typed into (req-2)")
        self.assertTrue(any("hold:busy" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
