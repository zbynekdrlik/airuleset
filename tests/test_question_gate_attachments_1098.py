"""#1098 — ``stop-check-question-quality.sh`` Check 9 must BLOCK an Odoo
BOARD-TASK question that does NOT state what the task's ATTACHMENTS contained.

Incident: a client put the specification as an IMAGE inside a ``project.task``
description (montalu task 1010 "Sieťka robust", att 37652, 20.9.2026); the
stream's text-only board read missed it and parked its ticket (odoo-erp #555)
on ``needs-answer`` asking for the very data the screenshot carried. Owner
21.9.2026 verbatim: „preco tuto ulohu vobec neriesis tam v popise je
screenshot", „aj ostatne ulohy skontroluj popis fotky".

ROZHODNUTÉ #1098 (supervisor, 2026-09-21): Option A — UNIVERSAL. Every Odoo
``❓`` block carrying a ``/odoo/project/<pid>/tasks/<tid>`` task URL (Check 7's
Odoo context + a specific board-task URL) must ALSO carry a ``Prílohy:`` line —
``Prílohy: att <ids> prečítané …`` or ``Prílohy: žiadne`` for a task with no
attachments. An action URL or an explicit no-task statement references no board
task (nothing to read), so is exempt — the trigger is the task URL only.

These are FUNCTIONAL tests (real Stop payload on stdin, reusing the #907
subprocess driver) plus content-locks on the doctrine (rule 15) and the recipe
(the ``project.task`` section of read-with-attachments.md).
"""

import json
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"
DOCTRINE = ROOT / "skills" / "odoo-client-messaging" / "client-board-tasks.md"
RECIPE = ROOT / "skills" / "odoo-client-messaging" / "read-with-attachments.md"


class _HookCase(unittest.TestCase):
    """Feed the Stop hook a payload and read its block/pass verdict (#907 driver)."""

    def _run(self, msg):
        sid = "qatt1098-%s" % uuid.uuid4().hex[:12]
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
# Fixtures — an Odoo board-task question with a /odoo/project/N/tasks/M URL.
# ---------------------------------------------------------------------------

# The incident shape: parking a board task on a question, without stating that
# the task's attachments were read. MUST BLOCK.
TASK_URL_NO_PRILOHY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Klient v popise "
    "úlohy „Sieťka robust“ zadal špecifikáciu, ale rozmery mi nie sú jasné.\n"
    "Odoo task: „Sieťka robust“ (stage: Potrebuje ujasniť) "
    "— https://erp.montalu.cloud/odoo/project/2/tasks/1010\n"
    "\n"
    "• Doplniť rozmery podľa odhadu (odporúčam)\n"
    "• Počkať na klienta\n"
    "\n"
    "❓ NEEDS YOU: aké rozmery má mať sieťka?\n"
)

# Same task, but the attachments were read and their values stated. MUST PASS.
TASK_URL_WITH_ATT = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Klient v popise "
    "úlohy „Sieťka robust“ pridal špecifikáciu ako screenshot.\n"
    "Odoo task: „Sieťka robust“ (stage: Potrebuje ujasniť) "
    "— https://erp.montalu.cloud/odoo/project/2/tasks/1010\n"
    "Prílohy: att 37652 prečítané (rozmery 1575/1924); farbu neuvádza\n"
    "\n"
    "• Použiť rozmery zo screenshotu (odporúčam)\n"
    "• Počkať na farbu\n"
    "\n"
    "❓ NEEDS YOU: akú farbu má mať sieťka (rozmery mám z prílohy)?\n"
)

# A dev task with no attachments states "žiadne". MUST PASS.
TASK_URL_ZIADNE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** V IT-Výroba "
    "module treba rozhodnúť správanie čísla šarže.\n"
    "Odoo task: „Číslo šarže v IT-Výroba“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/503\n"
    "Prílohy: žiadne\n"
    "\n"
    "• Opraviť len pre výrobu (odporúčam)\n"
    "• Opraviť univerzálne\n"
    "\n"
    "❓ NEEDS YOU: opraviť len pre výrobu alebo univerzálne?\n"
)

