"""#1176 reopen — a deferred fast-forward must never starve.

Live on dev1 (30.9.2026): `spinbike` sat 52 commits behind for 16 h with
`fast-forward deferred (33s of sweep budget left)` on EVERY sweep. Job 53 runs
late in `run_once`, so it never had the `MERGE_RESERVE_S` (60 s) the in-unit
merge needs, and the deferral repeated forever.

Approach 1 (the main's design): when the shared predicate says `ff`, the job
launches the merge as a detached `systemd-run --user --collect` transient unit
(outside the watchdog unit's 120 s kill), running the SAME
`cli_checkout_freshness` fast-forward path with hooks off. The unit writes a
result file; the next sweep collects it into status.json. A running unit
blocks a duplicate launch. Without `systemd-run` the old in-unit merge with
the reserve stays, and a checkout deferred >= 3 sweeps is processed first.

Every test fakes `systemd-run` (the `unit_run` seam): no transient unit is
ever started. The "executing" fake runs the unit's child command synchronously
against REAL temp repos, so the child entry itself is exercised end to end.
"""

import os
import re
import subprocess
import sys
import types
import unittest
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from test_checkout_freshness_1176 import T0, _cf, _job, _Repos  # noqa: E402

LOW_BUDGET = 33          # the live dev1 value: below MERGE_RESERVE_S every sweep


def _ok(stderr=""):
    return types.SimpleNamespace(returncode=0, stdout="", stderr=stderr)


class _Detached(_Repos):
    """A clean `develop` checkout behind origin + fake `systemd-run` clients."""

    def setUp(self):
        super().setUp()
        self.calls = []

    def recording_unit(self, argv, **kw):
        """systemd-run accepted the unit; the unit has not run yet."""
        self.calls.append(list(argv))
        return _ok()

    def executing_unit(self, argv, **kw):
        """systemd-run accepted the unit AND the unit ran to completion (the
        child command after `--`, in the unit's working directory)."""
        self.calls.append(list(argv))
        child = argv[argv.index("--") + 1:]
        wd = argv[argv.index("--working-directory") + 1]
        r = subprocess.run(child, cwd=wd, env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.child_rc = r.returncode
        return _ok()

    @staticmethod
    def absent_unit(argv, **kw):
        raise FileNotFoundError("systemd-run")

    def systemd_calls(self):
        return [c for c in self.calls if c and c[0] == "systemd-run"]


class TestLowBudgetLaunchesDetachedMerge(_Detached):

    def test_low_budget_launches_a_transient_unit_instead_of_deferring(self):
        self.advance_origin(n=2)
        before = self.head()
        self.run_job(T0, budget_left=lambda: LOW_BUDGET,
                     unit_run=self.recording_unit)
        (argv,) = self.systemd_calls()
        self.assertIn("--user", argv)
        self.assertIn("--collect", argv)
        unit = argv[argv.index("--unit") + 1]
        self.assertTrue(unit.startswith("airuleset-ff-"), unit)
        child = argv[argv.index("--") + 1:]
        self.assertTrue(any(a.endswith("cli_checkout_freshness.py") for a in child), child)
        self.assertIn("ff", child)
        self.assertEqual(child[child.index("--path") + 1], self.clone)
        self.assertEqual(child[child.index("--remote") + 1], "origin")
        self.assertEqual(child[child.index("--branch") + 1], "develop")
        self.assertIn("--result", child)
        e = self.entry()
        self.assertNotIn("deferred", e["reason"])
        self.assertIn("running", e["reason"])
        self.assertEqual(e["ff_pending"]["unit"], unit)
        self.assertEqual(self.head(), before, "the recording fake never ran the unit")

    def test_the_unit_fast_forwards_and_the_next_sweep_collects_it(self):
        self.advance_origin(n=3)
        self.run_job(T0, budget_left=lambda: LOW_BUDGET,
                     unit_run=self.executing_unit)
        self.assertEqual(self.child_rc, 0)
        self.assertEqual(self.head(), self.origin_head(),
                         "the transient unit's child brought the checkout current")
        # the next sweep (60 s later, NOT due for a new check) collects it
        self.run_job(T0 + 60, budget_left=lambda: LOW_BUDGET,
                     unit_run=self.executing_unit)
        e = self.entry()
        self.assertEqual(e["state"], "current")
        self.assertEqual(e["behind"], 0)
        self.assertEqual(e["ff"]["commits"], 3)
        self.assertNotIn("ff_pending", e)
        self.assertEqual(len(self.systemd_calls()), 1, "no second launch")
        ffdir = os.path.join(self.home, ".claude", "checkout-freshness", "ff")
        self.assertEqual([f for f in os.listdir(ffdir) if f.endswith(".json")], [],
                         "a collected result file is removed")

    def test_the_unit_runs_with_merge_hooks_off(self):
        hooks = os.path.join(self.root, "hooks")
        os.makedirs(hooks)
        marker = os.path.join(self.root, "post-merge-ran")
        with open(os.path.join(hooks, "post-merge"), "w") as fh:
            fh.write("#!/bin/sh\ntouch %s\n" % marker)
        os.chmod(os.path.join(hooks, "post-merge"), 0o755)
        self.g(self.clone, "config", "core.hooksPath", hooks)
        self.advance_origin()
        self.run_job(T0, budget_left=lambda: LOW_BUDGET,
                     unit_run=self.executing_unit)
        self.assertEqual(self.head(), self.origin_head())
        self.assertFalse(os.path.exists(marker))


class TestSingleFlight(_Detached):

    def test_a_running_unit_blocks_a_duplicate_launch(self):
        self.advance_origin()
        self.run_job(T0, unit_run=self.recording_unit)
        self.assertEqual(len(self.systemd_calls()), 1)
        # due again, the unit has written no result yet: still running
        self.run_job(T0 + _job().INTERVAL_S, unit_run=self.recording_unit)
        self.assertEqual(len(self.systemd_calls()), 1, "no duplicate launch")
        e = self.entry()
        self.assertIn("running", e["reason"])
        self.assertIn("ff_pending", e)

    def test_unit_name_already_exists_counts_as_running_not_as_fallback(self):
        self.advance_origin()
        before = self.head()

        def exists(argv, **kw):
            self.calls.append(list(argv))
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="Unit airuleset-ff-x.service already exists.")

        self.run_job(T0, unit_run=exists)
        self.assertEqual(self.head(), before, "no in-unit merge next to a live unit")
        e = self.entry()
        self.assertIn("running", e["reason"])
        self.assertIn("ff_pending", e)

    def test_a_unit_that_never_reports_expires_as_failed(self):
        job = _job()
        self.advance_origin()
        self.run_job(T0, unit_run=self.recording_unit)
        later = T0 + job.FF_PENDING_MAX_S + 1
        self.run_job(later, unit_run=self.recording_unit)
        # it expired -> the due check launched a fresh unit (the second launch)
        self.assertEqual(len(self.systemd_calls()), 2)
        self.assertEqual(self.entry()["ff_pending"]["launched"], later)

    def test_expired_pending_is_reported_as_no_result(self):
        job = _job()
        self.advance_origin()
        self.run_job(T0, unit_run=self.recording_unit)
        logs = self.run_job(T0 + job.FF_PENDING_MAX_S + 1,
                            unit_run=self.recording_unit)
        self.assertTrue(any("no result" in ln for ln in logs), logs)


