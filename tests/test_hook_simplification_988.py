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

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

REPO = Path(airuleset.__file__).resolve().parent
LIB_LOG = REPO / "hooks" / "lib_hook_block_log.sh"


def _log_via_lib(hook, snippet, env):
    """Source the shared block-log lib and log one block (#988(g))."""
    subprocess.run(["bash", "-c", 'source "$1"; log_hook_block "$2" "$3"',
                    "_", str(LIB_LOG), hook, snippet],
                   env=env, capture_output=True, text=True, check=True)
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
            capture_output=True, text=True, env=hermetic_hook_env(self)
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




class HookBlockLogRedact988g(unittest.TestCase):
    """#988(g): secret commands are redacted in hook-blocks.log (driven through
    the shared lib directly since #1137 removed the main-agent guard that used
    to be the example hook)."""

    def test_secret_redacted(self):
        with TemporaryDirectory() as d:
            log_file = Path(d) / "hook-blocks.log"
            env = hermetic_hook_env(self, AIRULESET_HOOK_BLOCK_LOG=str(log_file))
            _log_via_lib("t-hook", "vault secret show mypassword", env)
            content = log_file.read_text()
            self.assertIn("t-hook", content)
            self.assertNotIn("mypassword", content)
            self.assertIn("<redacted>", content)


# G4. Test-driven blocks must not pollute the production log (#988 fix-forward)
# ---------------------------------------------------------------------------

class TestPytestSuppression988(unittest.TestCase):
    """#988 fix-forward: under PYTEST_CURRENT_TEST with no explicit
    AIRULESET_HOOK_BLOCK_LOG the default ~/.claude/hook-blocks.log is NOT
    written; an explicit override still is (driven through the shared lib)."""

    def test_default_log_not_written_under_pytest(self):
        env = hermetic_hook_env(self, PYTEST_CURRENT_TEST="x::y (call)")
        env.pop("AIRULESET_HOOK_BLOCK_LOG", None)
        env.pop("AIRULESET_HOOK_BLOCKS_LOG", None)
        _log_via_lib("t-hook", "some-unknown-cmd arg", env)
        self.assertFalse(
            (Path(env["HOME"]) / ".claude" / "hook-blocks.log").exists())

    def test_explicit_override_writes_despite_pytest(self):
        with TemporaryDirectory() as d:
            explicit_log = Path(d) / "explicit-blocks.log"
            env = hermetic_hook_env(self, PYTEST_CURRENT_TEST="x::y (call)",
                                    AIRULESET_HOOK_BLOCK_LOG=str(explicit_log))
            _log_via_lib("t-hook", "some-unknown-cmd arg", env)
            self.assertIn("some-unknown-cmd", explicit_log.read_text())


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
