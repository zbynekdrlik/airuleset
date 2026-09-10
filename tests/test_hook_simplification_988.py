"""Hook simplification tests — airuleset #988.

Measured blocks on the gk box (2026-09-10): read-only coordination commands
blocked by the per-dispatch counter (#80), completed-run status reads blocked
as polls (#210), and compound heredoc+issue-create commands blocked before the
body file is written. Each fixture here reproduces the exact block from the
ticket and must PASS after the fix.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
HOOK_BMI = REPO / "hooks" / "block-main-implementation.sh"
HOOK_CPR = REPO / "hooks" / "block-ci-poll-repeat.sh"
HOOK_UGI = REPO / "hooks" / "block-ungated-issue-filing.sh"


def goal_armed_transcript(model="claude-opus-4-8"):
    return (
        '{"type":"assistant","message":{"content":"test","model":"%s"}}\n'
        '{"type":"user","message":{"content":"'
        '<local-command-stdout>Goal set: test goal</local-command-stdout>"}}\n'
    ) % model


# ---------------------------------------------------------------------------
# A. block-main-implementation.sh: coordination commands must not be blocked
#    by the per-dispatch counter (#80)
# ---------------------------------------------------------------------------

class CoordinationExemptFromCounter988(unittest.TestCase):
    """#988 items 1+2+4: the per-dispatch counter blocks ALL Bash calls
    regardless of classification. Pure coordination commands (read-only gh/git,
    narrow single-file reads, git worktree ops) must be EXEMPT from the
    counter, even when the count exceeds the cap."""

    def _run(self, sid, command, n=3, armed=True):
        env = dict(os.environ)
        env["AIRULESET_MAIN_BASH_PER_DISPATCH"] = str(n)
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(goal_armed_transcript())
            # Pre-seed counter to OVER the cap
            run_file = Path("/tmp/airuleset-main-bash-run-%s" % sid)
            run_file.write_text(str(n + 1))
            self.addCleanup(lambda: run_file.unlink(missing_ok=True))
            # Presence marker
            presence = Path("/tmp/airuleset-presence-%s" % sid)
            presence.touch()
            self.addCleanup(lambda: presence.unlink(missing_ok=True))
            payload = {
                "session_id": sid,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "transcript_path": tp,
            }
            return subprocess.run(
                ["bash", str(HOOK_BMI)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )

    def _sid(self, tag):
        sid = "t988-coord-%s-%s" % (tag, os.getpid())
        self.addCleanup(lambda: Path(
            "/tmp/airuleset-main-bash-run-%s" % sid).unlink(missing_ok=True))
        return sid

    # --- Ticket item 1: gh pr view at counter cap ---
    def test_gh_pr_view_passes_at_counter_cap(self):
        """gh pr view --json mergeable must never be blocked by the counter."""
        sid = self._sid("a1")
        out = self._run(sid, "gh pr view 42 --json mergeable,mergeableState")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_gh_issue_view_passes_at_counter_cap(self):
        sid = self._sid("a2")
        out = self._run(sid, "gh issue view 42")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_gh_run_view_passes_at_counter_cap(self):
        sid = self._sid("a3")
        out = self._run(sid, "gh run view 1234567890")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_status_passes_at_counter_cap(self):
        sid = self._sid("a4")
        out = self._run(sid, "git status")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_log_oneline_passes_at_counter_cap(self):
        sid = self._sid("a5")
        out = self._run(sid, "git log --oneline -5")
        self.assertEqual(out.returncode, 0, out.stderr)

    # --- Ticket item 2a: sed -i on work-products ---
    def test_sed_i_on_work_products_passes_at_counter_cap(self):
        """sed -i on ~/.claude/work-products/ is coordination, not bulk."""
        sid = self._sid("b2a")
        out = self._run(sid, "sed -i 's/old/new/g' ~/.claude/work-products/draft.md")
        self.assertEqual(out.returncode, 0, out.stderr)

    # --- Ticket item 2b: grep -n on one file ---
    def test_grep_n_single_file_passes_at_counter_cap(self):
        """grep -n on one file is a narrow read, not bulk."""
        sid = self._sid("b2b")
        out = self._run(sid, "grep -n deploy .github/workflows/ci.yml")
        self.assertEqual(out.returncode, 0, out.stderr)

    # --- Ticket item 2c: git worktree ---
    def test_git_worktree_remove_passes_at_counter_cap(self):
        """git worktree ops are coordination."""
        sid = self._sid("b2c")
        out = self._run(sid, "git worktree remove .claude/worktrees/agent-dead123")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_worktree_list_passes_at_counter_cap(self):
        sid = self._sid("b2c2")
        out = self._run(sid, "git worktree list")
        self.assertEqual(out.returncode, 0, out.stderr)

    # --- Ticket item 4: gh run rerun ---
    def test_gh_run_rerun_passes_at_counter_cap(self):
        """gh run rerun is coordination."""
        sid = self._sid("b4")
        out = self._run(sid, 'R=1234567890; gh run rerun "$R"')
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_gh_run_rerun_literal_passes(self):
        sid = self._sid("b4b")
        out = self._run(sid, "gh run rerun 1234567890")
        self.assertEqual(out.returncode, 0, out.stderr)

    # --- NEGATIVE: bulk commands must STILL be blocked ---
    def test_grep_recursive_still_blocked_at_any_count(self):
        """Bulk grep -rn stays blocked by classifier, not just counter."""
        sid = self._sid("neg1")
        out = self._run(sid, "grep -rn 'TODO' .")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_ambiguous_commands_still_counted(self):
        """An ambiguous command (not in allowed or blocked) still counts
        toward the per-dispatch counter."""
        sid = self._sid("neg2")
        out = self._run(sid, "some-unknown-command --flag arg")
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("DISPATCH", out.stderr)

    def test_pipe_with_bulk_tail_still_counted(self):
        """A piped command (gh pr view | grep -rn) is NOT pure coordination
        — the pipe tail can be a bulk read (review finding MEDIUM)."""
        sid = self._sid("neg3")
        out = self._run(sid, "gh pr view 1 --json body | grep -rn TODO .")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_sed_i_on_traversed_work_products_path_still_counted(self):
        """sed -i on a path that traverses OUT of work-products via ..
        must still be counted (review finding MEDIUM — security)."""
        sid = self._sid("neg4")
        out = self._run(
            sid,
            "sed -i 's/x/y/' ~/.claude/work-products/../../devel/x.py",
        )
        self.assertEqual(out.returncode, 2, out.stderr)


# ---------------------------------------------------------------------------
# C. block-ci-poll-repeat.sh: completed runs are not polls
# ---------------------------------------------------------------------------

class CompletedRunNotAPoll988(unittest.TestCase):
    """#988 item 3: a run whose status is already `completed` should not be
    counted as a oneshot poll. The hook should check the run status and pass."""

    def _run_hook(self, sid, run_id, oneshot_count=3, env_extra=None):
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_ONESHOT_FREE"] = "2"
        if env_extra:
            env.update(env_extra)
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text('{"type":"assistant","message":{"content":"test"}}\n')
            # Pre-seed oneshot counter above threshold
            key = "run-%s" % run_id
            oneshot_file = Path("/tmp/airuleset-cipoll-oneshot-%s-%s" % (sid, key))
            oneshot_file.write_text(str(oneshot_count))
            self.addCleanup(lambda: oneshot_file.unlink(missing_ok=True))
            # Clean up blocked file too
            blocked_file = Path("/tmp/airuleset-cipoll-blocked-%s-%s" % (sid, key))
            self.addCleanup(lambda: blocked_file.unlink(missing_ok=True))
            payload = {
                "session_id": sid,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": "gh run view %s --json status,conclusion" % run_id,
                },
                "transcript_path": tp,
            }
            return subprocess.run(
                ["bash", str(HOOK_CPR)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )

    def test_completed_run_passes_despite_high_oneshot_count(self):
        """A run that is already completed (marker file exists) should not
        be blocked as a poll, even when the oneshot counter exceeds the
        free threshold."""
        sid = "t988-cpoll-%d" % os.getpid()
        run_id = "1234567890"
        with TemporaryDirectory() as marker_dir:
            # Create the completion marker BEFORE the hook runs
            marker = Path(marker_dir) / ("airuleset-cipoll-completed-%s" % run_id)
            marker.touch()
            out = self._run_hook(
                sid, run_id,
                env_extra={"AIRULESET_CIPOLL_COMPLETED_MARKER_DIR": marker_dir},
            )
            self.assertEqual(out.returncode, 0,
                             "completed run should not be blocked as a poll: "
                             + out.stderr)


# ---------------------------------------------------------------------------
# E. block-ungated-issue-filing.sh: heredoc + -F compound command
# ---------------------------------------------------------------------------

class HeredocBodyFile988(unittest.TestCase):
    """#988 item 5: when -F/--body-file path doesn't exist yet but a heredoc
    in the same compound command creates it, the hook should say 'write the
    body file in a SEPARATE command' rather than evaluating the missing file
    as a Scope-gate violation."""

    def _run_hook(self, command):
        payload = {
            "session_id": "t988-ugif-%d" % os.getpid(),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        return subprocess.run(
            ["bash", str(HOOK_UGI)],
            input=json.dumps(payload),
            capture_output=True, text=True,
        )

    def test_heredoc_then_issue_create_blocked_with_separate_command_message(self):
        """A compound command that creates the body file via a redirect and
        then references it with -F should block with a clear 'write the body
        file in a SEPARATE command' message, not an opaque Scope-gate
        violation or 'body file not readable'."""
        # Use printf > file (NOT cat > file, which CATFILE_RE already handles).
        # This is the shape that triggers the real-world "body file not
        # readable" block — the file doesn't exist at hook time and the
        # heredoc wasn't captured.
        cmd = (
            "printf '%s\\n' 'Scope-gate: user-request' 'Dedup-checked: none' "
            "'## Title' 'Body' > /tmp/body-988-nonexist.md && "
            "gh issue create -R zbynekdrlik/test -t 'Test' -F /tmp/body-988-nonexist.md"
        )
        out = self._run_hook(cmd)
        self.assertEqual(out.returncode, 2, "should still block")
        stderr = out.stderr
        self.assertIn("SEPARATE", stderr,
                      "should say 'write body file in a SEPARATE command': " + stderr)


# ---------------------------------------------------------------------------
# G. Measurement: hook blocks logged to ~/.claude/hook-blocks.log
# ---------------------------------------------------------------------------

class HookBlockLogging988(unittest.TestCase):
    """#988 item g: every BLOCKED decision appends to ~/.claude/hook-blocks.log."""

    def test_blocked_command_logged_to_hook_blocks(self):
        """A blocked command should append a line to hook-blocks.log."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            env["AIRULESET_MAIN_BASH_PER_DISPATCH"] = "3"
            sid = "t988-log-%d" % os.getpid()
            tp_dir = Path(d) / "transcript"
            tp_dir.mkdir()
            tp = tp_dir / "sess.jsonl"
            tp.write_text(goal_armed_transcript())
            # Presence marker
            presence = Path("/tmp/airuleset-presence-%s" % sid)
            presence.touch()
            run_file = Path("/tmp/airuleset-main-bash-run-%s" % sid)
            run_file.write_text("4")  # over cap
            try:
                payload = {
                    "session_id": sid,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "some-unknown-cmd arg"},
                    "transcript_path": str(tp),
                }
                subprocess.run(
                    ["bash", str(HOOK_BMI)],
                    input=json.dumps(payload), env=env,
                    capture_output=True, text=True,
                )
                self.assertTrue(log_file.exists(),
                                "hook-blocks.log should be created on block")
                content = log_file.read_text()
                self.assertIn("block-main-implementation", content)
                self.assertIn("some-unknown-cmd", content)
            finally:
                presence.unlink(missing_ok=True)
                run_file.unlink(missing_ok=True)


# G2. Measurement: ci-poll-repeat and ungated-issue-filing also log
# ---------------------------------------------------------------------------

class HookBlockLogCiPoll988g(unittest.TestCase):
    """#988(g): block-ci-poll-repeat logs to hook-blocks.log on block."""

    def test_cipoll_block_logs(self):
        """A blocked CI loop should append to hook-blocks.log."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            state_dir = Path(d) / "state"
            state_dir.mkdir()
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            env["AIRULESET_CIPOLL_STATE_DIR"] = str(state_dir)
            sid = "t988g-cpr-%d" % os.getpid()
            run_id = "12345678"
            key = "run-%s" % run_id
            # Pre-seed: first loop already ran (second loop blocks)
            first_file = state_dir / ("airuleset-cipoll-first-%s-%s" % (sid, key))
            first_file.write_text("1")
            payload = {
                "session_id": sid,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": "while true; do gh run view %s --json status; sleep 60; done" % run_id,
                    "run_in_background": False,
                },
            }
            r = subprocess.run(
                ["bash", str(HOOK_CPR)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 2,
                             "should block: " + r.stderr[:500])
            self.assertTrue(log_file.exists(),
                            "hook-blocks.log should be created on ci-poll block")
            content = log_file.read_text()
            self.assertIn("block-ci-poll-repeat", content)

    def test_cipoll_pass_no_log(self):
        """A passing ci-poll (no loop match) should NOT log."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            payload = {
                "session_id": "t988g-cpr-pass-%d" % os.getpid(),
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
            }
            subprocess.run(
                ["bash", str(HOOK_CPR)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )
            self.assertFalse(log_file.exists(),
                             "hook-blocks.log should NOT be created on pass")


class HookBlockLogUGI988g(unittest.TestCase):
    """#988(g): block-ungated-issue-filing logs to hook-blocks.log on block."""

    def test_worker_block_logs(self):
        """A worker filing attempt should block AND log."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            payload = {
                "session_id": "t988g-ugi-%d" % os.getpid(),
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "agent_id": "agent-worker-988",
                "tool_input": {"command": "gh issue create --title test --body test"},
            }
            r = subprocess.run(
                ["bash", str(HOOK_UGI)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 2)
            self.assertTrue(log_file.exists(),
                            "hook-blocks.log should be created on worker block")
            content = log_file.read_text()
            self.assertIn("block-ungated-issue-filing", content)

    def test_pass_no_log(self):
        """A non-filing command should NOT log."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            payload = {
                "session_id": "t988g-ugi-pass-%d" % os.getpid(),
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
            }
            subprocess.run(
                ["bash", str(HOOK_UGI)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )
            self.assertFalse(log_file.exists(),
                             "hook-blocks.log should NOT be created on pass")


class HookBlockLogBMI988g(unittest.TestCase):
    """#988(g): block-main-implementation no-log-on-pass."""

    def test_pass_no_log(self):
        """A passing command (subagent) should NOT log."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            payload = {
                "session_id": "t988g-bmi-pass-%d" % os.getpid(),
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "agent_id": "worker-1",
                "tool_input": {"command": "echo hello"},
            }
            subprocess.run(
                ["bash", str(HOOK_BMI)],
                input=json.dumps(payload), env=env,
                capture_output=True, text=True,
            )
            self.assertFalse(log_file.exists(),
                             "hook-blocks.log should NOT be created on pass")


class HookBlockLogRedact988g(unittest.TestCase):
    """#988(g): secret commands are redacted in hook-blocks.log."""

    def test_secret_redacted(self):
        """A command containing 'secret' should be redacted."""
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(log_file)
            env["AIRULESET_MAIN_BASH_PER_DISPATCH"] = "3"
            sid = "t988g-redact-%d" % os.getpid()
            tp_dir = Path(d) / "transcript"
            tp_dir.mkdir()
            tp = tp_dir / "sess.jsonl"
            tp.write_text(goal_armed_transcript())
            presence = Path("/tmp/airuleset-presence-%s" % sid)
            presence.touch()
            run_file = Path("/tmp/airuleset-main-bash-run-%s" % sid)
            run_file.write_text("4")
            try:
                payload = {
                    "session_id": sid,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "vault secret show mypassword"},
                    "transcript_path": str(tp),
                }
                r = subprocess.run(
                    ["bash", str(HOOK_BMI)],
                    input=json.dumps(payload), env=env,
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 2)
                self.assertTrue(log_file.exists(),
                                "hook-blocks.log should be created on block")
                content = log_file.read_text()
                self.assertNotIn("mypassword", content)
                self.assertIn("<redacted>", content)
            finally:
                presence.unlink(missing_ok=True)
                run_file.unlink(missing_ok=True)


