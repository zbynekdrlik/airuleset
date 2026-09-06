"""#870 F4c step 1 — RED tests for the dominika LANE_HOST flip to controller.

Three properties locked:
  (a) controller's rendered lane set INCLUDES the dominika lane;
  (b) subdev NO LONGER renders/hosts the dominika lane after the flip;
  (c) pairwise consistency: for every human in LANE_HOST whose value is
      "controller", the hostname (WEBTERM_ACCESS_APPS) / gateway socket basename
      (the lane module's sock constant) / Access app binding are mutually
      consistent — catches a half-flipped entry.

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


class TestControllerIncludesDominikaLane(_BoxClassPinned):
    """(a) After the flip, controller's rendered lane set MUST include
    dominika — the tunnel ingress renders her hostname, the lane provision
    dispatches her gateway/ttyd/tunnel units."""

    def test_lane_host_dominika_is_controller(self):
        """LANE_HOST['dominika'] must be 'controller'."""
        self.assertEqual(
            p.LANE_HOST["dominika"], "controller",
            "LANE_HOST['dominika'] is %r — expected 'controller' (F4c flip)"
            % p.LANE_HOST["dominika"])

    def test_controller_lane_set_includes_dominika(self):
        """profile_for_host_set('controller') must include DOMINIKA."""
        lanes = p.profile_for_host_set("controller", "airuleset")
        self.assertIn(p.DOMINIKA, lanes,
                      "controller lane set missing dominika: %s" % lanes)


class TestSubdevNoLongerHostsDominika(_BoxClassPinned):
    """(b) After the flip, subdev must NOT render/host the dominika lane —
    the dominika entry's LANE_HOST value is no longer 'subdev', so
    profile_for_host_set('subdev', 'dominika') returns an empty set for
    dominika, and profile_for_host('subdev', 'dominika') returns None."""

    def test_subdev_lane_set_excludes_dominika(self):
        """profile_for_host_set('subdev', 'dominika') must NOT include
        DOMINIKA after the flip."""
        lanes = p.profile_for_host_set("subdev", p.DOMINIKA_GATEWAY_USER)
        self.assertNotIn(
            p.DOMINIKA, lanes,
            "subdev still hosts dominika lane after LANE_HOST flip: %s" % lanes)

    def test_profile_for_host_subdev_dominika_returns_not_dominika(self):
        """profile_for_host('subdev', 'dominika') must NOT return DOMINIKA
        after the flip — the function should fall through to a non-dominika
        result (e.g. DAVID default) for the dominika account on subdev."""
        result = p.profile_for_host("subdev", p.DOMINIKA_GATEWAY_USER)
        self.assertNotEqual(
            result, p.DOMINIKA,
            "profile_for_host('subdev', 'dominika') still returns DOMINIKA "
            "after LANE_HOST flip")


class TestPairwiseConsistency(_BoxClassPinned):
    """(c) Pairwise consistency lock: for every human in LANE_HOST, the
    hostname from WEBTERM_ACCESS_APPS, the gateway socket basename from the
    lane module, and the Access app binding must stay mutually consistent.
    This catches a half-flipped entry (e.g. LANE_HOST flipped but the socket
    basename still hardcodes the old host's path)."""

    def test_every_lane_host_human_has_access_app(self):
        """Every human in LANE_HOST must have a WEBTERM_ACCESS_APPS entry."""
        import cli_webterm_access as acc
        _HUMAN_TO_ACCESS = {
            "zbynek": "owner",
            "david": "david",
            "marek": "marek",
            "dominika": "dominika",
        }
        for human in p.LANE_HOST:
            access_key = _HUMAN_TO_ACCESS.get(human, human)
            self.assertIn(
                access_key, acc.WEBTERM_ACCESS_APPS,
                "LANE_HOST has %r but no WEBTERM_ACCESS_APPS entry" % human)

    def test_dominika_access_hostname_matches_lane_module(self):
        """The dominika lane module's tunnel hostname must match the
        WEBTERM_ACCESS_APPS hostname."""
        import cli_webterm_access as acc
        import cli_webterm_dominika as wdom
        self.assertEqual(
            wdom.WEBTERM_DOMINIKA_TUNNEL_HOSTNAME,
            acc.WEBTERM_ACCESS_APPS["dominika"]["hostname"],
            "dominika hostname mismatch between lane module and Access apps")

    def test_lane_host_values_are_valid(self):
        """Every LANE_HOST value must be one of the recognised box names."""
        valid = {"controller", "subdev", "dev1"}
        for human, host in p.LANE_HOST.items():
            self.assertIn(
                host, valid,
                "LANE_HOST[%r] = %r — not a recognised host" % (human, host))

    def test_dominika_gateway_sock_basename_consistent(self):
        """When dominika's LANE_HOST is 'controller', the gateway socket
        basename must still be valid and contain the human name."""
        import cli_webterm_dominika as wdom
        if p.LANE_HOST["dominika"] == "controller":
            self.assertIn(
                "dominika", wdom.WEBTERM_DOMINIKA_GATEWAY_SOCK_BASENAME,
                "socket basename doesn't contain 'dominika'")
            self.assertIn(
                "dominika", wdom.WEBTERM_DOMINIKA_TTYD_SOCK_BASENAME,
                "ttyd socket basename doesn't contain 'dominika'")

    def test_controller_lane_set_is_exactly_four(self):
        """The controller must host exactly the four humans in LANE_HOST."""
        lanes = p.profile_for_host_set("controller", "airuleset")
        self.assertEqual(
            len(lanes), 4,
            "controller lane set should be 4 (owner+david+marek+dominika), "
            "got %d: %s" % (len(lanes), lanes))


if __name__ == "__main__":
    unittest.main()
