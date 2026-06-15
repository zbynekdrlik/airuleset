import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class TestConstants(unittest.TestCase):
    def test_constants_present(self):
        from board import (PORT, BOARD_HOST_IP, REPORT_TIMEOUT, CIRCUIT_BREAKER_S,
                           FLUSH_CAP, QUEUE_MAX_BYTES, QUEUE_TTL_S, BODY_MAX,
                           EVENT_CAP_PER_RUN, STALE_ACTIVE_S, STALE_WAIT_S,
                           GH_POLL_FLOOR_S, TERMINAL_PHASES)
        self.assertEqual(PORT, 8787)
        self.assertEqual(BOARD_HOST_IP, "10.77.9.21")
        self.assertEqual(REPORT_TIMEOUT, 2)
        self.assertIn("done", TERMINAL_PHASES)
        self.assertIn("obsolete-closed", TERMINAL_PHASES)
