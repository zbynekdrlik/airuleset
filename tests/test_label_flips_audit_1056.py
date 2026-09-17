"""#1056 L2 (i) — the `--label-flips` metric: blind label flips per stream per
day, from the L1 blind-label-flip gate log (~/.claude/labeledit-gate.log, a
BLOCK = a flip attempt blocked at the stream box) UNION the (h) gk-side revert
automat's revert notes (audits/labeledit-reverts.log, a flip that got through
and was reverted). Target 0. RED-first: gates.audit.count_label_flips and the
audit script's --label-flips view do not exist yet.
"""
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gates import audit  # noqa: E402


def _today():
    return datetime.now().astimezone().strftime("%Y-%m-%d")


class CountLabelFlips(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="airuleset-labelflips-")
        self.day = _today()
        self.gate = Path(self.dir, "labeledit-gate.log")
        self.revert = Path(self.dir, "labeledit-reverts.log")

    def _gate(self, lines):
        self.gate.write_text("\n".join(lines) + "\n")

    def _revert(self, lines):
        self.revert.write_text("\n".join(lines) + "\n")

    def test_gate_block_lines_count_pass_lines_do_not(self):
        # gates.labeledit._write_log format: "<ISO> <stream> <verdict> <reason> <ticket>"
        self._gate([
            "%sT10:00:00+02:00 montalu1 BLOCK bounce-unanswered 5613" % self.day,
            "%sT10:05:00+02:00 montalu1 PASS commit-since-verdict 5614" % self.day,
            "%sT11:00:00+02:00 david1 BLOCK bounce-unanswered 42" % self.day,
        ])
        res = audit.count_label_flips(gate_log_path=str(self.gate),
                                      revert_log_path="/nonexistent")
        self.assertEqual(res["total"], 2)          # PASS excluded
        self.assertEqual(res["per_stream"]["montalu1"], 1)
        self.assertEqual(res["per_stream"]["david1"], 1)
        self.assertEqual(res["per_source"]["gate-block"], 2)

    def test_revert_lines_count(self):
        # _apply_bounce_flip_revert format: "<ISO> stream=<s> repo=<r> ticket=<N> verdict=<id> ids=<...>"
        self._revert([
            "%sT12:00:00+02:00 stream=montalu1 repo=odoo-erp ticket=5613 verdict=C99 ids=1,2"
            % self.day,
        ])
        res = audit.count_label_flips(gate_log_path="/nonexistent",
                                      revert_log_path=str(self.revert))
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["per_stream"]["montalu1"], 1)
        self.assertEqual(res["per_source"]["revert"], 1)

    def test_both_sources_sum_per_stream_and_day(self):
        self._gate(["%sT10:00:00+02:00 montalu1 BLOCK bounce-unanswered 5613"
                    % self.day])
        self._revert(["%sT12:00:00+02:00 stream=montalu1 repo=odoo-erp "
                      "ticket=6413 verdict=C50 ids=-" % self.day])
        res = audit.count_label_flips(gate_log_path=str(self.gate),
                                      revert_log_path=str(self.revert))
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["per_stream"]["montalu1"], 2)
        self.assertEqual(res["per_day"][self.day], 2)

    def test_missing_logs_are_zero(self):
        res = audit.count_label_flips(gate_log_path="/nonexistent",
                                      revert_log_path="/also/nonexistent")
        self.assertEqual(res["total"], 0)

    def test_window_excludes_old_lines(self):
        old = (datetime.now().astimezone() - timedelta(days=30)).strftime(
            "%Y-%m-%d")
        self._gate(["%sT10:00:00+02:00 montalu1 BLOCK bounce-unanswered 1"
                    % old])
        res = audit.count_label_flips(gate_log_path=str(self.gate),
                                      revert_log_path="/nonexistent",
                                      window_days=7)
        self.assertEqual(res["total"], 0)


class AuditScriptLabelFlipsView(unittest.TestCase):
    def test_label_flips_flag_runs(self):
        # The CLI view is JSON-serialisable and needs no --repo (box-local logs).
        r = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "audit_bounce_rule_updates.py"),
             "--label-flips", "--json"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        obj = json.loads(r.stdout)
        self.assertIn("total", obj)
        self.assertIn("per_stream", obj)


if __name__ == "__main__":
    unittest.main()
