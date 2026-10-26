"""#1137 (owner 26.10.2026, gk): no "Odoo task neexistuje" line in owner questions.

Owner, verbatim: "V gk stále čítam takýto bordel: Odoo task neexistuje — čisto
technická úloha. Už som to opakovane písal, že tam, kde sa Odoo projekty
nepoužívajú, sa nemajú do všetkého miešať!!!"

The question gate's Check 7 (#907) blocked every `projekt odoo-erp` question
without a project.task URL, and its only escape was that boilerplate line, so
gk and gk-infra wrote it into every infra question. A question about a board
task still cites the task (doctrine, plus Check 9 when a task URL is present);
a question with no task carries no task line and passes.
"""

import json
import subprocess
import unittest
import uuid
from pathlib import Path

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"
MODULE = ROOT / "modules" / "core" / "issue-reference-context.md"
SKILL = ROOT / "skills" / "user-questions-slovak" / "SKILL.md"

# The gk-infra shape that Check 7 rejected (no task line).
GK_INFRA_QUESTION = (
    "**Otázka — projekt odoo-erp (Odoo ERP, infra servery pre streamy):** "
    "Kópia produkcie pre stream beží na samostatnom serveri. Strop 3 hodiny "
    "sa dnes počíta od deployu, nie od vzniku servera, takže server žije dlhšie.\n"
    "\n"
    "• Počítať od vzniku servera (odporúčam) — server zanikne načas\n"
    "• Nechať počítanie od deployu — server môže žiť dlhšie\n"
    "\n"
    "❓ NEEDS YOU: strop 3 h počítať od vzniku servera, alebo od deployu?\n"
)

# Names an Odoo task in prose, cites none: no task line is demanded.
ODOO_TASK_WORD = (
    "**Otázka — projekt montalu (Odoo ERP pre montalu):** Odoo úloha na "
    "export faktúr je hotová, klient chce ešte stĺpec s IČ DPH. Doplniť ho "
    "teraz, alebo až po jeho potvrdení?\n"
    "\n"
    "• Doplniť teraz (odporúčam) — klient to už napísal\n"
    "• Počkať na potvrdenie — istota\n"
    "\n"
    "❓ NEEDS YOU: doplniť stĺpec IČ DPH teraz?\n"
)

# A question that DOES cite a board task keeps Check 9 (attachments read).
TASK_URL_NO_ATTACH = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Úloha s exportom "
    "faktúr je hotová.\n"
    "Odoo task: „Export faktúr“ (stage: V práci) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/503\n"
    "\n"
    "• Zavrieť (odporúčam)\n"
    "• Nechať otvorenú\n"
    "\n"
    "❓ NEEDS YOU: zavrieť úlohu s exportom?\n"
)


class _HookCase(unittest.TestCase):

    def _reason(self, msg):
        sid = "notask1137-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
            "/tmp/claude-lastq-refs-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        r = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30, env=hermetic_hook_env(self))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["reason"] if '"block"' in r.stdout else ""


class TestNoTaskLineDemanded(_HookCase):

    def test_gk_infra_question_without_task_line_passes(self):
        self.assertEqual(self._reason(GK_INFRA_QUESTION), "")

    def test_task_word_without_url_passes(self):
        self.assertEqual(self._reason(ODOO_TASK_WORD), "")

    def test_a_cited_task_still_needs_its_attachments(self):
        self.assertIn("ATTACHMENTS", self._reason(TASK_URL_NO_ATTACH))


class TestNoSurfaceAsksForTheLine(unittest.TestCase):
    """No hook reason or doctrine tells a session to write the boilerplate."""

    def test_no_surface_names_the_boilerplate(self):
        for path in (HOOK, MODULE, SKILL):
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("task neexistuje", text, path.name)
            self.assertNotIn("no matching task → say so", text, path.name)


if __name__ == "__main__":
    unittest.main()
