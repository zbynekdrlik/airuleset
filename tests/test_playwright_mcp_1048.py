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

    def test_reconcile_coerces_a_non_dict_mcpservers(self):
        # F2 (review-2 finding 2): a live ~/.claude.json with `"mcpServers":
        # null` or a stray non-dict must not crash (the raise was swallowed as
        # non-fatal, leaving the box unprovisioned) — coerce to {} and add ours.
        for bad in (None, "oops", 5, []):
            after = p.reconcile_playwright_mcp_server({"mcpServers": bad}, "/c")
            self.assertIn("playwright", after["mcpServers"])


class TestBoxClass1048(unittest.TestCase):
    def test_current_box_class_degrades_to_workstation_on_none(self):
        # F3 (review-2 finding 3): default_box_class() returns None on a
        # missing/unreadable marker — _current_box_class must degrade to
        # "workstation", never leak None into the resolver.
        with mock.patch("watchdog.reaper.default_box_class", return_value=None):
            self.assertEqual(p._current_box_class(), "workstation")

    def test_current_box_class_degrades_on_exception(self):
        with mock.patch("watchdog.reaper.default_box_class",
                        side_effect=RuntimeError("boom")):
            self.assertEqual(p._current_box_class(), "workstation")


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

    @staticmethod
    def _mk(*builds, marker=True):
        """A temp browsers dir holding each named build subdir, each WITH
        (marker=True) or WITHOUT an INSTALLATION_COMPLETE file."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        for name in builds:
            sub = d / name
            sub.mkdir()
            if marker:
                (sub / "INSTALLATION_COMPLETE").touch()
        return d

    def _both(self):
        b = p.PLAYWRIGHT_CHROMIUM_BUILD
        return "chromium-" + b, "chromium_headless_shell-" + b

    def test_opt_has_pinned_build_requires_both_halves(self):
        # #1048 fix-forward (a): the /opt reuse check now needs BOTH chromium-<b>
        # AND chromium_headless_shell-<b> (each with INSTALLATION_COMPLETE).
        chromium, headless = self._both()
        self.assertFalse(p._opt_has_pinned_build(self._mk()))            # empty
        self.assertFalse(p._opt_has_pinned_build(self._mk(chromium)))    # half only
        self.assertTrue(p._opt_has_pinned_build(self._mk(chromium, headless)))
        # a DIFFERENT (mismatched) build present is not a match (the #2420 drift)
        self.assertFalse(p._opt_has_pinned_build(self._mk("chromium-1243")))

    def test_pinned_build_installed_requires_BOTH_halves_and_markers(self):
        # #1048 fix-forward (a) — THE headline montalu3-6 defect: a half cache
        # (chromium-<b> ONLY, no headless shell) must NOT count as installed.
        chromium, headless = self._both()
        self.assertFalse(p._playwright_pinned_build_installed(self._mk()))  # empty
        # wrong/old build (review-2 finding 1) still not installed
        self.assertFalse(p._playwright_pinned_build_installed(self._mk("chromium-1234")))
        # the montalu3-6 half state: chromium-<b> complete, headless shell absent
        self.assertFalse(p._playwright_pinned_build_installed(self._mk(chromium)))
        # chromium present but WITHOUT its marker (interrupted) is not installed
        self.assertFalse(
            p._playwright_pinned_build_installed(self._mk(chromium, headless, marker=False)))
        # both halves + both markers => installed
        self.assertTrue(p._playwright_pinned_build_installed(self._mk(chromium, headless)))


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
    def setUp(self):
        import tempfile
        # #1048 fix-forward (d): isolate from the box's REAL opt-out marker so
        # these tests are deterministic regardless of the host filesystem.
        self.d = Path(tempfile.mkdtemp())
        self.optout = self.d / "airuleset-playwright-optout"
        self.bp_marker = self.d / "airuleset-playwright-browsers-path"
        self.claude_json = self.d / ".claude.json"
        for name, val in (("PLAYWRIGHT_OPTOUT_MARKER", self.optout),
                          ("PLAYWRIGHT_BROWSERS_PATH_MARKER", self.bp_marker)):
            pt = mock.patch.object(p, name, val)
            pt.start()
            self.addCleanup(pt.stop)

    def test_provision_opt_out_skips_install_server_and_tears_down(self):
        # (d): the per-box opt-out marker present => no browser install, no
        # server write; instead TEAR DOWN prior provisioning (marker + server),
        # print one honest line with the reason, return True (opt-out = success).
        import io
        self.optout.write_text(
            "spinbike-vps: no root, chromium system libs unavailable\n")
        out = io.StringIO()
        with mock.patch.object(p, "ensure_playwright_browsers") as eb, \
                mock.patch.object(p, "reconcile_playwright_mcp_file") as rc, \
                mock.patch.object(p, "unprovision_playwright_mcp_file") as un, \
                mock.patch("sys.stdout", out):
            ok = p.provision_playwright_mcp(box_class="shared-stream")
        self.assertTrue(ok)
        eb.assert_not_called()
        rc.assert_not_called()
        un.assert_called_once()                    # prior provisioning torn down
        self.assertIn("opted out on this box", out.getvalue())
        self.assertIn("spinbike-vps", out.getvalue())

    def test_provision_opt_out_survives_a_non_utf8_reason(self):
        # (d) review-1 finding: a non-UTF-8 reason line must NOT crash provision.
        self.optout.write_bytes(b"\xff\xfe not utf-8 reason\n")
        with mock.patch.object(p, "unprovision_playwright_mcp_file"), \
                mock.patch("sys.stdout", __import__("io").StringIO()):
            ok = p.provision_playwright_mcp(box_class="shared-stream")  # must not raise
        self.assertTrue(ok)

    def test_unprovision_removes_managed_server_and_marker_keeps_others(self):
        # (d) review-1 MAJOR: an opted-out box provisioned by an earlier push
        # (v0.1.321 wrote the server) must have the DEAD server removed, not
        # merely skipped — else Claude Code keeps launching a broken browser.
        self.bp_marker.write_text("/home/x/.cache/ms-playwright\n")
        seeded = {
            "topLevel": 1,
            "mcpServers": {
                "playwright": p.render_playwright_mcp_server("/c"),
                "other": {"command": "keepme"},
            },
        }
        self.claude_json.write_text(json.dumps(seeded), encoding="utf-8")
        p.unprovision_playwright_mcp_file(self.claude_json)
        data = json.loads(self.claude_json.read_text())
        self.assertNotIn("playwright", data["mcpServers"])   # dead server removed
        self.assertIn("other", data["mcpServers"])           # other server kept
        self.assertEqual(data["topLevel"], 1)                # other keys kept
        self.assertFalse(self.bp_marker.exists())            # marker removed

    def test_unprovision_is_idempotent_when_server_absent(self):
        seeded = {"mcpServers": {"other": {"command": "x"}}}
        self.claude_json.write_text(json.dumps(seeded), encoding="utf-8")
        before = self.claude_json.read_bytes()
        p.unprovision_playwright_mcp_file(self.claude_json)   # no managed server
        self.assertEqual(before, self.claude_json.read_bytes())

    def test_unprovision_non_fatal_on_missing_and_invalid_json(self):
        # no ~/.claude.json at all -> no raise, marker still removed
        self.bp_marker.write_text("x\n")
        self.assertFalse(self.claude_json.exists())
        p.unprovision_playwright_mcp_file(self.claude_json)   # must not raise
        self.assertFalse(self.bp_marker.exists())
        # invalid JSON -> non-fatal, left as-is
        self.claude_json.write_text("{ not json")
        import io
        with mock.patch("sys.stderr", io.StringIO()):
            p.unprovision_playwright_mcp_file(self.claude_json)  # must not raise
        self.assertEqual("{ not json", self.claude_json.read_text())

    def test_provision_no_opt_out_proceeds_normally(self):
        # (d): with NO opt-out marker, provisioning proceeds (install + server).
        self.assertFalse(self.optout.exists())
        with mock.patch.object(p, "ensure_playwright_browsers") as eb, \
                mock.patch.object(p, "reconcile_playwright_mcp_file",
                                  return_value=True) as rc:
            ok = p.provision_playwright_mcp(box_class="shared-stream")
        self.assertTrue(ok)
        eb.assert_called_once()
        rc.assert_called_once()

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

    def test_fragment_keeps_probe_stderr_and_names_exit_127(self):
        # #1048 fix-forward (c): the probe's stderr must be captured (not
        # discarded to /dev/null) and its last lines surfaced in the FAILED
        # message, and exit 127 named explicitly as missing system libraries.
        f = self._frag()
        self.assertIn("tail -n 3", f)                    # keep the last stderr lines
        self.assertIn("stderr tail", f)                  # surfaced in the message
        self.assertIn("127", f)                          # names the exit code
        self.assertIn("missing system shared libraries", f)
        self.assertIn("install-deps", f)                 # the named remedy
        self.assertIn('2>"$ERR"', f)                     # probe stderr -> temp file, not discarded

    def test_fragment_skips_loudly_when_opted_out(self):
        # #1048 fix-forward (d): a per-box opt-out marker makes the post-check
        # SKIP loudly with the reason, never a false failure.
        f = self._frag()
        self.assertIn("airuleset-playwright-optout", f)
        self.assertIn("opted out on this box", f)

    def test_postcheck_is_wired_into_the_deploy_loop(self):
        import inspect
        import cli_remote
        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        self.assertIn("_playwright_chromium_postcheck()", src)


class TestChromiumPostcheckExec1048(unittest.TestCase):
    """#1048 fix-forward review-1: the substring tests above cannot catch a shell
    syntax / quoting / command-substitution regression in this increasingly
    complex fragment. Execute it in a real bash with a controlled HOME + a fake
    npx (no real browser) and assert each branch's rc + operator message."""

    def setUp(self):
        import cli_remote
        import tempfile
        self.frag = cli_remote._playwright_chromium_postcheck()
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        (self.home / ".local" / "bin").mkdir(parents=True)

    def _fake_npx(self, script):
        binp = self.home / ".local" / "bin" / "npx"
        binp.write_text("#!/bin/sh\n" + script + "\n")
        binp.chmod(0o755)

    def _marker(self):
        (self.home / ".claude" / "airuleset-playwright-browsers-path").write_text("/tmp/bp\n")

    def _run(self):
        import os
        import subprocess
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = str(self.home / ".local" / "bin") + os.pathsep + env.get("PATH", "")
        return subprocess.run(["bash", "-c", self.frag],
                              capture_output=True, text=True, timeout=60, env=env)

    def test_fragment_is_valid_bash(self):
        import subprocess
        r = subprocess.run(["bash", "-n", "-c", self.frag],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_opt_out_skips_cleanly_with_reason(self):
        (self.home / ".claude" / "airuleset-playwright-optout").write_text("spinbike: no root\n")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("SKIPPED: managed Playwright opted out", r.stderr)
        self.assertIn("spinbike: no root", r.stderr)

    def test_missing_marker_skips_cleanly(self):
        self._fake_npx("exit 0")
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertIn("SKIPPED: managed Playwright not provisioned", r.stderr)

    def test_exit_127_fails_88_with_named_diagnosis(self):
        self._marker()
        self._fake_npx('echo "error while loading shared libraries: libatk" >&2; exit 127')
        r = self._run()
        self.assertEqual(r.returncode, 88)
        self.assertIn("exited 127", r.stderr)
        self.assertIn("missing system shared libraries", r.stderr)
        self.assertIn("install-deps", r.stderr)
        self.assertIn("stderr tail", r.stderr)

    def test_generic_failure_fails_88_with_stderr_tail(self):
        self._marker()
        self._fake_npx('echo "Executable doesnt exist at chromium_headless_shell" >&2; exit 1')
        r = self._run()
        self.assertEqual(r.returncode, 88)
        self.assertIn("did not render in 30s", r.stderr)
        self.assertIn("Executable doesnt exist", r.stderr)   # real error surfaced

    def test_success_exits_zero_and_leaves_no_probe_temp(self):
        import glob
        self._marker()
        self._fake_npx("exit 0")
        # snapshot-diff (not "/tmp is empty"): a concurrent probe/deploy on the
        # same box must not false-fail this — assert only that THIS run added no
        # lingering probe temp of its own.
        before = set(glob.glob("/tmp/airuleset-pw-probe.*"))
        r = self._run()
        self.assertEqual(r.returncode, 0)
        after = set(glob.glob("/tmp/airuleset-pw-probe.*"))
        self.assertEqual(set(), after - before,
                         "the probe must clean its own temp files")


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
