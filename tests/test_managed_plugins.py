"""Managed baseline plugins — every managed user's Claude gets superpowers.

Incident: david@gk (onboarded 2026-07-08) had no /brainstorming — only the
caveman plugin was airuleset-managed, while the ruleset's own workflow +
completion gates invoke superpowers skills directly (brainstorming,
writing-plans, subagent-driven-development, requesting-code-review). Install
now wires a managed plugin BASELINE the same way it wires caveman: install
the cache if missing, force the enabledPlugins key true, idempotently.
"""

import inspect
import json
import os
import sys
import tempfile
import unittest.mock as m
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
# #433 cluster L-C: MANAGED_PLUGINS / PLAYWRIGHT_BROWSER_CACHE
# moved into cli_caveman_plugins.py; the functions that READ them (reconcile_
# managed_plugins / ensure_playwright_browsers / setup_managed_plugins /
# _playwright_browsers_installed) moved with them and resolve those names in the
# LEAF's namespace — so a `patch.object(airuleset, "<X>")` is a silent no-op and
# every such patch below is repointed to `cli_caveman_plugins` (K #1482 seam).
# Plain READS of `airuleset.MANAGED_PLUGINS` etc. still work via the facade
# re-export and stay unchanged.
import cli_caveman_plugins


def _write_plugin_registry(claude_dir: Path, keys):
    """Write a fake claude plugin registry (installed_plugins.json shape --
    the exact backing store `claude plugin list` renders its output from,
    confirmed live on dev1: its `plugins` dict keys match `claude plugin
    list`'s printed plugin names 1:1) declaring `keys` as genuinely
    installed. Used across this file wherever a test needs claude's OWN
    registry to say a plugin IS installed (#276 -- file-presence proxy)."""
    reg_dir = claude_dir / "plugins"
    reg_dir.mkdir(parents=True, exist_ok=True)
    data = {"version": 2,
            "plugins": {k: [{"scope": "user"}] for k in keys}}
    (reg_dir / "installed_plugins.json").write_text(json.dumps(data))


class TestReconcileManagedPlugins(TestCase):
    def test_enables_every_baseline_plugin(self):
        out = airuleset.reconcile_managed_plugins({})
        for key in airuleset.MANAGED_PLUGINS:
            self.assertTrue(out["enabledPlugins"][key])

    def test_superpowers_is_in_the_baseline(self):
        self.assertIn("superpowers@claude-plugins-official",
                      airuleset.MANAGED_PLUGINS)

    def test_playwright_is_in_the_baseline(self):
        # #542 REVERTS #415's default-off: Playwright is a force-enabled
        # baseline plugin again. Force-disabling it fleet-wide made streams
        # report "nemám playwright" and skip the UNTOUCHABLE browser
        # verification (autonomous-verification.md). The resident-Chrome cost
        # #415 cited is empirically LAZY (measured live on dev1: 6 running
        # @playwright/mcp node servers but only 1 Chrome tree — Chrome spawns
        # only on the first browser call, only in the session that makes it),
        # so enabling everywhere restores availability with NO resident
        # browser in browser-free projects, only the cheap node MCP server.
        # The old `test_playwright_is_optional_not_baseline` asserted the
        # opposite; this is a deliberate policy inversion, justified here.
        self.assertIn("playwright@claude-plugins-official",
                      airuleset.MANAGED_PLUGINS)

    def test_playwright_is_force_enabled_in_user_scope(self):
        # #542: reconcile writes playwright True (force-enabled fleet-wide),
        # so a fresh session in ANY managed project has the browser MCP
        # available with no per-project opt-in step.
        out = airuleset.reconcile_managed_plugins({})
        pw = "playwright@claude-plugins-official"
        # assertIs(..., True), not assertTrue (mirrors the #415 F5 rationale):
        # the value must be the JSON literal true, not any truthy stand-in.
        self.assertIs(out["enabledPlugins"][pw], True)

    def test_reconcile_flips_a_stale_disabled_false(self):
        # #542 headline acceptance (symmetric to #415's own flip test): every
        # box pushed under #415 carries a stale `playwright: false`. reconcile
        # must actively FLIP it back to True, not merely leave it — otherwise
        # the restoration never takes effect on the exact fleet it exists to
        # fix (live-confirmed on dev1: ~/.claude/settings.json had playwright
        # => False before this change).
        pw = "playwright@claude-plugins-official"
        out = airuleset.reconcile_managed_plugins({"enabledPlugins": {pw: False}})
        self.assertIs(out["enabledPlugins"][pw], True)

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


