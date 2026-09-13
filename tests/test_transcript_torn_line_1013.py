"""#1013 — a single torn transcript line must not disable a whole-file jq reader.

`block-main-implementation.sh` read the WHOLE transcript with
`jq -r '<filter>' "$TRANSCRIPT"`; jq aborts at the first unparsable record (two
JSON objects glued onto one physical line, the live controller line 78272), rc
!= 0 -> GOAL_UNKNOWN=1 -> the session fails CLOSED ("transcript read failed —
failing closed") for the rest of its life. The fix reads per line
(`jq -R -r 'fromjson? // empty | <filter>'`) so a torn record is skipped while
every other line still contributes; the GENUINE jq-failure fail-closed path is
preserved.

`subagent-stop-check-bg-work.sh` reads the transcript in PYTHON per line
(`json.loads` in try/except), so it NEVER had the whole-file-jq-abort bug: a torn
record ELSEWHERE in the file is skipped and every other line still contributes
(the lock test pins exactly that). The residual — a torn line that itself CARRIES
the launch record is missed — is NOT a #1013 regression (it predates this fix and
is inherent to per-line parsing) and is covered in production by the synchronous
LEDGER, the primary torn-immune ownership source (the transcript is secondary).
So no code change is warranted there (STEP-0 re-scope, see #1013 design comment).

RED (fails before the fix): a torn line makes block-main fail closed.
"""
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BLOCK_HOOK = REPO / "hooks" / "block-main-implementation.sh"
BG_HOOK = REPO / "hooks" / "subagent-stop-check-bg-work.sh"


def _goal_set():
    return json.dumps({"type": "user", "message": {"role": "user",
        "content": "<local-command-stdout>Goal set: do the backlog"
                   "</local-command-stdout>"}})


def _assistant(model="claude-opus-4-8"):
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
        "model": model, "content": [{"type": "text", "text": "working"}]}})


def _torn():
    # two records glued onto ONE physical line: a record cut mid-content with
    # the next record glued on (the reported controller line 78272 shape).
    cut = '{"type":"user","message":{"role":"user","content":["text ...Fragments OK.'
    nxt = json.dumps({"parentUuid": "cfba30a3", "type": "assistant",
                      "message": {"role": "assistant", "model": "claude-opus-4-8",
                                  "content": [{"type": "text", "text": "x"}]}})
    return cut + nxt


class TornTranscriptBlockMain1013(unittest.TestCase):
    def _drive(self, lines, command, jqdir=None):
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text("\n".join(lines) + "\n")
            payload = {"session_id": "t-1013-" + uuid.uuid4().hex[:8],
                       "hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": command},
                       "transcript_path": tp}
            env = dict(os.environ)
            if jqdir:
                env["PATH"] = jqdir + os.pathsep + env.get("PATH", "")
            return subprocess.run(["bash", str(BLOCK_HOOK)],
                                  input=json.dumps(payload), env=env,
                                  capture_output=True, text=True)

    def test_torn_line_with_goal_reads_armed_not_failclosed(self):
        # RED: today the torn line aborts jq -> GOAL_UNKNOWN -> "transcript read
        # failed"; after the fix GOAL_ARMED is read and the ordinary classifier
        # runs (a bulk read under an armed goal blocks with the ARMED reason).
        out = self._drive([_goal_set(), _assistant(), _torn(), _assistant()],
                          "grep -rn 'TODO' .")
        self.assertNotIn(
            "transcript read failed", out.stderr,
            "a single torn line must not make the goal read fail closed")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("ARMED /goal", out.stderr,
                      "the goal-armed state must be read past the torn line")

    def test_torn_line_no_goal_allows_a_bulk_read(self):
        # RED: today the torn line aborts jq -> GOAL_UNKNOWN -> a bulk read is
        # blocked; after the fix the (correct) not-armed state is read and, with
        # no Fable/goal/away condition, the bulk read is allowed.
        out = self._drive([_assistant(), _torn(), _assistant()],
                          "grep -rn 'TODO' .")
        self.assertNotIn("transcript read failed", out.stderr, out.stderr)
        self.assertEqual(
            out.returncode, 0,
            "a non-armed non-Fable main must not be blocked by a torn line: %s"
            % out.stderr)

    def test_trailing_non_object_line_does_not_fail_closed(self):
        # review-2 🔵 lock: a top-level truthy NON-object valid-JSON line as the
        # LAST line (a bare `true`) must be dropped by `| objects`, NOT error jq
        # into fail-closed. The armed goal on the healthy lines is still read.
        out = self._drive([_goal_set(), _assistant(), "true"],
                          "grep -rn 'TODO' .")
        self.assertNotIn("transcript read failed", out.stderr, out.stderr)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("ARMED /goal", out.stderr,
                      "the goal must be read past a trailing non-object line")

    def test_genuine_jq_failure_still_fails_closed(self):
        # LOCK (passes before AND after): when jq genuinely CANNOT run the goal
        # read (a jq that fails on the .message.content filter), the hook must
        # STILL fail closed (GOAL_UNKNOWN) — the security fail-direction is
        # preserved, never turned fail-open by the torn-line fix.
        with TemporaryDirectory() as jqd:
            real = subprocess.run(["bash", "-c", "command -v jq"],
                                  capture_output=True, text=True).stdout.strip() \
                or "/usr/bin/jq"
            stub = Path(jqd) / "jq"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "    *'.message.content'*) exit 2 ;;\n"
                "esac\n"
                "exec %s \"$@\"\n" % real)
            stub.chmod(0o755)
            out = self._drive([_goal_set(), _assistant()],
                              "grep -rn 'TODO' .", jqdir=jqd)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("transcript read failed", out.stderr,
                          "a genuine jq failure must still fail closed")


class TornTranscriptBgWork1013(unittest.TestCase):
    def test_bg_work_scan_survives_a_torn_line(self):
        # LOCK (already-tolerant, no code change — STEP-0 re-scope): the bg-work
        # hook reads the transcript in PYTHON per line, so a torn record
        # ELSEWHERE is skipped and a launched-but-unterminated task on a healthy
        # line is still detected -> the subagent stop is still blocked. (A torn
        # line that itself carries the launch record would be missed, but that
        # is inherent to per-line parsing, not a #1013 regression, and the
        # synchronous ledger is the primary torn-immune ownership source.)
        launch = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result",
             "content": "Command running in background with ID: task-XYZ1013"}]}})
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "agent.jsonl")
            Path(tp).write_text("\n".join(
                [_goal_set(), launch, _torn(), _assistant()]) + "\n")
            payload = {"session_id": "t-1013-bg-" + uuid.uuid4().hex[:8],
                       "agent_id": "aWORKER1", "hook_event_name": "SubagentStop",
                       "agent_transcript_path": tp}
            out = subprocess.run(["bash", str(BG_HOOK)],
                                 input=json.dumps(payload),
                                 env=dict(os.environ),
                                 capture_output=True, text=True)
            self.assertIn("task-XYZ1013", out.stdout,
                          "the launched task must be detected past the torn line")
            self.assertIn('"decision": "block"', out.stdout,
                          "an in-flight task must still block the subagent stop")


if __name__ == "__main__":
    unittest.main()
