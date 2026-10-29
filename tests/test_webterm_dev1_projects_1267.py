"""#1267 (owner 29.10.2026): the wall-bridge@dev1 and songplayer@dev1 project
accounts went live (root bootstrap, uid 1003/1004), so each gets ONE webterm tab
on the OWNER's dashboard only, right after `varos` — the #1220 project-account
shape (`project_account_entry`, ONE shared project session, the owner's own
lane key). Marek is not in their `webterm_sessions`, so his dashboard stays
unchanged."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_fleet  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
from cli_webterm_only import WEBTERM_CONTROLLER_LANE_PUBKEYS  # noqa: E402

NEW = ("wall-bridge", "songplayer")


def _tab(inv, acct):
    return [e for e in inv if e["id"] == acct]


class TestOwnerTabs(unittest.TestCase):

    def test_owner_inventory_has_each_tab(self):
        inv = profiles.zbynek_inventory()
        for acct in NEW:
            got = _tab(inv, acct)
            self.assertEqual(len(got), 1, [e["id"] for e in inv])
            self.assertEqual(got[0], {
                "id": acct, "label": "%s (dev1)" % acct, "kind": "owner",
                "local": False, "host": profiles.FOHMIXER_HOST, "user": acct,
                "identity": profiles.WEBTERM_ZBYNEK_IDENTITY, "preferred": acct,
                "start_dir_chain": ["devel/" + acct]})

    def test_owner_dashboard_lists_them_after_varos(self):
        tabs = w.WEBTERM_DASHBOARD_TABS["zbynek"]
        i = tabs.index("varos")
        self.assertEqual(tabs[i + 1:i + 3], ["wall-bridge", "songplayer"])

    def test_the_owner_render_shows_both(self):
        html = w.render_dashboard_html(
            profiles.zbynek_inventory(), ttyd_base="/t", human="zbynek",
            term_grid=(176, 51))
        aliases = re.findall(r'<span class="al">([^<]+)</span>', html)
        i = aliases.index("varos")
        # the single #592 cli_aliases source cuts an account alias to 8 chars
        self.assertEqual(aliases[i + 1:i + 3], ["wall-bri", "songplay"])

    def test_no_other_dashboard_gets_them(self):
        for human, tabs in w.WEBTERM_DASHBOARD_TABS.items():
            if human != "zbynek":
                for acct in NEW:
                    self.assertNotIn(acct, tabs, human)
        for inv in (profiles.marek_inventory, profiles.david_inventory,
                    profiles.dominika_inventory, profiles.timo_inventory):
            for acct in NEW:
                self.assertEqual(_tab(inv(), acct), [], inv.__name__)


class TestDriftLocks(unittest.TestCase):

    def test_host_is_the_fleet_entry(self):
        # zero-import leaf duplicate, drift-locked to the ONE fleet source
        for acct in NEW:
            (fleet,) = [h for h in cli_fleet.REMOTE_HOSTS
                        if h["name"] == acct + "@dev1"]
            self.assertEqual(profiles.FOHMIXER_HOST, fleet["host"], acct)
        self.assertEqual(bootstrap.fleet_boxes()["dev1"], profiles.FOHMIXER_HOST)

    def test_tabs_match_the_declaration(self):
        for acct in NEW:
            spec = bootstrap.account_spec(acct)
            self.assertEqual(set(spec["webterm_sessions"]), {"zbynek"}, acct)
            (e,) = _tab(profiles.zbynek_inventory(), acct)
            sess = spec["webterm_sessions"]["zbynek"]
            self.assertEqual(e["preferred"], sess["preferred"], acct)
            self.assertEqual(e["start_dir_chain"], sess["start_dir_chain"], acct)
            self.assertEqual(spec["host"], "dev1", acct)

    def test_authorized_keys_carry_the_owner_lane_key(self):
        blob = WEBTERM_CONTROLLER_LANE_PUBKEYS["zbynek"].split()[1]
        for acct in NEW:
            forced = [k for k in bootstrap.desired_keys_for_service_account(acct)
                      if k.startswith("restrict,")]
            self.assertEqual(len(forced), 1, acct)
            self.assertIn(blob, forced[0], acct)


if __name__ == "__main__":
    unittest.main()
