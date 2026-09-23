"""#1041 — the job-20 goal-lane RIDERS the ticket names (dispatchable / reconcile /
deploy_state·ops-wait) must respect the SAME sweep budget the queue-arrival rider
already does (#1023), reusing job 20's existing `_budget_left_fn` seam — NOT a new
one. Each skips with `hold:budget` + UNTOUCHED state when too little sweep budget
remains, so a rider fetch never runs the sweep into the unit's 120s kill.

#1067 slice 1d REMOVED the dispatchable rider's guard: its fetch no longer runs the
blocking `--count-dispatchable` subprocess (it reads the detached quals snapshot), so
there is no sweep budget to protect. `TestDispatchableNoBudgetGuard` locks that a
low-budget cache MISS still reads (never `hold:budget`).

RED against the pre-fix tree: the three riders take no `budget_left_fn` and fetch
regardless of the remaining budget.
"""
import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd                       # noqa: E402
import watchdog.goal as goal                # noqa: E402
from watchdog import lane_reconcile         # noqa: E402
from watchdog import ops_wait_recheck as owr  # noqa: E402
from watchdog import deploy_state as ds     # noqa: E402

# The riders measure sweep budget against job 20's tail_deadline (110s); the unit's
# systemd kill is at 120s (TimeoutStartSec=2min). A start permitted at the min
# boundary + that rider's worst bounded fetch must finish before the kill.
_RIDER_REF_S = 110
_KILL_S = 120
_GH_FETCH_S = 15   # a single gh timeline read (the rider's own union fetch)


def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


class TestDispatchableNoBudgetGuard(unittest.TestCase):
    """#1067 slice 1d: the dispatchable fetch is a non-blocking snapshot read, so
    the former #1041 `hold:budget` guard is gone — a cache MISS still reads, and
    the decision takes no budget parameter."""
    CWD = "/c"

    def test_cache_miss_still_reads(self):
        calls = []
        spy = lambda cwd: (calls.append(cwd), [{"count": 3}])[1]  # noqa: E731
        skip, log, cand = goal._lane_dispatchable_decision(
            spy, self.CWD, {}, 1000, "loc", 1, 0, 5)   # empty state => MISS
        self.assertEqual(calls, [self.CWD])
        self.assertNotIn("hold:budget", log or "")
        self.assertEqual(cand, 3)

    def test_the_guard_constant_and_parameter_are_gone(self):
        import inspect
        self.assertFalse(hasattr(goal, "DISPATCHABLE_FETCH_MIN_BUDGET_S"))
        self.assertNotIn("budget_left_fn", inspect.signature(
            goal._lane_dispatchable_decision).parameters)
        self.assertNotIn("budget_left_fn", inspect.signature(
            goal.goal_lane_occupancy_nudge).parameters)


class TestReconcileBudget(unittest.TestCase):
    SID = "sess-1041rec"
    CWD = "/home/gatekeeper/devel/x"

    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)

    def _tpath(self, compaction_epoch):
        p = Path(self._td.name) / (self.SID + ".jsonl")
        lines = [{"type": "assistant", "message": {"id": "m1", "content": "hi"}},
                 {"type": "user", "isCompactSummary": True,
                  "timestamp": _iso(compaction_epoch),
                  "message": {"content": "compact summary"}}]
        p.write_text("\n".join(json.dumps(e) for e in lines) + "\n")
        return p

    def _run(self, budget_left, calls):
        now = time.time()
        tpath = self._tpath(now - 60)          # a fresh observed compaction
        spy = lambda cwd: (calls.append(cwd), [("worktree-agent-a", 7, "t")])[1]  # noqa: E731
        state = {}
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "nudges_enabled", lambda kind=None: True), \
                m.patch.object(wd, "send_verified", lambda *a, **k: True), \
                m.patch.object(wd, "_janitor_mark_watch", lambda *a, **k: None):
            return lane_reconcile.goal_lane_reconcile_recheck(
                now, object(), state.setdefault("lane_reconcile", {}), self.SID,
                self.CWD, "%9", tpath, "loc", False, set(),
                reconcile_fetch=spy, state=state,
                budget_left_fn=(lambda: budget_left)), state

    def test_low_budget_holds_and_does_not_fetch(self):
        calls = []
        logs, state = self._run(5, calls)
        self.assertTrue(any("lane-reconcile" in ln and "hold:budget" in ln
                            for ln in logs), logs)
        self.assertEqual(calls, [], "no reconcile git fetch at 5s budget")
        # dedup anchor untouched
        self.assertEqual(state.get("lane_reconcile", {}).get(self.SID, {})
                         .get("last_reconcile_ts"), None)

    def test_ample_budget_fetches(self):
        calls = []
        logs, _state = self._run(200, calls)
        self.assertEqual(calls, [self.CWD],
                         "an ample budget must run the reconcile fetch\n"
                         + "\n".join(logs))


