"""#1098 — ``stop-check-question-quality.sh`` Check 9 must BLOCK an Odoo
BOARD-TASK question that does NOT state what the task's ATTACHMENTS contained.

Incident: a client put the specification as an IMAGE inside a ``project.task``
description (montalu task 1010 "Sieťka robust", att 37652, 20.9.2026); the
stream's text-only board read missed it and parked its ticket (odoo-erp #555)
on ``needs-answer`` asking for the very data the screenshot carried. Owner
21.9.2026 verbatim: „preco tuto ulohu vobec neriesis tam v popise je
screenshot", „aj ostatne ulohy skontroluj popis fotky".

ROZHODNUTÉ #1098 (supervisor, 2026-09-21): Option A — UNIVERSAL. The trigger is
a ``/odoo/project/<pid>/tasks/<tid>`` task URL ALONE — NOT gated on Check 7's
``odoo_ctx`` (Review 1): the incident's OWN shape is headed „projekt montalu
(Odoo ERP…)" which Check 7's ODOO_PROJECT_RX / ODOO_WORK_RX do NOT match, so
odoo_ctx is empty there. Any block carrying a board-task URL must ALSO carry a
``Prílohy:`` line — ``Prílohy: att <ids> prečítané …`` (or a read-evidence
word) or ``Prílohy: žiadne`` for a task with no attachments. An action URL or an
explicit no-task statement references no board task (nothing to read), so is
exempt.

These are FUNCTIONAL tests (real Stop payload on stdin, reusing the #907
subprocess driver) plus content-locks (window teeth, #498/#500 pattern) on the
doctrine (rule 15) and the recipe (the ``project.task`` section of
read-with-attachments.md).
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


def _window(text, start_marker, end_prefixes):
    """Return the block from the line containing *start_marker* up to (but not
    including) the next line whose stripped start matches any *end_prefixes*,
    or EOF. The start line itself is always included."""
    out = []
    started = False
    for ln in text.splitlines():
        if not started:
            if start_marker in ln:
                started = True
                out.append(ln)
            continue
        if any(ln.startswith(p) for p in end_prefixes):
            break
        out.append(ln)
    return "\n".join(out)


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

# The incident shape (odoo-erp headed): parking a board task on a question,
# without stating that the task's attachments were read. MUST BLOCK.
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

# The incident's OWN head shape: „projekt montalu (Odoo ERP…)" — Check 7's
# ODOO_PROJECT_RX (projekt odoo[-_ ]) and ODOO_WORK_RX (odoo task/úloh) do NOT
# match, so odoo_ctx is EMPTY. The ONLY Odoo signal is the task URL. This is the
# exact #555 shape Review 1 flagged: the old Check 9 precondition
# `[ -n "${odoo_ctx:-}" ]` let it through. MUST BLOCK (task URL is the proof).
MONTALU_HEAD_NO_PRILOHY = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Klient "
    "v popise úlohy „Sieťka robust“ zadal špecifikáciu, rozmery nie sú jasné.\n"
    "Úloha na boarde: https://erp.montalu.cloud/odoo/project/2/tasks/1010\n"
    "\n"
    "• Doplniť rozmery podľa odhadu (odporúčam)\n"
    "• Počkať na klienta\n"
    "\n"
    "❓ NEEDS YOU: aké rozmery má mať sieťka?\n"
)

# Same montalu-headed shape but the attachment was read + stated. MUST PASS.
MONTALU_HEAD_WITH_PRILOHY = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Klient "
    "v popise úlohy „Sieťka robust“ pridal špecifikáciu ako screenshot.\n"
    "Úloha na boarde: https://erp.montalu.cloud/odoo/project/2/tasks/1010\n"
    "Prílohy: att 37652 prečítané (rozmery 1575/1924); farbu neuvádza\n"
    "\n"
    "• Použiť rozmery zo screenshotu (odporúčam)\n"
    "• Počkať na farbu\n"
    "\n"
    "❓ NEEDS YOU: akú farbu má mať sieťka (rozmery mám z prílohy)?\n"
)

# Same task, attachments read + values stated (odoo-erp head). MUST PASS.
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

# Markdown-bold Prílohy line (`**Prílohy:** žiadne`). MUST PASS — the anchor
# must tolerate the block's own markdown (Review 1 finding #3).
TASK_URL_BOLD_PRILOHY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Dev úloha.\n"
    "Odoo task: „Refaktor“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/778\n"
    "**Prílohy:** žiadne\n"
    "\n"
    "• Refaktorovať (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: refaktorovať?\n"
)

# Markdown-bullet Prílohy line (`- Prílohy: žiadne`). MUST PASS.
TASK_URL_BULLET_PRILOHY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Dev úloha.\n"
    "Odoo task: „Refaktor 2“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/779\n"
    "- Prílohy: žiadne\n"
    "\n"
    "• Refaktorovať (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: refaktorovať?\n"
)

# A read-evidence value with no leading `att`/`žiadne` — starts with a digit but
# carries "screenshot" + "prečítaný". MUST PASS (Review 1 🔵 value whitelist).
TASK_URL_READ_EVIDENCE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Board úloha "
    "so screenshotom.\n"
    "Odoo task: „Graf“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/780\n"
    "Prílohy: 1 screenshot prečítaný (rozmery 1575/1924)\n"
    "\n"
    "• Použiť rozmery (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: použiť tie rozmery?\n"
)

# A vague „pozriem neskôr" value does NOT satisfy the whitelist. MUST BLOCK.
TASK_URL_VAGUE_VALUE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Board úloha "
    "so screenshotom.\n"
    "Odoo task: „Graf“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/781\n"
    "Prílohy: pozriem neskôr\n"
    "\n"
    "• Použiť rozmery (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: použiť rozmery?\n"
)

# A bare „att" with no ids/values does NOT satisfy the whitelist. MUST BLOCK
# (Review 2 🔵: a bare `Prílohy: att` must not discharge the gate).
TASK_URL_BARE_ATT = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Board úloha.\n"
    "Odoo task: „Graf 2“ (stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/782\n"
    "Prílohy: att\n"
    "\n"
    "• Použiť rozmery (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: použiť rozmery?\n"
)

# A `Prílohy: žiadne` line INSIDE a ``` fence must NOT discharge the gate — the
# block is fence-stripped first (Review 1 🔵). MUST BLOCK.
TASK_URL_FENCED_PRILOHY = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Board úloha "
    "so screenshotom v popise.\n"
    "Odoo task: „Sieťka“ (stage: Potrebuje ujasniť) "
    "— https://erp.montalu.cloud/odoo/project/2/tasks/1011\n"
    "\n"
    "```\n"
    "Prílohy: žiadne\n"
    "```\n"
    "\n"
    "• Doplniť rozmery (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: aké rozmery?\n"
)

# A LONG multi-attachment Prílohy line sitting in the brief region must NOT trip
# Check 3 (briefwall) — Check 3 exempts the Prílohy line from the brief count
# (Review 1 finding #4). Brief WITHOUT the Prílohy line is short; WITH it counted
# the brief would exceed 600 chars. MUST PASS.
_LONG_PRILOHY = (
    "Prílohy: att 37652, 37653, 37654, 37655, 37656, 37657, 37658, 37659 "
    "prečítané (rozmery jokle 12x8mm: šírka 4 ks 1575, výška 4 ks 1924; "
    "farba RAL 9005; hrúbka 2 mm; počet kusov 8; povrch žiarový zinok; "
    "poznámka: rezať na mieru podľa priloženej tabuľky; termín do konca "
    "mesiaca; kontakt Patrik; dodatočné rozmery 1200/800/600; tolerancia "
    "+-2 mm; balenie po 4 kusoch; druhý list tabuľky: profil 40x40x2, dĺžka "
    "3000 mm, 12 ks, pozinkované, vŕtať otvory D10 á 250 mm; tretí list: "
    "spojovací materiál M8x30 nerez, 96 ks, podložky a matice v sade; "
    "štvrtý list: povrchová úprava komaxit antracit, záruka 5 rokov)"
)
TASK_URL_LONG_PRILOHY_BRIEF = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre montalu):** Sieťka robust.\n"
    "Odoo task: „Sieťka robust“ "
    "— https://erp.montalu.cloud/odoo/project/2/tasks/1010\n"
    + _LONG_PRILOHY + "\n"
    "\n"
    "• Vyrobiť podľa tabuľky (odporúčam)\n"
    "• Počkať\n"
    "\n"
    "❓ NEEDS YOU: vyrobiť podľa priloženej tabuľky?\n"
)

# An ACTION URL references a menu/view, not a board task — no attachments to
# read, so Check 9 is exempt even with no Prílohy line. MUST PASS.
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

# A non-Odoo question is untouched by Check 9 (no task URL). MUST PASS.
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
    """Check 9 blocks a board-task question that omits a valid Prílohy: line."""

    def test_task_url_without_attachments_blocks(self):
        r = self._run(TASK_URL_NO_PRILOHY)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)

    def test_montalu_headed_shape_blocks(self):
        # odoo_ctx is EMPTY here (Check 7 misses „projekt montalu (Odoo ERP…)");
        # the task URL alone must trigger Check 9 (Review 1 precondition drop).
        r = self._run(MONTALU_HEAD_NO_PRILOHY)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)
        self.assertIn("Check 9", self._reason(r))

    def test_block_reason_names_prilohy_check9_and_recipe(self):
        r = self._run(TASK_URL_NO_PRILOHY)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)
        reason = self._reason(r)
        self.assertIn("Prílohy", reason)
        self.assertIn("Check 9", reason)
        self.assertIn("read-with-attachments", reason)


class TestCheck9ValueWhitelist(_HookCase):
    """A vague / bare Prílohy value does NOT discharge the gate."""

    def test_vague_value_blocks(self):
        r = self._run(TASK_URL_VAGUE_VALUE)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)

    def test_bare_att_blocks(self):
        r = self._run(TASK_URL_BARE_ATT)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)


class TestCheck9Fence(_HookCase):
    """A Prílohy line inside a ``` fence does NOT discharge the gate."""

    def test_fenced_prilohy_blocks(self):
        r = self._run(TASK_URL_FENCED_PRILOHY)
        self.assertTrue(self._blocked(r), r.stdout + r.stderr)


class TestCheck9Passes(_HookCase):
    """Check 9 passes when the block states its attachments or has no board task."""

    def test_montalu_headed_with_prilohy_passes(self):
        r = self._run(MONTALU_HEAD_WITH_PRILOHY)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_task_url_with_att_line_passes(self):
        r = self._run(TASK_URL_WITH_ATT)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_task_url_with_ziadne_passes(self):
        r = self._run(TASK_URL_ZIADNE)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_task_url_with_ziadne_ascii_passes(self):
        r = self._run(TASK_URL_ZIADNE_ASCII)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bold_prilohy_passes(self):
        r = self._run(TASK_URL_BOLD_PRILOHY)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bullet_prilohy_passes(self):
        r = self._run(TASK_URL_BULLET_PRILOHY)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_read_evidence_value_passes(self):
        r = self._run(TASK_URL_READ_EVIDENCE)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_long_prilohy_does_not_trip_briefwall(self):
        # WITHOUT the Check 3 exemption the long Prílohy line inflates the brief
        # past 600 chars → briefwall block; WITH it the line is exempt → PASS.
        r = self._run(TASK_URL_LONG_PRILOHY_BRIEF)
        self.assertFalse(self._blocked(r),
                         "long Prílohy line must not trip Check 3: " + self._reason(r))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_action_url_without_attachments_passes(self):
        r = self._run(ACTION_URL_NO_PRILOHY)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_task_statement_passes(self):
        r = self._run(NO_TASK_STATEMENT)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_odoo_question_unaffected(self):
        r = self._run(NON_ODOO)
        self.assertFalse(self._blocked(r), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDoctrineLocks(unittest.TestCase):
    """Window teeth (#498/#500) on rule 15 — a deletion of the payload FAILS."""

    def _rule15(self):
        text = DOCTRINE.read_text(encoding="utf-8")
        return _window(text, "### 15.", ["### "])

    def test_rule15_present(self):
        self.assertIn("### 15.", DOCTRINE.read_text(encoding="utf-8"))

    def test_rule15_owner_quote(self):
        self.assertIn(
            "preco tuto ulohu vobec neriesis tam v popise je screenshot",
            self._rule15())

    def test_rule15_date(self):
        self.assertIn("21.9.2026", self._rule15())

    def test_rule15_negation_phrase(self):
        # Review 2 fix text: honest-null, "never wait".
        self.assertIn("LEN na to, čo v prílohe NIE JE", self._rule15())

    def test_rule15_attachment_ids(self):
        self.assertIn("attachment_ids", self._rule15())

    def test_rule15_att_id_citation(self):
        self.assertIn("att-id", self._rule15())

    def test_rule15_prilohy_line_shape(self):
        self.assertIn("Prílohy:", self._rule15())

    def test_rule15_work_products_path(self):
        self.assertIn("work-products", self._rule15())


class TestDoctrineRevert(unittest.TestCase):
    """The out-of-scope condensation is reverted — content deleted by the first
    lane and restored by Review 2 🔴 must be back verbatim."""

    def _text(self):
        return DOCTRINE.read_text(encoding="utf-8")

    def test_odoo19_stage_set_restored(self):
        # #1018/#1014 canonical Odoo-19 target stage set (deleted by the
        # condensation, present nowhere else in the repo).
        self.assertIn("Nové → Požadujú sa zmeny → V riešení → Čaká → Hotové", self._text())

    def test_board_standard_managed_flag_restored(self):
        self.assertIn("board_standard_managed", self._text())

    def test_verif_stage_coupling_payload_restored(self):
        # The COUPLING note must keep its payload: VERIF_STAGE_RX + "(add `Čaká`)".
        text = self._text()
        self.assertIn("VERIF_STAGE_RX", text)
        self.assertIn("add `Čaká`", text)


class TestRecipeLocks(unittest.TestCase):
    """Window teeth on the recipe's ``## project.task`` section — deleting the
    section (or gutting it) FAILS."""

    def _section(self):
        text = RECIPE.read_text(encoding="utf-8")
        return _window(text, "## project.task", ["## "])

    def test_section_present(self):
        self.assertIn("## project.task", RECIPE.read_text(encoding="utf-8"))

    def test_section_res_model_domain(self):
        self.assertIn('"res_model", "=", "project.task"', self._section())

    def test_section_res_field_named(self):
        # Review 2: the hidden ('res_field','=',False) prepend must be named.
        self.assertIn("res_field", self._section())

    def test_section_extraction_regex(self):
        # Review 2: /web/content/ (spreadsheet) also, not just /web/image/.
        self.assertIn("/web/(?:image|content)/(\\d+)", self._section())

    def test_section_mail_message_attachments(self):
        section = self._section()
        self.assertIn("mail.message", section)
        self.assertIn("attachment_ids", section)

    def test_section_work_products_path(self):
        self.assertIn("work-products", self._section())


if __name__ == "__main__":
    unittest.main()
