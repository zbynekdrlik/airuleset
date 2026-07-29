"""Jobs 27/28 — NET-ISSUE-DRIFT ALARM + STUCK-MAIN SWEEP (#137).

Both close the same observation gap: camera-box's own measured +101 net-open
issue drift ran two weeks before the user noticed by feel, and the 15-day
merge deadlock behind most of it (`origin/main` frozen since 2026-07-11) ran
undetected the whole time job 24 (#138) existed to catch exactly that shape
-- because job 24 only ever sees a repo with a LIVE PANE currently open in
it. These two sweep EVERY repo the box hosts, on their own hourly cadence,
independent of whether a session happens to be open there right now.

Per this repo's own "a detector that can only prove negatives is worthless"
rule (#137's own instruction) -- EVERY test class below has at least one
POSITIVE CONTROL that actually fires, built from a REAL git repository (the
job's whole job is reading git, so a hand-typed log string would test
nothing -- same discipline as test_delivery_stall.py).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                    # noqa: E402

DAY = 86400
NOW = 1785200000.0          # fixed; never time.time() (hour-bucket jitter rule)


def _git(repo, *args, ts=None):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    if ts is not None:
        stamp = "%d +0000" % int(ts)
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


def _make_repo(root, name="proj", base_ts=NOW - 20 * DAY, work_ts=NOW - 3600,
              undelivered=0, base_name="main", origin_url=None):
    """Same shape as test_delivery_stall.py's `repo()` helper: a base branch
    that last moved at `base_ts`, plus `undelivered` extra commits on a
    `dev` branch dated `work_ts`. `undelivered=0` leaves HEAD ON base."""
    r = root / name
    r.mkdir()
    _git(r, "init", "-q", "-b", base_name)
    (r / "f").write_text("0\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base", ts=base_ts)
    _git(r, "update-ref", "refs/remotes/origin/" + base_name, "HEAD")
    _git(r, "symbolic-ref", "refs/remotes/origin/HEAD",
        "refs/remotes/origin/" + base_name)
    if origin_url:
        _git(r, "remote", "add", "origin", origin_url)
    if undelivered:
        _git(r, "checkout", "-qb", "dev")
        for i in range(undelivered):
            (r / "f").write_text("%d\n" % (i + 1))
            _git(r, "add", "-A")
            _git(r, "commit", "-qm", "work %d" % i, ts=work_ts)
    return r


class TestDiscoverManagedRepos(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-discover-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_finds_real_git_repos(self):
        _make_repo(self.tmp, "a")
        _make_repo(self.tmp, "b")
        found = wd.discover_managed_repos(home=str(self.tmp))
        self.assertEqual(sorted(os.path.basename(f) for f in found), ["a", "b"])

    def test_excludes_node_modules_and_similar_noise(self):
        _make_repo(self.tmp, "real")
        noise = self.tmp / "real" / "node_modules" / "some-dep"
        noise.mkdir(parents=True)
        _git(noise, "init", "-q")
        found = wd.discover_managed_repos(home=str(self.tmp))
        self.assertEqual([os.path.basename(f) for f in found], ["real"])

    def test_respects_max_depth(self):
        deep = self.tmp / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        _git(deep, "init", "-q")
        found_shallow = wd.discover_managed_repos(home=str(self.tmp), max_depth=2)
        self.assertEqual(found_shallow, [])
        found_deep = wd.discover_managed_repos(home=str(self.tmp), max_depth=8)
        self.assertEqual(len(found_deep), 1)

    def test_no_repos_returns_empty_list(self):
        self.assertEqual(wd.discover_managed_repos(home=str(self.tmp)), [])


class TestRepoLabel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-label-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_from_origin_remote(self):
        r = _make_repo(self.tmp, "camera-box",
                       origin_url="git@github.com:zbynekdrlik/camera-box.git")
        self.assertEqual(wd._repo_label(str(r)), "zbynekdrlik/camera-box")

    def test_falls_back_to_basename_with_no_remote(self):
        r = _make_repo(self.tmp, "no-remote-repo")
        self.assertEqual(wd._repo_label(str(r)), "no-remote-repo")


class TestSweepDue(unittest.TestCase):
    def test_first_call_is_due(self):
        self.assertTrue(wd._sweep_due({}, "k", NOW, 3600))

    def test_immediate_repeat_is_not_due(self):
        state = {"k": NOW}
        self.assertFalse(wd._sweep_due(state, "k", NOW + 1, 3600))

    def test_due_again_after_interval(self):
        state = {"k": NOW}
        self.assertTrue(wd._sweep_due(state, "k", NOW + 3601, 3600))

    def test_unusable_stamp_is_due(self):
        state = {"k": "not-a-number"}
        self.assertTrue(wd._sweep_due(state, "k", NOW, 3600))


class TestNetDriftAlarm(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "dedup": dedup_key})
        return "sent"

    def test_gated_off_with_no_fetch(self):
        logs = wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                                  repo_roots=["/x"], issue_counts_fetch=None)
        self.assertEqual(logs, [])
        self.assertEqual(self.sent, [])

    def test_positive_control_high_net_drift_pings(self):
        """camera-box's own shape: 40 opened, 5 closed in a week -> net +35,
        well past the threshold. This is the case the job MUST catch."""
        def fetch(label, window_s):
            return (40, 5)
        logs = wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                                  repo_roots=["/repos/camera-box"],
                                  issue_counts_fetch=fetch)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("+35", self.sent[0]["msg"])
        self.assertTrue(any("PING" in line for line in logs))

    def test_below_threshold_does_not_ping(self):
        def fetch(label, window_s):
            return (5, 5)   # net = 0
        wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                           repo_roots=["/repos/healthy"], issue_counts_fetch=fetch)
        self.assertEqual(self.sent, [])

    def test_unmeasurable_repo_never_pings(self):
        def fetch(label, window_s):
            return None
        logs = wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                                  repo_roots=["/repos/no-gh-access"],
                                  issue_counts_fetch=fetch)
        self.assertEqual(self.sent, [])
        self.assertEqual(logs, [])

    def test_second_sweep_within_interval_is_a_noop(self):
        calls = []

        def fetch(label, window_s):
            calls.append(1)
            return (40, 5)
        wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                           repo_roots=["/repos/x"], issue_counts_fetch=fetch)
        wd.net_drift_alarm(NOW + 10, self.state, send_fn=self.send,
                           repo_roots=["/repos/x"], issue_counts_fetch=fetch)
        self.assertEqual(len(calls), 1)   # second sweep skipped -- not due yet

    def test_reping_dedup_within_window(self):
        def fetch(label, window_s):
            return (40, 5)
        wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                           repo_roots=["/repos/x"], issue_counts_fetch=fetch,
                           interval=1)
        wd.net_drift_alarm(NOW + 2, self.state, send_fn=self.send,
                           repo_roots=["/repos/x"], issue_counts_fetch=fetch,
                           interval=1)
        self.assertEqual(len(self.sent), 1)   # second sweep due, but re-ping deduped


class TestStuckMainSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-stuckmain-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sent = []
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "dedup": dedup_key})
        return "sent"

    def test_positive_control_camera_box_shape_pings(self):
        """origin/main frozen 6 days ago, 25 commits stranded on dev -- the
        exact camera-box shape (#137/#138). Must ping."""
        r = _make_repo(self.tmp, "camera-box", base_ts=NOW - 6 * DAY,
                       work_ts=NOW - 3600, undelivered=25)
        logs = wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                                   repo_roots=[str(r)])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("camera-box", self.sent[0]["msg"])
        self.assertTrue(any("stuck-main PING" in line for line in logs))

    def test_fresh_base_does_not_ping_despite_many_commits(self):
        r = _make_repo(self.tmp, "active", base_ts=NOW - 3600,
                       work_ts=NOW - 60, undelivered=50)
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)])
        self.assertEqual(self.sent, [])

    def test_old_base_with_few_commits_does_not_ping(self):
        r = _make_repo(self.tmp, "quiet", base_ts=NOW - 10 * DAY,
                       work_ts=NOW - 3600, undelivered=3)
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)])
        self.assertEqual(self.sent, [])

    def test_never_a_target_upper_bound_does_not_ping(self):
        """#138's own eft5000 lesson: a base abandoned 200 days ago (well
        past DELIVERY_STALL_MAX_S) was never a delivery target that stopped
        receiving -- it just isn't one. Must NOT ping forever."""
        r = _make_repo(self.tmp, "abandoned-fork", base_ts=NOW - 200 * DAY,
                       work_ts=NOW - 3600, undelivered=50)
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)])
        self.assertEqual(self.sent, [])

    def test_head_on_base_direct_push_repo_never_pings(self):
        """This repo's own shape (direct-to-main, no dev branch) -- HEAD ==
        base, undelivered=0, structurally never a candidate."""
        r = _make_repo(self.tmp, "airuleset-shaped", base_ts=NOW - 30 * DAY,
                       undelivered=0)
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)])
        self.assertEqual(self.sent, [])

    def test_git_fetch_is_invoked_per_repo(self):
        r = _make_repo(self.tmp, "x", base_ts=NOW - 6 * DAY, undelivered=25)
        fetched = []
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
                           git_fetch=lambda root: fetched.append(root))
        self.assertEqual(fetched, [str(r)])

    def test_git_fetch_error_is_logged_not_raised(self):
        r = _make_repo(self.tmp, "y", base_ts=NOW - 6 * DAY, undelivered=25)

        def boom(root):
            raise RuntimeError("network down")
        logs = wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                                   repo_roots=[str(r)], git_fetch=boom)
        self.assertTrue(any("git-fetch-error" in line for line in logs))
        # the job still measures + pings despite the fetch failure --
        # degrades to the local-only heuristic rather than going silent.
        self.assertEqual(len(self.sent), 1)

    def test_second_sweep_within_interval_is_a_noop(self):
        r = _make_repo(self.tmp, "x", base_ts=NOW - 6 * DAY, undelivered=25)
        calls = []
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
                           git_fetch=lambda root: calls.append(1))
        wd.stuck_main_sweep(NOW + 10, self.state, send_fn=self.send,
                           repo_roots=[str(r)],
                           git_fetch=lambda root: calls.append(1))
        self.assertEqual(len(calls), 1)


