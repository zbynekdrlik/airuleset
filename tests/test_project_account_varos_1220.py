"""#1220: the `varos` project account on dev2 (Varos East accounting: Fio
bank token, receipts). Owner ruling 8.10.2026 (`z1`): its own account, never
`newlevel` (#1184). Code-only private repo (documents never enter git), no
sudo, no LAN reach, owner-only webterm, its own Access-gated drop lane for
`secret request` + `upload`. `pending` until the root bootstrap ran, and no
`github_app` until the owner adds the repo to the App installation."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_drop_gateway as dg  # noqa: E402
import cli_fleet  # noqa: E402


class TestDeclaration(unittest.TestCase):

    def test_the_account(self):
        spec = bootstrap.account_spec("varos")
        self.assertEqual(spec["host"], "dev2")
        self.assertIs(spec["sudo"], False)
        self.assertEqual(list(spec["reach"]), [])
        self.assertEqual(list(spec["secrets"]), ["fio-token-varos"])
        self.assertEqual(spec["repo"], "zbynekdrlik/varos")
        self.assertEqual(spec["project_dir"], "devel/varos/uctovnictvo")
        self.assertEqual(spec["tmux_session"], "varos")
        self.assertEqual(set(spec["webterm_sessions"]), {"zbynek"})
        self.assertNotIn("github_app", spec)

    def test_the_table_stays_clean_and_renders(self):
        self.assertEqual(bootstrap.validate_all(), {})
        script = bootstrap.render_root_bootstrap("varos")
        self.assertIn("varos", script)
        self.assertNotIn("/etc/sudoers.d/airuleset-varos", script)


class TestFleetAndDropLane(unittest.TestCase):

    def entry(self):
        (h,) = [h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "varos@dev2"]
        return h

    def test_a_live_dev2_deploy_target(self):
        # bootstrapped on dev2 8.10. (BOOTSTRAP_RC=0, install as varos rc 0)
        h = self.entry()
        self.assertEqual((h["host"], h["user"]), ("100.82.64.27", "varos"))
        self.assertIs(h["pending"], False)
        self.assertNotIn("drop", h)          # a drop lane IS wanted

    def test_its_drop_lane_rides_the_controller_tunnel_behind_access(self):
        lane = dg.DROP_LANES[("dev2", "varos")]
        self.assertEqual(lane.topology, "controller")
        self.assertEqual(lane.origin_host, "100.82.64.27")
        self.assertIs(lane.access, True)
        self.assertIn("varos", lane.host)
        self.assertTrue(lane.host.endswith(".newlevel.media"))


if __name__ == "__main__":
    unittest.main()
