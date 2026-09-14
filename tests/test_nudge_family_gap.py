"""#1023 — the shared `nudge_gate` per-pane-per-KIND floor, wired into the
job-20 keystroke riders (partition-audit / release-gap / queue-arrival /
lane-occupancy). Each rider consults `nudge_gate.gate_ok(state, sid, category,
now)` right before its send: when THIS kind was delivered to this session within
`_min_interval()` (60 min), the rider DEFERS with `hold:floor` (no keystroke, own
tracking state preserved, retries a later sweep) — never cancels. A DIFFERENT
kind's recent delivery NEVER defers this rider (the pre-#1023 cross-kind family
gap is removed; per-kind staging bounds the total).

Originally #797 (the shared cadence gate) — the family-spacing half was removed
in #1023, so these tests now lock the per-kind floor at the rider level: a recent
SAME kind defers, a recent DIFFERENT kind delivers.
"""

import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: F401
from watchdog import goal
from watchdog import ops_wait_recheck as owr
from watchdog import release_gap as rg
from watchdog import queue_arrival_recheck as qa

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
CAD = 6 * 3600


def _recent(sid, category, ago=60):
    """A gate state with a RECENT delivery of `category` — floors THAT kind on
    `sid` for the next hour (#1023). It NEVER blocks a different kind."""
    return {"nudge_cadence": {sid: {category: NOW - ago}}}


class _Base(unittest.TestCase):
    CWD = "/home/newlevel/devel/famgap"

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
                                              "sess-famgap")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)


class TestOpsWaitGate(_Base):
    def _run(self, tmux, state):
        return owr.goal_ops_wait_recheck(
            NOW, tmux, {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}},
            self.sid, self.CWD, "%9", self.tpath, "sess:0", False, set(),
            ops_wait_fetch=lambda cwd: [41], state=state,
            sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=0)

    def test_recent_same_kind_defers(self):
        # #1023: a partition-audit delivery within the hour floors the next one.
        tmux = self._tmux()
        logs = self._run(tmux, _recent(self.sid, "partition-audit"))
        self.assertEqual(tmux.typed_texts(), [],
                         "a recent SAME-kind delivery must DEFER with hold:floor")
        self.assertTrue(any("hold:floor" in ln for ln in logs))

    def test_recent_different_kind_delivers(self):
        # #1023: a DIFFERENT kind's recent delivery must NOT block this rider.
        tmux = self._tmux()
        self._run(tmux, _recent(self.sid, "u-freshness"))
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))

    def test_open_gate_delivers_and_marks(self):
        tmux = self._tmux()
        state = {}
        self._run(tmux, state)
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))
        self.assertEqual(state["nudge_cadence"][self.sid]["partition-audit"], NOW)


class TestReleaseGapGate(_Base):
    def _run(self, tmux, state):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return rg.goal_release_gap_recheck(
                NOW, tmux,
                {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}},
                self.sid, self.CWD, "%9", self.tpath, "sess:0", False, set(),
                release_state_fetch=lambda cwd: {"ahead": 5, "in_flight": False},
                state=state, sleep_fn=lambda *a, **k: None)

    def test_recent_same_kind_defers(self):
        tmux = self._tmux()
        logs = self._run(tmux, _recent(self.sid, "release-gap"))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("hold:floor" in ln for ln in logs))

    def test_recent_different_kind_delivers(self):
        tmux = self._tmux()
        self._run(tmux, _recent(self.sid, "u-freshness"))
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))

    def test_open_gate_delivers_and_marks(self):
        tmux = self._tmux()
        state = {}
        self._run(tmux, state)
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))
        self.assertEqual(state["nudge_cadence"][self.sid]["release-gap"], NOW)


class TestQueueArrivalGate(_Base):
    def _run(self, tmux, state):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return qa.goal_queue_arrival_recheck(
                NOW, tmux,
                {self.sid: {"base": [1, 2], "first_seen": NOW - DAY}},
                self.sid, self.CWD, "%9", self.tpath, "sess:0", False, set(),
                queue_fetch=lambda cwd: [1, 2, 9], state=state,
                sleep_fn=lambda *a, **k: None)

    def test_recent_same_kind_defers(self):
        tmux = self._tmux()
        logs = self._run(tmux, _recent(self.sid, "queue-arrival"))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("hold:floor" in ln for ln in logs))
        # baseline NOT advanced — the arrival re-detects next sweep (never cancels)
        # (state's qrecs is internal; the log's defer + no keystroke is the lock)

    def test_recent_different_kind_delivers(self):
        tmux = self._tmux()
        self._run(tmux, _recent(self.sid, "u-freshness"))
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))

    def test_open_gate_delivers_and_marks(self):
        tmux = self._tmux()
        state = {}
        self._run(tmux, state)
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))
        self.assertEqual(state["nudge_cadence"][self.sid]["queue-arrival"], NOW)


class TestLaneOccupancyGate(_Base):
    def _run(self, tmux, state):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return goal.goal_lane_occupancy_nudge(
                NOW, tmux, {}, self.sid, self.CWD, "%9", GOAL_ARMED_CAP,
                self.tpath, NOW - goal.GOAL_LANE_IDLE_S - 100, "loc",
                lambda msg, **k: None, False, None, Path(self._proj.name),
                backlog_fetch=lambda cwd: 5, state=state,
                sleep_fn=lambda *a, **k: None)

    def test_recent_same_kind_defers(self):
        tmux = self._tmux()
        logs, owns = self._run(tmux, _recent(self.sid, "lane-occupancy"))
        self.assertEqual(tmux.typed_texts(), [],
                         "a recent SAME-kind delivery must DEFER the lane nudge")
        self.assertTrue(any("hold:floor" in ln for ln in logs))

    def test_recent_different_kind_delivers(self):
        tmux = self._tmux()
        logs, owns = self._run(tmux, _recent(self.sid, "u-freshness"))
        self.assertNotEqual(tmux.typed_texts(), [],
                            "a DIFFERENT kind's recent delivery must NOT defer "
                            "the lane nudge (per-kind independence)")

    def test_control_open_gate_delivers_and_marks(self):
        tmux = self._tmux()
        state = {}
        logs, owns = self._run(tmux, state)
        self.assertFalse(any("hold:floor" in ln for ln in logs), logs)
        self.assertNotEqual(tmux.typed_texts(), [],
                            "with an open gate the lane nudge is delivered "
                            "(control: the defer is the gate, not the harness)")
        # the delivered lane nudge stamps the shared cadence clock so a sibling
        # family category defers within the family gap (RED if the lane rider's
        # mark_sent is reverted — the burst fix would half-die silently).
        self.assertEqual(state["nudge_cadence"][self.sid]["lane-occupancy"], NOW)


if __name__ == "__main__":
    unittest.main()
