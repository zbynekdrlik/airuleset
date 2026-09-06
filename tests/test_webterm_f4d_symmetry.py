"""#870 F4d — post-F4c completeness invariant: all lanes on controller,
subdev/dev1 webterm install is a no-op, dev1 is a REMOTE_HOSTS target.

These tests lock the FINAL state of the webterm controller migration.
Individual flip tests (test_webterm_f4c_*_flip.py) lock each step;
this file locks the end state as a whole.

Hermeticity: box-class pinned to "workstation" (Pass B runs on real boxes),
HOME pinned to an empty tmp dir (controller has minted keys that would flip
prereq gates).
"""
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_fleet  # noqa: E402
import cli_webterm_profiles as p  # noqa: E402


class _BoxClassPinned(unittest.TestCase):
    """Base that pins box-class to 'workstation' and HOME to an empty tmp dir,
    so the tests are hermetic on real boxes (#870 F4a/F4b pattern)."""

    def setUp(self):
        super().setUp()
        self._orig_home = os.environ.get("HOME")
        self._tmpdir = tempfile.mkdtemp()
        os.environ["HOME"] = self._tmpdir
        self._bc_patcher = m.patch(
            "watchdog.reaper.default_box_class", return_value="workstation")
        self._bc_patcher.start()
        self.addCleanup(self._bc_patcher.stop)
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        else:
            os.environ.pop("HOME", None)


# ── ALL LANE_HOST values are "controller" ──────────────────────────────

class TestAllLanesOnController(_BoxClassPinned):
    """After F4c, every human's lane runs on the controller."""

    def test_all_lane_host_values_are_controller(self):
        """Every LANE_HOST value must be 'controller' — the post-F4c
        invariant. A future lane re-host changes this; until then it locks."""
        for human, host in p.LANE_HOST.items():
            self.assertEqual(
                host, "controller",
                "LANE_HOST[%r] = %r, expected 'controller'" % (human, host))

    def test_lane_host_has_exactly_four_humans(self):
        """The expected set: zbynek, david, marek, dominika."""
        self.assertEqual(
            set(p.LANE_HOST.keys()),
            {"zbynek", "david", "marek", "dominika"})


# ── subdev and dev1 webterm install is a no-op ─────────────────────────

class TestSubdevIsWebetermNoOp(_BoxClassPinned):
    """When all LANE_HOST values are 'controller', a push to subdev must
    NOT install any webterm lane."""

    def test_profile_for_host_subdev_david(self):
        self.assertIsNone(p.profile_for_host("subdev", "david1"))

    def test_profile_for_host_subdev_marek(self):
        self.assertIsNone(
            p.profile_for_host("subdev", p.MAREK_GATEWAY_USER))

    def test_profile_for_host_subdev_dominika(self):
        self.assertIsNone(
            p.profile_for_host("subdev", p.DOMINIKA_GATEWAY_USER))

    def test_profile_for_host_subdev_default(self):
        """Default account (non-marek, non-dominika) on subdev."""
        self.assertIsNone(p.profile_for_host("subdev"))


class TestDev1IsWebetermNoOp(_BoxClassPinned):
    """When LANE_HOST['zbynek'] != 'dev1', dev1 must not install the
    owner webterm lane."""

    def test_profile_for_host_dev1_returns_none(self):
        self.assertIsNone(p.profile_for_host("dev1"))


class TestControllerGetsAllLanes(_BoxClassPinned):
    """The controller provisions ALL four lanes."""

    def test_profile_for_host_set_controller(self):
        result = p.profile_for_host_set("controller")
        expected = frozenset({p.OWNER, p.DAVID, p.MAREK, p.DOMINIKA})
        self.assertEqual(result, expected)


# ── dev1 is in REMOTE_HOSTS as a target ────────────────────────────────

class TestDev1IsRemoteTarget(unittest.TestCase):
    """After CONTROLLER_CUTOVER_DONE, dev1 appears in REMOTE_HOSTS
    as a deploy target with the new fleet push key."""

    def test_cutover_is_done(self):
        self.assertTrue(cli_fleet.CONTROLLER_CUTOVER_DONE)

    def test_dev1_in_remote_hosts(self):
        names = [h["name"] for h in cli_fleet.REMOTE_HOSTS]
        self.assertIn("dev1", names)

    def test_dev1_uses_new_fleet_key(self):
        """Dev1's identity must be the new airuleset_push key, not the
        old gatekeeper_access key."""
        dev1 = next(h for h in cli_fleet.REMOTE_HOSTS
                    if h["name"] == "dev1")
        self.assertIn("airuleset_push", dev1.get("identity", ""))
        self.assertNotIn("gatekeeper_access", dev1.get("identity", ""))

    def test_dev1_host_is_tailscale_ip(self):
        dev1 = next(h for h in cli_fleet.REMOTE_HOSTS
                    if h["name"] == "dev1")
        self.assertEqual(dev1["host"], "100.104.8.125")


# ── machine-identities.md reflects the controller ─────────────────────

class TestMachineIdentitiesDoc(unittest.TestCase):
    """The always-on machine-identities module must name the controller
    as the airuleset SoT and dev1 as a target."""

    _PATH = Path(__file__).resolve().parent.parent / (
        "modules/core/machine-identities.md")

    def test_controller_named_as_sot(self):
        text = self._PATH.read_text()
        self.assertIn("100.101.214.103", text)
        self.assertIn("airuleset SoT", text)

    def test_push_runs_on_controller(self):
        text = self._PATH.read_text()
        self.assertIn("runs on the controller", text)

    def test_dev1_is_deploy_target(self):
        text = self._PATH.read_text()
        # The dev1 row must say "Deploy target", not "source of truth"
        self.assertIn("Deploy target", text)
        self.assertNotIn("source of truth", text)


if __name__ == "__main__":
    unittest.main()
