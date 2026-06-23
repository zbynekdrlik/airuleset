"""Tests for preserving externally-managed CLAUDE.md blocks (e.g. CodeGraph).

airuleset fully regenerates ~/.claude/CLAUDE.md from the profile. A tool like
`codegraph install` appends a delimited guidance block to the same file; without
preservation a `push` would silently wipe it. These tests lock the coexistence.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

CG = (
    "<!-- CODEGRAPH_START -->\n"
    "## CodeGraph\n\nUse codegraph_explore first.\n"
    "<!-- CODEGRAPH_END -->"
)


class TestPreserveExternalBlocks(TestCase):
    def test_reattaches_codegraph_block(self):
        old = "# old\n\n" + CG + "\n"
        new = "# fresh airuleset content\n"
        out = airuleset.preserve_external_blocks(old, new)
        self.assertIn("<!-- CODEGRAPH_START -->", out)
        self.assertIn("<!-- CODEGRAPH_END -->", out)
        self.assertIn("codegraph_explore", out)
        self.assertTrue(out.startswith("# fresh airuleset content"))

    def test_noop_when_old_has_no_block(self):
        new = "# fresh\n"
        self.assertEqual(airuleset.preserve_external_blocks("# plain old\n", new), new)

    def test_does_not_duplicate_when_already_present(self):
        old = CG + "\n"
        new = "# fresh\n\n" + CG + "\n"
        out = airuleset.preserve_external_blocks(old, new)
        self.assertEqual(out.count("<!-- CODEGRAPH_START -->"), 1)

    def test_ignores_truncated_block(self):
        # start marker but no end -> nothing intact to preserve
        old = "<!-- CODEGRAPH_START -->\n## CodeGraph\n(no end)\n"
        new = "# fresh\n"
        self.assertEqual(airuleset.preserve_external_blocks(old, new), new)

    def test_idempotent(self):
        old = "# old\n\n" + CG + "\n"
        once = airuleset.preserve_external_blocks(old, "# fresh\n")
        twice = airuleset.preserve_external_blocks(old, once)
        self.assertEqual(once.count("<!-- CODEGRAPH_START -->"), 1)
        self.assertEqual(twice.count("<!-- CODEGRAPH_START -->"), 1)


if __name__ == "__main__":
    main()
