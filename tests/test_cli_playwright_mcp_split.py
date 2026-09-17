"""#1058 split test — cli_playwright_mcp.py (managed Playwright MCP provisioning)
extracted from cli_caveman_plugins.py (area-review rework of #1048).

A pure-move split has no behaviour change, so this locks the SPLIT's own
invariants with mutation teeth (the sibling of tests/test_cli_caveman_plugins_split.py
for the #433 split):

  1. the leaf imports self-contained (fresh subprocess, WITHOUT airuleset in
     sys.modules) — proves zero module-level `import airuleset`;
  2. no module-level `import airuleset` (AST), same as the caveman leaf;
  3. facade identity: airuleset.X IS cli_caveman_plugins.X IS cli_playwright_mcp.X
     for every moved name (both the direct leaf AND the chained cli_caveman_plugins
     re-export must preserve identity, so `airuleset.PLAYWRIGHT_*` and
     `cli_caveman_plugins.PLAYWRIGHT_*` keep working);
  4. the moved constants are LEAF-local: a `cli_playwright_mcp.<CONST>` patch
     BITES the moved reader while a `cli_caveman_plugins.<CONST>` (chained facade)
     patch is a measurable NO-OP — which is why test_managed_plugins.py /
     test_playwright_mcp_1048.py's patches were repointed to cli_playwright_mcp.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
import unittest.mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_caveman_plugins as caveman
import cli_playwright_mcp as leaf

REPO = Path(__file__).resolve().parent.parent


# Every name moved into the leaf. cli_caveman_plugins re-exports ALL of them (so
# the `cli_caveman_plugins.PLAYWRIGHT_*` surface the test suite patches is
# unchanged); airuleset re-exports the same CURATED subset it always did (its
# facade import list — never widened by this split).
MOVED = [
    "PLAYWRIGHT_PLUGIN_KEY", "PLAYWRIGHT_BROWSER_CACHE", "PLAYWRIGHT_MANAGED",
    "PLAYWRIGHT_MCP_VERSION", "PLAYWRIGHT_PW_VERSION", "PLAYWRIGHT_CHROMIUM_BUILD",
    "PLAYWRIGHT_MCP_SERVER_NAME", "OPT_MS_PLAYWRIGHT",
    "PLAYWRIGHT_BROWSERS_PATH_MARKER", "PLAYWRIGHT_OPTOUT_MARKER",
    "_has_pinned_chromium_build", "_opt_has_pinned_build",
    "_playwright_pinned_build_installed", "resolve_playwright_browsers_path",
    "_current_box_class", "resolved_browsers_path", "render_playwright_mcp_server",
    "reconcile_playwright_mcp_server", "unreconcile_playwright_mcp_server",
    "_playwright_browsers_installed", "_is_per_user_cache", "_stderr_tail",
    "_sudo_n_available", "_headless_shell_binary", "_probe_headless_shell_rc",
    "_heal_system_libs", "ensure_playwright_browsers", "_atomic_write_text",
    "reconcile_playwright_mcp_file", "unprovision_playwright_mcp_file",
    "provision_playwright_mcp",
]

# The subset airuleset.py's facade import block re-exports (unchanged by #1058).
AIRULESET_FACADE = [
    "PLAYWRIGHT_PLUGIN_KEY", "PLAYWRIGHT_BROWSER_CACHE", "PLAYWRIGHT_MANAGED",
    "PLAYWRIGHT_MCP_VERSION", "PLAYWRIGHT_PW_VERSION", "PLAYWRIGHT_CHROMIUM_BUILD",
    "PLAYWRIGHT_MCP_SERVER_NAME", "OPT_MS_PLAYWRIGHT",
    "PLAYWRIGHT_BROWSERS_PATH_MARKER", "_opt_has_pinned_build",
    "resolve_playwright_browsers_path", "_current_box_class",
    "resolved_browsers_path", "render_playwright_mcp_server",
    "reconcile_playwright_mcp_server", "reconcile_playwright_mcp_file",
    "provision_playwright_mcp", "_playwright_browsers_installed",
    "ensure_playwright_browsers",
]


class TestLeafSelfContained(TestCase):
    def test_imports_without_airuleset_in_a_fresh_process(self):
        # A module-level `import airuleset` in the leaf would (a) re-execute the
        # CLI as __main__ and (b) show up here as airuleset in sys.modules.
        code = (
            "import sys; sys.path.insert(0, %r); import cli_playwright_mcp; "
            "assert 'airuleset' not in sys.modules, sorted(sys.modules); "
            "print('OK', cli_playwright_mcp.PLAYWRIGHT_MCP_VERSION)" % str(REPO)
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK 0.0.81", r.stdout)

    def test_leaf_module_level_has_no_import_airuleset(self):
        import ast
        tree = ast.parse((REPO / "cli_playwright_mcp.py").read_text())
        for node in tree.body:  # module top-level only
            if isinstance(node, ast.Import):
                self.assertNotIn("airuleset", [a.name for a in node.names])
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "airuleset")


class TestFacadeIdentity(TestCase):
    def test_caveman_reexports_every_moved_name_as_the_leaf_object(self):
        bad = [n for n in MOVED if getattr(caveman, n) is not getattr(leaf, n)]
        self.assertEqual(bad, [],
                         "cli_caveman_plugins re-export identity broken for: %s" % bad)

    def test_airuleset_facade_subset_is_the_same_object_across_all_three(self):
        bad = []
        for n in AIRULESET_FACADE:
            a = getattr(airuleset, n)
            c = getattr(caveman, n)
            p = getattr(leaf, n)
            if not (a is c is p):
                bad.append((n, a is c, c is p))
        self.assertEqual(bad, [], "airuleset facade identity broken for: %s" % bad)

    def test_caveman_no_longer_defines_playwright_in_its_own_source(self):
        # The move must be REAL: cli_caveman_plugins.py's source has no local
        # `def <playwright fn>` / `PLAYWRIGHT_* =` assignment any more — every
        # such name resolves only through the re-export from cli_playwright_mcp.
        src = (REPO / "cli_caveman_plugins.py").read_text()
        self.assertNotIn("def resolve_playwright_browsers_path(", src)
        self.assertNotIn("def ensure_playwright_browsers(", src)
        self.assertNotIn("def provision_playwright_mcp(", src)
        # It DOES carry the facade re-export line.
        self.assertIn("from cli_playwright_mcp import (", src)


class TestMovedConstantsAreLeafLocal(TestCase):
    """The repoint mandate: patch the LEAF (cli_playwright_mcp), not the facade."""

    def test_PLAYWRIGHT_BROWSER_CACHE_leaf_patch_bites_facade_patch_is_noop(self):
        populated = Path(tempfile.mkdtemp())
        (populated / "chromium-1").mkdir()
        empty = Path(tempfile.mkdtemp())
        # leaf patch BITES (default cache_dir=None -> leaf PLAYWRIGHT_BROWSER_CACHE)
        with m.patch.object(leaf, "PLAYWRIGHT_BROWSER_CACHE", populated):
            self.assertTrue(leaf._playwright_browsers_installed())
        with m.patch.object(leaf, "PLAYWRIGHT_BROWSER_CACHE", empty):
            self.assertFalse(leaf._playwright_browsers_installed())
        # a chained-facade (cli_caveman_plugins) patch is a NO-OP: the reader
        # lives in the leaf and reads its OWN (real, empty) module global.
        with m.patch.object(caveman, "PLAYWRIGHT_BROWSER_CACHE", populated), \
                m.patch.object(leaf, "PLAYWRIGHT_BROWSER_CACHE", empty):
            self.assertFalse(
                leaf._playwright_browsers_installed(),
                "a cli_caveman_plugins.PLAYWRIGHT_BROWSER_CACHE patch must NOT reach the leaf")

    def test_PLAYWRIGHT_MANAGED_leaf_patch_bites_facade_patch_is_noop(self):
        # leaf patch False BITES: reconcile_playwright_mcp_server skips the server
        with m.patch.object(leaf, "PLAYWRIGHT_MANAGED", False):
            out = leaf.reconcile_playwright_mcp_server({"mcpServers": {}}, "/c")
        self.assertNotIn("playwright", out["mcpServers"],
                         "a cli_playwright_mcp.PLAYWRIGHT_MANAGED patch must bite")
        # chained-facade patch is a NO-OP: the leaf still reads its own True
        with m.patch.object(caveman, "PLAYWRIGHT_MANAGED", False):
            out = leaf.reconcile_playwright_mcp_server({"mcpServers": {}}, "/c")
        self.assertIn("playwright", out["mcpServers"],
                      "a cli_caveman_plugins.PLAYWRIGHT_MANAGED patch must NOT reach the leaf")


if __name__ == "__main__":
    from unittest import main
    main()
