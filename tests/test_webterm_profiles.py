"""Tests for per-developer webterm profiles (#612, cli_webterm_profiles.py +
cli_webterm.py profile integration).

The SECURITY-CRITICAL properties this pins:
  * a domain resolves to a scoped session set + auth realm (`owner` vs `david`);
  * david's connect allowlist is PHYSICALLY his 5-member set — his ttyd cannot
    resolve any owner-fleet id (dev1/gk/marek/montalu…) → refused, never execed
    (the negative test the ticket demands);
  * per-profile credential realm (david login != owner login);
  * the codex-bridge tab MIRRORS David's existing dev2 access exactly (owner
    ruling 2026-08-21) — same user/key/host, never broader;
  * the owner profile is byte-identical to the pre-#612 single-tenant inventory
    (regression: nothing the owner uses changes).
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as p  # noqa: E402


# Reuse the controlled fleet shape from test_webterm.py so the owner-regression
# assertion draws on the SAME fixture the single-tenant tests use.
_FAKE_HOSTS = [
    {"name": "dev2", "host": "10.0.0.2", "user": "newlevel"},
    {"name": "gatekeeper", "host": "10.0.0.9", "user": "gatekeeper",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "david@subdev", "host": "10.0.0.5", "user": "david",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "montalu@subdev", "host": "10.0.0.5", "user": "montalu"},
]
_FAKE_AUTHORITY = {"david": "fork-no-merge", "montalu": "branch-merge"}


def _fleet_inventory():
    import airuleset
    with m.patch.object(airuleset, "REMOTE_HOSTS", _FAKE_HOSTS), \
            m.patch.object(airuleset, "AUTHORITY_BY_USER", _FAKE_AUTHORITY):
        return w.webterm_inventory()  # default profile == owner


class TestProfileForHost(unittest.TestCase):
    def test_dev1_is_owner(self):
        self.assertEqual(p.profile_for_host("dev1"), p.OWNER)

    def test_subdev_no_longer_david(self):
        # #870 F4c-david: david moved to controller — the bare
        # profile_for_host("subdev") call now returns None.
        self.assertIsNone(p.profile_for_host("subdev"))

    def test_other_box_has_no_profile(self):
        self.assertIsNone(p.profile_for_host("dev2"))
        self.assertIsNone(p.profile_for_host("gatekeeper-cx23"))
        self.assertIsNone(p.profile_for_host(""))


class TestDavidInventory(unittest.TestCase):
    def test_exactly_five_members(self):
        inv = p.david_inventory()
        ids = [e["id"] for e in inv]
        self.assertEqual(ids, ["david1", "david2", "david3", "david4",
                               "codex-bridge"])

    def test_david_accounts_use_dedicated_identity(self):
        # #870 F4c-david: after the flip, david's subdev-targeted entries use
        # the subdev tailscale IP (controller reaches subdev over tailnet),
        # never loopback. The rest (identity, user, preferred) is unchanged.
        inv = {e["id"]: e for e in p.david_inventory()}
        expected_host = p._subdev_target_host("david")
        for u in ("david1", "david2", "david3", "david4"):
            e = inv[u]
            self.assertEqual(e["user"], u)
            self.assertEqual(e["host"], expected_host)
            self.assertEqual(e["identity"], p.WEBTERM_DAVID_IDENTITY)
            self.assertEqual(e["preferred"], u)
            self.assertFalse(e.get("local"))

    def test_codex_bridge_mirrors_existing_dev2_access(self):
        # Owner ruling 2026-08-21: mirror David's existing ssh exactly —
        # newlevel@dev2 via ~/.ssh/id_ed25519 (david1's own key), the existing
        # `david` tmux group. NEVER a dedicated account / new key (deferred).
        cb = next(e for e in p.david_inventory() if e["id"] == "codex-bridge")
        self.assertEqual(cb["user"], "newlevel")
        self.assertEqual(cb["host"], "100.82.64.27")   # dev2 tailscale IP
        self.assertEqual(cb["identity"], "~/.ssh/id_ed25519")
        self.assertEqual(cb["preferred"], "david")

    def test_dedicated_identity_is_not_the_fleet_gatekeeper_key(self):
        # The david identity must be a DEDICATED key scoped to david1-4, never
        # the fleet gatekeeper key (which reaches marek/montalu/simap/miva).
        self.assertNotIn("gatekeeper", p.WEBTERM_DAVID_IDENTITY)


class TestProfileInventoryScoping(unittest.TestCase):
    def test_owner_profile_is_the_full_fleet_unchanged(self):
        fleet = _fleet_inventory()
        self.assertEqual(p.profile_inventory(p.OWNER, fleet), list(fleet))

    def test_david_profile_is_the_david_set(self):
        fleet = _fleet_inventory()
        self.assertEqual(p.profile_inventory(p.DAVID, fleet), p.david_inventory())

    def test_david_allowed_ids_contain_no_owner_fleet_id(self):
        fleet = _fleet_inventory()
        david_ids = p.allowed_ids(p.DAVID, fleet)
        self.assertEqual(david_ids,
                         {"david1", "david2", "david3", "david4", "codex-bridge"})
        # The security invariant: NONE of the owner fleet ids leak in.
        for owner_id in ("dev1", "dev2", "gatekeeper", "montalu-subdev"):
            self.assertNotIn(owner_id, david_ids)


class TestWebtermInventoryProfileArg(unittest.TestCase):
    def test_default_is_owner_fleet(self):
        # Regression: no-arg call is byte-identical to the pre-#612 inventory.
        self.assertEqual(_fleet_inventory()[0]["id"], "dev1")

    def test_david_profile_returns_david_set(self):
        inv = w.webterm_inventory(profile=p.DAVID)
        self.assertEqual([e["id"] for e in inv],
                         ["david1", "david2", "david3", "david4", "codex-bridge"])


def _david_inv_file():
    d = tempfile.mkdtemp()
    f = Path(d) / "david-inv.json"
    f.write_text(json.dumps(p.david_inventory()), encoding="utf-8")
    return f


class TestConnectAllowlistIsProfileScoped(unittest.TestCase):
    """The heart of the security boundary: david's ttyd child reads david's
    inventory (via the WEBTERM_INVENTORY env var the launcher exports), so
    connect_main can only ever resolve david's ids."""

    def test_owner_ids_are_refused_against_david_inventory(self):
        f = _david_inv_file()
        for owner_id in ("dev1", "gatekeeper", "gk", "montalu-subdev", "marek",
                         "dev2"):
            with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                    m.patch.object(w.os, "execvp",
                                   side_effect=AssertionError("must not exec")) as ex:
                rc = w.connect_main([owner_id])
            self.assertEqual(rc, 2, "owner id %r must be refused" % owner_id)
            ex.assert_not_called()

    def test_david_id_execs_ssh_with_dedicated_identity(self):
        f = _david_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["david2"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-i", argv)
        self.assertIn(os.path.expanduser(p.WEBTERM_DAVID_IDENTITY), argv)
        # #870 F4c-david: after the flip, david's ssh target uses the subdev
        # tailscale IP (controller reaches subdev over tailnet).
        self.assertIn("david2@%s" % p._subdev_target_host("david"), argv)

    def test_codex_bridge_id_execs_mirror_of_existing_dev2_access(self):
        f = _david_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["codex-bridge"])
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn(os.path.expanduser("~/.ssh/id_ed25519"), argv)
        self.assertIn("newlevel@100.82.64.27", argv)


