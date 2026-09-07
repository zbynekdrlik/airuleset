"""#797 — the shared per-session / per-category nudge CADENCE GATE
(`watchdog/nudge_gate.py`): the ONE floor + family-spacing gate that the new
u-freshness rider AND the four existing job-20 keystroke riders (partition-audit,
release-gap, queue-arrival, lane-occupancy) consult, so nudges stop arriving in
bursts ("chodia jak besne po sebe") and the u-freshness reconcile can never fire
more often than 1×/hour (the owner's hard strop).

#913 (owner directive 2026-09-06): the cross-family gap was raised from 15 min
to 60 min — NO watchdog nudge into any session prompt more often than 1×/hour
TOTAL, across ALL families. `NUDGE_FAMILY_GAP_MIN_S` raised to 3600 so the env
override can only RAISE above 1 h. `lane-reconcile` added to GATED_CATEGORIES.

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

    def test_family_gap_default_is_one_hour(self):
        # #913: cross-family gap raised to 1 h (owner directive).
        self.assertEqual(ng.NUDGE_FAMILY_GAP_S, HOUR)
        self.assertEqual(ng.NUDGE_FAMILY_GAP_MIN_S, HOUR)
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_FAMILY_GAP_S": "5"}):
            # env can only RAISE above 1 h, never lower.
            self.assertEqual(ng._family_gap(), HOUR)

    def test_category_floor_only_u_freshness(self):
        self.assertEqual(ng._category_floor("u-freshness"), ng._u_cadence())
        for cat in ("partition-audit", "release-gap", "queue-arrival",
                    "lane-occupancy"):
            self.assertEqual(ng._category_floor(cat), 0)

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

    def test_family_gap_defers_a_DIFFERENT_category(self):
        st = {}
        ng.mark_sent(st, "sess-a", "lane-occupancy", NOW)
        # a DIFFERENT category within the family gap is deferred (the burst fix)
        self.assertFalse(ng.gate_ok(st, "sess-a", "release-gap", NOW + 60))
        # past the family gap it is allowed
        self.assertTrue(ng.gate_ok(st, "sess-a", "release-gap",
                                   NOW + ng._family_gap()))

    def test_family_gap_defers_at_59_min_913(self):
        """#913: a second DIFFERENT-family nudge at +59 min must be deferred —
        the owner's hard 1×/hour cross-family strop."""
        st = {}
        ng.mark_sent(st, "sess-a", "partition-audit", NOW)
        self.assertFalse(ng.gate_ok(st, "sess-a", "u-freshness",
                                    NOW + 59 * 60))
        # at exactly 1 h it passes
        self.assertTrue(ng.gate_ok(st, "sess-a", "u-freshness", NOW + HOUR))

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

    def test_future_skewed_ts_fails_safe_to_allow(self):
        # a FUTURE / corrupt-huge ts must NOT permanently mute the session (a
        # clamp-to-now would defer FOREVER, since a re-read future ts re-clamps
        # to now every call — the worst direction for the owner's ONLY question
        # surface). It is ignored → the gate ALLOWS, and the next mark_sent
        # overwrites it with `now`. Both the per-category floor AND the family
        # gap must self-heal this way.
        st = {"nudge_cadence": {"s": {"u-freshness": NOW + 10 ** 9}}}
        self.assertTrue(ng.gate_ok(st, "s", "u-freshness", NOW))   # own floor
        self.assertTrue(ng.gate_ok(st, "s", "release-gap", NOW))   # family gap