# G4. Test-driven blocks must not pollute the production log (#988 fix-forward)
# ---------------------------------------------------------------------------

class TestPytestSuppression988(unittest.TestCase):
    """#988 fix-forward: when PYTEST_CURRENT_TEST is set (as it always is under
    pytest) and no explicit AIRULESET_HOOK_BLOCK_LOG overrides, the default
    ~/.claude/hook-blocks.log must NOT be written — otherwise the test suite
    inflates the production metric."""

    def test_default_log_not_written_under_pytest(self):
        """A block under PYTEST_CURRENT_TEST with no explicit override must
        NOT write to the default log file."""
        with TemporaryDirectory() as d:
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            claude_dir = fake_home / ".claude"
            claude_dir.mkdir()
            default_log = claude_dir / "hook-blocks.log"
            env = dict(os.environ)
            env["HOME"] = str(fake_home)
            env["PYTEST_CURRENT_TEST"] = "tests/test_hook_simplification_988.py::TestPytestSuppression988::test_default_log_not_written_under_pytest (call)"
            env["AIRULESET_MAIN_BASH_PER_DISPATCH"] = "3"
            # Remove any explicit override so the lib uses the default
            env.pop("AIRULESET_HOOK_BLOCK_LOG", None)
            env.pop("AIRULESET_HOOK_BLOCKS_LOG", None)  # legacy name
            sid = "t988-pytest-suppress-%d" % os.getpid()
            tp_dir = Path(d) / "transcript"
            tp_dir.mkdir()
            tp = tp_dir / "sess.jsonl"
            tp.write_text(goal_armed_transcript())
            presence = Path("/tmp/airuleset-presence-%s" % sid)
            presence.touch()
            run_file = Path("/tmp/airuleset-main-bash-run-%s" % sid)
            run_file.write_text("4")  # over cap → will block
            try:
                payload = {
                    "session_id": sid,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "some-unknown-cmd arg"},
                    "transcript_path": str(tp),
                }
                r = subprocess.run(
                    ["bash", str(HOOK_BMI)],
                    input=json.dumps(payload), env=env,
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 2, "should block")
                self.assertFalse(default_log.exists(),
                                 "default log must NOT be written when "
                                 "PYTEST_CURRENT_TEST is set and no "
                                 "AIRULESET_HOOK_BLOCK_LOG overrides")
            finally:
                presence.unlink(missing_ok=True)
                run_file.unlink(missing_ok=True)

    def test_explicit_override_writes_despite_pytest(self):
        """When AIRULESET_HOOK_BLOCK_LOG=<path> is set, the log IS written
        there even under PYTEST_CURRENT_TEST — so tests that want to assert
        the log mechanism still work."""
        with TemporaryDirectory() as d:
            explicit_log = Path(d) / "explicit-blocks.log"
            env = dict(os.environ)
            env["AIRULESET_HOOK_BLOCK_LOG"] = str(explicit_log)
            env["PYTEST_CURRENT_TEST"] = "tests/test_hook_simplification_988.py::TestPytestSuppression988::test_explicit_override_writes_despite_pytest (call)"
            env["AIRULESET_MAIN_BASH_PER_DISPATCH"] = "3"
            env.pop("AIRULESET_HOOK_BLOCKS_LOG", None)  # legacy name
            sid = "t988-pytest-explicit-%d" % os.getpid()
            tp_dir = Path(d) / "transcript"
            tp_dir.mkdir()
            tp = tp_dir / "sess.jsonl"
            tp.write_text(goal_armed_transcript())
            presence = Path("/tmp/airuleset-presence-%s" % sid)
            presence.touch()
            run_file = Path("/tmp/airuleset-main-bash-run-%s" % sid)
            run_file.write_text("4")
            try:
                payload = {
                    "session_id": sid,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "some-unknown-cmd arg"},
                    "transcript_path": str(tp),
                }
                subprocess.run(
                    ["bash", str(HOOK_BMI)],
                    input=json.dumps(payload), env=env,
                    capture_output=True, text=True,
                )
                self.assertTrue(explicit_log.exists(),
                                "explicit AIRULESET_HOOK_BLOCK_LOG path "
                                "must be written even under PYTEST_CURRENT_TEST")
                content = explicit_log.read_text()
                self.assertIn("block-main-implementation", content)
            finally:
                presence.unlink(missing_ok=True)
                run_file.unlink(missing_ok=True)


