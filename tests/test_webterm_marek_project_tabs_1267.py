"""#1267: the wall-bridge@dev1 and songplayer@dev1 project accounts are webterm
tabs on marek's dashboard too (owner 30.10.2026, answer "1" to "má Marek dostať
tab wall-bridge (a songplayer)?"). Same project-account shape as fohmixer and
varos (#1183/#1220): ONE shared project session, one lane key per human."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
from cli_webterm_only import WEBTERM_CONTROLLER_LANE_PUBKEYS  # noqa: E402

ACCOUNTS = ("wall-bridge", "songplayer")


class TestMarekProjectTabs(unittest.TestCase):

    def test_marek_inventory_has_both_tabs(self):
        inv = profiles.marek_inventory()
        for acct in ACCOUNTS:
            got = [e for e in inv if e["id"] == acct]
            self.assertEqual(len(got), 1, acct)
            e = got[0]
            self.assertFalse(e["local"])
            self.assertEqual(e["user"], acct)
            self.assertEqual(e["host"], profiles.FOHMIXER_HOST)      # dev1
            self.assertEqual(e["identity"], profiles.WEBTERM_MAREK_IDENTITY)
            self.assertEqual(e["preferred"], acct)
            self.assertEqual(e["start_dir_chain"], ["devel/" + acct])
            self.assertNotIn("u_tenant", e)     # #703: not marek's own tenant

    def test_marek_tabs_follow_varos_in_the_owner_order(self):
        tabs = w.WEBTERM_DASHBOARD_TABS["marek"]
        i = tabs.index("varos")
        self.assertEqual(tabs[i + 1:i + 3], ["wall-bridge", "songplayer"])

    def test_the_tabs_render_on_marek_dashboard(self):
        html = w.render_dashboard_html(
            profiles.marek_inventory(), ttyd_base="/t", human="marek",
            term_grid=(176, 51))
        for acct in ACCOUNTS:
            self.assertIn('<span class="al">%s</span>' % acct, html)

    def test_owner_and_marek_share_the_project_session(self):
        for acct in ACCOUNTS:
            sessions = bootstrap.account_spec(acct)["webterm_sessions"]
            self.assertEqual(set(sessions), {"zbynek", "marek"}, acct)
            for human, sess in sessions.items():
                self.assertEqual(sess["preferred"], acct, human)
                self.assertEqual(sess["start_dir_chain"], ["devel/" + acct], human)

    def test_authorized_keys_carry_both_lane_keys(self):
        for acct in ACCOUNTS:
            forced = [k for k in bootstrap.desired_keys_for_service_account(acct)
                      if k.startswith("restrict,")]
            self.assertEqual(len(forced), 2, acct)
            for human in ("zbynek", "marek"):
                blob = WEBTERM_CONTROLLER_LANE_PUBKEYS[human].split()[1]
                self.assertTrue(any(blob in k for k in forced), (acct, human))


if __name__ == "__main__":
    unittest.main()
