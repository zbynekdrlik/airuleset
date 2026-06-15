import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class TestConstants(unittest.TestCase):
    def test_constants_present(self):
        from board import (PORT, BOARD_HOST_IP, REPORT_TIMEOUT, CIRCUIT_BREAKER_S,
                           FLUSH_CAP, QUEUE_MAX_BYTES, QUEUE_TTL_S, BODY_MAX,
                           EVENT_CAP_PER_RUN, STALE_ACTIVE_S, STALE_WAIT_S,
                           GH_POLL_FLOOR_S, TERMINAL_PHASES)
        self.assertEqual(PORT, 8787)
        self.assertEqual(BOARD_HOST_IP, "10.77.9.21")
        self.assertEqual(REPORT_TIMEOUT, 2)
        self.assertIn("done", TERMINAL_PHASES)
        self.assertIn("obsolete-closed", TERMINAL_PHASES)


class TestGate(unittest.TestCase):
    def test_required_set_and_source(self):
        from board.gate import REQUIRED_GATES, source_of
        self.assertEqual(source_of("ci"), "verified")
        self.assertEqual(source_of("mergeable"), "verified")
        self.assertEqual(source_of("review"), "claimed")
        self.assertEqual(source_of("deploy_verified"), "claimed")
        self.assertIn("requesting_code_review", REQUIRED_GATES)

    def test_applicable_gates(self):
        from board.gate import applicable_gates
        feat = applicable_gates(is_bug_fix=False, has_deploy=False)
        self.assertNotIn("regression", feat)
        self.assertNotIn("deploy_verified", feat)
        self.assertIn("review", feat)
        bug = applicable_gates(is_bug_fix=True, has_deploy=True)
        self.assertIn("regression", bug)
        self.assertIn("deploy_verified", bug)


class TestAlarm(unittest.TestCase):
    def _run(self, **kw):
        base = dict(merged=False, merge_mode="auto", is_bug_fix=False,
                    has_deploy=False, phase="implementing",
                    last_report_age_s=10, gate={})
        base.update(kw); return base

    def test_merged_all_ok_no_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ticket_validated":"ok","ci":"ok","mergeable":"ok",
                            "plan_check":"ok","review":"ok","requesting_code_review":"ok"})
        self.assertNotIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_missing_rcr_alarms(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ci":"ok","mergeable":"ok","plan_check":"ok",
                            "review":"ok","ticket_validated":"ok"})  # rcr missing→pending
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_unstable_alarms(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ticket_validated":"ok","ci":"ok","mergeable":"fail",
                            "plan_check":"ok","review":"ok","requesting_code_review":"ok"})
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_manual_unmerged_green_no_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=False, merge_mode="manual", phase="done",
                      gate={k:"ok" for k in ("ticket_validated","ci","mergeable",
                            "plan_check","review","requesting_code_review")})
        self.assertEqual(compute_alarms(r), [])

    def test_pending_gate_recent_report_is_verifying_not_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="merge", last_report_age_s=30,
                      gate={"ci":"ok","mergeable":"ok"})  # rest pending, fresh
        a = compute_alarms(r)
        self.assertIn("VERIFYING", a)
        self.assertNotIn("MERGED_INCOMPLETE_GATE", a)

    def test_stale_abandoned_midgate(self):
        from board.gate import compute_alarms
        r = self._run(merged=False, phase="review", last_report_age_s=9999)
        self.assertIn("STALE_ABANDONED", compute_alarms(r))
