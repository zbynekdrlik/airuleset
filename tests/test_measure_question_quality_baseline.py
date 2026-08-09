"""#95 item 9 — the baseline question-quality measurement script.

Reuses the ACTUAL shipped `stop-check-question-quality.sh` as the yardstick
(never a re-implementation of its regexes), so the baseline and any future
post-change measurement are provably the same instrument.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "measure_question_quality_baseline.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_qq_baseline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure_qq_baseline"] = mod
    spec.loader.exec_module(mod)
    return mod


mqq = _load()

CLEAN_BLOCK = (
    "**Otázka — projekt camera-box (kamery a OBS pre kostolný prenos):** "
    "Chystáme aktualizáciu OBS, ktorá si vyžiada reštart. Ako postupovať?\n\n"
    "• Reštartovať teraz (odporúčam) — rýchle, malé riziko krátkeho výpadku.\n"
    "• Počkať do večera — bezpečnejšie, ale reštart nastane neskôr.\n\n"
    "❓ NEEDS YOU: reštartovať teraz alebo počkať?"
)

NO_BRIEFING_BLOCK = "❓ NEEDS YOU: mažem to?"


class TestAssistantTextExtraction(TestCase):
    def _write_transcript(self, entries):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        for e in entries:
            if isinstance(e, str):
                tmp.write(e + "\n")
                continue
            # ensure_ascii=False -- real Claude Code transcripts store the
            # emoji LITERALLY (verified live: grep for the raw ❓ byte
            # sequence hits real corpus files; ❓ hits ZERO), so a
            # fixture using the json.dumps default (ensure_ascii=True,
            # which escapes it to ❓) would defeat the module's own
            # cheap raw-line pre-filter for a reason that has nothing to
            # do with the code under test.
            tmp.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return tmp.name

    def test_extracts_only_assistant_entries_carrying_a_real_marker(self):
        path = self._write_transcript([
            {"type": "user", "message": {"content": "❓ some user text, not a marker"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "no question here at all"}
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": CLEAN_BLOCK}
            ]}},
            {"type": "assistant", "message": {"content": NO_BRIEFING_BLOCK}},  # bare-string shape
            "not even json {{{",
            {"type": "assistant"},  # no message field -- must not crash
        ])
        texts = list(mqq.assistant_texts(path))
        self.assertEqual(len(texts), 2)
        self.assertTrue(any("NEEDS YOU: reštartovať" in t for t in texts))
        self.assertTrue(any(t == NO_BRIEFING_BLOCK for t in texts))

    def test_missing_file_yields_nothing_never_raises(self):
        self.assertEqual(list(mqq.assistant_texts("/no/such/path.jsonl")), [])

    def test_bare_last_line_question_with_neither_needs_you_nor_asked_is_sampled(self):
        # mirrors the REAL hook's own second branch (a bare ❓ opening the
        # LAST non-blank line, with no "NEEDS YOU"/"ASKED" keyword
        # required at all) -- a pre-filter requiring those literal words
        # would silently UNDER-sample this real, hook-recognized shape
        # (#95 item 9 adversarial review, 🟡 finding).
        bare_last_line = "Nejaká rozpracovaná otázka bez šablóny.\n\n❓ mažem to súbor?"
        path = self._write_transcript([
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": bare_last_line}
            ]}},
        ])
        texts = list(mqq.assistant_texts(path))
        self.assertEqual(texts, [bare_last_line])

    def test_a_message_where_the_glyph_appears_but_not_as_a_real_marker_is_not_sampled(self):
        path = self._write_transcript([
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "The plan is done, no ❓ decision needed here at all."}
            ]}},
        ])
        # "❓" appears mid-sentence but does not open the last non-blank
        # line, and is not an ASKED-shaped body line either -- neither of
        # the real hook's two branches would recognize this as a question
        # turn, so it must not be sampled.
        self.assertEqual(list(mqq.assistant_texts(path)), [])


class TestReasonBucketing(TestCase):
    def test_each_real_hook_reason_buckets_correctly(self):
        cases = [
            ("Your ❓ block has no briefing — the away phone reader...", "no-briefing"),
            ("Your ❓ ping crams MULTIPLE decisions into one question...", "multi-decision"),
            ("Your ❓ briefing is a wall of text (900 > 600 chars)...", "wall-of-text"),
            ("Your ❓ question has no option bullets (odrážky)...", "no-options"),
            ("Your ❓ block references an OLD question by allusion...", "history-allusion"),
        ]
        for reason, expected in cases:
            self.assertEqual(mqq.bucket(reason), expected, reason)

    def test_unknown_reason_buckets_as_other(self):
        self.assertEqual(mqq.bucket("some future reason text"), "other")

    def test_empty_reason_buckets_as_unmeasurable(self):
        self.assertEqual(mqq.bucket(""), "unmeasurable")
        self.assertEqual(mqq.bucket(None), "unmeasurable")


class TestClassifyAgainstTheRealHook(TestCase):
    def test_a_clean_block_passes(self):
        blocked, reason = mqq.classify(CLEAN_BLOCK)
        self.assertFalse(blocked, reason)

    def test_a_no_briefing_block_is_blocked_and_bucketed(self):
        blocked, reason = mqq.classify(NO_BRIEFING_BLOCK)
        self.assertTrue(blocked)
        self.assertEqual(mqq.bucket(reason), "no-briefing")


class TestMainEndToEnd(TestCase):
    def test_scans_a_synthetic_projects_dir_and_reports_real_counts(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "-fake-proj"
            proj.mkdir()
            (proj / "s1.jsonl").write_text(
                json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "text", "text": CLEAN_BLOCK}
                ]}}, ensure_ascii=False) + "\n"
                + json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "text", "text": NO_BRIEFING_BLOCK}
                ]}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            sub = proj / "s1" / "subagents"
            sub.mkdir(parents=True)
            (sub / "agent-a1.jsonl").write_text(
                json.dumps({"type": "assistant", "message": {"content": [
                    # a byte-identical repeat of the clean block -- must be
                    # de-duped, not double-counted
                    {"type": "text", "text": CLEAN_BLOCK}
                ]}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            result_json = []
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = mqq.main(["--projects-dir", d, "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            result_json.append(data)
        d0 = result_json[0]
        self.assertEqual(d0["distinct_question_turns_sampled"], 2)
        self.assertEqual(d0["blocked"], 1)
        self.assertEqual(d0["clean"], 1)
        self.assertEqual(d0["blocked_by_reason"], {"no-briefing": 1})


if __name__ == "__main__":
    main()
