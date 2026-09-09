"""#960: claudy migration to controller — drift-lock and structural tests.

Locks the claudy@controller fleet entry, webterm inventory entries, dashboard
tabs, bootstrap renderer, notify routing, and authority classification against
drift from the ONE source of truth for each constant.
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_aliases  # noqa: E402
import cli_fleet  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402


class TestClaudyFleetEntry(unittest.TestCase):
    """The claudy@controller REMOTE_HOSTS entry."""

    def _entry(self):
        for h in cli_fleet.REMOTE_HOSTS:
            if h.get("name") == "claudy@controller":
                return h
        self.fail("claudy@controller not in REMOTE_HOSTS")

    def test_entry_exists_with_correct_host(self):
        e = self._entry()
        self.assertEqual(e["host"], "100.101.214.103")

    def test_entry_user_is_claudy(self):
        self.assertEqual(self._entry()["user"], "claudy")

    def test_entry_has_explicit_identity(self):
        # R2 Fable review: explicit identity prevents sshpass fallback
        self.assertEqual(self._entry()["identity"],
                         "~/.secrets/airuleset_push_ed25519")

    def test_entry_is_active(self):
        # R2 (flipped 2026-09-09 21:33 UTC+2): the claudy account exists on the
        # controller (root bootstrap ran) and its first install landed, so the
        # target is ACTIVE — push deploys to it like any other host.
        self.assertFalse(self._entry().get("pending", False))

    def test_active_entry_included_in_deployable_hosts(self):
        # Y6 (flipped): an ACTIVE entry is deployed by push.
        from cli_remote import _deployable_hosts
        names = [h["name"] for h in _deployable_hosts()]
        self.assertIn("claudy@controller", names)

    def test_entry_repo_path(self):
        self.assertEqual(self._entry()["repo_path"], "~/devel/airuleset")


class TestClaudyAuthority(unittest.TestCase):
    """Authority classification — claudy is a full-authority service account."""

    def test_claudy_in_full_authority_users(self):
        self.assertIn("claudy", cli_fleet.FULL_AUTHORITY_USERS)

    def test_claudy_not_in_authority_by_user(self):
        # NOT a sub-dev stream — must not be in the reduced-authority table
        self.assertNotIn("claudy", cli_fleet.AUTHORITY_BY_USER)

    def test_claudy_not_in_webterm_only_users(self):
        self.assertNotIn("claudy", cli_fleet.WEBTERM_ONLY_USERS)


class TestClaudyNotifyRouting(unittest.TestCase):
    """Notify routing — claudy pings route to zbynek."""

    def test_claudy_routes_to_zbynek(self):
        from notify import STREAM_NOTIFY_OWNER
        self.assertEqual(STREAM_NOTIFY_OWNER.get("claudy"), "zbynek")


class TestClaudyWebterm(unittest.TestCase):
    """Webterm inventory and dashboard tabs."""

    def test_zbynek_inventory_has_claudy_entry(self):
        inv = profiles.zbynek_inventory()
        ids = [e["id"] for e in inv]
        self.assertIn("claudy", ids)

    def test_zbynek_claudy_entry_shape(self):
        inv = profiles.zbynek_inventory()
        entry = [e for e in inv if e["id"] == "claudy"][0]
        self.assertEqual(entry["user"], "claudy")
        self.assertFalse(entry["local"])
        self.assertEqual(entry["preferred"], "zbynek")
        self.assertEqual(entry["identity"], profiles.WEBTERM_ZBYNEK_IDENTITY)

    def test_marek_inventory_has_claudy_entry(self):
        inv = profiles.marek_inventory()
        ids = [e["id"] for e in inv]
        self.assertIn("claudy", ids)

    def test_marek_claudy_entry_shape(self):
        inv = profiles.marek_inventory()
        entry = [e for e in inv if e["id"] == "claudy"][0]
        self.assertEqual(entry["user"], "claudy")
        self.assertFalse(entry["local"])
        self.assertEqual(entry["preferred"], profiles.MAREK_GATEWAY_USER)
        self.assertEqual(entry["identity"], profiles.WEBTERM_MAREK_IDENTITY)

    def test_per_human_sessions_differ(self):
        """R1 Fable review: zbynek's and marek's claudy tabs target different
        tmux sessions, so they don't collapse into one pane."""
        zinv = profiles.zbynek_inventory()
        minv = profiles.marek_inventory()
        ze = [e for e in zinv if e["id"] == "claudy"][0]
        me = [e for e in minv if e["id"] == "claudy"][0]
        self.assertNotEqual(ze["preferred"], me["preferred"])

    def test_zbynek_tab_list_has_claudy(self):
        self.assertIn("claudy", w.WEBTERM_DASHBOARD_TABS["zbynek"])

    def test_marek_tab_list_has_claudy(self):
        self.assertIn("claudy", w.WEBTERM_DASHBOARD_TABS["marek"])

    def test_zbynek_claudy_tab_position(self):
        tabs = w.WEBTERM_DASHBOARD_TABS["zbynek"]
        self.assertEqual(tabs.index("claudy"), tabs.index("ar") + 1)

    def test_marek_claudy_tab_before_dev1(self):
        tabs = w.WEBTERM_DASHBOARD_TABS["marek"]
        self.assertLess(tabs.index("claudy"), tabs.index("dev1"))

    def test_claudy_alias(self):
        alias = cli_aliases.short_target_alias("claudy", "claudy")
        self.assertEqual(alias, "claudy")


