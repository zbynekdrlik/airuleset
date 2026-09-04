"""tests/test_handoff_gate.py -- #843 hand-off composer + bounce round.

Tests for _bounce_round, _validate_self_review_table, _load_lens_list,
_parse_gk_findings, and the hook receipt matching logic.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airuleset
import cli_quals


class TestBounceRound(unittest.TestCase):
    """_bounce_round: count own prior READY-FOR-REVIEW comments + 1."""

    def _fake_runner(self, comments, labels=None):
        obj = {"comments": comments,
               "labels": [{"name": lb} for lb in (labels or [])]}
        def runner(*args, **kwargs):
            return json.dumps(obj)
        return runner

    def test_zero_prior_is_round_1(self):
        r = self._fake_runner([{"author": {"login": "b"}, "body": "hi"}])
        self.assertEqual(1, cli_quals._bounce_round(1, "b", runner=r))

    def test_two_prior_is_round_3(self):
        r = self._fake_runner([
            {"author": {"login": "b"}, "body": "READY-FOR-REVIEW: x"},
            {"author": {"login": "o"}, "body": "gk review"},
            {"author": {"login": "b"}, "body": "READY-FOR-REVIEW: y"},
        ])
        self.assertEqual(3, cli_quals._bounce_round(1, "b", runner=r))

    def test_foreign_not_counted(self):
        r = self._fake_runner([
            {"author": {"login": "o"}, "body": "READY-FOR-REVIEW: z"},
        ])
        self.assertEqual(1, cli_quals._bounce_round(1, "b", runner=r))

    def test_bounce_floors_to_2(self):
        r = self._fake_runner(
            [{"author": {"login": "b"}, "body": "nothing"}],
            labels=["prio:bounce"])
        self.assertEqual(2, cli_quals._bounce_round(1, "b", runner=r))

    def test_gh_error_returns_1(self):
        self.assertEqual(1, cli_quals._bounce_round(
            1, "b", runner=lambda *a, **k: ""))

    def test_header_rfr_counted(self):
        r = self._fake_runner([
            {"author": {"login": "b"}, "body": "## READY-FOR-REVIEW: a"},
        ])
        self.assertEqual(2, cli_quals._bounce_round(1, "b", runner=r))


class TestValidateTable(unittest.TestCase):
    """_validate_self_review_table: lens coverage + n/a reason."""

    FULL = (
        "| Lens | Verdict | Evidence |\n"
        "|---|---|---|\n"
        "| security | pass | f:1 |\n"
        "| correctness | pass | f:2 |\n"
        "| test-integrity | pass | f:3 |\n"
        "| evidence-integrity | pass | f:4 |\n"
        "| design-doctrine | pass | f:5 |\n"
        "| process | pass | f:6 |\n"
    )

    def test_all_covered(self):
        ok, r = airuleset._validate_self_review_table(
            self.FULL, airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertTrue(ok, r)

    def test_missing_blocked(self):
        ok, r = airuleset._validate_self_review_table(
            "| security | pass | f:1 |\n",
            airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertFalse(ok)
        self.assertIn("missing", r)

    def test_na_no_reason_blocked(self):
        table = self.FULL.replace("| security | pass | f:1 |",
                                   "| security | n/a |  |")
        ok, r = airuleset._validate_self_review_table(
            table, airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertFalse(ok)
        self.assertIn("n/a", r)

    def test_empty_blocked(self):
        ok, _ = airuleset._validate_self_review_table(
            "", airuleset.HANDOFF_DEFAULT_LENSES)
        self.assertFalse(ok)


class TestLoadLens(unittest.TestCase):
    """_load_lens_list: file present -> custom; absent -> default."""

    def test_default(self):
        self.assertEqual(
            airuleset._load_lens_list("/no"),
            airuleset.HANDOFF_DEFAULT_LENSES)

    def test_custom(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, ".claude", "rules")
            os.makedirs(d)
            with open(os.path.join(d, "gk-review-lenses.md"), "w") as f:
                f.write("# hdr\nalpha\nbeta\n")
            self.assertEqual(airuleset._load_lens_list(td), ["alpha", "beta"])


class TestParseFindings(unittest.TestCase):
    """_parse_gk_findings: extract ids from gk bounce comments."""

    def test_emoji(self):
        ids = airuleset._parse_gk_findings("\U0001f534 1 x\n\U0001f7e1 2 y")
        self.assertIn("1", ids)
        self.assertIn("2", ids)

    def test_f_id(self):
        self.assertIn("F3", airuleset._parse_gk_findings("F3 finding"))

    def test_empty(self):
        self.assertEqual([], airuleset._parse_gk_findings(""))
        self.assertEqual([], airuleset._parse_gk_findings(None))


class TestReceiptLogic(unittest.TestCase):
    """Receipt matching (the hook's core logic)."""

    def test_fresh_matches(self):
        body = "READY-FOR-REVIEW: test\n"
        h = hashlib.sha256(body.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            gd = os.path.join(td, "gate")
            os.makedirs(gd)
            with open(os.path.join(gd, "r.json"), "w") as f:
                json.dump({"sha256": h, "ts": time.time()}, f)
            self.assertTrue(self._scan(gd, h))

    def test_stale_no_match(self):
        body = "READY-FOR-REVIEW: test\n"
        h = hashlib.sha256(body.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            gd = os.path.join(td, "gate")
            os.makedirs(gd)
            with open(os.path.join(gd, "r.json"), "w") as f:
                json.dump({"sha256": h, "ts": time.time() - 700}, f)
            self.assertFalse(self._scan(gd, h))

    def test_wrong_hash(self):
        with tempfile.TemporaryDirectory() as td:
            gd = os.path.join(td, "gate")
            os.makedirs(gd)
            with open(os.path.join(gd, "r.json"), "w") as f:
                json.dump({"sha256": "bad", "ts": time.time()}, f)
            self.assertFalse(self._scan(gd, "good"))

    @staticmethod
    def _scan(gate_dir, want_hash):
        now = time.time()
        for fn in os.listdir(gate_dir):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(gate_dir, fn)) as f:
                r = json.loads(f.read())
            if r.get("sha256") == want_hash and now - r.get("ts", 0) <= 600:
                return True
        return False


class TestHookRoute(unittest.TestCase):
    """Hook pre-filter: non-RFR commands pass through."""

    def _run(self, cmd):
        import subprocess
        hp = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "hooks",
            "block-handoff-without-composer.sh")
        return subprocess.run(
            ["bash", hp],
            input=json.dumps({"tool_input": {"command": cmd}}),
            capture_output=True, text=True, timeout=10,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def test_plain_ok(self):
        self.assertEqual(0, self._run(
            "gh issue comment 1 --body hi").returncode)

    def test_bypass_ok(self):
        r = self._run(
            'gh issue comment 1 --body "READY-FOR-REVIEW" '
            "# airuleset:handoff-ok test")
        self.assertEqual(0, r.returncode)

    def test_cli_ok(self):
        self.assertEqual(0, self._run(
            "python3 airuleset.py handoff --repo x --issue 1").returncode)

    def test_non_comment_ok(self):
        self.assertEqual(0, self._run("echo READY-FOR-REVIEW").returncode)


class TestDoctrineContentLock843(unittest.TestCase):
    """#843 content lock: autopilot-worker.md must name `airuleset.py handoff`
    + the `Self-review:` table, and SKILL.md must name `round3!`. Locks the
    doctrine the same way test_batch_orchestration.py locks issue 848."""

    _WORKER = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "agents", "autopilot-worker.md")
    _SKILL = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "skills", "autopilot", "SKILL.md")

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_worker_names_handoff_cli(self):
        t = self._read(self._WORKER)
        self.assertIn("airuleset.py handoff", t)

    def test_worker_names_self_review_table(self):
        t = self._read(self._WORKER)
        self.assertIn("Self-review:", t)

    def test_worker_names_handoff_flags(self):
        t = self._read(self._WORKER)
        self.assertIn("--self-review-file", t)
        self.assertIn("--reviewed-by-tier", t)
        self.assertIn("--root-cause", t)
        self.assertIn("--prevencia-read", t)

    def test_worker_names_lens_list(self):
        t = self._read(self._WORKER)
        self.assertIn("gk-review-lenses.md", t)

    def test_worker_names_bounce_round_escalation(self):
        t = self._read(self._WORKER)
        self.assertIn("slice-quals --bounces", t)

    def test_skill_names_round3(self):
        t = self._read(self._SKILL)
        self.assertIn("round3!", t)

    def test_skill_names_fable_advisor_design_consult(self):
        t = self._read(self._SKILL)
        self.assertIn("fable-advisor", t)
        # The round3! clause must mention the design consult
        idx = t.find("round3!")
        self.assertGreater(idx, -1)
        window = t[idx:idx + 500]
        self.assertIn("DESIGN", window)


class TestNudgeRound3Clause(unittest.TestCase):
    """#843: _nudge_text carries a ROUND3 clause when round3_n > 0."""

    def test_no_clause_at_zero(self):
        from watchdog.ops_wait_recheck import _nudge_text
        text = _nudge_text(5, [], round3_n=0)
        self.assertNotIn("ROUND3", text)

    def test_clause_at_positive(self):
        from watchdog.ops_wait_recheck import _nudge_text
        text = _nudge_text(5, [], round3_n=2)
        self.assertIn("ROUND3 2", text)
        self.assertIn("#843", text)

    def test_clause_not_on_bool(self):
        from watchdog.ops_wait_recheck import _nudge_text
        text = _nudge_text(5, [], round3_n=True)
        self.assertNotIn("ROUND3", text)


if __name__ == "__main__":
    unittest.main()
