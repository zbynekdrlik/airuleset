"""#1339 (owner 16.11.2026): the fleet backup manager (restic + Hetzner Storage Box)
leaves the shared `newlevel` account for its own project account on dev1, so only
that account reads the Storage Box keys and host credentials. Declared PENDING until
its root bootstrap ran (#1267): no tab yet, and the project has no registry row
(it ran unregistered as newlevel), so the registry row and its airuleset peer link
land at the cutover, never as a newlevel row (LEGACY_CEILING only goes down)."""
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
ACCT = "fleet-backup"


class TestFleetBackupAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo_or_docker(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/fleet-backup")
        self.assertIs(self.spec["github_app"], True)
        self.assertEqual(self.spec["repo_secrets"], [])
        self.assertEqual(self.spec["secrets"], [])
        self.assertFalse(self.spec.get("devices"))
        self.assertNotIn("docker", self.spec.get("groups", []))   # root-equivalent

    def test_uv_pin_matches_the_sibling_on_the_same_box(self):
        # One pipx venv per box: the same uv pin nlch-finances declares.
        self.assertEqual(self.spec["tools"], ["pipx:uv==0.9.18"])
        self.assertIn("pipx:uv==0.9.18", bootstrap.account_spec("nlch-finances")["tools"])

    def test_pending_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "fleet-backup@dev1")
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

    def test_drop_port_is_the_next_free_in_range(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8910)
        ports = list(drop._GENERATED_DROP_PORTS.values())
        self.assertEqual(ports.count(8910), 1)
        self.assertNotIn(8910, drop._RESERVED_PORTS)

    def test_no_registry_row_or_peer_link_before_cutover(self):
        # A peer link needs a registry project on the host (cli_peer_channel
        # validation), and a newlevel row would raise LEGACY_CEILING: both land
        # together at the cutover, on the fleet-backup account.
        reg = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        self.assertEqual([r for r in reg if r["name"] == ACCT], [])
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        self.assertEqual([p for p in hosts["dev1"]["links"] if ACCT in p], [])


if __name__ == "__main__":
    unittest.main()
