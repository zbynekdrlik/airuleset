"""#936 — ``stop-check-question-quality.sh`` Check 8 must BLOCK an approval
question that references a file/ticket pointer instead of carrying the
proposed client message body inline.

Owner escalation (montalu 2026-09-07): the stream twice pointed at a file
path / ticket comment instead of inlining the proposed client text.

These are FUNCTIONAL tests (real Stop payload on stdin) plus content-locks on
the Check 8 implementation.
"""

import json
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"

# Curly quotes used in thread/task names throughout fixtures.
_LQ = "„"  # „
_RQ = "”"  # "


class _HookCase(unittest.TestCase):
    """Feed the Stop hook a payload and read its block/pass verdict."""

    def _run(self, msg):
        sid = "qapprove936-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
            "/tmp/claude-lastq-refs-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"]


# ---------------------------------------------------------------------------
# Incident shapes — MUST BLOCK (approval + pointer, no inline body)
# ---------------------------------------------------------------------------

# Shape 1: approval pointing at a work-products file
APPROVE_FILE_POINTER = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta do Discuss vlákna "
    + _LQ + "Tabula objednávok 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_288\n"
    "Odoo task: " + _LQ + "Tabula objednávok" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/503\n"
    "\n"
    "Text správy je v súbore ~/work-products/drafts-montalu-288.md\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš správu pre klienta?\n"
)

# Shape 2: approval pointing at a ticket comment
APPROVE_TICKET_POINTER = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta.\n"
    "Vlákno: " + _LQ + "Zákaznícky portál 3" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_290\n"
    "Odoo task: " + _LQ + "Zákaznícky portál" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/510\n"
    "\n"
    "Navrhovaný text je na tikete v komentári.\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš odpoveď pre klienta?\n"
)

# Shape 3: approval pointing at /tmp draft
APPROVE_TMP_POINTER = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Odpoveď klientovi.\n"
    "Vlákno: " + _LQ + "Objednávky 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_291\n"
    "Odoo task: " + _LQ + "Objednávky" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/511\n"
    "\n"
    "Pozri draft v /tmp/claude-1000/drafts/montalu-reply.md\n"
    "\n"
    "• Poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš správu?\n"
)

# Shape 4: "v drafte" redirect phrase
APPROVE_DRAFT_PHRASE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Uzavieracia správa.\n"
    "Vlákno: " + _LQ + "Výroba 2" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_295\n"
    "Odoo task: " + _LQ + "Výroba uzavretie" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/520\n"
    "\n"
    "Text je v drafte, pozri komentár na tikete.\n"
    "\n"
    "• Poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš odpoveď?\n"
)


# ---------------------------------------------------------------------------
# Compliant shapes — MUST PASS (inline body present)
# ---------------------------------------------------------------------------

# Fenced code block with the message body
APPROVE_WITH_FENCED_BODY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta do Discuss vlákna "
    + _LQ + "Tabula objednávok 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_288\n"
    "Odoo task: " + _LQ + "Tabula objednávok" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/503\n"
    "\n"
    "Navrhovaný text správy:\n"
    "\n"
    "```\n"
    "Dobrý deň,\n"
    "\n"
    "ďakujeme za Vašu objednávku. Potvrdenie "
    "Vám posielame v prílohe.\n"
    "\n"
    "S pozdravom,\n"
    "ZbynekAI 1\n"
    "```\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš správu pre klienta?\n"
)

# Blockquote with the message body
APPROVE_WITH_BLOCKQUOTE_BODY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta.\n"
    "Vlákno: " + _LQ + "Zákaznícky portál 3" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_290\n"
    "Odoo task: " + _LQ + "Zákaznícky portál" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/510\n"
    "\n"
    "Navrhovaný text:\n"
    "\n"
    "> Dobrý deň,\n"
    "> ďakujeme za Vašu objednávku. Potvrdenie\n"
    "> Vám posielame v prílohe.\n"
    "> S pozdravom, ZbynekAI 1\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš odpoveď pre klienta?\n"
)

# Ordinary question mentioning a file path but NOT an approval — PASS
ORDINARY_QUESTION_WITH_PATH = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "V module company_montalu_install_config som narazil na chybu pri "
    "importe z ~/devel/odoo-erp/addons/company_montalu_install_config/"
    "data.md\n"
    "\n"
    "Odoo task: " + _LQ + "Import dát" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/530\n"
    "\n"
    "• Opraviť len pre montalu (odporúčam)\n"
    "• Opraviť univerzálne\n"
    "\n"
    "❓ NEEDS YOU: opraviť import len pre montalu alebo "
    "univerzálne?\n"
)

# Shape 5: path-only pointer, no redirect phrase — pins POINTER_PATH_RX
APPROVE_PATH_ONLY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta.\n"
    "Vlákno: " + _LQ + "Faktúry 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_300\n"
    "Odoo task: " + _LQ + "Faktúry" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/550\n"
    "\n"
    "Text: ~/work-products/reply-300.md\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš správu?\n"
)

# Approval intent + NO pointer + NO body → PASS (pins the pointer gate)
APPROVE_NO_POINTER_NO_BODY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta.\n"
    "Vlákno: " + _LQ + "Faktúry 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_300\n"
    "Odoo task: " + _LQ + "Faktúry" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/550\n"
    "\n"
    "Mám pripravenú odpoveď.\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš správu?\n"
)

