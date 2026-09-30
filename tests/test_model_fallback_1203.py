"""#1203 — a Claude Code model fallback is VISIBLE (footer), STOPS work (the
PreToolUse gate) and is detected from the transcript the way it is really
written.

The incident: an Opus 5.5 safeguard stop appended an assistant entry whose
content is `{"type":"fallback","from":{"model":"claude-opus-5-5"},"to":{"model":
"claude-opus-4-8"}}` and the session kept working on 4.8 for two days while the
footer showed a green `opus`. Fixture entries below copy the live shape (the
marker is a CONTENT BLOCK of an assistant entry whose `message.model` is already
the fallback model).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import airuleset  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_lineup  # noqa: E402
import statusbar  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

MANAGED = "claude-opus-5-5[1m]"
HOOK = REPO / "hooks" / "block-model-fallback.sh"


def _assistant(model, content=None, uuid="a", sidechain=False, ts="2026-09-28T14:51:50Z"):
    return {"type": "assistant", "isSidechain": sidechain, "uuid": uuid, "timestamp": ts,
            "message": {"model": model, "role": "assistant",
                        "content": content or [{"type": "text", "text": "ok"}]}}


def _marker(frm="claude-opus-5-5", to="claude-opus-4-8", uuid="m1",
            ts="2026-09-28T14:51:50Z"):
    return _assistant(to, [{"type": "fallback", "from": {"model": frm},
                            "to": {"model": to}}], uuid=uuid, ts=ts)


def _user(text):
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


def _model_cmd(args):
    return _user("<command-name>/model</command-name>\n            <command-message>model"
                 "</command-message>\n            <command-args>%s</command-args>" % args)


def _write(testcase, entries, prefix=""):
    d = tempfile.mkdtemp(prefix="mf1203-")
    testcase.addCleanup(lambda: __import__("shutil").rmtree(d, True))
    p = os.path.join(d, "sess.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(prefix)
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return p


class ShortName(unittest.TestCase):
    def test_versioned_short_names(self):
        for mid, want in (("claude-opus-5-5[1m]", "opus5.5"),
                          ("claude-opus-4-8", "opus4.8"),
                          ("claude-fable-5-1", "fable5.1"),
                          ("claude-sonnet-5-5", "sonnet5.5"),
                          ("claude-sonnet-5", "sonnet5"),
                          ("claude-haiku-4-5", "haiku4.5"),
                          ("claude-sonnet-4-5-20250929", "sonnet4.5"),
                          ("claude-3-5-sonnet-20241022", "sonnet3.5"),
                          ("us.anthropic.claude-opus-4-8-v1:0", "opus4.8"),
                          ("Opus 4.8", "opus4.8"),
                          ("Fable", "fable")):
            self.assertEqual(mf.short_name(mid), want, mid)

    def test_foreign_or_hostile_is_empty(self):
        for bad in ("gpt-5", "impl-main", "deepseek-chat", "", None, 5, ["x"]):
            self.assertEqual(mf.short_name(bad), "", repr(bad))

    def test_same_model_ignores_context_tag_and_date(self):
        self.assertTrue(mf.same_model("claude-opus-5-5", MANAGED))
        self.assertFalse(mf.same_model("claude-opus-4-8", MANAGED))
        self.assertFalse(mf.same_model("claude-opus-5", MANAGED))


class TailState(unittest.TestCase):
    def test_last_entry_is_the_fallback_marker(self):
        p = _write(self, [_assistant("claude-opus-5-5"), _marker()])
        st = mf.tail_state(p)
        self.assertEqual(st["model"], "claude-opus-4-8")
        self.assertEqual(st["fallback"], {"from": "claude-opus-5-5", "to": "claude-opus-4-8"})
        self.assertEqual(st["marker"]["uuid"], "m1")
        self.assertEqual(mf.verdict(st, MANAGED)["kind"], "marker")

    def test_work_continues_on_the_fallback_model(self):
        p = _write(self, [_marker(), _user("next"), _assistant("claude-opus-4-8", uuid="b")])
        v = mf.verdict(mf.tail_state(p), MANAGED)
        self.assertEqual(v["kind"], "model")
        self.assertEqual(v["short"], "opus4.8")
        self.assertEqual(v["managed_short"], "opus5.5")

    def test_managed_model_is_healthy(self):
        p = _write(self, [_assistant("claude-opus-5-5")])
        self.assertIsNone(mf.verdict(mf.tail_state(p), MANAGED))

    def test_synthetic_and_sidechain_entries_are_skipped(self):
        p = _write(self, [_assistant("claude-opus-5-5"),
                          _assistant("<synthetic>"),
                          _assistant("claude-opus-4-8", sidechain=True)])
        st = mf.tail_state(p)
        self.assertEqual(st["model"], "claude-opus-5-5")
        self.assertIsNone(mf.verdict(st, MANAGED))

    def test_model_command_after_the_last_reply_reads_restored(self):
        p = _write(self, [_marker(), _assistant("claude-opus-4-8"), _model_cmd(MANAGED)])
        st = mf.tail_state(p)
        self.assertEqual(st["model_cmd"], MANAGED)
        self.assertIsNone(mf.verdict(st, MANAGED))

    def test_model_command_before_a_fallback_reply_does_not_count(self):
        p = _write(self, [_model_cmd(MANAGED), _assistant("claude-opus-4-8")])
        st = mf.tail_state(p)
        self.assertIsNone(st["model_cmd"])
        self.assertIsNotNone(mf.verdict(st, MANAGED))

    def test_model_command_to_another_model_is_still_a_mismatch(self):
        p = _write(self, [_assistant("claude-opus-4-8"), _model_cmd("sonnet")])
        self.assertIsNotNone(mf.verdict(mf.tail_state(p), MANAGED))

    def test_read_is_bounded_to_the_tail(self):
        # a marker far before the tail window is not seen; the tail's own entry is
        prefix = json.dumps(_marker()) + "\n" + (("x" * 400) + "\n") * 2000
        # (the late reply stays on the fallback model, so the marker is in force)
        p = _write(self, [_assistant("claude-opus-4-8", uuid="late")], prefix=prefix)
        self.assertIsNotNone(mf.tail_state(p, max_bytes=10 ** 7)["marker"])
        st = mf.tail_state(p, max_bytes=64 * 1024)
        self.assertEqual(st["uuid"], "late")
        self.assertIsNone(st["marker"])

    def test_no_assistant_entry_is_none(self):
        p = _write(self, [_user("hi")])
        self.assertIsNone(mf.tail_state(p))
        self.assertIsNone(mf.verdict(None, MANAGED))

    def test_unreadable_raises_oserror(self):
        with self.assertRaises(OSError):
            mf.tail_state("/nonexistent/sess.jsonl")

    def test_unresolvable_managed_never_blocks(self):
        p = _write(self, [_assistant("claude-opus-4-8")])
        self.assertIsNone(mf.verdict(mf.tail_state(p), ""))


class ModelLineup(unittest.TestCase):
    def test_airuleset_re_exports_the_one_source(self):
        self.assertIs(airuleset.MODEL_TIERS, model_lineup.MODEL_TIERS)
        self.assertEqual(airuleset.MANAGED_MODEL, model_lineup.MANAGED_MODEL)
        self.assertEqual(model_lineup.MANAGED_MODEL, MANAGED)

    def test_lineup_module_imports_nothing(self):
        src = (REPO / "model_lineup.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s")


class StatuslineTranscriptWins(unittest.TestCase):
    def test_transcript_fallback_beats_the_payload_model(self):
        # the payload keeps naming the configured model; the fallback is only
        # in the transcript — the #1203 blind spot.
        p = _write(self, [_assistant("claude-opus-5-5"), _marker()])
        seg = statusbar.model_segment(
            {"model": {"id": "claude-opus-5-5[1m]"}, "transcript_path": p},
            managed_model=MANAGED)
        self.assertEqual(seg, "\033[38;5;196mopus4.8 FALLBACK\033[0m")

    def test_healthy_transcript_stays_green(self):
        p = _write(self, [_assistant("claude-opus-5-5")])
        seg = statusbar.model_segment(
            {"model": {"id": "claude-opus-5-5[1m]"}, "transcript_path": p},
            managed_model=MANAGED)
        self.assertEqual(seg, "\033[38;5;40mopus5.5\033[0m")

    def test_typed_model_command_shows_the_restore(self):
        p = _write(self, [_marker(), _model_cmd(MANAGED)])
        seg = statusbar.model_segment(
            {"model": {"id": "claude-opus-5-5[1m]"}, "transcript_path": p},
            managed_model=MANAGED)
        self.assertEqual(seg, "\033[38;5;40mopus5.5\033[0m")

    def test_unreadable_transcript_falls_back_to_payload(self):
        seg = statusbar.model_segment(
            {"model": {"id": "claude-opus-4-8"}, "transcript_path": "/nope/x.jsonl"},
            managed_model=MANAGED)
        self.assertEqual(seg, "\033[38;5;196mopus4.8 FALLBACK\033[0m")

    def test_implementer_window_never_shows_fallback(self):
        with unittest.mock.patch.dict(os.environ, {"AIRULESET_ROLE": "implementer"}):
            seg = statusbar.model_segment({"model": {"id": "claude-sonnet-5-5"}},
                                           managed_model=MANAGED)
        self.assertNotIn("FALLBACK", seg)


class GateDecide(unittest.TestCase):
    def setUp(self):
        from gates import modelfallback
        self.g = modelfallback
        self.env = {}

    def _payload(self, tpath, **extra):
        d = {"tool_name": "Bash", "transcript_path": tpath}
        d.update(extra)
        return json.dumps(d)

    def test_blocks_a_main_session_on_the_marker(self):
        p = _write(self, [_assistant("claude-opus-5-5"), _marker()])
        msg, kind, _ = self.g.decide(self._payload(p), env=self.env)
        self.assertEqual(kind, "block")
        self.assertIn("BLOCKED (#1203)", msg)
        self.assertIn("opus4.8", msg)
        self.assertIn("claude-opus-5-5[1m]", msg)
        self.assertIn("ukonči tento turn", msg)      # Slovak
        self.assertIn("end the turn now", msg)       # English

    def test_blocks_continued_work_on_the_fallback_model(self):
        p = _write(self, [_marker(), _assistant("claude-opus-4-8", uuid="b")])
        msg, kind, _ = self.g.decide(self._payload(p), env=self.env)
        self.assertEqual(kind, "block")
        self.assertIsNotNone(msg)

    def test_allows_the_managed_model(self):
        p = _write(self, [_assistant("claude-opus-5-5")])
        self.assertEqual(self.g.decide(self._payload(p), env=self.env), (None, None, ""))

    def test_allows_after_a_typed_model_restore(self):
        p = _write(self, [_marker(), _model_cmd(MANAGED)])
        self.assertEqual(self.g.decide(self._payload(p), env=self.env), (None, None, ""))

    def test_subagents_are_exempt(self):
        p = _write(self, [_marker()])
        self.assertEqual(self.g.decide(self._payload(p, agent_id="a1"), env=self.env),
                         (None, None, ""))

    def test_implementer_window_is_exempt(self):
        p = _write(self, [_assistant("impl-main")])
        self.assertEqual(self.g.decide(self._payload(p),
                                       env={"AIRULESET_ROLE": "implementer"}),
                         (None, None, ""))

    def test_guard_off_bypasses_and_logs(self):
        p = _write(self, [_assistant("claude-sonnet-5-5")])
        msg, kind, _ = self.g.decide(self._payload(p), env={"AIRULESET_MODEL_GUARD": "off"})
        self.assertIsNone(msg)
        self.assertEqual(kind, "bypass")

    def test_read_error_fails_open_and_logs(self):
        msg, kind, extra = self.g.decide(self._payload("/nonexistent/s.jsonl"), env=self.env)
        self.assertIsNone(msg)
        self.assertEqual(kind, "read-error")
        self.assertIn("/nonexistent/s.jsonl", extra)

    def test_no_transcript_or_garbage_fails_open(self):
        for payload in ("", "not json", json.dumps([1]), json.dumps({"tool_name": "Read"})):
            self.assertEqual(self.g.decide(payload, env=self.env), (None, None, ""))


class HookEndToEnd(unittest.TestCase):
    def _run(self, payload, **env_extra):
        env = hermetic_hook_env(self, **env_extra)
        env.pop("AIRULESET_ROLE", None)
        env.pop("AIRULESET_MODEL_GUARD", None)
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), text=True,
                           capture_output=True, env=env, timeout=60)
        return r, env["HOME"]

    def test_hook_blocks_with_exit_2_and_logs(self):
        p = _write(self, [_marker()])
        r, home = self._run({"tool_name": "Read", "transcript_path": p})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("BLOCKED (#1203)", r.stderr)
        log = Path(home, ".claude", "model-fallback-gate.log").read_text()
        self.assertIn("\tblock\t", log)

    def test_hook_allows_the_managed_model(self):
        p = _write(self, [_assistant("claude-opus-5-5")])
        r, _ = self._run({"tool_name": "Read", "transcript_path": p})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_hook_exempts_a_subagent(self):
        p = _write(self, [_marker()])
        r, _ = self._run({"tool_name": "Bash", "transcript_path": p, "agent_id": "x"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_registered_for_every_tool(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        cmds = [(e.get("matcher"), h["command"]) for e in cfg["hooks"]["PreToolUse"]
                for h in e["hooks"]]
        self.assertIn(("*", "bash ~/devel/airuleset/hooks/block-model-fallback.sh"), cmds)


class ManagedSettingsDisableRefusalFallback(unittest.TestCase):
    def test_env_disables_the_refusal_fallback(self):
        import cli_config
        out = cli_config.apply_managed_settings_defaults({"env": {"KEEP": "1"}})
        self.assertEqual(out["env"]["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"], "1")
        self.assertEqual(out["env"]["KEEP"], "1")


class SlashCommandSubmitConfirm(unittest.TestCase):
    """`send_verified` confirms a submit by a transcript `user` turn; a slash
    command is written as the `<command-name>` composite, never the raw text."""

    def _t(self, entries):
        return _write(self, entries)

    def test_model_command_composite_confirms(self):
        from watchdog import transcripts
        p = self._t([_model_cmd(MANAGED)])
        self.assertTrue(transcripts._submit_confirmed(p, 0, "/model " + MANAGED))

    def test_other_args_never_confirm(self):
        from watchdog import transcripts
        p = self._t([_model_cmd("sonnet")])
        self.assertFalse(transcripts._submit_confirmed(p, 0, "/model " + MANAGED))

    def test_before_the_baseline_never_confirms(self):
        from watchdog import transcripts
        p = self._t([_model_cmd(MANAGED)])
        self.assertFalse(transcripts._submit_confirmed(p, os.path.getsize(p),
                                                       "/model " + MANAGED))


if __name__ == "__main__":
    unittest.main()
