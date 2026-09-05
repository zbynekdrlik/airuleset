"""Tests for classify_shared_benefit (#877)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import design_gate as dg


# A design comment that carries all three existing classifiers' concepts.
GOOD_BASE = (
    "Triage: non-trivial\n\n"
    "Root cause: the design gate has no check for shared-benefit disposition.\n"
    "Approach: add a classify_shared_benefit classifier.\n"
    "Rejected alternative: prose-only enforcement.\n\n"
    "Approach 1: HARD gate\nApproach 2: SOFT lens\n"
    "Trade-off: HARD catches every commit vs SOFT is less overhead.\n"
)


class TestClassifySharedBenefit(unittest.TestCase):

    def test_missing_line_fails(self):
        ok, reason = dg.classify_shared_benefit(GOOD_BASE)
        self.assertFalse(ok)
        self.assertIn("Shared-benefit", reason)

    def test_bare_na_without_reason_fails(self):
        body = GOOD_BASE + "\nShared-benefit: n/a"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok)
        self.assertIn("bare", reason.lower())

    def test_bare_nie_without_reason_fails(self):
        body = GOOD_BASE + "\nShared-benefit: nie"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok)

    def test_bare_no_without_reason_fails(self):
        body = GOOD_BASE + "\nShared-benefit: no"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok)

    def test_bare_none_without_reason_fails(self):
        body = GOOD_BASE + "\nShared-benefit: none"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok)

    def test_bare_dash_without_reason_fails(self):
        body = GOOD_BASE + "\nShared-benefit: -"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok)

    def test_na_with_reason_passes(self):
        body = GOOD_BASE + "\nShared-benefit: n/a — single-file typo v README"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_shared_disposition_passes(self):
        body = GOOD_BASE + "\nShared-benefit: shared — nationwide holidays go into company_base"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_single_client_disposition_passes(self):
        body = GOOD_BASE + "\nShared-benefit: single-client — MIVA-specific report format"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_fleet_wide_disposition_passes(self):
        body = GOOD_BASE + "\nShared-benefit: fleet-wide mechanism in airuleset modules"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_bold_header_recognized(self):
        body = GOOD_BASE + "\n**Shared-benefit:** fleet mechanism"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_dash_bullet_prefix_recognized(self):
        body = GOOD_BASE + "\n- **Shared-benefit:** fleet mechanism for all projects"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_no_hyphen_form_recognized(self):
        body = GOOD_BASE + "\nShared benefit: fleet-wide mechanism"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_empty_body_fails(self):
        ok, reason = dg.classify_shared_benefit("")
        self.assertFalse(ok)

    def test_none_body_fails(self):
        ok, reason = dg.classify_shared_benefit(None)
        self.assertFalse(ok)

    def test_empty_value_after_colon_fails(self):
        body = GOOD_BASE + "\nShared-benefit:"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok)

    def test_na_with_dash_reason_passes(self):
        body = GOOD_BASE + "\nShared-benefit: n/a - pure airuleset tooling change"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    def test_zdielane_disposition_passes(self):
        body = GOOD_BASE + "\nShared-benefit: zdielane do company_base, klient len data"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)

    # YELLOW-1: bold-form bare n/a must still be rejected
    def test_bold_bare_na_fails(self):
        body = GOOD_BASE + "\n**Shared-benefit:** n/a"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok, "bold bare n/a should be rejected")

    def test_bold_bare_nie_fails(self):
        body = GOOD_BASE + "\n**Shared-benefit:** nie"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok, "bold bare nie should be rejected")

    # YELLOW-3: boundary tests for the len(tail) < 5 threshold
    def test_na_with_3char_reason_fails(self):
        body = GOOD_BASE + "\nShared-benefit: n/a — abc"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertFalse(ok, "3-char reason should be rejected (< 5)")

    def test_na_with_5char_reason_passes(self):
        body = GOOD_BASE + "\nShared-benefit: n/a — abcde"
        ok, reason = dg.classify_shared_benefit(body)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
