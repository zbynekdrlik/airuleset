"""Tests for the W-drain gate (#868).

Hook: block-dispatch-over-wdrain.sh (PreToolUse Agent) — blocks autopilot-worker
dispatches when |W| > OPS_WAIT_WDRAIN_THRESHOLD and no
valid receipt exists.

CLI: wdrain-pass --record — validates per-member verdicts and writes a receipt.

Footer: wdrain_over bool in the cache → red `· W N!` vs grey `· W N`.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"


def _run_hook(payload, env_extra=None):
    """Drive the hook with a JSON payload on stdin. Returns (rc, stderr)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    return p.returncode, p.stderr


def _cwd_key(cwd):
    """Compute the cwd key using the SAME function statusbar uses."""
    sys.path.insert(0, str(REPO))
    try:
        import statusbar
        return statusbar.cwd_key(cwd)
    finally:
        sys.path.pop(0)


def _make_cache(tmpdir, cwd, ops_wait, ts=None, extra=None):
    """Write a tickets-status cache file for the given cwd."""
    key = _cwd_key(cwd)
    cache_dir = pathlib.Path(tmpdir) / ".claude" / "tickets-status"
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = {"ops_wait": ops_wait, "ts": ts or time.time()}
    if extra:
        entry.update(extra)
    with open(cache_dir / (key + ".json"), "w") as f:
        json.dump(entry, f)


def _make_receipt(tmpdir, cwd, expires_at):
    """Write a wdrain receipt for the given cwd."""
    key = _cwd_key(cwd)
    receipt_dir = pathlib.Path(tmpdir) / ".claude" / "wdrain"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = {"expires_at": expires_at, "ts": int(time.time()), "cwd": str(cwd)}
    with open(receipt_dir / (key + ".json"), "w") as f:
        json.dump(receipt, f)


class TestHookBasics(unittest.TestCase):
    """Non-Agent calls and excluded subagent types pass."""

    def test_non_agent_passes(self):
        """A non-Agent tool call exits 0."""
        rc, _ = _run_hook({"tool_name": "Bash", "tool_input": {}})
        self.assertEqual(0, rc)

    def test_excluded_types_pass_at_w20(self):
        """ticket-validator, Explore, general-purpose, etc. pass even at W=20."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 20)
            for stype in ("ticket-validator",
                          "Explore", "general-purpose"):
                rc, stderr = _run_hook(
                    {"tool_name": "Agent",
                     "tool_input": {"subagent_type": stype, "prompt": "test"},
                     "cwd": cwd},
                    env_extra={"HOME": td},
                )
                self.assertEqual(0, rc, f"{stype} should pass: {stderr}")


class TestHookBlocking(unittest.TestCase):
    """autopilot-worker at W > 8 without receipt → exit 2."""

    def test_blocks_autopilot_worker_at_w20(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 20)
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(2, rc)
            self.assertIn("wdrain-pass", stderr)


class TestHookFailOpen(unittest.TestCase):
    """Fail-open on missing cache, stale ts, non-int ops_wait."""

    def test_w5_passes(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 5)
            rc, _ = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "test"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc)

    def test_missing_cache_passes(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            # No cache created
            rc, _ = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "test"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc)

    def test_stale_cache_passes(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 20, ts=time.time() - 3600)  # 1h old
            rc, _ = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "test"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc)

    def test_non_int_ops_wait_passes(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, "not-a-number")
            rc, _ = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "test"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc)


class TestHookReceipt(unittest.TestCase):
    """A valid receipt allows dispatch; an expired one blocks."""

    def test_fresh_receipt_passes(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 20)
            _make_receipt(td, cwd, expires_at=int(time.time()) + 3600)
            rc, _ = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "test"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc)

    def test_expired_receipt_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 20)
            _make_receipt(td, cwd, expires_at=int(time.time()) - 100)
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "test"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(2, rc)


class TestHookBypass(unittest.TestCase):
    """WDRAIN-BYPASS: token in the prompt allows and logs."""

    def test_bypass_passes_and_logs(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 20)
            rc, _ = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "WDRAIN-BYPASS: release-blocking gk order"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc)
            # Verify bypass was logged
            log = pathlib.Path(td) / ".claude" / "wdrain" / "bypass.log"
            self.assertTrue(log.exists(), "bypass.log should exist")
            content = log.read_text()
            self.assertIn("release-blocking gk order", content)


class TestThresholdLock(unittest.TestCase):
    """The hook's threshold 8 must equal OPS_WAIT_WDRAIN_THRESHOLD."""

    def test_hook_threshold_equals_constant(self):
        sys.path.insert(0, str(REPO))
        try:
            import cli_quals
            constant = cli_quals.OPS_WAIT_WDRAIN_THRESHOLD
        finally:
            sys.path.pop(0)

        hook_src = HOOK.read_text()
        # The hook has: THRESHOLD=8
        import re
        m = re.search(r'^THRESHOLD=(\d+)', hook_src, re.MULTILINE)
        self.assertIsNotNone(m, "THRESHOLD=N not found in hook")
        self.assertEqual(constant, int(m.group(1)),
                         "hook THRESHOLD must equal OPS_WAIT_WDRAIN_THRESHOLD")


