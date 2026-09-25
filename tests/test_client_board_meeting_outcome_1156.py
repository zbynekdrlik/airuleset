"""#1156 — content locks for client-board-tasks.md rule 16 ("Meeting / decision
outcome → every affected Odoo task").

Owner, 25.9.2026 (montalu stream): agreements from a client meeting must land
in the Odoo board tasks (the state the client, the owner and the stream share),
not only in GitHub issues (the technical record only the stream reads).

Each method pins an OPERATIVE statement of the rule, not a bare noun. The
negation-bearing phrases ("ONLY on GitHub is NOT recorded", "no follower
e-mail", "no ticket numbers") are locked as whole phrases, because a lock that
pins only the nouns still passes when the negation is inverted (#799).
Multi-word phrases are matched against a whitespace-normalized body so a
markdown hard-wrap never hides a phrase (#498/#500).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
MSG = ROOT / "skills" / "odoo-client-messaging"
CORE = MSG / "client-board-tasks.md"
COMPANIONS = ("client-board-stages.md", "client-board-questions.md",
              "client-board-attachments.md")
HISTORY = MSG / "client-board-tasks-history.md"
MEETING_SKILL = ROOT / "skills" / "meeting-analysis" / "SKILL.md"

HEADER = "### 16. Meeting / decision outcome → every affected Odoo task"


def _norm(text):
    return " ".join(text.split())


def _rule16(text):
    """The rule-16 section only (header up to the next ### header or EOF), so a
    phrase elsewhere in the CORE can never satisfy a rule-16 lock."""
    start = text.index(HEADER)
    nxt = text.find("\n### ", start + len(HEADER))
    return _norm(text[start:] if nxt == -1 else text[start:nxt])


class TestMeetingOutcomeRule1156(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.core = CORE.read_text(encoding="utf-8")
        # an absent header yields an empty section, so every lock FAILS (not
        # errors) and names the missing statement.
        cls.rule = _rule16(cls.core) if HEADER in cls.core else ""

    # -- placement: the rule lives in the always-injected CORE, exactly once --
    def test_rule16_lives_only_in_core(self):
        self.assertEqual(self.core.count(HEADER), 1)
        for name in COMPANIONS:
            self.assertNotIn(HEADER, (MSG / name).read_text(encoding="utf-8"),
                             f"rule 16 must live only in the CORE, found in {name}")

    # -- the obligation: every meeting/decision, EACH affected task --
    def test_after_every_meeting_or_decision(self):
        self.assertIn("After every client meeting or decision", self.rule)
        self.assertIn("write what was agreed into EACH affected board task",
                      self.rule)

    # -- the note shape: plain Slovak, bold keywords, rule 7's jargon gate --
    def test_plain_slovak_bold_keywords_no_jargon(self):
        self.assertIn("plain Slovak", self.rule)
        self.assertIn("bold-keyword", self.rule)
        self.assertIn("no ticket numbers, no code (rule 7", self.rule)

    # -- transport: internal note, no follower e-mail, the repo poster --
    def test_internal_note_no_follower_email_repo_poster(self):
        self.assertIn("INTERNAL note", self.rule)
        self.assertIn("no follower e-mail", self.rule)
        self.assertIn("repo poster (rule 13)", self.rule)

    # -- approval: owner approves the text first --
    def test_owner_approves_before_posting(self):
        self.assertIn("the owner approves the text before posting", self.rule)

    # -- the negation: GitHub-only = NOT recorded (#799 teeth) --
    def test_github_stays_technical_record(self):
        self.assertIn("GitHub issues stay the technical record", self.rule)

    def test_github_only_is_not_recorded(self):
        self.assertIn("an agreement recorded ONLY on GitHub is NOT recorded",
                      self.rule)

    # -- origin: the owner's 25.9.2026 words, verbatim --
    def test_owner_origin_quoted_verbatim(self):
        self.assertIn("25.9.2026", self.rule)
        self.assertIn(
            "„chcel by som mať info o tom, čo sa dohodlo na meetingu aj v "
            "úlohách, lebo tu to zasa zabudneš a issues sú v princípe len pre "
            "teba. Tasky sú zdieľaný stav medzi nimi, mnou a tebou.\"",
            self.rule)

    def test_second_owner_quote_kept_in_history(self):
        hist = _norm(HISTORY.read_text(encoding="utf-8"))
        self.assertIn(
            "„a v issues môžeš mať technické veci, ktoré rozumieš len ty, no "
            "nikto z nás im nerozumie.\"", hist)

    # -- meeting-analysis Phase 5 points at the rule --
    def test_meeting_analysis_points_at_rule16(self):
        skill = _norm(MEETING_SKILL.read_text(encoding="utf-8"))
        self.assertIn("client-board-tasks.md` rule 16", skill)
        self.assertIn("EACH affected Odoo board task", skill)


if __name__ == "__main__":
    main()
