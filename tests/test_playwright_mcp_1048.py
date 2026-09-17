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
import json
import unittest
from pathlib import Path
from unittest import mock

import airuleset
import cli_caveman_plugins as p

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


class TestPlaywrightMcpPin1048(unittest.TestCase):
    """The @playwright/mcp / playwright / chromium-build MATCHED TRIO is
    version-PINNED (never @latest) so the MCP server, the installed browser and
    the /opt build-match probe agree by construction (verified against the npm
    registry 2026-09-17: @playwright/mcp@0.0.81 depends on
    playwright@1.64.0-alpha-2026-09-14 = chromium build 1244)."""

    def test_pin_is_never_latest(self):
        self.assertNotEqual(p.PLAYWRIGHT_MCP_VERSION.lower(), "latest")
        self.assertNotEqual(p.PLAYWRIGHT_PW_VERSION.lower(), "latest")
        self.assertNotIn("latest", p.PLAYWRIGHT_MCP_VERSION.lower())
        self.assertNotIn("latest", p.PLAYWRIGHT_PW_VERSION.lower())

    def test_pin_is_the_verified_matched_trio(self):
        self.assertEqual(p.PLAYWRIGHT_MCP_VERSION, "0.0.81")
        self.assertEqual(p.PLAYWRIGHT_PW_VERSION, "1.64.0-alpha-2026-09-14")
        self.assertEqual(p.PLAYWRIGHT_CHROMIUM_BUILD, "1244")


class TestRenderMcpServer1048(unittest.TestCase):
    def test_server_selects_chromium_headless_pinned(self):
        srv = p.render_playwright_mcp_server("/home/u/.cache/ms-playwright")
        self.assertEqual(srv["command"], "npx")
        self.assertEqual(
            srv["args"],
            ["-y", "@playwright/mcp@" + p.PLAYWRIGHT_MCP_VERSION,
             "--browser", "chromium", "--headless"])
        self.assertEqual(srv["env"]["PLAYWRIGHT_BROWSERS_PATH"],
                         "/home/u/.cache/ms-playwright")
        # belt-and-suspenders env alongside the authoritative CLI flags
        self.assertEqual(srv["env"]["PLAYWRIGHT_MCP_BROWSER"], "chromium")
        self.assertEqual(srv["env"]["PLAYWRIGHT_MCP_HEADLESS"], "true")

    def test_reconcile_adds_playwright_keeps_everything_else(self):
        before = {"topLevel": 1, "mcpServers": {"other": {"command": "x"}}}
        after = p.reconcile_playwright_mcp_server(before, "/c")
        self.assertEqual(after["topLevel"], 1)
        self.assertIn("other", after["mcpServers"])
        self.assertIn("playwright", after["mcpServers"])
        self.assertEqual(after["mcpServers"]["playwright"]["args"][1],
                         "@playwright/mcp@" + p.PLAYWRIGHT_MCP_VERSION)

    def test_reconcile_does_not_mutate_input(self):
        before = {"mcpServers": {}}
        p.reconcile_playwright_mcp_server(before, "/c")
        self.assertEqual(before["mcpServers"], {})

    def test_reconcile_is_a_noop_when_not_managed(self):
        with mock.patch.object(p, "PLAYWRIGHT_MANAGED", False):
            after = p.reconcile_playwright_mcp_server({"mcpServers": {}}, "/c")
        self.assertNotIn("playwright", after["mcpServers"])


class TestBrowsersPathResolver1048(unittest.TestCase):
    """The ONE resolver, verified for BOTH box classes (dispatch item 2)."""

    def test_shared_stream_always_per_user_cache_never_opt(self):
        # A no-sudo box can never write /opt, and /opt holds a mismatched build
        # — so it must ALWAYS resolve to the per-user cache, regardless of the
        # /opt build-match state (this is what makes the #2420 class impossible).
        home = Path("/home/montalu1")
        for opt_match in (True, False):
            got = p.resolve_playwright_browsers_path("shared-stream", opt_match, home=home)
            self.assertEqual(got, home / ".cache" / "ms-playwright")

    def test_workstation_uses_opt_only_when_build_matches(self):
        home = Path("/home/newlevel")
        self.assertEqual(
            p.resolve_playwright_browsers_path("workstation", True, home=home),
            p.OPT_MS_PLAYWRIGHT)
        self.assertEqual(
            p.resolve_playwright_browsers_path("workstation", False, home=home),
            home / ".cache" / "ms-playwright")

    def test_controller_and_gk_follow_the_workstation_rule(self):
        home = Path("/home/airuleset")
        for bc in ("controller", "gk"):
            self.assertEqual(
                p.resolve_playwright_browsers_path(bc, True, home=home),
                p.OPT_MS_PLAYWRIGHT)
            self.assertEqual(
                p.resolve_playwright_browsers_path(bc, False, home=home),
                home / ".cache" / "ms-playwright")

    def test_opt_has_pinned_build_checks_the_pinned_build_dir(self):
        import tempfile
        opt = Path(tempfile.mkdtemp())
        self.assertFalse(p._opt_has_pinned_build(opt))
        (opt / ("chromium-" + p.PLAYWRIGHT_CHROMIUM_BUILD)).mkdir()
        self.assertTrue(p._opt_has_pinned_build(opt))
        # a DIFFERENT (mismatched) build present is not a match (the #2420 drift)
        opt2 = Path(tempfile.mkdtemp())
        (opt2 / "chromium-1243").mkdir()
        self.assertFalse(p._opt_has_pinned_build(opt2))