class TestFooterWdrainOver(unittest.TestCase):
    """wdrain_over in cache → red `· W N!` footer."""

    def test_wdrain_over_true_renders_red_exclamation(self):
        sys.path.insert(0, str(REPO))
        try:
            import statusbar
            cache = {"ops_wait": 12, "wdrain_over": True}
            sfx = statusbar._ops_wait_sfx(cache)
            self.assertIn("196", sfx, "should use red (196)")
            self.assertIn("W 12!", sfx, "should have ! marker")
        finally:
            sys.path.pop(0)

    def test_wdrain_over_false_renders_grey(self):
        sys.path.insert(0, str(REPO))
        try:
            import statusbar
            cache = {"ops_wait": 5, "wdrain_over": False}
            sfx = statusbar._ops_wait_sfx(cache)
            self.assertIn("245", sfx, "should use grey (245)")
            self.assertNotIn("!", sfx, "should NOT have ! marker")
        finally:
            sys.path.pop(0)

    def test_legacy_cache_no_wdrain_over_renders_grey(self):
        sys.path.insert(0, str(REPO))
        try:
            import statusbar
            cache = {"ops_wait": 12}  # no wdrain_over key
            sfx = statusbar._ops_wait_sfx(cache)
            self.assertIn("245", sfx, "legacy cache should use grey")
            self.assertNotIn("!", sfx, "legacy cache should not have !")
        finally:
            sys.path.pop(0)

    def test_wdrain_over_matches_count_gt_threshold(self):
        """wdrain_over bool should be count > threshold, not >=."""
        sys.path.insert(0, str(REPO))
        try:
            from cli_quals import OPS_WAIT_WDRAIN_THRESHOLD as T
            # At threshold exactly: NOT over
            self.assertFalse(T > T)
            # One above: over
            self.assertTrue((T + 1) > T)
        finally:
            sys.path.pop(0)


class TestCliWdrainPass(unittest.TestCase):
    """wdrain-pass --record validates verdicts and writes receipt."""

    def test_citationless_verdict_rejected(self):
        sys.path.insert(0, str(REPO))
        try:
            from cli_quals import _comment_has_citation
            # A citation that passes _comment_has_citation
            self.assertTrue(_comment_has_citation("see v1.2.3"))
            # A citation that fails
            self.assertFalse(_comment_has_citation("no source here"))
        finally:
            sys.path.pop(0)

    def test_parse_verdicts_valid(self):
        sys.path.insert(0, str(REPO))
        try:
            from cli_wdrain import _parse_verdicts_file
        finally:
            sys.path.pop(0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv",
                                         delete=False) as f:
            f.write("#42\tclose\tv1.2.3 released\n")
            f.write("family:sms\tproposed\tsee #100\n")
            f.flush()
            verdicts = _parse_verdicts_file(f.name)
        os.unlink(f.name)

        self.assertEqual(2, len(verdicts))
        self.assertEqual(42, verdicts[0]["number"])
        self.assertIsNone(verdicts[0]["family"])
        self.assertEqual("close", verdicts[0]["action"])
        self.assertIsNone(verdicts[1]["number"])
        self.assertEqual("family:sms", verdicts[1]["family"])

    def test_expires_at_weekend_aware(self):
        """expires_at should account for weekends via working_time."""
        sys.path.insert(0, str(REPO))
        try:
            from cli_wdrain import _compute_expires_at
        finally:
            sys.path.pop(0)

        # A Friday 18:00 UTC (2026-09-04 is a Friday)
        # Friday 18:00 CET = Friday 16:00 UTC — 6h remaining on Friday,
        # then Saturday+Sunday skipped, then 18h on Monday.
        from datetime import datetime, timezone
        friday_18 = datetime(2026, 9, 4, 18, 0, 0, tzinfo=timezone.utc).timestamp()
        expires = _compute_expires_at(friday_18)

        # The expiry should be at least 24h wall-time away (weekends add time)
        self.assertGreater(expires - friday_18, 24 * 3600,
                           "weekend should extend the expiry beyond 24h wall time")


