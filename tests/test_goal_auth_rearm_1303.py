"""#1303 -- the owner's autopilot died at every overnight token rotation.

Incident (dasio@dev1, 11.11.2026; same on 9.11.): the owner armed `/autopilot`
at 20:03Z. At 02:00Z the nightly rotation revoked the session token, Claude
Code printed `OAuth token revoked` and cleared the goal ("Goal cleared after an
unrecoverable error (authentication failed)"). The watchdog saw it and recorded
an `auth-rearm` (#675), but `_declared_window_nudge` gave that origin the
staged `goal-sweep` kind, which is OFF on every box since #1023, so the delivery
ended `drop:nudge-off` with nothing typed. 13 workable tickets sat idle until
the owner came back in the morning.

An `auth-rearm` restores a goal the OWNER armed, cleared only by an auth error
(#890 counts it a RECOVERY-class origin, exempt from the daily pane budget), so
it rides the always-on `goal-arm` kind like the owner's own `self-callback`.
The other watchdog origins (dark/fulfilled/answer-rearm) keep the staged kind.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import _isolate_goal_state  # noqa: E402
from test_goal_self_arm_1181 import CWD, PAYLOAD, _pane, _real_kill_switch  # noqa: E402


class TestAuthRearmNudgeKind(unittest.TestCase):
    def test_auth_rearm_rides_the_always_on_recovery_kind(self):
        self.assertEqual(goal._declared_window_nudge(CWD, "auth-rearm"), "goal-arm")
        self.assertIn("goal-arm", wd.RECOVERY_NUDGE_KINDS)

    def test_other_watchdog_rearms_keep_the_staged_kind(self):
        for origin in ("dark-rearm", "fulfilled-rearm", "answer-rearm"):
            with self.subTest(origin=origin):
                self.assertEqual(goal._declared_window_nudge(CWD, origin), "goal-sweep")


class TestAuthRearmTypesWithStagedKindsOff(unittest.TestCase):
    """The production state: every staged kind OFF, the auth re-arm still types."""

    def setUp(self):
        _isolate_goal_state(self)
        _real_kill_switch(self)
        p = m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                           return_value=(False, ""))
        p.start()
        self.addCleanup(p.stop)

    def test_precondition_goal_sweep_is_off(self):
        self.assertFalse(wd.nudges_enabled("goal-sweep"))

    def test_auth_rearm_after_rotation_types_the_goal(self):
        sid = "sess-1303-auth"
        proj, tmux = _pane(self, sid)
        logs = []
        now = 100000
        word = goal.deliver_goal(sid, CWD, PAYLOAD, "full", run=tmux,
                                 projects_dir=proj, now=now, request_ts=now,
                                 state={}, sleep_fn=lambda s: None, logs=logs,
                                 origin="auth-rearm")
        self.assertNotEqual(word, "drop:nudge-off", logs)
        self.assertIn(PAYLOAD, tmux.typed_texts(), logs)
        self.assertEqual(word, "sent", logs)


if __name__ == "__main__":
    unittest.main()