# ASCII fallback for the diacritic-free "ziadne". MUST PASS.
TASK_URL_ZIADNE_ASCII = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Dev úloha bez "
    "klientskych podkladov.\n"
    "Odoo task: „Refaktor importu“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/777\n"
    "Prilohy: ziadne\n"
    "\n"
    "• Refaktorovať (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: refaktorovať teraz?\n"
)

# An ACTION URL references a menu/view, not a board task — no attachments to
# read, so Check 9 is exempt even with no Prílohy line. MUST PASS (task-URL-only
# trigger boundary — ROZHODNUTÉ consequence "every other #907 case
# byte-identical" forbids requiring Prílohy here).
ACTION_URL_NO_PRILOHY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Problém "
    "s výrobným modulom.\n"
    "Task: https://erp.montalu.cloud/odoo/action-mrp_menu/12\n"
    "\n"
    "• Opraviť (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: opraviť?\n"
)

# An explicit no-task statement references no board task. MUST PASS.
NO_TASK_STATEMENT = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Čisto "
    "technická migrácia schémy.\n"
    "Odoo task neexistuje — ide o čisto technickú úlohu.\n"
    "\n"
    "• Migrovať teraz (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: migrovať teraz?\n"
)

# A non-Odoo question is untouched by Check 9. MUST PASS.
NON_ODOO = (
    "**Otázka — projekt camera-box (kamery pre kostolný prenos):** "
    "OBS reštart pri update.\n"
    "\n"
    "• Reštartovať (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: reštartovať OBS?\n"
)


class TestCheck9Blocks(_HookCase):
    """Check 9 blocks a board-task question that omits the Prílohy: line."""

    def test_task_url_without_attachments_blocks(self):
        r = self._run(TASK_URL_NO_PRILOHY)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)

    def test_block_reason_names_prilohy_check9_and_recipe(self):
        r = self._run(TASK_URL_NO_PRILOHY)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)
        reason = self._reason(r)
        self.assertIn("Prílohy", reason)
        self.assertIn("Check 9", reason)
        self.assertIn("read-with-attachments", reason)


class TestCheck9Passes(_HookCase):
    """Check 9 passes when the block states its attachments or has no board task."""

    def test_task_url_with_att_line_passes(self):
        r = self._run(TASK_URL_WITH_ATT)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_task_url_with_ziadne_passes(self):
        r = self._run(TASK_URL_ZIADNE)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_task_url_with_ziadne_ascii_passes(self):
        r = self._run(TASK_URL_ZIADNE_ASCII)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_action_url_without_attachments_passes(self):
        r = self._run(ACTION_URL_NO_PRILOHY)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_no_task_statement_passes(self):
        r = self._run(NO_TASK_STATEMENT)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_non_odoo_question_unaffected(self):
        r = self._run(NON_ODOO)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)


class TestDoctrineLocks(unittest.TestCase):
    """Content locks (window teeth, #498/#500 pattern) on rule 15 + the recipe."""

    def test_rule15_anchor_present(self):
        text = DOCTRINE.read_text(encoding="utf-8")
        self.assertIn("### 15.", text)

    def test_rule15_negation_phrase(self):
        text = DOCTRINE.read_text(encoding="utf-8")
        self.assertIn("LEN na to, čo v prílohe NIE je", text)

    def test_rule15_web_image_reference(self):
        text = DOCTRINE.read_text(encoding="utf-8")
        self.assertIn("/web/image/", text)

    def test_rule15_work_products_path(self):
        text = DOCTRINE.read_text(encoding="utf-8")
        self.assertIn("work-products", text)

    def test_recipe_has_project_task_section(self):
        text = RECIPE.read_text(encoding="utf-8")
        self.assertIn("project.task", text)
        self.assertIn("res_model", text)
        self.assertIn("/web/image/(\\d+)", text)


if __name__ == "__main__":
    unittest.main()
