"""#630 — the push-GATE section was extracted VERBATIM from cli_remote.py into
the self-contained leaf cli_push_gate.py. This locks the extraction SEAM:

- the nine moved symbols now live in cli_push_gate and are re-exported through
  cli_remote at the old definition site (facade IDENTITY — so `cli_remote._X`
  and the two suites that go through it keep working), and
- cli_push_gate is a pure stdlib-only leaf (no `import airuleset`).

The behaviour-IDENTICAL characterization proof is the existing #548 + #629
suites (`test_tmp_litter_548.py`, `test_gate_tree_moved_629.py`) staying green
through the facade — this file adds the seam-identity check those suites, going
through the facade, cannot see: that a future edit which REDEFINES a name in
cli_remote (instead of re-exporting it) would break the single-source contract.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_push_gate  # noqa: E402
import cli_remote  # noqa: E402

MOVED_NAMES = (
    "PUSH_TMPDIR_LITTER_CAP",
    "_effective_push_tmpdir_cap",
    "_check_push_tmpdir_litter",
    "TREE_MOVED_CHANGED_FILES_SHOWN",
    "_tracked_tree_fingerprint",
    "_diff_tracked_tree_fingerprints",
    "_fp_unavailable_reason",
    "_render_tree_moved_report",
    "_classify_push_gate_outcome",
)


class TestPushGateExtractionSeam630(unittest.TestCase):
    def test_every_moved_name_is_re_exported_with_facade_identity(self):
        """cli_remote re-exports the SAME object cli_push_gate defines — never a
        second, drifting copy. This is the single-source contract that lets the
        #548/#629 suites keep calling cli_remote._X unchanged."""
        for name in MOVED_NAMES:
            self.assertTrue(hasattr(cli_push_gate, name),
                            "cli_push_gate is missing " + name)
            self.assertTrue(hasattr(cli_remote, name),
                            "cli_remote no longer re-exports " + name)
            self.assertIs(getattr(cli_remote, name), getattr(cli_push_gate, name),
                          "cli_remote.%s is not cli_push_gate.%s (facade drift)"
                          % (name, name))

    def test_moved_functions_now_live_in_cli_push_gate(self):
        """The definitions genuinely MOVED — a moved function's home module is
        cli_push_gate, so the split is real, not a copy left behind."""
        for name in MOVED_NAMES:
            obj = getattr(cli_push_gate, name)
            if inspect.isfunction(obj):
                self.assertEqual(obj.__module__, "cli_push_gate",
                                 "%s did not move to cli_push_gate" % name)

    def test_cli_push_gate_is_a_pure_leaf_no_airuleset_import(self):
        """The gate section is pure (repo dir arg + git subprocess) — the leaf
        must never couple back to the airuleset facade. Checked as an AST
        IMPORT statement, never a bare substring: the module docstring itself
        names `import airuleset` to explain its own absence (#113 self-prose
        trap), so a substring assert would match the very sentence documenting
        the invariant."""
        import ast
        tree = ast.parse(Path(cli_push_gate.__file__).read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertNotIn("airuleset", imported,
                         "cli_push_gate must stay a pure leaf: %r" % imported)

    def test_behaviour_identical_through_both_paths(self):
        """Characterization: the classifier returns byte-identical results
        whether reached through the leaf or the cli_remote facade — the split
        changed structure, never behaviour."""
        # clean verdict (git available, no mutation)
        for mod in (cli_push_gate, cli_remote):
            ok, reason, msg = mod._classify_push_gate_outcome(0, None, None)
            self.assertEqual((ok, reason), (True, "clean"))
        # a genuine test failure still surfaces
        ok, reason, _ = cli_remote._classify_push_gate_outcome(1, None, None)
        self.assertEqual((ok, reason), (False, "tests-failed"))
        # litter guard passes on a missing dir (never raises)
        self.assertEqual(cli_remote._check_push_tmpdir_litter("/nonexistent/x"),
                         cli_push_gate._check_push_tmpdir_litter("/nonexistent/x"))

    def test_cmd_push_still_resolves_the_gate_calls_after_the_move(self):
        """cmd_push STAYED in cli_remote and still wires the (now re-exported)
        gate helpers by bare name — the source-lock precondition every
        getsource(cmd_push) test depends on."""
        src = inspect.getsource(cli_remote.cmd_push)
        for tok in ("_tracked_tree_fingerprint(", "_classify_push_gate_outcome(",
                    "_check_push_tmpdir_litter", "_effective_push_tmpdir_cap()"):
            self.assertIn(tok, src)


if __name__ == "__main__":
    unittest.main()
