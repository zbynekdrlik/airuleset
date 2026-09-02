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
import notify                                             # noqa: E402

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


class TestRepoIsFork(unittest.TestCase):
    """#441: a fork clone (a distinct `upstream` remote) is detected purely
    locally, so job 28 never measures its deliberately-frozen origin/main."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-isfork-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_distinct_upstream_remote_is_a_fork(self):
        r = _make_repo(self.tmp, "odoo-erp",
                       origin_url="https://github.com/kvaskodev/odoo-erp.git")
        _git(r, "remote", "add", "upstream",
             "https://github.com/zbynekdrlik/odoo-erp.git")
        self.assertTrue(wd._repo_is_fork(str(r)))

    def test_no_upstream_remote_is_not_a_fork(self):
        r = _make_repo(self.tmp, "camera-box",
                       origin_url="https://github.com/zbynekdrlik/camera-box.git")
        self.assertFalse(wd._repo_is_fork(str(r)))

    def test_upstream_identical_to_origin_is_not_a_fork(self):
        url = "https://github.com/zbynekdrlik/thing.git"
        r = _make_repo(self.tmp, "thing", origin_url=url)
        _git(r, "remote", "add", "upstream", url)
        self.assertFalse(wd._repo_is_fork(str(r)))

    def test_upstream_present_with_no_origin_remote_is_a_fork(self):
        # A lone upstream with no configured origin remote still delivers
        # upstream, not to its own main -- treat it as a fork.
        r = _make_repo(self.tmp, "lonely")  # no origin_url -> no origin remote
        _git(r, "remote", "add", "upstream",
             "https://github.com/zbynekdrlik/lonely.git")
        self.assertTrue(wd._repo_is_fork(str(r)))


class TestStuckMainSkipSet(unittest.TestCase):
    """#441: AIRULESET_STUCK_MAIN_SKIP is the explicit per-repo opt-out for a
    fork/mirror clone that lacks the `upstream`-remote convention."""

    def test_empty_env_is_empty_set(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_STUCK_MAIN_SKIP", None)
            self.assertEqual(wd._stuck_main_skip_set(), set())

    def test_comma_and_space_separated(self):
        with unittest.mock.patch.dict(
                os.environ,
                {"AIRULESET_STUCK_MAIN_SKIP": "kvaskodev/odoo-erp, a/b  c/d"}):
            self.assertEqual(
                wd._stuck_main_skip_set(),
                {"kvaskodev/odoo-erp", "a/b", "c/d"})


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


class _RepoHealthStoreIsolated(unittest.TestCase):
    """#560: net_drift_alarm/stuck_main_sweep now consult
    notify.episode_gate(), whose DEFAULT store is
    ~/.claude/notify-episodes.json. Every test that FIRES these jobs (i.e.
    reaches a real measurement + a real send_fn, so the gate is consulted)
    MUST isolate that store off the real home, or it pollutes ~/.claude AND
    collides with the many concurrent worker suites this box runs (the #559
    keyless-send-pollution class). setUp patches notify._claude_dir to a
    throwaway per-test dir; `self.episodes()` reads the isolated store back.
    A test that kills BEFORE any measurement (the cadence/marker crash-safety
    classes) never reaches the gate and needs no isolation."""

    def setUp(self):
        super().setUp()
        self._ep_home = tempfile.mkdtemp(prefix="airuleset-560-store-")
        self.addCleanup(shutil.rmtree, self._ep_home, ignore_errors=True)
        _p = unittest.mock.patch.object(notify, "_claude_dir",
                                        lambda: self._ep_home)
        _p.start()
        self.addCleanup(_p.stop)

    def episodes(self):
        return notify.load_episodes()   # patched _claude_dir -> isolated store


class TestNetDriftAlarm(_RepoHealthStoreIsolated):
    def setUp(self):
        super().setUp()
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

    def test_positive_control_high_net_drift_logs_no_ping(self):
        """camera-box's own shape: 40 opened, 5 closed in a week -> net +35,
        well past the threshold. The job MUST still catch/log this -- but
        #850 (repo-health findings never ping the owner) means the CATCH is
        now machine-channel-only: no send, a decision line in the journal."""
        def fetch(label, window_s):
            return (40, 5)
        logs = wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                                  repo_roots=["/repos/camera-box"],
                                  issue_counts_fetch=fetch)
        self.assertEqual(self.sent, [], "#850: never an owner ping")
        self.assertTrue(
            any("net-drift" in line and "camera-box" in line and "open" in line
                for line in logs),
            "the onset is still visible in the journal (machine channel)")

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

    def test_second_consecutive_unhealthy_sweep_holds_no_resend(self):
        # #560 (was test_reping_dedup_within_window): a second DUE sweep while
        # the condition persists must NOT re-open -- the gate returns "hold"
        # after the onset. #850: neither sweep ever sends (owner ping
        # removed) -- the observable is now the machine-channel decision
        # line, which fires once on "open" and never again on "hold".
        def fetch(label, window_s):
            return (40, 5)
        logs1 = wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                                   repo_roots=["/repos/x"], issue_counts_fetch=fetch,
                                   interval=1)
        logs2 = wd.net_drift_alarm(NOW + 2, self.state, send_fn=self.send,
                                   repo_roots=["/repos/x"], issue_counts_fetch=fetch,
                                   interval=1)
        self.assertEqual(self.sent, [], "#850: never an owner ping")
        self.assertTrue(any("net-drift x -> open" in ln for ln in logs1),
                        "onset is logged")
        self.assertFalse(any("net-drift x -> open" in ln for ln in logs2),
                         "2nd sweep -> hold, no re-open line")


class TestStuckMainSweep(_RepoHealthStoreIsolated):
    def setUp(self):
        super().setUp()
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

    def test_git_fetch_error_is_logged_and_skips_the_repo(self):
        """#172 (reopened) finding 5: a failed fetch means the local refs
        MAY BE STALE -- measuring stuck-main on them anyway is a
        false-positive generator (a repo merely behind a slow link would
        read as stuck-main). The repo must be skipped entirely for this
        sweep, never pinged on data that might be stale. (This corrects the
        original #172 fix's own test, which asserted the OPPOSITE -- that
        the job "still measures + pings despite the fetch failure" -- as
        desired; that was the exact defect the reopened review found.)"""
        r = _make_repo(self.tmp, "y", base_ts=NOW - 6 * DAY, undelivered=25)

        def boom(root):
            raise RuntimeError("network down")
        logs = wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                                   repo_roots=[str(r)], git_fetch=boom)
        self.assertTrue(any("git-fetch-error" in line for line in logs))
        self.assertEqual(
            self.sent, [],
            "a fetch failure must skip the repo, never ping on refs that "
            "may be stale")

    def test_second_sweep_within_interval_is_a_noop(self):
        r = _make_repo(self.tmp, "x", base_ts=NOW - 6 * DAY, undelivered=25)
        calls = []
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
                           git_fetch=lambda root: calls.append(1))
        wd.stuck_main_sweep(NOW + 10, self.state, send_fn=self.send,
                           repo_roots=[str(r)],
                           git_fetch=lambda root: calls.append(1))
        self.assertEqual(len(calls), 1)

    def _fork(self, name="odoo-erp", base_ts=NOW - 36 * DAY, undelivered=30):
        """kvaskodev/odoo-erp's shape: origin/main frozen, work piled up, and
        a distinct `upstream` remote (integration goes there via gatekeeper)."""
        r = _make_repo(self.tmp, name, base_ts=base_ts, work_ts=NOW - 3600,
                       undelivered=undelivered,
                       origin_url="https://github.com/kvaskodev/%s.git" % name)
        _git(r, "remote", "add", "upstream",
             "https://github.com/zbynekdrlik/%s.git" % name)
        return r

    def test_fork_with_upstream_remote_is_not_pinged(self):
        """#441: a fork's origin/main is deliberately frozen (delivery goes
        upstream) -- job 28 must NOT alarm on it, even at the exact camera-box
        stuck-main signature."""
        r = self._fork()
        logs = wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                                   repo_roots=[str(r)])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("stuck-main skip" in line for line in logs))

    def test_fork_skip_happens_before_git_fetch(self):
        """A skipped fork must not even spend a git fetch on itself."""
        r = self._fork()
        fetched = []
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
                           git_fetch=lambda root: fetched.append(root))
        self.assertEqual(self.sent, [])
        self.assertEqual(fetched, [])

    def test_fork_skip_never_consults_the_gate_or_sends(self):
        """#560 (was test_fork_skip_drops_stale_dedup_memory): a fork is
        skipped BEFORE any measurement, so it never sends AND never opens an
        episode -- the gate is not consulted for a skipped repo. (The old
        assertion that a stale `state['stuck_main']` dedup entry gets cleared
        is retired -- that per-repo memory no longer exists; #560 moved dedup
        to episode_gate's store, and this proves the fork never enters it.)"""
        r = self._fork()
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send, repo_roots=[str(r)])
        self.assertEqual(self.sent, [])
        self.assertNotIn(
            "stuck-main:kvaskodev/odoo-erp", self.episodes(),
            "a fork-skipped repo must never open an episode (gate not consulted)")

    def test_env_skip_list_silences_a_named_repo(self):
        """#441: an operator opt-out via AIRULESET_STUCK_MAIN_SKIP silences a
        fork/mirror clone even when it lacks the `upstream`-remote convention."""
        r = _make_repo(self.tmp, "odoo-erp", base_ts=NOW - 6 * DAY,
                       work_ts=NOW - 3600, undelivered=25,
                       origin_url="https://github.com/kvaskodev/odoo-erp.git")
        with unittest.mock.patch.dict(
                os.environ,
                {"AIRULESET_STUCK_MAIN_SKIP": "kvaskodev/odoo-erp"}):
            logs = wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                                       repo_roots=[str(r)])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("stuck-main skip" in line for line in logs))


