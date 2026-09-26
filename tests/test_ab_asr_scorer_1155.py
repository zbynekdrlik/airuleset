"""#1155: the ASR A/B scorer (ab_asr.py) — its maths and its report.

Three measures per provider over the same slices:
- ERP-term error rate: per term, reference vs hypothesis occurrence counts;
  misses = ref - min(ref, hyp), insertions = hyp - min(ref, hyp),
  rate = (misses + insertions) / reference occurrences;
- speaker-attribution agreement: reference speech time whose hypothesis
  speaker matches under the BEST one-to-one label mapping, / reference
  speech time;
- cost per minute from the documented price table.
"""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from asr_testlib_1155 import import_script, new_tmp, write_wav


def T(spk, a, b, text=""):
    return {"speaker": spk, "start_s": a, "end_s": b, "text": text}


def terms_file(tmp: Path, lines) -> Path:
    p = tmp / "terms.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class TermErrors(unittest.TestCase):
    def setUp(self):
        self.m = import_script("ab_asr")
        self.tmp = new_tmp(self)

    def _terms(self, *lines):
        return self.m.load_terms(terms_file(self.tmp, lines))

    def test_misses_insertions_and_rate(self):
        terms = self._terms("# ERP terms", "", "faktúra", "OP", "ponuka")
        r = self.m.term_errors("faktúra a OP 12 faktúra", "faktura a OP 12 faktúra ponuka",
                               terms)
        self.assertEqual((r["ref"], r["hits"], r["misses"], r["insertions"]), (3, 2, 1, 1))
        self.assertAlmostEqual(r["rate"], 2 / 3)
        self.assertEqual(r["per_term"]["faktúra"], {"ref": 2, "hyp": 1})

    def test_whole_words_only_and_case_insensitive(self):
        terms = self._terms("OP")
        r = self.m.term_errors("op 12 a OP 13", "OPRAVA opakovať Op 12", terms)
        self.assertEqual((r["ref"], r["hits"], r["misses"]), (2, 1, 1))

    def test_regex_term(self):
        terms = self._terms(r"re:ZAK[- ]?\d+")
        r = self.m.term_errors("ZAK-12 a zak 7", "ZAK-12 a žiadna", terms)
        self.assertEqual((r["ref"], r["hits"], r["misses"]), (2, 1, 1))
        self.assertEqual(terms[0].label, r"re:ZAK[- ]?\d+")

    def test_multiword_and_unicode_normalisation(self):
        terms = self._terms("cenová ponuka", "faktúra")
        decomposed = "fakt" + "u\u0301" + "ra"     # u + combining acute (NFD)
        r = self.m.term_errors("cenová ponuka, faktúra", "cenová   ponuka a " + decomposed,
                               terms)
        self.assertEqual((r["ref"], r["hits"]), (2, 2))

    def test_no_reference_occurrences_gives_no_rate(self):
        r = self.m.term_errors("nič", "faktúra", self._terms("faktúra"))
        self.assertIsNone(r["rate"])
        self.assertEqual(r["insertions"], 1)


