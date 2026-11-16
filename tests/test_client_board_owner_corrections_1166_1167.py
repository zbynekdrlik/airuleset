"""#1166 + #1167 — content locks for the owner corrections of 28.9.2026.

#1166 (1), every profile: a handover lists only what the reader can use NOW,
plus changes to things they ALREADY used. Internal rework history and the
removal of a never-delivered feature stay out. Owner, verbatim: „preco spamujes
so zrusenym nahravanim cenika ved to nedostal nikdy tak preco ma vediet ze si
to zrusil?".
#1166 (2) and #1167 were montalu only (the addressee assignee and the
odoo-erp#8507 auto-close). The montalu client is gone (owner 11.11.2026), and
#1306 removed that profile from the client-board doctrine, so those locks are
now INVERSE locks: the montalu row, the auto-close paragraph and the compose
carve-out stay gone, and miva keeps NO assignee + an owner-only Done.

Each lock pins an OPERATIVE statement inside the rule's own section, not a bare
noun anywhere in the file. Negations are locked as whole phrases, so an
inverted rule fails the lock (#799). Matching runs on a whitespace-normalized
body, so a markdown hard-wrap never hides a phrase (#498/#500).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
MSG = ROOT / "skills" / "odoo-client-messaging"
CORE = MSG / "client-board-tasks.md"
STAGES = MSG / "client-board-stages.md"
QUESTIONS = MSG / "client-board-questions.md"
ATTACHMENTS = MSG / "client-board-attachments.md"
COMPOSE = MSG / "handover-compose.md"
HISTORY = MSG / "client-board-tasks-history.md"

RULE3 = "### 3. Handover chatter note"
RULE5 = "### 5. Assignee"
RULE6 = '### 6. "Done" stage'

QUOTE_1166_CONTENT = ("preco spamujes so zrusenym nahravanim cenika ved to "
                      "nedostal nikdy tak preco ma vediet ze si to zrusil?")


def _norm(text):
    return " ".join(text.split())


def _read(path):
    return path.read_text(encoding="utf-8")


def _section(text, header):
    """The section under ``header`` up to the next ``### `` header, or EOF.
    An absent header yields "" so every lock FAILS and names what is missing."""
    if header not in text:
        return ""
    start = text.index(header)
    nxt = text.find("\n### ", start + len(header))
    return _norm(text[start:] if nxt == -1 else text[start:nxt])


def _profile_row(core, board):
    for line in core.splitlines():
        if line.startswith(f"| **{board}**"):
            return _norm(line)
    return ""


class TestHandoverContentRule1166(TestCase):
    """#1166 (1): every profile, both the task-chatter note and the compose rule."""

    @classmethod
    def setUpClass(cls):
        cls.rule3 = _section(_read(STAGES), RULE3)
        cls.compose = _norm(_read(COMPOSE))
        cls.history = _norm(_read(HISTORY))

    def test_rule3_lists_only_usable_now_plus_changes_to_used(self):
        self.assertIn("only what the reader can use NOW", self.rule3)
        self.assertIn("changes to what they ALREADY used", self.rule3)

    def test_rule3_keeps_rework_and_never_delivered_removal_out(self):
        self.assertIn("internal rework history", self.rule3)
        self.assertIn("removal of a never-delivered feature stay OUT",
                      self.rule3)

    def test_rule3_content_rule_holds_on_every_profile(self):
        self.assertIn("every profile", self.rule3)

    def test_rule3_dated_and_owner_quote_in_history(self):
        # The verbatim quote lives in the provenance file (the #1156
        # precedent): the STAGES companion has no co-fire budget left for it.
        self.assertIn("owner 28.9.2026, #1166", self.rule3)
        self.assertIn(QUOTE_1166_CONTENT, self.history)

    def test_compose_carries_the_operative_rule(self):
        # Discuss / any other channel: the same content rule, pointing at
        # rule 3 where the owner quote lives.
        self.assertIn("only what the reader can use NOW", self.compose)
        self.assertIn("no never-delivered removals", self.compose)
        self.assertIn("`client-board-stages.md` rule 3", self.compose)

    def test_compose_keeps_the_already_live_lock(self):
        self.assertIn("Announce ONLY functions that are ALREADY LIVE on the "
                      "client's PROD", self.compose)


class TestMivaAssignee(TestCase):
    """Rule 5: miva carries NO assignee; rule 3's note carries no anchor."""

    @classmethod
    def setUpClass(cls):
        stages = _read(STAGES)
        cls.rule3 = _section(stages, RULE3)
        cls.rule5 = _section(stages, RULE5)
        cls.core = _read(CORE)

    def test_miva_still_carries_no_assignee(self):
        self.assertIn("On the **miva** profile a client task carries "
                      "**NO assignee** (`user_ids` empty)", self.rule5)

    def test_no_assignee_a_profile_does_not_name(self):
        self.assertIn("Never add an assignee a profile does not name",
                      self.rule5)

    def test_rule3_note_has_no_mention_anchor_on_any_profile(self):
        self.assertIn("no `@`-mention anchors, no `partner_ids`);", self.rule3)
        self.assertNotIn("ONE mention anchor", self.rule3)

    def test_core_miva_row_unchanged_no_assignee(self):
        row = _profile_row(self.core, "miva")
        self.assertIn("NONE — `user_ids` empty", row)
        self.assertIn("a question: ONLY ONE person (rule 4)", row)


class TestMivaDone(TestCase):
    """Rule 6: Done only after the client confirms; the OWNER moves it."""

    @classmethod
    def setUpClass(cls):
        cls.rule6 = _section(_read(STAGES), RULE6)
        cls.core = _read(CORE)

    def test_client_confirmation_rule_kept(self):
        self.assertIn("ONLY after the client confirms", self.rule6)

    def test_never_on_the_streams_own_judgment(self):
        self.assertIn("never on the stream's own judgment. WHO moves it: the "
                      "**OWNER** on miva.", self.rule6)

    def test_core_miva_done_column_owner_only(self):
        row = _profile_row(self.core, "miva")
        self.assertIn("the OWNER only, after client confirmation", row)


class TestMontaluProfileRemoved1306(TestCase):
    """#1306: the montalu client is gone; its profile left the doctrine."""

    def test_no_montalu_profile_row(self):
        self.assertEqual(_profile_row(_read(CORE), "montalu"), "")

    def test_injected_files_never_name_montalu(self):
        for path in (CORE, STAGES, QUESTIONS, ATTACHMENTS):
            with self.subTest(path=path.name):
                self.assertNotIn("montalu", _read(path).lower())

    def test_no_auto_close_path(self):
        for path in (CORE, STAGES):
            with self.subTest(path=path.name):
                text = _read(path)
                self.assertNotIn("8507", text)
                self.assertNotIn("auto-close", text)

    def test_no_montalu_assignee_rule(self):
        rule5 = _section(_read(STAGES), RULE5)
        self.assertNotIn("the employee the note is written for", rule5)
        self.assertNotIn("set as the task's assignee", rule5)

    def test_compose_has_no_auto_close_carve_out(self):
        compose = _norm(_read(COMPOSE))
        self.assertNotIn("Montalu úlohy", compose)
        self.assertNotIn("8507", compose)


if __name__ == "__main__":
    main()
