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

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent


def run(args, home=None):
    import os
    env = dict(os.environ)
    if home:
        env["HOME"] = home
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


if __name__ == "__main__":
    main()