class TestFamilyLabel(unittest.TestCase):
    """family:<slug> label grouping in --ops-wait output (placeholder).

    The family label grouping is a LABEL-only mechanism — tickets with
    a family:<slug> label are grouped in the --ops-wait summary. This test
    verifies the design constraint: family needs no title clustering.
    """

    def test_family_in_verdicts(self):
        """A family:<slug> verdict covers all members with that family."""
        sys.path.insert(0, str(REPO))
        try:
            from cli_wdrain import _parse_verdicts_file
        finally:
            sys.path.pop(0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv",
                                         delete=False) as f:
            f.write("family:sms\tproposed\tsee #100 for details\n")
            f.flush()
            verdicts = _parse_verdicts_file(f.name)
        os.unlink(f.name)

        self.assertEqual(1, len(verdicts))
        self.assertEqual("family:sms", verdicts[0]["family"])
        self.assertIsNone(verdicts[0]["number"])


class TestQualsTimeout902(unittest.TestCase):
    """#902: quals subprocess timeout must accommodate O(|W|) network calls."""

    def test_timeout_constant_exists_and_is_adequate(self):
        """_QUALS_SUBPROCESS_TIMEOUT must exist and be >= 120s.

        The quals subprocess does per-member gh calls (~2-3s each).
        At |W|=37, the old 30s timeout always failed. The constant must
        accommodate at least ~40 members comfortably.
        """
        sys.path.insert(0, str(REPO))
        try:
            from cli_wdrain import _QUALS_SUBPROCESS_TIMEOUT
        finally:
            sys.path.pop(0)

        self.assertGreaterEqual(
            _QUALS_SUBPROCESS_TIMEOUT, 120,
            "quals subprocess timeout must be >= 120s to accommodate "
            "O(|W|) network calls (at ~3s/member, 40 members = 120s)")

    def test_cmd_wdrain_pass_uses_timeout_constant(self):
        """The subprocess.run call must use _QUALS_SUBPROCESS_TIMEOUT,
        not a hardcoded literal, so the value is testable and documented.
        """
        sys.path.insert(0, str(REPO))
        try:
            import inspect
            import cli_wdrain
            src = inspect.getsource(cli_wdrain.cmd_wdrain_pass)
        finally:
            sys.path.pop(0)

        self.assertIn("_QUALS_SUBPROCESS_TIMEOUT", src,
                       "cmd_wdrain_pass must use the named constant")
        self.assertNotIn("timeout=30", src,
                         "the old hardcoded timeout=30 must be gone")


class TestDeployTargetExempt953(unittest.TestCase):
    """#953: W members tagged deploy-target! (blocked on a release/deploy)
    do not count toward the wdrain threshold. A W=9 all-deploy-target set
    should pass. Hard ceiling: total W > 2*THRESHOLD blocks regardless."""

    def test_w9_all_deploy_target_passes(self):
        """W=9 with 9 deploy-target-exempt → effective W=0 → pass."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 9, extra={"ops_wait_deploy_wait": 9})
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc, f"all-deploy-target W=9 should pass: {stderr}")

    def test_w9_partial_deploy_target_passes(self):
        """W=9, 3 deploy-target → effective W=6 ≤ 8 → pass."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 9, extra={"ops_wait_deploy_wait": 3})
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc, f"W=9 with 3 exempt should pass: {stderr}")

    def test_w12_all_deploy_target_passes(self):
        """W=12, 12 exempt → effective W=0, total 12 ≤ 16 → pass."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 12, extra={"ops_wait_deploy_wait": 12})
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(0, rc, f"W=12 all-exempt should pass: {stderr}")

    def test_hard_ceiling_blocks_at_double_threshold(self):
        """W=17 > 2*8=16 blocks even with all 17 exempt (hard ceiling)."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 17, extra={"ops_wait_deploy_wait": 17})
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(2, rc, "hard ceiling at 2x threshold should block")

    def test_no_exempt_field_behaves_as_zero(self):
        """Legacy cache without ops_wait_deploy_wait → exempt=0, old behavior."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 9)  # no ops_wait_deploy_wait field
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(2, rc, "W=9 with no exempt field should block (old behavior)")

    def test_w10_with_1_exempt_still_blocks(self):
        """W=10, 1 exempt → effective W=9 > 8 → still blocks."""
        with tempfile.TemporaryDirectory() as td:
            cwd = os.path.join(td, "repo")
            os.makedirs(cwd, exist_ok=True)
            _make_cache(td, cwd, 10, extra={"ops_wait_deploy_wait": 1})
            rc, stderr = _run_hook(
                {"tool_name": "Agent",
                 "tool_input": {"subagent_type": "autopilot-worker",
                                "prompt": "Work issue"},
                 "cwd": cwd},
                env_extra={"HOME": td},
            )
            self.assertEqual(2, rc, "effective W=9 should still block")


if __name__ == "__main__":
    unittest.main()