class TestRunOnceWiring(unittest.TestCase):
    """Both jobs are reachable through run_once's real dispatch, gated on
    their injected params exactly like jobs 8/11/16/24/25."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-runonce-repos-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_path = self.tmp / "state.json"
        self.sent = []

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append(msg)
        return "sent"

    def _run(self, **kw):
        return wd.run_once(now=NOW, run=lambda *a, **k: "",
                           send_fn=self.send, state_path=self.state_path,
                           **kw)

    def test_jobs_are_silent_when_ungated(self):
        logs = self._run()
        self.assertFalse(any(line.startswith("net-drift") for line in logs))
        self.assertFalse(any(line.startswith("stuck-main") for line in logs))

    def test_job_27_fires_through_run_once(self):
        logs = self._run(repo_roots=["/repos/x"],
                         issue_counts_fetch=lambda label, w: (40, 5))
        self.assertTrue(any(line.startswith("net-drift") for line in logs))
        self.assertEqual(len(self.sent), 1)

    def test_job_28_fires_through_run_once(self):
        r = _make_repo(self.tmp, "camera-box", base_ts=NOW - 6 * DAY,
                       undelivered=25)
        logs = self._run(repo_roots=[str(r)])
        self.assertTrue(any(line.startswith("stuck-main") for line in logs))
        self.assertEqual(len(self.sent), 1)


class TestCadencePersistedBeforeKill_172(unittest.TestCase):
    """#172 regression: jobs 27/28 must persist their cadence marker to DISK
    BEFORE the expensive per-repo loop. A real systemd `TimeoutStartSec=120`
    kill is an uncaught PROCESS TERMINATION, not a catchable Python
    exception -- modeled here with a fetch that raises `SystemExit`, which
    propagates straight past every `except Exception` in this module
    (job 27/28's own per-repo try/except AND run_once's own per-job
    try/except), exactly like a real SIGTERM aborts the process before
    run_once's own trailing `save_state()` ever runs.

    BEFORE the fix: the cadence marker lived only in run_once's in-memory
    `state` dict until the very end -- so a kill mid-sweep loses it
    entirely, and the very next 60s tick re-attempts the SAME repos,
    forever (the livelock #172 diagnoses, confirmed live: 236 kills on one
    day, zero before).

    AFTER the fix: `persist()` (the caller's save-state closure) runs
    immediately after the cadence marker is set in memory and BEFORE any
    per-repo network call, so the marker is already on disk by the time a
    kill can happen."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-killcadence-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_path = self.tmp / "state.json"

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        return "sent"

    def test_job27_net_drift_marker_survives_a_kill_mid_sweep(self):
        calls = []

        def killed_fetch(label, window_s):
            calls.append(label)
            raise SystemExit("simulated systemd TimeoutStartSec kill")

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path,
                       repo_roots=["/repos/a", "/repos/b"],
                       issue_counts_fetch=killed_fetch)
        self.assertEqual(len(calls), 1)   # aborted at the FIRST repo
        # "next 60s tick" = a fresh process reloading state from DISK
        on_disk = wd.load_state(self.state_path)
        self.assertIn(
            "net_drift_last_sweep", on_disk,
            "cadence marker must reach DISK before the per-repo loop, not "
            "only run_once()'s own in-memory state (lost on a kill)")
        wd.run_once(now=NOW + 10, run=lambda *a, **k: "", send_fn=self.send,
                   state_path=self.state_path,
                   repo_roots=["/repos/a", "/repos/b"],
                   issue_counts_fetch=killed_fetch)
        self.assertEqual(
            len(calls), 1,
            "second sweep (10s later, well under the hourly interval) must "
            "NOT re-attempt the fetch -- the persisted marker already shows "
            "this hour as swept")

    def test_job28_stuck_main_marker_survives_a_kill_mid_sweep(self):
        r = _make_repo(self.tmp, "x", base_ts=NOW - 6 * DAY, undelivered=25)
        calls = []

        def killed_git_fetch(root):
            calls.append(root)
            raise SystemExit("simulated systemd TimeoutStartSec kill")

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path, repo_roots=[str(r)],
                       git_fetch=killed_git_fetch)
        self.assertEqual(len(calls), 1)
        on_disk = wd.load_state(self.state_path)
        self.assertIn(
            "stuck_main_last_sweep", on_disk,
            "cadence marker must reach DISK before the per-repo loop, not "
            "only run_once()'s own in-memory state (lost on a kill)")
        wd.run_once(now=NOW + 10, run=lambda *a, **k: "", send_fn=self.send,
                   state_path=self.state_path, repo_roots=[str(r)],
                   git_fetch=killed_git_fetch)
        self.assertEqual(
            len(calls), 1,
            "second sweep must NOT re-attempt git_fetch -- the persisted "
            "marker already shows this hour as swept")


