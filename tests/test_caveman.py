"""Tests for airuleset's managed caveman-plugin wiring.

Covers the PURE logic that decides settings reconciliation + mode repair, plus
invariants of the stable statusline shim. The recurring real-world breakage was a
hard-coded plugin cache hash in settings.json that rots on `claude plugin update`;
these tests lock in the fix (statusLine -> a hash-independent shim) and the
idempotent enable/marketplace reconcile so a future edit can't reintroduce it.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class TestReconcileCavemanSettings(TestCase):
    def test_wires_into_empty_settings(self):
        out = airuleset.reconcile_caveman_settings({})
        self.assertEqual(out["statusLine"]["type"], "command")
        self.assertEqual(out["statusLine"]["command"], airuleset.CAVEMAN_STATUSLINE_COMMAND)
        self.assertTrue(out["enabledPlugins"]["caveman@caveman"])
        self.assertEqual(
            out["extraKnownMarketplaces"]["caveman"]["source"]["repo"],
            airuleset.CAVEMAN_MARKETPLACE_REPO,
        )

    def test_statusline_points_at_stable_shim_not_a_cache_hash(self):
        # The whole point: no content-hashed cache path in the managed statusLine.
        cmd = airuleset.reconcile_caveman_settings({})["statusLine"]["command"]
        self.assertIn("airuleset-caveman-statusline.sh", cmd)
        self.assertNotIn("/cache/caveman/", cmd)

    def test_repairs_stale_hardcoded_hash(self):
        stale = {
            "statusLine": {
                "type": "command",
                "command": 'bash "/home/x/.claude/plugins/cache/caveman/caveman/84cc3c14fa1e/hooks/caveman-statusline.sh"',
            }
        }
        out = airuleset.reconcile_caveman_settings(stale)
        self.assertNotIn("84cc3c14fa1e", out["statusLine"]["command"])
        self.assertEqual(out["statusLine"]["command"], airuleset.CAVEMAN_STATUSLINE_COMMAND)

    def test_preserves_other_keys_and_other_plugins(self):
        src = {
            "effortLevel": "xhigh",
            "hooks": {"Stop": [{"x": 1}]},
            "enabledPlugins": {"superpowers@claude-plugins-official": True},
        }
        out = airuleset.reconcile_caveman_settings(src)
        self.assertEqual(out["effortLevel"], "xhigh")
        self.assertEqual(out["hooks"], {"Stop": [{"x": 1}]})
        self.assertTrue(out["enabledPlugins"]["superpowers@claude-plugins-official"])
        self.assertTrue(out["enabledPlugins"]["caveman@caveman"])

    def test_idempotent(self):
        once = airuleset.reconcile_caveman_settings({})
        twice = airuleset.reconcile_caveman_settings(once)
        self.assertEqual(once, twice)

    def test_does_not_mutate_input(self):
        src = {"enabledPlugins": {"a@b": True}}
        airuleset.reconcile_caveman_settings(src)
        self.assertNotIn("caveman@caveman", src["enabledPlugins"])


class TestCavemanModeOrDefault(TestCase):
    def test_keeps_valid_mode(self):
        self.assertEqual(airuleset.caveman_mode_or_default("full"), "full")
        self.assertEqual(airuleset.caveman_mode_or_default("ultra"), "ultra")

    def test_strips_whitespace(self):
        self.assertEqual(airuleset.caveman_mode_or_default("  lite\n"), "lite")

    def test_none_falls_back_to_default(self):
        self.assertEqual(airuleset.caveman_mode_or_default(None), airuleset.CAVEMAN_DEFAULT_MODE)

    def test_garbage_falls_back_to_default(self):
        self.assertEqual(airuleset.caveman_mode_or_default("bogus"), airuleset.CAVEMAN_DEFAULT_MODE)
        self.assertEqual(airuleset.caveman_mode_or_default(""), airuleset.CAVEMAN_DEFAULT_MODE)

    def test_default_is_valid(self):
        self.assertIn(airuleset.CAVEMAN_DEFAULT_MODE, airuleset.VALID_CAVEMAN_MODES)


class TestCavemanShim(TestCase):
    def test_shim_resolves_hash_at_runtime(self):
        c = airuleset.CAVEMAN_SHIM_CONTENT
        self.assertIn("plugins/cache/caveman/caveman/", c)
        self.assertIn("ls -dt", c)  # newest cache hash wins
        self.assertIn("exec bash", c)

    def test_shim_never_errors_when_caveman_absent(self):
        # Must exit 0 (print nothing) so a missing plugin can't break the prompt.
        self.assertIn("exit 0", airuleset.CAVEMAN_SHIM_CONTENT)
        self.assertNotIn("set -e", airuleset.CAVEMAN_SHIM_CONTENT)


if __name__ == "__main__":
    main()
