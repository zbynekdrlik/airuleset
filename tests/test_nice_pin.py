"""Tests for the nice-0 pinning (#866).

Component (1): launch script pins nice 0 via `renice -n 0 -p $$`.
Component (2): tmux cutover service template carries `Nice=0`.
Component (3): watchdog nice_check module reads /proc/<pid>/stat field 19.
Component (4): hook child scheduling — not covered here (stdin/exit contract
               analysis showed it is NOT safe to add `nice -n 10` to hook
               children without risking stdin piping issues; documented below).
"""
import os
import unittest

# The repo root is the worktree dir itself; add it to sys.path so we can
# import the modules under test.
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLaunchScriptNicePin(unittest.TestCase):
    """The managed launch script must pin nice 0 before exec claude."""

    def test_launch_script_contains_renice(self):
        from cli_claude_scripts import CLAUDE_LAUNCH_SCRIPT_CONTENT
        self.assertIn("renice", CLAUDE_LAUNCH_SCRIPT_CONTENT,
                       "launch script must contain a renice call")

    def test_renice_before_first_exec(self):
        """renice must appear BEFORE the first `exec claude` line."""
        from cli_claude_scripts import CLAUDE_LAUNCH_SCRIPT_CONTENT
        lines = CLAUDE_LAUNCH_SCRIPT_CONTENT.splitlines()
        renice_line = None
        first_exec_line = None
        for i, ln in enumerate(lines):
            if renice_line is None and "renice" in ln:
                renice_line = i
            if first_exec_line is None and ln.strip().startswith("exec claude"):
                first_exec_line = i
        self.assertIsNotNone(renice_line, "no renice found")
        self.assertIsNotNone(first_exec_line, "no exec claude found")
        self.assertLess(renice_line, first_exec_line,
                        "renice must come before the first exec claude")

    def test_renice_targets_self(self):
        """The renice call must target $$ (current shell PID) at nice 0."""
        from cli_claude_scripts import CLAUDE_LAUNCH_SCRIPT_CONTENT
        # Look for a line that renices the current process to nice 0
        found = False
        for ln in CLAUDE_LAUNCH_SCRIPT_CONTENT.splitlines():
            if "renice" in ln and "$$" in ln and "-n 0" in ln:
                found = True
                break
        self.assertTrue(found,
                        "launch script must renice -n 0 -p $$ (pin self to nice 0)")

    def test_renice_is_best_effort(self):
        """renice must not abort the script on failure (|| true or 2>/dev/null)."""
        from cli_claude_scripts import CLAUDE_LAUNCH_SCRIPT_CONTENT
        for ln in CLAUDE_LAUNCH_SCRIPT_CONTENT.splitlines():
            if "renice" in ln and "$$" in ln:
                self.assertTrue(
                    "|| true" in ln or "2>/dev/null" in ln or "||" in ln,
                    "renice must be best-effort (|| true or stderr redirect)")
                return
        self.fail("no renice line found to check")


class TestTmuxCutoverNice(unittest.TestCase):
    """The tmux cutover service template must carry Nice=0."""

    def test_service_template_has_nice_zero(self):
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "settings", "tmux-cutover.service.template")
        with open(template_path) as f:
            content = f.read()
        self.assertIn("Nice=0", content,
                       "tmux cutover service template must carry Nice=0")

    def test_nice_in_service_section(self):
        """Nice=0 must be in the [Service] section."""
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "settings", "tmux-cutover.service.template")
        with open(template_path) as f:
            content = f.read()
        # Find [Service] and [Install] sections
        svc_idx = content.index("[Service]")
        install_idx = content.index("[Install]")
        service_section = content[svc_idx:install_idx]
        self.assertIn("Nice=0", service_section,
                       "Nice=0 must be within the [Service] section")


class TestNiceCheckModule(unittest.TestCase):
    """The watchdog nice_check module must parse /proc/pid/stat correctly."""

    def test_parse_nice_from_stat(self):
        """nice_from_proc_stat must extract nice (field 19, 0-indexed 18)."""
        from watchdog.nice_check import nice_from_proc_stat
        # A synthetic /proc/<pid>/stat line — field 19 (1-indexed) = nice.
        # Fields: pid, comm (in parens, may contain spaces), state, ppid, pgrp,
        # session, tty_nr, tpgid, flags, minflt, cminflt, majflt, cmajflt,
        # utime, stime, cutime, cstime, priority, nice, ...
        stat_line = (
            "12345 (claude code (beta)) S 100 12345 12345 0 -1 4194304 "
            "1000 0 0 0 50 10 0 0 20 5 1 0 123456 100000 500 "
            "18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
        )
        nice = nice_from_proc_stat(stat_line)
        self.assertEqual(nice, 5, "nice value should be field 19 = 5")

    def test_parse_nice_zero(self):
        from watchdog.nice_check import nice_from_proc_stat
        stat_line = (
            "999 (bash) S 1 999 999 0 -1 0 "
            "0 0 0 0 0 0 0 0 20 0 1 0 100 50000 200 "
            "18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
        )
        nice = nice_from_proc_stat(stat_line)
        self.assertEqual(nice, 0)

    def test_parse_nice_negative(self):
        from watchdog.nice_check import nice_from_proc_stat
        stat_line = (
            "999 (tmux: server) S 1 999 999 0 -1 0 "
            "0 0 0 0 0 0 0 0 30 -10 1 0 100 50000 200 "
            "18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
        )
        nice = nice_from_proc_stat(stat_line)
        self.assertEqual(nice, -10)

    def test_check_nice_nonzero_logs(self):
        """check_pids_nice must return entries for non-zero nice processes."""
        from watchdog.nice_check import check_pids_nice
        # Inject a fake stat reader that returns nice 10 for pid 42
        def fake_stat(pid):
            return (
                "%d (claude) S 1 %d %d 0 -1 0 "
                "0 0 0 0 0 0 0 0 20 10 1 0 100 50000 200 "
                "18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
            ) % (pid, pid, pid)
        results = check_pids_nice([42], stat_reader=fake_stat)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["pid"], 42)
        self.assertEqual(results[0]["nice"], 10)

    def test_check_nice_zero_no_entries(self):
        """check_pids_nice must return nothing for nice-0 processes."""
        from watchdog.nice_check import check_pids_nice
        def fake_stat(pid):
            return (
                "%d (claude) S 1 %d %d 0 -1 0 "
                "0 0 0 0 0 0 0 0 20 0 1 0 100 50000 200 "
                "18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
            ) % (pid, pid, pid)
        results = check_pids_nice([42], stat_reader=fake_stat)
        self.assertEqual(len(results), 0)

    def test_check_nice_unreadable_pid_skipped(self):
        """An unreadable /proc/<pid>/stat should be silently skipped."""
        from watchdog.nice_check import check_pids_nice
        def fake_stat(pid):
            raise FileNotFoundError("no such process")
        results = check_pids_nice([42], stat_reader=fake_stat)
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