class TestWorkDrivingPriority923(unittest.TestCase):
    """#923: work-driving families (lane-occupancy, release-gap, queue-arrival,
    lane-reconcile) have PRIORITY over audit families (partition-audit,
    u-freshness, goal-guard) within the shared 1h slot. An audit family must
    NOT claim the slot when a work-driving family is due."""

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

    def test_audit_defers_when_work_driving_is_due(self):
        """The gk starvation shape: audit fired recently, work-driving is
        old/due. Audit must defer so work-driving gets the next slot."""
        st = {}
        ng.mark_sent(st, "s", "lane-occupancy", NOW - 2 * HOUR)
        ng.mark_sent(st, "s", "partition-audit", NOW)
        # After 1h: lane-occupancy (work-driving) should pass
        self.assertTrue(ng.gate_ok(st, "s", "lane-occupancy", NOW + HOUR))
        # partition-audit (audit) must DEFER — work-driving hasn't had its turn
        self.assertFalse(ng.gate_ok(st, "s", "partition-audit", NOW + HOUR))

    def test_audit_allowed_after_work_driving_fires(self):
        """After work-driving fires, audit gets the next slot."""
        st = {}
        ng.mark_sent(st, "s", "lane-occupancy", NOW)
        # After 1h: audit should be allowed (work-driving had its turn)
        self.assertTrue(ng.gate_ok(st, "s", "partition-audit", NOW + HOUR))

    def test_audit_allowed_when_no_work_driving_history(self):
        """When work-driving has NEVER fired for this session, audit doesn't
        defer — there is nothing to yield to."""
        st = {}
        ng.mark_sent(st, "s", "partition-audit", NOW)
        self.assertTrue(ng.gate_ok(st, "s", "partition-audit", NOW + HOUR))

    def test_work_driving_unaffected_by_priority_check(self):
        """Work-driving categories are NEVER deferred by the priority check —
        only the existing (a)+(b) checks apply."""
        st = {}
        ng.mark_sent(st, "s", "partition-audit", NOW)
        # work-driving should pass regardless of audit history
        self.assertTrue(ng.gate_ok(st, "s", "lane-occupancy", NOW + HOUR))

    def test_alternation_over_three_windows(self):
        """Work-driving and audit alternate slots, never starving either."""
        st = {}
        # Window 1: work-driving fires
        ng.mark_sent(st, "s", "lane-occupancy", NOW)
        # Window 2: audit gets its turn
        self.assertTrue(ng.gate_ok(st, "s", "u-freshness", NOW + HOUR))
        ng.mark_sent(st, "s", "u-freshness", NOW + HOUR)
        # Window 3: work-driving gets priority again (audit fired last)
        self.assertFalse(ng.gate_ok(st, "s", "partition-audit",
                                    NOW + 2 * HOUR))
        self.assertTrue(ng.gate_ok(st, "s", "release-gap", NOW + 2 * HOUR))

    def test_starvation_shape_audit_monopoly(self):
        """RED repro of the gk incident: audit claims 3+ consecutive slots,
        lane-occupancy defers every time."""
        st = {}
        # Initial: lane-occupancy was active but audit took over
        ng.mark_sent(st, "s", "lane-occupancy", NOW - 5 * HOUR)
        for i in range(3):
            t = NOW + i * HOUR
            # Audit claims the slot
            ng.mark_sent(st, "s", "partition-audit", t)
        # After 3 hours of audit monopoly, lane-occupancy must be allowed
        # and audit must defer
        t_check = NOW + 3 * HOUR
        self.assertTrue(ng.gate_ok(st, "s", "lane-occupancy", t_check))
        self.assertFalse(ng.gate_ok(st, "s", "partition-audit", t_check))

    def test_quiescent_wd_does_not_permanently_mute_audit(self):
        """#923 review C1: when work-driving fires once then goes quiet
        (lanes full, no more refill needed), audit must NOT be permanently
        muted. The deferral is BOUNDED: audit yields at most one extra
        window (2x family_gap from the last overall nudge), then proceeds.
        Permanent mute of u-freshness is the module's cardinal sin
        (docstring lines 147-158)."""
        st = {}
        # Work-driving fires at T0, then never fires again (lanes full)
        ng.mark_sent(st, "s", "lane-occupancy", NOW)
        # Audit fires at T0+1h
        ng.mark_sent(st, "s", "partition-audit", NOW + HOUR)
        # At T0+2h: audit yields one window for work-driving
        self.assertFalse(ng.gate_ok(st, "s", "u-freshness", NOW + 2 * HOUR))
        # At T0+3h: work-driving didn't fire — audit MUST proceed (bounded)
        self.assertTrue(ng.gate_ok(st, "s", "u-freshness", NOW + 3 * HOUR))
        # Also verify partition-audit is allowed at T0+3h
        self.assertTrue(ng.gate_ok(st, "s", "partition-audit",
                                   NOW + 3 * HOUR))


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
