"""#1373 (owner 19.11.2026): restreamer leaves the shared `newlevel` account on
dev1 for its own project account, so the declared camera-box <-> restreamer peer
link can go live. Declared pending, then flipped live at the cutover (19.11.: root
bootstrap, install, transfer-session, checkout moved) with the owner + marek tab
(alias `restream`, the 8-char cut); the registry row moves to the account, the
airuleset infra link joins, and LEGACY_CEILING drops 20 -> 19. The pending path
keeps its coverage by re-marking the account pending through a REMOTE_HOSTS patch.
The tools are restreamer's own CI and lockfile pins, minus every cargo tool whose
CI version differs from a sibling's on dev1's one shared cargo bin (validate_tools
refuses such a clash, tests/test_toolchain_cargo_pins_1373.py)."""
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cli_account_bootstrap as bootstrap
import cli_accounts
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_ci_sync as ci_sync
import cli_project_gh_token as pgt
import cli_webterm as w
import cli_webterm_profiles as profiles
from cli_aliases import short_target_alias

ROOT = Path(__file__).resolve().parent.parent
ACCT = "restreamer"
INFRA_TOPIC = ("airuleset infra: accounts, permissions, peer links, tooling. Ask or "
               "report here; airuleset decides and a ticket alone wakes no session")


def _json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _pending():
    """REMOTE_HOSTS with restreamer marked pending again (the state before its
    root bootstrap), so the skip paths keep their coverage."""
    return mock.patch.object(cli_fleet, "REMOTE_HOSTS", [
        dict(h, pending=True) if h.get("user") == ACCT else h
        for h in cli_fleet.REMOTE_HOSTS])


class TestRestreamerAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/restreamer")
        self.assertIs(self.spec["github_app"], True)
        self.assertEqual(self.spec["repo_secrets"], [])   # all 20 CI secrets already set
        self.assertEqual(self.spec["project_dir"], "devel/restreamer")
        self.assertEqual(self.spec["tmux_session"], "restreamer")
        for grant in ("devices", "rtprio", "userns", "system_packages", "sudo_commands"):
            self.assertFalse(self.spec.get(grant), grant)

    def test_tools_are_its_ci_and_lockfile_pins(self):
        self.assertEqual(sorted(self.spec["tools"]), sorted([
            "rust-target:wasm32-unknown-unknown",   # leptos-ui
            "cargo:trunk",                           # unpinned in CI
            "cargo:cargo-mutants@27.1.0",            # CI pin
            "cargo:cargo-audit",                     # unpinned in CI
            "binaryen:version_131",                  # trunk data-wasm-opt
            "playwright:1.58.2/chromium",            # its untracked e2e lock; CI floats ^1.50.0
        ]))

    def test_secret_inventory_names_the_restreamer_secrets_files(self):
        # ~/.restreamer-secrets/{stream-lan,manager-server}.env, copied at cutover.
        self.assertEqual(self.spec["secrets"], ["stream-lan.env", "manager-server.env"])

    def test_live_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "restreamer@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], False)
        self.assertFalse(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)
        self.assertIn(ACCT, pgt.github_app_accounts())

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
        # #1267: mint --all skips loudly at rc 0; the CI secret sync and the
        # peer root leg leave it out.
        with _pending():
            self.assertTrue(bootstrap.is_pending(ACCT))
            rc, minted, out = self._mint_all()
            with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
                ci_sync.sync_all(run=lambda *a, **k: None)
            accts = cpc.accounts_for_host("dev1")
        self.assertEqual(rc, 0)
        self.assertNotIn(ACCT, minted)
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out)
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])
        self.assertNotIn(ACCT, accts)

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            self.assertEqual(self.spec["webterm_sessions"][human]["start_dir_chain"],
                             ["devel/restreamer"])
            tabs = w.WEBTERM_DASHBOARD_TABS[human]
            self.assertEqual(tabs.index(ACCT), tabs.index("companion-updater") + 1)
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_tab_alias_is_the_8_char_cut_without_a_clash(self):
        self.assertEqual(short_target_alias(ACCT, "restreamer-dev1"), "restream")
        users = set(bootstrap.SERVICE_ACCOUNTS) | {
            h.get("user", "") for h in cli_fleet.REMOTE_HOSTS}
        clash = [u for u in users - {ACCT} if short_target_alias(u, u) == "restream"]
        self.assertEqual(clash, [])

    def test_drop_port_is_fixed_and_free(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8916)
        self.assertEqual(list(drop._GENERATED_DROP_PORTS.values()).count(8916), 1)
        self.assertNotIn(8916, drop._RESERVED_PORTS)

    def test_registry_row_moved_to_the_account_at_cutover(self):
        (row,) = [r for r in _json("projects-registry.json") if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"], row["host"]),
                         (ACCT, "/home/restreamer/devel/restreamer", "dev1"))
        self.assertEqual((row["branch_model"], row["default_branch"], row["work_branch"]),
                         ("2-branch", "main", "dev"))
        self.assertLessEqual(cli_accounts.LEGACY_CEILING, 19)   # #1401 mikrotik-nlm: 18

    def test_peer_links_keep_camera_box_and_add_airuleset(self):
        dev1 = _json("peer-links.json")["hosts"]["dev1"]
        pairs = sorted(sorted(p) for p in dev1["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT], ["camera-box", ACCT]])
        self.assertEqual(dev1["topics"]["airuleset|restreamer"], INFRA_TOPIC)
        self.assertIn("camera-box|restreamer", dev1["topics"])
        self.assertEqual(cpc.validate_links(_json("peer-links.json"),
                                            _json("projects-registry.json")), [])
        self.assertIn(ACCT, cpc.accounts_for_host("dev1"))


if __name__ == "__main__":
    unittest.main()