class TestClaudyHostDriftLock(unittest.TestCase):
    """Y5 Fable review: host constant drift-lock."""

    def test_zbynek_claudy_host_matches_fleet_entry(self):
        fleet_host = None
        for h in cli_fleet.REMOTE_HOSTS:
            if h.get("name") == "claudy@controller":
                fleet_host = h["host"]
                break
        self.assertIsNotNone(fleet_host)
        self.assertEqual(profiles.ZBYNEK_CLAUDY_HOST, fleet_host)


class TestClaudyBootstrap(unittest.TestCase):
    """Root bootstrap renderer."""

    def test_render_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            bootstrap.render_root_bootstrap("nonexistent")

    def test_render_claudy_is_valid_bash(self):
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertIn("set -euo pipefail", script)
        self.assertIn("useradd", script)
        self.assertIn("loginctl enable-linger", script)
        # bash -n syntax check
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, "bash -n failed: %s" % r.stderr)

    def test_render_claudy_authorized_keys_has_push_keys(self):
        from cli_webterm_only import FLEET_PUSH_PUBKEYS
        script = bootstrap.render_root_bootstrap("claudy")
        for pubkey in FLEET_PUSH_PUBKEYS:
            self.assertIn(pubkey.split()[-1], script,
                          "push key comment not in bootstrap")

    def test_render_claudy_authorized_keys_has_forced_command_keys(self):
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertIn("restrict,pty,command=", script)
        # Both humans' keys present
        self.assertIn("webterm-zbynek-controller", script)
        self.assertIn("webterm-marek-controller", script)

    def test_desired_keys_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            bootstrap.desired_keys_for_service_account("nonexistent")

    def test_desired_keys_claudy_has_push_and_webterm_keys(self):
        keys = bootstrap.desired_keys_for_service_account("claudy")
        key_text = "\n".join(keys)
        self.assertIn("airuleset-push@airuleset", key_text)
        self.assertIn("restrict,pty,command=", key_text)

    def test_bootstrap_is_idempotent(self):
        """The script guards useradd with `id` check."""
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertIn("if id", script)

    def test_service_accounts_claudy_sessions(self):
        """The claudy service account has per-human sessions with chain."""
        spec = bootstrap.SERVICE_ACCOUNTS["claudy"]
        self.assertEqual(spec["webterm_sessions"]["zbynek"]["preferred"], "zbynek")
        self.assertEqual(spec["webterm_sessions"]["marek"]["preferred"], "marek")
        self.assertEqual(spec["webterm_sessions"]["zbynek"]["start_dir_chain"],
                         ["devel/claudy"])
        self.assertEqual(spec["webterm_sessions"]["marek"]["start_dir_chain"],
                         ["devel/claudy"])

    def test_service_accounts_preferred_matches_inventories(self):
        """Y2 drift-lock: SERVICE_ACCOUNTS preferred values must match the
        inventory entries' preferred fields."""
        spec = bootstrap.SERVICE_ACCOUNTS["claudy"]
        # zbynek inventory
        zinv = profiles.zbynek_inventory()
        ze = [e for e in zinv if e["id"] == "claudy"][0]
        self.assertEqual(ze["preferred"],
                         spec["webterm_sessions"]["zbynek"]["preferred"])
        # marek inventory
        minv = profiles.marek_inventory()
        me = [e for e in minv if e["id"] == "claudy"][0]
        self.assertEqual(me["preferred"],
                         spec["webterm_sessions"]["marek"]["preferred"])

    def test_forced_command_uses_start_dir_chain(self):
        """#960+#961: the rendered bootstrap's forced command must contain
        devel/claudy (the start_dir_chain), not the default STREAM_DEV_CWD_CHAIN."""
        script = bootstrap.render_root_bootstrap("claudy")
        self.assertIn("devel/claudy", script)
        # Must NOT contain the default odoo chain
        from cli_bashrc_appliers import STREAM_DEV_CWD_CHAIN
        for default_dir in STREAM_DEV_CWD_CHAIN:
            self.assertNotIn(default_dir, script,
                             "bootstrap should use devel/claudy, not %s" % default_dir)


class TestClaudyRegistry(unittest.TestCase):
    """projects-registry.json entry."""

    def test_claudy_in_registry(self):
        import json
        registry_path = Path(__file__).resolve().parent.parent / "projects-registry.json"
        with open(registry_path) as f:
            registry = json.load(f)
        claudy = [e for e in registry if e["name"] == "claudy"]
        self.assertEqual(len(claudy), 1)
        entry = claudy[0]
        self.assertEqual(entry["host"], "controller")
        self.assertEqual(entry["path"], "~/devel/claudy")
        self.assertEqual(entry["github_repo"], "zbynekdrlik/claudy")


if __name__ == "__main__":
    unittest.main()
