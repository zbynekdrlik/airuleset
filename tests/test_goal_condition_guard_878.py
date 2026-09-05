"""#878 — goal-condition dark-watch guard: an ARMED loop with a FOREIGN
/goal condition that lacks the `❓ NEEDS YOU` detection token, while a LASTQF
exists for that session, gets a ONE-TIME `goal-guard:` nudge (at most 1/24h
per sid via nudge_gate). Never auto-types /goal. Never touches a canonical
or `cleared` condition.

Fixtures:
  (1) foreign payload "all issues" + LASTQF present → nudge logged
  (2) canonical rendered condition → no nudge
  (3) state=="cleared" → no nudge
  (4) foreign payload containing `❓ NEEDS YOU` → no nudge
  (5) no LASTQF → no nudge
  (6) second call within 24h → gated by nudge_gate
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
            state={}, now=1000, loc="test-loc", dry_run=True)
        self.assertIsNotNone(result)
        self.assertIn("goal-guard", result)

    def test_2_canonical_condition_no_nudge(self):
        self.lastqf.write_text("some question text")
        payload = self.template.replace("/goal ", "", 1)
        result = goal._goal_guard_decide(
            sid=self.sid, payload=payload,
            template_line=self.template,
            state={}, now=1000, loc="test-loc", dry_run=True)
        self.assertIsNone(result)

    def test_3_cleared_state_no_nudge(self):
        self.lastqf.write_text("some question text")
        result = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state={}, now=1000, loc="test-loc", dry_run=True,
            mark_state="cleared")
        self.assertIsNone(result)

    def test_4_foreign_with_needs_you_token_no_nudge(self):
        self.lastqf.write_text("some question text")
        payload = "all issues done OR ❓ NEEDS YOU marker present"
        result = goal._goal_guard_decide(
            sid=self.sid, payload=payload,
            template_line=self.template,
            state={}, now=1000, loc="test-loc", dry_run=True)
        self.assertIsNone(result)

    def test_5_no_lastqf_no_nudge(self):
        result = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state={}, now=1000, loc="test-loc", dry_run=True)
        self.assertIsNone(result)

    def test_6_second_call_within_24h_gated(self):
        self.lastqf.write_text("some question text")
        state = {}
        r1 = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state=state, now=1000, loc="test-loc", dry_run=True)
        self.assertIsNotNone(r1)
        r2 = goal._goal_guard_decide(
            sid=self.sid, payload="all issues",
            template_line=self.template,
            state=state, now=2000, loc="test-loc", dry_run=True)
        self.assertIsNone(r2)


class TestGoalRegistryBlockedClauseLock(unittest.TestCase):
    """#878 — every profile's rendered condition must contain the (A)
    BLOCKED ON MY ANSWER clause and the ❓ NEEDS YOU: detection token."""

    def test_every_profile_contains_blocked_question_stop_clause(self):
        for p in gr.PROFILES:
            line = gr.render(p)
            self.assertIn("(A) BLOCKED ON MY ANSWER", line, p)
            self.assertIn("`❓ NEEDS YOU:`", line, p)


if __name__ == "__main__":
    unittest.main()