class SpeakerAgreement(unittest.TestCase):
    def setUp(self):
        self.m = import_script("ab_asr")

    def test_labels_are_mapped_not_compared_by_name(self):
        ref = [T("Peter", 0, 10), T("Jana", 10, 20)]
        hyp = [T("2", 0, 9), T("1", 9, 20)]
        r = self.m.speaker_agreement(ref, hyp)
        self.assertAlmostEqual(r["agreement"], 19 / 20)
        self.assertEqual(r["mapping"], {"Peter": "2", "Jana": "1"})
        self.assertEqual((r["n_ref"], r["n_hyp"]), (2, 2))

    def test_one_hypothesis_speaker_for_everyone(self):
        r = self.m.speaker_agreement([T("A", 0, 10), T("B", 10, 20)], [T("1", 0, 20)])
        self.assertAlmostEqual(r["agreement"], 0.5)

    def test_the_mapping_is_optimal_not_greedy(self):
        # shared time: A-1 5, A-2 4, B-1 4, B-2 0. Greedy takes A-1 then B-2 = 5;
        # the best one-to-one mapping is A-2 + B-1 = 8.
        ref = [T("A", 0, 5), T("A", 5, 9), T("B", 9, 13)]
        hyp = [T("1", 0, 5), T("2", 5, 9), T("1", 9, 13)]
        r = self.m.speaker_agreement(ref, hyp)
        self.assertAlmostEqual(r["matched_s"], 8.0)
        self.assertAlmostEqual(r["agreement"], 8 / 13)

    def test_uncovered_reference_time_counts_against(self):
        r = self.m.speaker_agreement([T("A", 0, 10)], [T("1", 0, 4)])
        self.assertAlmostEqual(r["agreement"], 0.4)

    def test_no_diarization_gives_no_agreement(self):
        r = self.m.speaker_agreement([T("A", 0, 10)], [T("?", 0, 10)])
        self.assertIsNone(r["agreement"])

    def test_many_speakers_still_map(self):
        ref = [T(f"r{i}", i, i + 1) for i in range(12)]
        hyp = [T(f"h{i}", i, i + 1) for i in range(12)]
        self.assertAlmostEqual(self.m.speaker_agreement(ref, hyp)["agreement"], 1.0)


class Cost(unittest.TestCase):
    def test_price_table(self):
        m = import_script("ab_asr")
        self.assertAlmostEqual(m.cost_per_min("soniox"), 0.10 / 60)
        self.assertAlmostEqual(m.cost_per_min("elevenlabs"), (0.22 + 0.05) / 60)
        self.assertAlmostEqual(m.cost_per_min("gemini"), 0.005)
        self.assertAlmostEqual(m.cost_per_min("gemini-vocab"), 0.005)
        self.assertEqual(set(m.PROVIDERS), {"soniox", "elevenlabs", "gemini", "gemini-vocab"})


class AggregateAndReport(unittest.TestCase):
    def setUp(self):
        self.m = import_script("ab_asr")
        self.tmp = new_tmp(self)

    def _slice(self, name, ref_turns, outputs, seconds=60.0):
        d = self.tmp / name
        write_wav(d / "audio.wav", seconds)
        (d / "reference.json").write_text(json.dumps({"turns": ref_turns}), encoding="utf-8")
        for provider, turns in outputs.items():
            o = d / provider
            o.mkdir()
            (o / "speaker_turns.json").write_text(json.dumps(turns), encoding="utf-8")
            (o / "done").write_text("ok")
        return d

    def test_aggregate_pools_counts_not_rates(self):
        rows = [
            {"terms": {"ref": 1, "misses": 1, "insertions": 0},
             "speakers": {"matched_s": 10.0, "ref_speech_s": 10.0}, "minutes": 1.0},
            {"terms": {"ref": 3, "misses": 0, "insertions": 0},
             "speakers": {"matched_s": 0.0, "ref_speech_s": 30.0}, "minutes": 2.0},
        ]
        agg = self.m.aggregate(rows)
        self.assertAlmostEqual(agg["term_rate"], 1 / 4)
        self.assertAlmostEqual(agg["agreement"], 10 / 40)
        self.assertAlmostEqual(agg["minutes"], 3.0)

    def test_score_cli_writes_the_markdown_report(self):
        ref1 = [T("Peter", 0, 30, "Pošli faktúru OP 12."), T("Jana", 30, 60, "Áno, ponuka.")]
        s1 = self._slice("s1", ref1, {
            "soniox": [T("1", 0, 30, "Pošli faktúru OP 12."), T("2", 30, 60, "Áno, ponuka.")],
            "elevenlabs": [T("1", 0, 60, "Pošli fakturu op dvanásť. Áno, ponuka.")],
        })
        terms = terms_file(self.tmp, ["faktúra", "faktúru", "OP", "ponuka"])
        report = self.tmp / "report.md"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.m.main(["score", "--terms", str(terms), "--out", str(report),
                              "--providers", "soniox,elevenlabs", str(s1)])
        self.assertEqual(rc, 0, buf.getvalue())
        md = report.read_text(encoding="utf-8")
        self.assertIn("| Provider |", md)
        soniox_row = next(ln for ln in md.splitlines() if ln.startswith("| soniox |"))
        eleven_row = next(ln for ln in md.splitlines() if ln.startswith("| elevenlabs |"))
        self.assertIn("0.0 %", soniox_row)            # no term errors
        self.assertIn("100.0 %", soniox_row)          # speakers fully agree
        self.assertIn("$0.0017", soniox_row)
        self.assertIn("33.3 %", eleven_row)           # "fakturu": 1 of 3 terms missed
        self.assertIn("50.0 %", eleven_row)           # one voice for two people
        self.assertIn("## Per slice", md)

    def test_score_flags_a_provider_that_did_not_finish(self):
        s1 = self._slice("s1", [T("A", 0, 10, "faktúra")],
                         {"soniox": [T("1", 0, 10, "faktúra")]})
        terms = terms_file(self.tmp, ["faktúra"])
        report = self.tmp / "report.md"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.m.main(["score", "--terms", str(terms), "--out", str(report),
                              "--providers", "soniox,gemini", str(s1)])
        self.assertEqual(rc, 1)
        self.assertIn("missing", report.read_text(encoding="utf-8"))


