"""#1155: chunk planning, wav slicing and cross-chunk speaker stitching for
the Gemini adapter (gemini-3.5-transcribe diarizes at most 30 min per
request, and numbers its speakers per request).

The stitching rule under test (documented in asr_chunks.py):
- chunks overlap by OVERLAP_S; each chunk's local labels are mapped onto the
  running global labels by the speaking time they share in that overlap,
  greedily by descending shared time, one-to-one, above MIN_MATCH_S;
- a local label with no match becomes a NEW global label;
- words in the overlap are kept once: before the overlap's midpoint from the
  earlier chunk, from the midpoint on from the later chunk;
- global labels are "1", "2", ... in order of first appearance.
"""
from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from asr_testlib_1155 import import_script, write_wav


def W(text, a, b, spk):
    return {"text": text, "start_s": a, "end_s": b, "speaker": spk}


class PlanChunks(unittest.TestCase):
    def setUp(self):
        self.m = import_script("asr_chunks")

    def test_short_audio_is_one_chunk(self):
        self.assertEqual(self.m.plan_chunks(600.0, 1680.0, 60.0), [(0.0, 600.0)])

    def test_long_audio_overlaps_and_covers_the_end(self):
        plan = self.m.plan_chunks(3600.0, 1680.0, 60.0)
        self.assertEqual(plan, [(0.0, 1680.0), (1620.0, 3300.0), (3240.0, 3600.0)])
        for (a0, a1), (b0, b1) in zip(plan, plan[1:]):
            self.assertAlmostEqual(a1 - b0, 60.0)
        self.assertTrue(all(e - s <= 1680.0 for s, e in plan))

    def test_zero_overlap_tiles_exactly(self):
        self.assertEqual(self.m.plan_chunks(10.0, 4.0, 0.0),
                         [(0.0, 4.0), (4.0, 8.0), (8.0, 10.0)])

    def test_overlap_not_below_chunk_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.plan_chunks(100.0, 10.0, 10.0)

    def test_empty_audio_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.plan_chunks(0.0, 10.0, 1.0)


class SliceWav(unittest.TestCase):
    def test_slice_has_the_right_frames_and_format(self):
        m = import_script("asr_chunks")
        tmp = Path(tempfile.mkdtemp(prefix="asr1155-"))
        src = write_wav(tmp / "a.wav", 5.0)
        self.assertAlmostEqual(m.wav_duration(src), 5.0)
        dst = m.slice_wav(src, 1.5, 3.5, tmp / "part.wav")
        with wave.open(str(dst), "rb") as w:
            self.assertEqual(w.getnframes(), 32000)
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()),
                             (1, 2, 16000))
        self.assertAlmostEqual(m.wav_duration(m.slice_wav(src, 4.0, 9.0, tmp / "t.wav")), 1.0)


