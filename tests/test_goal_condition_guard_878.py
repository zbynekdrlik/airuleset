"""#878 — goal-condition dark-watch guard: an ARMED loop with a FOREIGN
/goal condition that lacks the `❓ NEEDS YOU` detection token, while a LASTQF
exists for that session, gets a ONE-TIME `goal-guard:` nudge (at most 1/24h
per sid via nudge_gate). Never auto-types /goal. Never touches a canonical
or `cleared` condition.

Fixtures — decision helper:
  (1) foreign payload "all issues" + LASTQF present → nudge logged
  (2) canonical rendered condition → no nudge
  (3) state=="cleared" → no nudge
  (4) foreign payload containing `❓ NEEDS YOU` → no nudge
  (5) no LASTQF → no nudge

Fixtures — delivery helper:
  (6) delivery through a fake send_fn → sent once
  (7) second sweep within 24h → gated by nudge_gate
  (8) recent-human → skip
  (9) stopped pane (no-input-line) → skip
  (10) cleared state → _goal_guard_decide returns None (no delivery attempted)
  (11) dry-run → would-send (no keystroke)
"""

import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import goal_registry as gr  # noqa: E402
from watchdog import goal  # noqa: E402
import watchdog  # noqa: E402


class TestGoalGuardDecide(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-878gg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sid = "test-878-guard-%s" % os.getpid()
        self.lastqf = Path("/tmp/claude-discord-lastq-%s" % self.sid)
        self.addCleanup(lambda: self.lastqf.unlink(missing_ok=True))
        self.template = gr.render("full")

    def test_1_foreign_payload_with_lastqf_produces_nudge(self):
        self.lastqf.write_text("some question text")
        result = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state={}, now=1000, loc="test-loc")
        self.assertIsNotNone(result)
        self.assertIn("goal-guard", result)

    def test_2_canonical_condition_no_nudge(self):
        self.lastqf.write_text("some question text")
        payload = self.template.replace("/goal ", "", 1)
        result = goal._goal_guard_decide(
            sid=self.sid, payload=payload,
            template_line=self.template,
            state={}, now=1000, loc="test-loc")
        self.assertIsNone(result)

    def test_3_cleared_state_no_nudge(self):
        self.lastqf.write_text("some question text")
        result = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state={}, now=1000, loc="test-loc",
            mark_state="cleared")
        self.assertIsNone(result)

    def test_4_foreign_with_needs_you_token_no_nudge(self):
        self.lastqf.write_text("some question text")
        payload = "all issues done OR ❓ NEEDS YOU marker present"
        result = goal._goal_guard_decide(
            sid=self.sid, payload=payload,
            template_line=self.template,
            state={}, now=1000, loc="test-loc")
        self.assertIsNone(result)

    def test_5_no_lastqf_no_nudge(self):
        result = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state={}, now=1000, loc="test-loc")
        self.assertIsNone(result)


