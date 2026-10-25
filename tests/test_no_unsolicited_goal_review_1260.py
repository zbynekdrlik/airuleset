"""#1260 follow-up, review round: a retired-origin request leaves the store, and a
declared window gets no always-on re-arm keystroke.

RED against 6377d922:
  (a) `goal_sweep` logged `SKIP ... drop:declared-virgin-retired` every sweep but
      never cleared the request (the word was not terminal), so `status` read
      "arming" forever for a session the owner never armed;
  (b) with the owner kill-switch set (david3), the leftover could never be cleared;
  (c) `_declared_window_nudge` still gave a watchdog dark/auth re-arm into a
      declared window the ALWAYS-ON `goal-arm` kind, bypassing the #1023 per-kind
      switch; its only reason was the retired #1038 virgin arm.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
import cli_fleet  # noqa: E402

from tests._goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _write_marker_transcript,
    _isolate_goal_state,
)

D3_CWD = "/home/david3/devel/odoo/odoo-erp"
WINS = [{"name": "d3", "cwd": D3_CWD, "role": None, "mode": "sequential"}]


class TestLeftoverGone1260(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        sp = self.dir / "api-watchdog-state.json"
        sp.write_text("{}", encoding="utf-8")
        p = m.patch.object(wd, "STATE_PATH", sp)
        p.start()
        self.addCleanup(p.stop)
        self.proj = self.dir / "projects"
        self.proj.mkdir()
        self.sid = "128486e9-leftover"
        _write_marker_transcript(self.proj, D3_CWD, self.sid)
        self.req = str(self.dir / "goal-requests.json")
        goal.record_goal_request(self.sid, D3_CWD, "/goal X", "fork-no-merge",
                                 now=1000, path=self.req, origin="declared-virgin")

    def _sweep(self, now, disabled=False):
        tmux = DeliverGoalFakeTmux([("%0", "claude", D3_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        sent = []
        with m.patch.object(cli_fleet, "box_windows", return_value=WINS), \
             m.patch.object(wd, "_owner_disabled",
                            side_effect=lambda kind: disabled and kind == "goal"), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            logs = goal.goal_sweep(now, run=tmux, projects_dir=self.proj,
                                   requests_path=self.req, state={},
                                   send_fn=lambda *a, **k: sent.append(a),
                                   sleep_fn=lambda s: None)
        return tmux, logs, sent

    def test_one_sweep_clears_the_leftover(self):
        tmux, logs, sent = self._sweep(1100)
        self.assertEqual(goal.load_goal_requests(self.req), {},
                         "a retired-origin request must leave the store; logs=%r" % (logs,))
        self.assertEqual(tmux.sent, [])
        self.assertEqual(sent, [], "no owner ping for a retired request")

    def test_kill_switch_still_clears_the_leftover(self):
        tmux, logs, sent = self._sweep(1100, disabled=True)
        self.assertEqual(goal.load_goal_requests(self.req), {},
                         "the kill-switch must not pin a retired request; logs=%r" % (logs,))
        self.assertEqual(tmux.sent, [])
        self.assertEqual(sent, [])

    def test_retired_word_is_terminal(self):
        self.assertIn("drop:declared-virgin-retired", goal._GOAL_TERMINAL_WORDS)


class TestDeclaredNudge1260(unittest.TestCase):
    def test_watchdog_rearm_into_declared_window_stays_staged(self):
        with m.patch.object(cli_fleet, "box_windows", return_value=WINS):
            for origin in ("dark-rearm", "auth-rearm", "fulfilled-rearm", "answer-rearm"):
                with self.subTest(origin=origin):
                    self.assertEqual(goal._declared_window_nudge(D3_CWD, origin=origin),
                                     "goal-sweep")

    def test_owner_arm_stays_always_on(self):
        with m.patch.object(cli_fleet, "box_windows", return_value=WINS):
            self.assertEqual(goal._declared_window_nudge(D3_CWD, origin="self-callback"),
                             "goal-arm")


if __name__ == "__main__":
    unittest.main()
