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


if __name__ == "__main__":
    unittest.main()
