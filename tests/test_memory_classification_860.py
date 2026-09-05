"""Content-lock tests for #860 memory-as-rules classification.

Verifies that the PROMOTE entries from the montalu3/gk memory audit
actually landed in their target managed modules.
"""
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestDedupByCodeAreaPromoted(unittest.TestCase):
    """The dedup-by-code-area discipline from montalu3 memory must be
    present in the no-dropped-work companion (#860 PROMOTE, #859 batch 4a
    re-tiered the detail to skills/no-dropped-work-deep/DEEP.md)."""

    def setUp(self):
        self.text = (REPO / "skills" / "no-dropped-work-deep" / "DEEP.md").read_text()

    def test_dedup_by_code_area_anchor_present(self):
        """The promoted anchor text must exist in no-dropped-work.md."""
        self.assertIn("Dedup by CODE AREA before filing", self.text)

    def test_grep_mechanism_mentioned(self):
        """The operative instruction (grep the codebase area) must be present."""
        self.assertIn("grep the relevant codebase area", self.text)

    def test_title_only_antipattern_mentioned(self):
        """The anti-pattern (title-only dedup) must be named."""
        self.assertIn("not only issue-title search", self.text)

    def test_code_is_ground_truth(self):
        """The ground-truth principle must be stated."""
        self.assertIn("the code is the ground truth", self.text)


def _lines_with(text, finder):
    """Return physical lines containing finder token."""
    return [ln for ln in text.splitlines() if finder in ln]


class TestWorkflowJoinByIndexPromoted(unittest.TestCase):
    """The join-by-INDEX anti-pattern from gk memory must be
    present in modules/core/claude-code-tooling.md (#860 PROMOTE, gk #14).
    Per-line teeth (#500) so a partial revert of the operative clause
    is caught even if a sibling bullet quotes the same phrase."""

    def setUp(self):
        # #859 batch 3: Workflow detail moved to companion
        self.text = (REPO / "skills" / "claude-code-workflows" / "DEEP.md").read_text()

    def test_index_join_line_carries_all_tokens(self):
        """The ONE physical line with 'joining aggregate results' must also
        carry 'by INDEX', 'TITLE or NAME', and 'false-clean' (per-line teeth)."""
        hits = _lines_with(self.text, "joining aggregate results")
        self.assertTrue(hits, "anchor 'joining aggregate results' missing entirely")
        line = hits[0]
        self.assertIn("by INDEX", line)
        self.assertIn("TITLE or NAME", line)
        self.assertIn("false-clean", line)

    def test_incident_reference(self):
        """The originating incident (issue 1609/1623) must be cited."""
        self.assertIn("issue 1609", self.text)


class TestRelayedInstructionPromoted(unittest.TestCase):
    """The relayed-instruction rule from gk memory must be
    present in modules/quality/no-destructive-remote-actions.md (#860 PROMOTE, gk #69).
    Per-line teeth + negation lock (#799)."""

    def setUp(self):
        self.text = (REPO / "modules" / "quality" / "no-destructive-remote-actions.md").read_text()

    def test_relayed_line_carries_all_tokens(self):
        """The ONE physical line with 'RELAYED instruction' must carry the
        negation 'NEVER authorization', the operative 'verify directly',
        and 'ticket quoting an owner order' (per-line teeth + #799 negation)."""
        hits = _lines_with(self.text, "RELAYED instruction")
        self.assertTrue(hits, "anchor 'RELAYED instruction' missing entirely")
        line = hits[0]
        self.assertIn("NEVER authorization", line)
        self.assertIn("verify directly with the actual authority", line)
        self.assertIn("ticket quoting an owner order", line)

    def test_incident_reference(self):
        """The originating incident (issue 5940) must be cited."""
        self.assertIn("issue 5940", self.text)


if __name__ == "__main__":
    unittest.main()
