"""#858 — tests for cli_mdreview_audit + watchdog/mdreview_cadence.

RED→GREEN: committed BEFORE the implementation so the test fails on import.
"""

import hashlib
import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# dedup_candidates
# ---------------------------------------------------------------------------

class TestDedupCrossSurface(unittest.TestCase):
    """Cross-surface verbatim paragraph → flagged."""

    def test_cross_surface_verbatim_flagged(self):
        from cli_mdreview_audit import dedup_candidates
        shared = "Never commit credentials or API keys to version control or public repositories under any circumstances"
        files = {
            "module": {"modules/core/security.md": shared + "\n"},
            "skill": {"skills/sec/SKILL.md": shared + "\n"},
        }
        pairs = dedup_candidates(files)
        self.assertTrue(len(pairs) > 0, "cross-surface verbatim must produce pairs")
        found = any(
            "modules/core/security.md" in str(p) and "skills/sec/SKILL.md" in str(p)
            for p in pairs
        )
        self.assertTrue(found, f"expected cross-surface pair, got {pairs}")

    def test_same_surface_not_flagged(self):
        from cli_mdreview_audit import dedup_candidates
        shared = "Never commit credentials or API keys to version control or public repositories under any circumstances"
        files = {
            "module": {
                "modules/core/a.md": shared + "\n",
                "modules/core/b.md": shared + "\n",
            },
        }
        pairs = dedup_candidates(files)
        self.assertEqual(pairs, [], "same-surface dupes must NOT be flagged")

    def test_code_fence_skipped(self):
        from cli_mdreview_audit import dedup_candidates
        shared = "Never commit credentials or API keys to version control or public repositories under any circumstances"
        files = {
            "module": {"m.md": "```\n" + shared + "\n```\n"},
            "skill": {"s.md": shared + "\n"},
        }
        pairs = dedup_candidates(files)
        self.assertEqual(pairs, [], "code-fenced content must be skipped")

    def test_short_boilerplate_not_flagged(self):
        from cli_mdreview_audit import dedup_candidates
        files = {
            "module": {"m.md": "Short line.\n"},
            "skill": {"s.md": "Short line.\n"},
        }
        pairs = dedup_candidates(files)
        self.assertEqual(pairs, [], "short sentences (<40 chars) must not match")


# ---------------------------------------------------------------------------
# memory_candidates R/P/S
# ---------------------------------------------------------------------------

class TestMemoryRPS(unittest.TestCase):

    def _make_memory(self, tmp, index_lines, topic_files=None):
        mem = Path(tmp) / "memory"
        mem.mkdir(parents=True)
        idx = mem / "MEMORY.md"
        idx.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        for name, content in (topic_files or {}).items():
            (mem / name).write_text(content, encoding="utf-8")
        return mem

    def test_rule_classified_as_r(self):
        from cli_mdreview_audit import memory_candidates
        with tempfile.TemporaryDirectory() as tmp:
            mem = self._make_memory(tmp, [
                "- [Rule](rule.md) — always use set -euo pipefail",
            ], {"rule.md": "---\nname: r\ndescription: test\nmetadata:\n  type: feedback\n---\nAlways use set -euo pipefail in every script.\n"})
            result = memory_candidates(mem)
            self.assertTrue(any(c["classification"] == "R" for c in result["candidates"]),
                            f"expected R classification, got {result}")

    def test_fact_classified_as_p(self):
        from cli_mdreview_audit import memory_candidates
        with tempfile.TemporaryDirectory() as tmp:
            mem = self._make_memory(tmp, [
                "- [Fact](fact.md) — user prefers dark theme",
            ], {"fact.md": "---\nname: f\ndescription: test\nmetadata:\n  type: user\n---\nUser prefers dark theme in all editors.\n"})
            result = memory_candidates(mem)
            self.assertTrue(any(c["classification"] == "P" for c in result["candidates"]),
                            f"expected P classification, got {result}")

    def test_credential_flagged_s_value_absent(self):
        from cli_mdreview_audit import memory_candidates
        fake_token = "ghp_" + "a1b2c3d4e5f6g7h8i9j0"  # airuleset:secret-ok test fixture
        with tempfile.TemporaryDirectory() as tmp:
            mem = self._make_memory(tmp, [
                "- [Cred](cred.md) — some credential",
            ], {"cred.md": "---\nname: c\ndescription: test\nmetadata:\n  type: reference\n---\nThe API key is " + fake_token + ".\n"})
            result = memory_candidates(mem)
            self.assertGreater(result["S_flag_count"], 0,
                               "credential must be flagged as S")
            full_json = json.dumps(result)
            self.assertNotIn(fake_token, full_json,
                             "credential VALUE must NEVER appear in output")


