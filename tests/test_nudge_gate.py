"""#797 — the shared per-session / per-kind nudge CADENCE GATE
(`watchdog/nudge_gate.py`): the ONE floor gate that the u-freshness rider AND
the job-20 keystroke riders (partition-audit, release-gap, queue-arrival,
lane-occupancy, lane-reconcile) consult, so no machine nudge reaches a session's
prompt more often than 1×/hour (the owner's hard strop).

#1023 (owner directive 2026-09-14): the ONE floor is a per-pane-per-KIND 60-min
`NUDGE_MIN_INTERVAL_S` covering EVERY gated kind (u-freshness / goal-guard keep
their longer floors via max()).

#1023 fix-forward (integration finding, 2026-09-14): the #913 cross-kind TOTAL
cap is RESTORED (`NUDGE_TOTAL_GAP_S`, a NEW symbol — the old `NUDGE_FAMILY_GAP_S`/
`_family_gap` stay deleted). The owner's #913 rule is "nikdy viac ako raz za
hodinu!!!! a ani iny nudge do promptu!!!" = at most ONE PRIORITY nudge per pane
per hour in TOTAL, across kinds. So a DIFFERENT kind IS held while another
priority kind was delivered within the total gap — per-kind staging alone does
NOT bound the total (two kinds staged → two nudges/hour). RECOVERY kinds
(resume/compact) are exempt from both bounds and never count for the cap.

RED against the pre-implementation tree: `from watchdog import nudge_gate`
ImportErrors. GREEN once the module lands.
"""

import os
import unittest
import unittest.mock as m
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import nudge_gate as ng

NOW = 1_000_000
HOUR = 3600


