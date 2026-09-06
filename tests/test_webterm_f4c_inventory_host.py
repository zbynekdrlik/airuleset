"""#870 F4c inventory host — controller-hosted inventory entries must NOT
ssh 127.0.0.1 (the controller itself) but the subdev tailscale IP.

RED properties:
  (a) When LANE_HOST[human] == "controller", no subdev-targeted inventory
      entry for that human carries host == SUBDEV_LOCAL ("127.0.0.1").
  (b) The SUBDEV_TAILSCALE_HOST constant matches cli_fleet REMOTE_HOSTS
      subdev entries (drift-lock).
  (c) When LANE_HOST[human] == "subdev", the subdev-targeted entries still
      carry SUBDEV_LOCAL (byte-identical regression lock).

Hermeticity: LANE_HOST is pinned via patch so the tests are env-leak-safe,
box-class pinned to "workstation" (Pass A).
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
    """Base that pins box-class to 'workstation' and HOME to an empty tmp dir."""

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


class TestControllerHostedInventoryNoLoopback(_BoxClassPinned):
    """(a) A controller-hosted lane's subdev-targeted inventory entries
    must use the subdev tailscale IP, never loopback."""

    def _subdev_entries(self, inventory):
        """Return entries whose id ends with '-subdev' (subdev targets)."""
        return [e for e in inventory
                if e["id"].endswith("-subdev") and not e.get("local")]

    def test_dominika_controller_no_loopback(self):
        """dominika is LIVE on controller — no entry should have 127.0.0.1."""
        self.assertEqual(p.LANE_HOST["dominika"], "controller")
        inv = p.dominika_inventory()
        subdev = self._subdev_entries(inv)
        self.assertTrue(len(subdev) > 0, "no subdev entries in dominika inv")
        for entry in subdev:
            self.assertNotEqual(
                entry["host"], p.SUBDEV_LOCAL,
                "dominika entry %r still uses loopback on controller"
                % entry["id"])

    def test_dominika_entries_use_tailscale_ip(self):
        """dominika's subdev entries must carry the subdev tailscale IP."""
        inv = p.dominika_inventory()
        subdev = self._subdev_entries(inv)
        for entry in subdev:
            self.assertEqual(
                entry["host"], p.SUBDEV_TAILSCALE_HOST,
                "dominika entry %r host is %r, expected %r"
                % (entry["id"], entry["host"], p.SUBDEV_TAILSCALE_HOST))

    def test_david_controller_no_loopback(self):
        """david's loopback entries must use tailscale IP when on controller."""
        with m.patch.dict(p.LANE_HOST, {"david": "controller"}):
            inv = p.david_inventory()
            subdev = self._subdev_entries(inv)
            self.assertTrue(len(subdev) > 0)
            for entry in subdev:
                self.assertNotEqual(
                    entry["host"], p.SUBDEV_LOCAL,
                    "david entry %r still uses loopback on controller"
                    % entry["id"])

    def test_marek_controller_no_loopback(self):
        """marek's loopback entries must use tailscale IP when on controller."""
        with m.patch.dict(p.LANE_HOST, {"marek": "controller"}):
            inv = p.marek_inventory()
            # marek has dev1/dev2/gk/forestshop (non-subdev) + subdev loopbacks
            loopback_entries = [e for e in inv
                                if e.get("host") == p.SUBDEV_LOCAL]
            self.assertEqual(
                len(loopback_entries), 0,
                "marek has %d loopback entries on controller: %s"
                % (len(loopback_entries),
                   [e["id"] for e in loopback_entries]))

    def test_zbynek_controller_no_loopback(self):
        """zbynek's subdev entries must use tailscale IP when on controller."""
        with m.patch.dict(p.LANE_HOST, {"zbynek": "controller"}):
            inv = p.zbynek_inventory()
            # local entry (ar) has host=None — filter it out
            subdev = [e for e in inv
                      if e.get("host") == p.SUBDEV_LOCAL]
            self.assertEqual(
                len(subdev), 0,
                "zbynek has %d loopback entries on controller: %s"
                % (len(subdev), [e["id"] for e in subdev]))


class TestSubdevHostedInventoryKeepsLoopback(_BoxClassPinned):
    """(c) When LANE_HOST says 'subdev', entries stay loopback (regression)."""

    def test_david_subdev_keeps_loopback(self):
        """david's entries use loopback when lane is on subdev."""
        self.assertEqual(p.LANE_HOST["david"], "subdev")
        inv = p.david_inventory()
        # david1-4 entries should all be SUBDEV_LOCAL
        for entry in inv:
            if entry["id"] in ("david1", "david2", "david3", "david4"):
                self.assertEqual(
                    entry["host"], p.SUBDEV_LOCAL,
                    "david entry %r lost loopback on subdev" % entry["id"])

    def test_marek_subdev_keeps_loopback(self):
        """marek's subdev-targeted entries use loopback on subdev."""
        self.assertEqual(p.LANE_HOST["marek"], "subdev")
        inv = p.marek_inventory()
        for entry in inv:
            if entry["id"].endswith("-subdev"):
                self.assertEqual(
                    entry["host"], p.SUBDEV_LOCAL,
                    "marek entry %r lost loopback on subdev" % entry["id"])


class TestSubdevTailscaleHostDriftLock(_BoxClassPinned):
    """(b) Drift-lock: SUBDEV_TAILSCALE_HOST matches cli_fleet's subdev IP."""

    def test_matches_fleet_subdev_ip(self):
        """The duplicated constant must match cli_fleet REMOTE_HOSTS."""
        import cli_fleet
        # Every subdev entry in REMOTE_HOSTS has the same host IP
        subdev_hosts = {
            h["host"] for h in cli_fleet.REMOTE_HOSTS
            if "@subdev" in h["name"]}
        self.assertTrue(len(subdev_hosts) > 0, "no subdev entries in fleet")
        self.assertEqual(
            len(subdev_hosts), 1,
            "subdev hosts are not uniform: %s" % subdev_hosts)
        fleet_ip = subdev_hosts.pop()
        self.assertEqual(
            p.SUBDEV_TAILSCALE_HOST, fleet_ip,
            "SUBDEV_TAILSCALE_HOST %r drifted from cli_fleet %r"
            % (p.SUBDEV_TAILSCALE_HOST, fleet_ip))


if __name__ == "__main__":
    unittest.main()
