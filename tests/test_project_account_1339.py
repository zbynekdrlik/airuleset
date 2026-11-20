"""#1339 (owner 16.11.2026): the fleet backup manager (restic + Hetzner Storage Box)
leaves the shared `newlevel` account for its own project account on dev1, so only
that account reads the Storage Box keys and host credentials. Declared pending, then
flipped live once the root bootstrap ran on dev1 (16.11.) with the owner + marek tab
(#1267). The project has no registry row (it ran unregistered as newlevel), so the
registry row and its airuleset peer link land at the cutover, never as a newlevel
row (LEGACY_CEILING only goes down)."""
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


def _pending():
    """REMOTE_HOSTS with fleet-backup marked pending again (the state before its
    root bootstrap), so the skip paths keep their coverage."""
    return mock.patch.object(cli_fleet, "REMOTE_HOSTS", [
        dict(h, pending=True) if h.get("user") == ACCT else h
        for h in cli_fleet.REMOTE_HOSTS])


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

    def test_live_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "fleet-backup@dev1")
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
        # The cutover added the registry row on the fleet-backup account, so the
        # peer root leg now makes its socket dir.
        self.assertIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            sess = self.spec["webterm_sessions"][human]
            self.assertEqual(sess["start_dir_chain"], ["devel/fleet-backup"])
            self.assertIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
            tabs = w.WEBTERM_DASHBOARD_TABS[human]
            self.assertEqual(tabs.index(ACCT), tabs.index("devbridge") + 1)
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_drop_port_is_the_next_free_in_range(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8910)
        ports = list(drop._GENERATED_DROP_PORTS.values())
        self.assertEqual(ports.count(8910), 1)
        self.assertNotIn(8910, drop._RESERVED_PORTS)

    def test_registry_row_and_peer_link_at_cutover(self):
        # The cutover lands the row on the fleet-backup account (never newlevel,
        # so LEGACY_CEILING is untouched) and the one airuleset link, with a topic.
        reg = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        (row,) = [r for r in reg if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"], row["host"]),
                         (ACCT, "/home/fleet-backup/devel/fleet-backup", "dev1"))
        dev1 = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]["dev1"]
        # The cutover's own link must exist; later owner-requested links (#1386) may join it.
        self.assertIn(["airuleset", ACCT], [sorted(p) for p in dev1["links"] if ACCT in p])
        self.assertIn("airuleset|" + ACCT, dev1["topics"])
        self.assertEqual(cpc.validate_links(cpc.load_links(), cpc.load_registry()), [])


if __name__ == "__main__":
    unittest.main()
