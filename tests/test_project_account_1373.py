"""#1373 (owner 19.11.2026): restreamer leaves the shared `newlevel` account on
dev1 for its own project account, so the declared camera-box <-> restreamer peer
link can go live. This is the PENDING phase (#1267 order): the account is declared
before its root bootstrap, so no unattended controller path contacts the missing
user, its registry row still names `newlevel`, and no dashboard tab exists yet.
The tools are restreamer's own CI and lockfile pins, minus every cargo tool whose
CI version differs from a sibling's on dev1's one shared cargo bin."""
import json
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_gh_token as pgt
import cli_project_toolchain as toolchain
import cli_webterm as w

ROOT = Path(__file__).resolve().parent.parent
ACCT = "restreamer"


def _json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class TestRestreamerAccountPending(unittest.TestCase):
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
            "playwright:1.58.2/chromium",            # e2e lockfile
        ]))

    def test_no_cargo_pin_differs_from_a_sibling_on_the_shared_bin(self):
        # A pinned cargo tool always reinstalls (#1275), so two versions of one
        # crate on dev1 would replace each other on every render.
        mine = {c: v for c, v in toolchain.plan(self.spec)["cargo"] if v}
        for other, raw in bootstrap.SERVICE_ACCOUNTS.items():
            if other == ACCT or raw.get("host") != "dev1":
                continue
            theirs = {c: v for c, v in toolchain.plan(bootstrap.account_spec(other))["cargo"] if v}
            for crate, ver in mine.items():
                self.assertEqual(theirs.get(crate, ver), ver, (other, crate))

    def test_secret_inventory_names_the_restreamer_secrets_files(self):
        # ~/.restreamer-secrets/{stream-lan,manager-server}.env, copied at cutover.
        self.assertEqual(self.spec["secrets"], ["stream-lan.env", "manager-server.env"])

    def test_fleet_entry_is_pending_full_authority(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "restreamer@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], True)
        self.assertTrue(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)
        self.assertIn(ACCT, pgt.github_app_accounts())

    def test_webterm_sessions_declared_but_no_tab_before_the_flip(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            self.assertEqual(self.spec["webterm_sessions"][human]["start_dir_chain"],
                             ["devel/restreamer"])
        for human, tabs in w.WEBTERM_DASHBOARD_TABS.items():
            self.assertNotIn(ACCT, tabs, human)

    def test_drop_port_is_fixed_and_free(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8916)
        self.assertEqual(list(drop._GENERATED_DROP_PORTS.values()).count(8916), 1)
        self.assertNotIn(8916, drop._RESERVED_PORTS)

    def test_registry_row_stays_newlevel_until_cutover(self):
        (row,) = [r for r in _json("projects-registry.json") if r["name"] == ACCT]
        self.assertEqual((row["account"], row["path"], row["host"]),
                         ("newlevel", "~/devel/restreamer", "dev1"))

    def test_camera_box_link_kept_and_the_pending_account_is_left_out(self):
        dev1 = _json("peer-links.json")["hosts"]["dev1"]
        self.assertIn(["camera-box", ACCT], [sorted(p) for p in dev1["links"]])
        self.assertIn("camera-box|restreamer", dev1["topics"])
        self.assertEqual(cpc.validate_links(_json("peer-links.json"),
                                            _json("projects-registry.json")), [])
        self.assertNotIn(ACCT, cpc.accounts_for_host("dev1"))


if __name__ == "__main__":
    unittest.main()