class TestPluginRegistryKeys(TestCase):
    """`installed_plugins.json` is claude's OWN plugin registry — the exact
    backing store `claude plugin list` renders its output from (confirmed
    live, dev1: its `plugins` dict keys match `claude plugin list`'s
    printed plugin names 1:1). `_plugin_registry_keys()` reads it directly
    — no subprocess, no CLI text-output parsing (#276)."""

    def _claude_dir(self):
        return Path(tempfile.mkdtemp())

    def test_reads_the_real_registry_shape(self):
        d = self._claude_dir()
        _write_plugin_registry(d, ["superpowers@claude-plugins-official",
                                    "playwright@claude-plugins-official"])
        keys = airuleset._plugin_registry_keys(
            d / "plugins" / "installed_plugins.json")
        self.assertEqual(keys, {"superpowers@claude-plugins-official",
                                 "playwright@claude-plugins-official"})

    def test_missing_registry_file_is_an_empty_set(self):
        d = self._claude_dir()
        keys = airuleset._plugin_registry_keys(
            d / "plugins" / "installed_plugins.json")
        self.assertEqual(keys, set())

    def test_malformed_json_is_an_empty_set_never_a_crash(self):
        d = self._claude_dir()
        (d / "plugins").mkdir(parents=True)
        (d / "plugins" / "installed_plugins.json").write_text("{not valid json")
        keys = airuleset._plugin_registry_keys(
            d / "plugins" / "installed_plugins.json")
        self.assertEqual(keys, set())

    def test_plugins_field_not_a_dict_is_an_empty_set(self):
        d = self._claude_dir()
        (d / "plugins").mkdir(parents=True)
        (d / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": ["a", "b"]}))
        keys = airuleset._plugin_registry_keys(
            d / "plugins" / "installed_plugins.json")
        self.assertEqual(keys, set())

    def test_top_level_not_a_dict_is_an_empty_set(self):
        d = self._claude_dir()
        (d / "plugins").mkdir(parents=True)
        (d / "plugins" / "installed_plugins.json").write_text("[]")
        keys = airuleset._plugin_registry_keys(
            d / "plugins" / "installed_plugins.json")
        self.assertEqual(keys, set())

    def test_registry_path_is_a_directory_degrades_to_empty_set(self):
        # Adversarial-review MAJOR finding (#276): read_file_safe()'s
        # exists()->read_text() only catches a MISSING file — a path that
        # exists but genuinely cannot be READ (a directory, unreadable
        # permissions, invalid UTF-8) raised uncaught, and that raise
        # escapes `_managed_plugin_built()` at setup_managed_plugins()'s
        # `if _managed_plugin_built(key): continue` -- which is OUTSIDE the
        # per-plugin try/except -- so cmd_install()'s own outer try/except
        # silently swallows it as "(non-fatal)": remaining plugins and
        # ensure_playwright_browsers() never run, yet "Install complete."
        # is still reported. Never guess, but never CRASH either.
        d = self._claude_dir()
        reg_as_dir = d / "plugins" / "installed_plugins.json"
        reg_as_dir.mkdir(parents=True)
        keys = airuleset._plugin_registry_keys(reg_as_dir)   # must not raise
        self.assertEqual(keys, set())

    def test_unreadable_registry_file_degrades_to_empty_set(self):
        if os.geteuid() == 0:
            self.skipTest("running as root -- chmod 0 does not deny root")
        d = self._claude_dir()
        (d / "plugins").mkdir(parents=True)
        reg = d / "plugins" / "installed_plugins.json"
        reg.write_text(json.dumps({"plugins": {"x@y": [{}]}}))
        reg.chmod(0)
        try:
            keys = airuleset._plugin_registry_keys(reg)   # must not raise
        finally:
            reg.chmod(0o644)
        self.assertEqual(keys, set())

    def test_invalid_utf8_registry_degrades_to_empty_set(self):
        d = self._claude_dir()
        (d / "plugins").mkdir(parents=True)
        reg = d / "plugins" / "installed_plugins.json"
        reg.write_bytes(b"\xff\xfe\x00\x01not-utf8-garbage")
        keys = airuleset._plugin_registry_keys(reg)   # must not raise
        self.assertEqual(keys, set())

    def test_defaults_to_claude_dir_when_no_path_given(self):
        d = self._claude_dir()
        _write_plugin_registry(d, ["caveman@caveman"])
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            keys = airuleset._plugin_registry_keys()
        self.assertEqual(keys, {"caveman@caveman"})


class TestManagedPluginBuilt(TestCase):
    """#276: "built" is decided by claude's OWN registry
    (installed_plugins.json), never by cache-file presence — a stale/
    partial cache dir left by a FAILED pre-#273 install must never look
    "genuinely installed" again just because a glob happens to match it."""

    def _claude_dir(self):
        return Path(tempfile.mkdtemp())

    def test_registry_entry_present_means_built(self):
        d = self._claude_dir()
        _write_plugin_registry(d, ["superpowers@claude-plugins-official"])
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertTrue(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))

    def test_no_registry_at_all_means_not_built(self):
        d = self._claude_dir()
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))

    def test_registry_present_but_key_absent_means_not_built(self):
        d = self._claude_dir()
        _write_plugin_registry(d, ["caveman@caveman"])
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))

    def test_malformed_registry_is_not_built_never_guessed_true(self):
        d = self._claude_dir()
        (d / "plugins").mkdir(parents=True)
        (d / "plugins" / "installed_plugins.json").write_text("{not json")
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))

    def test_stale_cache_dir_with_no_registry_entry_is_not_built(self):
        # #276's own headline RED test: a STALE cache dir (the exact shape
        # a FAILED pre-#273 install left behind on montalu2/montalu3 for
        # playwright) must NEVER look "genuinely installed" just because it
        # satisfies a file glob — only claude's own registry can say that.
        # This is the file-presence-proxy defect the ticket exists to fix.
        d = self._claude_dir()
        stale_cache = (d / "plugins" / "cache" / "claude-plugins-official"
                       / "playwright" / "unknown")
        stale_cache.mkdir(parents=True)
        (stale_cache / ".mcp.json").write_text("{}")
        # no installed_plugins.json at all — claude's registry never heard
        # of this plugin, exactly like the live batch10/batch11 evidence.
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            self.assertFalse(airuleset._managed_plugin_built(
                "playwright@claude-plugins-official"))

    def test_settings_enabled_alone_never_satisfies_the_check(self):
        # #276 acceptance: "settings-enable sam osebe nesmie stacit" —
        # _managed_plugin_built() takes no settings.json input at all, so
        # an "enabled" flag with an absent registry can only ever read
        # False through this function; only a real registry entry counts.
        d = self._claude_dir()
        settings_path = d / "settings.json"
        settings_path.write_text(json.dumps(
            {"enabledPlugins": {"superpowers@claude-plugins-official": True}}))
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path):
            self.assertFalse(airuleset._managed_plugin_built(
                "superpowers@claude-plugins-official"))


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
        # #542: the guard keys on MANAGED_PLUGINS — a genuine no-op needs
        # playwright absent from the baseline.
        with m.patch.object(cli_caveman_plugins, "MANAGED_PLUGINS", ("superpowers@claude-plugins-official",)), \
                m.patch("subprocess.run") as run:
            airuleset.ensure_playwright_browsers(self._empty_dir())
        run.assert_not_called()

    def test_installs_when_playwright_in_the_baseline(self):
        # #542: playwright is a force-enabled baseline plugin — its browser
        # cache must be provisioned on every box.
        with m.patch.object(cli_caveman_plugins, "MANAGED_PLUGINS",
                            ("superpowers@claude-plugins-official",
                             "playwright@claude-plugins-official")), \
                m.patch("subprocess.run", return_value=m.Mock(returncode=0)) as run:
            airuleset.ensure_playwright_browsers(self._empty_dir())
        run.assert_called_once()

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


