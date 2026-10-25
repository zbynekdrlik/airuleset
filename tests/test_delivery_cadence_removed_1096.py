"""#1096 (architecture-rework) — the DEAD delivery-cadence machinery left behind
when #1089 retired the `lane-occupancy` keystroke DELIVERY stays REMOVED; #1137
(owner 25.10.2026) then removed the lane-occupancy branch itself together with
`watchdog/lane_resources.py`. Locks: the deleted symbols stay absent from
`watchdog.goal` and `watchdog.one_glance`, and the KEPT surface (the #1092
pane-budget gate, the stuck alert, the orphan prune, the account-block decision)
stays.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog.goal as goal  # noqa: E402
import watchdog.one_glance as og  # noqa: E402
import watchdog.nudge_gate as ng  # noqa: E402


# The delivery-cadence symbols #1089 orphaned — must be GONE (goal.py).
_GOAL_DEAD_SYMBOLS = (
    "_lane_record_nudge",
    "_lane_cooldown_decision",
    "_lane_giveup_decision",
    "_lane_giveup_cause",
    "_lane_giveup_backoff",
    "_lane_count_giveup_reset",
    "_lane_stash_abort_backoff",
    "_lane_pre_send_race",  # #486 pre-send race gate — orphaned since #1089 retired delivery
    "GOAL_LANE_NUDGE_TEXT_FN",
    # orphaned cadence constants (only consumer was a deleted helper)
    "GOAL_LANE_STARVED_INTERVAL_S",
    "GOAL_LANE_STARVED_MAX_CONSECUTIVE",
    "GOAL_LANE_MAX_NUDGES",
    "GOAL_LANE_GIVEUP_BACKOFF_S",
    "GOAL_LANE_GIVEUP_CACHE_MAX_AGE_S",
    "GOAL_LANE_MAX_STASH_ABORTS",
    "GOAL_LANE_STASH_ABORT_BACKOFF_S",
)

# one_glance.py — the give-up cause decider (sole consumer `_lane_giveup_cause`).
_OG_DEAD_SYMBOLS = ("lane_giveup_cause_decision",)


class TestDeadCadenceSymbolsAbsent(unittest.TestCase):
    def test_goal_module_has_no_dead_symbol(self):
        for name in _GOAL_DEAD_SYMBOLS:
            self.assertFalse(hasattr(goal, name),
                             "watchdog.goal must not define %s (dead #1089 "
                             "delivery-cadence machinery)" % name)

    def test_one_glance_has_no_dead_symbol(self):
        for name in _OG_DEAD_SYMBOLS:
            self.assertFalse(hasattr(og, name),
                             "watchdog.one_glance must not define %s" % name)

    def test_no_dead_symbol_defined_in_source(self):
        # a `def <sym>(`/`<SYM> =` definition, not a passing mention in a
        # comment — the resurrect this lock forbids.
        goal_src = Path(goal.__file__).read_text()
        og_src = Path(og.__file__).read_text()
        for name in _GOAL_DEAD_SYMBOLS:
            self.assertNotIn("def %s(" % name, goal_src)
            self.assertNotIn("\n%s =" % name, goal_src)
        for name in _OG_DEAD_SYMBOLS:
            self.assertNotIn("def %s(" % name, og_src)


class TestKeptSurface(unittest.TestCase):
    def test_pane_budget_gate_kept(self):
        # the #1092 pane-budget keystroke gate is UNTOUCHED.
        self.assertTrue(hasattr(ng, "PANE_ATTEMPT_BUDGET"))
        self.assertTrue(hasattr(ng, "pane_budget_ok"))

    def test_observation_helpers_kept(self):
        for name in ("_lane_stuck_owner_alert", "_prune_goal_lane_orphans",
                     "_account_limit_decision", "goal_account_limit_check"):
            self.assertTrue(hasattr(goal, name),
                            "observation helper %s must be kept" % name)


if __name__ == "__main__":
    unittest.main()
