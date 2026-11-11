"""#1306 — the montalu board / task-hygiene machinery is removed (client gone,
owner 11.11.2026). Retirement locks: nothing reads the overseer's leftover
status file any more, run_once has no Job 49 gate, the CLI flags and the nudge
kind are gone. The generic pieces stay: the #1258 no-board marker and the
#1179/#1180 handover Stop gate (with its own handover-shape check)."""
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import statusbar  # noqa: E402
import watchdog  # noqa: E402
from _hook_state_cleanup import sweep_session_files  # noqa: E402


def _seed_cache(home, cwd, open_n, name):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(
        {"open": open_n, "name": name, "root": str(cwd), "ts": int(time.time())}))


_STATUS = {"ts": None, "a": 2, "a_oldest_ts": 1.0, "b": 1, "b_verif": 1, "c": 0,
           "h": 1, "a_items": ["#7 x"], "b_items": ["#8 y"], "h_items": ["#9 z"]}


def _leftover(home):
    d = Path(home, ".clau" + "de", "task-hygiene")
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps(dict(_STATUS, ts=time.time())))


class Removed(unittest.TestCase):
    def test_modules_are_gone(self):
        for name in ("cli_task_hygiene", "cli_odoo_ro", "cli_handover_hygiene",
                     "watchdog.task_hygiene"):
            with self.subTest(name=name):
                self.assertIsNone(importlib.util.find_spec(name))

    def test_run_once_has_no_job_49_gate(self):
        self.assertNotIn("task_hygiene_enabled",
                         inspect.signature(watchdog.run_once).parameters)

    def test_no_task_hygiene_nudge_kind(self):
        self.assertNotIn("task-hygiene", watchdog.MACHINE_NUDGE_KINDS)

    def test_quals_have_no_task_hygiene_flag(self):
        for sub in ("core-quals", "slice-quals"):
            with self.subTest(sub=sub):
                r = subprocess.run([sys.executable, str(ROOT / "airuleset.py"), sub,
                                    "--help"], capture_output=True, text=True,
                                   timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("--task-hygiene", r.stdout)

    def test_leftover_status_does_not_change_footer_I(self):
        with tempfile.TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=5, name="demo")
            _leftover(home)
            self.assertIn("I 5", statusbar.tickets_segment(cwd, home=home,
                                                           spawn=False))

    def test_leftover_status_no_longer_stop_gates(self):
        with tempfile.TemporaryDirectory() as home:
            _leftover(home)
            sid = "rm1306-" + uuid.uuid4().hex[:10]
            r = subprocess.run(
                ["bash", str(ROOT / "hooks" / "stop-check-untracked-work.sh")],
                input=json.dumps({"last_assistant_message": "hotovo\n✅ DONE: x",
                                  "session_id": sid}),
                capture_output=True, text=True, timeout=60,
                env=dict(os.environ, HOME=home))
            sweep_session_files(sid)
            self.assertNotEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("task-hygiene", r.stderr)


class GenericPiecesStay(unittest.TestCase):
    def test_no_board_marker_still_written(self):
        import cli_odoo_board
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("off", cli_odoo_board.write_board_marker(d, "david1"))
            self.assertEqual(Path(d, cli_odoo_board.BOARD_MARKER).read_text(), "off\n")

    def test_handover_shape_lives_in_the_gate(self):
        from gates import handover
        self.assertTrue(handover.is_handover_body(
            "<p>Čo skúsiť: export.</p><p>stačí 👍</p>"))
        self.assertFalse(handover.is_handover_body("Skúsili ste? Stačí napísať."))


if __name__ == "__main__":
    unittest.main()
