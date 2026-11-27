"""#1402 (owner 27.11.2026): Dante names, grouping, renames and patching go
through dcontroller. The rule reaches a session the moment it works on Dante:
an owner/peer prompt naming Dante, a Bash command touching Dante (also inside a
quoted ssh command), or a written file naming Dante."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _hook_state_cleanup import MODULE_HOOK_HOME  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
MARK = "Dante Names and Patching Belong to dcontroller"


def _ctx(tool_name, tool_input, session):
    with tempfile.TemporaryDirectory() as tmp:
        payload = {"session_id": session, "tool_name": tool_name, "tool_input": tool_input,
                   "cwd": tmp}
        if tool_name == "UserPromptSubmit":
            payload = {"session_id": session, "prompt": tool_input["prompt"],
                       "hook_event_name": "UserPromptSubmit", "cwd": tmp}
        env = dict(os.environ, HOME=MODULE_HOOK_HOME, TMPDIR=tmp)
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), text=True,
                           capture_output=True, env=env, timeout=30)
    out = (r.stdout or "").strip()
    if not out:
        return ""
    return json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "")


class DanteRule(unittest.TestCase):

    def test_quoted_ssh_dante_command_injects(self):
        ctx = _ctx("Bash", {"command": "ssh alex-klavir 'netaudio channel rename 1 \"free 01\"'"},
                   "dante-bash-1")
        self.assertIn(MARK, ctx)

    def test_written_file_naming_dante_injects(self):
        ctx = _ctx("Write", {"file_path": "/tmp/x/config.toml",
                             "content": "[dante]\nchannel_1 = \"Hand1\"\n"}, "dante-write-1")
        self.assertIn(MARK, ctx)

    def test_prompt_naming_dante_injects(self):
        ctx = _ctx("UserPromptSubmit", {"prompt": "premenuj dante kanaly na mbc-snv"},
                   "dante-prompt-1")
        self.assertIn(MARK, ctx)

    def test_unrelated_command_stays_quiet(self):
        ctx = _ctx("Bash", {"command": "git status"}, "dante-quiet-1")
        self.assertNotIn(MARK, ctx)


if __name__ == "__main__":
    unittest.main()
