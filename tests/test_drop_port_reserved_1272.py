"""#1272: the iemmixer lane got port 8898, which dantesync holds as root on dev1,
so `secret request` on that account refused with "public drop port 8898 is busy"."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_drop_lanes as lanes  # noqa: E402


class TestReservedPorts(unittest.TestCase):

    def test_dantesync_port_is_reserved(self):
        self.assertIn(8898, lanes._RESERVED_PORTS)

    def test_no_fixed_lane_takes_a_reserved_port(self):
        self.assertFalse(set(lanes._GENERATED_DROP_PORTS.values()) & lanes._RESERVED_PORTS)


if __name__ == "__main__":
    unittest.main()