class TestReconcileMcpFile1048(unittest.TestCase):
    """reconcile_playwright_mcp_file writes ~/.claude.json + the marker,
    idempotently, preserving all other content — fully path-isolated."""

    def setUp(self):
        import tempfile
        self.d = Path(tempfile.mkdtemp())
        self.claude_json = self.d / ".claude.json"
        self.marker = self.d / "airuleset-playwright-browsers-path"
        self._mp = mock.patch.object(p, "PLAYWRIGHT_BROWSERS_PATH_MARKER", self.marker)
        self._mp.start()
        self.addCleanup(self._mp.stop)

    def _run(self, box_class="shared-stream"):
        return p.reconcile_playwright_mcp_file(self.claude_json, box_class=box_class)

    def test_writes_server_and_marker(self):
        self.claude_json.write_text(json.dumps({"existing": True, "mcpServers": {"a": {}}}))
        ok = self._run()
        self.assertTrue(ok)
        data = json.loads(self.claude_json.read_text())
        self.assertTrue(data["existing"])                 # other keys preserved
        self.assertIn("a", data["mcpServers"])            # other servers preserved
        self.assertIn("playwright", data["mcpServers"])   # managed server added
        self.assertEqual(data["mcpServers"]["playwright"]["args"][2:5],
                         ["--browser", "chromium", "--headless"])
        self.assertTrue(self.marker.exists())
        self.assertIn(".cache/ms-playwright", self.marker.read_text())

    def test_idempotent(self):
        self.claude_json.write_text("{}")
        self._run()
        first = self.claude_json.read_text()
        self._run()
        self.assertEqual(first, self.claude_json.read_text())

    def test_idempotent_against_a_claude_formatted_file(self):
        # F1/F2 (review finding 1+4): a real ~/.claude.json (2-space indent,
        # LITERAL non-ASCII, the managed server already present identical) must
        # be left BYTE-UNCHANGED — the old formatted-string / ascii-escaped
        # compare rewrote it on EVERY push, re-encoding the user's UTF-8 and
        # re-opening the concurrent-write window each time.
        browsers_path = p.resolved_browsers_path("shared-stream")
        seeded = {
            "noticeLine": "cache · živé",   # literal non-ASCII (middot + accents)
            "mcpServers": {"playwright": p.render_playwright_mcp_server(browsers_path)},
        }
        original = json.dumps(seeded, indent=2, ensure_ascii=False) + "\n"
        self.claude_json.write_text(original, encoding="utf-8")
        before = self.claude_json.read_bytes()
        ok = self._run()
        self.assertTrue(ok)
        self.assertEqual(
            before, self.claude_json.read_bytes(),
            "an already-correct Claude-formatted ~/.claude.json must not be rewritten")

    def test_creates_claude_json_when_absent(self):
        # no ~/.claude.json yet -> the managed server is still written.
        self.assertFalse(self.claude_json.exists())
        ok = self._run()
        self.assertTrue(ok)
        self.assertIn("playwright",
                      json.loads(self.claude_json.read_text())["mcpServers"])

    def test_invalid_json_is_non_fatal_and_reported(self):
        self.claude_json.write_text("{ this is not json")
        import io
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            ok = self._run()
        self.assertFalse(ok)                              # genuine failure -> False
        self.assertIn("invalid JSON", err.getvalue())

    def test_noop_true_when_not_managed(self):
        with mock.patch.object(p, "PLAYWRIGHT_MANAGED", False):
            ok = self._run()
        self.assertTrue(ok)
        self.assertFalse(self.claude_json.exists())


