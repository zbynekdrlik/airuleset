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
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
HOOK_BMI = REPO / "hooks" / "block-main-implementation.sh"
HOOK_CPR = REPO / "hooks" / "block-ci-poll-repeat.sh"
HOOK_UGI = REPO / "hooks" / "block-ungated-issue-filing.sh"


def goal_armed_transcript(model="claude-opus-4-6"):
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
        """A run that is already completed should not be blocked as a poll,
        even when the oneshot counter exceeds the free threshold."""
        # This test requires gh CLI to be available and the run to exist.
        # We mock the gh call by setting a test env var that the hook reads.
        sid = "t988-cpoll-%d" % os.getpid()
        # The hook needs to query the run status. For testing, we use a
        # completion-marker file approach: the hook checks if a completion
        # marker exists for this run.
        out = self._run_hook(sid, "1234567890",
                             env_extra={"AIRULESET_CIPOLL_COMPLETED_MARKER_DIR": "/tmp"})
        # Create the completion marker
        marker = Path("/tmp/airuleset-cipoll-completed-1234567890")
        marker.touch()
        self.addCleanup(lambda: marker.unlink(missing_ok=True))
        # The fix should make this pass; without the fix it blocks
        out2 = self._run_hook(sid, "1234567890",
                              env_extra={"AIRULESET_CIPOLL_COMPLETED_MARKER_DIR": "/tmp"})
        # At least one of the runs should pass after the fix
        # For RED: we expect the current code to block (exit 2)
        self.assertEqual(out2.returncode, 0,
                         "completed run should not be blocked as a poll: " + out2.stderr)


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
        """A compound command that creates the body file via heredoc and then
        references it with -F should block with a clear 'write the body file
        in a SEPARATE command' message, not a Scope-gate violation."""
        cmd = (
            "cat > /tmp/body-988.md << 'EOF'\n"
            "Scope-gate: user-request\n"
            "## Title\n"
            "Body\n"
            "EOF\n"
            "gh issue create -R zbynekdrlik/test -t 'Test' -F /tmp/body-988.md"
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
            env["AIRULESET_HOOK_BLOCKS_LOG"] = str(log_file)
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


if __name__ == "__main__":
    unittest.main()
