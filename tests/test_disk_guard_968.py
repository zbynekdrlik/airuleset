"""Behaviour tests for #968 disk-guard additions: stale-agent-worktrees rung,
runner _diag top-level logs, scratch-worktree containment extension, dynamic
critical threshold, drain_exhausted_streak, swap warning.

Each test uses a synthetic HOME under a tempdir — no real disk reads, no sudo,
no real git. All seams injectable.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ======================================================================= #
# 1. stale-agent-worktrees discovery
# ======================================================================= #

class TestStaleAgentWorktrees(unittest.TestCase):
    """discover_stale_agent_worktrees finds finished agent-* worktrees."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_agent_wt(self, repo_name="proj", agent_name="agent-abc123",
                     locked=False, make_git=True, gitdir_path=None):
        """Create a simulated agent worktree under a repo's .claude/worktrees/."""
        repo = Path(self.home) / "devel" / repo_name
        (repo / ".git").mkdir(parents=True)
        wt_root = repo / ".claude" / "worktrees"
        wt = wt_root / agent_name
        wt.mkdir(parents=True)
        if make_git:
            gd = gitdir_path or str(repo / ".git" / "worktrees" / agent_name)
            (wt / ".git").write_text("gitdir: %s" % gd)
            Path(gd).mkdir(parents=True, exist_ok=True)
            if locked:
                (Path(gd) / "locked").write_text("locked by session")
        return str(wt), str(repo)

    def test_discovers_reclaimable_agent_worktree(self):
        """A clean, unlocked agent worktree with HEAD on origin is reclaimable."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        self._mk_agent_wt()
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: {
                ("status", "--porcelain"): "",
                ("branch", "-r", "--contains", "HEAD"): "  origin/main\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "worktree-agent-abc123",
            }.get(tuple(cmd), ""),
            dir_stats_fn=lambda p: (123_000_000, 0),
            live_check_fn=lambda p: False,
        )
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 1,
                         "should discover one reclaimable agent worktree")
        self.assertEqual(reclaimable[0]["cls"], "stale-agent-worktree")
        self.assertEqual(reclaimable[0]["kind"], "worktree-remove")
        self.assertEqual(reclaimable[0]["bytes"], 123_000_000)

    def test_skips_locked(self):
        """A locked agent worktree is kept."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        self._mk_agent_wt(locked=True)
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: "",
            live_check_fn=lambda p: False,
        )
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 0, "locked worktree should be kept")

    def test_skips_dirty(self):
        """A dirty agent worktree is kept."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        self._mk_agent_wt()
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: {
                ("status", "--porcelain"): "M dirty.py\n",
                ("rev-parse", "--abbrev-ref", "HEAD"): "worktree-agent-abc123",
            }.get(tuple(cmd), ""),
            live_check_fn=lambda p: False,
        )
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 0, "dirty worktree should be kept")

    def test_skips_not_on_origin(self):
        """A worktree whose HEAD is not on any origin ref is kept."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        self._mk_agent_wt()
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: {
                ("status", "--porcelain"): "",
                ("branch", "-r", "--contains", "HEAD"): "",
                ("rev-parse", "--abbrev-ref", "HEAD"): "worktree-agent-abc123",
            }.get(tuple(cmd), ""),
            live_check_fn=lambda p: False,
        )
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 0,
                         "worktree not on origin should be kept")

    def test_skips_live_use(self):
        """A worktree with a live process inside is kept."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        self._mk_agent_wt()
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: "",
            live_check_fn=lambda p: True,
        )
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 0,
                         "live-use worktree should be kept")

    def test_skips_protected_branch(self):
        """A worktree on main/dev is never removed."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        self._mk_agent_wt()
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: {
                ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            }.get(tuple(cmd), ""),
            live_check_fn=lambda p: False,
        )
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 0,
                         "main branch worktree should be kept")

    def test_ignores_non_agent_dirs(self):
        """Directories not named agent-* are ignored."""
        from watchdog.disk_guard_worktrees import discover_stale_agent_worktrees
        repo = Path(self.home) / "devel" / "proj"
        (repo / ".git").mkdir(parents=True)
        wt = repo / ".claude" / "worktrees" / "wt-something"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /fake")
        rows = discover_stale_agent_worktrees(
            home=self.home, now=time.time(),
            git_run_fn=lambda cmd, wt: "",
            live_check_fn=lambda p: False,
        )
        self.assertEqual(len(rows), 0, "non-agent dirs should be ignored")

    def test_rung_in_default_planners(self):
        """stale-agent-worktree rung appears in _default_planners BEFORE journal."""
        from watchdog.disk_guard import _default_planners
        now = time.time()
        planners = _default_planners(self.home, now)
        labels = [label for label, _ in planners]
        self.assertIn("stale-agent-worktree", labels,
                      "stale-agent-worktree must be in default planners")
        agent_idx = labels.index("stale-agent-worktree")
        journal_idx = labels.index("journal")
        self.assertLess(agent_idx, journal_idx,
                        "stale-agent-worktree must come BEFORE journal")

    def test_reclaimable_class_registered(self):
        """stale-agent-worktree is in RECLAIMABLE_CLASSES."""
        from watchdog.disk_guard import RECLAIMABLE_CLASSES
        self.assertIn("stale-agent-worktree", RECLAIMABLE_CLASSES)