class TestMarketplaceSources(TestCase):
    """#273: a fresh account (montalu2/montalu3/montalu4) has NO marketplaces
    configured at all — `claude plugin install X@Y` fails "not found in
    marketplace Y" until Y is REGISTERED (`claude plugin marketplace add`).
    The set of marketplace NAMES needed is derived from MANAGED_PLUGINS
    itself (never a second, driftable list of names); MARKETPLACE_SOURCES
    supplies the one thing that can't be derived — each name's source."""

    def test_every_marketplace_used_by_the_baseline_has_a_known_source(self):
        for name in airuleset._marketplace_names_for(airuleset.MANAGED_PLUGINS):
            self.assertIn(name, airuleset.MARKETPLACE_SOURCES)

    def test_caveman_marketplace_also_has_a_known_source(self):
        # Adversarial-review MINOR finding: the check above only iterates
        # MANAGED_PLUGINS — caveman is registered/installed through a
        # completely separate call site (setup_caveman()) and was never
        # covered. ensure_marketplace_registered() silently no-ops ("nothing
        # to do") for a name absent from MARKETPLACE_SOURCES, so a future
        # rename of CAVEMAN_PLUGIN_KEY's marketplace segment would regress
        # to the exact pre-fix failure with no test catching it.
        market = airuleset.CAVEMAN_PLUGIN_KEY.split("@", 1)[1]
        self.assertIn(market, airuleset.MARKETPLACE_SOURCES)

    def test_official_marketplace_source_matches_the_real_repo(self):
        # Confirmed live (#273, isolated scratch CLAUDE_CONFIG_DIR, CC 2.1.223):
        # `claude plugin marketplace add anthropics/claude-plugins-official`
        # clones the real official marketplace successfully.
        self.assertEqual(
            airuleset.MARKETPLACE_SOURCES["claude-plugins-official"],
            "anthropics/claude-plugins-official")

    def test_marketplace_names_for_derives_from_the_keys(self):
        self.assertEqual(
            airuleset._marketplace_names_for(("a@x", "b@x", "c@y")),
            {"x", "y"})

    def test_marketplace_names_for_ignores_bare_keys(self):
        self.assertEqual(airuleset._marketplace_names_for(("bare",)), set())


