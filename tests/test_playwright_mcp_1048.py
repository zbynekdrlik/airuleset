"""#1048 — managed Playwright MCP: chromium channel + matched browsers path.

Owner escalation (montalu1, 16.9.): the Playwright MCP is DEAD on every no-sudo
stream box because the managed config force-enables the `playwright@claude-
plugins-official` plugin, whose own `.mcp.json` (`npx @playwright/mcp@latest`,
no `--browser`) defaults to the `chrome` channel (Google Chrome, root-only), and
the managed env points `PLAYWRIGHT_BROWSERS_PATH` at the root-owned /opt copy
that holds a MISMATCHED chromium build. The fix replaces the broken plugin
server with a managed, version-PINNED user-scope `playwright` MCP server
(`--browser chromium --headless`) and installs the matching chromium into a
per-user cache on a no-sudo box.

This RED-phase file reproduces the config-level defect against the EXISTING
managed-config symbols (the plugin is force-ENABLED, the shared-stream env points
at /opt). The remaining behaviour (the resolver, the rendered server, the
idempotent install, the push health-check) is verified in the greenfield tests
of the GREEN phase.
"""
import unittest

import airuleset

PW_PLUGIN = "playwright@claude-plugins-official"


class TestPlaywrightMcpConfig1048(unittest.TestCase):
    def test_playwright_plugin_not_force_enabled_in_baseline(self):
        # #1048: the plugin's OWN server is the broken chrome-channel one that
        # is DEAD on a no-sudo box — it must NOT be a force-enabled baseline
        # plugin any more (it is replaced by a managed pinned MCP server).
        self.assertNotIn(
            PW_PLUGIN, airuleset.MANAGED_PLUGINS,
            "the broken chrome-channel playwright plugin must not be force-enabled")

    def test_playwright_plugin_force_disabled(self):
        # It must be force-DISABLED so the broken chrome-channel server never
        # loads alongside the managed pinned server.
        self.assertIn(
            PW_PLUGIN, airuleset.MANAGED_DISABLED_PLUGINS,
            "the broken chrome-channel playwright plugin must be force-disabled")

    def test_stream_env_points_playwright_at_per_user_cache_not_opt(self):
        # #1048: the shared-stream ~/.bashrc must export the per-user cache (the
        # location airuleset installs the pinned chromium into), NEVER the
        # root-owned /opt/ms-playwright with its mismatched build (the #950
        # drift the incident is about). Assert on the ACTIVE `export` lines
        # only (a /opt mention in an explanatory comment is fine).
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        export_lines = [ln for ln in STREAM_ENV_BASHRC_BLOCK.splitlines()
                        if ln.strip().startswith("export")
                        and "PLAYWRIGHT_BROWSERS_PATH" in ln]
        self.assertTrue(export_lines,
                        "the shared-stream env must export PLAYWRIGHT_BROWSERS_PATH")
        for ln in export_lines:
            self.assertNotIn("/opt/ms-playwright", ln,
                             "the shared-stream env must not export the mismatched /opt copy")
            self.assertIn(".cache/ms-playwright", ln,
                          "the shared-stream env must point at the per-user cache")


if __name__ == "__main__":
    unittest.main()