class TestRunOnceWiring(_RepoHealthStoreIsolated):
    """Both jobs are reachable through run_once's real dispatch, gated on
    their injected params exactly like jobs 8/11/16/24/25."""

    def setUp(self):
        super().setUp()
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


class TestMarkerPersistedBeforeRepoRoots_172(unittest.TestCase):
    """#172 (reopened) finding 4: the cadence marker must reach DISK BEFORE
    `repo_roots()` itself runs (an `os.walk($HOME)`, executed once per due
    sweep) -- not merely before the per-repo network loop. A kill inside
    the walk used to lose the marker exactly like a kill inside the loop
    did (the original #172 fix only moved the persist point ahead of the
    loop, not ahead of `repo_roots()`)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-f4-172-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_path = self.tmp / "state.json"

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        return "sent"

    def test_job27_marker_persists_even_when_repo_roots_itself_is_killed(self):
        def killed_repo_roots():
            raise SystemExit("simulated kill during os.walk")

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path,
                       repo_roots=killed_repo_roots,
                       issue_counts_fetch=lambda label, w: (40, 5))
        on_disk = wd.load_state(self.state_path)
        self.assertIn(
            "net_drift_last_sweep", on_disk,
            "the cadence marker must persist BEFORE repo_roots() runs -- a "
            "kill inside the os.walk itself must not lose it either")

    def test_job28_marker_persists_even_when_repo_roots_itself_is_killed(self):
        def killed_repo_roots():
            raise SystemExit("simulated kill during os.walk")

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path,
                       repo_roots=killed_repo_roots)
        on_disk = wd.load_state(self.state_path)
        self.assertIn(
            "stuck_main_last_sweep", on_disk,
            "the cadence marker must persist BEFORE repo_roots() runs -- a "
            "kill inside the os.walk itself must not lose it either")


class TestDedupPersistedBeforeThePing_172(_RepoHealthStoreIsolated):
    """#172 (reopened) finding 3 -- CONVERTED for #560: the finding's
    guarantee (a kill between two pings must not lose the FIRST repo's dedup,
    or it re-pings on the next rotation) still holds, but the mechanism moved
    from the hand-rolled `state['net_drift']`/`state['stuck_main']` per-repo
    memory to notify.episode_gate()'s own atomic store (an `os.replace`
    per gate call, so the first repo's OPENED episode is on disk the instant
    its onset fires -- BEFORE the second repo's fetch even starts). We now
    assert the episode STORE, not the removed state key."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-f3-172-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_path = self.tmp / "state.json"
        self.sent = []

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append(msg)
        return "sent"

    def test_net_drift_first_repos_episode_survives_a_kill_on_the_second(self):
        def fetch(label, window_s):
            if label == "b":
                raise SystemExit("simulated kill mid-fetch on repo b")
            return (40, 5)   # repo "a" -- above threshold, opens first

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path,
                       repo_roots=["/repos/a", "/repos/b"],
                       issue_counts_fetch=fetch)
        self.assertEqual(len(self.sent), 1,
                         "repo a must have alerted (open) before repo b's kill")
        ep = self.episodes()
        self.assertTrue(
            ep.get("net-drift:a", {}).get("active"),
            "repo a's OPENED episode must have reached the episode store "
            "(atomic os.replace) BEFORE repo b's fetch even started -- a kill "
            "there must not lose it, or repo a re-opens on its next rotation")

    def test_stuck_main_first_repos_episode_survives_a_kill_on_the_second(self):
        r = _make_repo(self.tmp, "aa", base_ts=NOW - 6 * DAY, undelivered=25)

        def killed_fetch(root):
            if root.endswith("bb"):
                raise SystemExit("simulated kill mid-fetch on repo bb")

        with self.assertRaises(SystemExit):
            wd.run_once(now=NOW, run=lambda *a, **k: "", send_fn=self.send,
                       state_path=self.state_path,
                       repo_roots=[str(r), str(self.tmp / "bb")],
                       git_fetch=killed_fetch)
        self.assertEqual(len(self.sent), 1,
                         "repo aa must have alerted (open) before repo bb's kill")
        ep = self.episodes()
        self.assertTrue(
            ep.get("stuck-main:aa", {}).get("active"),
            "repo aa's OPENED episode must have reached the episode store "
            "BEFORE repo bb's fetch even started")


