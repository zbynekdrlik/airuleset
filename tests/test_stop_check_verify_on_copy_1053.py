"""#1053 — the verify-on-copy Stop gate inside hooks/stop-check-untracked-work.sh
(the #1036 task-hygiene family).

A turn ending ✅ DONE / ⏳ WORKING with a fresh ~/.claude/verify-on-copy/status.json
carrying overdue tickets (verify-on-copy > 24 h, no Verified-on-copy: comment) is
blocked (exit 2, reason on STDERR — the deny-stderr contract). Fail-OPEN when the
status file is absent or stale (a dead writer).
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


def _run(msg, home, sid=None):
    sid = sid or ("voc1053-" + uuid.uuid4().hex)
    payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
    env = dict(os.environ)
    env["HOME"] = home
    r = subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                       capture_output=True, env=env, timeout=30)
    for pat in ("airuleset-verify-on-copy-block-",
                "airuleset-task-hygiene-block-",
                "airuleset-untracked-work-block-"):
        p = "/tmp/" + pat + sid
        if os.path.exists(p):
            os.remove(p)
    return r


def _seed(home, overdue, ts=None):
    d = Path(home) / ".claude" / "verify-on-copy"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"ts": ts if ts is not None else time.time(),
               "overdue": overdue, "repo": "zbynekdrlik/odoo-erp"}
    (d / "status.json").write_text(json.dumps(payload))


@unittest.skipUnless(shutil.which("jq"), "jq required by the hook")
class VerifyOnCopyStopGate(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="voc1053-stophook-")
        self.addCleanup(shutil.rmtree, d, True)
        self.home = d

    def test_overdue_blocks_on_done(self):
        _seed(self.home, [{"number": 6300, "title": "Money Gate",
                           "age_h": 30}])
        r = _run("Hotovo.\n✅ DONE: nič viac", self.home)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("verify-on-copy", r.stderr)
        self.assertIn("6300", r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    def test_overdue_blocks_on_working(self):
        _seed(self.home, [{"number": 6301, "title": "x", "age_h": 48}])
        r = _run("Pracujem.\n⏳ WORKING: ďalej", self.home)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("verify-on-copy", r.stderr)

    def test_empty_overdue_does_not_block(self):
        _seed(self.home, [])
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_absent_status_fails_open(self):
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_stale_status_fails_open(self):
        _seed(self.home, [{"number": 6300, "title": "x", "age_h": 30}],
              ts=time.time() - 99999)
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_corrupt_status_fails_open(self):
        d = Path(self.home) / ".claude" / "verify-on-copy"
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text("{not json")
        r = _run("✅ DONE: hotovo", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_terminal_marker_does_not_block(self):
        _seed(self.home, [{"number": 6300, "title": "x", "age_h": 30}])
        r = _run("❓ NEEDS YOU: otázka", self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
