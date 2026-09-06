"""#906 — disk-guard: top-consumers report blind on /home/* trees +
stale-worktree drain rung for cross-user worktrees.

Hermetic tests ONLY — fake /proc, fake worktree fixtures under tmp dirs,
NO real sudo, NO real /home, NO real drains.
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the project root is on sys.path so watchdog/ and cli_* are importable.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from watchdog import disk_guard as dg  # noqa: E402


_NOW = time.time()


# ============================================================================
# FIX 1: Top-consumers report must cover /home/* trees
# ============================================================================

class TestTopConsumersHomeTreeCoverage(unittest.TestCase):
    """The top-consumers report must see worktree dirs under /home/*/devel/,
    not just the calling user's own $HOME planners."""

    def setUp(self):
        # Pin box class to workstation so toolchain-gated tests don't fail
        patch("watchdog.reaper.default_box_class", return_value="workstation").start()
        self.addCleanup(patch.stopall)

    def test_collect_top_consumers_includes_home_worktree_class(self):
        """_collect_top_consumers must include a 'home-worktree' planner so
        cross-user worktree trees appear in the report.  This fails RED
        before #906 because the planner list has no home-worktree entry."""
        # Verify the function at least TRIES to include home-worktree data.
        # We look at the planner labels inside _collect_top_consumers.
        import inspect
        src = inspect.getsource(dg._collect_top_consumers)
        self.assertIn("home-worktree", src,
                      "#906: _collect_top_consumers must include a 'home-worktree' "
                      "planner to cover /home/*/devel worktree trees")

    def test_ranked_consumers_includes_home_worktree_class(self):
        """_ranked_consumers must include a 'home-worktree' class so the
        escalation summary covers cross-user worktree trees."""
        import inspect
        src = inspect.getsource(dg._ranked_consumers)
        self.assertIn("home-worktree", src,
                      "#906: _ranked_consumers must include a 'home-worktree' "
                      "class to cover /home/* trees in the escalation summary")

    def test_discover_home_worktree_consumers_exists(self):
        """The discovery function for cross-user home worktree trees must exist."""
        self.assertTrue(hasattr(dg, "discover_home_worktree_consumers"),
                        "#906: disk_guard must expose discover_home_worktree_consumers")

    def test_discover_home_worktree_consumers_returns_list(self):
        """discover_home_worktree_consumers returns a list of action dicts."""
        fn = getattr(dg, "discover_home_worktree_consumers", None)
        if fn is None:
            self.fail("#906: discover_home_worktree_consumers not found")
        # With a non-existent glob, returns empty list
        result = fn(home_glob="/nonexistent-path-906-*")
        self.assertIsInstance(result, list)


# ============================================================================
# FIX 2: Stale-worktree drain rung
# ============================================================================

