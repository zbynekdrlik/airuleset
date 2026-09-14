"""#1023 — the shared `nudge_gate` becomes a per-pane-per-KIND 60-min floor:
a machine nudge of kind K to sid P is suppressed when the last CONFIRMED
delivery of kind K to P is younger than `NUDGE_MIN_INTERVAL_S = 3600`. The
cross-kind family gap (#913) is REMOVED — different kinds are independent, the
owner controls the total by per-kind staging (default all-OFF). Per-job floors
below the 60-min floor are deleted.

RED against the pre-#1023 tree: `_category_floor` returns 0 for every kind
except u-freshness/goal-guard, and the family gap defers a DIFFERENT kind — so
(a) a same-kind repeat at 45 min is NOT suppressed and (b) a different kind at
45 min IS (wrongly) suppressed. GREEN once the floor is uniform per-kind and the
family gap is gone.
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
        # past the hour it is allowed again
        self.assertTrue(ng.gate_ok(st, "sess-a", "queue-arrival", NOW + HOUR))

    def test_different_kind_at_45min_is_NOT_suppressed(self):
        # the floor is PER KIND — a queue-arrival delivery does not block a
        # lane-occupancy nudge (the family gap is gone).
        st = {}
        ng.mark_sent(st, "sess-a", "queue-arrival", NOW)
        self.assertTrue(ng.gate_ok(st, "sess-a", "lane-occupancy", NOW + MIN45),
                        "a DIFFERENT kind must NOT be suppressed by another "
                        "kind's floor (per-kind independence)")

    def test_family_gap_symbols_removed(self):
        # the cross-kind family gap is deleted, not left as dead code.
        self.assertFalse(hasattr(ng, "NUDGE_FAMILY_GAP_S"))
        self.assertFalse(hasattr(ng, "_family_gap"))


if __name__ == "__main__":
    unittest.main()
