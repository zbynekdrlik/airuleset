"""Tests for cli_webterm_reconcile (#974) — live-argv reconcile for webterm units.

The install step must detect when a RUNNING webterm unit's process argv references
a stale path (e.g. .claude/worktrees/) or a path different from the rendered
launcher/gateway module, and restart the unit. Tests are hermetic under
HOME=$(mktemp -d); they never touch real systemd.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestReconcileLiveArgv(unittest.TestCase):
    """reconcile_live_argv: restart stale-argv units, skip matching ones."""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"HOME": self._home})
        self._patcher.start()
        # Track systemctl calls
        self.systemctl_calls = []

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._home, ignore_errors=True)

    def _run_systemctl(self, args):
        """Fake systemctl: returns (rc, stdout, stderr)."""
        self.systemctl_calls.append(args)
        if args[0] == "show" and "-p" in args and "MainPID" in args:
            unit = args[-1]
            pid = self._pid_map.get(unit, "0")
            return (0, pid, "")
        if args[0] == "restart":
            return (0, "", "")
        return (0, "", "")

    def _make_proc_cmdline(self, pid, argv_parts):
        """Create a fake /proc/<pid>/cmdline."""
        proc_dir = Path(self._home) / "fake_proc" / str(pid)
        proc_dir.mkdir(parents=True, exist_ok=True)
        # /proc/<pid>/cmdline is NUL-separated
        cmdline = b"\x00".join(p.encode() for p in argv_parts) + b"\x00"
        (proc_dir / "cmdline").write_bytes(cmdline)

    def test_stale_worktree_argv_triggers_restart(self):
        """A unit whose argv references .claude/worktrees/ must be restarted."""
        import cli_webterm_reconcile as rec

        self._pid_map = {"webterm-ttyd.service": "12345"}
        stale_path = "/home/user/devel/airuleset/.claude/worktrees/agent-abc123/cli_webterm.py"
        self._make_proc_cmdline(12345, ["/usr/bin/env", "bash", stale_path])

        rendered_paths = {
            "webterm-ttyd.service": "/home/user/devel/airuleset/airuleset-webterm-ttyd.sh",
        }
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, ["webterm-ttyd.service"])
        restart_calls = [c for c in self.systemctl_calls if c[0] == "restart"]
        self.assertEqual(len(restart_calls), 1)
        self.assertEqual(restart_calls[0][1], "webterm-ttyd.service")

    def test_matching_argv_no_restart(self):
        """A unit whose argv matches the rendered path must NOT be restarted."""
        import cli_webterm_reconcile as rec

        correct_path = "/home/user/.claude/airuleset-webterm-ttyd.sh"
        self._pid_map = {"webterm-ttyd.service": "12345"}
        self._make_proc_cmdline(12345, ["/usr/bin/env", "bash", correct_path])

        rendered_paths = {
            "webterm-ttyd.service": correct_path,
        }
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, [])
        restart_calls = [c for c in self.systemctl_calls if c[0] == "restart"]
        self.assertEqual(len(restart_calls), 0)

    def test_mismatched_script_path_triggers_restart(self):
        """A unit whose argv names a DIFFERENT script path (not worktree, but wrong)
        must be restarted."""
        import cli_webterm_reconcile as rec

        old_path = "/home/user/.claude/old-airuleset-webterm-ttyd.sh"
        new_path = "/home/user/.claude/airuleset-webterm-ttyd.sh"
        self._pid_map = {"webterm-ttyd.service": "12345"}
        self._make_proc_cmdline(12345, ["/usr/bin/env", "bash", old_path])

        rendered_paths = {"webterm-ttyd.service": new_path}
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, ["webterm-ttyd.service"])

    def test_gateway_stale_argv_triggers_restart(self):
        """A gateway unit whose argv references a worktree gateway module must be
        restarted."""
        import cli_webterm_reconcile as rec

        stale = "/home/user/devel/airuleset/.claude/worktrees/agent-x/cli_webterm_gateway.py"
        correct = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        self._pid_map = {"webterm-gateway.service": "54321"}
        self._make_proc_cmdline(54321, [
            "/usr/bin/env", "python3", stale,
            "--bind", "100.104.8.125", "--port", "8080",
        ])

        rendered_paths = {"webterm-gateway.service": correct}
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-gateway.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, ["webterm-gateway.service"])

    def test_pid_zero_skipped(self):
        """A unit with MainPID=0 (not running) is skipped, no restart."""
        import cli_webterm_reconcile as rec

        self._pid_map = {"webterm-ttyd.service": "0"}
        rendered_paths = {"webterm-ttyd.service": "/some/path.sh"}
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, [])

    def test_missing_proc_cmdline_skipped(self):
        """A unit whose /proc/<pid>/cmdline is unreadable is skipped (no crash)."""
        import cli_webterm_reconcile as rec

        self._pid_map = {"webterm-ttyd.service": "99999"}
        # Don't create the fake /proc entry -> read will fail
        rendered_paths = {"webterm-ttyd.service": "/some/path.sh"}
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, [])

    def test_multiple_units_mixed(self):
        """Two units: one stale, one matching — only the stale one is restarted."""
        import cli_webterm_reconcile as rec

        correct_launch = "/home/user/.claude/airuleset-webterm-ttyd.sh"
        correct_gw = "/home/user/devel/airuleset/cli_webterm_gateway.py"
        stale_gw = "/home/user/devel/airuleset/.claude/worktrees/agent-z/cli_webterm_gateway.py"

        self._pid_map = {
            "webterm-ttyd.service": "111",
            "webterm-gateway.service": "222",
        }
        self._make_proc_cmdline(111, ["/usr/bin/env", "bash", correct_launch])
        self._make_proc_cmdline(222, ["/usr/bin/env", "python3", stale_gw, "--bind", "100.0.0.1"])

        rendered_paths = {
            "webterm-ttyd.service": correct_launch,
            "webterm-gateway.service": correct_gw,
        }
        restarted = rec.reconcile_live_argv(
            self._run_systemctl,
            ["webterm-ttyd.service", "webterm-gateway.service"],
            rendered_paths,
            log_prefix="webterm",
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(restarted, ["webterm-gateway.service"])


class TestCheckWebtermArgvHealth(unittest.TestCase):
    """check_webterm_argv_health: status rows for cmd_status."""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"HOME": self._home})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._home, ignore_errors=True)

    def _run_systemctl(self, args):
        if args[0] == "show" and "MainPID" in args:
            unit = args[-1]
            pid = self._pid_map.get(unit, "0")
            return (0, pid, "")
        return (0, "", "")

    def _make_proc_cmdline(self, pid, argv_parts):
        proc_dir = Path(self._home) / "fake_proc" / str(pid)
        proc_dir.mkdir(parents=True, exist_ok=True)
        cmdline = b"\x00".join(p.encode() for p in argv_parts) + b"\x00"
        (proc_dir / "cmdline").write_bytes(cmdline)

    def test_ok_status(self):
        """A matching unit shows OK."""
        import cli_webterm_reconcile as rec

        correct = "/home/user/.claude/airuleset-webterm-ttyd.sh"
        self._pid_map = {"webterm-ttyd.service": "100"}
        self._make_proc_cmdline(100, ["/usr/bin/env", "bash", correct])

        lines = rec.check_webterm_argv_health(
            self._run_systemctl,
            {"webterm-ttyd.service": correct},
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("OK", lines[0])

    def test_stale_status(self):
        """A stale-argv unit shows STALE."""
        import cli_webterm_reconcile as rec

        stale = "/home/user/.claude/worktrees/agent-x/cli_webterm.py"
        self._pid_map = {"webterm-ttyd.service": "100"}
        self._make_proc_cmdline(100, ["/usr/bin/env", "bash", stale])

        lines = rec.check_webterm_argv_health(
            self._run_systemctl,
            {"webterm-ttyd.service": "/home/user/.claude/airuleset-webterm-ttyd.sh"},
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("STALE", lines[0])
        self.assertIn("worktrees", lines[0])

    def test_not_running_status(self):
        """A unit with MainPID=0 shows NOT RUNNING."""
        import cli_webterm_reconcile as rec

        self._pid_map = {"webterm-ttyd.service": "0"}
        lines = rec.check_webterm_argv_health(
            self._run_systemctl,
            {"webterm-ttyd.service": "/some/path.sh"},
            proc_root=Path(self._home) / "fake_proc",
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("NOT RUNNING", lines[0])


if __name__ == "__main__":
    unittest.main()