class TestReconcileManagedPluginsRegistersMarketplace(TestCase):
    def test_registers_claude_plugins_official(self):
        out = airuleset.reconcile_managed_plugins({})
        self.assertEqual(
            out["extraKnownMarketplaces"]["claude-plugins-official"]["source"]["repo"],
            airuleset.MARKETPLACE_SOURCES["claude-plugins-official"])

    def test_preserves_existing_marketplace_entries(self):
        settings = {"extraKnownMarketplaces":
                    {"caveman": {"source": {"source": "github", "repo": "x/y"}}}}
        out = airuleset.reconcile_managed_plugins(settings)
        self.assertEqual(out["extraKnownMarketplaces"]["caveman"]["source"]["repo"], "x/y")
        self.assertIn("claude-plugins-official", out["extraKnownMarketplaces"])

    def test_idempotent_including_marketplaces(self):
        once = airuleset.reconcile_managed_plugins({})
        twice = airuleset.reconcile_managed_plugins(once)
        self.assertEqual(once, twice)


class TestEnsureMarketplaceRegistered(TestCase):
    """`claude plugin marketplace add <source>` (#273) — confirmed live to be
    idempotent (rc=0, "already on disk" on a re-run), so it is safe to call
    unconditionally before every plugin-install attempt."""

    def test_calls_marketplace_add_with_the_right_source(self):
        with m.patch("subprocess.run", return_value=m.Mock(returncode=0, stdout="", stderr="")) as run:
            ok = airuleset.ensure_marketplace_registered("claude-plugins-official")
        self.assertTrue(ok)
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["claude", "plugin", "marketplace", "add",
                                 airuleset.MARKETPLACE_SOURCES["claude-plugins-official"]])
        # Adversarial-review THEORETICAL finding: a bare `"env" in kwargs`
        # check is satisfied by `env=os.environ` too — the actual historical
        # bug (a non-login ssh shell's PATH lacking ~/.local/bin, where the
        # claude CLI lives — "[Errno 2] 'claude'" on the gatekeeper
        # migration) would survive that assertion. Check the PATH content.
        local_bin = str(Path.home() / ".local" / "bin")
        self.assertIn(local_bin, run.call_args.kwargs["env"]["PATH"].split(":"))

    def test_reports_failure_loudly_and_returns_false(self):
        out = StringIO()
        with m.patch("subprocess.run",
                     return_value=m.Mock(returncode=1, stderr="boom", stdout="")), \
                m.patch("sys.stderr", out):
            ok = airuleset.ensure_marketplace_registered("claude-plugins-official")
        self.assertFalse(ok)
        self.assertIn("marketplace add", out.getvalue())

    def test_exception_is_caught_and_returns_false(self):
        out = StringIO()
        with m.patch("subprocess.run", side_effect=FileNotFoundError("claude")), \
                m.patch("sys.stderr", out):
            ok = airuleset.ensure_marketplace_registered("claude-plugins-official")
        self.assertFalse(ok)

    def test_unknown_marketplace_name_is_a_harmless_no_op(self):
        with m.patch("subprocess.run") as run:
            ok = airuleset.ensure_marketplace_registered("some-other-marketplace")
        self.assertTrue(ok)
        run.assert_not_called()


