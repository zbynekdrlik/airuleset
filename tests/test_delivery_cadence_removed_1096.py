"""#1096 (architecture-rework) — the DEAD delivery-cadence machinery left behind
when #1089 retired the `lane-occupancy` keystroke DELIVERY is REMOVED, while the
lane-occupancy DECISION line and every observation helper are KEPT.

Root cause (#1089 F-1/F-3): the `lane-occupancy` nudge no longer types a
keystroke (the `gates/lanefill.py` Stop gate is the refill lever now, #1092), so
the give-up / cooldown / abort-backoff cadence gates and their helpers — which
only ever governed WHEN to re-DELIVER / when to give up on delivery — became
uncalled production code with ~40 gate/skip tests asserting behaviour no live
path reaches (false coverage).

Two locks:
  1. STRUCTURAL — the deleted symbols are absent from `watchdog.goal`,
     `watchdog.lane_resources`, and `watchdog.one_glance` (both as attributes and
     in the module source), so a future edit can never resurrect the dead path.
  2. BEHAVIOURAL — the `lane-occupancy … -> would-refill; DELIVERY RETIRED`
     decision line STILL journals on a fixture sweep of `goal_lane_occupancy_nudge`
     (observability preserved — the #1089 mandate). The KEPT `#1092` pane-budget
     gate (`nudge_gate.PANE_ATTEMPT_BUDGET`) is still present.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog.goal as goal  # noqa: E402
import watchdog.lane_resources as lr  # noqa: E402
import watchdog.one_glance as og  # noqa: E402
import watchdog.nudge_gate as ng  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_ARMED_CAP, _write_marker_transcript)


# The delivery-cadence symbols #1089 orphaned — must be GONE (goal.py).
_GOAL_DEAD_SYMBOLS = (
    "_lane_record_nudge",
    "_lane_cooldown_decision",
    "_lane_giveup_decision",
    "_lane_giveup_cause",
    "_lane_giveup_backoff",
    "_lane_count_giveup_reset",
    "_lane_stash_abort_backoff",
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

# lane_resources.py — the nudge-text renderer + its now-orphaned usage reader.
_LR_DEAD_SYMBOLS = ("_lane_nudge_text", "count_resource_usage")

# one_glance.py — the give-up cause decider (sole consumer `_lane_giveup_cause`).
_OG_DEAD_SYMBOLS = ("lane_giveup_cause_decision",)


class TestDeadCadenceSymbolsAbsent(unittest.TestCase):
    def test_goal_module_has_no_dead_symbol(self):
        for name in _GOAL_DEAD_SYMBOLS:
            self.assertFalse(hasattr(goal, name),
                             "watchdog.goal must not define %s (dead #1089 "
                             "delivery-cadence machinery)" % name)

    def test_lane_resources_has_no_dead_symbol(self):
        for name in _LR_DEAD_SYMBOLS:
            self.assertFalse(hasattr(lr, name),
                             "watchdog.lane_resources must not define %s" % name)

    def test_one_glance_has_no_dead_symbol(self):
        for name in _OG_DEAD_SYMBOLS:
            self.assertFalse(hasattr(og, name),
                             "watchdog.one_glance must not define %s" % name)

    def test_no_dead_symbol_defined_in_source(self):
        # a `def <sym>(`/`<SYM> =` definition, not a passing mention in a
        # comment — the resurrect this lock forbids.
        goal_src = Path(goal.__file__).read_text()
        lr_src = Path(lr.__file__).read_text()
        og_src = Path(og.__file__).read_text()
        for name in _GOAL_DEAD_SYMBOLS:
            self.assertNotIn("def %s(" % name, goal_src)
            self.assertNotIn("\n%s =" % name, goal_src)
        for name in _LR_DEAD_SYMBOLS:
            self.assertNotIn("def %s(" % name, lr_src)
        for name in _OG_DEAD_SYMBOLS:
            self.assertNotIn("def %s(" % name, og_src)

    def test_nudge_never_references_a_dead_cadence_helper(self):
        # AST: `goal_lane_occupancy_nudge` must not call the removed cadence
        # helpers (the parallel delivery-cadence path is gone).
        import ast
        import inspect
        src = inspect.getsource(goal.goal_lane_occupancy_nudge)
        tree = ast.parse(src.lstrip())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        for banned in ("_lane_record_nudge", "_lane_cooldown_decision",
                       "_lane_giveup_decision", "_lane_count_giveup_reset"):
            self.assertNotIn(banned, names,
                             "goal_lane_occupancy_nudge must not call %s" % banned)


class TestKeptSurface(unittest.TestCase):
    def test_pane_budget_gate_kept(self):
        # the #1092 pane-budget keystroke gate is UNTOUCHED.
        self.assertTrue(hasattr(ng, "PANE_ATTEMPT_BUDGET"))
        self.assertTrue(hasattr(ng, "pane_budget_ok"))

    def test_observation_helpers_kept(self):
        for name in ("_lane_wnt_gate", "_lane_effective_min_backlog",
                     "_lane_boundary_ok", "_lane_dispatchable_decision",
                     "_lane_stuck_owner_alert", "_prune_goal_lane_orphans",
                     "_account_limit_decision", "GOAL_LANE_INTERVAL_S",
                     "GOAL_LANE_MIN_BACKLOG"):
            self.assertTrue(hasattr(goal, name),
                            "observation helper %s must be kept" % name)


class TestDeliveryRetiredDecisionLineStillJournals(unittest.TestCase):
    """The lane-occupancy DECISION line survives the deletion: an idle, armed,
    under-filled pane with a workable backlog journals the
    `would-refill; DELIVERY RETIRED` line (the #1089 observability the owner
    reads), even though no keystroke is typed."""
    CWD = "/home/newlevel/devel/cadence1096"

    def test_decision_line_journals(self):
        now = 1_000_000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100   # idle
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        tpath = _write_marker_transcript(proj.name, self.CWD, "sess-1096")
        sid = tpath.stem
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch("watchdog._goal_autoarm_recent_human_activity",
                     return_value=(False, "")):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, sid, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, Path(proj.name),
                backlog_fetch=lambda cwd: 5, state={}, sleep_fn=lambda s: None)
        self.assertTrue(any("DELIVERY RETIRED" in ln for ln in logs),
                        "the lane-occupancy decision line must still journal: %r"
                        % logs)
        self.assertTrue(any("would-refill" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [],
                         "delivery is retired — no keystroke may be typed")


if __name__ == "__main__":
    unittest.main()