class TestRepoSweepBatch172(unittest.TestCase):
    """#172 fix (2): bound how many repos ONE sweep touches, with a
    round-robin cursor in state so coverage still rotates over successive
    sweeps instead of either sweeping ALL repos (the original 40-repo
    livelock trigger) or arbitrarily few forever."""

    def test_small_repo_list_is_untouched(self):
        repos = ["/r/a", "/r/b"]
        state = {}
        batch = wd._repo_sweep_batch(repos, state, "k", max_repos=5)
        self.assertEqual(batch, repos)

    def test_large_repo_list_is_bounded(self):
        repos = ["/r/%d" % i for i in range(10)]
        state = {}
        batch = wd._repo_sweep_batch(repos, state, "k", max_repos=3)
        self.assertEqual(len(batch), 3)

    def test_cursor_rotates_across_successive_calls(self):
        repos = ["/r/%d" % i for i in range(10)]
        state = {}
        seen = []
        for _ in range(4):
            seen.extend(wd._repo_sweep_batch(repos, state, "k", max_repos=3))
        # 4 batches of 3 = 12 slots over 10 repos -- every repo reached at
        # least once, and the first two repeat (wrap-around).
        self.assertEqual(set(seen), set(repos))
        self.assertEqual(seen[:3], repos[0:3])
        self.assertEqual(seen[3:6], repos[3:6])
        self.assertEqual(seen[6:9], repos[6:9])
        self.assertEqual(seen[9:12], repos[9:10] + repos[0:2])

    def test_env_override(self):
        repos = ["/r/%d" % i for i in range(10)]
        state = {}
        with unittest.mock.patch.dict(os.environ,
                                      {"AIRULESET_REPO_SWEEP_BATCH": "2"}):
            batch = wd._repo_sweep_batch(repos, state, "k")
        self.assertEqual(len(batch), 2)