class TestCadenceFloors(unittest.TestCase):
    """#1023: EVERY gated kind carries the global per-pane-per-kind 60-min floor
    (`_min_interval()`); u-freshness (owner's `_u_cadence()` strop) and goal-guard
    (24 h) keep their LONGER floors via max(). The env override can only RAISE,
    never lower below 3600 (#504/#543 floor clamp). The per-kind floor is against
    a kind's OWN last delivery; the cross-kind TOTAL cap (#1023 fix-forward,
    `NUDGE_TOTAL_GAP_S`) is the separate across-kinds bound tested elsewhere. The
    old `NUDGE_FAMILY_GAP_S`/`_family_gap` symbols stay deleted (a new name)."""

    def test_u_reconcile_default_is_one_hour(self):
        self.assertEqual(ng.U_RECONCILE_CADENCE_S, HOUR)

    def test_u_cadence_env_can_only_raise(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_U_RECONCILE_CADENCE_S": "60"}):
            self.assertEqual(ng._u_cadence(), HOUR)  # clamped up to the strop
        with m.patch.dict(os.environ,
                          {"AIRULESET_U_RECONCILE_CADENCE_S": str(3 * HOUR)}):
            self.assertEqual(ng._u_cadence(), 3 * HOUR)  # a raise is honored

    def test_u_cadence_garbage_env_falls_back(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_U_RECONCILE_CADENCE_S": "not-a-number"}):
            self.assertEqual(ng._u_cadence(), HOUR)

    def test_min_interval_default_is_one_hour(self):
        # #1023: the global per-kind floor replaces the #913 family gap.
        self.assertEqual(ng.NUDGE_MIN_INTERVAL_S, HOUR)
        self.assertEqual(ng.NUDGE_MIN_INTERVAL_MIN_S, HOUR)
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_MIN_INTERVAL_S": "5"}):
            # env can only RAISE above 1 h, never lower.
            self.assertEqual(ng._min_interval(), HOUR)

    def test_family_gap_symbols_removed(self):
        # #1023: the cross-kind family gap is deleted, not left as dead code.
        self.assertFalse(hasattr(ng, "NUDGE_FAMILY_GAP_S"))
        self.assertFalse(hasattr(ng, "NUDGE_FAMILY_GAP_MIN_S"))
        self.assertFalse(hasattr(ng, "_family_gap"))

    def test_category_floor_uniform_per_kind(self):
        # #1023: EVERY gated kind carries at least the 60-min floor.
        self.assertEqual(ng._category_floor("u-freshness"),
                         max(ng._min_interval(), ng._u_cadence()))
        self.assertEqual(ng._category_floor("goal-guard"), ng.GOAL_GUARD_FLOOR_S)
        for cat in ("partition-audit", "release-gap", "queue-arrival",
                    "lane-occupancy", "lane-reconcile"):
            self.assertEqual(ng._category_floor(cat), ng._min_interval())
            self.assertGreaterEqual(ng._category_floor(cat), HOUR)

    def test_gated_categories_includes_lane_reconcile(self):
        """#913: lane-reconcile already calls gate_ok/mark_sent — it must be
        in GATED_CATEGORIES for documentation + enumeration completeness."""
        self.assertIn("lane-reconcile", ng.GATED_CATEGORIES)


class TestGateOk(unittest.TestCase):
    def test_empty_state_allows_any_category(self):
        st = {}
        for cat in ng.GATED_CATEGORIES:
            self.assertTrue(ng.gate_ok(st, "sess-a", cat, NOW))

    def test_u_freshness_blocked_within_the_hour(self):
        st = {}
        ng.mark_sent(st, "sess-a", "u-freshness", NOW)
        self.assertFalse(ng.gate_ok(st, "sess-a", "u-freshness", NOW + 1800))
        self.assertTrue(ng.gate_ok(st, "sess-a", "u-freshness", NOW + HOUR))

    def test_u_freshness_env_raise_extends_the_block(self):
        st = {}
        with m.patch.dict(os.environ,
                          {"AIRULESET_U_RECONCILE_CADENCE_S": str(2 * HOUR)}):
            ng.mark_sent(st, "sess-a", "u-freshness", NOW)
            self.assertFalse(ng.gate_ok(st, "sess-a", "u-freshness",
                                        NOW + HOUR + 60))
            self.assertTrue(ng.gate_ok(st, "sess-a", "u-freshness",
                                       NOW + 2 * HOUR))

    def test_different_kind_deferred_by_total_cap_1023ff(self):
        # #1023 fix-forward: a DIFFERENT kind IS held by the restored cross-kind
        # TOTAL cap while another priority kind was delivered within the hour
        # (the #913 rule the #1023 lane wrongly dropped). Past the hour: allowed.
        st = {}
        ng.mark_sent(st, "sess-a", "lane-occupancy", NOW)
        self.assertFalse(ng.gate_ok(st, "sess-a", "release-gap", NOW + 60))
        self.assertFalse(ng.gate_ok(st, "sess-a", "release-gap", NOW + 59 * 60))
        self.assertTrue(ng.gate_ok(st, "sess-a", "release-gap", NOW + HOUR))

    def test_same_kind_deferred_within_the_hour_1023(self):
        """#1023: the SAME kind is floored — a second delivery of the SAME kind
        within 60 min of the last confirmed one is suppressed (the burst fix)."""
        st = {}
        ng.mark_sent(st, "sess-a", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + 45 * 60))
        self.assertFalse(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + 59 * 60))
        # at exactly 1 h it passes
        self.assertTrue(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + HOUR))

    def test_floor_is_per_session(self):
        st = {}
        ng.mark_sent(st, "sess-a", "queue-arrival", NOW)
        # a DIFFERENT session is unaffected
        self.assertTrue(ng.gate_ok(st, "sess-b", "queue-arrival", NOW + 60))

    def test_malformed_state_fails_safe_to_allow(self):
        # a corrupt gate entry must never SUPPRESS a legit nudge (the safe
        # direction for the existing riders; u-freshness has its own last_nudge
        # backstop so it can't burst even here).
        self.assertTrue(ng.gate_ok({"nudge_cadence": "boom"}, "s", "release-gap",
                                    NOW))
        self.assertTrue(ng.gate_ok({"nudge_cadence": {"s": "boom"}}, "s",
                                   "release-gap", NOW))
        self.assertTrue(ng.gate_ok({"nudge_cadence": {"s": {"release-gap":
                                    "nan"}}}, "s", "u-freshness", NOW))

    def test_future_skewed_ts_fails_safe_to_allow(self):
        # a FUTURE / corrupt-huge ts must NOT permanently mute the session (a
        # clamp-to-now would defer FOREVER, since a re-read future ts re-clamps
        # to now every call — the worst direction for the owner's ONLY question
        # surface). It is ignored → the gate ALLOWS, and the next mark_sent
        # overwrites it with `now`.
        st = {"nudge_cadence": {"s": {"u-freshness": NOW + 10 ** 9}}}
        self.assertTrue(ng.gate_ok(st, "s", "u-freshness", NOW))   # own floor
        # a DIFFERENT kind is unaffected by u-freshness's ts anyway (#1023).
        self.assertTrue(ng.gate_ok(st, "s", "release-gap", NOW))


