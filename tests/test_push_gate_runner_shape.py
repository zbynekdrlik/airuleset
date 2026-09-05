"""Push gate runner-shape pass (airuleset #875).

The pre-push gate in `cli_remote.cmd_push` must run a CI-mirroring "Pass A"
(runner-shape) pass BEFORE the existing unittest pass, under a clean HOME
(no ~/.claude, no dev1 tools) with the exact CI pytest argv derived from the
deny-list. Two consecutive main CI reds (v0.1.149, v0.1.150) were green in
the lane because the push gate ran as the owner uid with a real ~/.claude.

These tests monkeypatch `subprocess.run` inside `cmd_push` to capture the
calls it would make, then assert the runner-shape pass's argv and env.
"""
# airuleset:script-ok test file — SystemExit catches are expected test behavior

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_remote

REPO = Path(cli_remote.__file__).resolve().parent
DENYLIST = REPO / ".github" / "box-bound-tests.txt"


def _ci_pytest_args():
    """Run scripts/ci_pytest_args.py against the real deny-list and return the
    expected pytest args list."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ci_pytest_args.py"),
         str(DENYLIST)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return [a for a in result.stdout.strip().split("\n") if a]


class TestPassAArgvMatchesCI(unittest.TestCase):
    """Pass A's subprocess argv must be the EXACT CI argv: python3 -m pytest
    tests/ <deny-list args> -p no:cacheprovider -o addopts= -q. Drift-proof:
    compared against scripts/ci_pytest_args.py run on the real deny-list."""

    def test_pass_a_argv_matches_ci(self):
        ci_args = _ci_pytest_args()
        expected_argv = [
            sys.executable, "-m", "pytest", "tests/",
            *ci_args,
            "-p", "no:cacheprovider",
            "-o", "addopts=",
            "-q",
        ]

        calls = []
        _real_run = subprocess.run

        def _capture(cmd, **kw):
            # Let the ci_pytest_args.py subprocess run for REAL so it
            # produces the correct deny-list args.
            if (isinstance(cmd, list) and len(cmd) >= 2
                    and "ci_pytest_args" in str(cmd[1])):
                return _real_run(cmd, **kw)
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch("subprocess.run", side_effect=_capture):
            with mock.patch.object(cli_remote, "_tracked_tree_fingerprint",
                                   return_value={}):
                with mock.patch.object(cli_remote, "_classify_push_gate_outcome",
                                       return_value=(True, "clean", "  Tests passed.")):
                    with mock.patch.object(cli_remote, "_check_push_tmpdir_litter",
                                           return_value=(True, 0)):
                        with mock.patch.object(cli_remote, "_deploy_to_all_remotes"):
                            try:
                                import argparse
                                cli_remote.cmd_push(argparse.Namespace())
                            except SystemExit:
                                # cmd_push may exit on install/deploy steps —
                                # the captured calls are what we need
                                pass  # noqa: expected in test harness

        # Find the Pass A call — it should be the one with pytest in the argv
        pass_a_calls = [
            (cmd, kw) for cmd, kw in calls
            if isinstance(cmd, list) and len(cmd) > 2
            and cmd[1] == "-m" and cmd[2] == "pytest"
        ]

        self.assertGreaterEqual(len(pass_a_calls), 1,
                                "No Pass A pytest call found in captured subprocess.run calls. "
                                "Captured calls: %s" % [c[0] for c in calls])

        actual_argv = pass_a_calls[0][0]
        self.assertEqual(actual_argv, expected_argv,
                         "Pass A argv does not match CI argv")


class TestPassAEnvHomeIsFreshTmp(unittest.TestCase):
    """Pass A's env must have HOME set to a fresh tmp dir (not the real HOME),
    with a .gitconfig containing both safe.directory entries, and all the
    existing AIRULESET_* / TMPDIR overrides present."""

    def test_pass_a_env_home_is_fresh_tmp(self):
        # Capture env and .gitconfig content AT CALL TIME (inside the mock),
        # because _run_pass_a's TemporaryDirectory cleans the home after
        # the subprocess finishes — inspecting post-return would see a
        # deleted dir (F1, #875 review fix).
        captured = {}
        _real_run = subprocess.run

        def _capture(cmd, **kw):
            if (isinstance(cmd, list) and len(cmd) >= 2
                    and "ci_pytest_args" in str(cmd[1])):
                return _real_run(cmd, **kw)
            if (isinstance(cmd, list) and len(cmd) > 2
                    and cmd[1] == "-m" and cmd[2] == "pytest"
                    and "env" not in captured):
                env = kw.get("env", {})
                captured["env"] = dict(env)
                home = env.get("HOME", "")
                gc = Path(home) / ".gitconfig"
                captured["gitconfig_exists"] = gc.exists()
                captured["gitconfig_text"] = gc.read_text() if gc.exists() else ""
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch("subprocess.run", side_effect=_capture):
            with mock.patch.object(cli_remote, "_tracked_tree_fingerprint",
                                   return_value={}):
                with mock.patch.object(cli_remote, "_classify_push_gate_outcome",
                                       return_value=(True, "clean", "  Tests passed.")):
                    with mock.patch.object(cli_remote, "_check_push_tmpdir_litter",
                                           return_value=(True, 0)):
                        with mock.patch.object(cli_remote, "_deploy_to_all_remotes"):
                            try:
                                import argparse
                                cli_remote.cmd_push(argparse.Namespace())
                            except SystemExit:
                                pass  # noqa: expected in test harness

        self.assertIn("env", captured, "No Pass A pytest call captured")
        env = captured["env"]

        # HOME must not be the real HOME
        real_home = os.environ.get("HOME", "")
        self.assertNotEqual(env.get("HOME", real_home), real_home,
                            "Pass A HOME must be a fresh tmp dir, not the real HOME")

        # .gitconfig must exist and contain safe.directory (captured at call time)
        self.assertTrue(captured["gitconfig_exists"],
                        "Pass A HOME must contain .gitconfig")
        self.assertIn("safe", captured["gitconfig_text"])
        self.assertIn("directory", captured["gitconfig_text"])

        # Existing env overrides must be present
        self.assertIn("TMPDIR", env, "TMPDIR override must be present in Pass A")
        self.assertIn("AIRULESET_DRAFT_RESCUE_DIR", env)

        # PYTEST_ADDOPTS must NOT leak into Pass A (F3, #875 review)
        self.assertNotIn("PYTEST_ADDOPTS", env,
                         "PYTEST_ADDOPTS must be stripped from Pass A env")


class TestPassAFailureBlocksPush(unittest.TestCase):
    """Pass A returning rc=1 must make cmd_push exit 1 BEFORE any ssh/deploy
    call, and Pass A must run FIRST (before Pass B)."""

    def test_pass_a_failure_blocks_push(self):
        call_order = []
        _real_run = subprocess.run

        def _capture(cmd, **kw):
            if (isinstance(cmd, list) and len(cmd) >= 2
                    and "ci_pytest_args" in str(cmd[1])):
                return _real_run(cmd, **kw)
            if isinstance(cmd, list) and len(cmd) > 2:
                if cmd[1] == "-m" and cmd[2] == "pytest":
                    call_order.append("pass_a")
                    # Pass A fails
                    return subprocess.CompletedProcess(cmd, 1, "", "FAILED")
                elif cmd[1] == "-m" and cmd[2] == "unittest":
                    call_order.append("pass_b")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
            if isinstance(cmd, list) and cmd[0] == "ruff":
                call_order.append("ruff")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            call_order.append(("other", cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        deploy_called = []

        def _deploy(*a, **kw):
            deploy_called.append(True)

        with mock.patch("subprocess.run", side_effect=_capture):
            with mock.patch.object(cli_remote, "_deploy_to_all_remotes",
                                   side_effect=_deploy):
                with self.assertRaises(SystemExit) as ctx:
                    import argparse
                    cli_remote.cmd_push(argparse.Namespace())

        self.assertEqual(ctx.exception.code, 1,
                         "cmd_push must exit 1 when Pass A fails")
        self.assertFalse(deploy_called,
                         "Deploy must NOT be called when Pass A fails")
        # Pass A must run before Pass B
        self.assertIn("pass_a", call_order,
                      "Pass A (pytest) must have been called")
        self.assertNotIn("pass_b", call_order,
                         "Pass B (unittest) must NOT run when Pass A fails")


if __name__ == "__main__":
    unittest.main()
