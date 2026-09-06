"""#870 F4c-zbynek -- RED tests for the zbynek (owner) LANE_HOST flip to controller.

Properties locked (the marek/david flip test pattern, applied to zbynek):
  (a) LANE_HOST["zbynek"] must be "controller";
  (b) controller's rendered lane set INCLUDES the owner lane;
  (c) dev1 NO LONGER renders/hosts the owner lane after the flip;
  (d) zbynek's subdev-targeted inventory entries use the subdev tailscale IP
      (SUBDEV_TAILSCALE_HOST), never loopback (127.0.0.1);
  (e) profile_inventory(OWNER, ...) returns zbynek_inventory(), not
      fleet_inventory;
  (f) profile_for_host_set("dev1") is empty (zbynek was the only dev1 lane).

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


class TestZbynekLaneHostFlipped(_BoxClassPinned):
    """(a) LANE_HOST['zbynek'] must be 'controller' after the F4c flip."""

    def test_lane_host_zbynek_is_controller(self):
        self.assertEqual(
            p.LANE_HOST["zbynek"], "controller",
            "LANE_HOST['zbynek'] is %r -- expected 'controller' (F4c-zbynek flip)"
            % p.LANE_HOST["zbynek"])


class TestControllerIncludesOwnerLane(_BoxClassPinned):
    """(b) After the flip, controller's lane set MUST include the owner."""

    def test_controller_lane_set_includes_owner(self):
        lanes = p.profile_for_host_set("controller", "airuleset")
        self.assertIn(p.OWNER, lanes,
                      "controller lane set missing owner: %s" % lanes)


class TestDev1NoLongerHostsOwner(_BoxClassPinned):
    """(c) After the flip, dev1 must NOT render/host the owner lane."""

    def test_dev1_lane_set_excludes_owner(self):
        lanes = p.profile_for_host_set("dev1")
        self.assertNotIn(
            p.OWNER, lanes,
            "dev1 still hosts owner lane after LANE_HOST flip: %s" % lanes)

    def test_profile_for_host_dev1_returns_none(self):
        """profile_for_host('dev1') must return None after the flip."""
        result = p.profile_for_host("dev1")
        self.assertIsNone(
            result,
            "profile_for_host('dev1') still returns %r "
            "after LANE_HOST flip -- expected None" % result)

    def test_dev1_lane_set_is_empty(self):
        """(f) dev1 had ONLY the zbynek lane; after the flip it is empty."""
        lanes = p.profile_for_host_set("dev1")
        self.assertEqual(
            len(lanes), 0,
            "dev1 still has %d lane(s) after zbynek flip: %s" % (len(lanes), lanes))


class TestZbynekInventoryUseTailscaleIP(_BoxClassPinned):
    """(d) After the flip, zbynek's subdev-targeted inventory entries must use
    the subdev tailscale IP (100.118.174.27), never loopback (127.0.0.1)."""

    def test_no_loopback_subdev_entries(self):
        inv = p.zbynek_inventory()
        # filter out the local "ar" entry (host=None)
        subdev = [e for e in inv
                  if e.get("host") == p.SUBDEV_LOCAL]
        self.assertEqual(
            len(subdev), 0,
            "zbynek has %d loopback entries after flip: %s"
            % (len(subdev), [e["id"] for e in subdev]))

    def test_subdev_entries_use_tailscale_ip(self):
        inv = p.zbynek_inventory()
        subdev_entries = [e for e in inv if e["id"].endswith("-subdev")]
        self.assertTrue(len(subdev_entries) > 0,
                        "no subdev entries in zbynek inventory")
        for entry in subdev_entries:
            self.assertEqual(
                entry["host"], p.SUBDEV_TAILSCALE_HOST,
                "zbynek entry %r host is %r, expected %r"
                % (entry["id"], entry["host"], p.SUBDEV_TAILSCALE_HOST))


class TestProfileInventoryRoutesOwner(_BoxClassPinned):
    """(e) profile_inventory(OWNER, ...) must return zbynek_inventory(),
    not the fleet_inventory fallthrough."""

    def test_owner_gets_zbynek_inventory(self):
        sentinel = [{"id": "fleet-sentinel"}]
        result = p.profile_inventory(p.OWNER, sentinel)
        # Must NOT be the sentinel (fleet_inventory fallthrough)
        self.assertNotEqual(
            result, sentinel,
            "profile_inventory(OWNER, ...) still returns fleet_inventory "
            "instead of zbynek_inventory()")

    def test_owner_inventory_matches_zbynek_inventory(self):
        result = p.profile_inventory(p.OWNER, [])
        expected = p.zbynek_inventory()
        self.assertEqual(
            result, expected,
            "profile_inventory(OWNER, ...) does not match zbynek_inventory()")


class TestZbynekInventoryExplicitIdentity(_BoxClassPinned):
    """Every non-local zbynek_inventory entry must carry an explicit identity
    (WEBTERM_ZBYNEK_IDENTITY) -- the RED-2 invariant (sshpass on the controller
    would trigger fail2ban -> push outage)."""

    def test_all_non_local_entries_have_identity(self):
        inv = p.zbynek_inventory()
        for entry in inv:
            if entry.get("local"):
                continue
            self.assertIsNotNone(
                entry.get("identity"),
                "zbynek entry %r has no explicit identity -- "
                "would take the sshpass branch on the controller (RED-2)"
                % entry["id"])
            self.assertEqual(
                entry["identity"], p.WEBTERM_ZBYNEK_IDENTITY,
                "zbynek entry %r identity is %r, expected WEBTERM_ZBYNEK_IDENTITY"
                % (entry["id"], entry["identity"]))


if __name__ == "__main__":
    unittest.main()
