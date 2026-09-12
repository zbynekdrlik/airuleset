"""#992 req 1 / #993 item 2 — the lane-occupancy + queue-arrival nudges must
state that refill applies ONLY to independent units: infra work is SERIAL, so
when the only workable tickets are infra and one infra lane is live, NO refill.
The nudges must SAY this instead of unconditionally pushing dispatch.

Phrase-locked (the nudge is FACTS + doctrine, never a count/priority prescription
— #994): a partial revert of the clause fails these.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import watchdog.lane_resources as lr  # noqa: E402
import watchdog.queue_arrival_recheck as qa  # noqa: E402
import watchdog.goal as goal  # noqa: E402


class TestLaneNudgeInfraSerial(TestCase):
    def test_lane_nudge_states_infra_serial_no_refill(self):
        t = lr._lane_nudge_text(4, 1, {"total": 5}).lower()
        self.assertIn("infra", t)
        self.assertIn("sériov", t)          # SÉRIOVÉ (serial)
        self.assertIn("nerefill", t)        # no refill for infra-only + live infra lane


class TestQueueNudgeInfraSerial(TestCase):
    def test_queue_nudge_states_infra_serial_no_refill(self):
        t = qa._nudge_text([5177, 5310], 8).lower()
        self.assertIn("infra", t)
        self.assertIn("sériov", t)
        self.assertIn("nerefill", t)

    def test_queue_nudge_stays_within_cap(self):
        # #978: a folded clause must not push the nudge past NUDGE_MAX_CHARS.
        t = qa._nudge_text([5177, 5310], 8)
        self.assertLessEqual(len(t), qa.NUDGE_MAX_CHARS)


NOW = 1_000_000


class TestQueueDecisionClassAware(TestCase):
    """#993 item 4 — the queue-arrival DECISION filters arrivals by dispatch
    class: an arrival that is infra-while-infra-lane-live or dep-wait is HELD
    (no nudge); the nudge fires only for dispatchable arrivals."""

    def _base(self, base):
        return {"base": base, "first_seen": NOW - 100, "last_nudge": None}

    def test_no_classify_fn_is_legacy_all_dispatchable(self):
        action, _out, _r, arr = qa._queue_decision(self._base([1]), [1, 9], NOW)
        self.assertEqual(action, "nudge")
        self.assertEqual(arr, [9])

    def test_all_infra_serial_arrivals_hold(self):
        action, out, reason, arr = qa._queue_decision(
            self._base([1]), [1, 9], NOW,
            classify_fn=lambda n: "infra-serial")
        self.assertEqual(action, "hold")
        self.assertEqual(reason, "infra-serial")
        self.assertEqual(out["base"], [1])   # base kept OLD → re-detect later
        self.assertEqual(arr, [9])

    def test_all_dep_wait_arrivals_hold_with_dep_wait_reason(self):
        action, _out, reason, _arr = qa._queue_decision(
            self._base([1]), [1, 9], NOW,
            classify_fn=lambda n: "dep-wait")
        self.assertEqual(action, "hold")
        self.assertEqual(reason, "dep-wait")

    def test_mixed_nudges_only_dispatchable_arrivals(self):
        cls = {8: "dispatchable", 9: "infra-serial"}
        action, _out, _r, arr = qa._queue_decision(
            self._base([1]), [1, 8, 9], NOW,
            classify_fn=lambda n: cls[n])
        self.assertEqual(action, "nudge")
        self.assertEqual(arr, [8])          # only the dispatchable arrival named

    def test_mixed_infra_and_dep_holds_infra_serial(self):
        cls = {8: "dep-wait", 9: "infra-serial"}
        action, _out, reason, _arr = qa._queue_decision(
            self._base([1]), [1, 8, 9], NOW,
            classify_fn=lambda n: cls[n])
        self.assertEqual(action, "hold")
        self.assertEqual(reason, "infra-serial")   # any infra held → infra-serial


class TestLaneDispatchableDecision(TestCase):
    """#993 item 3 — the lane-occupancy candidate gate: a free slot with no
    dispatchable candidate is a journaled skip, NOT a nudge."""

    def _fetch(self, count, reason=None):
        return lambda cwd: [{"count": count, "reason": reason}]

    def test_unwired_no_gating(self):
        skip, log, cand = goal._lane_dispatchable_decision(
            None, "/c", {}, 100, "loc", 0, 0, 5)
        self.assertFalse(skip)
        self.assertIsNone(log)
        self.assertIsNone(cand)

    def test_zero_candidates_infra_serial_skips(self):
        skip, log, cand = goal._lane_dispatchable_decision(
            self._fetch(0, "infra-serial"), "/c", {}, 100, "loc", 1, 0, 5)
        self.assertTrue(skip)
        self.assertIn("skip:infra-serial", log)
        self.assertEqual(cand, 0)

    def test_zero_candidates_dep_wait_skips(self):
        skip, log, _c = goal._lane_dispatchable_decision(
            self._fetch(0, "dep-wait"), "/c", {}, 100, "loc", 0, 0, 3)
        self.assertTrue(skip)
        self.assertIn("skip:dep-wait", log)

    def test_positive_candidates_proceeds(self):
        skip, log, cand = goal._lane_dispatchable_decision(
            self._fetch(3), "/c", {}, 100, "loc", 0, 0, 5)
        self.assertFalse(skip)
        self.assertIsNone(log)
        self.assertEqual(cand, 3)

    def test_unmeasurable_skips_unknown(self):
        skip, log, _c = goal._lane_dispatchable_decision(
            lambda cwd: None, "/c", {}, 100, "loc", 0, 0, 5)
        self.assertTrue(skip)
        self.assertIn("skip:dispatchable-unknown", log)


class TestLaneNudgeTextCandidateCount(TestCase):
    """#993 item 3 — when candidate_n is given the text names it (dispatchable
    count), dropping the unconditional 'sú voľné sloty' pressure."""

    def test_candidate_count_named(self):
        t = lr._lane_nudge_text(37, 2, {"total": 5}, candidate_n=3)
        self.assertIn("3 DISPATCHOVATEĽNÝCH", t)
        self.assertNotIn("Sú VOĽNÉ sloty", t)
        self.assertIn("backlog=37", t)   # raw backlog still reported

    def test_no_candidate_keeps_legacy_wording(self):
        t = lr._lane_nudge_text(37, 2, {"total": 5})
        self.assertIn("Sú VOĽNÉ sloty", t)
        self.assertNotIn("DISPATCHOVATEĽNÝCH", t)


if __name__ == "__main__":
    main()
