"""#921 — _pane_busy_waiting starvation: when the 'Waiting for N background
agents to finish' line persists >= 10 min AND the input box is a bare free
prompt (kind=='input'), the keystroke must be delivered anyway — CC queues
the submitted prompt and fires it when the turn unblocks.

RED against the pre-#921 tree: _pane_busy_waiting always defers,
deliver_goal always returns skip:busy on a Waiting pane regardless of how
long the state has persisted.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import goal                                # noqa: E402
from watchdog import ops_wait_recheck as _owr             # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_marker_transcript,
)


# A pane capture showing a bare `❯` input AND the persistent Waiting line
# (the m1 shape: kind=="input" with a Waiting spinner a row above).
# Includes the status footer so pane_goal_armed returns False (not None).
_WAITING_PANE = ("● Predošlá práca hotová.\n"
                 "  ✻ Waiting for 7 background agents to finish\n"
                 "❯ \n"
                 "  ctx ███░  caveman:lite\n")

_WAITING_1_PANE = ("● Predošlá práca hotová.\n"
                   "  ✻ Waiting for 1 background agent to finish\n"
                   "❯ \n"
                   "  ctx ███░  caveman:lite\n")


class TestBusyWaitingAge(unittest.TestCase):
    """The age-bounded _pane_busy_waiting wrapper must deliver after the
    threshold when the input box is bare."""

    def test_pane_busy_waiting_is_true_for_waiting_pane(self):
        """Sanity: the underlying predicate is True for a Waiting pane."""
        self.assertTrue(_owr._pane_busy_waiting(_WAITING_PANE))

    def test_pane_busy_waiting_is_false_for_normal_pane(self):
        """Sanity: the predicate is False for a normal idle pane."""
        self.assertFalse(_owr._pane_busy_waiting(GOAL_IDLE_CAP))


class TestGoalSweepBusyAgeOut(unittest.TestCase):
    """deliver_goal must deliver (not skip:busy) after 10 min of persistent
    Waiting state when the input box is bare."""

    CWD = "/home/newlevel/devel/busyage921"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        # Persistent state dict shared across sweeps — the busy_first_seen
        # tracking must survive across goal_sweep calls (like run_once's state).
        self.state = {}

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _sweep(self, proj, sid, cap, now=100000.0):
        # Record the request FRESH relative to the sweep's `now` so it does
        # not lapse on the age cap.
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=now - 10, path=self.reqp,
                                 origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, sid)],
                                   cap, model_type=True)
        logs = goal.goal_sweep(now, run=tmux, projects_dir=proj,
                               requests_path=self.reqp, sleep_fn=lambda s: None,
                               state=self.state)
        return logs, tmux

    def test_busy_waiting_less_than_threshold_defers(self):
        """A Waiting pane younger than 10 min must still skip:busy."""
        proj = self._dir()
        sid = "sess-busy-young"
        _write_marker_transcript(proj, self.CWD, sid)
        logs, tmux = self._sweep(proj, sid, _WAITING_PANE, now=100000.0)
        combined = "\n".join(logs)
        self.assertIn("skip:busy", combined)
        self.assertEqual(tmux.sent, [])

    def test_busy_waiting_past_threshold_delivers(self):
        """After >= 10 min of persistent Waiting, deliver_goal must NOT return
        skip:busy — it must attempt delivery (the core fix)."""
        proj = self._dir()
        sid = "sess-busy-old"
        _write_marker_transcript(proj, self.CWD, sid)

        # First sweep at t=100000 — seeds the first-seen tracking
        logs1, tmux1 = self._sweep(proj, sid, _WAITING_PANE, now=100000.0)
        combined1 = "\n".join(logs1)
        self.assertIn("skip:busy", combined1)

        # Second sweep at t=100700 (>= 600s threshold) — must deliver
        # Re-record the goal request fresh relative to the second sweep
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=100690, path=self.reqp,
                                 origin="self-callback")
        tmux2 = DeliverGoalFakeTmux([("%9", "claude", self.CWD, sid)],
                                    _WAITING_PANE, model_type=True)
        logs2 = goal.goal_sweep(100700.0, run=tmux2, projects_dir=proj,
                                requests_path=self.reqp, sleep_fn=lambda s: None,
                                state=self.state)
        combined2 = "\n".join(logs2)
        # After the age bound, the sweep must NOT return skip:busy — it must
        # attempt the delivery (either send or another non-busy skip reason)
        self.assertNotIn("skip:busy", combined2,
                         "After 10+ min of persistent Waiting, deliver_goal "
                         "must NOT return skip:busy — the age bound should "
                         "allow delivery")
        # L3 review fix: tighten — verify a keystroke was actually attempted
        self.assertNotEqual(tmux2.sent, [],
                            "After the age bound, a keystroke should have been "
                            "attempted (sent list should be non-empty)")

    def test_busy_waiting_resets_when_waiting_clears(self):
        """When the Waiting line disappears between sweeps, the first-seen
        tracking must reset — a new Waiting state starts the clock over."""
        proj = self._dir()
        sid = "sess-busy-reset"
        _write_marker_transcript(proj, self.CWD, sid)

        # First sweep: Waiting pane at t=100000
        logs1, _ = self._sweep(proj, sid, _WAITING_PANE, now=100000.0)
        self.assertIn("skip:busy", "\n".join(logs1))

        # Second sweep: normal pane (Waiting cleared) at t=100400
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=100390, path=self.reqp,
                                 origin="self-callback")
        tmux2 = DeliverGoalFakeTmux([("%9", "claude", self.CWD, sid)],
                                    GOAL_IDLE_CAP, model_type=True)
        goal.goal_sweep(100400.0, run=tmux2, projects_dir=proj,
                        requests_path=self.reqp, sleep_fn=lambda s: None,
                        state=self.state)
        # Normal pane — not busy (delivery proceeds, resets first-seen)

        # Third sweep: Waiting again at t=100500 — should be treated as
        # a NEW Waiting (first-seen reset), so must skip:busy (< 10 min)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=100490, path=self.reqp,
                                 origin="self-callback")
        tmux3 = DeliverGoalFakeTmux([("%9", "claude", self.CWD, sid)],
                                    _WAITING_PANE, model_type=True)
        logs3 = goal.goal_sweep(100500.0, run=tmux3, projects_dir=proj,
                                requests_path=self.reqp, sleep_fn=lambda s: None,
                                state=self.state)
        combined3 = "\n".join(logs3)
        self.assertIn("skip:busy", combined3,
                      "After the Waiting state cleared and reappeared, the "
                      "first-seen tracking must reset — the new Waiting is "
                      "younger than the threshold")


class TestBusyWaitingAgeConstant(unittest.TestCase):
    """The age bound constant must exist and be ~600s."""

    def test_constant_exists(self):
        """BUSY_WAITING_AGE_BOUND_S must be defined in ops_wait_recheck."""
        self.assertTrue(hasattr(_owr, "BUSY_WAITING_AGE_BOUND_S"),
                        "BUSY_WAITING_AGE_BOUND_S constant must exist in "
                        "ops_wait_recheck (the age threshold for the busy-"
                        "waiting override)")
        self.assertGreaterEqual(_owr.BUSY_WAITING_AGE_BOUND_S, 300)
        self.assertLessEqual(_owr.BUSY_WAITING_AGE_BOUND_S, 900)


if __name__ == "__main__":
    unittest.main()
