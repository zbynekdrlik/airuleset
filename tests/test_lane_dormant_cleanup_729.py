"""#729 — the dormant job-20 lane-nudge machinery (#726 keep-dormant) is REMOVED.

#726 reversed the fleet doctrine to BATCH mode, which made two subsystems that
shaped the RETIRED under-saturated fill nudge structurally unreachable from the
nudge:

  * the memory OOM gate (`_mem_available_mb` / `_lane_min_mem_avail_mb` /
    `_lane_lowmem_skip` / `_lane_lowmem_reset` / `GOAL_LANE_MIN_MEM_AVAIL_MB` /
    `GOAL_LANE_LOWMEM_SURFACE_STREAK` / `one_glance.lane_low_mem_surface_decision`),
    and
  * the #509 effectiveness backoff (`_lane_effectiveness` /
    `_lane_effective_interval` / `GOAL_LANE_INEFFECTIVE_BACKOFF_S` /
    `_lane_clear_effectiveness`, and the `under_saturated`/`moved` branches of
    `_lane_cooldown_decision` / `_lane_record_nudge`).

The #729 memory DECISION (see the design comment): the empty-lane batch-start
nudge does NOT gate on memory headroom (it stays memory-EXEMPT — a fully stalled
box must always be nudged, and the supervisor already backs off on a real memory
signal WITHIN a batch), so the whole OOM subsystem is DELETED, not re-wired.

These are mutation-locks for the removed state — RED while the machinery is
present, GREEN once it is gone.
"""

import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog.goal as goal            # noqa: E402
from watchdog import one_glance as og   # noqa: E402


class MemoryOomSubsystemRemoved(unittest.TestCase):
    def test_goal_module_memory_helpers_are_gone(self):
        for name in ("_mem_available_mb", "_lane_min_mem_avail_mb",
                     "_lane_lowmem_skip", "_lane_lowmem_reset"):
            self.assertFalse(hasattr(goal, name),
                             "goal.%s must be removed (#729)" % name)

    def test_goal_module_memory_constants_are_gone(self):
        for name in ("GOAL_LANE_MIN_MEM_AVAIL_MB", "GOAL_LANE_LOWMEM_SURFACE_STREAK"):
            self.assertFalse(hasattr(goal, name),
                             "goal.%s must be removed (#729)" % name)

    def test_one_glance_low_mem_decider_is_gone(self):
        self.assertFalse(hasattr(og, "lane_low_mem_surface_decision"),
                         "one_glance.lane_low_mem_surface_decision removed (#729)")
        self.assertFalse(hasattr(og, "LaneLowMemSurface"),
                         "one_glance.LaneLowMemSurface removed (#729)")

    def test_no_memory_env_knob_left_in_goal_source(self):
        # The AIRULESET_LANE_MIN_MEM_MB env override went with the subsystem.
        self.assertNotIn("AIRULESET_LANE_MIN_MEM_MB", inspect.getsource(goal))


class EffectivenessBackoffRemoved(unittest.TestCase):
    def test_effectiveness_helpers_and_constant_are_gone(self):
        for name in ("_lane_effectiveness", "_lane_effective_interval",
                     "_lane_clear_effectiveness", "GOAL_LANE_INEFFECTIVE_BACKOFF_S"):
            self.assertFalse(hasattr(goal, name),
                             "goal.%s must be removed (#729)" % name)


# #1096: the `_lane_cooldown_decision` / `_lane_record_nudge` signature +
# behaviour tests that lived here are DELETED — those helpers (the whole lane
# delivery-cadence machinery) were removed once #1089 retired the lane-occupancy
# keystroke DELIVERY they gated. Their absence is now locked by
# tests/test_delivery_cadence_removed_1096.py.


if __name__ == "__main__":
    unittest.main()
