"""#1220: the varos@dev2 project account is a webterm tab on the owner's
(zbynek.newlevel.media) AND marek's (marek.newlevel.media) dashboard — owner
2026-10-09: "ale nevidim to okno v marek webterm". The varos Claude ran in
newlevel@dev2's `marek-3` window, reached via marek's `dev2` tab; the isolated
account (#1184) must be reachable the same way, through the fohmixer (#1183)
project-account shape: ONE shared project session, one lane key per human."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_fleet  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
from cli_webterm_only import WEBTERM_CONTROLLER_LANE_PUBKEYS  # noqa: E402

VAROS_DIR = "devel/varos/uctovnictvo"


def _varos(inv):
    return [e for e in inv if e["id"] == "varos"]


class TestVarosTab(unittest.TestCase):

    def _assert_tab(self, inv, identity):
        got = _varos(inv)
        self.assertEqual(len(got), 1, [e["id"] for e in inv])
        e = got[0]
        self.assertFalse(e["local"])
        self.assertEqual(e["user"], "varos")
        self.assertEqual(e["host"], profiles.VAROS_HOST)
        self.assertEqual(e["identity"], identity)
        self.assertEqual(e["preferred"], "varos")
        self.assertEqual(e["start_dir_chain"], [VAROS_DIR])
        self.assertNotIn("u_tenant", e)   # #703: not any lane's own tenant

    def test_marek_dashboard_has_the_varos_tab(self):
        self._assert_tab(profiles.marek_inventory(), profiles.WEBTERM_MAREK_IDENTITY)
        tabs = w.WEBTERM_DASHBOARD_TABS["marek"]
        self.assertEqual(tabs[tabs.index("fohmixer") + 1], "varos")

    def test_owner_dashboard_has_the_varos_tab(self):
        self._assert_tab(profiles.zbynek_inventory(), profiles.WEBTERM_ZBYNEK_IDENTITY)
        tabs = w.WEBTERM_DASHBOARD_TABS["zbynek"]
        self.assertEqual(tabs[tabs.index("fohmixer") + 1], "varos")

    def test_the_tab_renders_with_the_varos_alias(self):
        html = w.render_dashboard_html(
            profiles.marek_inventory(), ttyd_base="/t", human="marek",
            term_grid=(176, 51))
        self.assertIn('<span class="al">varos</span>', html)

    def test_host_is_the_fleet_varos_entry(self):
        # zero-import leaf duplicate, drift-locked to the ONE fleet source
        fleet = next(h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "varos@dev2")
        self.assertEqual(profiles.VAROS_HOST, fleet["host"])
        self.assertEqual(bootstrap.fleet_boxes()["dev2"], profiles.VAROS_HOST)

    def test_fohmixer_tab_is_unchanged(self):
        e = profiles.fohmixer_entry("~/k", kind="owner")
        self.assertEqual(e, {
            "id": "fohmixer", "label": "fohmixer (dev1)", "kind": "owner",
            "local": False, "host": profiles.FOHMIXER_HOST, "user": "fohmixer",
            "identity": "~/k", "preferred": "fohmixer",
            "start_dir_chain": ["devel/fohmixer"]})


class TestDeclaredHumans(unittest.TestCase):

    def test_owner_and_marek_share_the_project_session(self):
        sessions = bootstrap.account_spec("varos")["webterm_sessions"]
        self.assertEqual(set(sessions), {"zbynek", "marek"})
        for human, sess in sessions.items():
            self.assertEqual(sess["preferred"], "varos", human)
            self.assertEqual(sess["start_dir_chain"], [VAROS_DIR], human)

    def test_authorized_keys_carry_both_lane_keys(self):
        forced = [k for k in bootstrap.desired_keys_for_service_account("varos")
                  if k.startswith("restrict,")]
        self.assertEqual(len(forced), 2)
        for human in ("zbynek", "marek"):
            blob = WEBTERM_CONTROLLER_LANE_PUBKEYS[human].split()[1]
            self.assertTrue(any(blob in k for k in forced), human)


if __name__ == "__main__":
    unittest.main()
