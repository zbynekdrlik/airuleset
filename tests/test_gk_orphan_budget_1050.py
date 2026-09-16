"""#1050 — Job 36 `gk_orphan_marker_sweep` must bound its per-item gh loops by
wall clock so a saturated-box sweep never runs the unit into the 120s
`TimeoutStartSec` kill (gk 2026-09-16 09:14:33 + 15:15:42, both preceded by
`job start: gk_orphan_marker_sweep at 50s / 30s`).

The fix mirrors `watchdog/deploy_state.py::fetch_deploy_state` (#1041 review-2):
a TOTAL wall-clock budget (`_SweepBudget`, the `time_fn`/`budget_s` seam),
checked before every gh op in both fetches AND before every reconcile write,
with a JOB-WIDE single-overshoot guarantee, PLUS the registry `min_budget`
raised to `bound + one in-flight read` (`_BUDGET_MIN_GK_ORPHAN_S`).

RED against the pre-fix tree:
  * `_SweepBudget` / `_GKORPHAN_SWEEP_BUDGET_S` / `_GKORPHAN_GH_READ_S` /
    `_BUDGET_MIN_GK_ORPHAN_S` do not exist yet (ImportError/AttributeError);
  * `gk_orphan_marker_sweep` / `_fetch_gk_orphan_candidates` take no
    `time_fn`/`budget_s`/`budget` param (TypeError);
  * the registry registers Job 36 at `_BUDGET_MIN_GH_BATCH_S` (=25), so it does
    NOT hold at 39s of remaining budget the way the fix (floor 40) must.
"""

import inspect
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd                # noqa: E402
import watchdog.cross_stream as cs   # noqa: E402


# --------------------------------------------------------------------------- #
# A deterministic fake clock: returns successive values, repeating the last.
# --------------------------------------------------------------------------- #
class _Clock:
    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def __call__(self):
        v = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return v


# --------------------------------------------------------------------------- #
# 1. The arithmetic value-lock: start-floor + bound + one in-flight read must
#    stay under the 100s soft cap, and the floor must equal bound + one read.
# --------------------------------------------------------------------------- #
class TestBudgetValueLock(unittest.TestCase):
    def test_constants_exist_and_are_sane(self):
        self.assertIsInstance(cs._GKORPHAN_SWEEP_BUDGET_S, (int, float))
        self.assertIsInstance(cs._GKORPHAN_GH_READ_S, (int, float))
        self.assertIsInstance(wd._BUDGET_MIN_GK_ORPHAN_S, (int, float))
        self.assertGreater(cs._GKORPHAN_SWEEP_BUDGET_S, 0)
        self.assertGreater(cs._GKORPHAN_GH_READ_S, 0)

    def test_min_budget_is_bound_plus_one_read(self):
        self.assertEqual(
            wd._BUDGET_MIN_GK_ORPHAN_S,
            cs._GKORPHAN_SWEEP_BUDGET_S + cs._GKORPHAN_GH_READ_S,
            "min_budget must equal the in-job bound + one in-flight read")

    def test_value_lock_under_soft_cap(self):
        # the ticket's literal lock: min_budget + bound + one read < 100
        total = (wd._BUDGET_MIN_GK_ORPHAN_S
                 + cs._GKORPHAN_SWEEP_BUDGET_S
                 + cs._GKORPHAN_GH_READ_S)
        self.assertLess(total, 100, "min_budget + bound + one read must be < 100s soft cap")

    def test_worst_case_start_completes_under_the_hard_kill(self):
        # A job that STARTS at the latest permitted moment (remaining == min_budget,
        # i.e. elapsed == SOFT_CAP - min_budget) and runs its bound + one overshoot
        # read must finish under BOTH the soft cap and the 120s hard kill.
        latest_start = wd.SWEEP_SOFT_CAP_S - wd._BUDGET_MIN_GK_ORPHAN_S
        worst_finish = (latest_start
                        + cs._GKORPHAN_SWEEP_BUDGET_S
                        + cs._GKORPHAN_GH_READ_S)
        self.assertLessEqual(worst_finish, wd.SWEEP_SOFT_CAP_S)
        self.assertLess(worst_finish, 120)


