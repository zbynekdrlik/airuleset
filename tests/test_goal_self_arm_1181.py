"""#1181 -- the owner's `/autopilot` callback never armed a NON-declared pane.

Incident (dev1 iemmixer + fohmixer, 29.9.2026; the controller's own supervisor
pane since 22.9.): every job-9 delivery of a `self-callback` arm request ended
`skip:verify-failed` + `ARM-CONFIRM-FAIL boundary=input box=empty`, the
transcript gained no user turn, and after 3 attempts the request was dropped.

Proven cause (live repro in a private tmux server, matrix on the ticket):
`deliver_goal` gave the owner's OWN arm the staged PRIORITY nudge identity
`goal-sweep` on any pane that is not a declared window. #1023 made every
PRIORITY kind default OFF, and it is OFF on every box, so the one `keys`
primitive withheld every keystroke: nothing was typed, and the zero-keystroke
refusal was reported as a failed verify. gk never showed it because its
declared windows ride the always-on `goal-arm` kind (#1038). Payload length,
the CC version (2.1.283 / 2.1.284) and Remote Control made no difference.

Locked here:
  1. a `self-callback` arm on a non-declared pane with every staged kind OFF
     TYPES the /goal and arms (the root fix: the owner's own arm rides the
     always-on `goal-arm` recovery kind, like the #1128 stream watcher);
  2. a watchdog re-arm the owner's switch withholds is reported as
     `skip:nudge-off` (nothing typed), never as `skip:verify-failed`;
  3. a `self-callback` arm dropped at the strict attempt cap leaves ONE pointer
     line in the pane and a `goal: arm failed (...)` status row.
"""
import json
import os
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
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
)

CWD = "/home/newlevel/devel/iemmixer1181"     # NOT a declared window anywhere
PAYLOAD = "/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds x"


def _real_kill_switch(testcase):
    """The REAL per-kind predicate against an empty HOME = every staged kind OFF,
    exactly the box state (#1063 lesson: pop the conftest bypass, else the
    suite exercises the all-on shortcut, not the switch)."""
    home = TemporaryDirectory()
    testcase.addCleanup(home.cleanup)
    for p in (m.patch.dict(os.environ, {"HOME": home.name}),):
        p.start()
        testcase.addCleanup(p.stop)
    saved = os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
    if saved is not None:
        testcase.addCleanup(os.environ.__setitem__,
                            "AIRULESET_TEST_IGNORE_DISABLE", saved)
    return Path(home.name)


def _pane(testcase, sid):
    proj = TemporaryDirectory()
    testcase.addCleanup(proj.cleanup)
    tp = _write_marker_transcript(proj.name, CWD, sid)
    tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP,
                               model_type=True, transcript_path=tp)
    return Path(proj.name), tmux


class TestSelfCallbackArmTypesAtAllOff(unittest.TestCase):
    """1. The root: the owner's own arm is never withheld by the staged switch."""

    def setUp(self):
        _isolate_goal_state(self)
        _real_kill_switch(self)

    def test_the_box_state_really_has_goal_sweep_off(self):
        # the precondition the incident ran under, read from the real predicate
        self.assertFalse(wd.nudges_enabled("goal-sweep"))

    def test_self_callback_arm_on_a_non_declared_pane_types_and_arms(self):
        sid = "sess-1181-self"
        proj, tmux = _pane(self, sid)
        logs = []
        now = 100000
        word = goal.deliver_goal(sid, CWD, PAYLOAD, "full", run=tmux,
                                 projects_dir=proj, now=now, request_ts=now,
                                 state={}, sleep_fn=lambda s: None, logs=logs,
                                 origin="self-callback")
        self.assertIn(PAYLOAD, tmux.typed_texts(),
                      "the /goal was never typed (withheld by the staged "
                      "switch): %r" % logs)
        self.assertEqual(word, "sent", logs)
        self.assertFalse(any("nudges OFF: suppressed" in ln for ln in logs), logs)

    def test_the_nudge_identity_of_the_owners_arm_is_the_recovery_kind(self):
        self.assertEqual(goal._declared_window_nudge(CWD, "self-callback"),
                         "goal-arm")
        self.assertIn("goal-arm", wd.RECOVERY_NUDGE_KINDS)

    def test_a_watchdog_rearm_on_the_same_pane_keeps_the_staged_kind(self):
        # scope lock: only the owner's own arm changes identity
        self.assertEqual(goal._declared_window_nudge(CWD, "dark-rearm"),
                         "goal-sweep")


