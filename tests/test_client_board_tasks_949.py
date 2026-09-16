"""Content locks for client-board-tasks.md.

#949 (Y2): the original montalu-shaped doctrine.
#1014 / #1018 / #1024: the fleet-unification rewrite — ONE doctrine with
PER-BOARD PROFILES (montalu + slovnormal), a client-facing-body jargon ban,
"answers only in Odoo/chat, GitHub is a mirror", ATOMIC tasks, the
mixed-communication rule, the canonical stream account name, the reconciled
edit rule, and owner-corrections-go-to-the-rule-not-memory.

Each method asserts a STATEMENT from the acceptance criteria, not a bare
H1 substring — #498/#500 teeth. Multi-word phrases are matched against a
whitespace-normalized body so a markdown hard-wrap never hides a phrase.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
BODY_PATH = ROOT / "skills" / "odoo-client-messaging" / "client-board-tasks.md"


class TestClientBoardTasksDoctrine949(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = BODY_PATH.read_text(encoding="utf-8")
        cls.normed = " ".join(cls.body.split())

    # -- Injection identity (test_situational_injection.py depends on the H1) --
    def test_h1_client_board_tasks(self):
        self.assertIn("Client Board Tasks", self.body)

    # Rule 1: task name language
    def test_task_name_client_language(self):
        self.assertIn("CLIENT's language", self.body)
        self.assertIn("never English", self.normed)

    # Rule 2: description format
    def test_description_banned_list(self):
        self.assertIn("BANNED in the description", self.body)
        self.assertIn("channel ids", self.normed)
        self.assertIn("version numbers", self.normed)

    # Rule 3: verification chatter note
    def test_verification_note_mandatory(self):
        self.assertIn("MANDATORY on the", self.normed)

    def test_verification_note_four_sections(self):
        for section in ("Čo", "Kde", "Čo skúsiť", "stačí 👍"):
            self.assertIn(section, self.body)

    def test_verification_note_paired_with_reactions(self):
        self.assertIn("read-reactions.md", self.body)

    def test_plain_prose_vs_html_transport(self):
        self.assertIn("PLAIN PROSE", self.body)
        self.assertIn("body_is_html=True", self.body)

    # ---- #1014/#1018/#1024: per-board profiles ----
    def test_per_board_profiles_section(self):
        self.assertIn("Per-board profiles", self.body)

    def test_montalu_profile_stages(self):
        for stage in ("ToDo", "Potrebuje ujasniť", "Realizácia",
                      "Verifikácia", "Hotovo"):
            self.assertIn(stage, self.body)

    def test_slovnormal_profile_stages(self):
        self.assertIn("slovnormal", self.body)
        for stage in ("Nové", "V práci", "Na overenie", "Hotové"):
            self.assertIn(stage, self.body)

    def test_slovnormal_assignee_david_grena(self):
        self.assertIn("Dávid Greňa", self.body)

    def test_slovnormal_david_moves_to_done_himself(self):
        # slovnormal's terminal-stage mover is Dávid himself, not the owner
        self.assertIn("moves it to", self.normed)
        self.assertIn("himself", self.normed)

    # Rule 5: no assignee on montalu/miva profiles
    def test_montalu_no_assignee(self):
        self.assertIn("NO assignee", self.normed)
        self.assertIn("`user_ids` empty", self.body)

    # Rule 6: Done only after client confirmation
    def test_done_client_confirmation(self):
        self.assertIn("ONLY after the client confirms", self.normed)

    def test_acceptance_cited_order(self):
        self.assertIn("Acceptance-cited: msg <message_id> task <task_id>",
                      self.normed)

    # Rule 7: no GitHub / technical jargon in a client-facing body (ENFORCED)
    def test_no_github_jargon_in_client_body(self):
        self.assertIn("github.com", self.body)
        self.assertIn("GitHub ticket: #N", self.body)  # the allowlisted marker
        self.assertIn("airuleset:client-body-ok", self.body)  # the bypass

    # Rule 8: answers arrive only in Odoo/chat, GitHub is a mirror
    def test_answers_only_in_odoo_github_is_mirror(self):
        self.assertIn("answers arrive ONLY", self.normed)
        self.assertIn("TRACKING MIRROR", self.normed)
        self.assertIn("needs-answer", self.body)

    # Rule 9: ATOMIC tasks
    def test_atomic_tasks_one_topic(self):
        self.assertIn("one task = one topic", self.normed)
        self.assertIn("téma X pokračuje v úlohe Y", self.normed)

    # Rule 10: mixed communication
    def test_mixed_communication_rule(self):
        self.assertIn("Mixed communication", self.normed)
        self.assertIn("redirect", self.normed)

    # Rule 11: canonical stream account name
    def test_stream_account_name_canonical(self):
        self.assertIn("ZbynekAI <N>", self.body)
        self.assertIn("canonical", self.normed)

    # Rule 12: owner corrections change the rule, not memory
    def test_owner_corrections_to_rule_not_memory(self):
        self.assertIn("gk-request", self.body)
        self.assertIn("#1028", self.body)
        self.assertIn("per-stream", self.normed)

    # Rule 13: posting mechanics + reconciled edit rule
    def test_never_manual_browser_post(self):
        self.assertIn("never a manual browser post", self.normed)

    def test_edit_rule_reconciled(self):
        self.assertIn("Never edit or delete a posted chatter message",
                      self.normed)
        self.assertIn("OWNER-ORDERED cleanup", self.normed)
        self.assertIn("logged", self.normed)


if __name__ == "__main__":
    main()
