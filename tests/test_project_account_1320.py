"""#1320 (owner 13.11.2026): `devbridge` leaves the shared `newlevel` account for
its own project account on dev1, the first step towards a peer bridge to david3.
Declared PENDING until its root bootstrap ran (#1267): no tab yet, and the
registry row stays on `newlevel` until the cutover."""
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cli_account_bootstrap as bootstrap
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_ci_sync as ci_sync
import cli_project_gh_token as pgt
import cli_webterm as w
import cli_webterm_profiles as profiles

ROOT = Path(__file__).resolve().parent.parent
ACCT = "devbridge"


class TestDevbridgeAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/devbridge")
        self.assertIs(self.spec["github_app"], True)
        self.assertEqual(self.spec["repo_secrets"], [])
        self.assertFalse(self.spec.get("devices"))

    def test_pending_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "devbridge@dev1")
        self.assertIs(entry["pending"], True)
        self.assertTrue(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def test_unattended_paths_skip_it_while_pending(self):
        # #1267: mint --all skips loudly at rc 0, and the peer root leg leaves it out.
        out = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
                redirect_stdout(out):
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account=None, all=True, dry_run=False, key=None))
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, pgt.github_app_accounts())
        self.assertNotIn(ACCT, [c.args[0] for c in m.call_args_list])
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out.getvalue())
        self.assertNotIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_no_dashboard_tab_while_pending(self):
        # #1267: the tab rides the pending->live flip commit.
        for human, tabs in w.WEBTERM_DASHBOARD_TABS.items():
            self.assertNotIn(ACCT, tabs, human)
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            self.assertEqual([e for e in inv if e["id"] == ACCT], [])

    def test_drop_port_and_airuleset_peer_link_only(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8909)
        ports = list(drop._GENERATED_DROP_PORTS.values())
        self.assertEqual(ports.count(8909), 1)
        self.assertNotIn(8909, drop._RESERVED_PORTS)
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT]])
        self.assertIn("airuleset|devbridge", hosts["dev1"]["topics"])

    def test_registry_row_stays_on_newlevel_until_cutover(self):
        reg = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        (row,) = [r for r in reg if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"]), ("newlevel", "~/devel/devbridge"))


if __name__ == "__main__":
    unittest.main()