class TestSetupManagedPluginsRegistersBeforeInstall(TestCase):
    """#273: install must never be attempted before `claude plugin
    marketplace add` for that plugin's marketplace has run — on a fresh
    account, going straight to install reproduces the "not found in
    marketplace" failure every time."""

    def _empty_claude_dir(self):
        return Path(tempfile.mkdtemp())

    def test_registers_marketplace_before_installing_a_missing_plugin(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return m.Mock(returncode=0, stdout="", stderr="")

        d = self._empty_claude_dir()
        settings_path = d / "settings.json"
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run):
            ok = airuleset.setup_managed_plugins()
        self.assertTrue(ok)
        add_calls = [c for c in calls if c[:3] == ["claude", "plugin", "marketplace"]]
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        self.assertTrue(add_calls, "marketplace add was never called")
        self.assertTrue(install_calls, "plugin install was never called")
        self.assertLess(calls.index(add_calls[0]), calls.index(install_calls[0]),
                         "marketplace add must run BEFORE the first install attempt")

    def test_playwright_is_installed(self):
        # #542: playwright is a force-enabled baseline plugin — a fresh box
        # with no registry installs it (so the browser MCP works everywhere).
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return m.Mock(returncode=0, stdout="", stderr="")

        d = self._empty_claude_dir()
        settings_path = d / "settings.json"
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run):
            airuleset.setup_managed_plugins()
        installed = {c[3] for c in calls if c[:3] == ["claude", "plugin", "install"]}
        self.assertIn("playwright@claude-plugins-official", installed)

    def test_a_fresh_install_keeps_playwright_enabled_after_plugin_install(self):
        # #542: playwright is force-ENABLED (back in MANAGED_PLUGINS), so a
        # real `claude plugin install <key>` writing enabledPlugins[<key>]=true
        # is now the DESIRED end state — there is no second reconcile to flip
        # it back off (the #415 OPTIONAL tier that needed it is gone). This
        # simulates the install side effect and asserts playwright ends
        # ENABLED, alongside superpowers, through the whole sequence.
        d = self._empty_claude_dir()
        settings_path = d / "settings.json"
        pw = "playwright@claude-plugins-official"

        def fake_run(argv, **kwargs):
            argv = list(argv)
            if argv[:3] == ["claude", "plugin", "install"]:
                # Mimic real CC: installing a key writes enabledPlugins[key]=true.
                cur = json.loads(settings_path.read_text()) if settings_path.exists() else {}
                cur.setdefault("enabledPlugins", {})[argv[3]] = True
                settings_path.write_text(json.dumps(cur))
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run):
            airuleset.setup_managed_plugins()
        final = json.loads(settings_path.read_text())
        # playwright must end ENABLED (force-enabled baseline; no re-disable).
        self.assertIs(final["enabledPlugins"][pw], True)
        # superpowers is meant to stay enabled through the same sequence.
        self.assertIs(final["enabledPlugins"]["superpowers@claude-plugins-official"], True)

    def test_a_failed_marketplace_registration_skips_the_install_and_fails(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            if argv[:3] == ["claude", "plugin", "marketplace"]:
                return m.Mock(returncode=1, stdout="", stderr="network down")
            return m.Mock(returncode=0, stdout="", stderr="")

        d = self._empty_claude_dir()
        settings_path = d / "settings.json"
        out = StringIO()
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run), \
                m.patch("sys.stderr", out):
            ok = airuleset.setup_managed_plugins()
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        self.assertFalse(ok)
        self.assertEqual(install_calls, [],
                          "install must never be attempted once its marketplace "
                          "registration has failed")

    def test_already_built_plugins_never_call_marketplace_add(self):
        d = Path(tempfile.mkdtemp())
        # #542: every MANAGED_PLUGINS plugin (incl. playwright) must be
        # registry-built for the no-marketplace-add fast path.
        _write_plugin_registry(d, airuleset.MANAGED_PLUGINS)
        settings_path = d / "settings.json"
        playwright_cache = Path(tempfile.mkdtemp())
        (playwright_cache / "chromium-1234").mkdir()
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch.object(cli_caveman_plugins, "PLAYWRIGHT_BROWSER_CACHE", playwright_cache), \
                m.patch("subprocess.run") as run:
            ok = airuleset.setup_managed_plugins()
        self.assertTrue(ok)
        run.assert_not_called()

    def test_a_failed_install_after_successful_registration_returns_false(self):
        # Adversarial-review MAJOR finding: the two tests above only ever
        # exercise the REGISTRATION-failure branch (fake_run returns rc=0
        # for every install) — the "install itself still fails after
        # correct registration" branch (this ticket's own headline claim)
        # had zero coverage; deleting its `ok = False` line survived the
        # whole suite. Registration always succeeds; every install fails.
        def fake_run(argv, **kwargs):
            if argv[:3] == ["claude", "plugin", "marketplace"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            return m.Mock(returncode=1, stdout="", stderr="install exploded")

        d = self._empty_claude_dir()
        settings_path = d / "settings.json"
        out = StringIO()
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run), \
                m.patch("sys.stderr", out):
            ok = airuleset.setup_managed_plugins()
        self.assertFalse(ok)

    def test_a_bare_key_is_skipped_loudly_not_a_crash(self):
        # Adversarial-review MINOR finding: `_marketplace_names_for`
        # deliberately tolerates a bare (no "@") key, but
        # setup_managed_plugins()'s own `key.split("@", 1)[1]` is unguarded
        # — an IndexError there would be swallowed by cmd_install()'s outer
        # try/except as "(non-fatal)", with `install_failed` never set,
        # silently reporting "Install complete." A bare key must never
        # crash the whole function; the OTHER real plugins must still be
        # processed.
        def fake_run(argv, **kwargs):
            return m.Mock(returncode=0, stdout="", stderr="")

        d = self._empty_claude_dir()
        settings_path = d / "settings.json"
        out = StringIO()
        bad_plugins = ("bare-key-no-at",) + airuleset.MANAGED_PLUGINS
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch.object(cli_caveman_plugins, "MANAGED_PLUGINS", bad_plugins), \
                m.patch("subprocess.run", side_effect=fake_run), \
                m.patch("sys.stderr", out):
            airuleset.setup_managed_plugins()   # must NOT raise
        self.assertIn("bare-key-no-at", out.getvalue())

    def test_invalid_settings_json_is_reflected_in_the_return_value(self):
        # Adversarial-review MINOR finding: on a JSON-decode failure the
        # function printed a warning but left `ok` untouched — with
        # everything else healthy it would still return True ("Install
        # complete.") while enabledPlugins/extraKnownMarketplaces were
        # never actually written.
        #
        # Adversarial-review MINOR finding #2: this test relied on the
        # fast-path (registry lists both MANAGED_PLUGINS) to avoid
        # subprocess calls but never actually mocked `subprocess.run` —
        # observed live in the review: it ran REAL `claude` CLI commands
        # against this box's own account (idempotent here, but a genuine
        # unit test must never depend on that; on another box it could
        # mutate real `~/.claude` state mid-suite). Mock it AND assert it
        # is never called, matching the sibling
        # test_already_built_plugins_never_call_marketplace_add.
        d = Path(tempfile.mkdtemp())
        # #542: every MANAGED_PLUGINS plugin must be registry-built to reach
        # the no-subprocess fast path this test asserts.
        _write_plugin_registry(d, airuleset.MANAGED_PLUGINS)
        settings_path = d / "settings.json"
        settings_path.write_text("{not valid json")
        playwright_cache = Path(tempfile.mkdtemp())
        (playwright_cache / "chromium-1234").mkdir()
        out = StringIO()
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch.object(cli_caveman_plugins, "PLAYWRIGHT_BROWSER_CACHE", playwright_cache), \
                m.patch("subprocess.run") as run, \
                m.patch("sys.stderr", out):
            ok = airuleset.setup_managed_plugins()
        self.assertFalse(ok)
        run.assert_not_called()

    def test_stuck_account_self_heals_settings_enabled_registry_absent(self):
        # #276: the exact montalu2/montalu3/montalu4 shape — settings.json
        # ALREADY says the plugin is enabled (a prior push wrote it) AND a
        # stale cache dir sits on disk (left by a pre-#273 failed install),
        # but claude's own registry has never heard of the plugin. The next
        # push must actually retry the real install — no manual fix needed.
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return m.Mock(returncode=0, stdout="", stderr="")

        d = Path(tempfile.mkdtemp())
        stale_cache = (d / "plugins" / "cache" / "claude-plugins-official"
                       / "playwright" / "unknown")
        stale_cache.mkdir(parents=True)
        (stale_cache / ".mcp.json").write_text("{}")
        # no installed_plugins.json — registry never heard of either plugin
        settings_path = d / "settings.json"
        settings_path.write_text(json.dumps(
            {"enabledPlugins": {"playwright@claude-plugins-official": True,
                                 "superpowers@claude-plugins-official": True}}))
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run):
            ok = airuleset.setup_managed_plugins()
        self.assertTrue(ok)
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        installed_keys = {c[3] for c in install_calls}
        self.assertEqual(
            installed_keys,
            set(airuleset.MANAGED_PLUGINS),
            "a settings-enabled + registry-absent plugin must trigger a "
            "real install, even with a stale cache on disk and settings."
            "json already saying enabled (#542: playwright is a baseline "
            "plugin again)")