# #172 (reopened) smaller item -- `TestDedupMemoryAges_172` REMOVED for #560.
# It tested the hand-rolled `state['net_drift']`/`state['stuck_main']`
# dedup-memory AGING (`DEDUP_MEMORY_MAX_AGE_S` prune of a vanished repo's
# entry, and survival of a recently-touched one). #560 retires that whole
# per-repo memory in favour of notify.episode_gate()'s own atomic store, and
# the "age out an episode whose caller stopped observing it" guarantee moved
# WITH it -- `notify._prune_episodes` / `EPISODE_MAX_AGE_S` (30d), tested by
# `tests/test_notify_episode_dedup_558.py` (the primitive's own contract).
# Re-testing it here would just re-test episode_gate through repo_health, so
# the class is dropped rather than converted (its guarantee is not lost).


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

    def test_env_override_of_zero_clamps_to_the_default_not_all_repos(self):
        """#172 (reopened) finding 2: `AIRULESET_REPO_SWEEP_BATCH=0` (the
        obvious spelling for "disable batching") must NOT silently sweep
        the FULL repo list -- that re-arms the exact 40-repo-in-one-sweep
        cost the cap exists to prevent, via the knob an operator disabling
        batching is most likely to reach for."""
        repos = ["/r/%d" % i for i in range(10)]
        state = {}
        with unittest.mock.patch.dict(os.environ,
                                      {"AIRULESET_REPO_SWEEP_BATCH": "0"}):
            batch = wd._repo_sweep_batch(repos, state, "k")
        self.assertEqual(len(batch), wd.REPO_SWEEP_BATCH_MAX)

    def test_env_override_of_negative_also_clamps(self):
        repos = ["/r/%d" % i for i in range(10)]
        state = {}
        with unittest.mock.patch.dict(os.environ,
                                      {"AIRULESET_REPO_SWEEP_BATCH": "-5"}):
            batch = wd._repo_sweep_batch(repos, state, "k")
        self.assertEqual(len(batch), wd.REPO_SWEEP_BATCH_MAX)

    def test_explicit_max_repos_zero_also_clamps(self):
        repos = ["/r/%d" % i for i in range(10)]
        state = {}
        batch = wd._repo_sweep_batch(repos, state, "k", max_repos=0)
        self.assertEqual(len(batch), wd.REPO_SWEEP_BATCH_MAX)

    def test_short_list_fast_path_does_not_reset_the_cursor(self):
        """#172 (reopened) smaller item: a short repo list (batch >= repo
        count -- here because max_repos is large, but the same branch a
        TRANSIENT short `discover_managed_repos` result would also take)
        must leave the cursor untouched, not rewind it to 0. A mount
        hiccup that makes one sweep see only 2 repos instead of 40 must
        not restart the whole rotation once the real count returns."""
        state = {"k": 7}
        wd._repo_sweep_batch(["/r/a", "/r/b"], state, "k", max_repos=5)
        self.assertEqual(
            state.get("k"), 7,
            "the short-list fast path must not rewind an existing cursor")

    def test_n_zero_also_does_not_reset_an_existing_cursor(self):
        state = {"k": 3}
        wd._repo_sweep_batch([], state, "k", max_repos=5)
        self.assertEqual(state.get("k"), 3)