# ======================================================================= #
# 2. runner _diag top-level logs
# ======================================================================= #

class TestRunnerDiagTopLevel(unittest.TestCase):
    """discover_runner_diag_logs also finds top-level _diag/Runner_*.log."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.runner_root = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_runner_diag(self, runner_name="actions-runner1",
                         filename="Runner_20260909.log", age_days=3):
        """Create a runner _diag log file."""
        diag = Path(self.tmp) / runner_name / "_diag"
        diag.mkdir(parents=True)
        logfile = diag / filename
        logfile.write_text("log data " * 1000)
        old = time.time() - age_days * 86400
        os.utime(str(logfile), (old, old))
        return str(logfile)

    def test_discovers_top_level_runner_log(self):
        """Top-level _diag/Runner_*.log is discovered."""
        from watchdog.disk_guard import discover_runner_diag_logs
        self._mk_runner_diag("actions-runner1", "Runner_20260901.log", age_days=3)
        rows = discover_runner_diag_logs(
            runner_root=self.runner_root, now=time.time(),
            min_age_days=1, pgrep_fn=lambda re: "")
        reclaimable = [r for r in rows
                       if r.get("reason") is None and "Runner_" in r["path"]]
        self.assertTrue(len(reclaimable) > 0,
                        "should discover top-level _diag/Runner_*.log")

    def test_discovers_top_level_worker_log(self):
        """Top-level _diag/Worker_*.log is discovered."""
        from watchdog.disk_guard import discover_runner_diag_logs
        self._mk_runner_diag("actions-runner1", "Worker_20260901.log", age_days=3)
        rows = discover_runner_diag_logs(
            runner_root=self.runner_root, now=time.time(),
            min_age_days=1, pgrep_fn=lambda re: "")
        reclaimable = [r for r in rows
                       if r.get("reason") is None and "Worker_" in r["path"]]
        self.assertTrue(len(reclaimable) > 0,
                        "should discover top-level _diag/Worker_*.log")

    def test_skips_recent_top_level_log(self):
        """A top-level _diag log younger than min_age is kept."""
        from watchdog.disk_guard import discover_runner_diag_logs
        self._mk_runner_diag("actions-runner1", "Runner_20260909.log", age_days=0)
        rows = discover_runner_diag_logs(
            runner_root=self.runner_root, now=time.time(),
            min_age_days=1, pgrep_fn=lambda re: "")
        reclaimable = [r for r in rows if r.get("reason") is None]
        self.assertEqual(len(reclaimable), 0,
                         "recent top-level log should be kept")


# ======================================================================= #
# 3. scratch-worktree containment extension
# ======================================================================= #

class TestScratchWorktreeContainment(unittest.TestCase):
    """scratch-worktrees discovers worktrees whose HEAD is on origin
    (containment) even if they have commits ahead of upstream."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.uid = os.getuid()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_scratch_wt(self, name="wt1234", age_s=8000):
        scratch = Path(self.tmp) / ("claude-%d" % self.uid) / "cwdkey" / "sessuuid" / "scratchpad"
        wt = scratch / name
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /fake/repo/.git/worktrees/" + name)
        old = time.time() - age_s
        os.utime(str(wt), (old, old))
        return str(wt)

    def test_reclaimable_when_ahead_but_contained(self):
        """A scratch worktree with commits ahead but HEAD on origin is reclaimable."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wt5555", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: (100_000, 0),
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 2,  # 2 commits ahead
            contained_fn=lambda wt_path: True,  # but HEAD on origin
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertTrue(len(reclaimable) > 0,
                        "ahead-but-contained should be reclaimable")

    def test_kept_when_ahead_and_not_contained(self):
        """A scratch worktree with commits ahead and NOT on origin is kept."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wt6666", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: (100_000, 0),
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 2,
            contained_fn=lambda wt_path: False,
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertEqual(len(reclaimable), 0,
                         "ahead-and-not-contained should be kept")

    def test_scratch_worktree_rung_on_workstation(self):
        """scratch-worktree rung is in _default_planners (every box class)."""
        from watchdog.disk_guard import _default_planners
        now = time.time()
        planners = _default_planners(self.tmp, now)
        labels = [label for label, _ in planners]
        self.assertIn("scratch-worktree", labels,
                      "scratch-worktree must be in default planners")