# ---------------------------------------------------------------------------
# cadence — due / not-due / model-generation / reopen / create / runner-failure
# ---------------------------------------------------------------------------

class TestCadenceDue(unittest.TestCase):

    def _make_state(self, tmp, data):
        p = Path(tmp) / "mdreview-cadence.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def _tiers_hash(self):
        import airuleset
        return hashlib.sha1(
            str(sorted(airuleset.MODEL_TIERS.items())).encode()
        ).hexdigest()

    def test_closed_31d_is_due(self):
        from watchdog.mdreview_cadence import evaluate_cadence
        now = time.time()
        closed_at = "2026-07-01T00:00:00Z"
        th = self._tiers_hash()
        state = {"schema": 1, "ticket": 999, "model_tiers_hash": th,
                 "last_eval_ts": now - 86400}
        gh_calls = []
        def fake_gh(host_entry):
            gh_calls.append(host_entry)
            return json.dumps({"state": "closed", "closedAt": closed_at}), 0
        result = evaluate_cadence(state, now, gh_runner=fake_gh)
        self.assertEqual(result["due"], True)
        self.assertEqual(result["reason"], "30d")

    def test_closed_5d_not_due(self):
        from watchdog.mdreview_cadence import evaluate_cadence
        now = time.time()
        closed_5d = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5 * 86400))
        th = self._tiers_hash()
        state = {"schema": 1, "ticket": 999, "model_tiers_hash": th,
                 "last_eval_ts": now - 86400}
        def fake_gh(_):
            return json.dumps({"state": "closed", "closedAt": closed_5d}), 0
        result = evaluate_cadence(state, now, gh_runner=fake_gh)
        self.assertFalse(result["due"])

    def test_open_not_due(self):
        from watchdog.mdreview_cadence import evaluate_cadence
        now = time.time()
        th = self._tiers_hash()
        state = {"schema": 1, "ticket": 999, "model_tiers_hash": th,
                 "last_eval_ts": now - 86400}
        def fake_gh(_):
            return json.dumps({"state": "OPEN"}), 0
        result = evaluate_cadence(state, now, gh_runner=fake_gh)
        self.assertFalse(result["due"])

    def test_tiers_hash_mismatch_triggers_model_generation(self):
        from watchdog.mdreview_cadence import evaluate_cadence
        now = time.time()
        closed_5d = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5 * 86400))
        state = {"schema": 1, "ticket": 999, "model_tiers_hash": "stale-hash",
                 "last_eval_ts": now - 86400}
        def fake_gh(_):
            return json.dumps({"state": "closed", "closedAt": closed_5d}), 0
        result = evaluate_cadence(state, now, gh_runner=fake_gh)
        self.assertTrue(result["due"])
        self.assertEqual(result["reason"], "model-generation")


class TestCadenceReopen(unittest.TestCase):

    def test_reopen_argv_exactly_once(self):
        from watchdog.mdreview_cadence import act_on_due
        calls = []
        def fake_runner(argv):
            calls.append(argv)
            return "", 0
        act_on_due(
            ticket=999,
            reason="30d",
            audit_data={"schema": 1, "date": "2026-09-05", "boxes": [], "failed": []},
            gh_runner=fake_runner,
        )
        reopen_calls = [c for c in calls if "reopen" in str(c).lower()]
        self.assertEqual(len(reopen_calls), 1,
                         f"expected exactly 1 reopen, got {len(reopen_calls)}: {calls}")