class StitchChunks(unittest.TestCase):
    def setUp(self):
        self.m = import_script("asr_chunks")

    def test_single_chunk_relabels_by_first_appearance(self):
        out = self.m.stitch_chunks([{"start_s": 0.0, "end_s": 10.0, "words": [
            W("a", 0.0, 1.0, "spk_2"), W("b", 1.0, 2.0, "spk_1"), W("c", 2.0, 3.0, "spk_2")]}])
        self.assertEqual([(w["text"], w["speaker"]) for w in out],
                         [("a", "1"), ("b", "2"), ("c", "1")])

    def test_swapped_labels_are_mapped_through_the_overlap(self):
        chunks = [
            {"start_s": 0.0, "end_s": 20.0, "words": [
                W("x1", 1.0, 3.0, "spk_1"), W("y1", 12.0, 14.0, "spk_2"),
                W("x2", 16.0, 18.0, "spk_1"), W("y2", 18.5, 19.5, "spk_2")]},
            # starts at 10 s: overlap [10, 20], midpoint 15
            {"start_s": 10.0, "end_s": 30.0, "words": [
                W("y1", 2.0, 4.0, "spk_2"), W("x2", 6.0, 8.0, "spk_1"),
                W("y2", 8.5, 9.5, "spk_2"), W("x3", 12.0, 13.0, "spk_1")]},
        ]
        # chunk 2 labels DIFFER from chunk 1 on purpose: swap them
        for w in chunks[1]["words"]:
            w["speaker"] = {"spk_1": "spk_2", "spk_2": "spk_1"}[w["speaker"]]
        out = self.m.stitch_chunks(chunks)
        self.assertEqual([(w["text"], w["speaker"], w["start_s"]) for w in out],
                         [("x1", "1", 1.0), ("y1", "2", 12.0), ("x2", "1", 16.0),
                          ("y2", "2", 18.5), ("x3", "1", 22.0)])

    def test_overlap_words_are_kept_once(self):
        chunks = [
            {"start_s": 0.0, "end_s": 10.0, "words": [
                W("a", 1.0, 2.0, "s1"), W("b", 8.0, 8.5, "s1"), W("c", 9.2, 9.8, "s1")]},
            {"start_s": 8.0, "end_s": 18.0, "words": [
                W("b", 0.0, 0.5, "s1"), W("c", 1.2, 1.8, "s1"), W("d", 5.0, 6.0, "s1")]},
        ]
        out = self.m.stitch_chunks(chunks)
        self.assertEqual([w["text"] for w in out], ["a", "b", "c", "d"])
        starts = [w["start_s"] for w in out]
        self.assertEqual(starts, sorted(starts))

    def test_unmatched_local_speaker_becomes_a_new_global_label(self):
        chunks = [
            {"start_s": 0.0, "end_s": 10.0, "words": [W("a", 8.0, 9.5, "s1")]},
            {"start_s": 8.0, "end_s": 18.0, "words": [
                W("a", 0.0, 1.5, "s1"), W("new", 5.0, 6.0, "s2")]},
        ]
        out = self.m.stitch_chunks(chunks)
        self.assertEqual([(w["text"], w["speaker"]) for w in out],
                         [("a", "1"), ("new", "2")])

    def test_speaker_silent_in_the_overlap_gets_a_new_label(self):
        """The documented limit: without shared speech in the overlap there is
        no evidence to link a voice, so it is counted as a new speaker."""
        chunks = [
            {"start_s": 0.0, "end_s": 10.0, "words": [
                W("p", 1.0, 2.0, "s1"), W("q", 8.5, 9.5, "s2")]},
            {"start_s": 8.0, "end_s": 18.0, "words": [
                W("q", 0.5, 1.5, "s1"), W("p-again", 6.0, 7.0, "s2")]},
        ]
        out = self.m.stitch_chunks(chunks)
        self.assertEqual([(w["text"], w["speaker"]) for w in out],
                         [("p", "1"), ("q", "2"), ("p-again", "3")])

    def test_one_to_one_the_stronger_overlap_wins(self):
        chunks = [
            {"start_s": 0.0, "end_s": 10.0, "words": [W("g", 8.0, 10.0, "s1")]},
            # both local speakers talk over global "1" in the overlap [8, 10];
            # l1 shares 1.0 s, l2 shares 0.5 s -> l1 gets "1", l2 is new
            {"start_s": 8.0, "end_s": 18.0, "words": [
                W("l2", 0.0, 0.5, "l2"), W("l1", 1.0, 2.0, "l1"), W("l2b", 5.0, 6.0, "l2")]},
        ]
        out = self.m.stitch_chunks(chunks)
        by_text = {w["text"]: w["speaker"] for w in out}
        self.assertEqual(by_text["l1"], "1")
        self.assertEqual(by_text["l2b"], "2")

    def test_shared_time_below_the_threshold_is_not_a_match(self):
        chunks = [
            # "early" keeps global "1" in the output, so an unmatched s9 must
            # surface as "2" (labels number by first appearance)
            {"start_s": 0.0, "end_s": 10.0, "words": [
                W("early", 1.0, 2.0, "s1"), W("g", 9.9, 10.0, "s1")]},
            {"start_s": 8.0, "end_s": 18.0, "words": [
                W("g", 1.9, 2.0, "s9"), W("h", 5.0, 6.0, "s9")]},
        ]
        out = self.m.stitch_chunks(chunks)
        self.assertEqual({w["text"]: w["speaker"] for w in out}["h"], "2")


if __name__ == "__main__":
    unittest.main()
