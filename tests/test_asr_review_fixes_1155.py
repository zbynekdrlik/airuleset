"""#1155: regressions for the adversarial-review findings on the ASR adapters,
the chunk stitching and the A/B scorer.

- stitching: a word with no speaker (`?`) must never claim a real voice;
- the `error` marker carries the provider's HTTP error text (the first live run
  is the first real check of the request shapes);
- a custom biasing term file (`context=<path>`) for every adapter, not only the
  built-in montalu ERP list;
- the Gemini polling paths (upload PROCESSING, interaction in_progress) and a
  failed upload that still deletes the server-side file;
- Soniox's own error-marker text stays what it was before the refactor;
- the scorer refuses an unfilled reference, a zero-width or broken regex term,
  duplicate slice starts, and never counts one label's overlapping turns twice.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import unittest

from asr_testlib_1155 import (FAKE_CRED, FakeResponse, cleanup_module_tmp, fixture,
                              import_script, new_tmp, write_wav)
from test_asr_adapter_contract_1155 import (EXPECTED_TURNS, UPLOAD_URL, elevenlabs_routes,
                                            gemini_routes, run_adapter, soniox_routes)

TERMS = ["# client vocabulary", "", "Montaflex", "re:ZAK\\d+", "cenová ponuka"]
PLAIN_TERMS = ["Montaflex", "cenová ponuka"]


def terms_path(testcase):
    p = new_tmp(testcase) / "terms.txt"
    p.write_text("\n".join(TERMS) + "\n", encoding="utf-8")
    return str(p)


def W(text, a, b, spk):
    return {"text": text, "start_s": a, "end_s": b, "speaker": spk}


class StitchUnknownSpeaker(unittest.TestCase):
    def test_unknown_words_never_claim_a_real_speaker(self):
        m = import_script("asr_chunks")
        out = m.stitch_chunks([
            {"start_s": 0.0, "end_s": 10.0, "words": [
                W("a", 1.0, 2.0, "s1"), W("unk", 8.0, 10.0, "?")]},
            {"start_s": 8.0, "end_s": 18.0, "words": [
                W("x", 1.0, 2.0, "s1"), W("later", 5.0, 6.0, "s1")]},
        ])
        by_text = {w["text"]: w["speaker"] for w in out}
        self.assertNotEqual(by_text["later"], "?")
        self.assertEqual(by_text["unk"], "?")


class ErrorMarkerCarriesProviderText(unittest.TestCase):
    def test_http_error_body_reaches_the_error_marker(self):
        mod = import_script("transcribe_elevenlabs")
        rc, out, printed, _ = run_adapter(mod, "ELEVENLABS_API_KEY", [],
                                          [(("POST", "/v1/speech-to-text"), 500)])
        self.assertEqual(rc, 1)
        err = (out / "error").read_text()
        self.assertIn("500", err)
        self.assertIn('{"error":"fake"}', err)
        self.assertNotIn(FAKE_CRED, err + printed)

    def test_soniox_incomplete_marker_text_is_unchanged(self):
        mod = import_script("transcribe_soniox")
        routes = [r for r in soniox_routes() if r[0] != ("GET", "/transcriptions/tr_1")]
        routes.insert(3, (("GET", "/transcriptions/tr_1"),
                          FakeResponse({"status": "error", "error_message": "bad audio"})))
        rc, out, printed, _ = run_adapter(mod, "SONIOX_API_KEY", [], routes)
        self.assertEqual(rc, 1)
        self.assertEqual((out / "error").read_text(), "error bad audio")
        self.assertIn("ERROR: transcription did not complete: error bad audio", printed)


class CustomContextFile(unittest.TestCase):
    def test_elevenlabs_keyterms_come_from_the_file(self):
        mod = import_script("transcribe_elevenlabs")
        rc, out, printed, fake = run_adapter(mod, "ELEVENLABS_API_KEY",
                                             ["sk", terms_path(self)], elevenlabs_routes())
        self.assertEqual(rc, 0, printed)
        (call,) = fake.find("POST", "/v1/speech-to-text")
        body = call["data"].decode("utf-8", errors="replace")   # the wav part is binary
        self.assertEqual(re.findall(r'name="keyterms"\r\n\r\n([^\r]*)\r\n', body), PLAIN_TERMS)

    def test_gemini_vocabulary_comes_from_the_file(self):
        mod = import_script("transcribe_gemini")
        rc, out, printed, fake = run_adapter(
            mod, "GEMINI_API_KEY", ["sk", terms_path(self), "vocab"],
            gemini_routes(["gemini_interaction_vocab.json"]))
        self.assertEqual(rc, 0, printed)
        (call,) = fake.find("POST", "/v1beta/interactions")
        cfg = json.loads(call["data"])["generation_config"]["transcription_config"]
        self.assertEqual(cfg["custom_vocabulary"], PLAIN_TERMS)

    def test_soniox_context_comes_from_the_file(self):
        mod = import_script("transcribe_soniox")
        rc, out, printed, fake = run_adapter(mod, "SONIOX_API_KEY",
                                             ["sk", terms_path(self)], soniox_routes())
        self.assertEqual(rc, 0, printed)
        (call,) = fake.find("POST", "/v1/transcriptions")
        self.assertEqual(json.loads(call["data"])["context"], {"terms": PLAIN_TERMS})

    def test_missing_context_file_fails_before_any_request(self):
        for module, env in (("transcribe_soniox", "SONIOX_API_KEY"),
                            ("transcribe_elevenlabs", "ELEVENLABS_API_KEY"),
                            ("transcribe_gemini", "GEMINI_API_KEY")):
            with self.subTest(adapter=module):
                rc, out, printed, fake = run_adapter(
                    import_script(module), env, ["sk", "/nonexistent/asr-terms.txt"], [])
                self.assertEqual(rc, 1)
                self.assertIn("context", (out / "error").read_text())
                self.assertEqual(fake.calls, [])


class GeminiPolling(unittest.TestCase):
    def _routes(self, upload_file, interactions, extra):
        routes = gemini_routes(interactions)
        routes[0] = (("POST", "upload_id=u1"), FakeResponse(upload_file))
        return extra + routes

    def test_in_progress_interaction_is_polled_until_completed(self):
        for iid, path in (("interactions/p1", "/v1beta/interactions/p1"),
                          ("p2", "/v1beta/interactions/p2")):
            with self.subTest(id=iid):
                routes = gemini_routes([])
                routes[2] = (("POST", "/v1beta/interactions"),
                             FakeResponse({"id": iid, "status": "in_progress"}))
                routes.insert(0, (("GET", path),
                                  FakeResponse(fixture("gemini_interaction_single.json"))))
                rc, out, printed, fake = run_adapter(
                    import_script("transcribe_gemini"), "GEMINI_API_KEY", ["sk", "none"], routes)
                self.assertEqual(rc, 0, printed)
                self.assertEqual(len(fake.find("GET", path)), 1)
                turns = json.loads((out / "speaker_turns.json").read_text(encoding="utf-8"))
                self.assertEqual([(t["speaker"], t["text"]) for t in turns], EXPECTED_TURNS)

    def test_processing_upload_is_polled_until_active(self):
        processing = {"file": {**fixture("gemini_file_active.json")["file"],
                               "state": "PROCESSING"}}
        routes = self._routes(processing, ["gemini_interaction_single.json"],
                              [(("GET", "/v1beta/files/abc123"),
                                FakeResponse(fixture("gemini_file_active.json")["file"]))])
        rc, out, printed, fake = run_adapter(import_script("transcribe_gemini"),
                                             "GEMINI_API_KEY", ["sk", "none"], routes)
        self.assertEqual(rc, 0, printed)
        self.assertEqual(len(fake.find("GET", "/v1beta/files/abc123")), 1)

    def test_failed_upload_is_deleted_and_never_transcribed(self):
        failed = {"file": {**fixture("gemini_file_active.json")["file"], "state": "FAILED"}}
        rc, out, printed, fake = run_adapter(
            import_script("transcribe_gemini"), "GEMINI_API_KEY", ["sk", "none"],
            self._routes(failed, [], []))
        self.assertEqual(rc, 1)
        self.assertEqual(fake.find("POST", "/v1beta/interactions"), [])
        self.assertEqual(len(fake.find("DELETE", "/v1beta/files/abc123")), 1)
        self.assertIn(UPLOAD_URL, [c["url"] for c in fake.calls])


class ScorerRefusals(unittest.TestCase):
    def setUp(self):
        self.m = import_script("ab_asr")
        self.tmp = new_tmp(self)

    def test_unfilled_reference_is_flagged_not_scored(self):
        d = self.tmp / "s1"
        write_wav(d / "audio.wav", 10.0)
        (d / "reference.json").write_text(json.dumps({"turns": []}))
        (d / "soniox").mkdir()
        (d / "soniox" / "speaker_turns.json").write_text(
            json.dumps([{"speaker": "1", "start_s": 0, "end_s": 5, "text": "faktúra"}]))
        (d / "soniox" / "done").write_text("ok")
        terms = self.tmp / "t.txt"
        terms.write_text("faktúra\n", encoding="utf-8")
        report = self.tmp / "r.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.m.main(["score", "--terms", str(terms), "--out", str(report),
                              "--providers", "soniox", str(d)])
        self.assertEqual(rc, 1)
        self.assertIn("reference", report.read_text(encoding="utf-8").split("## Missing")[1])

    def test_zero_width_and_broken_regex_terms_are_rejected(self):
        for line in ("re:\\d*", "re:(", "re:"):
            with self.subTest(term=line):
                p = self.tmp / "bad.txt"
                p.write_text(line + "\n", encoding="utf-8")
                with self.assertRaises(ValueError) as ctx:
                    self.m.load_terms(p)
                self.assertIn(line, str(ctx.exception))

    def test_duplicate_slice_starts_are_refused(self):
        src = write_wav(self.tmp / "audio.wav", 30.0)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.m.main(["cut", "--audio", str(src), "--at", "10.2,10.7", "--len", "5",
                              "--out", str(self.tmp / "slices")])
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.tmp / "slices").exists())

    def test_overlapping_turns_of_one_label_count_once(self):
        r = self.m.speaker_agreement(
            [{"speaker": "A", "start_s": 0, "end_s": 10}],
            [{"speaker": "1", "start_s": 0, "end_s": 10},
             {"speaker": "1", "start_s": 5, "end_s": 10}])
        self.assertAlmostEqual(r["agreement"], 1.0)


def tearDownModule():
    cleanup_module_tmp()


if __name__ == "__main__":
    unittest.main()