class TestCadenceCreate(unittest.TestCase):

    def test_create_carries_both_gate_lines(self):
        from watchdog.mdreview_cadence import bootstrap_ticket
        calls = []
        def fake_runner(argv):
            calls.append(argv)
            return "https://github.com/zbynekdrlik/airuleset/issues/900\n", 0
        num = bootstrap_ticket(gh_runner=fake_runner)
        self.assertEqual(num, 900)
        create_calls = [c for c in calls if "create" in str(c).lower()]
        self.assertEqual(len(create_calls), 1)
        body = " ".join(str(x) for x in create_calls[0])
        self.assertIn("Scope-gate:", body)
        self.assertIn("Dedup-checked:", body)


class TestCadenceRunnerFailure(unittest.TestCase):

    def test_runner_failure_state_unadvanced(self):
        from watchdog.mdreview_cadence import evaluate_cadence
        now = time.time()
        th = "stale-hash"
        state = {"schema": 1, "ticket": 999, "model_tiers_hash": th,
                 "last_eval_ts": now - 86400}
        def failing_gh(_):
            return "", 1
        result = evaluate_cadence(state, now, gh_runner=failing_gh)
        self.assertFalse(result["due"])
        self.assertEqual(state["model_tiers_hash"], th,
                         "state must NOT be advanced on runner failure")


# ---------------------------------------------------------------------------
# fleet — one attempt, failed host, paused absent
# ---------------------------------------------------------------------------

class TestFleet(unittest.TestCase):

    def test_one_attempt_per_host(self):
        from cli_mdreview_audit import run_fleet
        attempts = []
        def fake_runner(host):
            attempts.append(host["name"])
            return json.dumps({
                "schema": 1, "host": host["name"], "date": "2026-09-05",
                "inventory": {}, "dedup_pairs": [], "memory": {},
            }), 0
        with mock.patch("cli_remote._deployable_hosts", return_value=[
            {"name": "box1", "host": "1.2.3.4", "user": "u", "repo_path": "~/a"},
        ]):
            result = run_fleet(runner=fake_runner)
        self.assertEqual(attempts, ["box1"])

    def test_failed_host_recorded(self):
        from cli_mdreview_audit import run_fleet
        def failing_runner(host):
            return "", 1
        with mock.patch("cli_remote._deployable_hosts", return_value=[
            {"name": "dead", "host": "1.2.3.4", "user": "u", "repo_path": "~/a"},
        ]):
            result = run_fleet(runner=failing_runner)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["host"], "dead")

    def test_paused_absent(self):
        from cli_mdreview_audit import run_fleet
        attempts = []
        def fake_runner(host):
            attempts.append(host["name"])
            return json.dumps({"schema": 1}), 0
        with mock.patch("cli_remote._deployable_hosts", return_value=[]):
            result = run_fleet(runner=fake_runner)
        self.assertEqual(attempts, [])


# ---------------------------------------------------------------------------
# run_once docstring + registry lock
# ---------------------------------------------------------------------------

class TestRunOnceJob43(unittest.TestCase):

    def test_docstring_counts_43_jobs(self):
        from watchdog import run_once
        doc = run_once.__doc__
        self.assertIn("43 numbered", doc)
        self.assertIn("37 LIVE", doc)

    def test_docstring_mentions_job_43(self):
        from watchdog import run_once
        doc = run_once.__doc__
        self.assertIn("(43)", doc)
        self.assertIn("MDREVIEW", doc.upper())

    def test_registry_includes_mdreview_cadence(self):
        import tests.test_run_once_registry_labels as reg
        self.assertIn("mdreview_cadence", reg.EXPECTED_STANDALONE)


# ---------------------------------------------------------------------------
# no-notify lock for mdreview_cadence
# ---------------------------------------------------------------------------

class TestNoNotifyImport(unittest.TestCase):

    def test_mdreview_cadence_does_not_import_notify(self):
        src_path = REPO / "watchdog" / "mdreview_cadence.py"
        src = src_path.read_text(encoding="utf-8")
        self.assertNotIn("import notify", src,
                         "mdreview_cadence must NOT import notify (BY CONSTRUCTION)")
        self.assertNotIn("from notify", src,
                         "mdreview_cadence must NOT import from notify")


if __name__ == "__main__":
    unittest.main()
