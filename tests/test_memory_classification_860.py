"""Content-lock tests for #860 memory-as-rules classification.

Verifies that the PROMOTE entries from the montalu3/gk memory audit
actually landed in their target managed modules.
"""
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestDedupByCodeAreaPromoted(unittest.TestCase):
    """The dedup-by-code-area discipline from montalu3 memory must be
    present in modules/quality/no-dropped-work.md (#860 PROMOTE)."""

    def setUp(self):
        self.text = (REPO / "modules" / "quality" / "no-dropped-work.md").read_text()

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


class TestWorkflowJoinByIndexPromoted(unittest.TestCase):
    """The join-by-INDEX anti-pattern from gk memory must be
    present in modules/core/claude-code-tooling.md (#860 PROMOTE, gk #14)."""

    def setUp(self):
        self.text = (REPO / "modules" / "core" / "claude-code-tooling.md").read_text()

    def test_index_join_antipattern_present(self):
        """The promoted anti-pattern text must exist in claude-code-tooling.md."""
        self.assertIn("joining aggregate results", self.text)
        self.assertIn("by INDEX", self.text)

    def test_title_join_named_as_wrong(self):
        """The wrong approach (title-join) must be named."""
        self.assertIn("TITLE or NAME", self.text)

    def test_incident_cited(self):
        """The originating incident must be cited."""
        self.assertIn("false-clean verdicts", self.text)


class TestRelayedInstructionPromoted(unittest.TestCase):
    """The relayed-instruction rule from gk memory must be
    present in modules/quality/no-destructive-remote-actions.md (#860 PROMOTE, gk #69)."""

    def setUp(self):
        self.text = (REPO / "modules" / "quality" / "no-destructive-remote-actions.md").read_text()

    def test_relayed_instruction_anchor_present(self):
        """The promoted anchor text must exist in no-destructive-remote-actions.md."""
        self.assertIn("RELAYED instruction", self.text)

    def test_verify_directly_mentioned(self):
        """The operative instruction (verify directly) must be present."""
        self.assertIn("verify directly with the actual authority", self.text)

    def test_ticket_quote_named(self):
        """The specific anti-pattern (ticket quoting an owner order) must be named."""
        self.assertIn("ticket quoting an owner order", self.text)


if __name__ == "__main__":
    unittest.main()