class TestBatchingPreservesUntouchedDedup_172(_RepoHealthStoreIsolated):
    """#172 -- CONVERTED for #560: when a sweep only touches a ROUND-ROBIN
    BATCH of the full repo list, a repo sitting OUT this sweep must keep its
    existing dedup -- BUT under #560 this is inherent to episode_gate rather
    than a hand-rolled prune: the gate only ever modifies keys it is CALLED
    with, and a sit-out repo is never measured, so its episode is untouched
    by construction. We now assert the episode STORE.

    Two repo roots with `max_repos=1` genuinely forces one to sit out a real
    round-robin batch (`_repo_label` with no remote falls back to the
    basename; sorted() puts 'touched' < 'untouched', so cursor=0 touches only
    the first)."""

    def setUp(self):
        super().setUp()
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        return "sent"

    def test_net_drift_repo_that_sits_out_the_batch_keeps_its_episode(self):
        # Pre-open an episode for the repo that will sit out this sweep.
        notify.episode_gate("net-drift:untouched", False, now=NOW - 100)
        fetched = []

        def fetch(label, window_s):
            fetched.append(label)     # records who was actually MEASURED
            return (40, 5)            # "touched" is above threshold -> opens

        wd.net_drift_alarm(NOW, self.state, send_fn=self.send,
                           repo_roots=["/repos/touched", "/repos/untouched"],
                           issue_counts_fetch=fetch, max_repos=1)
        # Independent proof of the sit-out (not inferred from episode state):
        # only "touched" was fetched, "untouched" genuinely sat out.
        self.assertEqual(fetched, ["touched"],
                         "only the batched repo is measured; 'untouched' sits out")
        ep = self.episodes()
        self.assertTrue(ep.get("net-drift:touched", {}).get("active"),
                        "the measured repo opened its episode")
        self.assertTrue(
            ep.get("net-drift:untouched", {}).get("active"),
            "a repo genuinely sitting out this sweep's round-robin batch keeps "
            "its existing episode -- the sweep never consults the gate for it")

    def test_stuck_main_repo_that_sits_out_the_batch_keeps_its_episode(self):
        notify.episode_gate("stuck-main:y", False, now=NOW - 100)
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                           repo_roots=["/repos/y", "/repos/x"],
                           git_fetch=None, max_repos=1)
        ep = self.episodes()
        self.assertTrue(
            ep.get("stuck-main:y", {}).get("active"),
            "a repo sitting out this sweep's batch keeps its episode")


