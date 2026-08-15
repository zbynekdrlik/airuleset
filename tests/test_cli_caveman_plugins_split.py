"""#433 cluster L-C split test — cli_caveman_plugins.py (caveman plugin wiring +
managed baseline plugin provisioning) extracted from airuleset.py.

A pure-move split has no behaviour change, so instead of RED->GREEN this locks
the SPLIT's own invariants with mutation teeth:

  1. the leaf imports self-contained (fresh subprocess, WITHOUT airuleset in
     sys.modules) — proves zero module-level `import airuleset`;
  2. airuleset.X IS cli_caveman_plugins.X for all 19 re-exported names (facade
     identity) + `_claude_cli_env` IS cli_binary_installers' (direct leaf import);
  3. reconcile_caveman_settings + CAVEMAN_MODE_FILE stayed RESIDENT (the two
     deliberate non-moves — default-arg anchor / CLAUDE_DIR-derived+test-patched);
  4. the deferred `airuleset.X` back-refs genuinely read airuleset's live value
     (patch airuleset.CLAUDE_DIR / airuleset.MARKETPLACE_SOURCES -> the moved fn
     reflects it);
  5. the moved constants are LEAF-local: a `cli_caveman_plugins.<CONST>` patch
     BITES the moved reader while an `airuleset.<CONST>` (facade) patch is a
     measurable NO-OP — which is exactly why test_managed_plugins.py's patches
     were repointed to the leaf (K #1482 seam).
"""

import json
import subprocess
import sys
import tempfile
import unittest.mock as m
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_binary_installers
import cli_caveman_plugins as leaf

REPO = Path(__file__).resolve().parent.parent


REEXPORTED = [
    "caveman_mode_or_default", "_caveman_plugin_built", "setup_caveman",
    "maybe_setup_caveman", "reconcile_managed_plugins", "_plugin_registry_keys",
    "_managed_plugin_built", "_playwright_browsers_installed",
    "ensure_playwright_browsers", "_reconcile_settings_file", "setup_managed_plugins",
    "CAVEMAN_PLUGIN_KEY", "CAVEMAN_DEFAULT_MODE", "VALID_CAVEMAN_MODES",
    "MANAGED_PLUGINS", "OPTIONAL_PLUGINS", "MANAGED_DISABLED_PLUGINS",
    "PLAYWRIGHT_PLUGIN_KEY", "PLAYWRIGHT_BROWSER_CACHE",
]


class TestLeafSelfContained(TestCase):
    def test_imports_without_airuleset_in_a_fresh_process(self):
        # A module-level `import airuleset` in the leaf would (a) re-execute the
        # CLI as __main__ and (b) show up here as airuleset in sys.modules.
        code = (
            "import sys; sys.path.insert(0, %r); import cli_caveman_plugins; "
            "assert 'airuleset' not in sys.modules, sorted(sys.modules); "
            "print('OK', cli_caveman_plugins.CAVEMAN_PLUGIN_KEY)" % str(REPO)
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK caveman@caveman", r.stdout)

    def test_leaf_module_level_has_no_import_airuleset(self):
        import ast
        tree = ast.parse((REPO / "cli_caveman_plugins.py").read_text())
        for node in tree.body:  # module top-level only
            if isinstance(node, ast.Import):
                self.assertNotIn("airuleset", [a.name for a in node.names])
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "airuleset")


class TestFacadeIdentity(TestCase):
    def test_every_reexported_name_is_the_leaf_object(self):
        bad = [n for n in REEXPORTED if getattr(airuleset, n) is not getattr(leaf, n)]
        self.assertEqual(bad, [], "facade re-export identity broken for: %s" % bad)

    def test_claude_cli_env_is_the_shipped_leaf_object(self):
        # direct `from cli_binary_installers import _claude_cli_env` — keeps
        # `env=_claude_cli_env()` byte-verbatim (a source-text assert relies on it)
        self.assertIs(leaf._claude_cli_env, cli_binary_installers._claude_cli_env)

    def test_deliberate_non_moves_stayed_resident(self):
        # reconcile_caveman_settings: default-arg anchored to resident
        # CAVEMAN_STATUSLINE_COMMAND; CAVEMAN_MODE_FILE: CLAUDE_DIR-derived +
        # directly test-patched -> both stay in airuleset, NOT in the leaf.
        self.assertTrue(hasattr(airuleset, "reconcile_caveman_settings"))
        self.assertFalse(hasattr(leaf, "reconcile_caveman_settings"))
        self.assertTrue(hasattr(airuleset, "CAVEMAN_MODE_FILE"))
        self.assertFalse(hasattr(leaf, "CAVEMAN_MODE_FILE"))