class TestStaleHomeWorktreeDiscovery(unittest.TestCase):
    """discover_stale_home_worktrees must enumerate /home/*/devel/**/.claude/worktrees/*
    with the verified #905 mechanics."""

    def setUp(self):
        patch("watchdog.reaper.default_box_class", return_value="workstation").start()
        self.addCleanup(patch.stopall)

    def test_discover_fn_exists(self):
        self.assertTrue(hasattr(dg, "discover_stale_home_worktrees"),
                        "#906: disk_guard must expose discover_stale_home_worktrees")

    def test_empty_on_no_homes(self):
        """No /home/* dirs -> empty list."""
        fn = getattr(dg, "discover_stale_home_worktrees", None)
        if fn is None:
            self.fail("#906: discover_stale_home_worktrees not found")
        result = fn(now=_NOW, home_glob="/nonexistent-906-*")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_skip_active_process_cwd(self):
        """A worktree with an active process cwd inside it must be SKIPPED."""
        fn = getattr(dg, "discover_stale_home_worktrees", None)
        if fn is None:
            self.fail("#906: discover_stale_home_worktrees not found")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # Build fake /home/user1/devel/repo/.claude/worktrees/agent-1
            wt = Path(td) / "user1" / "devel" / "repo" / ".claude" / "worktrees" / "agent-1"
            wt.mkdir(parents=True)
            # Write .git FIRST, then set old mtime (writing .git updates dir mtime)
            (wt / ".git").write_text("gitdir: /fake")
            old_time = _NOW - 2 * 86400
            os.utime(str(wt), (old_time, old_time))

            def fake_proc_cwds():
                return ({str(wt)}, set())

            result = fn(
                now=_NOW,
                home_glob=str(Path(td) / "*"),
                proc_cwd_fn=fake_proc_cwds,
                git_run_fn=lambda *a, **k: "",  # clean
                dir_size_fn=lambda p: 1000,
            )
            skipped = [r for r in result if r.get("reason") and "active" in r["reason"].lower()]
            self.assertTrue(len(skipped) > 0,
                            "#906: worktree with active process cwd must be SKIPPED")

    def test_skip_dirty_worktree(self):
        """A worktree with non-empty porcelain must be SKIPPED."""
        fn = getattr(dg, "discover_stale_home_worktrees", None)
        if fn is None:
            self.fail("#906: discover_stale_home_worktrees not found")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "user1" / "devel" / "repo" / ".claude" / "worktrees" / "agent-1"
            wt.mkdir(parents=True)
            # Write .git FIRST, then set old mtime
            (wt / ".git").write_text("gitdir: /fake")
            old_time = _NOW - 2 * 86400
            os.utime(str(wt), (old_time, old_time))

            def fake_git_run(args, **kw):
                # Return dirty status for porcelain checks
                return " M file.py"

            result = fn(
                now=_NOW,
                home_glob=str(Path(td) / "*"),
                proc_cwd_fn=lambda: (set(), set()),
                git_run_fn=fake_git_run,
                dir_size_fn=lambda p: 1000,
            )
            skipped = [r for r in result if r.get("reason") and "dirty" in r["reason"].lower()]
            self.assertTrue(len(skipped) > 0,
                            "#906: dirty worktree must be SKIPPED")

    def test_skip_too_recent(self):
        """A worktree with mtime < 24h must be SKIPPED (age guard)."""
        fn = getattr(dg, "discover_stale_home_worktrees", None)
        if fn is None:
            self.fail("#906: discover_stale_home_worktrees not found")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "user1" / "devel" / "repo" / ".claude" / "worktrees" / "agent-1"
            wt.mkdir(parents=True)
            # Write .git FIRST, then set RECENT mtime (< 24h)
            (wt / ".git").write_text("gitdir: /fake")
            recent_time = _NOW - 3600  # 1 hour ago
            os.utime(str(wt), (recent_time, recent_time))

            result = fn(
                now=_NOW,
                home_glob=str(Path(td) / "*"),
                proc_cwd_fn=lambda: (set(), set()),
                git_run_fn=lambda *a, **k: "",
                dir_size_fn=lambda p: 1000,
            )
            skipped = [r for r in result if r.get("reason") and "recent" in r["reason"].lower()]
            self.assertTrue(len(skipped) > 0,
                            "#906: worktree younger than 24h must be SKIPPED")

    def test_reclaimable_worktree_returned(self):
        """A clean, old, inactive worktree must be returned as reclaimable."""
        fn = getattr(dg, "discover_stale_home_worktrees", None)
        if fn is None:
            self.fail("#906: discover_stale_home_worktrees not found")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "user1" / "devel" / "repo" / ".claude" / "worktrees" / "agent-1"
            wt.mkdir(parents=True)
            # Write .git FIRST, then set old mtime
            (wt / ".git").write_text("gitdir: /fake")
            old_time = _NOW - 2 * 86400
            os.utime(str(wt), (old_time, old_time))

            result = fn(
                now=_NOW,
                home_glob=str(Path(td) / "*"),
                proc_cwd_fn=lambda: (set(), set()),
                git_run_fn=lambda *a, **k: "",  # clean porcelain
                dir_size_fn=lambda p: 935_000_000,
            )
            reclaimable = [r for r in result if r.get("reason") is None]
            self.assertTrue(len(reclaimable) > 0,
                            "#906: clean, old, inactive worktree must be reclaimable")
            row = reclaimable[0]
            self.assertEqual(row["cls"], "home-worktree")
            self.assertEqual(row["kind"], "home-worktree-remove")
            self.assertIn("owner", row)
            self.assertIn("repo", row)

    def test_owner_derived_from_path(self):
        """The owner must be derived from the /home/<user>/... path."""
        fn = getattr(dg, "discover_stale_home_worktrees", None)
        if fn is None:
            self.fail("#906: discover_stale_home_worktrees not found")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "david3" / "devel" / "odoo" / "odoo-erp" / ".claude" / "worktrees" / "agent-42"
            wt.mkdir(parents=True)
            # Write .git FIRST, then set old mtime
            (wt / ".git").write_text("gitdir: /fake")
            old_time = _NOW - 2 * 86400
            os.utime(str(wt), (old_time, old_time))

            result = fn(
                now=_NOW,
                home_glob=str(Path(td) / "*"),
                proc_cwd_fn=lambda: (set(), set()),
                git_run_fn=lambda *a, **k: "",
                dir_size_fn=lambda p: 1000,
            )
            reclaimable = [r for r in result if r.get("reason") is None]
            self.assertTrue(len(reclaimable) > 0)
            self.assertEqual(reclaimable[0]["owner"], "david3",
                             "#906: owner must be derived from /home/<user> path")


