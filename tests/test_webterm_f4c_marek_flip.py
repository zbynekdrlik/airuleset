"""#870 F4c-marek — RED tests for the marek LANE_HOST flip to controller.

Properties locked (the dominika flip test pattern, applied to marek):
  (a) LANE_HOST["marek"] must be "controller";
  (b) controller's rendered lane set INCLUDES the marek lane;
  (c) subdev NO LONGER renders/hosts the marek lane after the flip;
  (d) marek's subdev-targeted inventory entries use the subdev tailscale IP
      (SUBDEV_TAILSCALE_HOST), never loopback (127.0.0.1);
  (e) profile_for_host("subdev", "marek") returns None after the flip.

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


class TestMarekLaneHostFlipped(_BoxClassPinned):
    """(a) LANE_HOST['marek'] must be 'controller' after the F4c flip."""

    def test_lane_host_marek_is_controller(self):
        self.assertEqual(
            p.LANE_HOST["marek"], "controller",
            "LANE_HOST['marek'] is %r — expected 'controller' (F4c-marek flip)"
            % p.LANE_HOST["marek"])


class TestControllerIncludesMarekLane(_BoxClassPinned):
    """(b) After the flip, controller's lane set MUST include marek."""

    def test_controller_lane_set_includes_marek(self):
        lanes = p.profile_for_host_set("controller", "airuleset")
        self.assertIn(p.MAREK, lanes,
                      "controller lane set missing marek: %s" % lanes)


class TestSubdevNoLongerHostsMarek(_BoxClassPinned):
    """(c) After the flip, subdev must NOT render/host the marek lane."""

    def test_subdev_lane_set_excludes_marek(self):
        lanes = p.profile_for_host_set("subdev", p.MAREK_GATEWAY_USER)
        self.assertNotIn(
            p.MAREK, lanes,
            "subdev still hosts marek lane after LANE_HOST flip: %s" % lanes)

    def test_profile_for_host_subdev_marek_returns_none(self):
        """(e) profile_for_host('subdev', 'marek') must return None."""
        result = p.profile_for_host("subdev", p.MAREK_GATEWAY_USER)
        self.assertIsNone(
            result,
            "profile_for_host('subdev', 'marek') still returns %r "
            "after LANE_HOST flip — expected None" % result)


class TestMarekInventoryUseTailscaleIP(_BoxClassPinned):
    """(d) After the flip, marek's subdev-targeted inventory entries must use
    the subdev tailscale IP (100.118.174.27), never loopback (127.0.0.1)."""

    def test_no_loopback_entries(self):
        inv = p.marek_inventory()
        loopback = [e for e in inv if e.get("host") == p.SUBDEV_LOCAL]
        self.assertEqual(
            len(loopback), 0,
            "marek has %d loopback entries after flip: %s"
            % (len(loopback), [e["id"] for e in loopback]))

    def test_subdev_entries_use_tailscale_ip(self):
        inv = p.marek_inventory()
        subdev_entries = [e for e in inv if e["id"].endswith("-subdev")]
        self.assertTrue(len(subdev_entries) > 0,
                        "no subdev entries in marek inventory")
        for entry in subdev_entries:
            self.assertEqual(
                entry["host"], p.SUBDEV_TAILSCALE_HOST,
                "marek entry %r host is %r, expected %r"
                % (entry["id"], entry["host"], p.SUBDEV_TAILSCALE_HOST))


if __name__ == "__main__":
    unittest.main()
