"""#1061 item 4 -- the hand-off composer carries a `Reviewed-by: <role> <model>`
line (transcript-derived, the SAME way design-record stamps Design-by:), the
review-of-record fact. Optional: emitted only when the CLI supplies it, so no
existing caller/test changes shape.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cli_handoff_template as ht  # noqa: E402

REVIEW_TABLE = (
    "| lens | verdict | evidence |\n"
    "|---|---|---|\n"
    "| security | 0 red | foo.py:10 |\n")


def _extended(**kw):
    base = dict(
        branch_field="dev", head_sha="abc123",
        verified_at_utc="2026-09-17T00:00:00Z", stack="python",
        harness="pytest", shared_benefit="shared -- fleet governance",
        self_review_table=REVIEW_TABLE, bounce_round=1,
        self_review_model="claude-opus-4-8")
    base.update(kw)
    return ht.render_extended_body(**base)


def _generic(**kw):
    base = dict(
        branch="dev", head_sha="abc123",
        verified_at_utc="2026-09-17T00:00:00Z",
        self_review_table=REVIEW_TABLE, bounce_round=1,
        self_review_model="claude-opus-4-8")
    base.update(kw)
    return ht.render_generic_body(**base)


class TestExtendedReviewedBy(unittest.TestCase):
    def test_emits_reviewed_by_when_given(self):
        body = _extended(reviewed_by="main claude-fable-5-1")
        self.assertIn("Reviewed-by: main claude-fable-5-1", body)

    def test_absent_when_not_given(self):
        self.assertNotIn("Reviewed-by:", _extended())

    def test_worker_value_recorded_truthfully(self):
        body = _extended(reviewed_by="worker claude-opus-4-8")
        self.assertIn("Reviewed-by: worker claude-opus-4-8", body)


class TestGenericReviewedBy(unittest.TestCase):
    def test_emits_reviewed_by_when_given(self):
        self.assertIn("Reviewed-by: main claude-fable-5-1",
                      _generic(reviewed_by="main claude-fable-5-1"))

    def test_absent_when_not_given(self):
        self.assertNotIn("Reviewed-by:", _generic())


class TestComposeBodyThreadsReviewedBy(unittest.TestCase):
    def test_compose_extended_threads_reviewed_by(self):
        import unittest.mock as m
        with m.patch("cli_handoff_template.has_extended_template",
                     return_value=True):
            body, err = ht.compose_body(
                repo="zbynekdrlik/odoo-erp", branch="dev", head_sha="abc123",
                verified_at_utc="2026-09-17T00:00:00Z",
                self_review_table=REVIEW_TABLE, bounce_round=1,
                stack="python", harness="pytest",
                shared_benefit="shared -- fleet governance",
                self_review_model="claude-opus-4-8",
                reviewed_by="main claude-fable-5-1")
        self.assertIsNone(err, err)
        self.assertIn("Reviewed-by: main claude-fable-5-1", body)

    def test_compose_generic_threads_reviewed_by(self):
        import unittest.mock as m
        with m.patch("cli_handoff_template.has_extended_template",
                     return_value=False):
            body, err = ht.compose_body(
                repo="zbynekdrlik/some-repo", branch="dev", head_sha="abc123",
                verified_at_utc="2026-09-17T00:00:00Z",
                self_review_table=REVIEW_TABLE, bounce_round=1,
                self_review_model="claude-opus-4-8",
                reviewed_by="worker claude-opus-4-8")
        self.assertIsNone(err, err)
        self.assertIn("Reviewed-by: worker claude-opus-4-8", body)


if __name__ == "__main__":
    unittest.main()
