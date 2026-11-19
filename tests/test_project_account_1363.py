"""#1363 (owner 18.11.2026): companion-updater (the bkshading-simple Companion
module and its deploy to the SNV and PP Companions) leaves the shared `newlevel`
account for its own project account on dev1, so only that account holds the
Companion rigs' root password. The owner and Marek (the old session lived in his
tmux) get a tab, aliased `comp`. Its registry row moves from newlevel to the
account, so LEGACY_CEILING drops back to 20."""
import json
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_accounts
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_gh_token as pgt
import cli_webterm as w
import cli_webterm_profiles as profiles
from cli_aliases import short_target_alias

ROOT = Path(__file__).resolve().parent.parent
ACCT = "companion-updater"
INFRA_TOPIC = ("airuleset infra: accounts, permissions, peer links, tooling. Ask or "
               "report here; airuleset decides and a ticket alone wakes no session")


def _json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class TestCompanionUpdaterAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/companion-updater")
        self.assertIs(self.spec["github_app"], True)
        self.assertEqual(self.spec["repo_secrets"], [])
        self.assertEqual(self.spec["project_dir"], "devel/companion-updater")
        self.assertEqual(self.spec["tmux_session"], "companion-updater")
        self.assertFalse(self.spec.get("devices"))

    def test_only_the_companion_root_pass_is_declared(self):
        # COMPANION_PASS for module/deploy.sh; an inventory, never provisioned here.
        self.assertEqual(self.spec["secrets"], ["companion-pass"])

    def test_live_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "companion-updater@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], False)
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)
        self.assertIn(ACCT, pgt.github_app_accounts())

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            sess = self.spec["webterm_sessions"][human]
            self.assertEqual(sess["start_dir_chain"], ["devel/companion-updater"])
            tabs = w.WEBTERM_DASHBOARD_TABS[human]
            self.assertEqual(tabs.index(ACCT), tabs.index("fleet-backup") + 1)
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_tab_alias_is_comp_not_the_8_char_cut(self):
        self.assertEqual(short_target_alias(ACCT, "companion-updater-dev1"), "comp")
        # No other declared account or fleet user maps to the same alias.
        users = set(bootstrap.SERVICE_ACCOUNTS) | {
            h.get("user", "") for h in cli_fleet.REMOTE_HOSTS}
        clash = [u for u in users - {ACCT} if short_target_alias(u, u) == "comp"]
        self.assertEqual(clash, [])

    def test_drop_port_is_the_next_free_in_range(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8915)
        ports = list(drop._GENERATED_DROP_PORTS.values())
        self.assertEqual(ports.count(8915), 1)
        self.assertNotIn(8915, drop._RESERVED_PORTS)

    def test_registry_row_moved_to_the_account(self):
        (row,) = [r for r in _json("projects-registry.json") if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"], row["host"]),
                         (ACCT, "/home/companion-updater/devel/companion-updater", "dev1"))
        self.assertEqual((row["branch_model"], row["default_branch"], row["work_branch"]),
                         ("2-branch", "main", "dev"))
        self.assertEqual(row["onboarded"], "2026-11-18")
        self.assertIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_peer_links_keep_shading_and_add_airuleset(self):
        dev1 = _json("peer-links.json")["hosts"]["dev1"]
        pairs = sorted(sorted(p) for p in dev1["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT], [ACCT, "shading"]])
        self.assertEqual(dev1["topics"]["airuleset|companion-updater"], INFRA_TOPIC)
        self.assertIn("companion-updater|shading", dev1["topics"])

    def test_legacy_ceiling_back_to_20(self):
        # 20 after this migration; later ones (#1373 restreamer) only lower it.
        self.assertLessEqual(cli_accounts.LEGACY_CEILING, 20)


if __name__ == "__main__":
    unittest.main()
