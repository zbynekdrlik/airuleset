"""#1320 (owner 13.11.2026): `devbridge` leaves the shared `newlevel` account for
its own project account on dev1, the first step towards a peer bridge to david3.
Declared pending, then flipped live once the root bootstrap ran on dev1
(uid 1017, 14.11.) with the owner + marek tab (#1267). The registry row moved
to the account at the cutover (transfer-session, 14.11.)."""
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


def _pending():
    """REMOTE_HOSTS with devbridge marked pending again (the state before its
    root bootstrap), so the skip paths keep their coverage."""
    return mock.patch.object(cli_fleet, "REMOTE_HOSTS", [
        dict(h, pending=True) if h.get("user") == ACCT else h
        for h in cli_fleet.REMOTE_HOSTS])


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

    def test_live_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "devbridge@dev1")
        self.assertIs(entry["pending"], False)
        self.assertFalse(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def _mint_all(self):
        out = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
                redirect_stdout(out):
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account=None, all=True, dry_run=False, key=None))
        return rc, [c.args[0] for c in m.call_args_list], out.getvalue()

    def test_mint_all_mints_it_once_live(self):
        rc, minted, out = self._mint_all()
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, minted)
        self.assertNotIn("%s: SKIPPED" % ACCT, out)

    def test_unattended_paths_skip_it_while_pending(self):
        # #1267: mint --all skips loudly at rc 0, and the CI secret sync skips it.
        with _pending():
            self.assertTrue(bootstrap.is_pending(ACCT))
            rc, minted, out = self._mint_all()
            with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
                ci_sync.sync_all(run=lambda *a, **k: None)
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, pgt.github_app_accounts())
        self.assertNotIn(ACCT, minted)
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out)
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])

    def test_peer_socket_after_the_cutover(self):
        # The cutover (14.11.) moved the registry row to the account, so the
        # peer root leg gives devbridge its own socket dir.
        self.assertIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            sess = self.spec["webterm_sessions"][human]
            self.assertEqual(sess["start_dir_chain"], ["devel/devbridge"])
            self.assertIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_drop_port_and_airuleset_peer_link_only(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8909)
        ports = list(drop._GENERATED_DROP_PORTS.values())
        self.assertEqual(ports.count(8909), 1)
        self.assertNotIn(8909, drop._RESERVED_PORTS)
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT]])
        self.assertIn("airuleset|devbridge", hosts["dev1"]["topics"])

    def test_registry_row_moved_to_the_account_at_cutover(self):
        reg = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        (row,) = [r for r in reg if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"]),
                         (ACCT, "/home/devbridge/devel/devbridge"))


class TestReleasesMirror(unittest.TestCase):
    def test_token_also_covers_the_public_releases_repo(self):
        # devbridge#123 (owner 4.11.2026): code private, installers in a public mirror.
        self.assertEqual(bootstrap.account_spec(ACCT)["extra_repos"],
                         ["zbynekdrlik/devbridge-releases"])


if __name__ == "__main__":
    unittest.main()
