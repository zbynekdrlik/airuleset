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
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

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
                                 env=hermetic_hook_env(self),
                                 capture_output=True, text=True)
            self.assertIn("task-XYZ1013", out.stdout,
                          "the launched task must be detected past the torn line")
            self.assertIn('"decision": "block"', out.stdout,
                          "an in-flight task must still block the subagent stop")


if __name__ == "__main__":
    unittest.main()
