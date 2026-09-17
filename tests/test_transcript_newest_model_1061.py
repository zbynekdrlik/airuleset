"""#1061 fix-forward -- `transcript_newest_assistant_model`: the AUTHORSHIP
reader that finds the session's newest assistant `message.model` REGARDLESS of
text content, over a WIDE window, SKIPPING api-error and synthetic entries.

The live defect it fixes: `transcript_last_assistant_model` (the model-float
reader) walks only the DEFAULT 60-entry tail and ABORTS on an api-error entry,
so a busy main turn whose last 60 entries are tool-result / progress entries
returns `''` -> `session_model` -> `unknown` -> `design-record` stamps
`Design-by: main unknown` and the dispatch gate refuses a real main design.
"""
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog.transcripts import (  # noqa: E402
    transcript_newest_assistant_model,
    transcript_last_assistant_model,
)


def _asst(model, text="hi"):
    return {"type": "assistant",
            "message": {"role": "assistant", "model": model,
                        "content": [{"type": "text", "text": text}]}}


def _asst_apierror(model):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant", "model": model,
                        "content": [{"type": "text", "text": "API Error"}]}}


def _tool_result(i):
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result",
                                     "tool_use_id": "t%d" % i,
                                     "content": "output %d" % i}]}}


def _write(entries):
    d = tempfile.mkdtemp()
    p = Path(d) / "sess.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                 encoding="utf-8")
    return p


class TestNewestAssistantModel(unittest.TestCase):
    def test_reads_past_a_long_tool_result_tail(self):
        # ONE Fable assistant turn, then 200 tool-result entries (a busy
        # parallel-tool burst). The default 60-window reader misses the model
        # entirely; the wide authorship reader finds it.
        entries = [_asst("claude-fable-5-1", "the real turn")]
        entries += [_tool_result(i) for i in range(200)]
        p = _write(entries)
        self.assertEqual(
            transcript_newest_assistant_model(p), "claude-fable-5-1")
        # Difference proof: the model-float reader (60-entry window) returns ''.
        self.assertEqual(transcript_last_assistant_model(p), "")

    def test_skips_api_error_tail_returns_previous_model(self):
        entries = [_asst("claude-fable-5-1", "real turn"),
                   _asst_apierror("claude-opus-4-8")]
        p = _write(entries)
        self.assertEqual(
            transcript_newest_assistant_model(p), "claude-fable-5-1")

    def test_empty_transcript_is_blank(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        self.assertEqual(transcript_newest_assistant_model(p), "")

    def test_absent_transcript_is_blank(self):
        self.assertEqual(
            transcript_newest_assistant_model(Path("/no/such/transcript.jsonl")),
            "")

    def test_returns_model_regardless_of_text_content(self):
        # A tool-use-only turn (empty text) still carries the real served model;
        # the authorship reader takes it (unlike the float reader, which skips a
        # sentinel-text turn and walks back to an OLDER model).
        entries = [_asst("claude-fable-5-1", "older turn"),
                   _asst("claude-opus-4-8", "")]
        p = _write(entries)
        self.assertEqual(
            transcript_newest_assistant_model(p), "claude-opus-4-8")

    def test_skips_synthetic_model_placeholder(self):
        # A trailing synthetic/bookkeeping record carries model "<synthetic>",
        # not a real served model -> skip it, return the previous real model.
        entries = [_asst("claude-fable-5-1", "real turn"),
                   {"type": "assistant",
                    "message": {"role": "assistant", "model": "<synthetic>",
                                "content": [{"type": "text", "text": ""}]}}]
        p = _write(entries)
        self.assertEqual(
            transcript_newest_assistant_model(p), "claude-fable-5-1")


if __name__ == "__main__":
    unittest.main()
