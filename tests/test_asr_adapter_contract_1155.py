"""#1155: every cloud ASR adapter writes EXACTLY the transcribe_soniox.py
output contract.

One contract, three adapters (Soniox stt-async-v5, ElevenLabs Scribe v2,
Gemini gemini-3.5-transcribe). Each runs its real `main()` over a fixture
response in the provider's documented shape; only `urllib.request.urlopen`
is faked. The same audio fixture yields the same two turns from every
adapter, so a later switch of the primary provider is a one-line change the
rest of the meeting-analysis pipeline never notices.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asr_testlib_1155 import (CONTRACT_FILES, FAKE_CRED, FakeResponse, fake_http,
                              fixture, import_script, write_wav)

EXPECTED_TURNS = [("1", "Pošli mi faktúru OP 12."),
                  ("2", "Áno, zálohová faktúra je v Odoo.")]
TXT_LINE = re.compile(r"^\[\d\d:\d\d\] Speaker \S+: \S.*$")

GEMINI_BASE = "https://generativelanguage.googleapis.com"
UPLOAD_URL = GEMINI_BASE + "/upload/v1beta/files?upload_id=u1"


def soniox_routes():
    return [
        (("POST", "/v1/files"), FakeResponse({"id": "file_1"})),
        (("POST", "/v1/transcriptions"), FakeResponse({"id": "tr_1"})),
        (("GET", "/transcriptions/tr_1/transcript"),
         FakeResponse(fixture("soniox_transcript.json"))),
        (("GET", "/transcriptions/tr_1"), FakeResponse({"status": "completed"})),
        (("DELETE", "api.soniox.com"), FakeResponse(b"")),
    ]


def elevenlabs_routes(first=None):
    ok = FakeResponse(fixture("elevenlabs_response.json"))
    return [(("POST", "/v1/speech-to-text"), [first, ok] if first else ok)]


def gemini_routes(interactions):
    return [
        (("POST", "upload_id=u1"), FakeResponse(fixture("gemini_file_active.json"))),
        (("POST", "/upload/v1beta/files"),
         FakeResponse(b"", {"X-Goog-Upload-URL": UPLOAD_URL})),
        (("POST", "/v1beta/interactions"),
         [FakeResponse(fixture(n)) for n in interactions]),
        (("DELETE", "/v1beta/files/abc123"), FakeResponse(b"")),
    ]


ADAPTERS = {
    "soniox": ("transcribe_soniox", "SONIOX_API_KEY", [], soniox_routes),
    "elevenlabs": ("transcribe_elevenlabs", "ELEVENLABS_API_KEY", [], elevenlabs_routes),
    "gemini": ("transcribe_gemini", "GEMINI_API_KEY", [],
               lambda: gemini_routes(["gemini_interaction_single.json"])),
}


def run_adapter(module, env_name, argv, routes, *, seconds=4.0, cred=FAKE_CRED):
    """Run an adapter's real main() in a temp dir; returns (rc, out_dir,
    captured stdout+stderr, FakeHttp)."""
    tmp = Path(tempfile.mkdtemp(prefix="asr1155-"))
    audio = write_wav(tmp / "audio.wav", seconds)
    out = tmp / "out"
    env = {k: v for k, v in os.environ.items()
           if k not in ("SONIOX_API_KEY", "ELEVENLABS_API_KEY", "GEMINI_API_KEY")}
    if cred is not None:
        env[env_name] = cred
    buf = io.StringIO()
    with fake_http(routes) as fake, mock.patch.dict(os.environ, env, clear=True), \
            contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = module.main([str(audio), str(out), *argv])
    return rc, out, buf.getvalue(), fake


class ContractMixin:
    def assert_contract(self, out: Path, printed: str, model: str):
        self.assertEqual({p.name for p in out.iterdir()}, CONTRACT_FILES)
        self.assertEqual((out / "done").read_text(), "ok")
        tj = json.loads((out / "transcript.json").read_text(encoding="utf-8"))
        self.assertEqual(set(tj), {"model", "language", "tokens", "segments"})
        self.assertEqual(tj["model"], model)
        turns = json.loads((out / "speaker_turns.json").read_text(encoding="utf-8"))
        self.assertEqual(turns, tj["segments"])
        for t in turns:
            self.assertEqual(set(t), {"speaker", "start_s", "end_s", "text"})
            self.assertIsInstance(t["speaker"], str)
            self.assertLessEqual(t["start_s"], t["end_s"])
        self.assertEqual([(t["speaker"], t["text"]) for t in turns], EXPECTED_TURNS)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(set(summary),
                         {"duration_s", "n_tokens", "n_segments", "speakers", "model"})
        self.assertEqual(summary["speakers"], ["1", "2"])
        self.assertEqual(summary["n_segments"], 2)
        self.assertEqual(summary["model"], model)
        self.assertAlmostEqual(summary["duration_s"], 3.7, delta=0.05)
        lines = (out / "transcript.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertRegex(ln, TXT_LINE)
        self.assertEqual(lines[0], "[00:00] Speaker 1: Pošli mi faktúru OP 12.")
        # the credential never reaches the transcript or any output file
        self.assertNotIn(FAKE_CRED, printed)
        for p in out.iterdir():
            self.assertNotIn(FAKE_CRED, p.read_text(encoding="utf-8"))


class EveryAdapterWritesTheSonioxContract(ContractMixin, unittest.TestCase):
    def test_soniox(self):
        mod = import_script("transcribe_soniox")
        rc, out, printed, _ = run_adapter(mod, "SONIOX_API_KEY", [], soniox_routes())
        self.assertEqual(rc, 0, printed)
        self.assert_contract(out, printed, "stt-async-v5")

    def test_elevenlabs(self):
        mod = import_script("transcribe_elevenlabs")
        rc, out, printed, _ = run_adapter(mod, "ELEVENLABS_API_KEY", [],
                                          elevenlabs_routes())
        self.assertEqual(rc, 0, printed)
        self.assert_contract(out, printed, "scribe_v2")

    def test_gemini(self):
        mod = import_script("transcribe_gemini")
        rc, out, printed, _ = run_adapter(
            mod, "GEMINI_API_KEY", [], gemini_routes(["gemini_interaction_single.json"]))
        self.assertEqual(rc, 0, printed)
        self.assert_contract(out, printed, "gemini-3.5-transcribe")

    def test_missing_key_writes_error_and_makes_no_request(self):
        for name, (module, env_name, argv, routes) in ADAPTERS.items():
            with self.subTest(adapter=name):
                mod = import_script(module)
                rc, out, printed, fake = run_adapter(mod, env_name, argv, routes(),
                                                     cred=None)
                self.assertEqual(rc, 1)
                self.assertTrue((out / "error").exists())
                self.assertFalse((out / "done").exists())
                self.assertIn(env_name, (out / "error").read_text())
                self.assertEqual(fake.calls, [])

    def test_http_failure_writes_error_marker_not_done(self):
        mod = import_script("transcribe_elevenlabs")
        routes = [(("POST", "/v1/speech-to-text"), 500)]
        rc, out, printed, _ = run_adapter(mod, "ELEVENLABS_API_KEY", [], routes)
        self.assertEqual(rc, 1)
        self.assertTrue((out / "error").exists())
        self.assertFalse((out / "done").exists())
        self.assertNotIn(FAKE_CRED, printed)


class ElevenLabsRequestShape(unittest.TestCase):
    """The request follows the documented Scribe v2 multipart form."""

    def _run(self, argv, routes=None):
        mod = import_script("transcribe_elevenlabs")
        return run_adapter(mod, "ELEVENLABS_API_KEY", argv, routes or elevenlabs_routes())

    def test_fields_and_auth_header(self):
        rc, out, printed, fake = self._run(["sk", "erp"])
        self.assertEqual(rc, 0, printed)
        (call,) = fake.find("POST", "/v1/speech-to-text")
        self.assertTrue(call["url"].startswith("https://api.elevenlabs.io/"))
        self.assertEqual(call["headers"]["xi-api-key"], FAKE_CRED)
        body = call["data"].decode("utf-8", errors="replace")
        for field, value in (("model_id", "scribe_v2"), ("language_code", "sk"),
                             ("diarize", "true"), ("timestamps_granularity", "word")):
            self.assertRegex(body, rf'name="{field}"\r\n\r\n{value}\r\n')
        self.assertIn('name="file"; filename="audio.wav"', body)
        keyterms = re.findall(r'name="keyterms"\r\n\r\n([^\r]*)\r\n', body)
        self.assertIn("faktúra", keyterms)
        self.assertGreater(len(keyterms), 10)

    def test_context_none_sends_no_keyterms(self):
        rc, out, printed, fake = self._run(["sk", "none"])
        self.assertEqual(rc, 0, printed)
        (call,) = fake.find("POST", "/v1/speech-to-text")
        self.assertNotIn(b'name="keyterms"', call["data"])

    def test_rejected_keyterms_retries_without_them_and_warns(self):
        rc, out, printed, fake = self._run(["sk", "erp"], elevenlabs_routes(first=400))
        self.assertEqual(rc, 0, printed)
        first, second = fake.find("POST", "/v1/speech-to-text")
        self.assertIn(b'name="keyterms"', first["data"])
        self.assertNotIn(b'name="keyterms"', second["data"])
        self.assertIn("WARN", printed)

    def test_audio_events_are_not_transcript_words(self):
        rc, out, printed, _ = self._run(["sk", "erp"])
        self.assertEqual(rc, 0, printed)
        self.assertNotIn("smiech", (out / "transcript.txt").read_text(encoding="utf-8"))


class GeminiRequestShape(unittest.TestCase):
    """The request follows the documented gemini-3.5-transcribe config."""

    def _run(self, argv, interactions, seconds=4.0, **patches):
        mod = import_script("transcribe_gemini")
        with mock.patch.multiple(mod, **patches) if patches else contextlib.nullcontext():
            return run_adapter(mod, "GEMINI_API_KEY", argv, gemini_routes(interactions),
                               seconds=seconds)

    @staticmethod
    def _bodies(fake):
        return [json.loads(c["data"]) for c in fake.find("POST", "/v1beta/interactions")]

    def test_diarize_mode_config_and_cleanup(self):
        rc, out, printed, fake = self._run(["sk", "erp"], ["gemini_interaction_single.json"])
        self.assertEqual(rc, 0, printed)
        (body,) = self._bodies(fake)
        self.assertEqual(body["model"], "gemini-3.5-transcribe")
        self.assertEqual(body["input"][0]["uri"],
                         fixture("gemini_file_active.json")["file"]["uri"])
        cfg = body["generation_config"]["transcription_config"]
        self.assertEqual(cfg["language_codes"], ["sk-SK"])
        self.assertEqual(cfg["mode"], {"type": "verbatim", "diarization_mode": "speaker",
                                       "timestamp_granularities": ["word"]})
        # the documented incompatibility: no vocabulary WITH diarization
        self.assertNotIn("custom_vocabulary", cfg)
        self.assertIn("WARN", printed)
        for c in fake.calls:
            self.assertEqual(c["headers"].get("x-goog-api-key"), FAKE_CRED)
        self.assertEqual(len(fake.find("DELETE", "/v1beta/files/abc123")), 1)

    def test_vocab_mode_sends_vocabulary_without_diarization(self):
        rc, out, printed, fake = self._run(["sk", "erp", "vocab"],
                                           ["gemini_interaction_vocab.json"])
        self.assertEqual(rc, 0, printed)
        (body,) = self._bodies(fake)
        cfg = body["generation_config"]["transcription_config"]
        self.assertIn("faktúra", cfg["custom_vocabulary"])
        self.assertNotIn("mode", cfg)
        turns = json.loads((out / "speaker_turns.json").read_text(encoding="utf-8"))
        self.assertEqual([t["speaker"] for t in turns], ["?"])
        self.assertIn("zálohová faktúra", turns[0]["text"])
        self.assertEqual(json.loads((out / "summary.json").read_text())["speakers"], ["?"])

    def test_long_audio_is_chunked_and_speakers_stitched(self):
        rc, out, printed, fake = self._run(
            ["sk", "none"],
            ["gemini_interaction_chunk1.json", "gemini_interaction_chunk2.json"],
            seconds=3.5, CHUNK_S=2.0, OVERLAP_S=0.5)
        self.assertEqual(rc, 0, printed)
        self.assertEqual(len(self._bodies(fake)), 2)
        self.assertEqual(len(fake.find("DELETE", "/v1beta/files/")), 2)
        turns = json.loads((out / "speaker_turns.json").read_text(encoding="utf-8"))
        self.assertEqual([(t["speaker"], t["text"]) for t in turns],
                         [("1", "Pošli mi faktúru"), ("2", "Áno, zálohová faktúra"),
                          ("3", "Dobre.")])
        # chunk 2's words are on the absolute timeline
        self.assertAlmostEqual(turns[-1]["start_s"], 2.7, places=2)

    def test_non_wav_input_fails_loud(self):
        mod = import_script("transcribe_gemini")
        tmp = Path(tempfile.mkdtemp(prefix="asr1155-"))
        bad = tmp / "audio.mp3"
        bad.write_bytes(b"not a wav")
        buf = io.StringIO()
        with fake_http([]) as fake, \
                mock.patch.dict(os.environ, {"GEMINI_API_KEY": FAKE_CRED}), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = mod.main([str(bad), str(tmp / "out")])
        self.assertEqual(rc, 1)
        self.assertTrue((tmp / "out" / "error").exists())
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
