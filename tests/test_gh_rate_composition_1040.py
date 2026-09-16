"""#1040 — the gh-rate guard composes with the #1041 sweep-budget guard in the
run_once standalone-registry loop: a gh-poller job is HELD (`hold:budget`) when
the shared GitHub budget warrants a backoff, EVEN with ample sweep budget, and
the once-per-episode alert surfaces in the sweep log. Gated on `gh_rate_fetch`
being wired, so every existing run_once test stays network-free.

Harness mirrors tests/test_sweep_budget_attribution_1041.py (the fake clock
drives remaining_budget_s directly).
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


def _low_graphql():
    return {"resources": {
        "graphql": {"remaining": 200, "limit": 5000, "reset": 0},   # 4%
        "core": {"remaining": 4000, "limit": 5000, "reset": 0},
    }}


def _healthy():
    return {"resources": {
        "graphql": {"remaining": 4000, "limit": 5000, "reset": 0},
        "core": {"remaining": 4000, "limit": 5000, "reset": 0},
    }}


class TestGhRateComposition(unittest.TestCase):
    def test_gh_poller_held_when_budget_low_despite_ample_sweep(self):
        holder = {"elapsed": 0}             # full SWEEP budget — only the rate is low
        net_calls = []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        with mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, issue_counts_fetch=lambda *a, **k: {},
                        gh_rate_fetch=_low_graphql)

        self.assertEqual(net_calls, [],
                         "a gh-poller must NOT run when the GitHub budget is <20%\n"
                         + "\n".join(logs))
        self.assertTrue(
            any(ln.startswith("net_drift_alarm -> hold:budget")
                and "gh-rate" in ln for ln in logs),
            "the skip must journal a gh-rate hold:budget line\n" + "\n".join(logs))

    def test_gh_poller_runs_when_budget_healthy(self):
        holder = {"elapsed": 0}
        net_calls = []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        with mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, issue_counts_fetch=lambda *a, **k: {},
                        gh_rate_fetch=_healthy)

        self.assertEqual(net_calls, [1],
                         "a healthy GitHub budget must not hold a gh-poller\n"
                         + "\n".join(logs))

    def test_no_gh_rate_fetch_never_holds(self):
        holder = {"elapsed": 0}
        net_calls = []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        with mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, issue_counts_fetch=lambda *a, **k: {})

        self.assertEqual(net_calls, [1],
                         "with no gh_rate_fetch wired the composition is inert\n"
                         + "\n".join(logs))
        self.assertFalse(any("gh-rate" in ln for ln in logs))

    def test_write_job_never_held_when_budget_low(self):
        # #1040 review-2 MAJOR-2: a WRITE-performing job (gk_request_backstop,
        # NOT gh_poll_hold-marked) must STILL run under a low budget — its
        # owner-facing writes must never be delayed.
        holder = {"elapsed": 0}
        calls = []

        def _gkr(*a, **k):
            calls.append(1)
            return ["gkreq::ran"]

        with mock.patch.object(wd, "gk_request_backstop", _gkr):
            logs = _run(holder, gkreq_fetch=lambda *a, **k: [],
                        gh_rate_fetch=_low_graphql)

        self.assertEqual(calls, [1],
                         "a WRITE job must never be held on a low budget\n"
                         + "\n".join(logs))

    def test_no_gh_job_never_held_when_budget_low(self):
        # #1040 review-2 MAJOR-1: a job that makes NO gh call (stuck_main_sweep,
        # pure local git) is not gh_poll_hold-marked and must keep running under
        # a low budget — holding it would stall detection for zero benefit.
        holder = {"elapsed": 0}
        calls = []

        def _stuck(*a, **k):
            calls.append(1)
            return ["stuck::ran"]

        with mock.patch.object(wd, "stuck_main_sweep", _stuck):
            logs = _run(holder, repo_roots=lambda: [], git_fetch=lambda *a, **k: True,
                        gh_rate_fetch=_low_graphql)

        self.assertEqual(calls, [1],
                         "a no-gh local job must never be held on a low budget\n"
                         + "\n".join(logs))

    def test_fetch_error_fails_open(self):
        holder = {"elapsed": 0}
        net_calls = []

        def _net(*a, **k):
            net_calls.append(1)
            return ["net-drift::ran"]

        def _boom():
            raise RuntimeError("gh down")

        with mock.patch.object(wd, "net_drift_alarm", _net):
            logs = _run(holder, issue_counts_fetch=lambda *a, **k: {},
                        gh_rate_fetch=_boom)

        self.assertEqual(net_calls, [1],
                         "a gh-rate read error must fail open (no hold)\n"
                         + "\n".join(logs))
        self.assertTrue(any("gh-rate: read error" in ln for ln in logs),
                        "the fail-open path must be journalled\n" + "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
