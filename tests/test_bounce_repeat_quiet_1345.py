"""#1345: the #1329 repeat-ACK branch of the bounce Stop gate must not spam a
working session (david3 17.11.2026: a hook error on every wake while two
rework lanes were running)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import cli_bounce_unhandled as cbu  # noqa: E402
from gates import bounce_unhandled as bgate  # noqa: E402


class TestRepeatAckQuiet(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ack-1345-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = "/repo/odoo"
        self.vts = time.time() - 7200
        path = bgate._cache_path(self.cwd, self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"bounce_unhandled": [{"number": 10226, "verdict_ts": self.vts}]}, fh)
        self.grace = cbu.BOUNCE_GRACE_SECONDS

    def _decide(self, now, tasks=None, msg="BOUNCE-ACK: #10226\n⏳ WORKING: lanes"):
        payload = {"last_assistant_message": msg, "cwd": self.cwd}
        if tasks is not None:
            payload["background_tasks"] = tasks
        return bgate.decide(json.dumps(payload), now=now, home=self.home)

    def test_a_running_lane_suppresses_the_repeat_block(self):
        t0 = time.time()
        self._decide(t0)
        running = [{"id": "a1", "status": "running"}]
        self.assertFalse(self._decide(t0 + self.grace + 60, running)[0])

    def test_a_finished_task_does_not_suppress(self):
        t0 = time.time()
        self._decide(t0)
        done = [{"id": "a1", "status": "completed"}]
        self.assertTrue(self._decide(t0 + self.grace + 60, done)[0])

    def test_one_repeat_block_per_grace_window(self):
        t0 = time.time()
        self._decide(t0)
        t1 = t0 + self.grace + 60
        self.assertTrue(self._decide(t1)[0])
        self.assertFalse(self._decide(t1 + 30)[0])
        self.assertFalse(self._decide(t1 + 600)[0])
        self.assertTrue(self._decide(t1 + self.grace + 60)[0])

    def test_unacked_overdue_bounce_still_blocks_with_a_lane(self):
        running = [{"id": "a1", "status": "running"}]
        block, reason = self._decide(time.time(), running, msg="⏳ WORKING: x")
        self.assertTrue(block)
        self.assertIn("10226", reason)


if __name__ == "__main__":
    unittest.main()
