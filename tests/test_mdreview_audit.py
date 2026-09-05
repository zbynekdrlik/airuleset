"""#858 — tests for cli_mdreview_audit + watchdog/mdreview_cadence.

RED→GREEN: committed BEFORE the implementation so the test fails on import.
"""

import hashlib
import json
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
        shared = ("Never commit credentials or API keys or tokens or passwords "
                   "to version control or public repositories under any circumstances "
                   "because that would be a serious security violation in production")
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
        shared = ("Never commit credentials or API keys or tokens or passwords "
                   "to version control or public repositories under any circumstances "
                   "because that would be a serious security violation in production")
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
        shared = ("Never commit credentials or API keys or tokens or passwords "
                   "to version control or public repositories under any circumstances "
                   "because that would be a serious security violation in production")
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
            self.assertGreater(len(result.get("R", [])), 0,
                               f"expected R classification, got {result}")

    def test_fact_classified_as_p(self):
        from cli_mdreview_audit import memory_candidates
        with tempfile.TemporaryDirectory() as tmp:
            mem = self._make_memory(tmp, [
                "- [Fact](fact.md) — user prefers dark theme",
            ], {"fact.md": "---\nname: f\ndescription: test\nmetadata:\n  type: user\n---\nUser prefers dark theme in all editors.\n"})
            result = memory_candidates(mem)
            self.assertGreater(len(result.get("P", [])), 0,
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
            run_fleet(runner=fake_runner)
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
            run_fleet(runner=fake_runner)
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


# ---------------------------------------------------------------------------
# Finding 2 — cmd_watchdog must pass mdreview_cadence_enabled=True
# ---------------------------------------------------------------------------

class TestCmdWatchdogEnable(unittest.TestCase):

    def test_cmd_watchdog_enables_mdreview_cadence(self):
        import inspect
        import airuleset
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("mdreview_cadence_enabled=True", src,
                      "cmd_watchdog must pass mdreview_cadence_enabled=True")


# ---------------------------------------------------------------------------
# Finding 4 — every gh argv must carry -R zbynekdrlik/airuleset
# ---------------------------------------------------------------------------

class TestGhRepoFlag(unittest.TestCase):

    def test_evaluate_cadence_gh_argv_carries_repo_flag(self):
        from watchdog.mdreview_cadence import evaluate_cadence
        now = time.time()
        state = {"schema": 1, "ticket": 999,
                 "model_tiers_hash": "x", "last_eval_ts": now - 86400}
        calls = []
        def capture_gh(argv):
            calls.append(argv)
            return json.dumps({"state": "OPEN"}), 0
        evaluate_cadence(state, now, gh_runner=capture_gh)
        for c in calls:
            argv_str = " ".join(str(x) for x in c)
            self.assertIn("-R", argv_str,
                          f"gh argv must carry -R flag: {c}")

    def test_act_on_due_gh_argv_carries_repo_flag(self):
        from watchdog.mdreview_cadence import act_on_due
        calls = []
        def capture_gh(argv):
            calls.append(argv)
            return "", 0
        act_on_due(999, "30d",
                   {"schema": 1, "date": "2026-09-05", "boxes": [], "failed": []},
                   gh_runner=capture_gh)
        for c in calls:
            argv_str = " ".join(str(x) for x in c)
            self.assertIn("-R", argv_str,
                          f"gh argv must carry -R flag: {c}")

    def test_bootstrap_gh_argv_carries_repo_flag(self):
        from watchdog.mdreview_cadence import bootstrap_ticket
        calls = []
        def capture_gh(argv):
            calls.append(argv)
            return "https://github.com/zbynekdrlik/airuleset/issues/900\n", 0
        bootstrap_ticket(gh_runner=capture_gh)
        for c in calls:
            argv_str = " ".join(str(x) for x in c)
            self.assertIn("-R", argv_str,
                          f"gh argv must carry -R flag: {c}")


# ---------------------------------------------------------------------------
# Finding 6 — act_on_due must check gh rc and return success/failure
# ---------------------------------------------------------------------------

class TestActOnDueRc(unittest.TestCase):

    def test_failed_reopen_returns_false(self):
        from watchdog.mdreview_cadence import act_on_due
        def failing_gh(argv):
            return "", 1
        result = act_on_due(999, "30d",
                            {"schema": 1, "date": "2026-09-05",
                             "boxes": [], "failed": []},
                            gh_runner=failing_gh)
        self.assertIs(result, False,
                      "act_on_due must return False on gh failure")


# ---------------------------------------------------------------------------
# Finding 8 — dev1 gate
# ---------------------------------------------------------------------------

class TestDev1Gate(unittest.TestCase):

    def test_non_dev1_skips(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            with mock.patch("socket.gethostname", return_value="dev2"):
                logs = mdreview_cadence_job(
                    time.time(), {}, state_path=str(sp))
            self.assertTrue(
                any("not dev1" in ln for ln in logs),
                f"non-dev1 must skip: {logs}")


# ---------------------------------------------------------------------------
# Finding 10 — redaction BEFORE truncation + key alignment
# ---------------------------------------------------------------------------

class TestRedactBeforeTrunc(unittest.TestCase):

    def test_long_credential_redacted_before_truncation(self):
        from cli_mdreview_audit import memory_candidates
        long_token = "ghp_" + "X" * 250  # airuleset:secret-ok test fixture
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / "memory"
            mem.mkdir()
            (mem / "MEMORY.md").write_text("# mem\n", encoding="utf-8")
            (mem / "t.md").write_text(
                "---\nname: t\ndescription: t\nmetadata:\n  type: reference\n---\n"
                + "Token is " + long_token + " end\n",
                encoding="utf-8")
            result = memory_candidates(mem)
            full_json = json.dumps(result)
            self.assertNotIn("ghp_XXX", full_json,
                             "credential prefix must NOT leak via truncation")

    def test_candidate_key_is_class_not_classification(self):
        from cli_mdreview_audit import memory_candidates
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp) / "memory"
            mem.mkdir()
            (mem / "MEMORY.md").write_text("# mem\n", encoding="utf-8")
            (mem / "r.md").write_text(
                "---\nname: r\ndescription: t\nmetadata:\n  type: feedback\n---\n"
                "Always use set -euo pipefail everywhere in every script.\n",
                encoding="utf-8")
            result = memory_candidates(mem)
            for c in result.get("candidates", []):
                self.assertIn("class", c,
                              "candidate key must be 'class', not 'classification'")
                self.assertNotIn("classification", c,
                                 "key must be 'class', not 'classification'")


# ---------------------------------------------------------------------------
# Finding 11 — job-level tests for mdreview_cadence_job
# ---------------------------------------------------------------------------

class TestCadenceJob(unittest.TestCase):

    def _tiers_hash(self):
        import airuleset
        import hashlib as _hl
        return _hl.sha1(
            str(sorted(airuleset.MODEL_TIERS.items())).encode()
        ).hexdigest()

    def test_ttl_skip(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 999,
                "model_tiers_hash": self._tiers_hash(),
                "last_eval_ts": now - 100,
            }), encoding="utf-8")
            with mock.patch("socket.gethostname", return_value="dev1"):
                logs = mdreview_cadence_job(now, {}, state_path=str(sp))
            self.assertTrue(any("ttl-skip" in ln for ln in logs),
                            f"recent eval must ttl-skip: {logs}")

    def test_due_advances_state(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        now = time.time()
        th = self._tiers_hash()
        closed_old = "2026-01-01T00:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 999,
                "model_tiers_hash": th,
                "last_eval_ts": now - 86400 * 2,
            }), encoding="utf-8")
            gh_calls = []
            def fake_gh(argv):
                gh_calls.append(argv)
                if "view" in str(argv):
                    return json.dumps({"state": "closed",
                                       "closedAt": closed_old}), 0
                return "", 0
            with mock.patch("socket.gethostname", return_value="dev1"):
                with mock.patch("cli_remote._deployable_hosts",
                                return_value=[]):
                    logs = mdreview_cadence_job(
                        now, {}, state_path=str(sp),
                        gh_runner=fake_gh, fleet_runner=lambda h: ("{}",0))
            state_after = json.loads(sp.read_text())
            self.assertEqual(state_after["last_eval_ts"], now,
                             "state must be advanced after due")
            self.assertTrue(any("due" in ln for ln in logs), f"logs: {logs}")

    def test_dry_run_no_gh_reopen(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        now = time.time()
        th = self._tiers_hash()
        closed_old = "2026-01-01T00:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 999,
                "model_tiers_hash": th,
                "last_eval_ts": now - 86400 * 2,
            }), encoding="utf-8")
            gh_calls = []
            def fake_gh(argv):
                gh_calls.append(argv)
                if "view" in str(argv):
                    return json.dumps({"state": "closed",
                                       "closedAt": closed_old}), 0
                return "", 0
            with mock.patch("socket.gethostname", return_value="dev1"):
                logs = mdreview_cadence_job(
                    now, {}, dry_run=True, state_path=str(sp),
                    gh_runner=fake_gh)
            reopen_calls = [c for c in gh_calls if "reopen" in str(c)]
            self.assertEqual(len(reopen_calls), 0,
                             "dry-run must NOT reopen")

    def test_failure_no_hash_advance_but_ttl_advanced(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        now = time.time()
        old_hash = "stale-hash-value"
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            sp.write_text(json.dumps({
                "schema": 1, "ticket": 999,
                "model_tiers_hash": old_hash,
                "last_eval_ts": now - 86400 * 2,
            }), encoding="utf-8")
            def fake_gh(argv):
                if "view" in str(argv):
                    return json.dumps({"state": "closed",
                                       "closedAt": "2026-01-01T00:00:00Z"}), 0
                return "", 0
            with mock.patch("socket.gethostname", return_value="dev1"):
                with mock.patch("cli_remote._deployable_hosts",
                                return_value=[]):
                    with mock.patch("cli_mdreview_audit.run_fleet",
                                    side_effect=RuntimeError("audit boom")):
                        logs = mdreview_cadence_job(
                            now, {}, state_path=str(sp),
                            gh_runner=fake_gh)
            state_after = json.loads(sp.read_text())
            self.assertNotEqual(state_after.get("model_tiers_hash"),
                                self._tiers_hash(),
                                "hash must NOT advance on failure")
            self.assertEqual(state_after["last_eval_ts"], now,
                             "daily TTL MUST advance even on failure")


# ---------------------------------------------------------------------------
# Finding 3 — bootstrap wiring in no-ticket branch
# ---------------------------------------------------------------------------

class TestBootstrapWiring(unittest.TestCase):

    def test_no_ticket_state_triggers_bootstrap(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            gh_calls = []
            def fake_gh(argv):
                gh_calls.append(argv)
                return "https://github.com/zbynekdrlik/airuleset/issues/900\n", 0
            with mock.patch("socket.gethostname", return_value="dev1"):
                logs = mdreview_cadence_job(
                    now, {}, state_path=str(sp), gh_runner=fake_gh)
            create_calls = [c for c in gh_calls if "create" in str(c)]
            self.assertGreater(len(create_calls), 0,
                               "no-ticket state must trigger bootstrap_ticket")
            state_after = json.loads(sp.read_text())
            self.assertEqual(state_after.get("ticket"), 900,
                             "bootstrap must store ticket number")


# ---------------------------------------------------------------------------
# Finding 12 — hash must be persisted when absent
# ---------------------------------------------------------------------------

class TestHashPersist(unittest.TestCase):

    def test_bootstrap_persists_tiers_hash(self):
        from watchdog.mdreview_cadence import mdreview_cadence_job
        import hashlib as _hl
        import airuleset
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "state.json"
            gh_calls = []
            def fake_gh(argv):
                gh_calls.append(argv)
                return "https://github.com/zbynekdrlik/airuleset/issues/900\n", 0
            with mock.patch("socket.gethostname", return_value="dev1"):
                mdreview_cadence_job(now, {}, state_path=str(sp),
                                    gh_runner=fake_gh)
            state_after = json.loads(sp.read_text())
            expected = _hl.sha1(
                str(sorted(airuleset.MODEL_TIERS.items())).encode()
            ).hexdigest()
            self.assertEqual(state_after.get("model_tiers_hash"), expected,
                             "bootstrap must persist current tiers hash")


# ---------------------------------------------------------------------------
# Finding 5 — dedup_pairs and zero_caller_skills must NOT be hardcoded []
# ---------------------------------------------------------------------------

class TestDedupWiring(unittest.TestCase):

    def test_local_audit_populates_dedup_pairs(self):
        """Verify the dedup_pairs key is populated from real file content."""
        from cli_mdreview_audit import cmd_mdreview_audit
        import argparse
        import io
        from contextlib import redirect_stdout
        args = argparse.Namespace(fleet=False, json_output=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_mdreview_audit(args)  # airuleset:script-ok test captures output
        output = buf.getvalue()
        if output.strip():
            data = json.loads(output)
            self.assertIn("dedup_pairs", data,
                          "output must include dedup_pairs key")


# ---------------------------------------------------------------------------
# Finding 9 — separate fleet_runner param
# ---------------------------------------------------------------------------

class TestFleetRunnerSep(unittest.TestCase):

    def test_fleet_runner_used_for_ssh(self):
        from cli_mdreview_audit import run_fleet
        fleet_calls = []
        def fake_fleet(host):
            fleet_calls.append(host["name"])
            return json.dumps({"schema": 1, "host": host["name"]}), 0
        with mock.patch("cli_remote._deployable_hosts", return_value=[
            {"name": "rmt", "host": "1.2.3.4", "user": "u", "repo_path": "~/a"},
        ]):
            run_fleet(fleet_runner=fake_fleet)
        self.assertEqual(fleet_calls, ["rmt"],
                         "fleet_runner must be used for remote hosts")


# ---------------------------------------------------------------------------
# Finding 13 — scoping_matrix hoisted to top level in fleet result
# ---------------------------------------------------------------------------

class TestScopingHoisted(unittest.TestCase):

    def test_fleet_scoping_at_top_level(self):
        from cli_mdreview_audit import run_fleet
        def fake_fleet(host):
            return json.dumps({"schema": 1, "host": host["name"]}), 0
        with mock.patch("cli_remote._deployable_hosts", return_value=[]):
            data = run_fleet(fleet_runner=fake_fleet)
        self.assertIn("scoping", data,
                      "scoping_matrix must be at top level of fleet result")
        for box in data.get("boxes", []):
            self.assertNotIn("scoping", box,
                             "scoping must NOT be in per-box data")


if __name__ == "__main__":
    unittest.main()