# --------------------------------------------------------------------------- #
# 2. The `_SweepBudget` primitive — single-overshoot may_start_op + spent().
# --------------------------------------------------------------------------- #
class TestSweepBudgetPrimitive(unittest.TestCase):
    def test_unbounded_never_spends_or_stops(self):
        b = cs._SweepBudget(time_fn=_Clock([0, 1, 2, 3, 4]), budget_s=None)
        for _ in range(10):
            self.assertTrue(b.may_start_op())
            self.assertFalse(b.spent())

    def test_first_op_always_runs_then_deadline_gates(self):
        # deadline = 0 + 3. clock advances 1 per call after __init__.
        b = cs._SweepBudget(time_fn=_Clock([0, 1, 2, 3, 4, 5]), budget_s=3)
        self.assertTrue(b.may_start_op())    # clock 1 — bootstrap, always runs
        self.assertTrue(b.may_start_op())    # clock 2 < 3 — under budget
        self.assertFalse(b.may_start_op())   # clock 3 >= 3 — spent
        self.assertFalse(b.may_start_op())   # clock 4 — stays spent (single overshoot)

    def test_spent_is_a_plain_deadline_check(self):
        b = cs._SweepBudget(time_fn=_Clock([0, 1, 2, 3]), budget_s=2)
        self.assertFalse(b.spent())   # clock 1 < 2
        self.assertTrue(b.spent())    # clock 2 >= 2
        self.assertTrue(b.spent())    # clock 3 >= 2


# --------------------------------------------------------------------------- #
# 3. The JOB's mutated processing loop stops at the bound, leaving the rest
#    untouched, persisting per processed item, and logging one budget line.
# --------------------------------------------------------------------------- #
class _Recorder:
    def __init__(self, status="labeled"):
        self.status = status
        self.calls = []

    def __call__(self, root, num):
        self.calls.append((root, num))
        return self.status


class _SendRec:
    def __init__(self):
        self.calls = []

    def __call__(self, body, dedup_key=None, dry_run=False, project=None):
        self.calls.append((body, dedup_key, project))
        return True


def _orphan(n):
    return {"number": n, "has_mutated": True, "has_proper": False,
            "handoff_flow": False, "currently_labeled": False,
            "ga_title": False, "ever_labeled": False}


NOW = 10 ** 9
ROOT = "/home/gatekeeper/devel/odoo-erp"


