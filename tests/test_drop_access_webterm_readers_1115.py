"""#1115 reopen, part 2 (owner 2026-09-23: „a na mareka si preco zabudol!!!!
drlik.marek@gmail.com ved aj on ma pristup k webtermu!"): whoever the webterm
lets open an account must also pass that account's drop lane. The webterm
profile inventories (which accounts each human's dashboard connects to) and the
webterm Access include lists (the human's login emails) are the ONE source."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as g
import cli_drop_lanes as dl
import cli_fleet
import cli_webterm_access as wa
import cli_webterm_profiles as wp

PROFILES = {"david": wp.david_inventory, "marek": wp.marek_inventory,
            "dominika": wp.dominika_inventory}


def _lane_host_for(user, host):
    for entry in cli_fleet.REMOTE_HOSTS:
        if entry.get("user") == user and entry.get("host") == host:
            lane = g.DROP_LANES.get((dl._nodename_for_entry(entry), user))
            return lane.host if lane is not None and lane.access else None
    return None


class TestWebtermReadersOnDropLanes1115(unittest.TestCase):
    def test_every_webterm_reader_passes_the_drop_lane_of_each_account(self):
        checked = 0
        for profile, inventory in PROFILES.items():
            emails = set(wa.WEBTERM_ACCESS_APPS[profile]["allowed_emails"])
            for e in inventory():
                host = _lane_host_for(e.get("user"), e.get("host"))
                if host is None:
                    continue
                have = set(g.DROP_ACCESS_APPS[host]["allowed_emails"])
                self.assertTrue(emails <= have,
                                f"{profile} can open {e.get('id')} in the "
                                f"webterm but not its drop lane {host}")
                checked += 1
        self.assertGreater(checked, 10)

    def test_marek_is_on_his_montalu_and_miva_lanes(self):
        for host in ("drop-subdev-montalu1.newlevel.media",
                     "drop-subdev-montalu2.newlevel.media",
                     "drop-subdev-montalu4.newlevel.media",
                     "drop-subdev-miva1.newlevel.media"):
            self.assertIn("drlik.marek@gmail.com",
                          g.DROP_ACCESS_APPS[host]["allowed_emails"], host)

    def test_no_reader_lands_on_an_account_the_webterm_does_not_open(self):
        # montalu7/8 are on no programmer's dashboard: owner only.
        for host in ("drop-subdev-montalu7.newlevel.media",
                     "drop-subdev-montalu8.newlevel.media"):
            self.assertEqual(g.DROP_ACCESS_APPS[host]["allowed_emails"],
                             ["drlik.zbynek@gmail.com"], host)


if __name__ == "__main__":
    unittest.main()
