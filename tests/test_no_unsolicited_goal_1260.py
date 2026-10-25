"""#1260 follow-up (owner 25.10.2026) — the watchdog never STARTS a /goal.

Owner rule: a goal exists only because the owner typed `/autopilot` in that
session. #1038's declared-window virgin arm broke it: on david3 the owner started
a fresh session with `claude-new` (no `/autopilot`) at 14:55:11, and at 14:56:23
job 9 recorded `RECORD (virgin-arm) david3:0.0 sid=128486e9… -> declared-virgin
armed: sequential`. Only a busy pane kept it from being typed.

RED against 0.1.552:
  (a) a never-armed session at an idle prompt in a declared window gets a goal
      request recorded and typed by `goal_sweep`;
  (b) a `declared-virgin` request already on disk (recorded before the deploy,
      e.g. david3's 14:56 one) is delivered as a keystroke instead of dropped.
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
SHORT_GOAL = "/goal STOP CONDITIONS sequential"


def _declared_d3(cwd=D3_CWD):
    return [{"name": "d3", "cwd": cwd, "role": None, "mode": "sequential"}]


def _typed_goal(tmux):
    return [a for a in tmux.sent
            if "-l" in a and isinstance(a[-1], str) and a[-1].startswith("/goal")]


class TestNoUnsolicitedGoal1260(unittest.TestCase):
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

    def test_fresh_session_in_declared_window_gets_no_goal(self):
        proj = self.dir / "projects"
        proj.mkdir()
        sid = "128486e9-fresh-claude-new"
        _write_marker_transcript(proj, D3_CWD, sid)       # no Goal set / cleared marker
        reqp = str(self.dir / "goal-requests.json")
        tmux = DeliverGoalFakeTmux([("%0", "claude", D3_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows", return_value=_declared_d3()), \
             m.patch.object(goal, "_default_rearm_fn",
                            side_effect=lambda cwd: (SHORT_GOAL, "fork-no-merge")), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            logs = []
            for now in (2000, 2400, 4000):
                logs += goal.goal_sweep(now, run=tmux, projects_dir=proj,
                                        requests_path=reqp, state={},
                                        sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "no /goal may be typed into a session the owner started "
                         "without /autopilot; logs=%r" % (logs,))
        self.assertEqual(goal.load_goal_requests(reqp), {},
                         "no goal-arm request may be recorded for it")
        self.assertFalse([ln for ln in logs if "virgin-arm" in ln],
                         "job 9 has no virgin-arm pass any more; logs=%r" % (logs,))

    def test_leftover_declared_virgin_request_is_dropped_not_typed(self):
        proj = self.dir / "projects"
        proj.mkdir()
        sid = "128486e9-leftover"
        _write_marker_transcript(proj, D3_CWD, sid)
        tmux = DeliverGoalFakeTmux([("%0", "claude", D3_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows", return_value=_declared_d3()), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            word = goal.deliver_goal(
                sid, D3_CWD, SHORT_GOAL, "fork-no-merge", run=tmux,
                projects_dir=proj, now=100000, request_ts=100000,
                state={}, sleep_fn=lambda s: None, origin="declared-virgin")
        self.assertEqual(word, "drop:declared-virgin-retired")
        self.assertEqual(tmux.sent, [], "a leftover declared-virgin request is never typed")
        self.assertIn("DROP declared-virgin-retired",
                      Path(self.syncp).read_text(encoding="utf-8"))

    def test_declared_virgin_is_no_longer_a_watchdog_rearm_origin(self):
        self.assertNotIn("declared-virgin", goal._GOAL_WATCHDOG_REARM_ORIGINS)
        self.assertNotIn("declared-virgin", goal._GOAL_USER_CALLBACK_ORIGINS)


if __name__ == "__main__":
    unittest.main()