class TestClassification923(unittest.TestCase):
    """#923: WORK_DRIVING vs AUDIT classification of gated categories."""

    def test_classification_covers_all_gated(self):
        """Every GATED_CATEGORIES member is in exactly one class."""
        self.assertEqual(ng.WORK_DRIVING_CATEGORIES | ng.AUDIT_CATEGORIES,
                         ng.GATED_CATEGORIES)
        self.assertEqual(len(ng.WORK_DRIVING_CATEGORIES & ng.AUDIT_CATEGORIES),
                         0)

    def test_work_driving_members(self):
        self.assertEqual(ng.WORK_DRIVING_CATEGORIES, frozenset({
            "lane-occupancy", "release-gap", "queue-arrival", "lane-reconcile",
        }))

    def test_audit_members(self):
        self.assertEqual(ng.AUDIT_CATEGORIES, frozenset({
            "partition-audit", "u-freshness", "goal-guard",
        }))


class TestBatchEligible923(unittest.TestCase):
    """#923 BATCHING, rebased on #1023's per-kind floor AND the #1023 fix-forward
    cross-kind TOTAL cap: batch_eligible returns [] while ANY priority kind was
    delivered within the total gap (a new batch is a new interruption), else the
    categories whose OWN per-kind floor has expired, ordered work-driving first,
    audit second. Batching is the mechanism that lets multiple due kinds SHARE
    the ONE hourly interruption; the total cap forbids a SECOND one in the hour."""

    def test_empty_state_all_eligible(self):
        st = {}
        result = ng.batch_eligible(st, "s", NOW)
        # All categories eligible (no history, no floor blocks)
        self.assertEqual(len(result), len(ng.GATED_CATEGORIES))
        # Work-driving comes first
        wd_end = 0
        for cat in result:
            if cat in ng.WORK_DRIVING_CATEGORIES:
                wd_end += 1
            else:
                break
        self.assertEqual(wd_end, len(ng.WORK_DRIVING_CATEGORIES))

    def test_recent_delivery_blocks_the_whole_batch(self):
        # #1023 fix-forward: a recent priority delivery closes the cross-kind
        # TOTAL cap, so NO new batch is composed this hour (was: other kinds
        # stayed eligible — the two-nudges-per-hour hole via the batch path).
        st = {}
        ng.mark_sent(st, "s", "lane-occupancy", NOW)
        self.assertEqual(ng.batch_eligible(st, "s", NOW + 60), [])

    def test_gap_open_returns_all_eligible(self):
        st = {}
        ng.mark_sent(st, "s", "lane-occupancy", NOW)
        # After 1h: lane-occupancy's floor has expired → all eligible again
        result = ng.batch_eligible(st, "s", NOW + HOUR)
        self.assertGreater(len(result), 0)
        self.assertIn("lane-occupancy", result)
        self.assertIn("partition-audit", result)

    def test_total_cap_then_floor_gates_the_batch(self):
        """#1023 fix-forward: after a u-freshness delivery at NOW, the total cap
        holds the WHOLE batch until NOW+HOUR (no batch at NOW+HOUR-1), then at
        NOW+HOUR the cap opens and u-freshness's own 1h floor has expired too, so
        it (and every other kind) is eligible."""
        st = {}
        ng.mark_sent(st, "s", "u-freshness", NOW)
        # At NOW+HOUR-1: total cap still closed -> no batch at all.
        self.assertEqual(ng.batch_eligible(st, "s", NOW + HOUR - 1), [])
        # At NOW+HOUR: cap open + own floor expired -> included.
        result = ng.batch_eligible(st, "s", NOW + HOUR)
        self.assertIn("u-freshness", result)
        self.assertIn("partition-audit", result)

    def test_starvation_impossible_with_batching(self):
        """The gk starvation shape is impossible with batching: when the slot
        opens, BOTH lane-occupancy AND partition-audit are in the batch."""
        st = {}
        ng.mark_sent(st, "s", "lane-occupancy", NOW - 2 * HOUR)
        ng.mark_sent(st, "s", "partition-audit", NOW)
        # After 1h from the latest mark_sent: gap opens
        result = ng.batch_eligible(st, "s", NOW + HOUR)
        self.assertIn("lane-occupancy", result)
        self.assertIn("partition-audit", result)

    def test_mark_batch_sent_stamps_all(self):
        """#1023 + fix-forward: mark_batch_sent marks all categories at once, and
        the cross-kind total cap then holds the WHOLE batch (even an unmarked
        kind) for the hour — the batch WAS the one hourly interruption."""
        st = {}
        cats = ["lane-occupancy", "partition-audit", "u-freshness"]
        ng.mark_batch_sent(st, "s", cats, NOW)
        for cat in cats:
            self.assertEqual(st["nudge_cadence"]["s"][cat], NOW)
        # Within the hour: the total cap closes the batch entirely (no unmarked
        # kind leaks a second interruption).
        self.assertEqual(ng.batch_eligible(st, "s", NOW + 30 * 60), [])
        # Past the hour every kind is eligible again.
        self.assertEqual(len(ng.batch_eligible(st, "s", NOW + HOUR)),
                         len(ng.GATED_CATEGORIES))

    def test_ordering_work_driving_first(self):
        """Work-driving categories appear before audit in the batch."""
        st = {}
        result = ng.batch_eligible(st, "s", NOW)
        wd_idx = [i for i, c in enumerate(result)
                  if c in ng.WORK_DRIVING_CATEGORIES]
        au_idx = [i for i, c in enumerate(result)
                  if c in ng.AUDIT_CATEGORIES]
        if wd_idx and au_idx:
            self.assertLess(max(wd_idx), min(au_idx))