class TestIncrementalLogFlush172(_RepoHealthStoreIsolated):
    """#172 fix (3): job decision lines must reach `log_fn` AS THEY HAPPEN,
    not only via the list run_once() RETURNS -- a sweep killed mid-way
    (systemd TimeoutStartSec) never returns at all, so the old "print the
    returned list" path in cmd_watchdog showed NOTHING for the whole 14h
    the #172 incident recurred, even though job 27 runs (and logs) BEFORE
    job 28's hung `git fetch` could have eaten the rest of the budget."""

    def setUp(self):
        super().setUp()
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


# --------------------------------------------------------------------------- #
# #560 — jobs 27/28 wired through notify.episode_gate() (#558): a persistent
# chronic condition alerts ONCE at onset + ONE recovery, never a re-page per
# reping window (the #546 `burn-alert:<hour>` anti-pattern) and never a
# silent clear. The behavioral tests below drive the CURRENT public signature
# and isolate the episode store off the real ~/.claude by patching
# notify._claude_dir — they FAIL on the pre-#560 per-reping-bucket code (5
# pings / no recovery). The onset/recovery-ROUTING unit tests inject a fake
# episode_gate to walk each decision (open/hold/clearing/recover/quiet).
# --------------------------------------------------------------------------- #
class _EpisodeStoreIsolated(_RepoHealthStoreIsolated):
    """#560 hysteresis tests: the episode store is auto-isolated off the real
    ~/.claude by the base's setUp; add the per-test send collector + state."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.state = {}

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "dedup": dedup_key})
        return "sent"


class TestNetDriftEpisodeHysteresis(_EpisodeStoreIsolated):
    """#560 behavioral RED: a persistent backlog alerts ONCE, and its
    recovery alerts exactly ONCE — never a per-reping-window re-page and
    never a silent clear. #850: neither alert is ever an owner SEND any
    more (the gate + its journal decision line are the machine channel)."""

    @staticmethod
    def _fetch(opened, closed):
        return lambda label, window_s: (opened, closed)

    def test_persistent_condition_alerts_once_not_per_reping_window(self):
        fetch = self._fetch(40, 5)                       # net +35, persistent
        all_logs = []
        for i in range(5):                                # five daily sweeps
            all_logs += wd.net_drift_alarm(
                NOW + i * DAY, self.state, send_fn=self.send,
                repo_roots=["/repos/x"], issue_counts_fetch=fetch, interval=1)
        self.assertEqual(self.sent, [], "#850: never an owner ping")
        self.assertEqual(
            sum(1 for ln in all_logs if "net-drift x -> open" in ln), 1,
            "a persistent chronic condition must alert ONCE at onset, not "
            "re-page every reping window (the #546 burn-alert:<hour> disease)")

    def test_recovery_sends_exactly_one_message_after_clearing(self):
        all_logs = wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send, repo_roots=["/repos/x"],
            issue_counts_fetch=self._fetch(40, 5), interval=1)   # onset
        for i in range(1, 4):                             # three healthy passes
            all_logs += wd.net_drift_alarm(
                NOW + i * DAY, self.state, send_fn=self.send,
                repo_roots=["/repos/x"], issue_counts_fetch=self._fetch(5, 5),
                interval=1)
        self.assertEqual(self.sent, [], "#850: never an owner ping")
        self.assertEqual(
            sum(1 for ln in all_logs if "net-drift x -> open" in ln), 1)
        self.assertEqual(
            sum(1 for ln in all_logs if "net-drift x -> recover" in ln), 1,
            "onset + exactly ONE recovery after the hysteresis window; the "
            "pre-#560 code drops the dedup silently with no recovery signal")


class TestStuckMainEpisodeHysteresis(_EpisodeStoreIsolated):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-560-sm-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_persistent_stuck_main_alerts_once_not_per_reping_window(self):
        r = _make_repo(self.tmp, "camera-box", base_ts=NOW - 6 * DAY,
                       work_ts=NOW - 3600, undelivered=25)
        for i in range(5):
            wd.stuck_main_sweep(NOW + i * DAY, self.state, send_fn=self.send,
                                repo_roots=[str(r)], interval=1)
        self.assertEqual(
            len(self.sent), 1,
            "a persistently stuck main must alert ONCE at onset, not re-page "
            "every reping window")


class _RecordingGate:
    """A fake episode_gate that records every consult and returns a scripted
    decision -- lets a test walk the routing (open/recover/hold/...) without
    driving the real hysteresis state machine, and locks the wiring SEAM (a
    refactor that drops the consult, mis-keys the condition or mis-routes a
    decision fails loudly -- the #534 injected-callable-seam lesson)."""

    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def __call__(self, condition_key, healthy, now=None, **kw):
        self.calls.append((condition_key, healthy, now))
        return self.decision


class TestEpisodeGateRouting(_RepoHealthStoreIsolated):
    """#560: net_drift/stuck_main consult the INJECTED episode_gate with a
    stable condition_key + `healthy`, and route open -> onset / recover ->
    recovery / everything-else -> nothing. Fresh send-layer keys per instant."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.state = {}
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-560-route-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "dedup": dedup_key})
        return "sent"

    def _net(self, gate, opened=40, closed=5, **kw):
        self.state = {}
        return wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send, repo_roots=["/repos/proj"],
            issue_counts_fetch=lambda lbl, w: (opened, closed),
            episode_gate=gate, **kw)

    def test_net_drift_open_logs_onset_never_sends(self):
        # #850: the gate is still consulted (it feeds the footer `I N▲`
        # signal + the journal) but the onset is NEVER an owner send any
        # more -- only a machine-channel decision line.
        g = _RecordingGate("open")
        logs = self._net(g)
        self.assertEqual(g.calls, [("net-drift:proj", False, NOW)],
                         "gate consulted with a stable key + healthy=False")
        self.assertEqual(self.sent, [], "#850: never an owner ping")
        self.assertTrue(
            any("net-drift proj -> open" in ln and "never an owner ping" in ln
                for ln in logs),
            logs)

    def test_net_drift_recover_logs_recovery_never_sends(self):
        g = _RecordingGate("recover")
        logs = self._net(g, opened=5, closed=5)   # net 0 -> healthy=True
        self.assertEqual(g.calls, [("net-drift:proj", True, NOW)])
        self.assertEqual(self.sent, [], "#850: never an owner ping")
        self.assertTrue(
            any("net-drift proj -> recover" in ln and "never an owner ping" in ln
                for ln in logs),
            logs)

    def test_net_drift_hold_clearing_quiet_send_nothing(self):
        for d in ("hold", "clearing", "quiet"):
            self.sent = []
            self._net(_RecordingGate(d))
            self.assertEqual(self.sent, [], "decision %r must not send" % d)

    def test_net_drift_dry_run_never_consults_the_gate(self):
        g = _RecordingGate("open")
        self._net(g, dry_run=True)
        self.assertEqual(g.calls, [], "dry-run must not touch the episode store")
        self.assertEqual(self.sent, [])

    def test_net_drift_send_fn_none_never_consults_the_gate(self):
        g = _RecordingGate("open")
        wd.net_drift_alarm(NOW, {}, send_fn=None, repo_roots=["/repos/proj"],
                           issue_counts_fetch=lambda lbl, w: (40, 5),
                           episode_gate=g)
        self.assertEqual(g.calls, [], "no delivery path -> episode untouched")

    def _sm(self, gate, name, stalled=True, **kw):
        base_ts = NOW - 6 * DAY if stalled else NOW - 3600
        r = _make_repo(self.tmp, name, base_ts=base_ts, work_ts=NOW - 3600,
                       undelivered=25)
        self.state = {}
        return wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                                   repo_roots=[str(r)], episode_gate=gate, **kw)

    def test_stuck_main_open_sends_onset_healthy_false(self):
        g = _RecordingGate("open")
        self._sm(g, "smopen", stalled=True)
        self.assertEqual(g.calls, [("stuck-main:smopen", False, NOW)])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(
            self.sent[0]["dedup"].startswith("stuck-main-open:smopen:"))

    def test_stuck_main_recover_sends_recovery_healthy_true(self):
        g = _RecordingGate("recover")
        self._sm(g, "smrec", stalled=False)       # fresh base -> not stalled
        self.assertEqual(g.calls, [("stuck-main:smrec", True, NOW)])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(
            self.sent[0]["dedup"].startswith("stuck-main-recover:smrec:"))

    def test_stuck_main_hold_sends_nothing(self):
        g = _RecordingGate("hold")
        self._sm(g, "smhold", stalled=True)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
