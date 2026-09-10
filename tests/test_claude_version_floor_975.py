"""#975: Claude CLI version floor enforcement — RED→GREEN tests.

Tests the new cli_claude_version module and its integration points:
  * version parsing (numeric tuple comparison, not string)
  * autoUpdatesChannel stable→latest rewrite
  * ensure_claude_version_current with fake subprocess seams
  * floor guard: below-floor after update = failure
  * format_version_summary output
  * check_local_version_vs_floor for cmd_status
  * FLEET_CLAUDE_MIN_VERSION constant in cli_fleet
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the repo root is on sys.path for direct imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_claude_version as cv
import cli_fleet


class TestParseClaudeVersion(unittest.TestCase):
    """Version parsing: numeric tuple, never string comparison."""

    def test_simple_version(self):
        self.assertEqual(cv.parse_claude_version("2.1.267"), (2, 1, 267))

    def test_version_with_suffix(self):
        self.assertEqual(cv.parse_claude_version("1.0.29 (claude-code)"), (1, 0, 29))

    def test_multiline(self):
        self.assertEqual(cv.parse_claude_version("foo\n2.1.251\nbar"), (2, 1, 251))

    def test_empty(self):
        self.assertIsNone(cv.parse_claude_version(""))

    def test_none(self):
        self.assertIsNone(cv.parse_claude_version(None))

    def test_no_version(self):
        self.assertIsNone(cv.parse_claude_version("no version here"))

    def test_numeric_comparison_not_string(self):
        """Tuple (2, 1, 267) > (2, 1, 9) — string '267' < '9' would be wrong."""
        v1 = cv.parse_claude_version("2.1.267")
        v2 = cv.parse_claude_version("2.1.9")
        self.assertGreater(v1, v2)

    def test_floor_comparison(self):
        """2.1.236 < 2.1.251 (the incident)."""
        stale = cv.parse_claude_version("2.1.236")
        floor = cv.parse_claude_version("2.1.251")
        self.assertLess(stale, floor)


class TestVersionTupleToStr(unittest.TestCase):

    def test_format(self):
        self.assertEqual(cv.version_tuple_to_str((2, 1, 267)), "2.1.267")


class TestAutoUpdatesChannel(unittest.TestCase):
    """autoUpdatesChannel read + stable→latest rewrite."""

    def test_read_default_when_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cv.read_auto_updates_channel(home=d), "default")

    def test_read_stable(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".claude", "settings.json"), "w") as f:
                json.dump({"autoUpdatesChannel": "stable"}, f)
            self.assertEqual(cv.read_auto_updates_channel(home=d), "stable")

    def test_fix_stable_to_latest(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            p = os.path.join(d, ".claude", "settings.json")
            with open(p, "w") as f:
                json.dump({"autoUpdatesChannel": "stable", "other": 42}, f)
            changed, old = cv.fix_auto_updates_channel(home=d)
            self.assertTrue(changed)
            self.assertEqual(old, "stable")
            # Verify the file was rewritten.
            with open(p) as f:
                data = json.load(f)
            self.assertEqual(data["autoUpdatesChannel"], "latest")
            self.assertEqual(data["other"], 42)  # Preserves other keys.

    def test_no_fix_when_default(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".claude", "settings.json"), "w") as f:
                json.dump({"other": 1}, f)
            changed, old = cv.fix_auto_updates_channel(home=d)
            self.assertFalse(changed)
            self.assertEqual(old, "default")

    def test_no_fix_when_latest(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            with open(os.path.join(d, ".claude", "settings.json"), "w") as f:
                json.dump({"autoUpdatesChannel": "latest"}, f)
            changed, old = cv.fix_auto_updates_channel(home=d)
            self.assertFalse(changed)
            self.assertEqual(old, "latest")

    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            p = os.path.join(d, ".claude", "settings.json")
            with open(p, "w") as f:
                json.dump({"autoUpdatesChannel": "stable"}, f)
            changed, old = cv.fix_auto_updates_channel(home=d, dry_run=True)
            self.assertTrue(changed)
            # File should NOT be modified in dry_run.
            with open(p) as f:
                data = json.load(f)
            self.assertEqual(data["autoUpdatesChannel"], "stable")


class TestEnsureClaudeVersionCurrent(unittest.TestCase):
    """ensure_claude_version_current with patched get/update seams."""

    @patch("cli_claude_version.fix_auto_updates_channel", return_value=(False, "default"))
    @patch("cli_claude_version.run_claude_update", return_value=(True, "Updated"))
    @patch("cli_claude_version.get_local_claude_version")
    def test_stale_version_triggers_update(self, mock_ver, mock_upd, mock_fix):
        """A target below the floor triggers claude update + summary line."""
        mock_ver.side_effect = ["2.1.236", "2.1.267"]
        result = cv.ensure_claude_version_current(
            env={"PATH": "/usr/bin"}, home="/tmp/fake",
            min_version="2.1.251")
        self.assertEqual(result["old"], "2.1.236")
        self.assertEqual(result["new"], "2.1.267")
        self.assertTrue(result["updated"])
        self.assertFalse(result["below_floor"])
        mock_upd.assert_called_once()

    @patch("cli_claude_version.fix_auto_updates_channel", return_value=(False, "default"))
    @patch("cli_claude_version.run_claude_update")
    @patch("cli_claude_version.get_local_claude_version", return_value="2.1.267")
    def test_current_version_is_noop(self, mock_ver, mock_upd, mock_fix):
        """A target already at/above floor: no update, no error."""
        result = cv.ensure_claude_version_current(
            env={"PATH": "/usr/bin"}, home="/tmp/fake",
            min_version="2.1.251")
        self.assertEqual(result["old"], "2.1.267")
        self.assertEqual(result["new"], "2.1.267")
        self.assertFalse(result["updated"])
        self.assertFalse(result["below_floor"])
        mock_upd.assert_not_called()

    @patch("cli_claude_version.fix_auto_updates_channel", return_value=(True, "stable"))
    @patch("cli_claude_version.run_claude_update")
    @patch("cli_claude_version.get_local_claude_version", return_value="2.1.267")
    def test_stable_channel_fixed(self, mock_ver, mock_upd, mock_fix):
        """stable→latest channel rewrite is reported."""
        result = cv.ensure_claude_version_current(
            env={"PATH": "/usr/bin"}, home="/tmp/fake",
            min_version="2.1.251")
        self.assertTrue(result["channel_fixed"])

    @patch("cli_claude_version.fix_auto_updates_channel", return_value=(False, "default"))
    @patch("cli_claude_version.run_claude_update", return_value=(False, "no write permission"))
    @patch("cli_claude_version.get_local_claude_version")
    def test_below_floor_after_update_is_failure(self, mock_ver, mock_upd, mock_fix):
        """A target still below floor after update = deploy FAILURE.
        The error preserves the original update failure message."""
        mock_ver.side_effect = ["2.1.228", "2.1.228"]
        result = cv.ensure_claude_version_current(
            env={"PATH": "/usr/bin"}, home="/tmp/fake",
            min_version="2.1.251")
        self.assertTrue(result["below_floor"])
        self.assertIn("BELOW", result["error"])
        # #975 review LOW: original update error must be preserved.
        self.assertIn("no write permission", result["error"])

    @patch("cli_claude_version.fix_auto_updates_channel", return_value=(False, "default"))
    @patch("cli_claude_version.run_claude_update")
    @patch("cli_claude_version.get_local_claude_version", return_value="2.1.260")
    def test_update_failure_but_version_ok(self, mock_ver, mock_upd, mock_fix):
        """Update command fails but version is still above floor (edge case)."""
        result = cv.ensure_claude_version_current(
            env={"PATH": "/usr/bin"}, home="/tmp/fake",
            min_version="2.1.251")
        # 2.1.260 >= 2.1.251, no update needed.
        self.assertFalse(result["below_floor"])
        self.assertFalse(result["updated"])
        mock_upd.assert_not_called()


class TestFormatVersionSummary(unittest.TestCase):

    def test_updated(self):
        r = {"old": "2.1.236", "new": "2.1.267", "updated": True,
             "channel_fixed": False, "below_floor": False, "error": None}
        s = cv.format_version_summary(r)
        self.assertIn("2.1.236→2.1.267", s)
        self.assertIn("updated", s)

    def test_current(self):
        r = {"old": "2.1.267", "new": "2.1.267", "updated": False,
             "channel_fixed": False, "below_floor": False, "error": None}
        s = cv.format_version_summary(r)
        self.assertIn("2.1.267", s)
        self.assertIn("current", s)

    def test_below_floor(self):
        r = {"old": "2.1.228", "new": "2.1.228", "updated": False,
             "channel_fixed": False, "below_floor": True,
             "error": "BELOW the fleet floor"}
        s = cv.format_version_summary(r)
        self.assertIn("BELOW FLOOR", s)

    def test_channel_fixed(self):
        r = {"old": "2.1.236", "new": "2.1.267", "updated": True,
             "channel_fixed": True, "below_floor": False, "error": None}
        s = cv.format_version_summary(r)
        self.assertIn("channel fixed", s)


class TestCheckLocalVersionVsFloor(unittest.TestCase):
    """cmd_status integration: local version vs floor."""

    @patch("cli_claude_version.get_local_claude_version", return_value="2.1.267")
    def test_ok(self, _):
        ver_str, status = cv.check_local_version_vs_floor(min_version="2.1.251")
        self.assertEqual(ver_str, "2.1.267")
        self.assertEqual(status, "OK")

    @patch("cli_claude_version.get_local_claude_version", return_value="2.1.236")
    def test_below_floor(self, _):
        ver_str, status = cv.check_local_version_vs_floor(min_version="2.1.251")
        self.assertEqual(ver_str, "2.1.236")
        self.assertIn("BELOW FLOOR", status)

    @patch("cli_claude_version.get_local_claude_version", return_value=None)
    def test_not_found(self, _):
        ver_str, status = cv.check_local_version_vs_floor(min_version="2.1.251")
        self.assertEqual(status, "UNKNOWN")


class TestSubprocessSeams(unittest.TestCase):
    """Real-seam tests: stub `claude` script on a temp PATH exercises
    get_local_claude_version + run_claude_update through subprocess."""

    def _stub_env(self, script_body):
        """Create a temp dir with a stub `claude` script, return env."""
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        stub = os.path.join(d, "claude")
        with open(stub, "w") as f:
            f.write("#!/bin/bash\nset -euo pipefail\n" + script_body)
        os.chmod(stub, 0o755)
        return {"PATH": d + ":" + os.environ.get("PATH", "")}

    def test_get_version_success(self):
        env = self._stub_env('echo "2.1.267"\n')
        v = cv.get_local_claude_version(env=env)
        self.assertEqual(v, "2.1.267")

    def test_get_version_failure(self):
        env = self._stub_env('exit 1\n')
        v = cv.get_local_claude_version(env=env)
        self.assertIsNone(v)

    def test_update_success(self):
        env = self._stub_env('echo "Updated to 2.1.267"\n')
        ok, out = cv.run_claude_update(env=env)
        self.assertTrue(ok)
        self.assertIn("Updated", out)

    def test_update_failure(self):
        env = self._stub_env('echo "failed" >&2; exit 1\n')
        ok, out = cv.run_claude_update(env=env)
        self.assertFalse(ok)
        self.assertIn("failed", out)


class TestFleetFloorConstant(unittest.TestCase):
    """FLEET_CLAUDE_MIN_VERSION exists and is parseable."""

    def test_constant_exists(self):
        self.assertTrue(hasattr(cli_fleet, "FLEET_CLAUDE_MIN_VERSION"))

    def test_parseable(self):
        t = cv.parse_claude_version(cli_fleet.FLEET_CLAUDE_MIN_VERSION)
        self.assertIsNotNone(t)
        self.assertEqual(len(t), 3)

    def test_at_least_2_1_251(self):
        t = cv.parse_claude_version(cli_fleet.FLEET_CLAUDE_MIN_VERSION)
        self.assertGreaterEqual(t, (2, 1, 251))


class TestFacadeReExport(unittest.TestCase):
    """airuleset.py facade re-exports the new module's names."""

    def test_facade_imports(self):
        import airuleset
        for name in ("parse_claude_version", "ensure_claude_version_current",
                     "format_version_summary", "check_local_version_vs_floor",
                     "FLEET_CLAUDE_MIN_VERSION"):
            self.assertTrue(hasattr(airuleset, name),
                            f"airuleset.{name} not re-exported")


if __name__ == "__main__":
    unittest.main()
