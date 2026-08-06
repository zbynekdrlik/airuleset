"""Managed baseline plugins — every managed user's Claude gets superpowers.

Incident: david@gk (onboarded 2026-07-08) had no /brainstorming — only the
caveman plugin was airuleset-managed, while the ruleset's own workflow +
completion gates invoke superpowers skills directly (brainstorming,
writing-plans, subagent-driven-development, requesting-code-review). Install
now wires a managed plugin BASELINE the same way it wires caveman: install
the cache if missing, force the enabledPlugins key true, idempotently.
"""

import inspect
import os
import sys
import tempfile
import unittest.mock as m
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class TestReconcileManagedPlugins(TestCase):
    def test_enables_every_baseline_plugin(self):
        out = airuleset.reconcile_managed_plugins({})
        for key in airuleset.MANAGED_PLUGINS:
            self.assertTrue(out["enabledPlugins"][key])

    def test_superpowers_is_in_the_baseline(self):
        self.assertIn("superpowers@claude-plugins-official",
                      airuleset.MANAGED_PLUGINS)

    def test_playwright_is_in_the_baseline(self):
        # #158: Playwright is MANDATED by the rules (autonomous-verification,
        # e2e-real-user-testing, post-deploy-verification, version-on-
        # dashboard) as the browser-driving verification tool, but was only
        # ever installed by hand — david@subdev had none at all.
        self.assertIn("playwright@claude-plugins-official",
                      airuleset.MANAGED_PLUGINS)

    def test_preserves_unrelated_keys_and_plugins(self):
        settings = {"model": "sonnet",
                    "enabledPlugins": {"caveman@caveman": True,
                                       "discord@claude-plugins-official": False}}
        out = airuleset.reconcile_managed_plugins(settings)
        self.assertEqual(out["model"], "sonnet")
        self.assertTrue(out["enabledPlugins"]["caveman@caveman"])
        self.assertFalse(out["enabledPlugins"]["discord@claude-plugins-official"])

    def test_idempotent(self):
        once = airuleset.reconcile_managed_plugins({"enabledPlugins": {}})
        twice = airuleset.reconcile_managed_plugins(once)
        self.assertEqual(once, twice)

    def test_pure_does_not_mutate_input(self):
        settings = {"enabledPlugins": {}}
        airuleset.reconcile_managed_plugins(settings)
        self.assertEqual(settings["enabledPlugins"], {})


class TestManagedDisabledPlugins(TestCase):
    """#39 item 3 (2026-07-25 /doctor findings, dev2): rust-analyzer-lsp +
    claude-md-management had 0 lifetime uses and were disabled directly in
    dev2's settings.json by `/doctor`. airuleset's plugin reconcile only
    ever ENABLES MANAGED_PLUGINS and otherwise merges enabledPlugins
    untouched, so these disables already survive a normal push — this list
    makes the intent EXPLICIT and durable so a future push can never
    silently resurrect them."""

    def test_disables_every_listed_plugin(self):
        out = airuleset.reconcile_managed_plugins({})
        for key in airuleset.MANAGED_DISABLED_PLUGINS:
            self.assertFalse(out["enabledPlugins"][key])

    def test_rust_analyzer_and_claude_md_management_are_listed(self):
        self.assertIn("rust-analyzer-lsp@claude-plugins-official",
                      airuleset.MANAGED_DISABLED_PLUGINS)
        self.assertIn("claude-md-management@claude-plugins-official",
                      airuleset.MANAGED_DISABLED_PLUGINS)

    def test_overrides_an_existing_true_value(self):
        settings = {"enabledPlugins":
                   {"rust-analyzer-lsp@claude-plugins-official": True}}
        out = airuleset.reconcile_managed_plugins(settings)
        self.assertFalse(out["enabledPlugins"]["rust-analyzer-lsp@claude-plugins-official"])

    def test_does_not_disable_the_managed_baseline(self):
        # sanity: the disabled list and MANAGED_PLUGINS never overlap — a
        # baseline plugin must never end up force-disabled.
        self.assertEqual(set(airuleset.MANAGED_DISABLED_PLUGINS)
                         & set(airuleset.MANAGED_PLUGINS), set())

    def test_unrelated_plugins_untouched(self):
        settings = {"enabledPlugins": {"caveman@caveman": True,
                                       "discord@claude-plugins-official": True}}
        out = airuleset.reconcile_managed_plugins(settings)
        self.assertTrue(out["enabledPlugins"]["caveman@caveman"])
        self.assertTrue(out["enabledPlugins"]["discord@claude-plugins-official"])