class TestInventorySelectionIsEnvNotClientArgv(unittest.TestCase):
    """#612 adversarial review: the scoped inventory is chosen by the launcher's
    WEBTERM_INVENTORY env var, NEVER by a client argv flag — ttyd's `-a` appends
    client `?arg=` values, so honoring an argv `--inventory` would be
    client-injectable (point the allowlist at an arbitrary JSON)."""

    def test_client_argv_inventory_flag_is_treated_as_sid_and_refused(self):
        # A client smuggling `?arg=--inventory&arg=/evil.json&arg=dev1` must NOT
        # be honored: `--inventory` becomes the session id and is refused.
        with m.patch.dict(os.environ, {}, clear=False), \
                m.patch.object(w.os, "execvp",
                               side_effect=AssertionError("must not exec")):
            os.environ.pop("WEBTERM_INVENTORY", None)
            rc = w.connect_main(["--inventory", "/evil.json", "dev1"])
        self.assertEqual(rc, 2)

    def test_env_var_selects_scoped_inventory(self):
        f = _david_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["david1"])
        ex.assert_called_once()

    def test_explicit_kwarg_overrides_env(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "inv.json"
        f.write_text(json.dumps([{"id": "dev1", "local": True,
                                  "preferred": "zbynek"}]), encoding="utf-8")
        # kwarg wins even if a (different) env var is present.
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": "/nonexistent.json"}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["dev1"], inventory_path=f)
        ex.assert_called_once()


class TestLauncherPassesInventory(unittest.TestCase):
    def test_launcher_without_inventory_is_unchanged(self):
        s = w.render_webterm_launch_script()
        self.assertIn("webterm-connect", s)
        self.assertNotIn("WEBTERM_INVENTORY", s)
        # No client-injectable argv flag on the connect command, ever.
        self.assertNotIn("--inventory", s)

    def test_launcher_with_inventory_exports_env_not_argv(self):
        s = w.render_webterm_launch_script(inventory_path="/x/david-inv.json")
        self.assertIn("export WEBTERM_INVENTORY=/x/david-inv.json", s)
        # The scoped path is NEVER passed as a connect argv flag (injectable).
        self.assertNotIn("webterm-connect --inventory", s)
        self.assertNotIn("--inventory", s)
        self.assertIn("-a", s)  # ttyd still appends the url-arg id
        # The export precedes the exec so the ttyd child inherits it.
        self.assertLess(s.index("export WEBTERM_INVENTORY"), s.index("exec ttyd"))


class TestAuthRealmSeparation(unittest.TestCase):
    def test_login_user_per_profile(self):
        self.assertEqual(w._login_user(p.OWNER), "zbynek")
        self.assertEqual(w._login_user(p.DAVID), "david")

    def test_cred_path_per_profile_are_distinct(self):
        self.assertNotEqual(w._cred_path(p.OWNER), w._cred_path(p.DAVID))

    def test_ensure_credential_writes_david_realm(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / ".secrets"
            with m.patch.object(w, "SECRETS_DIR", secrets), \
                    m.patch.object(w, "WEBTERM_DAVID_CRED_PATH",
                                   secrets / "webterm_david_credential"):
                cred = w._ensure_credential(profile=p.DAVID)
        user, _, pw = cred.partition(":")
        self.assertEqual(user, "david")
        self.assertTrue(pw)


class TestProfileForHostReturnSet870(unittest.TestCase):
    """#870 F4a D2: profile_for_host returns a lane SET; controller branch
    selected by box-class marker + pwd-based user."""

    def test_controller_returns_all_four_lanes(self):
        """On the controller box (box-class 'controller', user 'airuleset'),
        profile_for_host returns the full set of all human lanes."""
        result = p.profile_for_host_set("controller", "airuleset")
        self.assertIsInstance(result, (set, frozenset))
        self.assertEqual(result, {p.OWNER, p.DAVID, p.MAREK, p.DOMINIKA})

    def test_dev1_returns_owner_only(self):
        result = p.profile_for_host_set("dev1", "newlevel")
        self.assertEqual(result, {p.OWNER})

    def test_subdev_david_returns_empty_after_flip(self):
        # #870 F4c-david: david moved to controller — subdev no longer hosts
        # david, so profile_for_host_set("subdev", "david1") returns empty.
        result = p.profile_for_host_set("subdev", "david1")
        self.assertEqual(result, frozenset())

    def test_lane_host_table_exists(self):
        """LANE_HOST maps each human to the box that hosts their lane."""
        self.assertIsInstance(p.LANE_HOST, dict)
        self.assertIn("zbynek", p.LANE_HOST)
        self.assertIn("david", p.LANE_HOST)
        self.assertIn("marek", p.LANE_HOST)
        self.assertIn("dominika", p.LANE_HOST)


class TestZbynekInventory870(unittest.TestCase):
    """#870 F4a D4: zbynek_inventory() declarative leaf — no identity=None
    unless local=True; sshpass refusal on controller box-class."""

    def test_zbynek_inventory_exists(self):
        self.assertTrue(hasattr(p, "zbynek_inventory"))

    def test_zbynek_inventory_returns_entries(self):
        inv = p.zbynek_inventory()
        self.assertIsInstance(inv, list)
        self.assertTrue(len(inv) > 0)

    def test_no_identity_none_unless_local(self):
        """RED-2: a non-local entry with identity=None would trigger sshpass
        on the controller — fail2ban ban — push outage."""
        for e in p.zbynek_inventory():
            if not e.get("local"):
                self.assertIsNotNone(
                    e.get("identity"),
                    "non-local entry %r has identity=None — would trigger "
                    "sshpass on controller" % e["id"])

    def test_local_ar_tab_exists(self):
        """Y6: the controller's own tmux tab (local: True)."""
        inv = p.zbynek_inventory()
        local_entries = [e for e in inv if e.get("local")]
        self.assertTrue(len(local_entries) > 0, "no local entry for the ar tab")
        ar = local_entries[0]
        self.assertEqual(ar["id"], "ar")

    def test_zbynek_inventory_non_local_ids_match_dashboard_tabs(self):
        """Pairwise lock: zbynek_inventory()'s NON-LOCAL ids == the dashboard
        tabs (the local 'ar' tab is controller-only and added to the tabs by
        F4c when LANE_HOST["zbynek"] flips to "controller")."""
        inv_ids = {e["id"] for e in p.zbynek_inventory() if not e.get("local")}
        tab_ids = set(w.WEBTERM_DASHBOARD_TABS["zbynek"])
        self.assertEqual(inv_ids, tab_ids)


class TestLaneSpecFields870(unittest.TestCase):
    """#870 F4a D3: LaneSpec gains collector_mode + shared_tunnel fields."""

    def test_lanespec_has_collector_mode(self):
        import cli_webterm_lane as lane
        self.assertTrue(hasattr(lane.LaneSpec, "collector_mode"))

    def test_lanespec_has_shared_tunnel(self):
        import cli_webterm_lane as lane
        self.assertTrue(hasattr(lane.LaneSpec, "shared_tunnel"))


class TestSshpassRefusal870(unittest.TestCase):
    """#870 F4a D4: _ssh_interactive_prefix and _ssh_read_prefix REFUSE the
    sshpass branch outright when box-class == controller."""

    def test_interactive_prefix_refuses_sshpass_on_controller(self):
        """On the controller box, an entry without identity must NOT produce
        sshpass — it must raise or refuse."""
        entry = {"host": "10.0.0.1", "user": "test", "identity": None}
        with m.patch("watchdog.reaper.default_box_class",
                     return_value="controller"):
            with self.assertRaises((ValueError, SystemExit)):
                w._ssh_interactive_prefix(entry)


class TestCollectorIdentitySplit870(unittest.TestCase):
    """#870 F4a D8: per-entry collect_identity separate from tab identity.
    The U-dot reader must never use a webterm forced-command key."""

    def test_collect_identity_field_supported(self):
        """Entries can carry a collect_identity for the U reader."""
        entry = {
            "id": "test", "host": "10.0.0.1", "user": "test",
            "identity": "~/.secrets/webterm_zbynek_ed25519",
            "collect_identity": "~/.secrets/airuleset_push_ed25519",
        }
        # _ssh_read_prefix should use collect_identity when present
        with m.patch("cli_remote.host_key_check_opts", return_value=[]):
            prefix = w._ssh_read_prefix(entry)
        self.assertIn("airuleset_push_ed25519", " ".join(prefix))
        self.assertNotIn("webterm_zbynek", " ".join(prefix))


class TestAppendOnlyKeyWriter870(unittest.TestCase):
    """#870 F4a D6: append-only options-bearing key writer for non-webterm-only
    targets. Idempotent on blob, never desired-set rewrite."""

    def test_append_pubkey_command_exists(self):
        import cli_webterm_only as wo
        self.assertTrue(hasattr(wo, "append_controller_lane_pubkey_command"))

    def test_appends_options_bearing_key(self):
        import cli_webterm_only as wo
        key_line = (
            'restrict,pty,command="tmux new -A -s test" '
            'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey test-comment'
        )
        cmd = wo.append_controller_lane_pubkey_command(
            "testuser", key_line, ssh_dir="/tmp/test-ssh"
        )
        self.assertIsInstance(cmd, str)
        self.assertIn("AAAAC3NzaC1lZDI1NTE5AAAAITestKey", cmd)
        self.assertIn("restrict,pty", cmd)

    def test_idempotent_on_blob(self):
        """The command must check if the blob already exists before appending."""
        import cli_webterm_only as wo
        key_line = (
            'restrict,pty,command="tmux new -A -s test" '
            'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey test-comment'
        )
        cmd = wo.append_controller_lane_pubkey_command(
            "testuser", key_line, ssh_dir="/tmp/test-ssh"
        )
        # Must contain a grep/check for the blob before appending
        self.assertIn("grep", cmd.lower())


if __name__ == "__main__":
    unittest.main()
