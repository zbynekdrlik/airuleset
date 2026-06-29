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
        self.assertIn("caveman-statusline.sh", c)  # still runs caveman's badge
        # Must NOT exec — the shim has to keep running to append the context meter.
        self.assertNotIn("exec bash", c)

    def test_shim_never_errors_when_caveman_absent(self):
        # Must exit 0 (print nothing it can't render) so a missing plugin or a
        # malformed payload can never break the prompt render.
        self.assertIn("exit 0", airuleset.CAVEMAN_SHIM_CONTENT)
        self.assertNotIn("set -e", airuleset.CAVEMAN_SHIM_CONTENT)

    def test_shim_renders_context_meter(self):
        c = airuleset.CAVEMAN_SHIM_CONTENT
        # Reads the session JSON from stdin and renders from context_window.
        self.assertIn("context_window", c)
        self.assertIn("used_percentage", c)

    def test_shim_renders_usage_limits(self):
        c = airuleset.CAVEMAN_SHIM_CONTENT
        # Also renders the 5h + weekly usage-limit meters from rate_limits.
        self.assertIn("rate_limits", c)
        self.assertIn("five_hour", c)
        self.assertIn("seven_day", c)


class TestCavemanShimBehavior(TestCase):
    """Run the shim as a real subprocess against a fake HOME + sample stdin."""

    import os
    import subprocess
    import tempfile
    import json

    def _run(self, payload, with_caveman=True):
        import os
        import subprocess
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as home:
            if with_caveman:
                cav = os.path.join(
                    home, ".claude/plugins/cache/caveman/caveman/abc123/hooks"
                )
                os.makedirs(cav)
                sl = os.path.join(cav, "caveman-statusline.sh")
                with open(sl, "w") as fh:
                    fh.write('#!/usr/bin/env bash\nprintf "[CAVEMAN:LITE]"\n')
                os.chmod(sl, 0o755)
            shim = os.path.join(home, "shim.sh")
            with open(shim, "w") as fh:
                fh.write(airuleset.CAVEMAN_SHIM_CONTENT)
            env = dict(os.environ)
            env["HOME"] = home
            r = subprocess.run(
                ["bash", shim],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env=env,
            )
            return r

    def test_badge_quiet_and_ctx_is_bar_only(self):
        r = self._run(
            {
                "context_window": {
                    "used_percentage": 18,
                    "context_window_size": 1000000,
                    "total_input_tokens": 177186,
                    "current_usage": {
                        "input_tokens": 2,
                        "cache_read_input_tokens": 175516,
                        "cache_creation_input_tokens": 1668,
                    },
                }
            }
        )
        self.assertEqual(r.returncode, 0)
        # caveman rendered quietly: lowercased, no loud [CAVEMAN] brackets.
        self.assertIn("caveman:lite", r.stdout)
        self.assertNotIn("[CAVEMAN:LITE]", r.stdout)
        # ctx is a bar only — no percentage, no token count.
        self.assertIn("ctx", r.stdout)
        self.assertTrue("█" in r.stdout or "░" in r.stdout)
        self.assertNotIn("177k", r.stdout)
        self.assertNotIn("18%", r.stdout)

    def test_ctx_bar_computed_when_used_percentage_missing(self):
        # used_percentage null -> compute from current_usage / size -> 50% bar.
        r = self._run(
            {
                "context_window": {
                    "used_percentage": None,
                    "context_window_size": 200000,
                    "current_usage": {
                        "input_tokens": 0,
                        "cache_read_input_tokens": 100000,
                        "cache_creation_input_tokens": 0,
                    },
                }
            }
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("█████░░░░░", r.stdout)  # 50% -> half-filled bar

    def test_no_meter_after_compact_still_shows_badge(self):
        # Right after /compact current_usage is null and there's no percentage:
        # render the (quiet) badge alone, no crash, no stray "%".
        r = self._run(
            {
                "context_window": {
                    "used_percentage": None,
                    "current_usage": None,
                    "context_window_size": 1000000,
                }
            }
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("caveman", r.stdout)
        self.assertNotIn("%", r.stdout)

    def test_ctx_bar_renders_even_without_caveman(self):
        # No caveman plugin on disk -> no tag, but the ctx bar still shows.
        r = self._run(
            {"context_window": {"used_percentage": 42, "context_window_size": 1000000,
                                "total_input_tokens": 420000}},
            with_caveman=False,
        )
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("caveman", r.stdout.lower())
        self.assertIn("ctx", r.stdout)
        self.assertTrue("█" in r.stdout or "░" in r.stdout)

    def test_malformed_payload_never_errors(self):
        r = self._run("not json at all")  # passed through json.dumps -> a string
        self.assertEqual(r.returncode, 0)

    def test_usage_limits_render_with_context(self):
        r = self._run(
            {
                "context_window": {
                    "used_percentage": 18,
                    "context_window_size": 1000000,
                    "total_input_tokens": 177186,
                },
                "rate_limits": {
                    "five_hour": {"used_percentage": 21, "resets_at": 0},
                    "seven_day": {"used_percentage": 97, "resets_at": 0},
                },
            }
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("ctx", r.stdout)
        self.assertIn("5h 21%", r.stdout)
        self.assertIn("wk 97%", r.stdout)

    def test_usage_limits_render_without_context(self):
        # No context_window block, but rate_limits present -> badge + limits.
        r = self._run(
            {
                "rate_limits": {
                    "five_hour": {"used_percentage": 5},
                    "seven_day": {"used_percentage": 60},
                }
            }
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("caveman:lite", r.stdout)
        self.assertIn("5h 5%", r.stdout)
        self.assertIn("wk 60%", r.stdout)


if __name__ == "__main__":
    main()