class TestDeferredBackRefsReadAiruleset(TestCase):
    def test_plugin_registry_keys_reads_airuleset_CLAUDE_DIR(self):
        d = Path(tempfile.mkdtemp())
        (d / "plugins").mkdir()
        (d / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": {"x@y": [{"scope": "user"}]}}))
        with m.patch.object(airuleset, "CLAUDE_DIR", d):
            keys = leaf._plugin_registry_keys()  # default path derives from CLAUDE_DIR
        self.assertEqual(keys, {"x@y"},
                         "moved _plugin_registry_keys must read airuleset.CLAUDE_DIR")

    def test_reconcile_managed_plugins_reads_airuleset_MARKETPLACE_SOURCES(self):
        # patch the L-A-resident MARKETPLACE_SOURCES with a sentinel repo for the
        # real superpowers marketplace name; the moved fn must reflect it.
        mkt = airuleset._marketplace_names_for(leaf.MANAGED_PLUGINS)
        name = next(iter(mkt))
        with m.patch.object(airuleset, "MARKETPLACE_SOURCES", {name: "sentinel/repo"}):
            out = leaf.reconcile_managed_plugins({})
        self.assertEqual(
            out["extraKnownMarketplaces"][name]["source"]["repo"], "sentinel/repo",
            "moved reconcile_managed_plugins must read airuleset.MARKETPLACE_SOURCES")


class TestMovedConstantsAreLeafLocal(TestCase):
    """The repoint mandate: patch the LEAF, not the facade. Proven with teeth."""

    def test_MANAGED_PLUGINS_leaf_patch_bites_facade_patch_is_noop(self):
        # leaf patch BITES
        with m.patch.object(leaf, "MANAGED_PLUGINS", ("sentinel@mkt",)):
            out = leaf.reconcile_managed_plugins({})
        self.assertTrue(out["enabledPlugins"].get("sentinel@mkt"),
                        "a cli_caveman_plugins.MANAGED_PLUGINS patch must bite")
        # facade (airuleset) patch is a NO-OP for the moved reader
        with m.patch.object(airuleset, "MANAGED_PLUGINS", ("facade@mkt",)):
            out = leaf.reconcile_managed_plugins({})
        self.assertNotIn("facade@mkt", out["enabledPlugins"],
                         "an airuleset.MANAGED_PLUGINS patch must NOT reach the leaf")

    def test_PLAYWRIGHT_BROWSER_CACHE_leaf_patch_bites_facade_patch_is_noop(self):
        populated = Path(tempfile.mkdtemp())
        (populated / "chromium-1").mkdir()
        empty = Path(tempfile.mkdtemp())
        # leaf patch BITES (default cache_dir=None -> leaf PLAYWRIGHT_BROWSER_CACHE)
        with m.patch.object(leaf, "PLAYWRIGHT_BROWSER_CACHE", populated):
            self.assertTrue(leaf._playwright_browsers_installed())
        with m.patch.object(leaf, "PLAYWRIGHT_BROWSER_CACHE", empty):
            self.assertFalse(leaf._playwright_browsers_installed())
        # facade patch is a NO-OP: leaf still reads its own real (empty) cache
        with m.patch.object(airuleset, "PLAYWRIGHT_BROWSER_CACHE", populated), \
                m.patch.object(leaf, "PLAYWRIGHT_BROWSER_CACHE", empty):
            self.assertFalse(leaf._playwright_browsers_installed(),
                             "an airuleset.PLAYWRIGHT_BROWSER_CACHE patch must NOT reach the leaf")


if __name__ == "__main__":
    from unittest import main
    main()