class TestFailedUnitIsCollected(_Detached):

    def test_the_unit_rechecks_and_a_refusal_is_collected_as_failed(self):
        self.advance_origin()
        before = self.head()

        def dirty_then_run(argv, **kw):
            # a session edits a tracked file between the job's verdict and
            # the unit's start: the unit's own re-check must refuse
            self.write(self.clone, "notes.txt", "session edit\n")
            return self.executing_unit(argv, **kw)

        self.run_job(T0, unit_run=dirty_then_run)
        self.assertEqual(self.head(), before)
        self.run_job(T0 + 60, unit_run=self.recording_unit)
        e = self.entry()
        self.assertEqual(e["state"], "lagging")
        self.assertIn("fast-forward failed", e["reason"])
        self.assertIn("dirty", e["reason"])
        self.assertNotIn("ff_pending", e)


class TestChildEntry(_Repos):
    """`python3 cli_checkout_freshness.py ff ...` — what the unit runs."""

    def _child(self, result):
        return subprocess.run(
            [sys.executable, str(REPO / "cli_checkout_freshness.py"), "ff",
             "--path", self.clone, "--remote", "origin", "--branch", "develop",
             "--result", result],
            cwd=str(REPO), env=self.env, capture_output=True, text=True,
            timeout=60)

    def test_child_fast_forwards_and_writes_an_ok_result(self):
        self.advance_origin(n=2)
        result = os.path.join(self.root, "r.json")
        r = self._child(result)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(), self.origin_head())
        import json
        with open(result) as fh:
            data = json.load(fh)
        self.assertTrue(data["ok"])
        self.assertEqual(data["commits"], 2)

    def test_child_refuses_a_branch_switch_since_the_verdict(self):
        self.advance_origin()
        self.g(self.clone, "checkout", "-q", "-b", "work")
        result = os.path.join(self.root, "r.json")
        r = self._child(result)
        self.assertNotEqual(r.returncode, 0)
        import json
        with open(result) as fh:
            data = json.load(fh)
        self.assertFalse(data["ok"])
        self.assertIn("branch", data["reason"])