# G3. Status breakdown
# ---------------------------------------------------------------------------

class StatusBreakdown988g(unittest.TestCase):
    """#988(g): airuleset.py status shows per-hook breakdown."""

    def test_breakdown(self):
        """_print_hook_blocks_count should print per-hook breakdown."""
        from io import StringIO
        import contextlib

        with TemporaryDirectory() as d:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            lines = [
                "%s\tblock-main-implementation\tsome cmd\n" % now,
                "%s\tblock-main-implementation\tanother cmd\n" % now,
                "%s\tblock-ci-poll-repeat\tgh run view\n" % now,
                "%s\tblock-ungated-issue-filing\tgh issue create\n" % now,
            ]
            claude_dir = Path(d) / ".claude"
            claude_dir.mkdir()
            log_in_claude = claude_dir / "hook-blocks.log"
            log_in_claude.write_text("".join(lines))
            import unittest.mock as m
            buf = StringIO()
            with m.patch("airuleset.Path.home", return_value=Path(d)):
                with contextlib.redirect_stdout(buf):
                    airuleset._print_hook_blocks_count()
            output = buf.getvalue()
            self.assertIn("hook blocks (24 h): 4", output)
            self.assertIn("block-main-implementation 2", output)
            self.assertIn("block-ci-poll-repeat 1", output)
            self.assertIn("block-ungated-issue-filing 1", output)


if __name__ == "__main__":
    unittest.main()