class TestComposeBatch923(unittest.TestCase):
    """#923: compose_batch formats items into a BATCH_PREFIX-headed message."""

    def test_prefix_leads(self):
        text, included = ng.compose_batch([("lane-occupancy", "refill 3 lanes")])
        self.assertTrue(text.startswith(ng.BATCH_PREFIX))
        self.assertIn("lane-occupancy", included)

    def test_orders_wd_first(self):
        items = [("partition-audit", "I5 U0"),
                 ("lane-occupancy", "refill 3")]
        text, included = ng.compose_batch(items)
        lo_pos = text.index("[lane-occupancy]")
        pa_pos = text.index("[partition-audit]")
        self.assertLess(lo_pos, pa_pos)

    def test_empty_returns_empty_tuple(self):
        text, included = ng.compose_batch([])
        self.assertEqual(text, "")
        self.assertEqual(included, [])

    def test_max_chars_trims_audit_first(self):
        items = [("lane-occupancy", "refill"),
                 ("partition-audit", "I5 U0 W0 skip0")]
        text, included = ng.compose_batch(items, max_chars=50)
        # Work-driving kept, audit trimmed if needed
        self.assertIn("[lane-occupancy]", text)
        self.assertTrue(len(text) <= 50)

    def test_batch_prefix_is_machine_recognized(self):
        """BATCH_PREFIX must be 'nudge:' — already in goal.py
        _machine_prefixes, so the transcript classifier handles it."""
        self.assertEqual(ng.BATCH_PREFIX, "nudge:")


