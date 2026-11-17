"""#1354: a BOUNCE-ACK given in one turn must carry to the next turns of the
same verdict -- david3 17.11.2026 had 4 overdue bounces and every Stop was
blocked until the message re-ACKed all four (339 hook blocks)."""
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


class TestAckCarries(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ack-1354-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = "/repo/odoo"
        self.vts = time.time() - 7200
        path = bgate._cache_path(self.cwd, self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"bounce_unhandled": [
                {"number": 10242, "verdict_ts": self.vts},
                {"number": 10243, "verdict_ts": self.vts}]}, fh)
        self.grace = cbu.BOUNCE_GRACE_SECONDS

    def _decide(self, now, msg):
        payload = {"last_assistant_message": msg, "cwd": self.cwd}
        return bgate.decide(json.dumps(payload), now=now, home=self.home)

    def test_an_earlier_ack_carries_to_a_later_turn(self):
        t0 = time.time()
        self.assertFalse(self._decide(t0, "BOUNCE-ACK: #10242\nBOUNCE-ACK: #10243\n✅ DONE: x")[0])
        self.assertFalse(self._decide(t0 + 60, "✅ DONE: other work")[0])

    def test_a_partial_ack_does_not_reblock_the_earlier_ones(self):
        t0 = time.time()
        self._decide(t0, "BOUNCE-ACK: #10242\nBOUNCE-ACK: #10243\n⏳ WORKING: x")
        self.assertFalse(self._decide(t0 + 120, "BOUNCE-ACK: #10242\n⏳ WORKING: x")[0])

    def test_a_never_acked_bounce_still_blocks(self):
        t0 = time.time()
        self._decide(t0, "BOUNCE-ACK: #10242\n⏳ WORKING: x")
        block, reason = self._decide(t0 + 60, "⏳ WORKING: x")
        self.assertTrue(block)
        self.assertIn("#10243", reason)
        self.assertNotIn("#10242", reason)

    def test_the_carried_ack_still_gets_the_repeat_nag(self):
        t0 = time.time()
        self._decide(t0, "BOUNCE-ACK: #10242\nBOUNCE-ACK: #10243\n✅ DONE: x")
        self.assertTrue(self._decide(t0 + self.grace + 60, "✅ DONE: y")[0])


if __name__ == "__main__":
    unittest.main()
