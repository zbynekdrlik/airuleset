"""#166 -- mention-vs-use classifier for the `🏁 BACKLOG EMPTY:` marker #159
introduced in the three `/goal` STOP CONDITIONS templates
(`skills/autopilot/SKILL.md`).

THE PROBLEM #166 measured (see the issue comment posted before this commit,
and `.claude/rules/airuleset-internals.md`'s new CLAUDE_CODE_STOP_HOOK_BLOCK_CAP
entry): a Stop hook cannot durably PREVENT a false "backlog empty" stop --
Claude Code overrides ANY blocking Stop hook after `CLAUDE_CODE_STOP_HOOK_
BLOCK_CAP` (default 8) consecutive blocked Stop events and force-ends the
turn anyway. So #166 does NOT ship an enforcing Stop hook. What it DOES ship
is the one piece of reusable value the ticket's own re-scope branch pointed
at #160 for: a classifier that tells a genuine `🏁 BACKLOG EMPTY:` CLAIM
(the marker starting its own line, in the live turn) apart from a MENTION
of the marker string (inside a fenced code block, an inline backtick span,
or mid-line prose) -- exactly the self-tripping risk the ticket named: "any
session working on this protocol writes that string -- including the one
that implemented #159." #160's watchdog-side "verify before accepting
achieved" fix can import this directly instead of re-deriving the same
mention-vs-use logic.

This file locks the classifier in isolation, with a small hand-authored,
portable fixture corpus (no dependency on real local transcripts -- those
are covered separately, on-demand, by `scripts/replay_backlog_marker_
corpus.py` and its own bounded, git-corpus-only test file, since real
Claude Code transcripts are private/machine-local and not reproducible on a
fresh clone or CI-less box).
"""
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backlog_marker_gate as bmg                          # noqa: E402


class TestMarkerConstant(TestCase):
    def test_marker_matches_the_shipped_goal_template(self):
        # Never hardcode a second copy of the marker literal in a test --
        # scrape it from the actual shipped SKILL.md text so this can never
        # silently drift from what #159 actually ships (same discipline as
        # tests/test_goal_backlog_proof.py's own MARKER constant).
        skill = (Path(__file__).resolve().parent.parent
                 / "skills" / "autopilot" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(bmg.MARKER, skill)


class TestGenuineUseIsDetected(TestCase):
    def test_marker_at_line_start_is_present(self):
        text = "gh issue list ... prints 0\ngh run list ... prints success\n🏁 BACKLOG EMPTY: 0 open, main green\n✅ DONE: backlog empty"
        present, reason = bmg.classify_backlog_empty_claim(text)
        self.assertTrue(present, reason)

    def test_marker_at_line_start_with_leading_whitespace_is_present(self):
        text = "some proof output\n   🏁 BACKLOG EMPTY: 0 open, main green\n✅ DONE: done"
        present, _ = bmg.classify_backlog_empty_claim(text)
        self.assertTrue(present)

    def test_sole_line_message_is_present(self):
        present, _ = bmg.classify_backlog_empty_claim("🏁 BACKLOG EMPTY: 0 open, main green")
        self.assertTrue(present)

    def test_genuine_use_found_even_alongside_noise_elsewhere(self):
        text = (
            "Earlier I discussed the `🏁 BACKLOG EMPTY:` marker's format.\n"
            "gh issue list ... prints 0\n"
            "🏁 BACKLOG EMPTY: 0 open, main green\n"
        )
        present, _ = bmg.classify_backlog_empty_claim(text)
        self.assertTrue(present)


class TestMentionIsNotUse(TestCase):
    def test_inline_backtick_mention_is_absent(self):
        text = "Give backlog-completion its own terminator (e.g. `🏁 BACKLOG EMPTY:`)."
        present, reason = bmg.classify_backlog_empty_claim(text)
        self.assertFalse(present, reason)

    def test_fenced_code_block_mention_is_absent(self):
        text = (
            "The template says:\n"
            "```\n"
            "🏁 BACKLOG EMPTY: 0 open, main green\n"
            "```\n"
            "-- do not satisfy the goal by pasting this example.\n"
        )
        present, _ = bmg.classify_backlog_empty_claim(text)
        self.assertFalse(present)

    def test_midline_prose_mention_is_absent(self):
        text = "I will now write 🏁 BACKLOG EMPTY: 0 open once I have real proof."
        present, _ = bmg.classify_backlog_empty_claim(text)
        self.assertFalse(present)

    def test_pure_discussion_with_no_marker_at_all_is_absent(self):
        text = "The backlog is not empty yet; several tickets remain open."
        present, _ = bmg.classify_backlog_empty_claim(text)
        self.assertFalse(present)

    def test_empty_and_none_text_is_absent_and_does_not_raise(self):
        self.assertFalse(bmg.classify_backlog_empty_claim("")[0])
        self.assertFalse(bmg.classify_backlog_empty_claim(None)[0])


class TestNaiveVsCarefulDelta(TestCase):
    """The comparison the corpus-replay script needs: naive substring
    matching is a strict superset of the careful (mention-vs-use-aware)
    classifier -- every case the careful classifier accepts, naive also
    accepts, so the only possible disagreement direction here is naive-True/
    careful-False (a false positive naive would have produced)."""

    def test_naive_is_superset_of_careful_on_the_fixture_corpus(self):
        cases = [
            "🏁 BACKLOG EMPTY: 0 open, main green",
            "the marker is `🏁 BACKLOG EMPTY:`",
            "```\n🏁 BACKLOG EMPTY: 0 open\n```",
            "I will write 🏁 BACKLOG EMPTY: soon",
            "nothing here",
            "",
        ]
        for text in cases:
            careful, _ = bmg.classify_backlog_empty_claim(text)
            naive = bmg.naive_marker_present(text)
            if careful:
                self.assertTrue(naive, "careful=True but naive=False for %r" % text)


if __name__ == "__main__":
    main()
