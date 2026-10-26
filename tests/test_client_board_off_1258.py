"""#1258 — slovnormal (david1-4) has NO Odoo client board (owner 2026-10-24).

Owner, verbatim: "david1-4 nechce pouzivat projekty a tasky v odoo a claude v
tychto projektoch sa stale snazia nieco robit v taskoch co podla mna zerie
zbytocne tokeny, chcem to mat odstavene pre slovnormal".

The Stop hook's Check 9 (attachments required) forced a david owner question
into a project.task lookup. A per-user fact in `cli_odoo_board` drives an
install-written marker `~/.claude/airuleset-client-board` = `off`; the check
and the task-hygiene config loader honour it. (Check 7, the task URL
requirement, was deleted on every box: owner 26.10.2026, #1137.)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cli_odoo_board  # noqa: E402
import cli_odoo_ro  # noqa: E402

HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"

ODOO_TASK_NO_ATTACH = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre slovnormal):** Pre platobný "
    "terminál je hotový vyplnený dokument pre Worldline.\n"
    "Odoo task: „Platobný terminál“ (stage: V práci) "
    "— https://erp.slovnormal.sk/odoo/project/28/tasks/2128\n"
    "\n"
    "• Pošleš ho ty (odporúčam)\n"
    "• Počkáme na ich odpoveď\n"
    "\n"
    "❓ NEEDS YOU: pošleš dokument Worldline ty, alebo počkáme?\n"
)


class TestQuestionGateHonoursNoBoard(unittest.TestCase):

    def _run(self, msg, marker=None):
        sid = "board1258-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
            "/tmp/claude-lastq-refs-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        env = hermetic_hook_env(self)
        if marker is not None:
            d = Path(env["HOME"], ".claude")
            d.mkdir(parents=True, exist_ok=True)
            (d / "airuleset-client-board").write_text(marker, encoding="utf-8")
        r = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30, env=env)
        return json.loads(r.stdout)["reason"] if '"block"' in r.stdout else ""

    def test_check9_still_blocks_on_a_board_box(self):
        self.assertIn("ATTACHMENTS", self._run(ODOO_TASK_NO_ATTACH))

    def test_check9_skipped_when_the_board_is_off(self):
        self.assertEqual(self._run(ODOO_TASK_NO_ATTACH, marker="off\n"), "")

    def test_any_other_marker_value_keeps_the_checks(self):
        self.assertIn("ATTACHMENTS", self._run(ODOO_TASK_NO_ATTACH, marker="on\n"))


class TestBoardFact(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="board1258-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self.claude = os.path.join(self.home, ".claude")
        os.makedirs(self.claude)

    def test_slovnormal_streams_have_no_board(self):
        for u in ("david1", "david2", "david3", "david4"):
            self.assertFalse(cli_odoo_board.board_enabled(u), u)

    def test_other_accounts_keep_their_board(self):
        for u in ("montalu1", "miva1", "gatekeeper", "airuleset"):
            self.assertTrue(cli_odoo_board.board_enabled(u), u)

    def test_install_writes_the_marker_for_a_no_board_user(self):
        cli_odoo_board.write_board_marker(self.claude, "david3")
        marker = Path(self.claude, cli_odoo_board.BOARD_MARKER)
        self.assertEqual(marker.read_text(encoding="utf-8"), "off\n")
        self.assertTrue(cli_odoo_board.board_off_here(self.home))

    def test_install_removes_a_stale_marker_for_a_board_user(self):
        marker = Path(self.claude, cli_odoo_board.BOARD_MARKER)
        marker.write_text("off\n", encoding="utf-8")
        cli_odoo_board.write_board_marker(self.claude, "montalu1")
        self.assertFalse(marker.exists())
        self.assertFalse(cli_odoo_board.board_off_here(self.home))

    def test_task_config_is_not_loaded_when_the_board_is_off(self):
        cfg = Path(self.claude, cli_odoo_ro.CONFIG_BASENAME)
        cfg.write_text(json.dumps({"url": "https://erp.slovnormal.sk"}), encoding="utf-8")
        self.assertIsInstance(cli_odoo_ro.load_config(home=self.home), dict)
        Path(self.claude, cli_odoo_board.BOARD_MARKER).write_text("off\n", encoding="utf-8")
        self.assertIsNone(cli_odoo_ro.load_config(home=self.home))


class TestDoctrineSaysNoBoard(unittest.TestCase):

    def test_board_doctrine_marks_slovnormal_as_no_board(self):
        text = (ROOT / "skills/odoo-client-messaging/client-board-tasks.md").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| **slovnormal**"))
        self.assertIn("NO board", row)
        self.assertNotIn("Nové → V práci", row)

    def test_owner_question_rule_exempts_a_client_without_a_board(self):
        text = (ROOT / "modules/core/issue-reference-context.md").read_text(encoding="utf-8")
        self.assertIn("slovnormal", text)


if __name__ == "__main__":
    unittest.main()