class RunAndCut(unittest.TestCase):
    def setUp(self):
        self.m = import_script("ab_asr")
        self.tmp = new_tmp(self)

    def test_adapter_commands(self):
        d = self.tmp / "s1"
        cmd = self.m.adapter_cmd("gemini-vocab", d, "sk")
        self.assertTrue(cmd[1].endswith("transcribe_gemini.py"))
        self.assertEqual(cmd[2:], [str(d / "audio.wav"), str(d / "gemini-vocab"),
                                   "sk", "erp", "vocab"])
        self.assertEqual(self.m.adapter_cmd("elevenlabs", d, "sk")[2:],
                         [str(d / "audio.wav"), str(d / "elevenlabs"), "sk", "erp"])
        with self.assertRaises(KeyError):
            self.m.adapter_cmd("whisper", d, "sk")

    def test_run_invokes_the_adapter_per_slice_and_reports_failures(self):
        s1, s2 = self.tmp / "s1", self.tmp / "s2"
        write_wav(s1 / "audio.wav", 1.0)
        write_wav(s2 / "audio.wav", 1.0)
        seen = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return mock.Mock(returncode=0 if Path(cmd[2]).parent.name == "s1" else 1)

        buf = io.StringIO()
        with mock.patch("subprocess.run", fake_run), contextlib.redirect_stdout(buf):
            rc = self.m.main(["run", "--provider", "soniox", str(s1), str(s2)])
        self.assertEqual(rc, 1)
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[0][1].endswith("transcribe_soniox.py"))

    def test_cut_makes_slice_dirs_with_a_reference_template(self):
        src = write_wav(self.tmp / "audio.wav", 30.0)
        out = self.tmp / "slices"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.m.main(["cut", "--audio", str(src), "--at", "0,12", "--len", "10",
                              "--out", str(out)])
        self.assertEqual(rc, 0, buf.getvalue())
        chunks = import_script("asr_chunks")
        for name in ("slice-0000s", "slice-0012s"):
            self.assertAlmostEqual(chunks.wav_duration(out / name / "audio.wav"), 10.0)
            ref = json.loads((out / name / "reference.json").read_text())
            self.assertEqual(ref["turns"], [])
        # an existing, filled reference is never overwritten
        (out / "slice-0000s" / "reference.json").write_text('{"turns": [1]}')
        with contextlib.redirect_stdout(buf):
            self.m.main(["cut", "--audio", str(src), "--at", "0", "--len", "10",
                         "--out", str(out)])
        self.assertEqual(json.loads((out / "slice-0000s" / "reference.json").read_text()),
                         {"turns": [1]})


if __name__ == "__main__":
    unittest.main()
