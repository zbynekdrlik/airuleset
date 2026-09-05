"""Tests for watchdog.priority_policy (#885).

Job 44 — priority policy enforcer (renice Chrome/MCP to yield CPU).
Job 45 — orphan bg-poll-loop reaper (SIGKILL dead sessions' poll loops).

All tests use injected seams — no real /proc, no real gh, no real
renice/kill.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watchdog.priority_policy import (
    ppid_from_proc_stat,
    _is_chrome_family,
    _is_mcp_node,
    _classify_process,
    _is_poll_loop_signature,
    _extract_target,
    _is_orphan,
    priority_policy_job,
    orphan_poll_reaper,
)


# ---------------------------------------------------------------------------
# Part A — Priority policy enforcer tests
# ---------------------------------------------------------------------------

class TestChromeClassifier(unittest.TestCase):
    """Argv[0]-anchored Chrome family detection."""

    def test_chrome_basename(self):
        self.assertTrue(_is_chrome_family("chrome --headless"))

    def test_chromium(self):
        self.assertTrue(_is_chrome_family("/usr/bin/chromium --no-sandbox"))

    def test_headless_shell(self):
        self.assertTrue(_is_chrome_family("headless_shell --disable-gpu"))

    def test_nacl_helper(self):
        self.assertTrue(_is_chrome_family("nacl_helper"))

    def test_crashpad(self):
        self.assertTrue(_is_chrome_family(
            "/opt/chrome/chrome-crashpad-handler --no-rate-limit"))

    def test_crashpad_underscore(self):
        self.assertTrue(_is_chrome_family("chrome_crashpad_handler"))

    def test_not_chrome(self):
        self.assertFalse(_is_chrome_family("node server.js"))

    def test_quoting_not_matched(self):
        """A process mentioning chrome in args but not argv[0]."""
        self.assertFalse(_is_chrome_family(
            "watch pgrep -af chrome --headless"))

    def test_empty(self):
        self.assertFalse(_is_chrome_family(""))
        self.assertFalse(_is_chrome_family(None))


class TestMcpNodeClassifier(unittest.TestCase):
    """Structural MCP-node detection via parent cmdline."""

    def test_node_with_claude_parent(self):
        self.assertTrue(_is_mcp_node(
            "node /home/u/.claude/mcp-server.js",
            parent_cmdline="claude --model fable"))

    def test_node_with_shell_parent(self):
        """node with a non-claude parent is NOT an MCP server."""
        self.assertFalse(_is_mcp_node(
            "node /home/u/.claude/mcp-server.js",
            parent_cmdline="bash"))

    def test_claude_cli_itself_not_matched(self):
        """The claude CLI node process must NOT match."""
        self.assertFalse(_is_mcp_node(
            "node /usr/lib/claude/cli.js --model fable",
            parent_cmdline="bash"))

    def test_claude_cli_npm_shape_not_matched(self):
        """The npm-shape CLI (basename cli.js, path contains claude-code)
        must NOT match even with a claude parent (#885 F3)."""
        self.assertFalse(_is_mcp_node(
            "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js --resume",
            parent_cmdline="claude --model fable"))

    def test_no_parent_cmdline(self):
        self.assertFalse(_is_mcp_node("node server.js", parent_cmdline=None))

    def test_nodejs_basename(self):
        self.assertTrue(_is_mcp_node(
            "nodejs /mcp/serve.js",
            parent_cmdline="/usr/bin/claude --resume"))


class TestClassifyProcess(unittest.TestCase):
    def test_chrome(self):
        self.assertEqual(_classify_process("chrome --headless"), "chrome")

    def test_mcp(self):
        self.assertEqual(
            _classify_process("node /mcp/srv.js",
                              parent_cmdline="claude"),
            "mcp-node")

    def test_unknown(self):
        self.assertIsNone(_classify_process("python3 app.py"))


class TestPpidFromProcStat(unittest.TestCase):
    def test_normal(self):
        # pid=1234, comm=(bash), state=S, ppid=5678
        line = "1234 (bash) S 5678 1234 1234 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0"
        self.assertEqual(ppid_from_proc_stat(line), 5678)

    def test_comm_with_spaces(self):
        line = "99 (tmux: server) S 1 99 99 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0"
        self.assertEqual(ppid_from_proc_stat(line), 1)

    def test_malformed(self):
        with self.assertRaises(ValueError):
            ppid_from_proc_stat("no parens here")


class TestPriorityPolicyJob(unittest.TestCase):
    """Integration tests for the priority enforcer."""

    def _make_ps(self, rows):
        """Return a ps_fetch that yields the given rows."""
        return lambda: rows

    def _make_stat(self, nice_map, ppid_map=None):
        """Return a stat_reader that yields stat lines with the given nice
        and ppid values per pid."""
        ppid_map = ppid_map or {}
        def reader(pid):
            nice = nice_map.get(pid, 0)
            ppid = ppid_map.get(pid, 1)
            return "%d (proc) S %d %d %d 0 -1 0 0 0 0 0 0 0 0 0 20 %d 1 0" % (
                pid, ppid, pid, pid, nice)
        return reader

    def test_renices_chrome_at_nice0(self):
        """Chrome at nice 0 should be reniced to 10 + ionice idle."""
        calls = {"renice": [], "ionice": []}
        ps = self._make_ps([(100, 3600, 10, "headless_shell --disable-gpu")])
        stat = self._make_stat({100: 0}, {100: 1})
        def verify(pid):
            return "headless_shell --disable-gpu"
        def renice(pid, val):
            return calls["renice"].append((pid, val))
        def ionice(pid):
            return calls["ionice"].append(pid)
        def parent_cmd(pid):
            return "bash"

        logs = priority_policy_job(
            ps_fetch=ps, renice_fn=renice, ionice_fn=ionice,
            verify_fn=verify, stat_reader=stat,
            parent_cmdline_fn=parent_cmd)

        self.assertEqual(calls["renice"], [(100, 10)])
        self.assertEqual(calls["ionice"], [100])
        self.assertTrue(any("renice pid=100 label=chrome 0->10" in ln for ln in logs))
        self.assertTrue(any("+ionice-idle" in ln for ln in logs))

    def test_idempotent_at_target(self):
        """Chrome already at nice 10 — no renice call."""
        calls = {"renice": []}
        ps = self._make_ps([(100, 3600, 10, "chrome --headless")])
        stat = self._make_stat({100: 10}, {100: 1})
        def renice(pid, val):
            return calls["renice"].append((pid, val))
        def parent_cmd(pid):
            return "bash"

        logs = priority_policy_job(
            ps_fetch=ps, renice_fn=renice,
            stat_reader=stat, parent_cmdline_fn=parent_cmd)

        self.assertEqual(calls["renice"], [])
        self.assertEqual(logs, [])

    def test_never_lowers_nice(self):
        """Chrome at nice 15 — NEVER lowered to 10."""
        calls = {"renice": []}
        ps = self._make_ps([(100, 3600, 10, "chrome --headless")])
        stat = self._make_stat({100: 15}, {100: 1})
        def renice(pid, val):
            return calls["renice"].append((pid, val))
        def parent_cmd(pid):
            return "bash"

        priority_policy_job(
            ps_fetch=ps, renice_fn=renice,
            stat_reader=stat, parent_cmdline_fn=parent_cmd)

        self.assertEqual(calls["renice"], [])

    def test_dry_run_never_mutates(self):
        ps = self._make_ps([(100, 3600, 10, "chrome --headless")])
        stat = self._make_stat({100: 0}, {100: 1})
        calls = {"renice": []}
        def renice(pid, val):
            return calls["renice"].append((pid, val))
        def parent_cmd(pid):
            return "bash"

        logs = priority_policy_job(
            ps_fetch=ps, renice_fn=renice,
            stat_reader=stat, parent_cmdline_fn=parent_cmd,
            dry_run=True)

        self.assertEqual(calls["renice"], [])
        self.assertTrue(any("DRY-RUN" in ln for ln in logs))

    def test_default_renice_fn_is_wired(self):
        """renice_fn=None defaults to the real os.setpriority, not 'not wired'.
        F1 review finding: the job must NOT ship inert (#885)."""
        from watchdog.priority_policy import _default_renice_fn  # noqa: F401
        self.assertIs(priority_policy_job.__defaults__[1], None,
                      "renice_fn param default must be None (resolved to real at call)")
        # Verify the resolution: calling with renice_fn=None on a DRY-RUN
        # should show DRY-RUN, not 'not wired'.
        ps = self._make_ps([(100, 3600, 10, "chrome --headless")])
        stat = self._make_stat({100: 0}, {100: 1})
        def parent_cmd(pid):
            return "bash"

        logs = priority_policy_job(
            ps_fetch=ps, renice_fn=None,
            stat_reader=stat, parent_cmdline_fn=parent_cmd,
            dry_run=True)

        self.assertTrue(any("DRY-RUN" in ln for ln in logs))
        self.assertFalse(any("not wired" in ln for ln in logs))

    def test_toctou_reused_pid_skipped(self):
        """pid reused by a non-chrome process → no renice."""
        calls = {"renice": []}
        ps = self._make_ps([(100, 3600, 10, "chrome --headless")])
        stat = self._make_stat({100: 0}, {100: 1})
        def verify(pid):
            return "python3 app.py"
        def renice(pid, val):
            return calls["renice"].append((pid, val))
        def parent_cmd(pid):
            return "bash"

        logs = priority_policy_job(
            ps_fetch=ps, renice_fn=renice, verify_fn=verify,
            stat_reader=stat, parent_cmdline_fn=parent_cmd)

        self.assertEqual(calls["renice"], [])
        self.assertTrue(any("reused" in ln for ln in logs))

    def test_mcp_node_reniced(self):
        """Node MCP server with claude parent → reniced to 10."""
        calls = {"renice": []}
        ps = self._make_ps([(200, 100, 5, "node /mcp/playwright.js")])
        # pid 200 has ppid 300 (the claude CLI)
        stat = self._make_stat({200: 0}, {200: 300})
        parent_cmdline_map = {300: "claude --model fable"}
        def verify(pid):
            return "node /mcp/playwright.js"
        def renice(pid, val):
            return calls["renice"].append((pid, val))
        def parent_cmd(pid):
            return parent_cmdline_map.get(pid)

        logs = priority_policy_job(
            ps_fetch=ps, renice_fn=renice, verify_fn=verify,
            stat_reader=stat, parent_cmdline_fn=parent_cmd)

        self.assertEqual(calls["renice"], [(200, 10)])
        self.assertTrue(any("label=mcp-node" in ln for ln in logs))

    def test_ps_error_kills_nothing(self):
        def bad_ps():
            raise OSError("ps failed")
        logs = priority_policy_job(ps_fetch=bad_ps)
        self.assertTrue(any("ps error" in ln for ln in logs))

    def test_ps_none_kills_nothing(self):
        logs = priority_policy_job(ps_fetch=lambda: None)
        self.assertEqual(logs, [])


# ---------------------------------------------------------------------------
# Part B — Orphan bg-poll-loop reaper tests
# ---------------------------------------------------------------------------

class TestPollLoopSignature(unittest.TestCase):
    def test_gh_run_view_loop(self):
        cmd = ("bash -c 'while :; do gh run view 123 --json status; "
               "sleep 60; done'")
        self.assertTrue(_is_poll_loop_signature(cmd))

    def test_gh_pr_view_loop(self):
        cmd = "sh -c 'while :; do gh pr view 45 --json state; sleep 30; done'"
        self.assertTrue(_is_poll_loop_signature(cmd))

    def test_not_a_loop(self):
        self.assertFalse(_is_poll_loop_signature("gh run view 123"))

    def test_watch_quoting_not_matched(self):
        self.assertFalse(_is_poll_loop_signature(
            "watch 'gh run view 123 while sleep'"))

    def test_empty(self):
        self.assertFalse(_is_poll_loop_signature(""))
        self.assertFalse(_is_poll_loop_signature(None))


class TestExtractTarget(unittest.TestCase):
    def test_run_id(self):
        cmd = "bash -c 'while :; do gh run view 98765 --json status; sleep 60; done'"
        self.assertEqual(_extract_target(cmd), ("run", "98765"))

    def test_pr_number(self):
        cmd = "sh -c 'while :; do gh pr view 42 --json state; sleep 30; done'"
        self.assertEqual(_extract_target(cmd), ("pr", "42"))

    def test_pr_checks(self):
        cmd = "bash -c 'while :; do gh pr checks 42; sleep 60; done'"
        self.assertEqual(_extract_target(cmd), ("pr", "42"))

    def test_no_match(self):
        cmd = "bash -c 'while :; do gh api repos/foo/bar; sleep 60; done'"
        self.assertIsNone(_extract_target(cmd))


class TestIsOrphan(unittest.TestCase):
    def test_orphan_chain_to_init(self):
        """ppid chain: pid 100 -> ppid 50 -> ppid 1 (init), no claude."""
        stat_map = {
            100: "100 (bash) S 50 100 100 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0",
            50: "50 (timeout) S 1 50 50 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0",
        }
        cmd_map = {50: "timeout 10800 bash -c while..."}

        self.assertTrue(_is_orphan(
            100,
            stat_reader=lambda p: stat_map[p],
            cmdline_reader=lambda p: cmd_map.get(p, "")))

    def test_parented_by_claude(self):
        """ppid chain: pid 100 -> ppid 50 (claude) → NOT orphan."""
        stat_map = {
            100: "100 (bash) S 50 100 100 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0",
        }
        cmd_map = {50: "claude --model fable"}

        self.assertFalse(_is_orphan(
            100,
            stat_reader=lambda p: stat_map[p],
            cmdline_reader=lambda p: cmd_map.get(p, "")))

    def test_parented_by_npm_shape_claude(self):
        """ppid chain: pid 100 -> ppid 50 (node .../claude-code/cli.js).
        Must detect as parented, not orphan (#885 F2: basename cli.js
        doesn't contain 'claude', but full cmdline does)."""
        stat_map = {
            100: "100 (bash) S 50 100 100 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0",
        }
        cmd_map = {
            50: "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js --resume"
        }

        self.assertFalse(_is_orphan(
            100,
            stat_reader=lambda p: stat_map[p],
            cmdline_reader=lambda p: cmd_map.get(p, "")))

    def test_read_error_not_orphan(self):
        """Any read error → NOT orphan (fail-safe)."""
        def bad_stat(pid):
            raise OSError("proc gone")
        self.assertFalse(_is_orphan(100, stat_reader=bad_stat))


class TestOrphanPollReaper(unittest.TestCase):
    """Integration tests for the orphan bg-poll-loop reaper."""

    def _poll_loop_cmd(self, kind="run", ident="12345"):
        if kind == "run":
            return ("bash -c 'while :; do gh run view %s --json status; "
                    "sleep 60; done'" % ident)
        return ("bash -c 'while :; do gh pr view %s --json state; "
                "sleep 30; done'" % ident)

    def _orphan_stat(self, pid):
        """Stat line with ppid=1 (orphaned)."""
        return "%d (bash) S 1 %d %d 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0" % (
            pid, pid, pid)

    def _parented_stat(self, pid, ppid=500):
        return "%d (bash) S %d %d %d 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0" % (
            pid, ppid, pid, pid)

    def test_orphan_terminal_run_killed(self):
        """Orphan poll loop for a completed run → SIGKILL."""
        cmd = self._poll_loop_cmd("run", "123")
        kills = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._orphan_stat(pid)
        def verify(pid):
            return cmd
        def cwd(pid):
            return "/home/u/repo"
        def gh_check(cwd, kind, ident):
            return "terminal"
        def cmdline(pid):
            return ""

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            verify_fn=verify, stat_reader=stat,
            cmdline_reader=cmdline, cwd_reader=cwd,
            gh_check_fn=gh_check)

        self.assertEqual(kills, [100])
        self.assertTrue(any("SIGKILL pid=100" in ln for ln in logs))
        self.assertTrue(any("run=123" in ln for ln in logs))

    def test_parented_loop_no_gh_call(self):
        """A loop with a live claude parent spends NO gh API call."""
        cmd = self._poll_loop_cmd("run", "123")
        gh_calls = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._parented_stat(pid, 500)
        cmdline_map = {500: "claude --resume"}
        def cmdline(pid):
            return cmdline_map.get(pid, "")

        def gh_check(cwd, kind, ident):
            gh_calls.append((kind, ident))
            return "terminal"

        orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: None,
            stat_reader=stat, cmdline_reader=cmdline,
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=gh_check)

        self.assertEqual(gh_calls, [])

    def test_live_target_kept(self):
        """Terminal check returns 'live' → no kill."""
        cmd = self._poll_loop_cmd("run", "123")
        kills = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._orphan_stat(pid)

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            verify_fn=lambda pid: cmd, stat_reader=stat,
            cmdline_reader=lambda pid: "",
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=lambda *a: "live")

        self.assertEqual(kills, [])
        self.assertTrue(any("skip:target-live" in ln for ln in logs))

    def test_gh_error_kept(self):
        """gh check returns error → no kill (fail-safe)."""
        cmd = self._poll_loop_cmd("run", "123")
        kills = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._orphan_stat(pid)

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            verify_fn=lambda pid: cmd, stat_reader=stat,
            cmdline_reader=lambda pid: "",
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=lambda *a: "error")

        self.assertEqual(kills, [])
        self.assertTrue(any("skip:gh-error" in ln for ln in logs))

    def test_young_orphan_kept(self):
        """Age < 30min → not reaped."""
        cmd = self._poll_loop_cmd("run", "123")
        kills = []
        def ps():
            return [(100, 600, 0, cmd)]  # 10 min

        orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            gh_check_fn=lambda *a: "terminal")

        self.assertEqual(kills, [])

    def test_no_extractable_id_kept(self):
        """Bare gh api loop with no run/PR id → skip."""
        cmd = "bash -c 'while :; do gh api repos/foo/bar; sleep 60; done'"
        kills = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._orphan_stat(pid)

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            stat_reader=stat, cmdline_reader=lambda pid: "",
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=lambda *a: "terminal")

        self.assertEqual(kills, [])
        self.assertTrue(any("skip:no-id" in ln for ln in logs))

    def test_gh_budget_cap(self):
        """Max 3 gh checks per cycle."""
        gh_calls = []

        def gh_check(cwd, kind, ident):
            gh_calls.append(ident)
            return "terminal"

        # 5 distinct targets, each in its own cwd to avoid dedup.
        procs = []
        for i in range(5):
            cmd = self._poll_loop_cmd("run", str(1000 + i))
            procs.append((100 + i, 3600, 0, cmd))

        def ps():
            return procs
        def stat(pid):
            return self._orphan_stat(pid)
        def cwd(pid):
            return "/repo/%d" % pid

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: None,
            verify_fn=lambda pid: procs[pid - 100][3] if 100 <= pid < 105 else "",
            stat_reader=stat, cmdline_reader=lambda pid: "",
            cwd_reader=cwd, gh_check_fn=gh_check,
            max_gh_checks=3)

        self.assertEqual(len(gh_calls), 3)
        # The remaining 2 should be skip:gh-budget.
        budget_skips = [ln for ln in logs if "skip:gh-budget" in ln]
        self.assertEqual(len(budget_skips), 2)

    def test_dedupe_same_target(self):
        """Multiple loops polling the same target = one gh call."""
        gh_calls = []

        def gh_check(cwd, kind, ident):
            gh_calls.append(ident)
            return "terminal"

        cmd = self._poll_loop_cmd("run", "999")
        def ps():
            return [
                    (100, 3600, 0, cmd),
                    (101, 3600, 0, cmd),
                ]
        def stat(pid):
            return self._orphan_stat(pid)

        orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: None,
            verify_fn=lambda pid: cmd,
            stat_reader=stat, cmdline_reader=lambda pid: "",
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=gh_check)

        self.assertEqual(len(gh_calls), 1)

    def test_dry_run_kills_nothing(self):
        cmd = self._poll_loop_cmd("run", "123")
        kills = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._orphan_stat(pid)

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            verify_fn=lambda pid: cmd, stat_reader=stat,
            cmdline_reader=lambda pid: "",
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=lambda *a: "terminal",
            dry_run=True)

        self.assertEqual(kills, [])
        self.assertTrue(any("DRY-RUN" in ln for ln in logs))

    def test_toctou_reused_pid(self):
        """pid reused by a non-poll-loop → no kill."""
        cmd = self._poll_loop_cmd("run", "123")
        kills = []
        def ps():
            return [(100, 3600, 0, cmd)]
        def stat(pid):
            return self._orphan_stat(pid)

        logs = orphan_poll_reaper(
            ps_fetch=ps, kill_fn=lambda pid: kills.append(pid),
            verify_fn=lambda pid: "python3 app.py",
            stat_reader=stat, cmdline_reader=lambda pid: "",
            cwd_reader=lambda pid: "/repo",
            gh_check_fn=lambda *a: "terminal")

        self.assertEqual(kills, [])
        self.assertTrue(any("reused" in ln for ln in logs))

    def test_ps_error_kills_nothing(self):
        def bad_ps():
            raise OSError("fail")
        logs = orphan_poll_reaper(ps_fetch=bad_ps)
        self.assertTrue(any("ps error" in ln for ln in logs))


if __name__ == "__main__":
    unittest.main()