class TestWithheldRearmIsNamedNotVerifyFailed(unittest.TestCase):
    """2. A delivery the owner's switch withholds types NOTHING and says so."""

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        _real_kill_switch(self)

    def test_off_rearm_is_skip_nudge_off_with_zero_keystrokes(self):
        sid = "sess-1181-off"
        proj, tmux = _pane(self, sid)
        logs = []
        now = 100000
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            word = goal.deliver_goal(sid, CWD, PAYLOAD, "full", run=tmux,
                                     projects_dir=proj, now=now, request_ts=now,
                                     state={}, sleep_fn=lambda s: None,
                                     logs=logs, origin="dark-rearm")
        self.assertEqual(word, "skip:nudge-off", logs)
        self.assertEqual(tmux.keys(), [], "nothing may be typed at OFF")
        self.assertTrue(any("nudges OFF: suppressed goal-sweep" in ln
                            for ln in logs), logs)
        sync = self.syncp.read_text() if self.syncp.exists() else ""
        self.assertNotIn("ARM-CONFIRM-FAIL", sync,
                         "a withheld keystroke is not a failed arm confirm")
        self.assertIn("SKIP nudge-off(goal-sweep)", sync)

    def test_nudge_off_keeps_the_existing_attempt_cap_bound(self):
        # parity: counted like the verify-failed it replaces, so the cap drop,
        # its ping and the re-record bounding are unchanged for re-arm origins
        self.assertIn("skip:nudge-off", goal._GOAL_KEYSTROKE_SKIPS)


class TestCappedSelfArmIsVisible(unittest.TestCase):
    """3. A capped owner arm leaves one pane line + a status row."""

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        _real_kill_switch(self)

    def _capped_request(self, sid, origin):
        now = 100000
        goal.record_goal_request(sid, CWD, PAYLOAD, "full", now=now - 200,
                                 path=self.reqp, origin=origin)
        d = json.loads(self.reqp.read_text())
        d[sid].update(dl_fails=goal.GOAL_DELIVERY_ATTEMPT_CAP,
                      dl_last="skip:verify-failed")
        self.reqp.write_text(json.dumps(d))
        return now

    def _sweep(self, sid, origin):
        proj, tmux = _pane(self, sid)
        now = self._capped_request(sid, origin)
        state = {}
        logs = goal.goal_sweep(now, run=tmux, projects_dir=proj,
                               requests_path=self.reqp, state=state,
                               sleep_fn=lambda s: None)
        return tmux, state, logs

    def test_self_callback_cap_types_one_pointer_line_and_records_it(self):
        sid = "sess-1181-cap"
        with m.patch.object(wd, "_tmux_client_recent_input",
                            return_value=(False, "")):
            tmux, state, logs = self._sweep(sid, "self-callback")
        self.assertTrue(any("drop:attempt-cap" in ln for ln in logs), logs)
        notices = [t for t in tmux.typed_texts() if "arm failed" in t]
        self.assertEqual(len(notices), 1, "exactly one pointer line: %r"
                         % tmux.typed_texts())
        self.assertIn("/autopilot", notices[0])
        self.assertNotIn(PAYLOAD, tmux.typed_texts(), "never a new /goal type")
        rec = state.get("goal_arm_failed", {}).get(sid)
        self.assertIsNotNone(rec, state)
        self.assertEqual(rec["reason"], "skip:verify-failed")

    def test_a_watchdog_rearm_at_the_cap_gets_no_pane_line(self):
        sid = "sess-1181-rearm-cap"
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            tmux, state, logs = self._sweep(sid, "dark-rearm")
        self.assertFalse(any("arm failed" in t for t in tmux.typed_texts()))
        self.assertFalse(state.get("goal_arm_failed"))

    def test_status_row_names_the_failed_arm(self):
        import airuleset
        sid = "sess-1181-status"
        proj, tmux = _pane(self, sid)
        statep = Path(proj) / "api-watchdog-state.json"
        statep.write_text(json.dumps({"goal_arm_failed": {sid: {
            "ts": 10 ** 10, "reason": "skip:verify-failed", "attempts": 3,
            "cwd": CWD}}}))
        with m.patch.object(wd, "STATE_PATH", statep), \
                m.patch.object(wd, "PROJECTS_DIR", proj), \
                m.patch("time.time", return_value=10 ** 10 + 60):
            row = airuleset.goal_status_probe(CWD, run=tmux, pane_env="%9")
        self.assertEqual(
            row, "goal: arm failed (skip:verify-failed x3) — paste the line "
                 "above or re-run /autopilot")

    def test_an_armed_pane_never_shows_a_stale_failure(self):
        import airuleset
        from _goal_arm_helpers import GOAL_ARMED_CAP
        sid = "sess-1181-armed"
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        _write_marker_transcript(proj.name, CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")],
                                   GOAL_ARMED_CAP)
        statep = Path(proj.name) / "api-watchdog-state.json"
        statep.write_text(json.dumps({"goal_arm_failed": {sid: {
            "ts": 10 ** 10, "reason": "skip:verify-failed", "attempts": 3}}}))
        with m.patch.object(wd, "STATE_PATH", statep), \
                m.patch.object(wd, "PROJECTS_DIR", Path(proj.name)), \
                m.patch("time.time", return_value=10 ** 10 + 60):
            row = airuleset.goal_status_probe(CWD, run=tmux, pane_env="%9")
        self.assertTrue(row.startswith("goal: armed"), row)


if __name__ == "__main__":
    unittest.main()
