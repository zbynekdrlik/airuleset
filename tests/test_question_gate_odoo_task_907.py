"""#907 — ``stop-check-question-quality.sh`` Check 7 must BLOCK an Odoo-context
question that carries NO Odoo project.task reference URL.

Owner directive (montalu session 2026-09-06): every question to the owner about
Odoo work must carry the Odoo task deep URL (``/odoo/project/<pid>/tasks/<tid>``)
or an action URL, or an explicit no-task statement. The Odoo task is the primary
client tracking; the GitHub issue is only the developer tracking.

These are FUNCTIONAL tests (real Stop payload on stdin) plus content-locks on the
Check 7 implementation and the doctrine surfaces.
"""

import json
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"
MODULE = ROOT / "modules" / "core" / "issue-reference-context.md"
SKILL = ROOT / "skills" / "user-questions-slovak" / "SKILL.md"


class _HookCase(unittest.TestCase):
    """Feed the Stop hook a payload and read its block/pass verdict."""

    def _run(self, msg):
        sid = "qtask907-%s" % uuid.uuid4().hex[:12]
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
# Fixture: Odoo-context question WITHOUT a task URL — the incident shape
# ---------------------------------------------------------------------------

ODOO_NO_TASK = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** V IT-Výroba "
    "module som narazil na problém s číslom šarže pri výrobe. Ako to "
    "riešiť?\n"
    "\n"
    "• Opraviť len pre výrobu (odporúčam)\n"
    "• Opraviť univerzálne\n"
    "\n"
    "❓ NEEDS YOU: opraviť číslo šarže len pre výrobu alebo univerzálne?\n"
)

# With a task URL — should PASS
ODOO_WITH_TASK = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** V IT-Výroba "
    "module som narazil na problém s číslom šarže pri výrobe.\n"
    'Odoo task: „Číslo šarže v IT-Výroba“ (stage: V riešení) '
    "— https://erp.montalu.cloud/odoo/project/4/tasks/503\n"
    "\n"
    "• Opraviť len pre výrobu (odporúčam)\n"
    "• Opraviť univerzálne\n"
    "\n"
    "❓ NEEDS YOU: opraviť číslo šarže len pre výrobu alebo univerzálne?\n"
)

# With an action URL — should PASS
ODOO_WITH_ACTION = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Problém "
    "s výrobným modulom.\n"
    "Task: https://erp.montalu.cloud/odoo/action-mrp_menu/12\n"
    "\n"
    "• Opraviť (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: opraviť?\n"
)

# With explicit no-task — should PASS
ODOO_NO_TASK_EXPLICIT = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Čisto "
    "technická úloha — migrácia databázovej schémy.\n"
    "Odoo task neexistuje — ide o čisto technickú úlohu.\n"
    "\n"
    "• Migrovať teraz (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: migrovať teraz?\n"
)

# Non-Odoo question — should PASS without task URL
NON_ODOO = (
    "**Otázka — projekt camera-box (kamery pre kostolný prenos):** "
    "OBS reštart pri update.\n"
    "\n"
    "• Reštartovať (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: reštartovať OBS?\n"
)

# Odoo task mentioned in block — odoo context via "odoo modul"
ODOO_TASK_WORD_NO_URL = (
    "**Otázka — projekt odoo-erp (Odoo ERP):** V Odoo module "
    "mrp som narazil na problém.\n"
    "\n"
    "• Opraviť (odporúčam)\n"
    "• Odložiť\n"
    "\n"
    "❓ NEEDS YOU: opraviť alebo odložiť?\n"
)

# "Odoo task" work-context mention — triggers via ODOO_WORK_RX
ODOO_WORK_CONTEXT_NO_TASK = (
    "**Otázka — projekt fakturacia (fakturacny system):** "
    "Odoo task pre import objednavok treba rozhodnut.\n"
    "\n"
    "• Opraviť (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: opraviť?\n"
)

# A question about a non-Odoo project that mentions a stream name — should PASS
NON_ODOO_WITH_STREAM = (
    "**Otázka — projekt camera-box (kamery pre kostolný prenos):** "
    "Klient montalu chce zmeniť uhol kamery.\n"
    "\n"
    "• Zmeniť (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: zmeniť uhol?\n"
)


class TestCheck7Blocks(_HookCase):
    """Check 7 blocks an Odoo-context question without a task URL."""

    def test_odoo_question_without_task_url_blocks(self):
        r = self._run(ODOO_NO_TASK)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)
        self.assertIn("task", self._reason(r).lower())

    def test_odoo_module_mention_without_task_url_blocks(self):
        r = self._run(ODOO_TASK_WORD_NO_URL)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)

    def test_odoo_work_context_without_task_url_blocks(self):
        r = self._run(ODOO_WORK_CONTEXT_NO_TASK)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)


class TestCheck7Passes(_HookCase):
    """Check 7 passes when the block carries a task URL or no-task statement."""

    def test_with_task_url_passes(self):
        r = self._run(ODOO_WITH_TASK)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_with_action_url_passes(self):
        r = self._run(ODOO_WITH_ACTION)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_with_explicit_no_task_passes(self):
        r = self._run(ODOO_NO_TASK_EXPLICIT)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_non_odoo_question_passes(self):
        r = self._run(NON_ODOO)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)

    def test_non_odoo_with_stream_mention_passes(self):
        r = self._run(NON_ODOO_WITH_STREAM)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)


class TestDoctrineLocks(unittest.TestCase):
    """Content locks on the doctrine surfaces: the module and the skill."""

    def test_module_has_task_reference_section(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn("Odoo Task References", text)
        self.assertIn("/odoo/project/", text)
        self.assertIn("action URL", text)
        self.assertIn("model-form", text)
        self.assertIn("Check 7", text)

    def test_skill_has_odoo_task_section(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("Odoo-context questions", text)
        self.assertIn("/odoo/project/", text)
        self.assertIn("action URL", text)
        self.assertIn("task neexistuje", text.lower())

    def test_hook_has_check_7(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertIn("Check 7", text)
        self.assertIn("VIOLATION=\"task\"", text)
        self.assertIn("/odoo/project/", text)


if __name__ == "__main__":
    unittest.main()