# Approval WITHOUT any pointer — PASS (no file/ticket redirect present)
APPROVE_NO_POINTER = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** "
    "Pripravil som odpoveď pre klienta.\n"
    "Vlákno: " + _LQ + "Tabula objednávok 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_288\n"
    "Odoo task: " + _LQ + "Tabula objednávok" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/503\n"
    "\n"
    "```\n"
    "Dobrý deň, potvrdenie Vám posielame v prílohe. "
    "S pozdravom, ZbynekAI 1\n"
    "```\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schváliš správu pre klienta?\n"
)


class TestApprovalBodyBlock(_HookCase):
    """The incident shape: approval pointing at a file/ticket, no body."""

    def test_file_pointer_blocked(self):
        r = self._run(APPROVE_FILE_POINTER)
        self.assertTrue(self._blocked(r),
                        "approval with ~/work-products pointer should BLOCK")
        self.assertIn("#936", self._reason(r))

    def test_ticket_pointer_blocked(self):
        r = self._run(APPROVE_TICKET_POINTER)
        self.assertTrue(self._blocked(r),
                        "approval 'na tikete v komentari' should BLOCK")
        self.assertIn("#936", self._reason(r))

    def test_tmp_pointer_blocked(self):
        r = self._run(APPROVE_TMP_POINTER)
        self.assertTrue(self._blocked(r),
                        "approval pointing at /tmp/ draft should BLOCK")
        self.assertIn("#936", self._reason(r))

    def test_draft_phrase_blocked(self):
        r = self._run(APPROVE_DRAFT_PHRASE)
        self.assertTrue(self._blocked(r),
                        "approval with 'v drafte' redirect should BLOCK")
        self.assertIn("#936", self._reason(r))

    def test_path_only_no_phrase_blocked(self):
        """Pins POINTER_PATH_RX — no redirect phrase, just a ~/path."""
        r = self._run(APPROVE_PATH_ONLY)
        self.assertTrue(self._blocked(r),
                        "approval with path-only pointer should BLOCK")
        self.assertIn("#936", self._reason(r))


class TestApprovalBodyPass(_HookCase):
    """Compliant shapes: inline body present or no approval intent."""

    def test_fenced_body_passes(self):
        r = self._run(APPROVE_WITH_FENCED_BODY)
        self.assertFalse(self._blocked(r),
                         "approval with fenced body should PASS")

    def test_blockquote_body_passes(self):
        r = self._run(APPROVE_WITH_BLOCKQUOTE_BODY)
        self.assertFalse(self._blocked(r),
                         "approval with blockquote body should PASS")

    def test_ordinary_question_with_path_passes(self):
        r = self._run(ORDINARY_QUESTION_WITH_PATH)
        self.assertFalse(self._blocked(r),
                         "ordinary question mentioning a path should PASS")

    def test_approval_without_pointer_passes(self):
        r = self._run(APPROVE_NO_POINTER)
        self.assertFalse(self._blocked(r),
                         "approval with inline body and no pointer PASS")

    def test_no_pointer_no_body_passes(self):
        """Pins the pointer gate: approval + no pointer + no body = PASS."""
        r = self._run(APPROVE_NO_POINTER_NO_BODY)
        self.assertFalse(self._blocked(r),
                         "approval with no pointer and no body should PASS")


class TestApprovalBodyLock(unittest.TestCase):
    """Content-lock: Check 8 exists in the hook and names the violation."""

    def test_hook_has_approvebody_violation(self):
        src = HOOK.read_text()
        self.assertIn('VIOLATION="approvebody"', src)
        self.assertIn("APPROVE_INTENT_RX", src)
        self.assertIn("POINTER_PATH_RX", src)
        self.assertIn("POINTER_PHRASE_RX", src)

    def test_hook_has_approvebody_reason(self):
        src = HOOK.read_text()
        self.assertIn("approvebody)", src)
        self.assertIn("#936", src)

    def test_40_char_threshold(self):
        """The 40-char threshold must be present."""
        src = HOOK.read_text()
        self.assertIn("-lt 40", src)


class TestApprovalBodyBoundary(_HookCase):
    """Boundary: 39 chars body = BLOCK, 40 chars body = PASS."""

    def _msg_with_body(self, body_text):
        return (
            "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):**"
            " Odpoveď pre klienta.\n"
            "Vlákno: " + _LQ + "Test 1" + _RQ + " "
            "— https://erp.montalu.cloud/odoo/discuss?active_id="
            "discuss.channel_299\n"
            "Odoo task: " + _LQ + "Test" + _RQ + " "
            "(stage: V riešení) "
            "— https://erp.montalu.cloud/odoo/project/4/tasks/599\n\n"
            "Text je v súbore ~/draft.md\n\n"
            "Navrhovaný text:\n\n"
            "```\n"
            + body_text + "\n"
            "```\n\n"
            "• Schváliť a poslať (odporúčam)\n"
            "• Upraviť\n\n"
            "❓ NEEDS YOU: schváliš správu?\n"
        )

    def test_39_chars_blocks(self):
        body = "A" * 39
        r = self._run(self._msg_with_body(body))
        self.assertTrue(self._blocked(r),
                        "39-char body should still BLOCK")

    def test_40_chars_passes(self):
        body = "A" * 40
        r = self._run(self._msg_with_body(body))
        self.assertFalse(self._blocked(r),
                         "40-char body should PASS")


if __name__ == "__main__":
    unittest.main()
