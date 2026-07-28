"""Locks scripts/measure_design_compliance.py's pure logic (#136, Deliverable 1).

This is the corpus-measurement script: for a closed issue, was there a `gh
issue comment` that classifies as root-cause+approach+alternative
(design_gate.classify_design_comment -- the EXACT SAME classifier the
enforcement hooks use, so the measured baseline and the gate are provably
the same yardstick) POSTED BEFORE the first commit referencing that issue?

Only the PURE decision functions are locked here (no network/subprocess) --
`evaluate_issue` (per-issue verdict) and `summarize` (aggregate + before/
after split around the 95dbc9b cutover). The gh/git I/O wrappers are thin
and exercised live when the script actually runs against real repos.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import measure_design_compliance as m                      # noqa: E402

GOOD_BODY = (
    "Root cause: the retry loop never reset its backoff counter after a "
    "successful call. Chosen approach: reset the counter on the first "
    "success after any failure. Rejected alternative: replacing the whole "
    "backoff strategy with a token bucket -- too big a change for this bug."
)
BAD_BODY = "still looking into this, will update soon"


class TestEvaluateIssue(unittest.TestCase):

    def test_compliant_comment_before_first_commit(self):
        comments = [{"body": GOOD_BODY, "createdAt": "2026-07-10T10:00:00Z"}]
        r = m.evaluate_issue(comments, "2026-07-10T11:00:00Z")
        self.assertTrue(r["compliant"], r["reason"])
        self.assertEqual(r["reason"], "ok")

    def test_comment_after_first_commit_is_not_compliant(self):
        comments = [{"body": GOOD_BODY, "createdAt": "2026-07-10T12:00:00Z"}]
        r = m.evaluate_issue(comments, "2026-07-10T11:00:00Z")
        self.assertFalse(r["compliant"])
        self.assertIn("after", r["reason"])

    def test_no_qualifying_comment_at_all(self):
        comments = [{"body": BAD_BODY, "createdAt": "2026-07-10T09:00:00Z"}]
        r = m.evaluate_issue(comments, "2026-07-10T11:00:00Z")
        self.assertFalse(r["compliant"])
        self.assertIn("no comment", r["reason"].lower())

    def test_no_comments_at_all(self):
        r = m.evaluate_issue([], "2026-07-10T11:00:00Z")
        self.assertFalse(r["compliant"])

    def test_unknown_first_commit_is_content_ok_order_unknown(self):
        comments = [{"body": GOOD_BODY, "createdAt": "2026-07-10T10:00:00Z"}]
        r = m.evaluate_issue(comments, None)
        self.assertFalse(r["compliant"])
        self.assertIn("order unknown", r["reason"].lower())

    def test_earliest_qualifying_comment_is_the_one_that_counts(self):
        comments = [
            {"body": GOOD_BODY, "createdAt": "2026-07-10T12:00:00Z"},  # after
            {"body": GOOD_BODY, "createdAt": "2026-07-10T09:00:00Z"},  # before
        ]
        r = m.evaluate_issue(comments, "2026-07-10T11:00:00Z")
        self.assertTrue(r["compliant"], r["reason"])

    def test_malformed_comment_entries_are_skipped_not_fatal(self):
        comments = [None, {"no_body_key": True}, {"body": GOOD_BODY,
                    "createdAt": "2026-07-10T10:00:00Z"}]
        r = m.evaluate_issue(comments, "2026-07-10T11:00:00Z")
        self.assertTrue(r["compliant"], r["reason"])


class TestSummarize(unittest.TestCase):

    CUTOVER = "2026-07-27T16:00:22+02:00"

    def _row(self, repo, issue, compliant, first_commit_iso):
        return {"repo": repo, "issue": issue, "compliant": compliant,
                "reason": "ok" if compliant else "x",
                "first_commit_iso": first_commit_iso}

    def test_overall_rate(self):
        rows = [self._row("r", 1, True, "2026-07-01T00:00:00Z"),
                self._row("r", 2, False, "2026-07-01T00:00:00Z"),
                self._row("r", 3, False, "2026-07-01T00:00:00Z"),
                self._row("r", 4, True, "2026-07-01T00:00:00Z")]
        s = m.summarize(rows, self.CUTOVER)
        self.assertEqual(s["n_examined"], 4)
        self.assertEqual(s["n_compliant"], 2)
        self.assertAlmostEqual(s["rate"], 0.5)

    def test_before_after_split(self):
        rows = [
            self._row("r", 1, True, "2026-07-10T00:00:00Z"),   # before, compliant
            self._row("r", 2, False, "2026-07-10T00:00:00Z"),  # before, not
            self._row("r", 3, False, "2026-07-10T00:00:00Z"),  # before, not
            self._row("r", 4, True, "2026-07-28T00:00:00Z"),   # after, compliant
            self._row("r", 5, True, "2026-07-28T00:00:00Z"),   # after, compliant
        ]
        s = m.summarize(rows, self.CUTOVER)
        self.assertEqual(s["before"]["n_examined"], 3)
        self.assertEqual(s["before"]["n_compliant"], 1)
        self.assertEqual(s["after"]["n_examined"], 2)
        self.assertEqual(s["after"]["n_compliant"], 2)

    def test_rows_with_no_commit_date_are_unclassifiable_not_before_or_after(self):
        rows = [self._row("r", 1, False, None)]
        s = m.summarize(rows, self.CUTOVER)
        self.assertEqual(s["n_examined"], 1)
        self.assertEqual(s["before"]["n_examined"], 0)
        self.assertEqual(s["after"]["n_examined"], 0)
        self.assertEqual(s["n_unclassifiable_timing"], 1)

    def test_empty_input(self):
        s = m.summarize([], self.CUTOVER)
        self.assertEqual(s["n_examined"], 0)
        self.assertIsNone(s["rate"])


if __name__ == "__main__":
    unittest.main()
