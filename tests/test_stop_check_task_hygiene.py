"""#1036 — the task-hygiene Stop gate inside hooks/stop-check-untracked-work.sh.

A turn ending ✅ DONE / ⏳ WORKING on a CONFIGURED box (a fresh
~/.claude/task-hygiene/status.json) with A > 0 older than 24 h, or B > 0 for
Verifikácia, is blocked (exit 2, reason on STDERR — the deny-stderr contract).
Fail-OPEN when the status file is absent or stale (dead watchdog).
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-untracked-work.sh"

DAY = 24 * 3600


def _run(msg, home, sid=None):
    sid = sid or ("i1036-" + uuid.uuid4().hex)
    payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
    env = dict(os.environ)
    env["HOME"] = home
    r = subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                       capture_output=True, env=env, timeout=30)
    # clean this run's retry markers (uuid sid → collision-proof)
    for pat in ("airuleset-task-hygiene-block-", "airuleset-untracked-work-block-"):
        p = "/tmp/" + pat + sid
        if os.path.exists(p):
            os.remove(p)
    return r


def _seed(home, **fields):
    d = Path(home) / ".claude" / "task-hygiene"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"ts": time.time(), "a": 0, "b": 0, "c": 0,
               "a_oldest_ts": None, "b_verif": 0, "a_items": [], "b_items": []}
    payload.update(fields)
    (d / "status.json").write_text(json.dumps(payload))


@unittest.skipUnless(shutil.which("jq"), "jq required by the hook")
class TaskHygieneStopGate(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="i1036-stophook-")
        self.addCleanup(shutil.rmtree, d, True)
        self.home = d

    def test_A_older_than_24h_blocks_on_done(self):
        _seed(self.home, a=2, a_oldest_ts=time.time() - 2 * DAY,
              a_items=["#518 Vec", "#632 Iná vec"])
        r = _run("Hotovo.\n✅ DONE: nič viac", self.home)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("task-hygiene", r.stderr)
        self.assertIn("#518", r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    def test_B_verif_blocks_on_working(self):
        _seed(self.home, b=1, b_verif=1, b_items=["#873 Verif vec"])
        r = _run("Pracujem.\n⏳ WORKING: ďalej", self.home)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Verifikácia bez správy streamu", r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    def test_A_younger_than_24h_does_not_block(self):
        _seed(self.home, a=2, a_oldest_ts=time.time() - 3600,
              a_items=["#1 x"])
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_absent_status_fails_open(self):
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_stale_status_fails_open(self):
        _seed(self.home, a=2, a_oldest_ts=time.time() - 2 * DAY,
              ts=time.time() - 99999, a_items=["#1 x"])
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_terminal_marker_does_not_block(self):
        # a ❓ turn (session is engaging, not stopping) is never blocked
        _seed(self.home, a=2, a_oldest_ts=time.time() - 2 * DAY, a_items=["#1 x"])
        r = _run("❓ NEEDS YOU: otázka", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_configured_but_clean_does_not_block(self):
        _seed(self.home, a=0, b=0, b_verif=0)
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