class TestManagedPluginBuilt(TestCase):
    def _claude_dir_with(self, rel):
        d = tempfile.mkdtemp()
        if rel:
            (Path(d) / rel).mkdir(parents=True)
        return Path(d)

    def test_detects_installed_superpowers_cache(self):
        d = self._claude_dir_with(
            "plugins/cache/claude-plugins-official/superpowers/6.1.1/skills")
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertTrue(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))

    def test_absent_cache_means_not_built(self):
        d = self._claude_dir_with(None)
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))

    def test_every_baseline_plugin_has_a_cache_glob(self):
        for key in airuleset.MANAGED_PLUGINS:
            self.assertIn(key, airuleset.MANAGED_PLUGIN_CACHE_GLOBS)

    def test_detects_installed_playwright_cache(self):
        # Playwright's real cache shape (confirmed live, dev1) differs from
        # superpowers': a literal "unknown" version segment instead of a
        # content hash, so the glob has to match on `.mcp.json` — the real
        # load-bearing file for this plugin's MCP server (never the
        # `.claude-plugin/plugin.json` manifest alone, which could survive
        # an interrupted extraction and still report "built").
        d = self._claude_dir_with(
            "plugins/cache/claude-plugins-official/playwright/unknown")
        (d / "plugins/cache/claude-plugins-official/playwright/unknown/"
           ".mcp.json").write_text("{}")
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertTrue(airuleset._managed_plugin_built(
                "playwright@claude-plugins-official"))

    def test_manifest_alone_without_mcp_json_is_not_built(self):
        # An interrupted extraction leaving only the manifest must NOT
        # report "built" — #158 review finding.
        d = self._claude_dir_with(
            "plugins/cache/claude-plugins-official/playwright/unknown/"
            ".claude-plugin")
        (d / "plugins/cache/claude-plugins-official/playwright/unknown/"
           ".claude-plugin/plugin.json").write_text("{}")
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "playwright@claude-plugins-official"))

    def test_absent_playwright_cache_means_not_built(self):
        d = self._claude_dir_with(None)
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "playwright@claude-plugins-official"))


class TestClaudeCliEnv(TestCase):
    """A push's remote install runs in a non-login ssh shell whose PATH lacks
    ~/.local/bin (where the claude CLI lives) — [Errno 2] 'claude' seen live
    on the gatekeeper migration. _claude_cli_env must repair it idempotently."""

    def test_prepends_local_bin_when_missing(self):
        with m.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            env = airuleset._claude_cli_env()
        self.assertTrue(env["PATH"].startswith(
            str(Path.home() / ".local" / "bin") + ":"))

    def test_does_not_duplicate_when_present(self):
        local_bin = str(Path.home() / ".local" / "bin")
        with m.patch.dict(os.environ, {"PATH": f"{local_bin}:/usr/bin"}):
            env = airuleset._claude_cli_env()
        self.assertEqual(env["PATH"].split(":").count(local_bin), 1)


class TestInstallWiresManagedPlugins(TestCase):
    def test_cmd_install_calls_setup_managed_plugins(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("setup_managed_plugins()", src)

    def test_plugin_install_subprocesses_use_cli_env(self):
        # BOTH plugin installers must carry the PATH-repaired env — a bare
        # subprocess call regresses to the remote-install [Errno 2] failure.
        for fn in (airuleset.setup_managed_plugins, airuleset.setup_caveman):
            src = inspect.getsource(fn)
            if "plugin" in src and "install" in src:
                self.assertIn("env=_claude_cli_env()", src, fn.__name__)

    def test_setup_managed_plugins_calls_ensure_playwright_browsers(self):
        src = inspect.getsource(airuleset.setup_managed_plugins)
        self.assertIn("ensure_playwright_browsers()", src)


class TestPlaywrightBrowsers(TestCase):
    """#158 review finding: enabling the plugin alone does NOT pull the
    actual browser binaries — measured live, three fleet accounts (montalu2/
    montalu3/montalu4) had node + the plugin enabled but an EMPTY browser
    cache, so every real browser call would fail with "Executable doesn't
    exist" until someone ran `npx playwright install chromium` by hand."""

    def _empty_dir(self):
        return Path(tempfile.mkdtemp())

    def _populated_dir(self):
        d = Path(tempfile.mkdtemp())
        (d / "chromium-1234").mkdir()
        return d

    def test_absent_cache_is_not_installed(self):
        d = Path(tempfile.mkdtemp()) / "does-not-exist"
        self.assertFalse(airuleset._playwright_browsers_installed(d))

    def test_empty_cache_dir_is_not_installed(self):
        # a bare `mkdir` from an interrupted install must NOT look "done"
        # forever — only real content inside counts.
        self.assertFalse(airuleset._playwright_browsers_installed(self._empty_dir()))

    def test_populated_cache_dir_is_installed(self):
        self.assertTrue(airuleset._playwright_browsers_installed(self._populated_dir()))

    def test_no_op_when_playwright_not_in_the_baseline(self):
        with m.patch.object(airuleset, "MANAGED_PLUGINS", ("superpowers@claude-plugins-official",)), \
                m.patch("subprocess.run") as run:
            airuleset.ensure_playwright_browsers(self._empty_dir())
        run.assert_not_called()

    def test_no_op_when_already_populated(self):
        with m.patch("subprocess.run") as run:
            airuleset.ensure_playwright_browsers(self._populated_dir())
        run.assert_not_called()

    def test_installs_when_cache_is_missing(self):
        with m.patch("subprocess.run", return_value=m.Mock(returncode=0)) as run:
            airuleset.ensure_playwright_browsers(self._empty_dir())
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["npx", "--yes", "playwright", "install", "chromium"])
        self.assertIn("env", run.call_args.kwargs)

    def test_install_failure_is_loud_but_non_fatal(self):
        out = StringIO()
        with m.patch("subprocess.run",
                     return_value=m.Mock(returncode=1, stderr="boom", stdout="")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_playwright_browsers(self._empty_dir())   # must not raise
        self.assertIn("playwright install chromium", out.getvalue())

    def test_install_exception_is_non_fatal(self):
        out = StringIO()
        with m.patch("subprocess.run", side_effect=FileNotFoundError("npx")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_playwright_browsers(self._empty_dir())   # must not raise
        self.assertIn("playwright install chromium", out.getvalue())


if __name__ == "__main__":
    main()
