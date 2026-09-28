"""#1166 + #1167 — content locks for the montalu owner corrections of 28.9.2026.

#1166 (1), every profile: a handover lists only what the reader can use NOW,
plus changes to things they ALREADY used. Internal rework history and the
removal of a never-delivered feature stay out. Owner, verbatim: „preco spamujes
so zrusenym nahravanim cenika ved to nedostal nikdy tak preco ma vediet ze si
to zrusil?".
#1166 (2), montalu only: the addressed person is @mentioned in the handover
message AND set as the task's `user_ids` assignee. Owner, verbatim: „a musi byt
oznaceny v spravach a taskoch patrik". miva stays with NO assignee.
#1167, montalu only: the odoo-erp#8507 auto-close path. One reminder after 14
days without a reaction, then Hotovo at >= 21 days and >= 7 days after the
reminder. A reaction cancels the countdown. The GitHub ticket closes with the
exact `Acceptance-cited:` line from the ticket body, plus the chatter message
id of the auto-close note. Owner-only Done stays on miva.

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
COMPOSE = MSG / "handover-compose.md"
HISTORY = MSG / "client-board-tasks-history.md"
MSG_SKILL = MSG / "SKILL.md"
LEGACY_SKILL = ROOT / "skills" / "odoo-discuss-xmlrpc" / "SKILL.md"

RULE3 = "### 3. Handover chatter note"
RULE5 = "### 5. Assignee"
RULE6 = '### 6. "Done" stage'

QUOTE_1166_CONTENT = ("preco spamujes so zrusenym nahravanim cenika ved to "
                      "nedostal nikdy tak preco ma vediet ze si to zrusil?")
QUOTE_1166_TAG = "a musi byt oznaceny v spravach a taskoch patrik"
QUOTE_1167 = ("Pochopil som, ze klient odmieta prechadzat tasky vo odo na "
              "verifikaciu takze budeme musiet zvolit nejaky autoclose rezim.")
ACC_LINE = ("Acceptance-cited: auto-close after 21 days without reaction "
            "(owner ROZHODNUTÉ 28.9., odoo-erp#8507)")


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

    def test_compose_closure_carve_out_for_montalu(self):
        # the #799 tacit closure must not run beside the #8507 auto-close
        self.assertIn("Montalu úlohy: NAHRÁDZA ho odoo-erp#8507 auto-close",
                      self.compose)

    def test_compose_keeps_the_already_live_lock(self):
        self.assertIn("Announce ONLY functions that are ALREADY LIVE on the "
                      "client's PROD", self.compose)


class TestMontaluAddresseeTagged1166(TestCase):
    """#1166 (2): montalu = @mention + `user_ids` assignee; miva unchanged."""

    @classmethod
    def setUpClass(cls):
        stages = _read(STAGES)
        cls.rule3 = _section(stages, RULE3)
        cls.rule5 = _section(stages, RULE5)
        cls.core = _read(CORE)
        cls.history = _norm(_read(HISTORY))

    def test_montalu_no_longer_grouped_with_miva_as_no_assignee(self):
        self.assertNotIn("On the **montalu** and **miva** profiles a client "
                         "task carries **NO assignee**", self.rule5)

    def test_miva_still_carries_no_assignee(self):
        self.assertIn("On the **miva** profile a client task carries "
                      "**NO assignee** (`user_ids` empty)", self.rule5)

    def test_montalu_addressee_mentioned_and_assigned_at_handover(self):
        self.assertIn("On the **montalu** profile the person the handover "
                      "addresses is @mentioned in the message AND set as the "
                      "task's assignee (`user_ids`)", self.rule5)

    def test_rule5_dated_and_owner_quote_in_history(self):
        self.assertIn("owner 28.9.2026, #1166", self.rule5)
        self.assertIn(QUOTE_1166_TAG, self.history)

    def test_rule3_note_mentions_the_montalu_addressee(self):
        # rule 3 used to ban every @-mention anchor in the note; montalu now
        # needs one for the addressed person (rule 5).
        self.assertIn("on **montalu** ONE mention anchor for the addressed "
                      "person (rule 5)", self.rule3)
        # ...while the base negation for every other profile stays
        self.assertIn("no `@`-mention anchors", self.rule3)

    def test_montalu_addressee_is_defined(self):
        self.assertIn("the employee the note is written for", self.rule5)

    def test_core_montalu_row_names_the_assignee(self):
        row = _profile_row(self.core, "montalu")
        self.assertTrue(row, "montalu profile row missing")
        self.assertNotIn("NONE", row)
        self.assertIn("the handover addressee", row)

    def test_core_miva_row_unchanged_no_assignee(self):
        row = _profile_row(self.core, "miva")
        self.assertIn("NONE — `user_ids` empty", row)
        self.assertIn("a question: ONLY ONE person (rule 4)", row)
        self.assertNotIn("as montalu", row,
                         "miva must not inherit the new montalu addressee")

    def test_pointer_lines_no_longer_say_no_assignee(self):
        for path in (MSG_SKILL, LEGACY_SKILL):
            self.assertNotIn("no assignee", _read(path),
                             f"{path.name}: stale 'no assignee' pointer")


class TestMontaluAutoClose1167(TestCase):
    """#1167: the odoo-erp#8507 auto-close path, montalu only."""

    @classmethod
    def setUpClass(cls):
        cls.rule6 = _section(_read(STAGES), RULE6)
        cls.core = _read(CORE)
        cls.history = _norm(_read(HISTORY))

    def test_client_confirmation_rule_kept(self):
        self.assertIn("ONLY after the client confirms", self.rule6)

    def test_owner_still_moves_on_miva(self):
        self.assertIn("the **OWNER** on miva", self.rule6)

    def test_montalu_auto_close_path(self):
        self.assertIn("On **montalu** a task in Verifikácia also reaches "
                      "Hotovo by the odoo-erp#8507 auto-close", self.rule6)

    def test_auto_close_timing(self):
        self.assertIn("ONE reminder after 14 days without a reaction", self.rule6)
        self.assertIn("at ≥ 21 days and ≥ 7 days after the reminder", self.rule6)
        # odoo-erp#8507 rule 5: a reaction is a chatter message from anyone
        # but the streams/OdooBot, or a move out of Verifikácia (not an emoji)
        self.assertIn("any non-stream chatter message or a move out of "
                      "Verifikácia cancels the countdown", self.rule6)

    def test_stream_never_moves_to_hotovo_itself(self):
        self.assertIn("never the stream by hand", self.rule6)

    def test_acceptance_cited_line_exact_plus_message_id(self):
        self.assertIn(ACC_LINE + " msg <auto-close note id> task <task_id>",
                      self.rule6)

    def test_rule6_dated_and_quote_in_history(self):
        self.assertIn("owner ROZHODNUTÉ 28.9.2026", self.rule6)
        self.assertIn(QUOTE_1167, self.history)

    def test_core_montalu_done_column_names_auto_close(self):
        row = _profile_row(self.core, "montalu")
        self.assertIn("or the odoo-erp#8507 auto-close (rule 6)", row)

    def test_core_miva_done_column_owner_only(self):
        row = _profile_row(self.core, "miva")
        self.assertIn("the OWNER only, after client confirmation", row)


if __name__ == "__main__":
    main()