class TestMutatedProcessingLoopBudget(unittest.TestCase):
    def _run(self, candidates, budget_s, clock):
        rec = _Recorder()
        persist_calls = []
        st = {}

        def persist():
            persist_calls.append(1)

        with mock.patch.object(wd, "list_claude_panes",
                               lambda *a, **k: []), \
             mock.patch.object(cs, "_cache_repo_roots",
                               lambda *a, **k: {ROOT: "odoo-erp"}):
            logs = cs.gk_orphan_marker_sweep(
                NOW, run=None, state=st, send_fn=_SendRec(), user="newlevel",
                gh_fetch=lambda root, **kw: list(candidates), apply_fn=rec,
                persist=persist, time_fn=clock, budget_s=budget_s)
        return logs, st, rec, persist_calls

    def test_loop_stops_at_bound_rest_untouched_persist_per_item(self):
        cands = [_orphan(n) for n in (100, 101, 102, 103, 104)]
        # clock: __init__ -> 0 (deadline 4); reconcile #1 -> 1 (bootstrap),
        # #2 -> 2 (<4), #3 -> 3 (<4), #4 -> 4 (>=4 -> stop). => 3 reconciled.
        logs, st, rec, persist_calls = self._run(
            cands, budget_s=4, clock=_Clock([0, 1, 2, 3, 4, 5, 6]))
        self.assertEqual(rec.calls,
                         [(ROOT, 100), (ROOT, 101), (ROOT, 102)],
                         "the loop must reconcile only the items that fit the budget")
        seen = st["gkorphan"]["seen"]
        self.assertIn("odoo-erp#100", seen)
        self.assertIn("odoo-erp#102", seen)
        self.assertNotIn("odoo-erp#103", seen)   # untouched — re-read next sweep
        self.assertNotIn("odoo-erp#104", seen)
        self.assertTrue(any("budget spent after 3/5 items" in ln for ln in logs),
                        "one budget-spent line naming N/M processed\n" + "\n".join(logs))
        # per-item write-through: a persist per reconcile (+ the cadence stamp).
        self.assertGreaterEqual(len(persist_calls), len(rec.calls) + 1)

    def test_ample_budget_reconciles_all(self):
        cands = [_orphan(n) for n in (200, 201, 202)]
        logs, st, rec, _ = self._run(
            cands, budget_s=10 ** 6, clock=_Clock([0, 1, 2, 3, 4, 5]))
        self.assertEqual([c[1] for c in rec.calls], [200, 201, 202])
        self.assertFalse(any("budget spent" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 4. The real fetch's per-candidate gh loop stops at the bound, returns the
#    partial classified set, and logs a budget line.
# --------------------------------------------------------------------------- #
class TestFetchLoopBudget(unittest.TestCase):
    def _fake_run(self, n_candidates):
        rows = [{"number": 1000 + i, "title": "t",
                 "updatedAt": "2026-09-1%dT00:00:00Z" % (i % 9)}
                for i in range(n_candidates)]

        import json as _json

        def fake_run(argv, **kw):
            if "list" in argv:
                return SimpleNamespace(returncode=0, stdout=_json.dumps(rows), stderr="")
            # a per-candidate `gh issue view`: return a PROCESSED (has_proper)
            # ticket so the cheap gate fails and no ever_labeled call fires.
            return SimpleNamespace(
                returncode=0,
                stdout=_json.dumps({"number": 0, "title": "t", "labels": [],
                                    "comments": [{"body": "GATEKEEPER-ACTION: done"}]}),
                stderr="")
        return fake_run

    def test_fetch_stops_after_the_budget_and_returns_partial(self):
        logs = []
        # clock: __init__ -> 0 (deadline 4); search gate -> 1 (bootstrap);
        # view#0 -> 2 (<4), view#1 -> 3 (<4), view#2 -> 4 (>=4 -> stop). => 2 views.
        budget = cs._SweepBudget(time_fn=_Clock([0, 1, 2, 3, 4, 5, 6, 7]), budget_s=4)
        with mock.patch.object(cs, "_gh_env", lambda *a, **k: {}), \
             mock.patch("subprocess.run", self._fake_run(6)):
            out = cs._fetch_gk_orphan_candidates(
                ROOT, home=None, budget=budget, logs=logs)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 2, "only the candidates that fit the budget are classified")
        self.assertTrue(any("budget spent after 2/6 items" in ln for ln in logs),
                        "the fetch must log one budget-spent line\n" + "\n".join(logs))

    def test_unbounded_fetch_classifies_all(self):
        with mock.patch.object(cs, "_gh_env", lambda *a, **k: {}), \
             mock.patch("subprocess.run", self._fake_run(6)):
            out = cs._fetch_gk_orphan_candidates(ROOT, home=None)   # no budget
        self.assertEqual(len(out), 6)


# --------------------------------------------------------------------------- #
# 5. The registry raises Job 36's min_budget to _BUDGET_MIN_GK_ORPHAN_S and it
#    holds:budget below that floor (mirrors the #1041 attribution harness).
# --------------------------------------------------------------------------- #
_BASE = 5000.0
_RNOW = 1_000_000


def _run_once_clock(holder):
    seq = {"n": 0}

    def clock():
        seq["n"] += 1
        return _BASE if seq["n"] == 1 else _BASE + holder["elapsed"]
    return clock


def _run_once(holder, **kwargs):
    with TemporaryDirectory() as d:
        return list(wd.run_once(
            now=_RNOW, dry_run=True, run=lambda *a, **k: "",
            send_fn=lambda *a, **k: None,
            projects_dir=Path(d) / "proj",
            state_path=str(Path(d) / "state.json"),
            time_fn=_run_once_clock(holder),
            **kwargs,
        ))


class TestRegistryMinBudget(unittest.TestCase):
    def _patched(self, holder):
        calls = []

        def _gko(*a, **k):
            calls.append(1)
            return []

        with mock.patch.object(wd, "gk_orphan_marker_sweep", _gko):
            logs = _run_once(holder, gkorphan_fetch=lambda *a, **k: [])
        return calls, logs

    def test_holds_just_below_the_floor(self):
        # remaining == min_budget - 1  => must HOLD (not start the 60-view loop).
        holder = {"elapsed": wd.SWEEP_SOFT_CAP_S - wd._BUDGET_MIN_GK_ORPHAN_S + 1}
        calls, logs = self._patched(holder)
        self.assertEqual(calls, [], "Job 36 must not start below its raised floor\n"
                         + "\n".join(logs))
        self.assertTrue(
            any(ln.startswith("gk_orphan_marker_sweep -> hold:budget") for ln in logs),
            "the skip must journal hold:budget\n" + "\n".join(logs))

    def test_runs_with_ample_budget(self):
        holder = {"elapsed": wd.SWEEP_SOFT_CAP_S - wd._BUDGET_MIN_GK_ORPHAN_S - 5}
        calls, _ = self._patched(holder)
        self.assertEqual(calls, [1], "Job 36 must run with budget above its floor")

    def test_registry_marks_the_job_gh_poll_hold(self):
        # gk_orphan is a heavy READ poller — its rare backstop write is non-urgent,
        # so it must be gh_poll_hold-marked (held during a gh-rate throttle) and
        # carry the raised _BUDGET_MIN_GK_ORPHAN_S floor.
        src = inspect.getsource(wd.run_once)
        m = re.search(r'_add\(\s*"gk_orphan_marker_sweep".*?\n(?:.*?\n){0,20}?'
                      r'.*?"gk-orphan-marker-sweep error"[^)]*\)', src, re.S)
        self.assertIsNotNone(m, "could not locate the gk_orphan _add block")
        block = m.group(0)
        self.assertIn("_BUDGET_MIN_GK_ORPHAN_S", block,
                      "Job 36 must register with the raised floor")
        self.assertIn("gh_poll_hold=True", block,
                      "Job 36 must be gh_poll_hold-marked")


if __name__ == "__main__":
    unittest.main()