class TestGoalGuardDeliver(unittest.TestCase):
    """Delivery tests using a fake _send_goal_verified."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-878ggd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sid = "test-878-deliver-%s" % os.getpid()
        self.lastqf = Path("/tmp/claude-discord-lastq-%s" % self.sid)
        self.addCleanup(lambda: self.lastqf.unlink(missing_ok=True))
        self.sent = []

    def _fake_send(self, pid, text, run, captured=None, sleep_fn=None,
                   logs=None, verify_armed=True):
        self.sent.append(text)
        return True

    def _fake_send_fail(self, pid, text, run, captured=None, sleep_fn=None,
                        logs=None, verify_armed=True):
        self.sent.append(text)
        return False

    def _bare_pane(self):
        return "❯ "

    def _no_input_pane(self):
        return "some output\nno input box here"

    @mock.patch.object(watchdog, "find_active_transcript", return_value=None)
    @mock.patch.object(watchdog, "_classify_boundary",
                       return_value=("input", ""))
    def test_6_delivery_sends_once(self, _cb, _fat):
        self.lastqf.write_text("q")
        state = {}
        with mock.patch.object(goal, "_send_goal_verified", self._fake_send):
            logs = goal._goal_guard_deliver(
                self.sid, "pid1", self._bare_pane(), "/tmp/cwd",
                state, 1000, "loc", lambda *a: "", None, False, "/tmp")
        self.assertTrue(any("sent" in ln for ln in logs), logs)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("goal-guard:", self.sent[0])

    @mock.patch.object(watchdog, "find_active_transcript", return_value=None)
    @mock.patch.object(watchdog, "_classify_boundary",
                       return_value=("input", ""))
    def test_7_second_sweep_gated_by_nudge_gate(self, _cb, _fat):
        self.lastqf.write_text("q")
        state = {}
        with mock.patch.object(goal, "_send_goal_verified", self._fake_send):
            logs1 = goal._goal_guard_deliver(
                self.sid, "pid1", self._bare_pane(), "/tmp/cwd",
                state, 1000, "loc", lambda *a: "", None, False, "/tmp")
            self.assertTrue(any("sent" in ln for ln in logs1), logs1)
            logs2 = goal._goal_guard_deliver(
                self.sid, "pid1", self._bare_pane(), "/tmp/cwd",
                state, 2000, "loc", lambda *a: "", None, False, "/tmp")
        self.assertTrue(any("cadence-gate" in ln for ln in logs2), logs2)
        self.assertEqual(len(self.sent), 1)

    def test_8_recent_human_skips(self):
        self.lastqf.write_text("q")
        state = {}
        with mock.patch.object(watchdog, "find_active_transcript",
                               return_value=(Path("/tmp/fake.jsonl"), 1000)):
            with mock.patch.object(
                    watchdog, "_goal_autoarm_recent_human_activity",
                    return_value=(True, "human")):
                with mock.patch.object(goal, "_send_goal_verified",
                                       self._fake_send):
                    logs = goal._goal_guard_deliver(
                        self.sid, "pid1", self._bare_pane(), "/tmp/cwd",
                        state, 1000, "loc", lambda *a: "", None,
                        False, "/tmp")
        self.assertTrue(any("recent-human" in ln for ln in logs), logs)
        self.assertEqual(len(self.sent), 0)

    @mock.patch.object(watchdog, "find_active_transcript", return_value=None)
    def test_9_stopped_pane_skips(self, _fat):
        self.lastqf.write_text("q")
        state = {}
        with mock.patch.object(watchdog, "_classify_boundary",
                               return_value=("no-input-line", "")):
            with mock.patch.object(goal, "_send_goal_verified",
                                   self._fake_send):
                logs = goal._goal_guard_deliver(
                    self.sid, "pid1", self._no_input_pane(), "/tmp/cwd",
                    state, 1000, "loc", lambda *a: "", None, False, "/tmp")
        self.assertTrue(any("stopped-pane" in ln for ln in logs), logs)
        self.assertEqual(len(self.sent), 0)

    @mock.patch.object(watchdog, "find_active_transcript", return_value=None)
    @mock.patch.object(watchdog, "_classify_boundary",
                       return_value=("input", ""))
    def test_11_dry_run_does_not_send(self, _cb, _fat):
        self.lastqf.write_text("q")
        state = {}
        with mock.patch.object(goal, "_send_goal_verified", self._fake_send):
            logs = goal._goal_guard_deliver(
                self.sid, "pid1", self._bare_pane(), "/tmp/cwd",
                state, 1000, "loc", lambda *a: "", None, True, "/tmp")
        self.assertTrue(any("would-send" in ln for ln in logs), logs)
        self.assertEqual(len(self.sent), 0)

    @mock.patch.object(watchdog, "find_active_transcript", return_value=None)
    @mock.patch.object(watchdog, "_classify_boundary",
                       return_value=("input", ""))
    def test_send_failure_logged(self, _cb, _fat):
        self.lastqf.write_text("q")
        state = {}
        with mock.patch.object(goal, "_send_goal_verified",
                               self._fake_send_fail):
            logs = goal._goal_guard_deliver(
                self.sid, "pid1", self._bare_pane(), "/tmp/cwd",
                state, 1000, "loc", lambda *a: "", None, False, "/tmp")
        self.assertTrue(any("send-failed" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
