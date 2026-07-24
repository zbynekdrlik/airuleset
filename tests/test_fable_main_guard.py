"""Fable-as-advisor enforcement (airuleset #32, 2026-07-24).

The user runs 3 full Max subscriptions and still hits limits. Live case: a
presenter session with the main model set to Fable IMPLEMENTED a whole issue
itself (no subagents) — a Fable main re-reads the full conversation every
turn, so every edit/test/build of an implementation loop burns Fable prices.
The ADVISOR-shape rule exists in prose (model-awareness.md) and the session
violated it anyway ("mal som kontext v hlave") — the exact failure class that
gets a HOOK:

- `block-fable-main-implementation.sh` (PreToolUse Edit|Write): a MAIN
  session (no agent_id) whose CURRENT model is claude-fable-* writing MORE
  than AIRULESET_FABLE_EDIT_MAX (~800 chars) in one Edit/Write is blocked
  with the delegation instruction. Small surgical edits pass (oversight is
  legitimate); subagents pass (execution belongs there); non-Fable mains
  pass; unknown model fails open; deliberate bypass = touch
  /tmp/airuleset-fable-exec-ok-<session_id> (logged).
- `fable-advisor` skill: the one-command ADVISOR path for a cheap master —
  fable-gate → tight digest → ONE Agent dispatch model:fable effort:xhigh →
  decision back; execution goes to a Sonnet worker.
"""

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "block-fable-main-implementation.sh"

BIG = "x = 1\n" * 300          # way over any threshold
SMALL = "x = 1\n"


def transcript(model="claude-fable-5"):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                                                "content": "do the thing"}}),
        json.dumps({"type": "assistant", "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "working"}]}}),
    ]
    return "\n".join(lines) + "\n"


class FableMainGuard(unittest.TestCase):
    def _run(self, tool="Edit", content=BIG, model="claude-fable-5",
             agent_id=None, transcript_text=None, sid=None, bypass=False):
        sid = sid or ("t-fg-" + uuid.uuid4().hex[:8])
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript_text
                                if transcript_text is not None
                                else transcript(model))
            if bypass:
                Path("/tmp/airuleset-fable-exec-ok-%s" % sid).write_text("")
                self.addCleanup(
                    lambda: Path("/tmp/airuleset-fable-exec-ok-%s"
                                 % sid).unlink(missing_ok=True))
            ti = ({"file_path": "/x/app.py", "old_string": "a",
                   "new_string": content} if tool == "Edit"
                  else {"file_path": "/x/app.py", "content": content})
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": ti,
                       "transcript_path": tp}
            if agent_id:
                payload["agent_id"] = agent_id
            return subprocess.run(["bash", str(HOOK)],
                                  input=json.dumps(payload),
                                  capture_output=True, text=True)

    def test_fable_main_big_edit_blocked(self):
        out = self._run()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("Fable", out.stderr)
        self.assertIn("worker", out.stderr)

    def test_fable_main_big_write_blocked(self):
        out = self._run(tool="Write")
        self.assertEqual(out.returncode, 2)

    def test_small_surgical_edit_passes(self):
        out = self._run(content=SMALL)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_subagent_context_passes(self):
        # execution BELONGS in workers — a subagent edit is never blocked
        out = self._run(agent_id="aWORKER1")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_non_fable_main_passes(self):
        out = self._run(model="claude-opus-4-8")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_model_switch_mid_session_uses_latest(self):
        # /model can change mid-session — the LAST assistant entry decides
        tx = transcript("claude-fable-5") + transcript("claude-opus-4-8")
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_unknown_model_fails_open(self):
        out = self._run(transcript_text=json.dumps(
            {"type": "user", "message": {"role": "user", "content": "x"}})
            + "\n")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_bypass_file_allows_and_is_deliberate(self):
        out = self._run(bypass=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_threshold_env_tunable(self):
        env = dict(os.environ, AIRULESET_FABLE_EDIT_MAX="100000")
        sid = "t-fg-env-" + uuid.uuid4().hex[:6]
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript())
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": "Write",
                       "tool_input": {"file_path": "/x/a.py", "content": BIG},
                       "transcript_path": tp}
            out = subprocess.run(["bash", str(HOOK)],
                                 input=json.dumps(payload), env=env,
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


class TestWiringAndSkill(unittest.TestCase):
    def test_hook_exists_and_wired_for_edit_and_write(self):
        self.assertTrue(HOOK.exists())
        self.assertTrue(os.access(HOOK, os.X_OK))
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        for tool in ("Edit", "Write"):
            ms = json.dumps([mm for mm in cfg["hooks"]["PreToolUse"]
                             if mm.get("matcher") == tool])
            self.assertIn("block-fable-main-implementation.sh", ms,
                          "guard missing for PreToolUse(%s)" % tool)

    def test_fable_advisor_skill_exists_and_registered(self):
        sk = REPO / "skills" / "fable-advisor" / "SKILL.md"
        self.assertTrue(sk.exists())
        txt = sk.read_text()
        for needle in ("fable-gate", "digest", "xhigh", "sonnet"):
            self.assertIn(needle, txt, needle)
        self.assertIn("fable-advisor", airuleset.SKILL_NAMES)

    def test_model_awareness_points_at_the_enforcement(self):
        txt = (REPO / "modules" / "core" / "model-awareness.md").read_text()
        self.assertIn("block-fable-main-implementation.sh", txt)
        self.assertIn("fable-advisor", txt)


if __name__ == "__main__":
    unittest.main()
