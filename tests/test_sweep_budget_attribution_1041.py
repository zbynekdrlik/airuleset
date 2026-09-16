"""#1041 sweep-budget attribution (follow-up of #1023) — the `run_once`
standalone-job loop must (1) journal `job start: <name> at <elapsed>s` BEFORE
each registered job so a systemd `TimeoutStartSec=2min` kill DURING a job leaves
that job's name as the last `job start:` line (the gk 15:13:32 kill printed only
`Failed with result 'timeout'`), and (2) let every network/subprocess job read
the ONE `remaining_budget_s()` primitive and skip with `hold:budget` +
UNTOUCHED state when fewer than its own timeout-derived minimum remain, so one
job started late cannot run the unit into the kill.

RED against the pre-fix tree: (1) no `job start:` line exists anywhere; (2) only
the queue-arrival rider has a budget guard — every other standalone job runs
regardless of remaining budget, so a low-budget sweep still fires them.

The fake clock: `run_once` derives `_sweep_start` from its FIRST `time_fn()`
call (`sweep_deadline - sweep_budget_s == start`). A clock that returns BASE
first and `BASE + holder["elapsed"]` afterwards makes
`remaining_budget_s() == SWEEP_SOFT_CAP_S - holder["elapsed"]`, so a test drives
the remaining budget directly by setting `holder["elapsed"]`.
"""
import sys
import unittest
import unittest.mock as mock
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd            # noqa: E402

NOW = 1_000_000
BASE = 5000.0


def _clock(holder):
    """First call anchors `_sweep_start = BASE`; every later call is
    `BASE + holder['elapsed']`, so `remaining_budget_s()` == SOFT_CAP - elapsed."""
    seq = {"n": 0}

    def clock():
        seq["n"] += 1
        return BASE if seq["n"] == 1 else BASE + holder["elapsed"]
    return clock


def _run(holder, **kwargs):
    with TemporaryDirectory() as d:
        return list(wd.run_once(
            now=NOW, dry_run=True, run=lambda *a, **k: "",
            send_fn=lambda *a, **k: None,
            projects_dir=Path(d) / "proj",
            state_path=str(Path(d) / "state.json"),
            time_fn=_clock(holder),
            **kwargs,
        ))


class TestJobStartAttribution(unittest.TestCase):
    def test_every_registry_job_emits_a_job_start_line(self):
        holder = {"elapsed": 0}
        logs = _run(holder)
        starts = [ln for ln in logs if ln.startswith("job start:")]
        # the always-on jobs alone already produce several job-start lines
        self.assertTrue(
            any("job start: deliver_pending_done" in ln for ln in starts),
            "run_once must journal a `job start:` line before each registered "
            "job so a mid-job systemd kill names the culprit\n" + "\n".join(logs))
        self.assertTrue(
            any("job start: session_health_observe" in ln for ln in starts),
            "the LAST always-on job must also get a job-start line\n"
            + "\n".join(logs))
        # each job-start line carries the elapsed anchor
        self.assertTrue(all(" at " in ln and ln.rstrip().endswith("s")
                            for ln in starts), starts)

    def test_blocker_job_start_precedes_the_next_budget_aware_hold(self):
        # bounce_backstop (job 8, early) "blocks": it advances the sweep clock so
        # every later budget-aware job sees too little budget. net_drift_alarm
        # (job 27, late, budget-aware) must then HOLD, and bounce's job-start
        # line must precede net_drift's hold line.
        holder = {"elapsed": 0}
        net_calls = []

        def _bounce(*a, **k):
            holder["elapsed"] = 95          # consume budget → remaining 5s
            return []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        with mock.patch.object(wd, "bounce_backstop", _bounce), \
                mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, bounce_fetch=lambda *a, **k: [],
                        issue_counts_fetch=lambda *a, **k: {})

        self.assertEqual(net_calls, [],
                         "net_drift_alarm must NOT run with 5s budget left\n"
                         + "\n".join(logs))
        hold = [i for i, ln in enumerate(logs)
                if ln.startswith("net_drift_alarm -> hold:budget")]
        start = [i for i, ln in enumerate(logs)
                 if ln.startswith("job start: bounce_backstop")]
        self.assertTrue(hold, "net_drift_alarm must hold:budget\n" + "\n".join(logs))
        self.assertTrue(start, "bounce_backstop must get a job-start line")
        self.assertLess(start[0], hold[0],
                        "the blocker's job-start line must precede the later "
                        "job's hold line (attribution order)")


class TestRegistryBudgetGuard(unittest.TestCase):
    def test_low_budget_skips_a_network_job_untouched_zero_calls(self):
        holder = {"elapsed": 95}            # 5s remaining for the whole sweep
        net_calls = []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        with mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, issue_counts_fetch=lambda *a, **k: {})

        self.assertEqual(net_calls, [],
                         "a budget-aware job must not spawn its subprocess/fetch "
                         "with 5s of sweep budget left\n" + "\n".join(logs))
        self.assertTrue(
            any(ln.startswith("net_drift_alarm -> hold:budget") for ln in logs),
            "the skip must journal `net_drift_alarm -> hold:budget`\n"
            + "\n".join(logs))

    def test_ample_budget_still_runs_the_network_job(self):
        holder = {"elapsed": 0}             # full budget
        net_calls = []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        with mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, issue_counts_fetch=lambda *a, **k: {})

        self.assertEqual(len(net_calls), 1,
                         "an ample budget must NOT skip the job\n" + "\n".join(logs))
        self.assertFalse(
            any(ln.startswith("net_drift_alarm -> hold:budget") for ln in logs),
            logs)

    def test_disk_guard_and_reapers_also_budget_guarded(self):
        # a representative subprocess (non-fetch) job — disk_guard (du drain) —
        # must ALSO honour the budget, proving the guard is registry-wide, not
        # gh-fetch-only.
        holder = {"elapsed": 95}
        with mock.patch.object(wd.disk_guard, "run_disk_guard",
                               lambda *a, **k: ["dg::ran"]):
            logs = _run(holder, disk_guard_enabled=True)
        self.assertTrue(
            any(ln.startswith("disk_guard -> hold:budget") for ln in logs),
            "disk_guard must hold:budget at low budget\n" + "\n".join(logs))
        self.assertNotIn("dg::ran", logs,
                         "disk_guard's drain must not run at low budget")


class TestSoftCapAndJobStartOrder(unittest.TestCase):
    def test_soft_cap_line_and_job_start_lines_both_present_in_order(self):
        # clock crosses the soft cap immediately: the existing `sweep budget:`
        # line survives AND the new job-start lines appear, the first job-start
        # before the first soft-cap line.
        holder = {"elapsed": 200}          # >> SWEEP_SOFT_CAP_S
        logs = _run(holder)
        starts = [i for i, ln in enumerate(logs) if ln.startswith("job start:")]
        budget = [i for i, ln in enumerate(logs) if ln.startswith("sweep budget:")]
        self.assertTrue(starts, "job-start lines must be present\n" + "\n".join(logs))
        self.assertTrue(budget,
                        "the #1023 `sweep budget:` line must still fire\n"
                        + "\n".join(logs))
        self.assertLess(starts[0], budget[0],
                        "the first job-start line precedes the first soft-cap line")


if __name__ == "__main__":
    unittest.main()