class TestOpsWaitBudget(unittest.TestCase):
    SID = "sess-1041ow"
    CWD = "/home/gatekeeper/devel/y"

    def _run(self, budget_left, ow_calls, ds_calls, state=None):
        state = state if state is not None else {}
        ow = lambda cwd: (ow_calls.append(cwd), [1, 2])[1]  # noqa: E731
        ds = lambda cwd: (ds_calls.append(cwd), {"behind": []})[1]  # noqa: E731
        return owr.goal_ops_wait_recheck(
            1000, object(), state.setdefault("ops_wait_recheck", {}), self.SID,
            self.CWD, "%9", "/t", "loc", False, set(),
            ops_wait_fetch=ow, state=state, i_count=0,
            deploy_state_fetch=ds, budget_left_fn=(lambda: budget_left)), state

    def test_low_budget_holds_and_does_not_fetch(self):
        ow_calls, ds_calls = [], []
        logs, _state = self._run(5, ow_calls, ds_calls)   # empty state => caches MISS
        self.assertTrue(any("ops-wait-recheck" in ln and "hold:budget" in ln
                            for ln in logs), logs)
        self.assertEqual(ow_calls, [], "no ops-wait gh fetch at 5s budget")
        self.assertEqual(ds_calls, [], "no deploy-state fetch at 5s budget")

    def test_ample_budget_fetches(self):
        ow_calls, ds_calls = [], []
        logs, _state = self._run(200, ow_calls, ds_calls)
        self.assertEqual(ow_calls, [self.CWD],
                         "an ample budget must run the ops-wait fetch\n"
                         + "\n".join(logs))

    def test_boundary_just_below_min_holds_just_above_runs(self):
        # #1041 review-2 🟡-2 — lock the ACTUAL threshold value: a revert of the min
        # would flip one of these. Budget is measured against the 110 ref, but the
        # rider is handed `budget_left_fn` directly here, so pass the remaining value.
        below = owr.OPS_WAIT_FETCH_MIN_BUDGET_S - 1
        above = owr.OPS_WAIT_FETCH_MIN_BUDGET_S + 1
        ow_below, _ = self._run(below, [], [])[0], None
        self.assertTrue(any("hold:budget" in ln for ln in ow_below),
                        "budget just BELOW the min must hold: %r" % ow_below)
        ow_calls = []
        logs, _state = self._run(above, ow_calls, [])
        self.assertEqual(ow_calls, [self.CWD],
                         "budget just ABOVE the min must run the fetch: %r" % logs)


class TestDeployFetchWallClockBound(unittest.TestCase):
    """#1041 review-2 🟡-1 — fetch_deploy_state's per-instance loop must STOP at its
    total wall-clock budget so a multi-instance repo (odoo-erp: 3) cannot sum
    unbounded network into the sweep."""

    def test_multi_instance_loop_stops_near_budget(self):
        clock = {"t": 0.0}
        calls = []

        def _slow_read(vs, timeout=15):
            calls.append(vs)
            clock["t"] += 15.0     # each per-instance HTTP GET "takes" 15s
            return "1.0.0"

        reg_project = {"deploy_state": {
            "main_version_file": "v",
            "instances": [{"name": str(i), "version_source": "u%d" % i}
                          for i in range(5)]}}
        with m.patch.object(ds, "_load_registry", return_value=[reg_project]), \
                m.patch.object(ds, "_find_project", return_value=reg_project), \
                m.patch.object(ds, "read_main_version", return_value="1.0.0"), \
                m.patch.object(ds, "read_prod_version", _slow_read):
            res = ds.fetch_deploy_state(
                "/x", time_fn=(lambda: clock["t"]),
                budget_s=ds.DEPLOY_STATE_FETCH_BUDGET_S)
        self.assertGreaterEqual(len(calls), 1, "at least one instance is always read")
        self.assertLess(len(calls), 5,
                        "the loop must STOP at the budget, not read all 5 instances")
        self.assertEqual(len(res), len(calls), "results match the instances reached")


class TestBudgetValueLocks(unittest.TestCase):
    """#1041 review-2 🟡-2 — regression-lock each rider min VALUE against its own
    worst bounded fetch, so a revert (e.g. ops_wait min 55→20) or a deploy-budget bump
    fails LOUDLY here rather than silently passing the 5/200-budget behaviour tests."""

    def test_ops_wait_min_covers_union_plus_bounded_deploy(self):
        # deploy fetch worst ≈ read_main(git 10) + loop(budget + one 15s overshoot)
        deploy_worst = 10 + ds.DEPLOY_STATE_FETCH_BUDGET_S + _GH_FETCH_S
        combined = _GH_FETCH_S + deploy_worst           # union THEN deploy
        start_at = _RIDER_REF_S - owr.OPS_WAIT_FETCH_MIN_BUDGET_S
        self.assertLessEqual(
            start_at + combined, _KILL_S,
            "ops_wait min=%d too low: a start at elapsed %d + %ds worst fetch = %ds "
            "> the %ds kill (review-2 🟡-1/🟡-2)"
            % (owr.OPS_WAIT_FETCH_MIN_BUDGET_S, start_at, combined,
               start_at + combined, _KILL_S))

    def test_reconcile_min_covers_its_git_budget(self):
        reconcile_worst = 40                            # its own BUDGET_S
        start_at = _RIDER_REF_S - lane_reconcile.RECONCILE_FETCH_MIN_BUDGET_S
        self.assertLessEqual(start_at + reconcile_worst, _KILL_S,
                             "reconcile min too low vs its 40s git budget")


if __name__ == "__main__":
    unittest.main()
