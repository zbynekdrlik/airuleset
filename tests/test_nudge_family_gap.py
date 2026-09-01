"""#797 — the shared `nudge_gate` FAMILY-SPACING floor, wired into the four
EXISTING job-20 keystroke riders (partition-audit / release-gap / queue-arrival /
lane-occupancy). Each rider now consults `nudge_gate.gate_ok(state, sid,
category, now)` right before its send: when a DIFFERENT gated-family category
nudged this session within NUDGE_FAMILY_GAP_S, the rider DEFERS (no keystroke,
own tracking state preserved, retries a later sweep) — never cancels. This kills
the cross-sweep bursts ("chodia jak besne po sebe") without touching any rider's
own cadence semantics (the family gap ignores the SAME category, so a rider's own
repeat is governed only by its own cadence).

RED against the pre-implementation tree: the gate is not wired, so a rider with a
closed family gate STILL types. GREEN once each rider consults `gate_ok`.
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


def _closed_gate(sid, category="u-freshness", ago=60):
    """A gate state with a RECENT nudge of `category` — closes the family gap for
    any OTHER category on `sid`."""
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

    def test_closed_family_gate_defers(self):
        tmux = self._tmux()
        logs = self._run(tmux, _closed_gate(self.sid))
        self.assertEqual(tmux.typed_texts(), [],
                         "a closed family gate must DEFER the partition-audit "
                         "nudge (RED before the gate is wired)")
        self.assertTrue(any("cadence-gate" in ln for ln in logs))

    def test_open_gate_delivers_and_marks(self):
        tmux = self._tmux()
        state = {}
        self._run(tmux, state)
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))
        self.assertEqual(state["nudge_cadence"][self.sid]["partition-audit"], NOW)

    def test_same_category_recent_still_delivers(self):
        # the family gap ignores the SAME category — a partition-audit clock does
        # not block a partition-audit nudge (its own cadence governs that).
        tmux = self._tmux()
        self._run(tmux, _closed_gate(self.sid, "partition-audit"))
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))


class TestReleaseGapGate(_Base):
    def _run(self, tmux, state):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return rg.goal_release_gap_recheck(
                NOW, tmux,
                {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}},
                self.sid, self.CWD, "%9", self.tpath, "sess:0", False, set(),
                release_state_fetch=lambda cwd: {"ahead": 5, "in_flight": False},
                state=state, sleep_fn=lambda *a, **k: None)

    def test_closed_family_gate_defers(self):
        tmux = self._tmux()
        logs = self._run(tmux, _closed_gate(self.sid))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("cadence-gate" in ln for ln in logs))

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

    def test_closed_family_gate_defers(self):
        tmux = self._tmux()
        logs = self._run(tmux, _closed_gate(self.sid))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("cadence-gate" in ln for ln in logs))
        # baseline NOT advanced — the arrival re-detects next sweep (never cancels)
        # (state's qrecs is internal; the log's defer + no keystroke is the lock)

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

    def test_closed_family_gate_defers(self):
        tmux = self._tmux()
        logs, owns = self._run(tmux, _closed_gate(self.sid))
        self.assertEqual(tmux.typed_texts(), [],
                         "a closed family gate must DEFER the lane nudge")
        self.assertTrue(any("cadence-gate" in ln for ln in logs))

    def test_control_open_gate_delivers_and_marks(self):
        tmux = self._tmux()
        state = {}
        logs, owns = self._run(tmux, state)
        self.assertFalse(any("cadence-gate" in ln for ln in logs), logs)
        self.assertNotEqual(tmux.typed_texts(), [],
                            "with an open gate the lane nudge is delivered "
                            "(control: the defer is the gate, not the harness)")
        # the delivered lane nudge stamps the shared cadence clock so a sibling
        # family category defers within the family gap (RED if the lane rider's
        # mark_sent is reverted — the burst fix would half-die silently).
        self.assertEqual(state["nudge_cadence"][self.sid]["lane-occupancy"], NOW)


if __name__ == "__main__":
    unittest.main()