def _write_plugin_registry_with_paths(claude_dir: Path, key_path_map: dict):
    """Write a fake registry where each key maps to an installPath.

    ``key_path_map`` is ``{"key@market": "/install/path", ...}``.
    The shape matches the REAL registry structure observed on dev1:
    ``{"version": 2, "plugins": {"key": [{"scope": "user",
    "installPath": "/path/..."}]}}``."""
    reg_dir = claude_dir / "plugins"
    reg_dir.mkdir(parents=True, exist_ok=True)
    plugins = {}
    for key, path in key_path_map.items():
        plugins[key] = [{"scope": "user", "installPath": path}]
    data = {"version": 2, "plugins": plugins}
    (reg_dir / "installed_plugins.json").write_text(json.dumps(data))


class TestStaleInstallPathHealing(TestCase):
    """#845: after an account rename (montalu → montalu1, #537) every
    plugin installPath still points at /home/montalu/… (nonexistent).
    _managed_plugin_built() checks only key PRESENCE so
    setup_managed_plugins() reports 'already built' and never reinstalls.
    A stale installPath must be treated as 'not built' → re-install."""

    def test_stale_installpath_triggers_reinstall(self):
        """RED: a managed plugin with a stale installPath (nonexistent
        dir) must be reinstalled by setup_managed_plugins()."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return m.Mock(returncode=0, stdout="", stderr="")

        d = Path(tempfile.mkdtemp())
        # Write a registry where every managed plugin has an installPath
        # pointing to a NONEXISTENT directory (the exact post-rename shape).
        stale_paths = {k: "/nonexistent/old-home/.claude/plugins/cache/"
                       + k.replace("@", "/") for k in airuleset.MANAGED_PLUGINS}
        _write_plugin_registry_with_paths(d, stale_paths)
        settings_path = d / "settings.json"
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch("subprocess.run", side_effect=fake_run):
            ok = airuleset.setup_managed_plugins()
        self.assertTrue(ok)
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        installed_keys = {c[3] for c in install_calls}
        self.assertEqual(
            installed_keys,
            set(airuleset.MANAGED_PLUGINS),
            "plugins with stale installPaths (nonexistent dirs) must be "
            "reinstalled — a stale registry entry must never count as "
            "'already built' (#845)")

    def test_valid_installpath_is_not_healed(self):
        """A registry entry whose installPath EXISTS on disk must NOT be
        touched — only genuinely stale entries are healed.  Also asserts
        the registry file is NOT rewritten (#845 review finding 6)."""
        d = Path(tempfile.mkdtemp())
        # Create a real installPath dir so the entry is NOT stale.
        valid_path = d / "plugins" / "cache" / "valid"
        valid_path.mkdir(parents=True)
        _write_plugin_registry_with_paths(
            d, {"superpowers@claude-plugins-official": str(valid_path)})
        # Also write the OTHER managed plugins with valid paths.
        reg_path = d / "plugins" / "installed_plugins.json"
        data = json.loads(reg_path.read_text())
        for k in airuleset.MANAGED_PLUGINS:
            if k not in data["plugins"]:
                kdir = d / "plugins" / "cache" / k.replace("@", "_")
                kdir.mkdir(parents=True, exist_ok=True)
                data["plugins"][k] = [{"scope": "user",
                                       "installPath": str(kdir)}]
        reg_path.write_text(json.dumps(data))
        reg_bytes_before = reg_path.read_bytes()
        settings_path = d / "settings.json"
        playwright_cache = Path(tempfile.mkdtemp())
        (playwright_cache / "chromium-1234").mkdir()
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch.object(cli_caveman_plugins, "PLAYWRIGHT_BROWSER_CACHE",
                               playwright_cache), \
                m.patch("subprocess.run") as run:
            ok = airuleset.setup_managed_plugins()
        self.assertTrue(ok)
        run.assert_not_called()
        self.assertEqual(reg_path.read_bytes(), reg_bytes_before,
                         "registry must NOT be rewritten when all "
                         "installPaths are valid")

    def test_registry_without_installpath_is_unchanged(self):
        """Entries that lack installPath entirely (the shape the test
        helper _write_plugin_registry writes) must be treated as built —
        backwards-compatible with the pre-#845 behaviour.  Registry file
        must NOT be rewritten (#845 review finding 6)."""
        d = Path(tempfile.mkdtemp())
        _write_plugin_registry(d, airuleset.MANAGED_PLUGINS)
        reg_path = d / "plugins" / "installed_plugins.json"
        reg_bytes_before = reg_path.read_bytes()
        settings_path = d / "settings.json"
        playwright_cache = Path(tempfile.mkdtemp())
        (playwright_cache / "chromium-1234").mkdir()
        with m.patch.object(airuleset, "CLAUDE_DIR", d), \
                m.patch.object(airuleset, "SETTINGS_JSON", settings_path), \
                m.patch.object(cli_caveman_plugins, "PLAYWRIGHT_BROWSER_CACHE",
                               playwright_cache), \
                m.patch("subprocess.run") as run:
            ok = airuleset.setup_managed_plugins()
        self.assertTrue(ok)
        run.assert_not_called()
        self.assertEqual(reg_path.read_bytes(), reg_bytes_before,
                         "registry must NOT be rewritten when nothing "
                         "is stale")


class TestCmdInstallFailsLoudlyOnPluginFailure(TestCase):
    """#273: a still-failing plugin install (after correct marketplace
    registration) must fail the target's deploy loudly (non-zero exit),
    consistent with script-failure-policy — not silently print "Install
    complete." while a plugin never actually installed."""

    def test_cmd_install_tracks_and_exits_on_plugin_setup_failure(self):
        # Adversarial-review MAJOR finding: `assertIn("maybe_setup_caveman()"
        # ...)` alone is satisfied by a BARE (unwrapped, return-value-
        # ignoring) call too — replacing both wirings with plain
        # `maybe_setup_caveman()` / `setup_managed_plugins()` statements (so
        # `install_failed` can never become True) left this test green,
        # since every literal string it checked was still present somewhere
        # in the source. Anchor on the actual CONDITIONAL wiring.
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("if not maybe_setup_caveman():", src)
        self.assertIn("if not setup_managed_plugins():", src)
        self.assertIn("install_failed", src)
        self.assertIn("sys.exit(1)", src)


if __name__ == "__main__":
    main()
