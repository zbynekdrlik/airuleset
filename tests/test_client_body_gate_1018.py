"""Tests for gates.clientbody (#1018 / #1014 / #1024).

Two disjoint checks in ONE client-board-doctrine module:
  * classify_client_body  — blocks GitHub/technical jargon in a client-facing
    message body (message_post / mail.message.write / odoo_post /
    odoo-task-sync.py post-message). Scans only the EXTRACTED body, never the
    surrounding posting code, and fails OPEN when it cannot extract a body — so
    a legitimate client message is never falsely blocked.
  * classify_memory_write — refuses a NEW per-stream memory whose SUBJECT is
    client-board chatter doctrine, pointing to client-board-tasks.md + gk-request
    (owner corrections change the RULE, not a per-stream memory — #1014/#1028).
"""

from unittest import TestCase, main

from gates import clientbody


class TestClientBodyJargonGate(TestCase):

    def _blocked(self, content):
        blocked, reason = clientbody.classify_client_body(content)
        return blocked, reason

    # ---- BLOCK: developer jargon / GitHub refs in a client-facing body ----
    def test_github_url_in_message_body_blocked(self):
        c = ("""python3 -c "models.execute_kw(db, uid, key, 'project.task',"""
             """ 'message_post', [[42]], {'body': '<p>Pozri github.com/zbynekdrlik/odoo-erp/issues/1</p>', 'body_is_html': True})" """)
        blocked, reason = self._blocked(c)
        self.assertTrue(blocked, reason)

    def test_bare_issue_ref_in_body_blocked(self):
        c = "scripts/odoo-task-sync.py post-message 42 \"<p>Opravené, detaily v #7110 (importer retry)</p>\""
        blocked, reason = self._blocked(c)
        self.assertTrue(blocked, reason)

    def test_commit_jargon_in_body_blocked(self):
        c = "odoo_post.py --task-id 42 --body \"<p>Nasadené v commit abc123 na branch dev</p>\""
        blocked, reason = self._blocked(c)
        self.assertTrue(blocked, reason)

    def test_pr_jargon_in_body_blocked(self):
        c = "channel.message_post(body='<p>Hotové v PR #12, po merge to nasadíme</p>', body_is_html=True)"
        blocked, reason = self._blocked(c)
        self.assertTrue(blocked, reason)

    def test_block_reason_is_slovak(self):
        c = "scripts/odoo-task-sync.py post-message 42 \"<p>detaily na github.com/x/y</p>\""
        blocked, reason = self._blocked(c)
        self.assertTrue(blocked)
        # a Slovak reason — at least one distinctive SK word must be present
        low = reason.lower()
        self.assertTrue(any(w in low for w in ("klient", "správ", "github", "žiadn", "technick")), reason)

    def test_own_message_rewrite_jargon_blocked(self):
        c = "models.execute_kw(db, uid, key, 'mail.message', 'write', [[999], {'body': '<p>viď commit v repozitári</p>'}])"
        blocked, reason = self._blocked(c)
        self.assertTrue(blocked, reason)

    # ---- ALLOW: sanctioned markers / clean bodies / non-posting ----
    def test_github_ticket_marker_allowed(self):
        # the repo-sanctioned linking marker odoo-task-sync depends on
        c = "scripts/odoo-task-sync.py post-message 42 \"GitHub ticket: #7110\""
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)

    def test_description_trailer_reference_allowed(self):
        c = "channel.message_post(body='<p>Mapa trás je nasadená. (GitHub #1234)</p>', body_is_html=True)"
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)

    def test_clean_discuss_reply_allowed(self):
        c = ("channel.message_post(body='<p>Dobrý deň, zoznam zákazníkov je teraz vpravo "
             "s vyhľadávaním. Skúste a dajte 👍. ZbynekAI 3</p>', body_is_html=True)")
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)

    def test_bypass_marker_allowed(self):
        c = ("scripts/odoo-task-sync.py post-message 42 \"<p>detaily v #7110</p>\"  "
             "# airuleset:client-body-ok legacy migration note")
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)

    def test_non_posting_content_allowed(self):
        c = "git commit -m 'fix the importer #7110' && gh pr merge 5"
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)

    def test_posting_code_with_cr_commit_and_clean_body_allowed(self):
        # env.cr.commit() is posting CODE, not the client body — must not block
        c = ("env['project.task'].browse(42).message_post(body='<p>Nasadené. stačí 👍</p>', "
             "body_is_html=True)\nenv.cr.commit()")
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)

    def test_message_post_without_extractable_body_fails_open(self):
        # body built in a variable — cannot classify -> allow (never false-block)
        c = "html = build_body(task)\nchannel.message_post(body=html, body_is_html=True)"
        blocked, reason = self._blocked(c)
        self.assertFalse(blocked, reason)


class TestClientBoardMemoryGuard(TestCase):

    MEM = "/home/u/.claude/projects/-home-u-x/memory/feedback_odoo_chatter.md"
    # An unrelated memory must use an unrelated FILENAME too — the filename is a
    # SUBJECT signal (#1028), so a memory literally named ..._odoo_chatter.md IS
    # about odoo chatter and is correctly blocked regardless of its body.
    MEM_UNRELATED = "/home/u/.claude/projects/-home-u-x/memory/feedback_dev2_build_box.md"
    NOTMEM = "/home/u/devel/x/notes.md"

    def test_client_board_memory_blocked(self):
        content = ("---\ntype: feedback\ndescription: Odoo chatter komentár na board — "
                   "Hotovo posúva owner\n---\n# note\nbody")
        blocked, reason = clientbody.classify_memory_write(self.MEM, content)
        self.assertTrue(blocked, reason)
        self.assertIn("client-board-tasks.md", reason)
        self.assertIn("gk-request", reason)

    def test_unrelated_memory_allowed(self):
        content = ("---\ntype: feedback\ndescription: dev2 build box for Android "
                   "gradle heavy jobs\n---\n# note\nbody")
        blocked, reason = clientbody.classify_memory_write(self.MEM_UNRELATED, content)
        self.assertFalse(blocked, reason)

    def test_non_memory_path_allowed(self):
        content = "Odoo chatter komentár board Hotovo client task"
        blocked, reason = clientbody.classify_memory_write(self.NOTMEM, content)
        self.assertFalse(blocked, reason)

    def test_memory_guard_bypass_allowed(self):
        content = ("---\ndescription: Odoo chatter board note\n---\n"
                   "# airuleset:client-board-memory-ok genuinely unrelated\nbody")
        blocked, reason = clientbody.classify_memory_write(self.MEM, content)
        self.assertFalse(blocked, reason)


if __name__ == "__main__":
    main()
