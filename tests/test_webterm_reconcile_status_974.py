"""Tests for #974 fix-forward — cmd_status lane-profile enumeration (MEDIUM-4).

The controller hosts 4 lane profiles (zbynek, david, marek, dominika) x 2 unit
types (ttyd, gateway) = 8 webterm units.  ``enumerate_status_units()`` must
return all 8 rendered-paths entries on a controller box (where
``is_webterm_gateway()`` is False), so ``cmd_status`` can print them.

Hermetic under ``HOME=$(mktemp -d)``; never touches real systemd.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestEnumerateStatusUnits(unittest.TestCase):
    """enumerate_status_units: returns rendered_paths for all webterm units on the box."""

    def setUp(self):
        self._home = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"HOME": self._home})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._home, ignore_errors=True)

    @patch("cli_webterm.is_webterm_gateway", return_value=False)
    def test_controller_enumerates_all_lane_units(self, _mock_gw):
        """On a controller box with is_webterm_gateway() False and all 4 lanes
        hosted, enumerate_status_units returns 8 entries (4 profiles x 2 units)."""
        import cli_webterm_reconcile as rec

        # Fake the box-class detection: controller
        with patch("cli_webterm_reconcile._box_class", return_value="controller"):
            rendered = rec.enumerate_status_units()

        # All 4 profiles: zbynek, david, marek, dominika — each with ttyd + gateway
        expected_profiles = {"zbynek", "david", "marek", "dominika"}
        found_profiles = set()
        for unit_name in rendered:
            for profile in expected_profiles:
                if profile in unit_name:
                    found_profiles.add(profile)
                    break

        self.assertEqual(found_profiles, expected_profiles,
                         "Must enumerate all 4 profiles")
        self.assertEqual(len(rendered), 8,
                         "Must have 8 entries (4 profiles x 2 unit types)")

        # Each ttyd entry should point to a launch script (.sh)
        for unit_name, path in rendered.items():
            if "ttyd" in unit_name:
                self.assertTrue(path.endswith(".sh"),
                                "ttyd rendered path should be a .sh launcher: %s" % path)
            if "gateway" in unit_name:
                self.assertTrue(path.endswith(".py"),
                                "gateway rendered path should be a .py module: %s" % path)

    @patch("cli_webterm.is_webterm_gateway", return_value=True)
    def test_owner_box_includes_owner_pair(self, _mock_gw):
        """On dev1 (is_webterm_gateway True), the owner pair is included."""
        import cli_webterm_reconcile as rec

        # No lanes hosted on dev1 (all LANE_HOST -> "controller")
        with patch("cli_webterm_reconcile._box_class", return_value="dev1"):
            rendered = rec.enumerate_status_units()

        self.assertIn("webterm-ttyd.service", rendered)
        self.assertIn("webterm-gateway.service", rendered)

    @patch("cli_webterm.is_webterm_gateway", return_value=False)
    def test_other_box_returns_empty(self, _mock_gw):
        """On a box that is neither controller nor has is_webterm_gateway, returns empty."""
        import cli_webterm_reconcile as rec

        with patch("cli_webterm_reconcile._box_class", return_value="workstation"):
            rendered = rec.enumerate_status_units()

        self.assertEqual(rendered, {})

    @patch("cli_webterm.is_webterm_gateway", return_value=False)
    def test_controller_unit_names_match_spec(self, _mock_gw):
        """Unit names from enumerate_status_units match the _spec() derivation."""
        import cli_webterm_reconcile as rec

        with patch("cli_webterm_reconcile._box_class", return_value="controller"):
            rendered = rec.enumerate_status_units()

        # Check exact unit names from the _spec() derivation
        expected_units = set()
        for profile in ["zbynek", "david", "marek", "dominika"]:
            expected_units.add("webterm-%s-ttyd.service" % profile)
            expected_units.add("webterm-%s-gateway.service" % profile)

        self.assertEqual(set(rendered.keys()), expected_units)


if __name__ == "__main__":
    unittest.main()
