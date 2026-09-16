"""#1041 — the job-20 goal-lane RIDERS the ticket names (dispatchable / reconcile /
deploy_state·ops-wait) must respect the SAME sweep budget the queue-arrival rider
already does (#1023), reusing job 20's existing `_budget_left_fn` seam — NOT a new
one. Each skips with `hold:budget` + UNTOUCHED state when too little sweep budget
remains, so a rider fetch never runs the sweep into the unit's 120s kill.

The dispatchable guard is CACHE-AWARE (skips only when the 5-min --count-dispatchable
subprocess would actually MISS the cache and fire), so a normal cache-HIT sweep never
defers the refill nudge — the key non-over-defer property.

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


def _iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


class TestDispatchableBudget(unittest.TestCase):
    CWD = "/c"

    def _decide(self, state, budget_left, calls):
        spy = lambda cwd: (calls.append(cwd), [{"count": 3}])[1]  # noqa: E731
        return goal._lane_dispatchable_decision(
            spy, self.CWD, state, 1000, "loc", 1, 0, 5,
            budget_left_fn=(lambda: budget_left))

    def test_cache_miss_low_budget_holds_and_does_not_fetch(self):
        calls = []
        skip, log, cand = self._decide({}, 5, calls)   # empty state => cache MISS
        self.assertTrue(skip)
        self.assertIn("hold:budget", log)
        self.assertEqual(calls, [], "no --count-dispatchable subprocess at 5s budget")

    def test_cache_miss_ample_budget_fetches(self):
        calls = []
        skip, log, cand = self._decide({}, 200, calls)
        self.assertEqual(calls, [self.CWD], "an ample budget must run the fetch")
        self.assertNotIn("hold:budget", log or "")
        self.assertEqual(cand, 3)

    def test_cache_hit_low_budget_still_uses_cache_no_hold(self):
        # a FRESH cache entry => the guard must NOT defer (microsecond read), so the
        # refill nudge is never crippled on a normal sweep even at low budget.
        state = {"dispatchable_cache": {self.CWD: {"ts": 1000, "members": [{"count": 4}]}}}
        calls = []
        skip, log, cand = self._decide(state, 5, calls)
        self.assertEqual(calls, [], "a cache hit does not fetch")
        self.assertFalse(skip)
        self.assertIsNone(log)
        self.assertEqual(cand, 4)

    def test_unmeasurable_budget_applies_no_guard(self):
        calls = []
        skip, log, cand = self._decide({}, None, calls)  # budget_left_fn returns None
        self.assertEqual(calls, [self.CWD])
        self.assertEqual(cand, 3)


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


if __name__ == "__main__":
    unittest.main()