# ======================================================================= #
# 4. dynamic critical threshold
# ======================================================================= #

class TestDynamicCriticalThreshold(unittest.TestCase):
    """effective_critical_pct returns 85 on small disks, 90 on large."""

    def test_small_disk_returns_85(self):
        from watchdog.disk_guard_worktrees import effective_critical_pct

        class FakeStatvfs:
            f_frsize = 4096
            f_blocks = 38 * 1024 * 1024 * 1024 // 4096  # 38 GB

        self.assertEqual(effective_critical_pct(statvfs_fn=lambda p: FakeStatvfs()), 85)

    def test_large_disk_returns_90(self):
        from watchdog.disk_guard_worktrees import effective_critical_pct

        class FakeStatvfs:
            f_frsize = 4096
            f_blocks = 500 * 1024 * 1024 * 1024 // 4096  # 500 GB

        self.assertEqual(effective_critical_pct(statvfs_fn=lambda p: FakeStatvfs()), 90)

    def test_64gb_boundary_returns_85(self):
        from watchdog.disk_guard_worktrees import effective_critical_pct

        class FakeStatvfs:
            f_frsize = 4096
            f_blocks = 64 * 1024 * 1024 * 1024 // 4096  # exactly 64 GB

        self.assertEqual(effective_critical_pct(statvfs_fn=lambda p: FakeStatvfs()), 85)

    def test_error_returns_default_90(self):
        from watchdog.disk_guard_worktrees import effective_critical_pct
        self.assertEqual(effective_critical_pct(statvfs_fn=lambda p: (_ for _ in ()).throw(OSError())), 90)


# ======================================================================= #
# 5. drain_exhausted_streak
# ======================================================================= #

