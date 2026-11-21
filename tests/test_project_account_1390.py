"""#1390 (owner 21.11.2026): wall-songs, a new project that replaces Resolume on the
SNV songs PC (LED-wall visuals for the live band during songs), gets its own
project account on dev1. Declared PENDING until its root bootstrap ran (#1267):
no unattended controller path contacts the missing user, no dashboard tab yet.
It is a brand-new project with no registry row, so the registry row and its peer
links (airuleset infra, wall-bridge) land at the cutover on the wall-songs
account, never as a newlevel row (LEGACY_CEILING only goes down)."""
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
ACCT = "wall-songs"


def _json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class TestWallSongsAccountPending(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo_or_grants(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/wall-songs")
        self.assertIs(self.spec["github_app"], True)
        self.assertEqual(self.spec["repo_secrets"], [])   # else verify FAILs secret-sync
        self.assertEqual(self.spec["secrets"], [])
        self.assertEqual(self.spec["project_dir"], "devel/wall-songs")
        self.assertEqual(self.spec["tmux_session"], "wall-songs")
        self.assertNotIn("docker", self.spec.get("groups", []))   # root-equivalent
        for grant in ("devices", "rtprio", "userns", "system_packages", "sudo_commands"):
            self.assertFalse(self.spec.get(grant), grant)

    def test_no_tool_pins_beyond_the_baseline(self):
        # The repo holds only README + CLAUDE.md: no CI, toolchain file or lockfile.
        self.assertEqual(self.spec["tools"], [])

    def test_pending_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "wall-songs@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], True)
        self.assertTrue(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def test_unattended_paths_skip_it_while_pending(self):
        # #1267: mint --all skips loudly at rc 0, the CI secret sync skips it,
        # and the peer root leg leaves it out.
        out = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
                redirect_stdout(out):
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account=None, all=True, dry_run=False, key=None))
        with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
            ci_sync.sync_all(run=lambda *a, **k: None)
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, pgt.github_app_accounts())
        self.assertNotIn(ACCT, [c.args[0] for c in m.call_args_list])
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out.getvalue())
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])
        self.assertNotIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_webterm_sessions_declared_but_no_tab_before_the_flip(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            self.assertEqual(self.spec["webterm_sessions"][human]["start_dir_chain"],
                             ["devel/wall-songs"])
        for human, tabs in w.WEBTERM_DASHBOARD_TABS.items():
            self.assertNotIn(ACCT, tabs, human)
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            self.assertEqual([e for e in inv if e["id"] == ACCT], [])

    def test_drop_port_is_fixed_and_free(self):
        # #1237: a project account keeps the base channels (secret/upload/share).
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8917)
        self.assertEqual(list(drop._GENERATED_DROP_PORTS.values()).count(8917), 1)
        self.assertNotIn(8917, drop._RESERVED_PORTS)

    def test_no_registry_row_or_peer_link_before_cutover(self):
        # A peer link needs a registry project on the host (validate_links), and
        # a newlevel row would raise LEGACY_CEILING: both land at the cutover.
        self.assertEqual([r for r in _json("projects-registry.json") if r["name"] == ACCT], [])
        dev1 = _json("peer-links.json")["hosts"]["dev1"]
        self.assertEqual([p for p in dev1["links"] if ACCT in p], [])
        self.assertEqual([t for t in dev1["topics"] if ACCT in t.split("|")], [])


if __name__ == "__main__":
    unittest.main()
