"""#1267 (owner 29.10.2026): `wall-bridge` (new project, the SNV LED wall Spout
bridge) and `songplayer` (migrated out of `newlevel`) are #1184 project accounts
on dev1, declared BEFORE their root bootstrap runs.

A declared-but-not-bootstrapped account is `pending` in REMOTE_HOSTS. Two
unattended controller paths used to name every declared account anyway:

* the 30-min `project-gh-token mint --all` timer minted every `github_app`
  account, and `account_target` raises on a pending entry, so every run went
  rc 1 (the reason nlvpn/resolume/varos were declared without `github_app`);
* the peer root leg (`cli_peer_channel.render_root_script`) exits 3 on ANY
  missing linked account before changing anything, so a link naming
  wall-bridge would fail the whole dev1 leg on every push.

`cli_account_bootstrap.is_pending` is the one predicate both now honour.
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_accounts  # noqa: E402
import cli_drop_gateway as dg  # noqa: E402
import cli_fleet  # noqa: E402
import cli_peer_channel as cpc  # noqa: E402
import cli_project_ci_sync as ci_sync  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402

NEW = ("wall-bridge", "songplayer")
DEV1 = "100.104.8.125"


def _entry(user):
    (h,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == user]
    return h


class TestDeclaration(unittest.TestCase):

    def test_the_table_stays_clean(self):
        self.assertEqual(bootstrap.validate_all(), {})

    def test_common_shape(self):
        for acct in NEW:
            spec = bootstrap.account_spec(acct)
            self.assertEqual(spec["host"], "dev1", acct)
            self.assertIs(spec["sudo"], False, acct)
            self.assertIs(spec["github_app"], True, acct)
            self.assertEqual(spec["repo"], "zbynekdrlik/" + acct)
            self.assertEqual(spec["project_dir"], "devel/" + acct)
            self.assertEqual(spec["tmux_session"], acct)
            self.assertEqual(spec["webterm_sessions"], {
                "zbynek": {"preferred": acct, "start_dir_chain": ["devel/" + acct]}})
            # declared empty: `accounts verify` needs the allow-list declared
            self.assertEqual(spec["repo_secrets"], [], acct)
            self.assertNotIn("code_only", spec, acct)

    def test_wall_bridge_cross_builds_for_windows_and_holds_no_secret(self):
        spec = bootstrap.account_spec("wall-bridge")
        self.assertEqual(list(spec["secrets"]), [])
        self.assertEqual(spec["tools"], ["rust-target:x86_64-pc-windows-gnu"])

    def test_songplayer_secrets_and_pins(self):
        spec = bootstrap.account_spec("songplayer")
        self.assertEqual(list(spec["secrets"]),
                         ["songplayer_peer_api_key",
                          "songplayer_pp_cf_service_token.json"])
        # the shared Cloudflare token and the unowned Resolume password stay
        # open questions on #1267, never declared here
        for name in ("cloudflare-newlevel-access", "resolume-snv-newlevel-password"):
            self.assertNotIn(name, spec["secrets"])
        self.assertEqual(spec["tools"], ["rust-target:wasm32-unknown-unknown",
                                         "playwright:1.59.1/chromium"])
        # no ruff pin: CI's 0.5.7 would clash with fohmixer's on dev1's ONE venv
        self.assertFalse([t for t in spec["tools"] if t.startswith("pipx:ruff")])

    def test_both_render(self):
        wb = bootstrap.render_root_bootstrap("wall-bridge")
        self.assertIn("ACCOUNT='wall-bridge'", wb)
        self.assertIn("x86_64-pc-windows-gnu", wb)
        self.assertIn("# 11. Project-account GitHub App token shim", wb)
        self.assertIn("tmux new-session -d -s wall-bridge", wb)
        sp = bootstrap.render_root_bootstrap("songplayer")
        self.assertIn("ACCOUNT='songplayer'", sp)
        self.assertIn("wasm32-unknown-unknown", sp)
        self.assertIn("playwright@1.59.1", sp)
        self.assertIn("git clone https://github.com/zbynekdrlik/songplayer.git", sp)


class TestFleet(unittest.TestCase):

    def test_pending_push_targets_on_dev1(self):
        ident = "~/.sec" + "rets/airuleset_push_ed25519"
        for acct in NEW:
            h = _entry(acct)
            self.assertEqual(h["name"], acct + "@dev1")
            self.assertEqual(h["host"], DEV1)
            self.assertEqual(h["identity"], ident)
            self.assertIs(h["pending"], True)

    def test_live_project_accounts_unchanged(self):
        ident = "~/.sec" + "rets/airuleset_push_ed25519"
        self.assertEqual(_entry("fohmixer"), {
            "name": "fohmixer@dev1", "host": DEV1, "user": "fohmixer",
            "repo_path": "~/devel/airuleset", "identity": ident, "pending": False})
        self.assertEqual(_entry("varos"), {
            "name": "varos@dev2", "host": "100.82.64.27", "user": "varos",
            "repo_path": "~/devel/airuleset", "identity": ident, "pending": False})

    def test_full_authority(self):
        for acct in NEW:
            self.assertIn(acct, cli_fleet.FULL_AUTHORITY_USERS)

    def test_fixed_drop_lanes(self):
        for acct, port in (("songplayer", 8894), ("wall-bridge", 8895)):
            lane = dg.DROP_LANES[("dev1", acct)]
            self.assertEqual(lane.port, port)
            self.assertEqual(lane.host, "drop-dev1-%s.newlevel.media" % acct)
            self.assertEqual(lane.topology, "controller")

    def test_is_pending(self):
        for acct in NEW:
            self.assertTrue(bootstrap.is_pending(acct), acct)
        # live accounts, a declared account with no fleet entry, the legacy
        # account and an unknown name are never pending
        for acct in ("fohmixer", "varos", "camera-box", "newlevel", "nobody"):
            self.assertFalse(bootstrap.is_pending(acct), acct)

    def test_a_paused_account_is_never_contacted_either(self):
        paused = [dict(h, paused="dispute 29.10.") if h.get("user") == "fohmixer"
                  else h for h in cli_fleet.REMOTE_HOSTS]
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", paused):
            self.assertTrue(bootstrap.is_pending("fohmixer"))
            self.assertEqual(bootstrap.account_remote("fohmixer")["user"], "fohmixer")
        self.assertIsNone(bootstrap.account_remote("newlevel"))
        self.assertIsNone(bootstrap.account_remote("camera-box"))   # no entry

    def test_is_pending_reads_the_accounts_own_host(self):
        other = [dict(h, name="wall-bridge@dev2") if h.get("user") == "wall-bridge"
                 else h for h in cli_fleet.REMOTE_HOSTS]
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", other):
            self.assertFalse(bootstrap.is_pending("wall-bridge"))


class TestRegistry(unittest.TestCase):

    def test_wall_bridge_row(self):
        (row,) = [e for e in cpc.load_registry() if e["name"] == "wall-bridge"]
        self.assertEqual(row["host"], "dev1")
        self.assertEqual(row["account"], "wall-bridge")
        self.assertEqual(row["path"], "/home/wall-bridge/devel/wall-bridge")
        self.assertFalse(cli_accounts.is_legacy_account(row["account"]))

    def test_songplayer_row_waits_for_the_cutover(self):
        # the registry says where the checkout lives NOW; the live newlevel
        # session is mapped by account + path until the cutover flips it
        (row,) = [e for e in cpc.load_registry() if e["name"] == "songplayer"]
        self.assertEqual(row["account"], "newlevel")


class TestMinterSkipsPending(unittest.TestCase):

    def _mint_all(self):
        out = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                redirect_stdout(out):
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account=None, all=True, dry_run=False, key=None))
        return rc, [c.args[0] for c in m.call_args_list], out.getvalue()

    def test_mint_all_skips_pending_accounts_loudly(self):
        self.assertEqual(pgt.github_app_accounts(),
                         ["fohmixer", "songplayer", "varos", "wall-bridge"])
        with mock.patch.object(ci_sync, "run_after_mint", return_value=0):
            rc, minted, out = self._mint_all()
        self.assertEqual(rc, 0)
        self.assertEqual(minted, ["fohmixer", "varos"])
        for acct in NEW:
            self.assertIn("project-gh-token: %s: SKIPPED — pending" % acct, out)

    def test_a_live_account_is_minted_again(self):
        live = [dict(h, pending=False) for h in cli_fleet.REMOTE_HOSTS]
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", live), \
                mock.patch.object(ci_sync, "run_after_mint", return_value=0):
            rc, minted, out = self._mint_all()
        self.assertEqual(minted, ["fohmixer", "songplayer", "varos", "wall-bridge"])
        self.assertNotIn("SKIPPED", out)

    def test_an_explicit_mint_is_never_skipped(self):
        with mock.patch.object(pgt, "mint_account", return_value=1) as m:
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account="wall-bridge", all=False, dry_run=False,
                key=None))
        self.assertEqual(rc, 1)
        self.assertEqual([c.args[0] for c in m.call_args_list], ["wall-bridge"])

    def test_secret_sync_skips_pending_accounts(self):
        with mock.patch.object(ci_sync, "sync_account", return_value=0) as s, \
                mock.patch.object(bootstrap, "is_pending",
                                  side_effect=lambda a: a == "varos"):
            ci_sync.sync_all(run=lambda *a, **k: None)
        self.assertEqual([c.args[0] for c in s.call_args_list],
                         ["fohmixer", "songplayer", "wall-bridge"])


class TestPeerLinks(unittest.TestCase):

    def test_the_owner_named_links(self):
        self.assertEqual(cpc.load_links()["hosts"]["dev1"]["links"],
                         [["songplayer", "wall-bridge"], ["camera-box", "songplayer"]])
        self.assertEqual(cpc.validate_links(cpc.load_links(), cpc.load_registry()), [])

    def test_root_leg_names_only_existing_accounts(self):
        # camera-box and songplayer run under newlevel today; wall-bridge is
        # pending, so the root script never hits its `account missing` exit 3
        accts = cpc.accounts_for_host("dev1")
        self.assertEqual(accts, ["newlevel"])
        self.assertNotIn("wall-bridge", cpc.render_root_script(accts))

    def test_wall_bridge_joins_once_live(self):
        with mock.patch.object(bootstrap, "is_pending", return_value=False):
            self.assertEqual(cpc.accounts_for_host("dev1"), ["newlevel", "wall-bridge"])


if __name__ == "__main__":
    unittest.main()