class TestDrainLadderHasHomeWorktreeRung(unittest.TestCase):
    """The drain ladder must include a home-worktree rung."""

    def setUp(self):
        patch("watchdog.reaper.default_box_class", return_value="workstation").start()
        self.addCleanup(patch.stopall)

    def test_default_planners_has_home_worktree(self):
        """_default_planners must include a 'home-worktree' rung."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            planners = dg._default_planners(td, _NOW)
            labels = [label for label, _ in planners]
            self.assertIn("home-worktree", labels,
                          "#906: _default_planners must include 'home-worktree' rung")

    def test_home_worktree_in_reclaimable_classes(self):
        """'home-worktree' must be in RECLAIMABLE_CLASSES."""
        self.assertIn("home-worktree", dg.RECLAIMABLE_CLASSES,
                      "#906: 'home-worktree' must be in RECLAIMABLE_CLASSES")

    def test_home_worktree_in_sudo_classes(self):
        """'home-worktree' must be in SUDO_CLASSES (cross-user ops need sudo)."""
        self.assertIn("home-worktree", dg.SUDO_CLASSES,
                      "#906: 'home-worktree' must be in SUDO_CLASSES")

    def test_home_worktree_rung_before_per_user_worktree(self):
        """The home-worktree rung should come BEFORE the per-user worktree rung
        (it's safer: clean+old+inactive only)."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            planners = dg._default_planners(td, _NOW)
            labels = [label for label, _ in planners]
            if "home-worktree" not in labels or "worktree" not in labels:
                self.fail("Both home-worktree and worktree must be in planners")
            hw_idx = labels.index("home-worktree")
            wt_idx = labels.index("worktree")
            self.assertLess(hw_idx, wt_idx,
                            "#906: home-worktree rung must come before per-user worktree rung")


class TestPerformActionHomeWorktreeRemove(unittest.TestCase):
    """The executor must handle 'home-worktree-remove' kind."""

    def test_perform_action_recognizes_home_worktree_remove(self):
        """_perform_action must handle kind='home-worktree-remove'."""
        import inspect
        src = inspect.getsource(dg._perform_action)
        self.assertIn("home-worktree-remove", src,
                      "#906: _perform_action must handle 'home-worktree-remove' kind")


if __name__ == "__main__":
    unittest.main()
