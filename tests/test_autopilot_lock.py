"""Tests for `airuleset.py autopilot-lock` (issue #8).

The "serial per repo" autopilot dispatch rule (skills/autopilot/SKILL.md,
modules/git/two-branch-workflow.md) only ever had SESSION-LOCAL enforcement
(the supervisor checks its own agent strip before dispatching a background
worker) — a SEPARATE `/autopilot` session on the same repo (another
terminal/tmux window) has no visibility into that and can dispatch a
colliding worker onto the same `dev` branch at the same time (the proven
root cause of camera-box #495 and the #499/#500-vs-#505 collision).

This adds a repo-path-keyed cross-session lock: a lockfile under the system
tempdir, named by sha1(realpath(repo)), holding {pid, session, repo,
acquired_at} JSON. `acquire` fails loudly when a LIVE holder exists;
`release` only removes a lock it actually owns (never someone else's);
`status` is a read-only report. `acquire`'s critical section is guarded by
a brief `fcntl.flock` on a sibling `.mutex` file so two concurrent
`acquire` calls on the SAME repo can't both win a stale-steal race.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent


def run(args, home=None, extra_env=None):
    import os
    env = dict(os.environ)
    if home:
        env["HOME"] = home
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(REPO / "airuleset.py"), "autopilot-lock"] + args,
        capture_output=True, text=True, timeout=30, env=env,
    )


def dead_pid():
    """A PID that WAS valid and is now guaranteed dead (reaped)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class TestAcquireRelease(TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()

    def test_acquire_succeeds_when_unlocked(self):
        r = run(["acquire", "--repo", self.repo, "--pid", "999999999"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACQUIRED", r.stdout)

    def test_status_unlocked_when_no_lock(self):
        r = run(["status", "--repo", self.repo])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("UNLOCKED", r.stdout)

    def test_acquire_then_status_shows_locked(self):
        # use OUR OWN live pid as the recorded holder so status sees it as alive
        import os
        r1 = run(["acquire", "--repo", self.repo, "--pid", str(os.getpid())])
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        r2 = run(["status", "--repo", self.repo])
        self.assertEqual(r2.returncode, 0, r2.stdout)
        self.assertIn("LOCKED", r2.stdout)
        self.assertNotIn("stale", r2.stdout)

    def test_acquire_blocks_when_held_by_live_pid(self):
        import os
        me = os.getpid()  # this test process is definitely alive
        r1 = run(["acquire", "--repo", self.repo, "--pid", str(me)])
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        r2 = run(["acquire", "--repo", self.repo, "--pid", "1234567890"])
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        self.assertIn(str(me), r2.stdout + r2.stderr)

    def test_acquire_steals_stale_lock_held_by_dead_pid(self):
        home = tempfile.mkdtemp()
        dp = dead_pid()
        r1 = run(["acquire", "--repo", self.repo, "--pid", str(dp)], home=home)
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        r2 = run(["acquire", "--repo", self.repo, "--pid", "424242"], home=home)
        self.assertEqual(r2.returncode, 0,
                         "must steal a dead holder's lock: " + r2.stdout + r2.stderr)
        log = Path(home) / "devel" / "airuleset" / "audits" / "autopilot-lock-steals.log"
        self.assertTrue(log.exists(), "the steal must be logged")
        self.assertIn(str(dp), log.read_text())

    def test_release_removes_owned_lock(self):
        r1 = run(["acquire", "--repo", self.repo, "--pid", "555555"])
        self.assertEqual(r1.returncode, 0, r1.stdout)
        r2 = run(["release", "--repo", self.repo, "--pid", "555555"])
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        r3 = run(["status", "--repo", self.repo])
        self.assertIn("UNLOCKED", r3.stdout)

    def test_release_refuses_when_not_owner(self):
        r1 = run(["acquire", "--repo", self.repo, "--pid", "111111"])
        self.assertEqual(r1.returncode, 0, r1.stdout)
        r2 = run(["release", "--repo", self.repo, "--pid", "222222"])
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        r3 = run(["status", "--repo", self.repo])
        self.assertIn("LOCKED", r3.stdout)  # still locked — refused release didn't touch it

    def test_release_idempotent_when_already_unlocked(self):
        r = run(["release", "--repo", self.repo, "--pid", "1"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_different_repos_get_independent_locks(self):
        repo_b = tempfile.mkdtemp()
        r1 = run(["acquire", "--repo", self.repo, "--pid", "777"])
        r2 = run(["acquire", "--repo", repo_b, "--pid", "888"])
        self.assertEqual(r1.returncode, 0, r1.stdout)
        self.assertEqual(r2.returncode, 0, r2.stdout)

    def test_lock_path_stable_across_trailing_slash(self):
        # pid=1 (init) is guaranteed to exist on any Linux box, so the second
        # acquire below is unambiguously testing "same lock file, live
        # holder" rather than depending on an arbitrary pid happening to be
        # alive on whatever machine runs this test.
        r1 = run(["acquire", "--repo", self.repo, "--pid", "1"])
        self.assertEqual(r1.returncode, 0, r1.stdout)
        r2 = run(["acquire", "--repo", self.repo + "/", "--pid", "444"])
        self.assertEqual(r2.returncode, 1,
                         "trailing slash must resolve to the SAME lock file: "
                         + r2.stdout + r2.stderr)


class TestDirectoryShapedLockPath(TestCase):
    """(#248) `acquire` used to crash with an unhandled IsADirectoryError
    when the lock path already exists as a DIRECTORY — a stale artifact of
    an older mkdir-style lock implementation, or any manual mkdir. Hit live
    on dev2 (presenter repo, 2026-08-05): the path was an EMPTY directory,
    `rmdir` + retry fixed it manually — the command should self-heal instead:
    an EMPTY directory is a stale artifact (removed, acquire proceeds); a
    NON-EMPTY one is an error with a clear message, never a traceback."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()

    def _lock_path(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        return airuleset._autopilot_lock_path(self.repo)

    def test_empty_directory_at_lock_path_self_heals(self):
        lp = self._lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.mkdir()                      # the exact stale artifact from the ticket
        r = run(["acquire", "--repo", self.repo, "--pid", "999999999"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACQUIRED", r.stdout)
        self.assertFalse(lp.is_dir(), "the stale directory must be gone")
        self.assertTrue(lp.is_file(), "a real lock file must exist now")

    def test_non_empty_directory_at_lock_path_refuses_cleanly(self):
        lp = self._lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.mkdir()
        (lp / "unexpected-file").write_text("do not touch")
        r = run(["acquire", "--repo", self.repo, "--pid", "999999999"])
        self.assertNotEqual(r.returncode, 0,
                            "a non-empty directory must never be silently removed")
        self.assertNotIn("Traceback", r.stdout + r.stderr,
                         "must refuse cleanly, never crash: " + r.stdout + r.stderr)
        self.assertTrue(lp.is_dir(), "the non-empty directory must be left alone")
        self.assertTrue((lp / "unexpected-file").exists())

    def test_a_symlink_to_an_empty_directory_refuses_cleanly_never_crashes(self):
        # (adversarial-review finding on #248 — MINOR) `rmdir()` on a
        # SYMLINK whose target is an empty directory raises
        # NotADirectoryError even though `is_dir()`/`iterdir()` both report
        # it as a normal, empty directory — verified empirically. The
        # original fix only wrapped `iterdir()` in try/except, leaving
        # `rmdir()` itself able to crash the exact same way the ticket was
        # filed about, just via a rarer filesystem shape.
        lp = self._lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        real_dir = lp.parent / (lp.name + "-real-target")
        real_dir.mkdir()
        lp.symlink_to(real_dir, target_is_directory=True)
        r = run(["acquire", "--repo", self.repo, "--pid", "999999999"])
        self.assertNotIn("Traceback", r.stdout + r.stderr,
                         "must refuse cleanly, never crash: " + r.stdout + r.stderr)

    def test_status_on_a_directory_shaped_lock_path_never_crashes(self):
        lp = self._lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.mkdir()
        r = run(["status", "--repo", self.repo])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)


class TestLockDirEnvOverride(TestCase):
    """(#385) `_autopilot_lock_path()` hardcodes the REAL system tempdir with
    no override. Every test in `TestAcquireRelease`/`TestDirectoryShapedLockPath`
    above spawns a REAL `autopilot-lock acquire` subprocess against a fresh
    `tempfile.mkdtemp()` repo path — since the lock path is a hash of that
    (never-reused) repo path and nothing ever deletes it, each test run leaves
    a permanent, un-owned lock/mutex/symlink/directory artifact in production
    `/tmp` (thousands measured live, see the issue). `AIRULESET_AUTOPILOT_LOCK_DIR`
    redirects the lock DIRECTORY itself; unset (production) is byte-for-byte
    unchanged."""

    def test_env_override_redirects_the_lock_path(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        import os as _os
        override = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, override, ignore_errors=True)
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        old = _os.environ.get("AIRULESET_AUTOPILOT_LOCK_DIR")
        _os.environ["AIRULESET_AUTOPILOT_LOCK_DIR"] = override
        try:
            lp = airuleset._autopilot_lock_path(repo)
        finally:
            if old is None:
                _os.environ.pop("AIRULESET_AUTOPILOT_LOCK_DIR", None)
            else:
                _os.environ["AIRULESET_AUTOPILOT_LOCK_DIR"] = old
        self.assertEqual(str(lp.parent), override,
                          "the env override must redirect the lock DIRECTORY")

    def test_no_override_falls_back_to_system_tempdir_unchanged(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        import os as _os
        import tempfile as _tempfile
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        old = _os.environ.pop("AIRULESET_AUTOPILOT_LOCK_DIR", None)
        try:
            lp = airuleset._autopilot_lock_path(repo)
        finally:
            if old is not None:
                _os.environ["AIRULESET_AUTOPILOT_LOCK_DIR"] = old
        self.assertEqual(str(lp.parent), _tempfile.gettempdir(),
                          "production (no override set) must be unchanged")

    def test_a_real_acquire_subprocess_writes_nothing_into_the_real_tempdir(self):
        """The actual anti-litter proof: run the REAL CLI subprocess (the
        exact shape every other test in this file uses) with the override
        env var set, and assert the artifact NEVER appears at the exact path
        it would have used under the REAL system tempdir. Uses a FRESH
        `mkdtemp()` repo path (never seen before) and asserts the would-be
        real path does NOT already exist before running — so this can never
        pass by silently hiding inside years of pre-existing litter
        (playbook #115 — the exact trap this ticket names)."""
        import hashlib
        import tempfile as _tempfile
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        real_hash = hashlib.sha1(str(Path(repo).resolve()).encode()).hexdigest()
        would_be_real_path = (Path(_tempfile.gettempdir())
                               / f"airuleset-autopilot-{real_hash}.lock")
        self.assertFalse(would_be_real_path.exists(),
                          "fixture must start from a never-before-seen repo "
                          "path, or this test could pass by luck")
        override = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, override, ignore_errors=True)
        r = run(["acquire", "--repo", repo, "--pid", "999999999"],
                extra_env={"AIRULESET_AUTOPILOT_LOCK_DIR": override})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(
            would_be_real_path.exists(),
            "must NEVER write into the real system tempdir when the "
            "override is set — this is the litter this ticket exists to stop")
        self.assertTrue(
            (Path(override) / f"airuleset-autopilot-{real_hash}.lock").exists(),
            "the lock must actually land under the override directory")


class TestWiring(TestCase):
    def test_registered_in_subcommands_table(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        self.assertIs(airuleset.SUBCOMMANDS["autopilot-lock"], airuleset.cmd_autopilot_lock)

    def test_wired_into_autopilot_skill_doc(self):
        text = (REPO / "skills" / "autopilot" / "SKILL.md").read_text()
        self.assertIn("autopilot-lock", text)

    def test_wired_into_worker_doc(self):
        text = (REPO / "agents" / "autopilot-worker.md").read_text()
        self.assertIn("autopilot-lock", text)


class TestCampaignPidAncestryWalk(TestCase):
    """(adversarial-review finding) `_campaign_pid()` must stay alive for
    the WHOLE autopilot campaign (acquire..release). It walks up from
    os.getppid() looking for the long-lived `claude` process. The OLD
    implementation walked exactly ONE hop up (`_proc_parent_pid(ppid)`,
    the "grandparent" of the current process) — correct ONLY when there is
    EXACTLY one ephemeral shell layer between this process and `claude`.
    An EXTRA shell layer (a `bash -c '...'` wrapper, or any nested
    invocation) makes that one-hop walk land on ANOTHER ephemeral shell
    instead of `claude` — that shell dies the instant its own tool call
    returns, so the recorded holder PID looks stale almost immediately,
    and a concurrent `/autopilot` session on the same repo steals the
    "live" lock (reintroducing the exact #8 collision this lock exists to
    prevent). The fix walks UP the ancestry an unbounded number of hops
    (bounded only as a safety cap) until it finds a process whose `comm`
    is a known long-lived one (`claude` / `node`), not a fixed hop count."""

    def setUp(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        self.airuleset = airuleset

    def test_single_shell_layer_still_returns_grandparent(self):
        # the common case (unchanged from before): one ephemeral shell
        # between this process and `claude` — walking up from ppid finds
        # `claude` after exactly one hop, same as the old grandparent-only
        # behavior.
        import unittest.mock as mock
        parents = {2000: 3000}
        comms = {2000: "bash", 3000: "claude"}
        with mock.patch.object(self.airuleset.os, "getppid", return_value=2000), \
             mock.patch.object(self.airuleset, "_proc_parent_pid",
                               side_effect=lambda p: parents.get(p)), \
             mock.patch.object(self.airuleset, "_proc_comm",
                               side_effect=lambda p: comms.get(p)):
            self.assertEqual(self.airuleset._campaign_pid(), 3000)

    def test_extra_shell_layer_still_finds_claude_not_the_extra_shell(self):
        # a `bash -c '...'` wrapper adds an EXTRA ephemeral shell layer
        # between this process and `claude`. Walking only ONE hop up (the
        # old, buggy behavior) would land on that extra shell (pid 3000,
        # comm "bash") instead of `claude` (pid 4000) — the lock would go
        # stale the instant that extra shell exits.
        import unittest.mock as mock
        parents = {2000: 3000, 3000: 4000}
        comms = {2000: "bash", 3000: "bash", 4000: "claude"}
        with mock.patch.object(self.airuleset.os, "getppid", return_value=2000), \
             mock.patch.object(self.airuleset, "_proc_parent_pid",
                               side_effect=lambda p: parents.get(p)), \
             mock.patch.object(self.airuleset, "_proc_comm",
                               side_effect=lambda p: comms.get(p)):
            self.assertEqual(self.airuleset._campaign_pid(), 4000)

    def test_no_claude_found_falls_back_to_last_known_pid(self):
        # /proc reads can fail (off-Linux, permission, the ancestry chain
        # genuinely ends) — must never crash, and must fall back to SOME
        # usable pid rather than None.
        import unittest.mock as mock
        with mock.patch.object(self.airuleset.os, "getppid", return_value=2000), \
             mock.patch.object(self.airuleset, "_proc_parent_pid", return_value=None), \
             mock.patch.object(self.airuleset, "_proc_comm", return_value=None):
            self.assertEqual(self.airuleset._campaign_pid(), 2000)


class TestDiscoverAutopilotLockLitter(TestCase):
    """discover_autopilot_lock_litter() -- #409, a follow-up to #385.

    Uses the discovery function's own `lock_dir=` parameter directly (no
    subprocess, no AIRULESET_AUTOPILOT_LOCK_DIR env var needed at all) --
    fully isolated from the real system tempdir by construction, and from
    any other concurrent test/session on this box."""

    def setUp(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        self.airuleset = airuleset
        self.scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)

    def _lock_file(self, stem, payload, age_s=7200):
        import json
        import os as _os
        import time as _time
        p = Path(self.scratch) / ("airuleset-autopilot-%s.lock" % stem)
        p.write_text(json.dumps(payload))
        old = _time.time() - age_s
        _os.utime(p, (old, old))
        return p

    def _mutex(self, stem, age_s=7200):
        import os as _os
        import time as _time
        p = Path(self.scratch) / ("airuleset-autopilot-%s.lock.mutex" % stem)
        p.write_text("")
        old = _time.time() - age_s
        _os.utime(p, (old, old))
        return p

    def _named_dir(self, name, age_s=7200, empty=True):
        import os as _os
        import time as _time
        p = Path(self.scratch) / name
        p.mkdir()
        if not empty:
            (p / "x").write_text("x")
        old = _time.time() - age_s
        _os.utime(p, (old, old))
        return p

    def _discover(self, min_age_s=0):
        return self.airuleset.discover_autopilot_lock_litter(
            lock_dir=self.scratch, min_age_s=min_age_s)

    def test_dead_pid_lock_file_is_litter(self):
        dp = dead_pid()
        self._lock_file("aaa", {"pid": dp, "repo": "/tmp/whatever"})
        rows = [r for r in self._discover() if r["kind"] == "lock"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["reason"])

    def test_live_pid_lock_file_is_never_litter(self):
        import os
        self._lock_file("bbb", {"pid": os.getpid(), "repo": "/tmp/whatever"})
        rows = [r for r in self._discover() if r["kind"] == "lock"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["reason"])
        self.assertIn("alive", rows[0]["reason"])

    def test_too_recent_lock_is_excluded_regardless_of_dead_pid(self):
        dp = dead_pid()
        self._lock_file("ccc", {"pid": dp, "repo": "/tmp/x"}, age_s=10)
        rows = [r for r in self._discover(min_age_s=3600) if r["kind"] == "lock"]
        self.assertEqual(len(rows), 1)
        self.assertIn("too recent", rows[0]["reason"])

    def test_empty_directory_shaped_lock_is_litter(self):
        self._named_dir("airuleset-autopilot-ddd.lock", empty=True)
        rows = [r for r in self._discover() if r["kind"] == "lock-dir"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["reason"])

    def test_non_empty_directory_shaped_lock_is_never_litter(self):
        self._named_dir("airuleset-autopilot-eee.lock", empty=False)
        rows = [r for r in self._discover() if r["kind"] == "lock-dir"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["reason"])

    def test_symlink_to_empty_real_target_is_litter(self):
        target = self._named_dir("airuleset-autopilot-fff.lock-real-target", empty=True)
        link = Path(self.scratch) / "airuleset-autopilot-fff.lock"
        link.symlink_to(target)
        rows = [r for r in self._discover() if r["path"] == str(link)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "lock-symlink")
        self.assertIsNone(rows[0]["reason"])

    def test_symlink_to_non_empty_real_target_is_never_litter(self):
        target = self._named_dir("airuleset-autopilot-ggg.lock-real-target", empty=False)
        link = Path(self.scratch) / "airuleset-autopilot-ggg.lock"
        link.symlink_to(target)
        rows = [r for r in self._discover() if r["path"] == str(link)]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["reason"])

    def test_mutex_with_no_base_lock_is_litter(self):
        self._mutex("hhh")
        rows = [r for r in self._discover() if r["kind"] == "mutex"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["reason"])

    def test_mutex_with_a_still_alive_base_lock_is_never_litter(self):
        import os
        self._lock_file("iii", {"pid": os.getpid(), "repo": "/tmp/x"})
        self._mutex("iii")
        rows = [r for r in self._discover() if r["kind"] == "mutex"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["reason"])
        self.assertIn("not litter", rows[0]["reason"])

    def test_mutex_with_a_confirmed_litter_base_lock_is_also_litter(self):
        dp = dead_pid()
        self._lock_file("jjj", {"pid": dp, "repo": "/tmp/x"})
        self._mutex("jjj")
        rows = [r for r in self._discover() if r["kind"] == "mutex"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["reason"])

    def test_real_target_orphaned_with_no_symlink_is_litter(self):
        self._named_dir("airuleset-autopilot-kkk.lock-real-target", empty=True)
        rows = [r for r in self._discover() if r["kind"] == "real-target"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["reason"])

    def test_real_target_non_empty_is_never_litter(self):
        self._named_dir("airuleset-autopilot-lll.lock-real-target", empty=False)
        rows = [r for r in self._discover() if r["kind"] == "real-target"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["reason"])

    def test_nonexistent_lock_dir_returns_empty_list(self):
        rows = self.airuleset.discover_autopilot_lock_litter(
            lock_dir=str(Path(self.scratch) / "does-not-exist"), min_age_s=0)
        self.assertEqual(rows, [])

    def test_unrelated_files_in_the_directory_are_ignored(self):
        (Path(self.scratch) / "unrelated-file.txt").write_text("x")
        (Path(self.scratch) / "airuleset-something-else.lock").write_text("x")
        self.assertEqual(self._discover(), [])

    def test_a_fifo_shaped_lock_is_refused_not_hung(self):
        # #409 review finding 1: a FIFO/socket/device node matching this
        # name pattern used to HANG FOREVER inside _autopilot_lock_read()'s
        # open()/read() (a FIFO blocks waiting for a writer that never
        # comes) -- proven live via a REAL SEPARATE subprocess with a hard
        # wall-clock timeout (a `subprocess.run(..., timeout=8)` against the
        # pre-fix code genuinely raised TimeoutExpired and had to be killed
        # -- confirmed live during review, not merely reasoned about).
        #
        # An in-process SIGALRM guard was tried FIRST and rejected: a
        # blocked open()/read() interrupted by SIGALRM raises
        # InterruptedError/OSError from INSIDE _autopilot_lock_read()'s own
        # `except Exception: return {}` -- so the alarm's own exception is
        # SWALLOWED there, and the caller sees a fast, clean "not alive"
        # result indistinguishable from "never hung at all". Assert
        # STRUCTURALLY instead: the S_ISREG guard must short-circuit BEFORE
        # _autopilot_lock_read is ever called on a non-regular-file path --
        # this has zero timing dependency and cannot itself hang.
        import os as _os
        import time as _time
        import unittest.mock as mock
        fifo_path = Path(self.scratch) / "airuleset-autopilot-mmm.lock"
        _os.mkfifo(str(fifo_path))
        stamp = _time.time() - 7200
        _os.utime(str(fifo_path), (stamp, stamp))

        def _must_not_be_called(p):
            raise AssertionError(
                "_autopilot_lock_read must never be called on a non-regular "
                "file -- the S_ISREG guard has to refuse it first")

        with mock.patch.object(self.airuleset, "_autopilot_lock_read",
                               side_effect=_must_not_be_called):
            rows = self._discover()
        fifo_rows = [r for r in rows if r.get("path") == str(fifo_path)]
        self.assertEqual(len(fifo_rows), 1)
        self.assertIsNotNone(fifo_rows[0].get("reason"))
        self.assertIn("not a regular file", fifo_rows[0]["reason"])

    def test_a_foreign_owned_lock_file_is_refused(self):
        # #409 review finding 6: /tmp is sticky-bit -- a foreign-owned
        # artifact can never be unlinked by this sweep's own uid anyway, so
        # refusing it at DISCOVERY time (rather than attempting-and-failing
        # every 24h sweep, forever) avoids permanent unactionable
        # "delete failed" churn on the shared subdev/gk boxes (3 managed
        # users each). Simulate "foreign owner" by patching os.getuid() to
        # a value that can never match this real file's real uid.
        import unittest.mock as mock
        dp = dead_pid()
        p = self._lock_file("nnn", {"pid": dp, "repo": "/tmp/x"})
        with mock.patch.object(self.airuleset.os, "getuid", return_value=-1):
            rows = self._discover()
        lock_rows = [r for r in rows if r.get("path") == str(p)]
        self.assertEqual(len(lock_rows), 1)
        self.assertIn("owned by another user", lock_rows[0]["reason"])

    def test_a_foreign_owned_mutex_is_refused(self):
        import unittest.mock as mock
        p = self._mutex("ooo")
        with mock.patch.object(self.airuleset.os, "getuid", return_value=-1):
            rows = self._discover()
        mutex_rows = [r for r in rows if r.get("path") == str(p)]
        self.assertEqual(len(mutex_rows), 1)
        self.assertIn("owned by another user", mutex_rows[0]["reason"])

    def test_a_foreign_owned_real_target_dir_is_refused(self):
        import unittest.mock as mock
        p = self._named_dir("airuleset-autopilot-ppp.lock-real-target", empty=True)
        with mock.patch.object(self.airuleset.os, "getuid", return_value=-1):
            rows = self._discover()
        target_rows = [r for r in rows if r.get("path") == str(p)]
        self.assertEqual(len(target_rows), 1)
        self.assertIn("owned by another user", target_rows[0]["reason"])


class TestSweepAutopilotLockLitter(TestCase):
    """sweep_autopilot_lock_litter() -- #409. Discovery already isolates
    via `lock_dir=`; the sweep function accepts the SAME parameter, so no
    subprocess/env-var isolation is needed here either."""

    def setUp(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        self.airuleset = airuleset
        self.scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.log_path = Path(self.scratch) / "sweep.log"
        self.state_path = Path(self.scratch) / "sweep-state.json"

    def _lock_file(self, stem, payload, age_s=7200):
        import json
        import os as _os
        import time as _time
        p = Path(self.scratch) / ("airuleset-autopilot-%s.lock" % stem)
        p.write_text(json.dumps(payload))
        old = _time.time() - age_s
        _os.utime(p, (old, old))
        return p

    def _mutex(self, stem, age_s=7200):
        import os as _os
        import time as _time
        p = Path(self.scratch) / ("airuleset-autopilot-%s.lock.mutex" % stem)
        p.write_text("")
        old = _time.time() - age_s
        _os.utime(p, (old, old))
        return p

    def _named_dir(self, name, age_s=7200, empty=True):
        import os as _os
        import time as _time
        p = Path(self.scratch) / name
        p.mkdir()
        if not empty:
            (p / "x").write_text("x")
        old = _time.time() - age_s
        _os.utime(p, (old, old))
        return p

    def _sweep(self, **kw):
        kw.setdefault("lock_dir", self.scratch)
        kw.setdefault("log_path", self.log_path)
        kw.setdefault("state_path", self.state_path)
        kw.setdefault("force", True)
        kw.setdefault("min_age_s", 0)
        return self.airuleset.sweep_autopilot_lock_litter(**kw)

    def test_removes_a_genuine_litter_lock_file(self):
        dp = dead_pid()
        p = self._lock_file("aaa", {"pid": dp, "repo": "/tmp/x"})
        results = self._sweep()
        self.assertTrue(any(r.get("removed") for r in results))
        self.assertFalse(p.exists())
        self.assertTrue(self.log_path.exists())

    def test_never_removes_a_still_alive_lock_file(self):
        import os
        p = self._lock_file("bbb", {"pid": os.getpid(), "repo": "/tmp/x"})
        results = self._sweep()
        self.assertFalse(any(r.get("removed") for r in results))
        self.assertTrue(p.exists(), "a still-alive lock must never be deleted")

    def test_dry_run_removes_nothing(self):
        dp = dead_pid()
        p = self._lock_file("ccc", {"pid": dp, "repo": "/tmp/x"})
        results = self._sweep(dry_run=True)
        self.assertTrue(p.exists())
        self.assertTrue(any("would remove" in (r.get("reason") or "") for r in results))
        self.assertFalse(self.state_path.exists(),
                         "a dry-run must never write the cadence state file")

    def test_cadence_gate_skips_a_second_non_forced_call(self):
        dp = dead_pid()
        self._lock_file("ddd", {"pid": dp, "repo": "/tmp/x"})
        r1 = self._sweep(force=False)
        self.assertTrue(any(r.get("removed") for r in r1))
        # A second lock appears; the cadence gate (state file just written)
        # must skip this call entirely -- returns [] without even looking.
        self._lock_file("eee", {"pid": dead_pid(), "repo": "/tmp/y"})
        r2 = self._sweep(force=False)
        self.assertEqual(r2, [])

    def test_force_bypasses_the_cadence_gate(self):
        dp = dead_pid()
        self._lock_file("fff", {"pid": dp, "repo": "/tmp/x"})
        r1 = self._sweep(force=False)
        self.assertTrue(any(r.get("removed") for r in r1))
        self._lock_file("ggg", {"pid": dead_pid(), "repo": "/tmp/y"})
        r2 = self._sweep(force=True)
        self.assertTrue(any(r.get("removed") for r in r2),
                        "force=True must always bypass the cadence gate")

    def test_toctou_recheck_refuses_a_lock_that_became_alive_since_discovery(self):
        import unittest.mock as mock
        dp = dead_pid()
        self._lock_file("hhh", {"pid": dp, "repo": "/tmp/x"})
        real_alive = self.airuleset._pid_alive
        calls = {"n": 0}

        def flip_alive(pid):
            calls["n"] += 1
            if calls["n"] > 1:   # discovery's own call sees dead; the
                return True      # sweep's re-check call sees alive
            return real_alive(pid)

        with mock.patch.object(self.airuleset, "_pid_alive", side_effect=flip_alive):
            results = self._sweep()
        self.assertFalse(any(r.get("removed") for r in results))
        self.assertTrue((Path(self.scratch) / "airuleset-autopilot-hhh.lock").exists())

    # --- #409 review finding 4: the mutex/lock-dir/real-target/lock-symlink
    # DELETE branches had zero coverage -- mutation-confirmed to be no-ops
    # that pass the whole file (M5/M6/M7/M9/M15/M17). Each test below drives
    # a REAL (non-dry-run) sweep through one specific delete branch and
    # asserts the artifact is actually gone, not merely that `removed` is
    # truthy in the returned dict.

    def test_removes_a_genuine_litter_mutex(self):
        p = self._mutex("iii")   # no base .lock at all -- litter by definition
        results = self._sweep()
        mutex_rows = [r for r in results if r.get("kind") == "mutex"]
        self.assertEqual(len(mutex_rows), 1)
        self.assertTrue(mutex_rows[0].get("removed"))
        self.assertFalse(p.exists(), "the mutex file must actually be gone")

    def test_removes_an_empty_legacy_lock_dir(self):
        p = self._named_dir("airuleset-autopilot-jjj.lock", empty=True)
        results = self._sweep()
        dir_rows = [r for r in results if r.get("kind") == "lock-dir"]
        self.assertEqual(len(dir_rows), 1)
        self.assertTrue(dir_rows[0].get("removed"))
        self.assertFalse(p.exists(), "the empty legacy lock directory must actually be gone")

    def test_removes_an_orphaned_empty_real_target_dir(self):
        p = self._named_dir("airuleset-autopilot-kkk.lock-real-target", empty=True)
        results = self._sweep()
        target_rows = [r for r in results if r.get("kind") == "real-target"]
        self.assertEqual(len(target_rows), 1)
        self.assertTrue(target_rows[0].get("removed"))
        self.assertFalse(p.exists(), "the orphaned empty real-target directory must actually be gone")

    def test_removes_a_symlink_to_an_empty_real_target(self):
        target = self._named_dir("airuleset-autopilot-lll.lock-real-target", empty=True)
        link = Path(self.scratch) / "airuleset-autopilot-lll.lock"
        link.symlink_to(target)
        import os as _os
        import time as _time
        old = _time.time() - 7200
        _os.utime(str(link), (old, old), follow_symlinks=False)
        results = self._sweep()
        symlink_rows = [r for r in results if r.get("kind") == "lock-symlink"]
        self.assertEqual(len(symlink_rows), 1)
        self.assertTrue(symlink_rows[0].get("removed"))
        self.assertFalse(link.is_symlink(), "the symlink itself must actually be gone")
        self.assertFalse(target.exists(),
                         "its paired real-target directory is ALSO its own "
                         "litter candidate (paired symlink confirmed litter) "
                         "and must be swept in the same pass")


class TestSweepAutopilotLocksCommand(TestCase):
    def test_registered_in_subcommands_table(self):
        sys.path.insert(0, str(REPO))
        import airuleset
        self.assertIs(airuleset.SUBCOMMANDS["sweep-autopilot-locks"],
                      airuleset.cmd_sweep_autopilot_locks)

    def test_cli_dry_run_reports_without_deleting(self):
        # #409 review finding 3: the original version of this test asserted
        # only that the fixture file still exists and that "would be"
        # appears SOMEWHERE in stdout -- both are true even if the CLI does
        # NOTHING at all (an empty sweep's own summary line unconditionally
        # reads "0 ... would be removed."). Mutation-confirmed: pointing
        # `lock_dir` at a nonexistent path, or having discovery ignore
        # AIRULESET_AUTOPILOT_LOCK_DIR entirely, both left the OLD
        # assertions green. Assert the SPECIFIC path is named under a real
        # "WOULD REMOVE" tag, and that the count is exactly 1.
        import json
        import os as _os
        import subprocess as _sp
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        dp = dead_pid()
        p = Path(scratch) / "airuleset-autopilot-zzz.lock"
        p.write_text(json.dumps({"pid": dp, "repo": "/tmp/x"}))
        stamp = _os.path.getmtime(p) - 7200
        _os.utime(p, (stamp, stamp))
        env = dict(_os.environ)
        env["AIRULESET_AUTOPILOT_LOCK_DIR"] = scratch
        r = _sp.run(
            [sys.executable, str(REPO / "airuleset.py"), "sweep-autopilot-locks",
             "--dry-run"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(p.exists(), "dry-run must never delete anything")
        self.assertIn("WOULD REMOVE: %s" % p, r.stdout,
                      "the specific candidate path must actually be named, "
                      "not just the word 'would' appearing anywhere")
        self.assertIn("1 autopilot-lock litter artifact(s) would be removed.",
                      r.stdout)



if __name__ == "__main__":
    main()