class TestBatchingPreservesUntouchedDedup_172(unittest.TestCase):
    """#172: when a sweep only touches a ROUND-ROBIN BATCH of the full repo
    list, a repo sitting OUT this sweep must keep its existing dedup memory
    -- the original pruning rule (`seen if k in live`) silently assumed
    every repo was re-measured every sweep, which stopped being true once
    batching was added."""

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        return "sent"

    def test_net_drift_untouched_repo_keeps_its_pinged_state(self):
        state = {"net_drift": {"o/untouched": {"pinged_ts": NOW - 10}}}

        def fetch(label, window_s):
            return (40, 5)   # net well above threshold -> would re-ping

        # batch of exactly 1, repo list has 2 -- "o/untouched" sits out.
        wd.net_drift_alarm(NOW, state, send_fn=self.send,
                           repo_roots=["/repos/x"],
                           issue_counts_fetch=fetch, max_repos=1)
        # A repo never even in repo_roots this call can't be "touched" by
        # definition -- assert its prior dedup entry survived untouched.
        self.assertIn("o/untouched", state.get("net_drift", {}))


class TestIncrementalLogFlush172(unittest.TestCase):
    """#172 fix (3): job decision lines must reach `log_fn` AS THEY HAPPEN,
    not only via the list run_once() RETURNS -- a sweep killed mid-way
    (systemd TimeoutStartSec) never returns at all, so the old "print the
    returned list" path in cmd_watchdog showed NOTHING for the whole 14h
    the #172 incident recurred, even though job 27 runs (and logs) BEFORE
    job 28's hung `git fetch` could have eaten the rest of the budget."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-flush172-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_path = self.tmp / "state.json"

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        return "sent"

    def test_job27_line_is_flushed_before_job28_kills_the_sweep(self):
        seen = []

        def net_fetch(label, window_s):
            return (40, 5)          # job 27 succeeds and logs "net-drift ..."

        def killed_git_fetch(root):
            raise SystemExit("simulated kill during job 28")

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path, log_fn=seen.append,
                       repo_roots=["/repos/x"],
                       issue_counts_fetch=net_fetch,
                       git_fetch=killed_git_fetch)
        self.assertTrue(
            any(line.startswith("net-drift") for line in seen),
            "job 27's decision line must be visible via log_fn even though "
            "run_once() itself never returned (job 28 killed the sweep) -- "
            "the OLD 'print only the returned list' path would show nothing")


if __name__ == "__main__":
    unittest.main()
