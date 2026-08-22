"""#623 — a LIVE, armed `/goal` loop whose stored condition PREDATES the
shipped template must be re-armed.

Root cause (validated live on montalu1): `watchdog/goal.py` re-arms only a
CONFIRMED-DEAD loop (`goal_dark_watch`) or on a real `/autopilot` invocation.
An ALIVE armed loop carrying a condition older than the deployed SKILL.md
template is never re-read, so a `/goal` template change (e.g. #621's saturation
clause) lands on disk and stays INERT until the loop dies. This suite locks the
detection (`_classify_armed_condition`) and the re-arm-request path integrated
into `goal_dark_watch`'s `armed is True` branch, delivered by the EXISTING
`goal_sweep`/`deliver_goal` verified channel as a `/goal` REPLACE.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

# The stable opening every autopilot /goal condition carries (goal_registry's
# `header` clause). An OLD armed condition (some earlier template) and the NEW
# shipped template BOTH open with it; they DIFFER only past it -> "stale".
_SIG = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"
_OLD_COND = (_SIG + ", both checkable from the transcript: (A) an OLDER wording "
             "of the stop conditions, from before the shipped template changed.")
_NEW_TEMPLATE = ("/goal " + _SIG + ", both checkable from the transcript: (A) the "
                 "NEW wording carrying the saturation clause: SATURATE parallel "
                 "isolation:worktree autopilot-worker lanes.")


class TestStaleArmedRearmRecorded(unittest.TestCase):
    CWD = "/home/newlevel/devel/stalerearm"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_stale_armed_loop_records_a_rearm(self):
        # RED before GREEN: a LIVE, armed loop (GOAL_ARMED_CAP -> pane_goal_armed
        # True) whose marker condition PREDATES the shipped template records a
        # stale-rearm request so goal_sweep/deliver_goal can REPLACE it. Today
        # dark_watch's armed==True branch reads nothing about WHICH condition is
        # armed and records nothing -> this fails.
        proj = self._dir()
        sid = "sess-stale-1"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + _OLD_COND,
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP)
        reqs = self._dir() / "goal-requests.json"
        sent = []
        goal.goal_dark_watch(
            100000, run=tmux, send_fn=lambda mm, **k: sent.append(mm),
            projects_dir=proj, state={}, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (7, 100000),      # workable, fresh
            rearm_fn=lambda cwd: (_NEW_TEMPLATE, "branch-merge"),
            requests_path=reqs)
        req = goal.load_goal_requests(reqs).get(sid)
        self.assertIsInstance(req, dict,
                              "a stale-rearm request must be recorded")
        self.assertEqual(req.get("origin"), "stale-rearm")
        self.assertEqual(req.get("text"), _NEW_TEMPLATE)
        self.assertEqual(tmux.sent, [],
                         "dark_watch itself records a request, never keystrokes")


if __name__ == "__main__":
    unittest.main()