class TestFallbackWithoutSystemdRun(_Detached):

    def test_no_systemd_run_keeps_the_reserved_in_unit_merge(self):
        job = _job()
        self.advance_origin()
        before = self.head()
        self.run_job(T0, budget_left=lambda: job.PER_CHECKOUT_S + 1,
                     unit_run=self.absent_unit)
        self.assertEqual(self.head(), before)
        e = self.entry()
        self.assertIn("deferred", e["reason"])
        self.assertIn("systemd-run", e["reason"])
        self.assertEqual(e["deferrals"], 1)
        self.run_job(T0 + 60, unit_run=self.absent_unit)
        self.assertEqual(self.head(), self.origin_head())
        e = self.entry()
        self.assertEqual(e["state"], "current")
        self.assertNotIn("deferrals", e)

    def test_deferrals_accumulate_across_sweeps(self):
        self.advance_origin()
        for i in range(3):
            self.run_job(T0 + 60 * i, budget_left=lambda: LOW_BUDGET,
                         unit_run=self.absent_unit)
        self.assertEqual(self.entry()["deferrals"], 3)

    def test_a_repeatedly_deferred_checkout_is_processed_first(self):
        job, cf = _job(), _cf()
        self.advance_origin()
        # `other` was checked LONGER ago (plain oldest-first would pick it),
        # but `clone` has been deferred DEFER_PRIORITY_AFTER sweeps in a row
        two = [{"path": self.other, "bases": ["develop"], "source": "b"},
               {"path": self.clone, "bases": ["develop"], "source": "a"}]
        job.write_status({"ts": T0, "checkouts": {
            self.other: {"state": "current", "checked": 1},
            self.clone: {"state": "lagging", "checked": 5,
                         "deferrals": job.DEFER_PRIORITY_AFTER,
                         "reason": "fast-forward deferred"}}}, self.home)
        ticks = iter([0, 0, 10 ** 6, 10 ** 6])
        logs = self.run_job(T0, checkouts=two, clock=lambda: next(ticks),
                            unit_run=self.absent_unit)
        lines = [ln for ln in logs if ln.startswith("checkout-freshness: /")]
        self.assertEqual(len(lines), 1, logs)
        self.assertIn(self.clone, lines[0])
        self.assertEqual(cf.read_status(self.home)["checkouts"][self.clone]["state"],
                         "current")

    def test_no_real_systemd_run_from_a_test_process(self):
        # the default launcher (no `unit_run` seam) never starts a real
        # transient unit from a test process: it takes the fallback
        real = subprocess.run
        seen = []

        def spy(argv, *a, **kw):
            if argv and argv[0] == "systemd-run":
                seen.append(argv)
                raise AssertionError("a test process started systemd-run")
            return real(argv, *a, **kw)

        self.advance_origin()
        with mock.patch("subprocess.run", spy):
            self.run_job(T0)
        self.assertEqual(seen, [])
        self.assertEqual(self.head(), self.origin_head())


class TestOneSharedLauncher(unittest.TestCase):

    def test_one_systemd_run_argv_builder_in_the_watchdog(self):
        pat = re.compile(r'\[\s*"systemd-run"')
        owners = sorted(p.name for p in (REPO / "watchdog").glob("*.py")
                        if pat.search(p.read_text(encoding="utf-8")))
        self.assertEqual(owners, ["user_unit.py"])
        src = (REPO / "watchdog" / "checkout_freshness.py").read_text(encoding="utf-8")
        self.assertIn("user_unit", src)
        src = (REPO / "watchdog" / "ops_wait_refresh.py").read_text(encoding="utf-8")
        self.assertIn("user_unit", src)

    def test_constants_value_lock(self):
        job = _job()
        self.assertEqual(job.DEFER_PRIORITY_AFTER, 3)
        # the unit's own lifetime bounds a wedged merge; generous vs a merge
        self.assertGreaterEqual(job.FF_UNIT_MAX_S, 600)
        self.assertGreater(job.FF_PENDING_MAX_S, job.FF_UNIT_MAX_S)
        # the fallback keeps the review-2 reserve unchanged
        self.assertEqual(job.MERGE_RESERVE_S, 60)


if __name__ == "__main__":
    unittest.main()
