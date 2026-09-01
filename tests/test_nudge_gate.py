"""#797 — the shared per-session / per-category nudge CADENCE GATE
(`watchdog/nudge_gate.py`): the ONE floor + family-spacing gate that the new
u-freshness rider AND the four existing job-20 keystroke riders (partition-audit,
release-gap, queue-arrival, lane-occupancy) consult, so nudges stop arriving in
bursts ("chodia jak besne po sebe") and the u-freshness reconcile can never fire
more often than 1×/hour (the owner's hard strop).

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
    """The u-freshness per-category floor is the owner's hard 1×/hour strop; the
    env override can only RAISE it, never lower it below 3600 (#504/#543 floor
    clamp). The other four categories carry NO per-category floor (their own
    cadences govern), so the gate is a pure ADDITIONAL floor for them."""

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

    def test_family_gap_default_and_floor(self):
        self.assertEqual(ng.NUDGE_FAMILY_GAP_S, 15 * 60)
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_FAMILY_GAP_S": "5"}):
            self.assertEqual(ng._family_gap(), ng.NUDGE_FAMILY_GAP_MIN_S)

    def test_category_floor_only_u_freshness(self):
        self.assertEqual(ng._category_floor("u-freshness"), ng._u_cadence())
        for cat in ("partition-audit", "release-gap", "queue-arrival",
                    "lane-occupancy"):
            self.assertEqual(ng._category_floor(cat), 0)


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

    def test_family_gap_defers_a_DIFFERENT_category(self):
        st = {}
        ng.mark_sent(st, "sess-a", "lane-occupancy", NOW)
        # a DIFFERENT category within the family gap is deferred (the burst fix)
        self.assertFalse(ng.gate_ok(st, "sess-a", "release-gap", NOW + 60))
        # past the family gap it is allowed
        self.assertTrue(ng.gate_ok(st, "sess-a", "release-gap",
                                   NOW + ng._family_gap()))

    def test_family_gap_ignores_SAME_category(self):
        # a rider's OWN back-to-back is governed by its own cadence, NOT the
        # family gap — the gate must never change a rider's own semantics. The
        # four floorless categories therefore pass their own repeat immediately.
        st = {}
        ng.mark_sent(st, "sess-a", "release-gap", NOW)
        self.assertTrue(ng.gate_ok(st, "sess-a", "release-gap", NOW + 60))

    def test_family_gap_is_per_session(self):
        st = {}
        ng.mark_sent(st, "sess-a", "lane-occupancy", NOW)
        # a DIFFERENT session is unaffected
        self.assertTrue(ng.gate_ok(st, "sess-b", "release-gap", NOW + 60))

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


if __name__ == "__main__":
    unittest.main()
