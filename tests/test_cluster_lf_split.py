"""#433 cluster L-F 2-file split lock — cli_claude_scripts.py (File A, the
launcher + history-viewer SCRIPT templates + renderers) and
cli_bashrc_appliers.py (File B, the two idempotent ~/.bashrc appliers).

This was a byte-verbatim module extraction (no behavior change), so instead
of a RED->GREEN regression pair it ships a split-lock test with mutation
teeth. It pins the invariants that keep the extraction correct AND that a
future edit cannot silently break:

  1. `import airuleset` stays clean in a FRESH subprocess, and importing the
     SIBLING half FIRST (cli_bashrc_appliers, which forward-imports
     cli_claude_scripts) is also clean — a circular-import regression would
     blow up exactly here.
  2. The dependency DAG is one-directional (cli_bashrc_appliers ->
     cli_claude_scripts), never back, and NEITHER leaf has a MODULE-LEVEL
     `import airuleset` (the resident couplings are deferred to call time).
  3. Every one of the 18 moved names is the SAME object at `airuleset.<name>`
     and at its leaf — a dead `patch.object(airuleset, "<name>")` seam is
     exactly this bug (the L2 lesson).
  4. The deferred-import couplings are LIVE: patching the resident value on
     `airuleset` changes the moved function's behavior (a mutant reverting a
     deferred `airuleset.X` read to a frozen/direct copy is caught).
"""

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_bashrc_appliers  # noqa: E402
import cli_claude_scripts  # noqa: E402
import cli_tmux_provisioning  # noqa: E402

# The hand-maintained move checklist: the reviewer diffs this against the
# facade import block in airuleset.py. File A = templates, File B = appliers.
FILE_A_NAMES = [
    "CLAUDE_LAUNCH_SCRIPT_DEST",
    "CLAUDE_LAUNCH_SCRIPT_CONTENT",
    "render_claude_launch_script",
    "encode_project_dir",
    "CLAUDE_HISTORY_SCRIPT_DEST",
    "CLAUDE_HISTORY_SCRIPT_CONTENT",
    "render_claude_history_script",
    "CLAUDE_HISTORY_POPUP_SCRIPT_DEST",
    "CLAUDE_HISTORY_POPUP_SCRIPT_CONTENT",
    "render_claude_history_popup_script",
]
FILE_B_NAMES = [
    "ULTRACODE_BASHRC_BLOCK",
    "apply_ultracode_launcher",
    "STREAM_DEV_CWD_REL",
    "STREAM_SSH_ATTACH_MARK_START",
    "STREAM_SSH_ATTACH_MARK_END",
    "STREAM_SSH_ATTACH_BLOCK",
    "_stream_marker_block_spans",
    "apply_stream_ssh_attach",
]


def _module_level_imports(path):
    """Every name a module imports at MODULE level (top-of-file), as a set of
    the dotted roots — e.g. `from cli_claude_scripts import X` -> the module
    'cli_claude_scripts'; `import airuleset` -> 'airuleset'."""
    tree = ast.parse((REPO / path).read_text())
    mods = set()
    for node in tree.body:  # module level ONLY (not nested in a function body)
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


class TestFreshSubprocessImport(unittest.TestCase):
    def _run(self, code):
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])

    def test_import_airuleset_clean(self):
        self._run("import airuleset")

    def test_import_sibling_half_first_is_clean(self):
        # cli_bashrc_appliers forward-imports cli_claude_scripts; importing it
        # FIRST (before airuleset) must not deadlock or fail on a cycle.
        self._run("import cli_bashrc_appliers; import cli_claude_scripts; import airuleset")

    def test_base_half_standalone(self):
        self._run("import cli_claude_scripts as a; assert a.TMUX_HISTORY_LIMIT")

    def test_import_watchdog_clean(self):
        self._run("import watchdog")


