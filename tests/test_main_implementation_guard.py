"""MAIN-session implementation guard (airuleset #32, generalized by #54).

The user runs 3 full Max subscriptions and still hits limits. Live case 1
(#32, 2026-07-24): a presenter session with the main model set to Fable
IMPLEMENTED a whole issue itself (no subagents) — a Fable main re-reads the
full conversation every turn, so every edit/test/build of an implementation
loop burns Fable prices. Live case 2 (#54, 2026-07-25): david@subdev's Opus
MAIN session, with an ARMED /goal, did 354 direct Edits + 56 Writes
alongside 229 Agent dispatches — the model wasn't Fable, but the failure is
the same: main implements instead of dispatching a worker. The ADVISOR-shape
rule exists in prose (model-awareness.md) and BOTH sessions violated it
anyway — the exact failure class a HOOK enforces:

- `block-main-implementation.sh` (PreToolUse Edit|Write, renamed from
  `block-fable-main-implementation.sh`): a MAIN session (no agent_id)
  writing MORE than AIRULESET_FABLE_EDIT_MAX (~800 chars) in one Edit/Write
  is blocked with the delegation instruction whenever EITHER (a) its CURRENT
  model is claude-fable-*, OR (b) the session's TRANSCRIPT shows an ARMED
  /goal (the latest `<local-command-stdout>Goal set:` / `Goal cleared:`
  marker is a "set" with no later "cleared"). Small surgical edits pass
  (oversight is legitimate); subagents pass (execution belongs there); a
  plain non-Fable, non-goal-armed main passes (unchanged existing
  behavior); unknown model / no goal markers fails open; deliberate bypass
  = touch /tmp/airuleset-main-exec-ok-<session_id> (logged), with the
  original /tmp/airuleset-fable-exec-ok-<session_id> still honored for
  backward compatibility.
- `fable-advisor` skill: the one-command ADVISOR path for a cheap master —
  fable-gate → tight digest → ONE Agent dispatch model:fable effort:xhigh →
  decision back; execution goes to a Sonnet worker.

Goal-armed detection is INDEPENDENT of the Fable-model detection (#38's
stale-model-after-/model-switch caveat applies ONLY to the model path, never
to the goal-armed path — the goal-armed tests here never touch `MODEL`).
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
HOOK = REPO / "hooks" / "block-main-implementation.sh"

BIG = "x = 1\n" * 300          # way over any threshold
SMALL = "x = 1\n"


def _entry(role_type, content, **extra):
    d = {"type": role_type}
    d.update(extra)
    if role_type == "user":
        d["message"] = {"role": "user", "content": content}
    else:
        d["content"] = content
    return json.dumps(d)


def transcript(model="claude-fable-5"):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                                                "content": "do the thing"}}),
        json.dumps({"type": "assistant", "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "working"}]}}),
    ]
    return "\n".join(lines) + "\n"


def goal_set_line(text="do the whole backlog"):
    # exactly how Claude Code itself writes it: a plain "user" entry whose
    # string content is the <local-command-stdout> the /goal command prints.
    return _entry("user",
                  "<local-command-stdout>Goal set: %s</local-command-stdout>" % text)


def goal_cleared_line(text="do the whole backlog"):
    # observed shape: a "system"/"local_command" entry, content at top level.
    return _entry("system",
                  "<local-command-stdout>Goal cleared: %s</local-command-stdout>" % text,
                  subtype="local_command")


def nested_foreign_goal_line(word="set"):
    # a tool_result whose (array) content happens to QUOTE another session's
    # goal marker text — must NEVER be mistaken for THIS session's own state.
    return json.dumps({"type": "user", "message": {"content": [
        {"tool_use_id": "t1", "type": "tool_result",
         "content": "<local-command-stdout>Goal %s: some other repo's loop"
                    "</local-command-stdout>" % word}]}})


def goal_armed_transcript(model="claude-opus-4-8"):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                                                "content": "/autopilot"}}),
        goal_set_line(),
        json.dumps({"type": "assistant", "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "working the backlog"}]}}),
    ]
    return "\n".join(lines) + "\n"


class MainImplementationGuard(unittest.TestCase):
    def _run(self, tool="Edit", content=BIG, model="claude-fable-5",
             agent_id=None, transcript_text=None, sid=None, bypass=None):
        sid = sid or ("t-mg-" + uuid.uuid4().hex[:8])
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript_text
                                if transcript_text is not None
                                else transcript(model))
            if bypass:
                marker = ("/tmp/airuleset-main-exec-ok-%s" % sid if bypass == "new"
                          else "/tmp/airuleset-fable-exec-ok-%s" % sid)
                Path(marker).write_text("")
                self.addCleanup(lambda: Path(marker).unlink(missing_ok=True))
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

    # ---- existing Fable-model behavior (#32) — unchanged ----

    def test_fable_main_big_edit_blocked(self):
        out = self._run()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("FABLE", out.stderr)
        self.assertIn("worker", out.stderr)

    def test_fable_main_big_write_blocked(self):
        out = self._run(tool="Write")
        self.assertEqual(out.returncode, 2)

    def test_small_surgical_edit_passes(self):
        out = self._run(content=SMALL)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_subagent_context_passes(self):
        # execution BELONGS in workers — a subagent edit is never blocked,
        # even with an armed goal in its (irrelevant) transcript
        out = self._run(agent_id="aWORKER1",
                        transcript_text=goal_armed_transcript())
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
        out = self._run(bypass="legacy")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_threshold_env_tunable(self):
        env = dict(os.environ, AIRULESET_FABLE_EDIT_MAX="100000")
        sid = "t-mg-env-" + uuid.uuid4().hex[:6]
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

    # ---- new: goal-armed generalization (#54) ----

    def test_goal_armed_opus_main_big_edit_blocked(self):
        # the exact david@subdev shape: NON-Fable model, ARMED /goal
        out = self._run(transcript_text=goal_armed_transcript("claude-opus-4-8"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("ARMED", out.stderr)
        self.assertIn("worker", out.stderr)

    def test_goal_armed_sonnet_main_big_write_blocked(self):
        out = self._run(tool="Write",
                        transcript_text=goal_armed_transcript("claude-sonnet-5"))
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_goal_armed_main_new_bypass_marker_allows(self):
        sid = "t-mg-bypass-new-" + uuid.uuid4().hex[:6]
        out = self._run(sid=sid, bypass="new",
                        transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_armed_main_legacy_bypass_marker_still_allows(self):
        # backward compat: the ORIGINAL fable-only marker name still bypasses
        # the GENERALIZED (goal-armed) block path too.
        sid = "t-mg-bypass-legacy-" + uuid.uuid4().hex[:6]
        out = self._run(sid=sid, bypass="legacy",
                        transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_not_goal_armed_ordinary_main_passes_unchanged(self):
        # THIS MUST NOT CHANGE: an ordinary interactive main session with no
        # /goal ever armed, on a non-Fable model, still passes.
        tx = "\n".join([
            json.dumps({"type": "user", "message": {"role": "user",
                                                    "content": "fix the bug"}}),
            json.dumps({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "on it"}]}}),
        ]) + "\n"
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_cleared_after_set_is_not_armed(self):
        tx = "\n".join([goal_set_line(), goal_cleared_line()]) + "\n"
        out = self._run(transcript_text=tx, model="claude-opus-4-8")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_set_again_after_cleared_is_re_armed(self):
        tx = "\n".join([goal_set_line("first backlog"), goal_cleared_line("first backlog"),
                        goal_set_line("second backlog")]) + "\n"
        out = self._run(transcript_text=tx, model="claude-opus-4-8")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_small_edit_passes_even_when_goal_armed(self):
        out = self._run(content=SMALL, transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_nested_foreign_transcript_goal_text_is_not_armed(self):
        # a session that greps/pastes ANOTHER session's transcript containing
        # goal marker text (inside a tool_result) must NOT be mistaken for
        # its OWN goal-armed state.
        tx = "\n".join([
            json.dumps({"type": "user", "message": {"role": "user",
                                                    "content": "grep other repos"}}),
            nested_foreign_goal_line("set"),
            json.dumps({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "found it"}]}}),
        ]) + "\n"
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_armed_and_fable_both_hold_still_blocked(self):
        out = self._run(transcript_text=goal_armed_transcript("claude-fable-5"),
                        model="claude-fable-5")
        self.assertEqual(out.returncode, 2, out.stderr)


class TestWiringAndSkill(unittest.TestCase):
    def test_hook_exists_and_wired_for_edit_and_write(self):
        self.assertTrue(HOOK.exists())
        self.assertTrue(os.access(HOOK, os.X_OK))
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        for tool in ("Edit", "Write"):
            ms = json.dumps([mm for mm in cfg["hooks"]["PreToolUse"]
                             if mm.get("matcher") == tool])
            self.assertIn("block-main-implementation.sh", ms,
                          "guard missing for PreToolUse(%s)" % tool)
        # the OLD filename must be gone from the wiring — this is a rename,
        # not an addition of a second hook.
        self.assertNotIn("block-fable-main-implementation.sh",
                         (REPO / "settings" / "hooks.json").read_text())

    def test_fable_advisor_skill_exists_and_registered(self):
        sk = REPO / "skills" / "fable-advisor" / "SKILL.md"
        self.assertTrue(sk.exists())
        txt = sk.read_text()
        for needle in ("fable-gate", "digest", "xhigh", "sonnet"):
            self.assertIn(needle, txt, needle)
        self.assertIn("fable-advisor", airuleset.SKILL_NAMES)

    def test_model_awareness_points_at_the_enforcement(self):
        txt = (REPO / "modules" / "core" / "model-awareness.md").read_text()
        self.assertIn("block-main-implementation.sh", txt)
        self.assertIn("fable-advisor", txt)
        # the generalization itself must be documented, not just the old
        # Fable-only behavior
        self.assertIn("#54", txt)
        self.assertRegex(txt, r"(?i)armed[^\n]*(/goal|goal)")


if __name__ == "__main__":
    unittest.main()
