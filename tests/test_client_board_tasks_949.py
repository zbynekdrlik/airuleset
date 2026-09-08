"""Content locks for client-board-tasks.md (#949 Y2).

Each method asserts a STATEMENT from the acceptance criteria, not a bare
H1 substring — #498/#500 teeth.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
BODY_PATH = ROOT / "skills" / "odoo-client-messaging" / "client-board-tasks.md"


class TestClientBoardTasksDoctrine949(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = BODY_PATH.read_text(encoding="utf-8")

    # Rule 1: task name language
    def test_task_name_client_language(self):
        self.assertIn("CLIENT's language", self.body)
        # "never\nEnglish" wraps across lines — normalize
        normed = " ".join(self.body.split())
        self.assertIn("never English", normed)

    # Rule 2: description format
    def test_description_banned_list(self):
        self.assertIn("BANNED in the description", self.body)
        self.assertIn("channel ids", self.body)
        self.assertIn("version numbers", self.body)

    # Rule 3: verification chatter note
    def test_verification_note_mandatory(self):
        self.assertIn("MANDATORY on stage transition", self.body)

    def test_verification_note_closing_line(self):
        self.assertIn("stačí 👍", self.body)

    def test_verification_note_paired_with_reactions(self):
        self.assertIn("read-reactions.md", self.body)

    # Rule 4: client question stage
    def test_client_question_stage(self):
        self.assertIn("Potrebuje ujasniť", self.body)

    # Rule 5: no assignee — anchored assertion
    def test_no_assignee(self):
        self.assertIn("`user_ids` empty", self.body)
        self.assertIn("No assignee", self.body)

    # Rule 6: Hotovo only after client confirmation + owner authority
    def test_hotovo_client_confirmation(self):
        self.assertIn("ONLY by the OWNER", self.body)
        self.assertIn("client confirms acceptance", self.body)

    # Rule 7: stage table
    def test_stage_table_rows(self):
        for stage in ("ToDo", "Potrebuje ujasniť", "Realizácia",
                      "Verifikácia", "Hotovo"):
            self.assertIn(stage, self.body)

    # Rule 8: posting mechanics
    def test_never_manual_browser_post(self):
        self.assertIn("never a manual browser post", self.body)

    def test_never_edit_or_delete(self):
        self.assertIn("Never edit or delete", self.body)

    # Acceptance-cited order aligned with SKILL.md:35
    def test_acceptance_cited_order(self):
        # msg <message_id> must appear — the order is locked by the
        # template "Acceptance-cited: msg <message_id> task <task_id>"
        self.assertIn("Acceptance-cited: msg <message_id> task <task_id>",
                       self.body)

    # B2: plain prose content vs body_is_html transport
    def test_plain_prose_vs_html_transport(self):
        self.assertIn("PLAIN PROSE", self.body)
        self.assertIn("body_is_html=True", self.body)


if __name__ == "__main__":
    main()
