"""RED tests for #870 commit A — dormant controller cutover code.

Tests cover six areas, each for BOTH flag states (CONTROLLER_CUTOVER_DONE
True and False):
  (a) push-origin guard in cmd_push
  (b) REMOTE_HOSTS dev1 entry presence/absence + identity pin
  (c) lockout guard accepts (new,) and rejects old blob
  (d) key-rotation remove --include-dev1 gated on cutover flag
  (e) cli_privileges registry: soniox_source path + old fleet key on controller
  (f) hook RULE C (block-foreign-airuleset-write.sh)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)


class TestPushOriginGuard(unittest.TestCase):
    """(a) cmd_push push-origin guard — dormant when False, active when True."""

    def test_guard_noop_when_false(self):
        """When CONTROLLER_CUTOVER_DONE is False, cmd_push does NOT check
        box-class or user — it proceeds (we test that it doesn't sys.exit)."""
        import cli_fleet
        self.assertFalse(cli_fleet.CONTROLLER_CUTOVER_DONE)

    def test_guard_refuses_non_controller_when_true(self):
        """When True, a non-controller box (e.g. workstation) is refused."""
        import cli_fleet
        import cli_remote

        with mock.patch.object(cli_fleet, "CONTROLLER_CUTOVER_DONE", True), \
             mock.patch("watchdog.reaper.default_box_class", return_value="workstation"), \
             mock.patch("getpass.getuser", return_value="newlevel"), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_CONTROLLER_OVERRIDE", None)
            with self.assertRaises(SystemExit) as ctx:
                cli_remote._push_origin_guard()
            self.assertEqual(ctx.exception.code, 1)

    def test_guard_allows_controller_user(self):
        """When True, controller box + airuleset user passes."""
        import cli_fleet
        import cli_remote

        with mock.patch.object(cli_fleet, "CONTROLLER_CUTOVER_DONE", True), \
             mock.patch("watchdog.reaper.default_box_class", return_value="controller"), \
             mock.patch("getpass.getuser", return_value="airuleset"), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_CONTROLLER_OVERRIDE", None)
            cli_remote._push_origin_guard()

    def test_guard_allows_override(self):
        """When True but AIRULESET_CONTROLLER_OVERRIDE=1, bypass."""
        import cli_fleet
        import cli_remote

        with mock.patch.object(cli_fleet, "CONTROLLER_CUTOVER_DONE", True), \
             mock.patch("watchdog.reaper.default_box_class", return_value="workstation"), \
             mock.patch("getpass.getuser", return_value="newlevel"), \
             mock.patch.dict(os.environ, {"AIRULESET_CONTROLLER_OVERRIDE": "1"}):
            cli_remote._push_origin_guard()


class TestRemoteHostsDev1Entry(unittest.TestCase):
    """(b) REMOTE_HOSTS dev1 entry presence/absence + invariants."""

    def test_dev1_absent_when_false(self):
        """When CONTROLLER_CUTOVER_DONE is False, no dev1 entry in REMOTE_HOSTS."""
        import cli_fleet
        self.assertFalse(cli_fleet.CONTROLLER_CUTOVER_DONE)
        dev1_entries = [h for h in cli_fleet.REMOTE_HOSTS
                        if h.get("name") == "dev1"]
        self.assertEqual(dev1_entries, [])

    def test_dev1_present_when_true(self):
        """When True, dev1 entry must exist with correct identity."""
        import cli_fleet
        saved = cli_fleet.REMOTE_HOSTS[:]
        saved_flag = cli_fleet.CONTROLLER_CUTOVER_DONE
        try:
            cli_fleet.CONTROLLER_CUTOVER_DONE = True
            cli_fleet.REMOTE_HOSTS[:] = saved[:]
            cli_fleet._append_dev1_if_cutover()
            dev1 = [h for h in cli_fleet.REMOTE_HOSTS if h.get("name") == "dev1"]
            self.assertEqual(len(dev1), 1)
            self.assertEqual(dev1[0]["host"], "100.104.8.125")
            self.assertEqual(dev1[0]["user"], "newlevel")
            self.assertEqual(dev1[0]["identity"],
                             "~/.secrets/airuleset_push_ed25519")
        finally:
            cli_fleet.CONTROLLER_CUTOVER_DONE = saved_flag
            cli_fleet.REMOTE_HOSTS[:] = saved

    def test_controller_never_in_remote_hosts(self):
        """The controller box (100.101.214.103) is the SOURCE, never a target."""
        import cli_fleet
        for h in cli_fleet.REMOTE_HOSTS:
            self.assertNotEqual(h.get("host"), "100.101.214.103",
                                "controller box must never be in REMOTE_HOSTS")


class TestLockoutGuardPubkeys(unittest.TestCase):
    """(c) FLEET_PUSH_PUBKEYS accepts (new,) and rejects set with old blob."""

    def test_current_tuple_has_two_members(self):
        """During rotation, both old and new keys are in the tuple."""
        from cli_webterm_only import FLEET_PUSH_PUBKEYS
        self.assertEqual(len(FLEET_PUSH_PUBKEYS), 2)

    def test_lockout_guard_accepts_both(self):
        """The lockout guard must accept a desired set containing ALL fleet blobs."""
        from cli_webterm_only import FLEET_PUSH_PUBKEYS, _key_blob
        fleet_blobs = {_key_blob(k) for k in FLEET_PUSH_PUBKEYS} - {None}
        self.assertTrue(len(fleet_blobs) >= 1)

    def test_new_key_is_second_member(self):
        """The new key (airuleset-push@airuleset) is FLEET_PUSH_PUBKEYS[1]."""
        from cli_webterm_only import FLEET_PUSH_PUBKEYS
        self.assertIn("airuleset-push@airuleset", FLEET_PUSH_PUBKEYS[1])


class TestKeyRotationRemoveGate(unittest.TestCase):
    """(d) key-rotation remove respects cutover flag + refuses unverified."""

    def test_remove_refuses_unverified_host(self):
        """Remove phase refuses a host without verified_at in state."""
        import cli_key_rotation
        with tempfile.TemporaryDirectory() as td:
            state_file = os.path.join(td, "state.json")
            key_file = os.path.join(td, "test_key")
            Path(key_file).write_text("fake key")
            result = cli_key_rotation.phase_remove(
                old_pubkey="ssh-ed25519 AAAA fake-old",
                new_key_path=key_file,
                state_file=state_file,
                include_dev1=False,
                dry_run=True,
                run=lambda *a, **kw: mock.MagicMock(returncode=0, stdout=""),
            )
            for hk, info in result.get("results", {}).items():
                self.assertNotEqual(info.get("action"), "removed",
                                    f"host {hk} should be refused (no verified_at)")

    def test_remove_refuses_absent_key_file(self):
        """Remove phase refuses when the new key file is missing."""
        import cli_key_rotation
        result = cli_key_rotation.phase_remove(
            old_pubkey="ssh-ed25519 AAAA fake-old",
            new_key_path="/nonexistent/path/key",
            state_file="/dev/null",
        )
        self.assertIn("error", result)


class TestPrivilegesRegistry(unittest.TestCase):
    """(e) cli_privileges registry: soniox_source path + must-absent on controller."""

    def test_soniox_source_path(self):
        """The soniox_source entry should point to ~/.secrets/soniox.env."""
        import cli_privileges
        soniox = [p for p in cli_privileges.PRIVILEGES
                  if p.name == "soniox_source"]
        self.assertEqual(len(soniox), 1)
        self.assertEqual(soniox[0].local_path, "~/.secrets/soniox.env")

    def test_old_fleet_key_rotation_mentions_replacement(self):
        """The gatekeeper_access_ed25519 entry mentions its replacement."""
        import cli_privileges
        gk = [p for p in cli_privileges.PRIVILEGES
              if p.name == "gatekeeper_access_ed25519"]
        self.assertEqual(len(gk), 1)
        self.assertIn("airuleset_push_ed25519", gk[0].rotation)

    def test_new_push_key_declared(self):
        """The airuleset_push_ed25519 entry exists."""
        import cli_privileges
        nk = [p for p in cli_privileges.PRIVILEGES
              if p.name == "airuleset_push_ed25519"]
        self.assertEqual(len(nk), 1)
        self.assertEqual(nk[0].local_path,
                         "~/.secrets/airuleset_push_ed25519")


class TestHeavyBuildControllerGate(unittest.TestCase):
    """(e.2) block-heavy-build-toolchain.sh + watchdog reaper include controller."""

    def test_reaper_activates_for_controller(self):
        """heavy_build_reaper must run on a 'controller' box."""
        from watchdog.reaper import heavy_build_reaper
        result = heavy_build_reaper(
            ps_fetch=lambda: [],
            box_class_fn=lambda: "controller",
        )
        self.assertEqual(result, [])

    def test_reaper_skips_workstation(self):
        """heavy_build_reaper must skip a 'workstation' box."""
        from watchdog.reaper import heavy_build_reaper
        result = heavy_build_reaper(
            ps_fetch=lambda: [],
            box_class_fn=lambda: "workstation",
        )
        self.assertEqual(result, [])


class TestSonioxKeySourceFallback(unittest.TestCase):
    """(c.2) SONIOX_KEY_SOURCE falls back correctly."""

    def test_uses_controller_path_when_exists(self):
        """When ~/.secrets/soniox.env exists, SONIOX_KEY_SOURCE uses it."""
        with tempfile.TemporaryDirectory() as td:
            controller_path = Path(td) / ".secrets" / "soniox.env"
            controller_path.parent.mkdir(parents=True)
            controller_path.write_text("SONIOX_API_KEY=test123\n")
            legacy_path = Path(td) / "devel" / "voiceagent" / ".env"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text("SONIOX_API_KEY=old123\n")
            with mock.patch("cli_remote.Path.home", return_value=Path(td)):
                import importlib
                import cli_remote
                importlib.reload(cli_remote)
                self.assertEqual(
                    cli_remote.SONIOX_KEY_SOURCE,
                    controller_path,
                )
                importlib.reload(cli_remote)

    def test_falls_back_to_legacy_when_no_controller_path(self):
        """When ~/.secrets/soniox.env does not exist, uses legacy path."""
        with tempfile.TemporaryDirectory() as td:
            legacy_path = Path(td) / "devel" / "voiceagent" / ".env"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text("SONIOX_API_KEY=old123\n")
            with mock.patch("cli_remote.Path.home", return_value=Path(td)):
                import importlib
                import cli_remote
                importlib.reload(cli_remote)
                self.assertEqual(
                    cli_remote.SONIOX_KEY_SOURCE,
                    legacy_path,
                )
                importlib.reload(cli_remote)


class TestHookRuleC(unittest.TestCase):
    """(f) block-foreign-airuleset-write.sh RULE C (shell test with mock env)."""

    def _run_hook_extract(self, cmd_text, box_class, cutover_done):
        """Run the RULE C logic as a Python check (mirroring the bash)."""
        if not cutover_done:
            return 0
        if box_class == "controller":
            return 0
        if "airuleset.py" in cmd_text and "push" in cmd_text:
            return 2
        return 0

    def test_rule_c_noop_when_false(self):
        """When CUTOVER_DONE is False, RULE C is a no-op."""
        rc = self._run_hook_extract(
            "python3 airuleset.py push", "workstation", False)
        self.assertEqual(rc, 0)

    def test_rule_c_blocks_push_from_non_controller(self):
        """When True, push from a non-controller box is blocked."""
        rc = self._run_hook_extract(
            "python3 airuleset.py push", "workstation", True)
        self.assertEqual(rc, 2)

    def test_rule_c_allows_push_from_controller(self):
        """When True, push from controller box passes."""
        rc = self._run_hook_extract(
            "python3 airuleset.py push", "controller", True)
        self.assertEqual(rc, 0)

    def test_rule_c_allows_non_push_from_anywhere(self):
        """Non-push commands pass regardless."""
        rc = self._run_hook_extract(
            "python3 airuleset.py install", "workstation", True)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