class TestDependencyDag(unittest.TestCase):
    def test_file_b_forward_imports_file_a(self):
        self.assertIn("cli_claude_scripts", _module_level_imports("cli_bashrc_appliers.py"))

    def test_file_a_does_not_import_file_b(self):
        # no back edge -> no cycle
        self.assertNotIn("cli_bashrc_appliers", _module_level_imports("cli_claude_scripts.py"))

    def test_neither_leaf_imports_airuleset_at_module_level(self):
        # the resident couplings (MANAGED_MODEL / BASHRC / AUTHORITY_BY_USER /
        # _current_user) are deferred to call time; a module-level airuleset
        # import is the import-cycle hazard this split deliberately avoids.
        self.assertNotIn("airuleset", _module_level_imports("cli_claude_scripts.py"))
        self.assertNotIn("airuleset", _module_level_imports("cli_bashrc_appliers.py"))

    def test_history_popup_limit_sourced_from_tmux_provisioning(self):
        # "keep that direction": TMUX_HISTORY_LIMIT flows history-viewer ->
        # cli_tmux_provisioning, never inverted (leaf owns it, not File A).
        self.assertIn("cli_tmux_provisioning", _module_level_imports("cli_claude_scripts.py"))
        self.assertIs(cli_claude_scripts.TMUX_HISTORY_LIMIT,
                      cli_tmux_provisioning.TMUX_HISTORY_LIMIT)


class TestFacadeIdentity(unittest.TestCase):
    def test_file_a_names_are_same_object(self):
        for n in FILE_A_NAMES:
            self.assertIs(getattr(airuleset, n), getattr(cli_claude_scripts, n),
                          "airuleset.%s is not cli_claude_scripts.%s" % (n, n))

    def test_file_b_names_are_same_object(self):
        for n in FILE_B_NAMES:
            self.assertIs(getattr(airuleset, n), getattr(cli_bashrc_appliers, n),
                          "airuleset.%s is not cli_bashrc_appliers.%s" % (n, n))

    def test_ultracode_marks_dup_equals_resident(self):
        # File B keeps its own byte-identical copy (needed at import time by
        # ULTRACODE_BASHRC_BLOCK); the resident copy stays for tests. Equal.
        self.assertEqual(airuleset.ULTRACODE_MARK_START,
                         cli_bashrc_appliers.ULTRACODE_MARK_START)
        self.assertEqual(airuleset.ULTRACODE_MARK_END,
                         cli_bashrc_appliers.ULTRACODE_MARK_END)


class TestDeferredCouplingsAreLive(unittest.TestCase):
    """Mutation teeth: patch the RESIDENT value on airuleset and prove the
    moved function reads it at call time. A mutant that reverts a deferred
    `airuleset.X` read to a frozen module-level copy fails these."""

    def test_render_launch_reflects_patched_managed_model(self):
        import unittest.mock as m
        with m.patch.object(airuleset, "MANAGED_MODEL", "ZZTOP-sentinel-model"):
            out = airuleset.render_claude_launch_script()
        self.assertIn("ZZTOP-sentinel-model", out)
        self.assertNotIn("{{MANAGED_MODEL}}", out)

    def test_stream_attach_uses_patched_current_user(self):
        import unittest.mock as m
        bp = Path(tempfile.mkdtemp()) / ".bashrc"
        bp.write_text("# x\n")
        # a non-stream user via _current_user -> no block added
        with m.patch.object(airuleset, "_current_user", lambda: "newlevel"):
            changed = airuleset.apply_stream_ssh_attach(bp)  # user=None -> _current_user()
        self.assertFalse(changed)
        self.assertNotIn(airuleset.STREAM_SSH_ATTACH_MARK_START, bp.read_text())

    def test_stream_attach_honors_patched_authority_registry(self):
        import unittest.mock as m
        bp = Path(tempfile.mkdtemp()) / ".bashrc"
        bp.write_text("# x\n")
        # a fake user made a stream account ONLY via the patched registry:
        # proves the deferred `airuleset.AUTHORITY_BY_USER` read is live.
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", {"faux-stream-acct": "branch-merge"}):
            changed = airuleset.apply_stream_ssh_attach(bp, user="faux-stream-acct")
        self.assertTrue(changed)
        self.assertIn(airuleset.STREAM_SSH_ATTACH_MARK_START, bp.read_text())

    def test_ultracode_launcher_writes_exact_renderer_output(self):
        # a mutant swapping which renderer feeds which path is caught: each
        # written script must byte-match its OWN renderer.
        d = Path(tempfile.mkdtemp())
        bp = d / ".bashrc"
        s = d / ".claude" / "l.sh"
        h = d / ".claude" / "h.py"
        pp = d / ".claude" / "pp.sh"
        airuleset.apply_ultracode_launcher(bp, s, h, pp)
        self.assertEqual(s.read_text(), airuleset.render_claude_launch_script())
        self.assertEqual(h.read_text(), airuleset.render_claude_history_script())
        self.assertEqual(pp.read_text(), airuleset.render_claude_history_popup_script())


if __name__ == "__main__":
    unittest.main()
