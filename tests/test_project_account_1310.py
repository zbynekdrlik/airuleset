"""#1310 (owner 12.11.2026): `mikrotik-pz`, a Claude manager for the MikroTik
router at slovnormal's PZ site, gets its own account on dev1 and a tab for
Marek and the owner. No device, NIC or sudo grant until the project asks."""
import json
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_drop_lanes as drop
import cli_fleet
import cli_webterm as w
import cli_webterm_profiles as profiles

ROOT = Path(__file__).resolve().parent.parent
ACCT = "mikrotik-pz"


class TestMikrotikPzAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/mikrotik-pz")
        self.assertEqual(self.spec["repo_secrets"], [])

    def test_no_device_or_nic_grant_yet(self):
        self.assertFalse(self.spec.get("devices"))
        self.assertFalse(self.spec.get("nic_noqueue"))

    def test_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "mikrotik-pz@dev1")
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            sess = self.spec["webterm_sessions"][human]
            self.assertEqual(sess["start_dir_chain"], ["devel/mikrotik-pz"])
            self.assertIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_drop_port_peer_link_and_registry(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8908)
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT]])
        self.assertIn("airuleset|mikrotik-pz", hosts["dev1"]["topics"])
        reg = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        (row,) = [r for r in reg if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"]), (ACCT, "/home/mikrotik-pz/devel/mikrotik-pz"))


if __name__ == "__main__":
    unittest.main()
