"""#1237 — every push-managed project account keeps the base channels.

fohmixer@dev1 (the first #1184 project account) was declared with
`drop.disabled`, so it had no drop lane: `secret request`, `upload` and `share`
refused on the box, a regression from the `newlevel` account it replaced. A
project account in `cli_fleet.REMOTE_HOSTS` must have a generated drop lane on
a FIXED port (#1220: a next-free port shifts other lanes).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_account_bootstrap as bootstrap   # noqa: E402
import cli_drop_gateway                     # noqa: E402
import cli_drop_lanes                       # noqa: E402
import cli_fleet                            # noqa: E402


def _project_entries():
    return [e for e in cli_fleet.REMOTE_HOSTS
            if e.get("user") in bootstrap.SERVICE_ACCOUNTS]


class TestProjectAccountDropLane(unittest.TestCase):

    def test_the_fleet_has_project_accounts(self):
        users = {e["user"] for e in _project_entries()}
        self.assertTrue({"fohmixer", "varos", "claudy"} <= users, users)

    def test_no_project_account_disables_its_drop_lane(self):
        for e in _project_entries():
            self.assertNotIn("disabled", e.get("drop") or {}, e["name"])

    def test_every_project_account_has_a_lane_on_a_fixed_port(self):
        for e in _project_entries():
            key = (cli_drop_lanes._nodename_for_entry(e), e["user"])
            self.assertIn(key, cli_drop_gateway.DROP_LANES, e["name"])
            self.assertIn(key, cli_drop_lanes._GENERATED_DROP_PORTS, e["name"])
            self.assertEqual(cli_drop_gateway.DROP_LANES[key].port,
                             cli_drop_lanes._GENERATED_DROP_PORTS[key])

    def test_fohmixer_lane(self):
        lane = cli_drop_gateway.DROP_LANES[("dev1", "fohmixer")]
        self.assertEqual(lane.host, "drop-dev1-fohmixer.newlevel.media")
        self.assertEqual(lane.port, 8893)
        self.assertEqual(lane.topology, "controller")

    def test_existing_dev1_lane_keeps_its_host_and_port(self):
        lane = cli_drop_gateway.DROP_LANES[("dev1", "newlevel")]
        self.assertEqual(lane.host, "drop-dev1.newlevel.media")
        self.assertEqual(lane.port, 8878)


if __name__ == "__main__":
    unittest.main()
