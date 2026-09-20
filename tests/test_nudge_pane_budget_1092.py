"""#1092 — the per-pane typing-attempt budget (item c).

A brake INDEPENDENT of the per-kind floor + cross-kind total cap, keyed on the
PANE (pid) not the session (sid): at most `PANE_ATTEMPT_BUDGET` (2) machine
typing attempts per pane per rolling `PANE_ATTEMPT_WINDOW_S` (1 h), across ALL
kinds. Even if a future outcome escapes the per-kind floor stamp (the #1092
swallowed-batch storm was exactly that), the pane can never receive more than two
typing attempts an hour.

RED against the pre-#1092 tree: `nudge_gate` has no `PANE_ATTEMPT_BUDGET` /
`pane_budget_ok` / `mark_pane_attempt` / `pane_budget_hold_reason` at all.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import nudge_gate as ng  # noqa: E402

NOW = 1_000_000
HOUR = 3600


class TestPaneBudgetPrimitives(unittest.TestCase):
    def test_budget_constant_is_two_and_window_one_hour(self):
        self.assertEqual(ng.PANE_ATTEMPT_BUDGET, 2)
        self.assertEqual(ng.PANE_ATTEMPT_WINDOW_S, HOUR)

    def test_empty_state_allows(self):
        self.assertTrue(ng.pane_budget_ok({}, "%1", NOW))
        self.assertTrue(ng.pane_budget_ok(None, "%1", NOW))

    def test_third_attempt_refused_within_the_hour(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        self.assertTrue(ng.pane_budget_ok(state, "%1", NOW + 60))
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        # two attempts in the window -> the third is refused BEFORE any keystroke
        self.assertFalse(ng.pane_budget_ok(state, "%1", NOW + 120))

    def test_a_different_pane_has_its_own_budget(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        self.assertFalse(ng.pane_budget_ok(state, "%1", NOW + 120))
        # a DIFFERENT pane is unaffected
        self.assertTrue(ng.pane_budget_ok(state, "%2", NOW + 120))

    def test_budget_reopens_after_the_rolling_hour(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        self.assertFalse(ng.pane_budget_ok(state, "%1", NOW + 120))
        # past the window from the OLDEST attempt: it ages out -> one slot frees
        self.assertTrue(ng.pane_budget_ok(state, "%1", NOW + HOUR + 1))

    def test_hold_reason_names_count_and_a_clock_time(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        reason = ng.pane_budget_hold_reason(state, "%1", NOW + 120)
        self.assertIn("hold:pane-budget", reason)
        self.assertIn("2 attempts since", reason)

    def test_mark_prunes_its_own_list_to_the_window_on_write(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        # a later mark far outside the window drops the stale entry, keeps state bounded
        ng.mark_pane_attempt(state, "%1", NOW + 2 * HOUR)
        self.assertEqual(len(state["nudge_pane_attempts"]["%1"]), 1)

    def test_malformed_state_reads_as_no_prior_attempt(self):
        self.assertTrue(ng.pane_budget_ok({"nudge_pane_attempts": "junk"}, "%1", NOW))
        self.assertTrue(ng.pane_budget_ok(
            {"nudge_pane_attempts": {"%1": "junk"}}, "%1", NOW))
        # a future-skewed ts is ignored (fail-safe ALLOW, mirroring _gate_ts)
        self.assertTrue(ng.pane_budget_ok(
            {"nudge_pane_attempts": {"%1": [NOW + 10 * HOUR, NOW + 11 * HOUR]}},
            "%1", NOW))

    def test_prune_reaps_a_gone_pane_with_no_in_window_attempt(self):
        state = {"nudge_pane_attempts": {"%gone": [NOW - 2 * HOUR],
                                         "%live": [NOW]}}
        ng.prune(state, visited_sids=set(), now=NOW)
        self.assertNotIn("%gone", state["nudge_pane_attempts"])
        self.assertIn("%live", state["nudge_pane_attempts"])


if __name__ == "__main__":
    unittest.main()
