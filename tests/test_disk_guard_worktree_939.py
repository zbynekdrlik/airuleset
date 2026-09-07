"""#939 — regression test for the dirty-tree worktree discriminator.

The disk_guard worktree rung (`_worktree_reclaimable` in cli_worktree_sweep.py)
must correctly classify three scenarios:

1. LIVE-PROCESS worktree: a process has cwd inside → KEPT (never removed)
2. PUSHED+DIRTY worktree: HEAD reachable from origin, dirty tree → DELETED
   (the work is preserved on origin; only scratch/temp files are lost)
3. UNPUSHED worktree: HEAD NOT reachable from origin → KEPT (work not preserved)

This test would have caught issue 935's Part A drop, which claimed the existing
rung "already covers" project-local worktrees. Live data proved it did not:
55 worktrees / 20G on david3, ZERO removed at 90-97% pressure, because the
dirty-tree gate (line 999 pre-fix) blocked 100% of returned workers' worktrees.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

# Allow import from repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup_repo_with_worktrees(tmp):
    """Build a git repo with three worktrees in a .claude/worktrees/ dir:

    - wt_live: a process (this test) will cd into it → live-use kept
    - wt_pushed_dirty: HEAD reachable from origin, dirty tree → reclaimable
    - wt_unpushed: HEAD NOT reachable from origin → kept

    Returns (repo_root, wt_live, wt_pushed_dirty, wt_unpushed).
    """
    repo = os.path.join(tmp, "fakerepo")
    os.makedirs(repo)

    def git(*args, cwd=repo):
        r = subprocess.run(["git"] + list(args), cwd=cwd,
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@test")
    git("config", "user.name", "Test")

    # Create initial commit on main.
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("hello\n")
    git("add", "README.md")
    git("commit", "-m", "initial")

    # Create a bare clone as "origin" to test reachability.
    origin = os.path.join(tmp, "origin.git")
    git("clone", "--bare", repo, origin, cwd=tmp)
    git("remote", "add", "origin", origin)
    git("fetch", "origin")

    # Create the worktree directory structure.
    wt_dir = os.path.join(repo, ".claude", "worktrees")
    os.makedirs(wt_dir, exist_ok=True)

    # --- wt_live: a branch whose worktree will have a live process cwd ---
    wt_live = os.path.join(wt_dir, "agent-live")
    git("worktree", "add", wt_live, "-b", "worktree-agent-live")
    # Touch a file to make it recent (within 24h).
    # Actually, for the live-process test, recency doesn't matter —
    # the live-use check fires first. But we set mtime old enough
    # so that if the live-use check were bypassed, the age gate passes.
    old_time = time.time() - 48 * 3600
    os.utime(wt_live, (old_time, old_time))
    for admin_rel in ("HEAD", "index"):
        admin_path = os.path.join(repo, ".git", "worktrees", "agent-live", admin_rel)
        if os.path.exists(admin_path):
            os.utime(admin_path, (old_time, old_time))

    # --- wt_pushed_dirty: HEAD reachable (pushed to origin), but dirty tree ---
    wt_pushed = os.path.join(wt_dir, "agent-pushed-dirty")
    git("worktree", "add", wt_pushed, "-b", "worktree-agent-pushed-dirty")
    # Make a commit, push to origin, then leave scratch files.
    with open(os.path.join(wt_pushed, "work.py"), "w") as f:
        f.write("# real work\n")
    git("add", "work.py", cwd=wt_pushed)
    git("commit", "-m", "real work", cwd=wt_pushed)
    # Push the branch to origin so HEAD is reachable.
    git("push", "origin", "worktree-agent-pushed-dirty", cwd=wt_pushed)
    # Leave a dirty file (scratch/temp).
    with open(os.path.join(wt_pushed, "scratch.tmp"), "w") as f:
        f.write("temp data\n")
    # Age it.
    os.utime(wt_pushed, (old_time, old_time))
    for admin_rel in ("HEAD", "index"):
        admin_path = os.path.join(repo, ".git", "worktrees", "agent-pushed-dirty", admin_rel)
        if os.path.exists(admin_path):
            os.utime(admin_path, (old_time, old_time))

    # --- wt_unpushed: HEAD NOT reachable from origin ---
    wt_unpushed = os.path.join(wt_dir, "agent-unpushed")
    git("worktree", "add", wt_unpushed, "-b", "worktree-agent-unpushed")
    # Make a commit but do NOT push.
    with open(os.path.join(wt_unpushed, "unpushed.py"), "w") as f:
        f.write("# unpushed work\n")
    git("add", "unpushed.py", cwd=wt_unpushed)
    git("commit", "-m", "unpushed work", cwd=wt_unpushed)
    # Age it.
    os.utime(wt_unpushed, (old_time, old_time))
    for admin_rel in ("HEAD", "index"):
        admin_path = os.path.join(repo, ".git", "worktrees", "agent-unpushed", admin_rel)
        if os.path.exists(admin_path):
            os.utime(admin_path, (old_time, old_time))

    return repo, wt_live, wt_pushed, wt_unpushed


class TestWorktreeDiscriminator939(unittest.TestCase):
    """The three-worktree regression test for issue 939."""

    def test_pushed_dirty_deleted_live_kept_unpushed_kept(self):
        """Build 3 worktrees in tmpdir:
        - live-process cwd → KEPT
        - pushed+dirty → DELETED (reason=None, the discriminator's signal)
        - unpushed → KEPT (HEAD not reachable from origin)

        Each with a decision-log line (reason is not None for kept ones).
        """
        from cli_worktree_sweep import _worktree_reclaimable, _worktree_sweep_base_branch

        with tempfile.TemporaryDirectory() as tmp:
            repo, wt_live, wt_pushed, wt_unpushed = _setup_repo_with_worktrees(tmp)

            base = _worktree_sweep_base_branch(repo)
            self.assertEqual(base, "main")

            now = time.time()

            # Simulate live-process for wt_live: inject a fake in_live_use
            # that returns True for wt_live and False for others.
            def fake_live_use(path):
                return os.path.realpath(path) == os.path.realpath(wt_live)

            # Simulate old recency for all three (>24h idle).
            def fake_recency(root, path, now_):
                return 48 * 3600  # 48h idle

            # --- wt_live: live process → KEPT ---
            row_live = _worktree_reclaimable(
                repo, wt_live, "worktree-agent-live", base, None, now,
                in_live_use=fake_live_use, recency_fn=fake_recency)
            self.assertIsNotNone(row_live["reason"],
                                 "live-process worktree should be KEPT (reason not None)")
            self.assertIn("live", row_live["reason"].lower())

            # --- wt_pushed_dirty: pushed + dirty → DELETED ---
            row_pushed = _worktree_reclaimable(
                repo, wt_pushed, "worktree-agent-pushed-dirty", base, None, now,
                in_live_use=lambda p: False, recency_fn=fake_recency)
            self.assertIsNone(row_pushed["reason"],
                              "pushed+dirty worktree should be DELETED (reason=None), "
                              "got: %s" % row_pushed.get("reason"))
            # Verify it knows about the dirty state via the reachable_via field.
            self.assertIsNotNone(row_pushed.get("reachable_via"),
                                 "reclaimable row should carry reachable_via")

            # --- wt_unpushed: NOT pushed → KEPT ---
            row_unpushed = _worktree_reclaimable(
                repo, wt_unpushed, "worktree-agent-unpushed", base, None, now,
                in_live_use=lambda p: False, recency_fn=fake_recency)
            self.assertIsNotNone(row_unpushed["reason"],
                                  "unpushed worktree should be KEPT (reason not None)")
            self.assertIn("not reachable", row_unpushed["reason"].lower())


class TestRemoveWorktreeDirForce939(unittest.TestCase):
    """The executor must use --force and skip the clean-tree re-check."""

    def test_dirty_worktree_removed_with_force(self):
        """A worktree classified reclaimable (dirty but reachable) must be
        removable by the executor — it uses --force, not the pre-#939 plain
        `git worktree remove` that refuses dirty trees."""
        from watchdog.disk_guard import _remove_worktree_dir

        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)

            def git(*args, cwd=repo):
                r = subprocess.run(["git"] + list(args), cwd=cwd,
                                   capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr))
                return r.stdout.strip()

            git("init", "-b", "main")
            git("config", "user.email", "test@test")
            git("config", "user.name", "Test")
            with open(os.path.join(repo, "README.md"), "w") as f:
                f.write("hello\n")
            git("add", "README.md")
            git("commit", "-m", "initial")

            wt_dir = os.path.join(repo, ".claude", "worktrees")
            os.makedirs(wt_dir, exist_ok=True)
            wt = os.path.join(wt_dir, "agent-dirty")
            git("worktree", "add", wt, "-b", "worktree-agent-dirty")

            # Make it dirty.
            with open(os.path.join(wt, "scratch.tmp"), "w") as f:
                f.write("temp\n")

            # The executor should succeed with --force.
            action = {"repo": repo, "path": wt}
            _remove_worktree_dir(action)

            # The worktree directory should be gone.
            self.assertFalse(os.path.exists(wt),
                             "dirty worktree should have been removed with --force")


class TestPrevPlanners939(unittest.TestCase):
    """The prevention-pass planners must include the worktree rung so it runs
    proactively on shared-stream boxes, not only under >=80% pressure."""

    def test_worktree_in_prevention_planners(self):
        from watchdog.disk_guard import _prevention_planners
        labels = [label for label, _ in _prevention_planners("/fake", 0)]
        self.assertIn("worktree", labels,
                      "worktree rung must be in _prevention_planners for proactive cleanup")


if __name__ == "__main__":
    unittest.main()
