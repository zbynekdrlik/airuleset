"""#1185 — a Hotovo/Hotové stage that a STREAM account set is never client
acceptance (closure sweep of 29.9.2026).

Root cause: `discuss_close_guard.has_disposition` accepted ANY non-empty
`Acceptance-cited:` value, so a closure sweep citing only a stage
(„task 1102 v Hotovo") closed a thread-bound ticket on the stream's own move.

Teeth:
  1. GATE — an `Acceptance-cited:` value counts as a disposition only when it
     carries a message reference (`msg <digits>`, also `mail.message <digits>`).
     The odoo-erp#8507 auto-close line counts because it carries
     `msg <auto-close note id>`. A stage-only value is BLOCKED with its own
     reason and its own fix text in the hook. Legacy `Discuss-closed:` /
     `Discuss-defer:` and `Acceptance-defer:` stay value-blind (unchanged).
  2. DOCTRINE — `client-board-stages.md` rule 6 and the `handover-compose.md`
     close-time line state the rule as a statement (negation locked whole,
     matched on a whitespace-normalized section, #498/#500/#799).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_close_guard as g  # noqa: E402

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

MODULE = ROOT / "discuss_close_guard.py"
HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"
MSGDIR = ROOT / "skills" / "odoo-client-messaging"
STAGES = MSGDIR / "client-board-stages.md"
COMPOSE = MSGDIR / "handover-compose.md"

BOUND = "Acceptance-thread: task 1102"
AUTO_CLOSE = ("Acceptance-cited: auto-close after 21 days without reaction "
              "(owner ROZHODNUTÉ 28.9., odoo-erp#8507) msg 555 task 1102")

STAGE_ONLY = (
    "Acceptance-cited: task 1102 v Hotovo",
    "Acceptance-cited: stage Hotovo",
    "Acceptance-cited: moved to done 29.9.",
    "Acceptance-cited: úloha presunutá do Hotovo 29.9.2026",
    "Acceptance-cited: Odoo task 2030 → Hotové",
    "**Acceptance-cited:** task 1102 je v Hotovo",
    # the auto-close SHAPE with the placeholder still in it is no evidence
    "Acceptance-cited: auto-close after 21 days without reaction "
    "(owner ROZHODNUTÉ 28.9., odoo-erp#8507) msg <auto-close note id> task 1102",
)

MSG_CITED = (
    "Acceptance-cited: msg 1742799",
    "Acceptance-cited: msg 1742799 task 1102",
    'Acceptance-cited: vlákno „Zákaznícke e-maily 1" (discuss.channel_288) '
    "/ msg 1739648",
    "Acceptance-cited: mail.message **1941** (PETROVIČOVÁ Alena, 3.9.)",
    "Acceptance-cited: 👍 reakcia klienta na msg #1904",
    "acceptance-cited: MSG 9",
    AUTO_CLOSE,
)


def _issue(body="", comments=()):
    return json.dumps({"body": body, "comments": [{"body": c} for c in comments]})


def _norm(text):
    return " ".join(text.split())


def _section(text, header):
    if header not in text:
        return ""
    rest = text.split(header, 1)[1]
    return rest.split("\n### ", 1)[0]


class TestGateStageOnlyCitation(TestCase):
    def test_stage_only_citation_is_blocked(self):
        for line in STAGE_ONLY:
            with self.subTest(line=line):
                self.assertEqual(
                    g.evaluate_close(_issue(body=BOUND, comments=[line])),
                    "acceptance-cited-without-msg",
                )

    def test_stage_only_citation_is_not_a_disposition(self):
        for line in STAGE_ONLY:
            with self.subTest(line=line):
                self.assertFalse(g.has_disposition(line))

    def test_msg_citation_is_allowed(self):
        for line in MSG_CITED:
            with self.subTest(line=line):
                self.assertIsNone(
                    g.evaluate_close(_issue(body=BOUND, comments=[line]))
                )

    def test_full_auto_close_line_is_allowed(self):
        self.assertIsNone(
            g.evaluate_close(_issue(body=BOUND, comments=[AUTO_CLOSE]))
        )

    def test_msg_elsewhere_on_the_ticket_does_not_rescue_a_stage_citation(self):
        # the msg id must sit ON the Acceptance-cited line — a msg id in prose
        # (the handover note, a quote) is not the acceptance evidence
        self.assertEqual(
            g.evaluate_close(_issue(
                body=BOUND + "\nHandover posted as msg 1742700.",
                comments=["Acceptance-cited: task 1102 v Hotovo"],
            )),
            "acceptance-cited-without-msg",
        )

    def test_any_msg_bearing_citation_line_satisfies(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body=BOUND,
            comments=["Acceptance-cited: task 1102 v Hotovo",
                      "Acceptance-cited: msg 1742799 task 1102"],
        )))

    def test_msg_word_without_digits_is_not_a_reference(self):
        for line in ("Acceptance-cited: msg <message_id> task <task_id>",
                     "Acceptance-cited: msgs checked, Hotovo",
                     "Acceptance-cited: message from client, Hotovo"):
            with self.subTest(line=line):
                self.assertFalse(g.has_disposition(line))

    def test_digits_glued_to_a_word_are_not_a_msg_reference(self):
        self.assertFalse(g.has_disposition("Acceptance-cited: xmsg1742799 Hotovo"))
        self.assertFalse(g.has_disposition("Acceptance-cited: msg 17x Hotovo"))


class TestGateUnchangedForms(TestCase):
    def test_legacy_discuss_closed_value_blind(self):
        for v in ("Discuss-closed: msg 1731999", "Discuss-closed: tacit 3d"):
            with self.subTest(v=v):
                self.assertIsNone(g.evaluate_close(_issue(body=BOUND, comments=[v])))

    def test_legacy_discuss_defer_value_blind(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body="Discuss-thread: 257",
            comments=["Discuss-defer: siblings #4812 still open"],
        )))

    def test_acceptance_defer_unchanged(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body=BOUND, comments=["Acceptance-defer: siblings #4812 still open"],
        )))

    def test_stage_citation_plus_legacy_disposition_is_allowed(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body=BOUND,
            comments=["Acceptance-cited: task 1102 v Hotovo",
                      "Discuss-closed: msg 1731999"],
        )))

    def test_bound_without_any_line_keeps_its_reason(self):
        self.assertEqual(g.evaluate_close(_issue(body=BOUND)),
                         "thread-bound-no-closing-note")

    def test_unbound_ticket_with_stage_citation_is_not_our_concern(self):
        self.assertIsNone(g.evaluate_close(_issue(
            body="ordinary", comments=["Acceptance-cited: task 1102 v Hotovo"],
        )))


class TestCli(TestCase):
    def _run(self, payload):
        p = subprocess.run([sys.executable, str(MODULE)], input=payload,
                           capture_output=True, text=True)
        return p.stdout.strip()

    def test_cli_verdicts(self):
        self.assertEqual(self._run(_issue(body=BOUND)), "BLOCK")
        self.assertEqual(
            self._run(_issue(body=BOUND, comments=["Acceptance-cited: stage Hotovo"])),
            "BLOCK-CITED",
        )
        self.assertEqual(
            self._run(_issue(body=BOUND, comments=["Acceptance-cited: msg 1742799"])),
            "OK",
        )


class TestHook(TestCase):
    def _cwd(self):
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text("# p\n<!-- airuleset:authority=full -->\n")
        return d

    def _run(self, comments):
        fd = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         encoding="utf-8")
        fd.write(_issue(body=BOUND, comments=comments))
        fd.close()
        env = hermetic_hook_env(self)
        env["AIRULESET_DISCUSS_CLOSE_FIXTURE"] = fd.name
        payload = json.dumps({"tool_input": {
            "command": "gh issue close 8507 -R zbynekdrlik/odoo-erp --comment done"}})
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, cwd=self._cwd(),
                              env=env)

    def test_hook_blocks_stage_only_with_its_own_fix_text(self):
        r = self._run(["Acceptance-cited: task 1102 v Hotovo"])
        self.assertEqual(r.returncode, 2, r.stderr)
        err = _norm(r.stderr)
        self.assertIn("a Hotovo/Hotové a STREAM account set is never acceptance "
                      "evidence", err)
        self.assertIn('gh issue comment 8507 --body "Acceptance-cited: msg '
                      '<message-id> task <task-id>"', err)
        self.assertIn("#1185", err)

    def test_hook_allows_msg_citation(self):
        r = self._run(["Acceptance-cited: msg 1742799 task 1102"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_allows_auto_close_line(self):
        r = self._run([AUTO_CLOSE])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_no_line_keeps_the_closing_note_text(self):
        r = self._run([])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("carries no closing-note", r.stderr)
        self.assertNotIn("never acceptance evidence", r.stderr)


class TestDoctrine(TestCase):
    def test_stages_rule6_states_the_rule(self):
        sec = _norm(_section(STAGES.read_text(encoding="utf-8"),
                             '### 6. "Done" stage'))
        for phrase in (
            "A Hotovo/Hotové a STREAM account set is never acceptance evidence",
            "a client message or reaction (`msg <id>`)",
            "the owner's own move after a confirmation",
            "the odoo-erp#8507 auto-close note",
            # ROZHODNUTÉ issuecomment-5894541407: the owner-ruling exit joins msg <id>
            "the close gate rejects an `Acceptance-cited:` with no `msg <id>`, "
            "`meeting <recording id>`, owner `issuecomment-<id>` or Discord message "
            "URL (#1185)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, sec)

    def test_compose_close_line_states_the_rule(self):
        text = _norm(COMPOSE.read_text(encoding="utf-8"))
        self.assertIn("`Acceptance-cited:` bez `msg <id>`/`meeting <id>`/`issuecomment-<id>`/Discord URL BLOKUJE — Hotovo "
                      "nastavené streamom nie je akceptácia (#1185)", text)


class TestReviewFindings(TestCase):
    """Fresh-context review of the #1185 lane (F2/F3/F5/F6)."""

    def test_real_spellings_count_as_a_msg_reference(self):
        # F5: spellings seen on real odoo-erp closes, now accepted
        for line in ("Acceptance-cited: message 1742799",
                     "Acceptance-cited: msg. 1742799",
                     "Acceptance-cited: msg-1742799",
                     "Acceptance-cited: msgs 1742799, 1742800",
                     "Acceptance-cited: msg ids 1742799 1742800",
                     "Acceptance-cited: mail.message(1742799)",
                     "Acceptance-cited: message_id 1742799",
                     "Acceptance-cited: mail_message 1818093",
                     "Acceptance-cited: msg 1742799"):
            with self.subTest(line=line):
                self.assertTrue(g.has_disposition(line))

    def test_a_date_is_not_a_msg_reference(self):
        # F6: a date right after the word is not a message id
        for line in ("Acceptance-cited: Hotovo, správa klienta message 29.9.",
                     "Acceptance-cited: msg 29.9.2026 task 1102 v Hotovo"):
            with self.subTest(line=line):
                self.assertFalse(g.has_disposition(line))

    def test_stages_rule6_names_the_clients_own_move(self):
        # F2: slovnormal's client moves the task himself — cite its tracking msg
        sec = _norm(_section(STAGES.read_text(encoding="utf-8"),
                             '### 6. "Done" stage'))
        self.assertIn("or the client's own move (its tracking `msg <id>`)", sec)

    def test_hook_cited_text_rejects_stream_authored_evidence(self):
        # F3: a msg/move authored by a stream account is not acceptance either
        r = TestHook._run(TestHook(), ["Acceptance-cited: stage Hotovo"])
        self.assertEqual(r.returncode, 2, r.stderr)
        err = _norm(r.stderr)
        self.assertIn("carries no msg <id> (e.g. it cites only a stage", err)
        self.assertIn("a message or stage move a STREAM account authored is "
                      "not acceptance either", err)


if __name__ == "__main__":
    main()
