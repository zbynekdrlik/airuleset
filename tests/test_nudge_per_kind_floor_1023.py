"""#1023 — the shared `nudge_gate` has a per-pane-per-KIND 60-min floor: a
machine nudge of kind K to sid P is suppressed when the last CONFIRMED delivery
of kind K to P is younger than `NUDGE_MIN_INTERVAL_S = 3600`. Per-job floors
below the 60-min floor are deleted.

#1023 fix-forward: ON TOP of the per-kind floor, the #913 cross-kind TOTAL cap
is RESTORED (`NUDGE_TOTAL_GAP_S`, a NEW symbol) — a priority nudge of ANY kind
is held while ANY OTHER priority kind was delivered within the total gap (the
owner's "raz za hodinu … a ani iny nudge do promptu"). So a same-kind repeat at
45 min is held by the FLOOR, and a DIFFERENT kind at 45 min is held by the TOTAL
CAP; both are allowed past the hour.
"""
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import nudge_gate as ng  # noqa: E402

NOW = 1_000_000
MIN45 = 45 * 60
HOUR = 3600


class TestPerKindFloor(unittest.TestCase):
    def test_min_interval_default_and_floor_clamp(self):
        self.assertEqual(ng.NUDGE_MIN_INTERVAL_S, HOUR)
        # env can only RAISE (floor-clamp lesson #504/#543)
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_MIN_INTERVAL_S": "5"}):
            self.assertEqual(ng._min_interval(), HOUR)
        with m.patch.dict(os.environ,
                          {"AIRULESET_NUDGE_MIN_INTERVAL_S": str(2 * HOUR)}):
            self.assertEqual(ng._min_interval(), 2 * HOUR)

    def test_every_gated_kind_has_the_60min_floor(self):
        # the floor covers EVERY machine nudge, not just u-freshness/goal-guard
        for cat in ng.GATED_CATEGORIES:
            self.assertGreaterEqual(ng._category_floor(cat), HOUR,
                                    "%s must carry the 60-min floor" % cat)

    def test_goal_guard_keeps_its_longer_24h_floor(self):
        self.assertEqual(ng._category_floor("goal-guard"), ng.GOAL_GUARD_FLOOR_S)
        self.assertGreater(ng.GOAL_GUARD_FLOOR_S, HOUR)

    def test_same_kind_at_45min_is_suppressed(self):
        st = {}
        ng.mark_sent(st, "sess-a", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + MIN45),
                         "a SAME-kind nudge within 60 min must be suppressed")
        # #1023 fix-forward 2 (owner "3", 2026-09-17): the 3 h total cap now
        # counts the SAME kind too, so past the 60-min floor the repeat is STILL
        # held (by the total cap) — allowed only past the 3 h total gap.
        self.assertFalse(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + HOUR),
                         "at 1 h the same kind is held by the 3 h total cap")
        self.assertTrue(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + 181 * 60))

    def test_different_kind_at_45min_is_suppressed_by_total_cap(self):
        # #1023 fix-forward: a DIFFERENT kind at 45 min IS suppressed by the
        # restored cross-kind TOTAL cap (a queue-arrival delivery holds a
        # lane-occupancy nudge for the total gap); at 61 min it is STILL held
        # (the owner's 3 h total cap, 2026-09-17), at 181 min it is allowed.
        st = {}
        ng.mark_sent(st, "sess-a", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "sess-a", "lane-occupancy", NOW + MIN45),
                         "a DIFFERENT kind IS held by the cross-kind total cap")
        self.assertFalse(ng.gate_ok(st, "sess-a", "lane-occupancy", NOW + 61 * 60))
        self.assertTrue(ng.gate_ok(st, "sess-a", "lane-occupancy", NOW + 181 * 60))

    def test_old_family_gap_symbols_stay_removed(self):
        # the fix-forward uses a NEW name (NUDGE_TOTAL_GAP_S); the pre-#1023
        # symbols stay deleted, not resurrected.
        self.assertFalse(hasattr(ng, "NUDGE_FAMILY_GAP_S"))
        self.assertFalse(hasattr(ng, "_family_gap"))


if __name__ == "__main__":
    unittest.main()