class TestMarkSent(unittest.TestCase):
    def test_mark_sent_records_per_sid_per_category(self):
        st = {}
        ng.mark_sent(st, "sess-a", "u-freshness", NOW)
        self.assertEqual(st["nudge_cadence"]["sess-a"]["u-freshness"], NOW)

    def test_mark_sent_does_not_clobber_other_categories(self):
        st = {}
        ng.mark_sent(st, "sess-a", "lane-occupancy", NOW - 100)
        ng.mark_sent(st, "sess-a", "u-freshness", NOW)
        self.assertEqual(st["nudge_cadence"]["sess-a"]["lane-occupancy"],
                         NOW - 100)
        self.assertEqual(st["nudge_cadence"]["sess-a"]["u-freshness"], NOW)


class TestPrune(unittest.TestCase):
    def test_prune_reaps_gone_and_aged_sid(self):
        st = {"nudge_cadence": {
            "gone": {"u-freshness": NOW - 2 * ng.NUDGE_CADENCE_ORPHAN_TTL_S},
            "live": {"u-freshness": NOW - 10},
        }}
        ng.prune(st, {"live"}, NOW)
        self.assertNotIn("gone", st["nudge_cadence"])
        self.assertIn("live", st["nudge_cadence"])

    def test_prune_keeps_a_gone_but_FRESH_sid(self):
        # visited gate is primary, but a recently-active (fresh) gone sid is kept
        # by the secondary TTL — never reap a sid whose clock is still young.
        st = {"nudge_cadence": {"gone": {"release-gap": NOW - 10}}}
        ng.prune(st, set(), NOW)
        self.assertIn("gone", st["nudge_cadence"])

    def test_prune_reaps_gone_malformed_entry(self):
        st = {"nudge_cadence": {"gone": "garbage"}}
        ng.prune(st, set(), NOW)
        self.assertNotIn("gone", st["nudge_cadence"])

    def test_prune_never_reaps_a_visited_sid(self):
        st = {"nudge_cadence": {"live": "garbage"}}  # malformed but LIVE
        ng.prune(st, {"live"}, NOW)
        self.assertIn("live", st["nudge_cadence"])

    def test_prune_tolerates_missing_namespace(self):
        st = {}
        ng.prune(st, set(), NOW)  # never raises


class TestBatchCallerRefactor923(unittest.TestCase):
    """#923 CALLER-REFACTOR: compose_batch returns (text, included_categories)
    so callers know which families survived trimming, and mark_batch_sent marks
    only the included ones."""

    def test_compose_batch_returns_tuple_with_included(self):
        """compose_batch returns (text, included_categories), not just text."""
        items = [("lane-occupancy", "refill 3"),
                 ("u-freshness", "U=2")]
        result = ng.compose_batch(items)
        # Must be a 2-tuple: (text, included_categories)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        text, included = result
        self.assertTrue(text.startswith(ng.BATCH_PREFIX))
        self.assertIn("lane-occupancy", included)
        self.assertIn("u-freshness", included)

    def test_compose_batch_trim_excludes_audit(self):
        """When max_chars trims audit, included_categories omits the trimmed."""
        items = [("lane-occupancy", "refill 3"),
                 ("partition-audit", "I5 U0 W0 gk0 skip0")]
        text, included = ng.compose_batch(items, max_chars=50)
        self.assertIn("lane-occupancy", included)
        # audit may or may not be trimmed depending on exact lengths,
        # but included must match what's actually in the text
        for cat in included:
            self.assertIn("[%s]" % cat, text)

    def test_compose_batch_empty_returns_empty(self):
        """Empty items still returns a 2-tuple."""
        text, included = ng.compose_batch([])
        self.assertEqual(text, "")
        self.assertEqual(included, [])

    def test_two_families_both_marked_after_batch(self):
        """#1023 + fix-forward: when two families are batched, mark_batch_sent
        stamps BOTH; the cross-kind total cap then closes the next batch entirely
        for the hour (an unbatched kind no longer leaks a second interruption)."""
        st = {}
        cats = ["lane-occupancy", "u-freshness"]
        ng.mark_batch_sent(st, "s", cats, NOW)
        sess = st["nudge_cadence"]["s"]
        self.assertEqual(sess["lane-occupancy"], NOW)
        self.assertEqual(sess["u-freshness"], NOW)
        # The total cap holds the whole batch within the hour.
        self.assertEqual(ng.batch_eligible(st, "s", NOW + 60), [])


if __name__ == "__main__":
    unittest.main()