class TestDrainExhaustedStreak(unittest.TestCase):
    """drain_exhausted_streak increments on zero-yield drains at critical."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_streak_shown_at_2(self):
        """statusbar shows disk badge when drain_exhausted_streak >= 2."""
        import statusbar
        from watchdog.disk_guard import _guard_dir, STATUS_CACHE_NAME
        gd = _guard_dir(self.home)
        gd.mkdir(parents=True, exist_ok=True)
        now = time.time()
        # 92% with streak 2 — should show badge even though < 95%
        status = {"worst_pct": 92, "ts": now, "drain_exhausted_streak": 2}
        (gd / STATUS_CACHE_NAME).write_text(json.dumps(status))
        seg = statusbar.disk_segment(home=self.home, now=now)
        self.assertIn("disk 92%", seg,
                      "badge should show at streak >= 2 even below 95%")

    def test_streak_1_not_shown(self):
        """statusbar does NOT show badge for streak 1."""
        import statusbar
        from watchdog.disk_guard import _guard_dir, STATUS_CACHE_NAME
        gd = _guard_dir(self.home)
        gd.mkdir(parents=True, exist_ok=True)
        now = time.time()
        status = {"worst_pct": 92, "ts": now, "drain_exhausted_streak": 1}
        (gd / STATUS_CACHE_NAME).write_text(json.dumps(status))
        seg = statusbar.disk_segment(home=self.home, now=now)
        self.assertEqual(seg, "",
                         "badge should NOT show for streak 1 below 95%")

    def test_legacy_bool_still_works(self):
        """Legacy drain_exhausted=True still triggers badge (backwards compat)."""
        import statusbar
        from watchdog.disk_guard import _guard_dir, STATUS_CACHE_NAME
        gd = _guard_dir(self.home)
        gd.mkdir(parents=True, exist_ok=True)
        now = time.time()
        status = {"worst_pct": 92, "ts": now, "drain_exhausted": True}
        (gd / STATUS_CACHE_NAME).write_text(json.dumps(status))
        seg = statusbar.disk_segment(home=self.home, now=now)
        self.assertIn("disk 92%", seg,
                      "legacy drain_exhausted=True should still trigger")


# ======================================================================= #
# 6. swap warning
# ======================================================================= #

class TestSwapWarning(unittest.TestCase):
    """check_swap_warning reports when swapfile > 2x MemTotal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_meminfo(self, mem_kb):
        p = os.path.join(self.tmp, "meminfo")
        with open(p, "w") as f:
            f.write("MemTotal:     %d kB\n" % mem_kb)
            f.write("MemFree:      1000 kB\n")
        return p

    def _write_swapfile(self, size_bytes):
        p = os.path.join(self.tmp, "swapfile")
        with open(p, "wb") as f:
            f.seek(size_bytes - 1)
            f.write(b"\0")
        return p

    def test_warns_when_swap_oversized(self):
        """Warns when swapfile > 2x MemTotal."""
        from watchdog.disk_guard_worktrees import check_swap_warning
        meminfo = self._write_meminfo(4_000_000)  # 4 GB
        swapfile = self._write_swapfile(10_000_000_000)  # 10 GB > 2*4=8 GB
        result = check_swap_warning(meminfo_path=meminfo, swapfile_path=swapfile)
        self.assertIsNotNone(result)
        self.assertTrue(result["swap_oversized"])
        self.assertGreater(result["ratio"], 2.0)

    def test_no_warning_when_swap_normal(self):
        """No warning when swapfile <= 2x MemTotal."""
        from watchdog.disk_guard_worktrees import check_swap_warning
        meminfo = self._write_meminfo(4_000_000)  # 4 GB
        swapfile = self._write_swapfile(4_000_000_000)  # 4 GB <= 2*4=8 GB
        result = check_swap_warning(meminfo_path=meminfo, swapfile_path=swapfile)
        self.assertIsNone(result)

    def test_no_warning_when_no_swapfile(self):
        """No warning when swapfile doesn't exist."""
        from watchdog.disk_guard_worktrees import check_swap_warning
        meminfo = self._write_meminfo(4_000_000)
        result = check_swap_warning(meminfo_path=meminfo,
                                     swapfile_path="/nonexistent/swapfile")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
