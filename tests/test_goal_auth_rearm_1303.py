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
import json
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP, DeliverGoalFakeTmux, _isolate_goal_state,
    _write_goal_marker, _write_marker_transcript)
from test_goal_auth_clear_675 import (  # noqa: E402
    _COND, _TEMPLATE, _iso, _write_auth_clear_marker)
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


class TestAuthRearmWaitsForAWorkingSession(unittest.TestCase):
    """Review 🟡 x2: a re-arm typed into a process that still holds the revoked
    token earns a new 401 + a new clear (an unbounded re-arm loop, and a newer
    401 that makes job 1 read the credentials as stale). Record the re-arm only
    once the session produced a real reply after the 401, and prove it end to
    end on ONE sweep `state` (#1128): dark-watch records, deliver_goal types."""

    CWD = CWD

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        _real_kill_switch(self)
        p = m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                           return_value=(False, ""))
        p.start()
        self.addCleanup(p.stop)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = Path(d.name)

    def _append(self, sid, entry):
        p = self.proj / wd.encode_project_dir(self.CWD) / (sid + ".jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _cleared_by_rotation(self, sid):
        tp = _write_marker_transcript(self.proj, self.CWD, sid)
        _write_goal_marker(self.proj, self.CWD, sid, "Goal set: %s" % _COND,
                           ts_epoch=500)
        self._append(sid, {"type": "assistant", "isApiErrorMessage": True,
                           "timestamp": _iso(550), "message": {
                               "id": "e1", "content": [{"type": "text", "text":
                                   "API Error: 401 OAuth token revoked · "
                                   "Please run /login"}]}})
        _write_auth_clear_marker(self.proj, self.CWD, sid, ts_epoch=600)
        return tp

    def _watch(self, sid, tp, state):
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   transcript_path=tp)
        out = goal.goal_dark_watch(100000, run=tmux, send_fn=lambda mm, **k: None,
                                   projects_dir=self.proj, state=state,
                                   sleep_fn=lambda s: None,
                                   obligation_fn=lambda cwd: (5, 100),
                                   rearm_fn=lambda cwd: (_TEMPLATE, "full"),
                                   requests_path=self.reqp)
        logs = out[0] if isinstance(out, tuple) else out
        return tmux, logs

    def test_still_dead_session_is_held_not_recorded(self):
        sid = "sess-1303-dead"
        tp = self._cleared_by_rotation(sid)
        _tmux, logs = self._watch(sid, tp, {})
        self.assertNotIn(sid, goal.load_goal_requests(self.reqp), logs)
        self.assertTrue(any("revoked-token 401" in ln for ln in logs), logs)

    def test_recovered_session_is_rearmed_end_to_end(self):
        sid = "sess-1303-back"
        tp = self._cleared_by_rotation(sid)
        self._append(sid, {"type": "assistant", "timestamp": _iso(700),
                           "message": {"id": "r1", "content": [
                               {"type": "text", "text": "Pokračujem na #12."}]}})
        state = {}
        tmux, logs = self._watch(sid, tp, state)
        req = goal.load_goal_requests(self.reqp).get(sid)
        self.assertIsInstance(req, dict, logs)
        self.assertEqual(req["origin"], goal._GOAL_AUTH_REARM_ORIGIN)
        dlogs = []
        word = goal.deliver_goal(sid, self.CWD, req["text"], "full", run=tmux,
                                 projects_dir=self.proj, now=100000,
                                 request_ts=100000, state=state,
                                 sleep_fn=lambda s: None, logs=dlogs,
                                 origin=goal._GOAL_AUTH_REARM_ORIGIN)
        self.assertEqual(word, "sent", dlogs)
        self.assertIn(_TEMPLATE, tmux.typed_texts(), dlogs)


if __name__ == "__main__":
    unittest.main()
