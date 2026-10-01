"""#1203 subagent slice (decision 30.9.): a subagent whose OWN transcript
proves a Claude Code fallback is blocked too.

Live evidence: 12 subagent transcripts on the controller carry a fallback
marker, e.g. ``agent-a0cf4394c677e264e.jsonl`` (25.9. 11:54Z, opus 5.5 -> 4.8,
then 32 replies on 4.8). Every entry of a subagent's own transcript is
``isSidechain: true``, which the main-session reader skipped. Only a PROVEN
fallback blocks; a per-dispatch model that merely differs stays allowed.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import model_fallback as mf  # noqa: E402
from gates import modelfallback as gate  # noqa: E402


def _assistant(model, content=None, uuid="a"):
    return {"type": "assistant", "isSidechain": True, "uuid": uuid,
            "timestamp": "2026-09-25T11:54:03Z",
            "message": {"model": model, "role": "assistant",
                        "content": content or [{"type": "text", "text": "ok"}]}}


def _marker(frm="claude-opus-5-5", to="claude-opus-4-8"):
    return _assistant(to, [{"type": "fallback", "from": {"model": frm},
                            "to": {"model": to}}], uuid="m1")


class _Session(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mf1203s-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, True))
        self.main = os.path.join(self.d, "sid1.jsonl")
        with open(self.main, "w") as fh:
            fh.write(json.dumps({"type": "assistant", "isSidechain": False,
                                 "message": {"model": "claude-opus-5-5",
                                             "content": [{"type": "text", "text": "x"}]}}) + "\n")

    def sub(self, agent_id, entries):
        p = os.path.join(self.d, "sid1", "subagents", "agent-%s.jsonl" % agent_id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        return p

    def decide(self, agent_id, **extra):
        payload = {"tool_name": "Bash", "transcript_path": self.main, "agent_id": agent_id}
        payload.update(extra)
        return gate.decide(json.dumps(payload), env={})


class TestSidechainReader(_Session):
    def test_sidechain_entries_are_read_on_request(self):
        p = self.sub("a1", [_assistant("claude-opus-5-5"), _marker(),
                            _assistant("claude-opus-4-8", uuid="b")])
        st = mf.tail_state(p, sidechain=True)
        self.assertEqual(st["model"], "claude-opus-4-8")
        self.assertIsNotNone(st["marker"])

    def test_the_default_still_skips_sidechain_entries(self):
        p = self.sub("a2", [_marker()])
        self.assertIsNone(mf.tail_state(p))


class TestSubagentGate(_Session):
    def test_a_subagent_that_fell_back_is_blocked(self):
        self.sub("a0cf4394c677e264e", [_assistant("claude-opus-5-5"), _marker(),
                                        _assistant("claude-opus-4-8", uuid="b")])
        msg, kind, _ = self.decide("a0cf4394c677e264e")
        self.assertEqual(kind, "block-subagent")
        self.assertIn("BLOCKED (#1203)", msg)
        self.assertIn("subagent", msg)
        self.assertIn("opus4.8", msg)

    def test_a_per_dispatch_model_without_a_marker_is_allowed(self):
        self.sub("a3", [_assistant("claude-sonnet-5-5")])
        self.assertEqual(self.decide("a3"), (None, None, ""))

    def test_the_agent_transcript_path_field_wins(self):
        p = self.sub("other", [_marker()])
        msg, kind, _ = self.decide("a4", agent_transcript_path=p)
        self.assertEqual(kind, "block-subagent")

    def test_a_missing_transcript_is_allowed(self):
        self.assertEqual(self.decide("a5"), (None, None, ""))

    def test_a_traversing_agent_id_is_allowed_and_never_read(self):
        for bad in ("../x", "a/b", "", "x" * 65):
            self.assertEqual(self.decide(bad)[0], None, bad)

    def test_the_guard_off_switch_applies_to_subagents(self):
        self.sub("a6", [_marker()])
        payload = json.dumps({"tool_name": "Bash", "transcript_path": self.main,
                              "agent_id": "a6"})
        msg, kind, _ = gate.decide(payload, env={"AIRULESET_MODEL_GUARD": "off"})
        self.assertIsNone(msg)
        self.assertEqual(kind, "bypass")


if __name__ == "__main__":
    unittest.main()
