"""#1220: adding a second account to a box must not change any existing drop
lane. Declaring varos@dev2 (a) renamed the live `drop-dev2.newlevel.media`
lane of the box's own `newlevel` account to `drop-dev2-newlevel...` (the
shared-box naming) and (b) handed admin@forestshop-dev's fixed port 8880 to
the new account (next-free ran before the fixed ports were reserved), which
dropped forestshop's lane. The box's own account (a bare `<box>` entry) keeps
the bare hostname, and every fixed port is reserved before any next-free one."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_drop_gateway as dg  # noqa: E402
import cli_drop_lanes  # noqa: E402

DEV2 = "100.82.64.27"


def _lanes(hosts):
    return dg.build_drop_lanes(hosts)


class TestBareBoxAccountKeepsItsHost(unittest.TestCase):

    def test_a_second_account_does_not_rename_the_box_lane(self):
        lanes = _lanes([{"name": "dev2", "host": DEV2, "user": "newlevel"},
                        {"name": "zzz@dev2", "host": DEV2, "user": "zzz"}])
        self.assertEqual(lanes[("dev2", "newlevel")].host, "drop-dev2.newlevel.media")
        self.assertEqual(lanes[("dev2", "zzz")].host, "drop-dev2-zzz.newlevel.media")


class TestFixedPortsAreReserved(unittest.TestCase):

    def test_a_new_account_never_takes_a_fixed_port(self):
        fixed = dict(cli_drop_lanes._GENERATED_DROP_PORTS)
        hosts = [{"name": "aaa@dev2", "host": DEV2, "user": "aaa"},
                 {"name": "admin@forestshop-dev", "host": "forestshop-dev.newlevel.media",
                  "user": "admin"}]
        lanes = _lanes(hosts)
        self.assertEqual(lanes[("forestshop-dev", "admin")].port,
                         fixed[("forestshop-dev", "admin")])
        self.assertNotIn(lanes[("dev2", "aaa")].port, set(fixed.values()))


if __name__ == "__main__":
    unittest.main()
