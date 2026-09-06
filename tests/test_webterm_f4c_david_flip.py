"""#870 F4c-david — RED tests for the david LANE_HOST flip to controller.

Properties locked (the marek/dominika flip test pattern, applied to david):
  (a) LANE_HOST["david"] must be "controller";
  (b) controller's rendered lane set INCLUDES the david lane;
  (c) subdev NO LONGER renders/hosts the david lane after the flip;
  (d) david's subdev-targeted inventory entries use the subdev tailscale IP
      (SUBDEV_TAILSCALE_HOST), never loopback (127.0.0.1);
  (e) profile_for_host("subdev", david1) returns None after the flip;
  (f) profile_for_host("subdev") returns None (david was the default).

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


class TestDavidLaneHostFlipped(_BoxClassPinned):
    """(a) LANE_HOST['david'] must be 'controller' after the F4c flip."""

    def test_lane_host_david_is_controller(self):
        self.assertEqual(
            p.LANE_HOST["david"], "controller",
            "LANE_HOST['david'] is %r — expected 'controller' (F4c-david flip)"
            % p.LANE_HOST["david"])


class TestControllerIncludesDavidLane(_BoxClassPinned):
    """(b) After the flip, controller's lane set MUST include david."""

    def test_controller_lane_set_includes_david(self):
        lanes = p.profile_for_host_set("controller", "airuleset")
        self.assertIn(p.DAVID, lanes,
                      "controller lane set missing david: %s" % lanes)


class TestSubdevNoLongerHostsDavid(_BoxClassPinned):
    """(c) After the flip, subdev must NOT render/host the david lane."""

    def test_subdev_lane_set_excludes_david(self):
        lanes = p.profile_for_host_set("subdev", p.DAVID_GATEWAY_USER)
        self.assertNotIn(
            p.DAVID, lanes,
            "subdev still hosts david lane after LANE_HOST flip: %s" % lanes)

    def test_profile_for_host_subdev_david1_returns_none(self):
        """(e) profile_for_host('subdev', 'david1') must return None."""
        result = p.profile_for_host("subdev", p.DAVID_GATEWAY_USER)
        self.assertIsNone(
            result,
            "profile_for_host('subdev', 'david1') still returns %r "
            "after LANE_HOST flip — expected None" % result)

    def test_profile_for_host_subdev_default_returns_none(self):
        """(f) profile_for_host('subdev') (no account) returns None — david was
        the default; with david off subdev, the bare call returns None."""
        result = p.profile_for_host("subdev")
        self.assertIsNone(
            result,
            "profile_for_host('subdev') still returns %r "
            "after LANE_HOST flip — expected None" % result)


class TestDavidInventoryUseTailscaleIP(_BoxClassPinned):
    """(d) After the flip, david's subdev-targeted inventory entries must use
    the subdev tailscale IP (100.118.174.27), never loopback (127.0.0.1)."""

    def test_no_loopback_entries_for_subdev_targets(self):
        inv = p.david_inventory()
        # codex-bridge uses CODEX_HOST (dev2 tailscale IP), not subdev — skip
        subdev_entries = [e for e in inv if e["id"] in p.DAVID_ACCOUNTS]
        loopback = [e for e in subdev_entries if e.get("host") == p.SUBDEV_LOCAL]
        self.assertEqual(
            len(loopback), 0,
            "david has %d loopback entries after flip: %s"
            % (len(loopback), [e["id"] for e in loopback]))

    def test_subdev_entries_use_tailscale_ip(self):
        inv = p.david_inventory()
        subdev_entries = [e for e in inv if e["id"] in p.DAVID_ACCOUNTS]
        self.assertTrue(len(subdev_entries) > 0,
                        "no subdev entries in david inventory")
        for entry in subdev_entries:
            self.assertEqual(
                entry["host"], p.SUBDEV_TAILSCALE_HOST,
                "david entry %r host is %r, expected %r"
                % (entry["id"], entry["host"], p.SUBDEV_TAILSCALE_HOST))


if __name__ == "__main__":
    unittest.main()