class TestProvisionPlaywrightMcp1048(unittest.TestCase):
    def test_provision_calls_browser_install_then_server_reconcile(self):
        with mock.patch.object(p, "ensure_playwright_browsers") as eb, \
                mock.patch.object(p, "reconcile_playwright_mcp_file",
                                  return_value=True) as rc:
            ok = p.provision_playwright_mcp(box_class="shared-stream")
        self.assertTrue(ok)
        eb.assert_called_once()
        rc.assert_called_once()

    def test_provision_returns_false_when_server_reconcile_fails(self):
        with mock.patch.object(p, "ensure_playwright_browsers"), \
                mock.patch.object(p, "reconcile_playwright_mcp_file",
                                  return_value=False):
            self.assertFalse(p.provision_playwright_mcp())

    def test_provision_noop_true_when_not_managed(self):
        with mock.patch.object(p, "PLAYWRIGHT_MANAGED", False), \
                mock.patch.object(p, "ensure_playwright_browsers") as eb:
            self.assertTrue(p.provision_playwright_mcp())
        eb.assert_not_called()


class TestChromiumPostcheck1048(unittest.TestCase):
    """The push-time health-check shell fragment (mirrors #1051's gh-chain
    postcheck): bounded, PATH-forcing, skip-if-absent, distinct rc, pinned."""

    def _frag(self):
        import cli_remote
        return cli_remote._playwright_chromium_postcheck()

    def test_fragment_is_bounded_and_fails_the_target(self):
        f = self._frag()
        self.assertIn("timeout -k 5 30", f)         # bounded <= 30s, kill-hardened
        self.assertIn("exit 88", f)                 # distinct rc from gh-chain's 87
        self.assertIn("--browser chromium", f)
        self.assertIn("about:blank", f)             # no network/display dependency
        self.assertIn("playwright@" + p.PLAYWRIGHT_PW_VERSION, f)  # pinned

    def test_fragment_skips_when_npx_absent_and_forces_path(self):
        f = self._frag()
        # no npx -> SKIP (exit 0) with a VISIBLE skip line (finding 3)
        self.assertIn('command -v npx >/dev/null 2>&1 ||', f)
        self.assertIn("SKIPPED: no npx", f)
        self.assertIn("exit 0", f)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', f)
        # reads the resolved browsers path the install wrote
        self.assertIn("airuleset-playwright-browsers-path", f)

    def test_fragment_skips_when_marker_absent(self):
        # F1: a box with no marker (PLAYWRIGHT_MANAGED opt-out / provision did
        # not run) has nothing to verify -> SKIP (visible line), never a false
        # failure.
        f = self._frag()
        self.assertIn('[ -n "$BP" ] ||', f)
        self.assertIn("SKIPPED: managed Playwright not provisioned", f)

    def test_postcheck_is_wired_into_the_deploy_loop(self):
        import inspect
        import cli_remote
        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        self.assertIn("_playwright_chromium_postcheck()", src)


class TestStream2420(unittest.TestCase):
    """Dispatch item 4: re-verify the odoo-erp#2420 class cannot recur on every
    stream box class (montalu / david / miva are all shared-stream; simap is
    PAUSED, #851, so it is not provisioned). The #2420 failure was a no-sudo box
    resolving to the root-owned /opt copy (chrome-not-found / build drift); the
    proof it cannot recur is that every such account resolves to the per-user
    cache with a chromium-selecting pinned server."""

    STREAM_ACCOUNTS = ("montalu1", "david1", "david2", "david3", "david4", "miva1")
    PAUSED_ACCOUNTS = ("simap1",)

    def test_every_stream_account_resolves_to_per_user_cache(self):
        for acct in self.STREAM_ACCOUNTS:
            home = Path("/home") / acct
            # a stream account is shared-stream -> per-user cache, even if /opt
            # happens to hold the pinned build (it cannot write it anyway).
            for opt_match in (True, False):
                got = p.resolve_playwright_browsers_path("shared-stream", opt_match, home=home)
                self.assertEqual(got, home / ".cache" / "ms-playwright",
                                 "%s must never use the root-owned /opt copy" % acct)

    def test_managed_server_selects_chromium_for_a_stream_account(self):
        # The rendered server for a stream account's per-user cache never
        # requires the chrome channel -> the #2420 "chrome not found" is gone.
        home = Path("/home/montalu1")
        path = p.resolve_playwright_browsers_path("shared-stream", False, home=home)
        srv = p.render_playwright_mcp_server(path)
        self.assertIn("--browser", srv["args"])
        self.assertIn("chromium", srv["args"])
        self.assertNotIn("chrome", srv["args"])       # never the dead chrome channel

    def test_paused_simap_is_documented_not_provisioned(self):
        # simap1 is paused (#851): it is deliberately excluded, not tested as a
        # working stream box. This asserts the fixture set keeps them separate.
        self.assertNotIn("simap1", self.STREAM_ACCOUNTS)
        self.assertIn("simap1", self.PAUSED_ACCOUNTS)


if __name__ == "__main__":
    unittest.main()
