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

    def test_cooldown_decision_signature_is_simplified(self):
        params = set(inspect.signature(goal._lane_cooldown_decision).parameters)
        for gone in ("under_saturated", "eff_workers"):
            self.assertNotIn(gone, params,
                             "_lane_cooldown_decision must drop %r (#729)" % gone)
        # the hourly-cap + #670 dedup inputs stay
        for kept in ("rec", "now", "backlog_n", "live_workers", "waiters"):
            self.assertIn(kept, params, kept)

    def test_record_nudge_signature_is_simplified(self):
        params = set(inspect.signature(goal._lane_record_nudge).parameters)
        for gone in ("under_saturated", "moved"):
            self.assertNotIn(gone, params,
                             "_lane_record_nudge must drop %r (#729)" % gone)
        for kept in ("rec", "backlog_n", "n", "now"):
            self.assertIn(kept, params, kept)


class CooldownStillDedupsAndCaps(unittest.TestCase):
    """The KEPT behaviour: hourly cap + #670 dedup on the (workers, backlog)
    signature, now the single un-branched cadence gate."""

    NOW = 1_000_000_000.0

    def _decide(self, rec, live_workers, backlog_n):
        return goal._lane_cooldown_decision(
            rec, self.NOW, backlog_n, loc="zbynek:1.0",
            live_workers=live_workers, waiters=1)

    def test_unchanged_state_past_cooldown_is_deduped(self):
        hour = goal.GOAL_LANE_INTERVAL_S
        rec = {"llast": self.NOW - 2 * hour, "lsw": 0, "lsb": 22}
        skip, log = self._decide(rec, live_workers=0, backlog_n=22)
        self.assertTrue(skip)
        self.assertIn("dedup-unchanged", log or "")

    def test_within_cooldown_is_hourly_capped(self):
        hour = goal.GOAL_LANE_INTERVAL_S
        rec = {"llast": self.NOW - hour // 2, "lsw": 0, "lsb": 22}
        skip, log = self._decide(rec, live_workers=0, backlog_n=22)
        self.assertTrue(skip)
        self.assertIn("hourly-cap", log or "")

    def test_changed_state_past_cooldown_still_fires(self):
        hour = goal.GOAL_LANE_INTERVAL_S
        rec = {"llast": self.NOW - 2 * hour, "lsw": 0, "lsb": 22}
        skip, log = self._decide(rec, live_workers=0, backlog_n=21)
        self.assertFalse(skip, log)

    def test_record_then_decide_dedups(self):
        rec = {}
        hour = goal.GOAL_LANE_INTERVAL_S
        goal._lane_record_nudge(rec, live_workers=0, backlog_n=22, n=0,
                                now=self.NOW - 2 * hour)
        self.assertEqual((rec.get("lsw"), rec.get("lsb")), (0, 22))
        skip, log = self._decide(rec, live_workers=0, backlog_n=22)
        self.assertTrue(skip)
        self.assertIn("dedup-unchanged", log or "")


if __name__ == "__main__":
    unittest.main()
