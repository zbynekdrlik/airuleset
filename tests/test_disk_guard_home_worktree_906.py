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

    def test_cross_user_sizes_are_no_longer_walked(self):
        """#1195 item 2 (owner 30.9.): root reads nothing from foreign accounts,
        so the #906 fix-1 cross-user size report is removed (its
        ``discover_home_worktree_consumers`` walker had no production caller;
        ``_ranked_consumers``/``_collect_top_consumers`` no longer list the
        report-only rung). Each account's own guard reports its own trees."""
        import inspect
        self.assertFalse(hasattr(dg, "discover_home_worktree_consumers"))
        for fn in (dg._collect_top_consumers, dg._ranked_consumers):
            self.assertNotIn("home-worktree", inspect.getsource(fn), fn.__name__)


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

    def test_old_clean_worktree_is_report_only(self):
        """#1195 item 2 (owner 30.9.): another account's worktree — even an old,
        clean, idle one — is never a delete action, only a report row. Each
        account's own disk-guard reclaims its own worktrees behind the #1193
        live-lane gate. (Replaces the #906 reclaimable / active-cwd / dirty /
        too-recent cases: none of those checks runs on a foreign home now.)"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "user1" / "devel" / "repo" / ".claude" / "worktrees" / "agent-1"
            wt.mkdir(parents=True)
            (wt / ".git").write_text("gitdir: /fake")
            old_time = _NOW - 2 * 86400
            os.utime(str(wt), (old_time, old_time))
            with patch.object(dg.subprocess, "run",
                              side_effect=AssertionError("no sudo on a foreign home")):
                result = dg.discover_stale_home_worktrees(
                    now=_NOW, home_glob=str(Path(td) / "*"))
            self.assertEqual(len(result), 1, result)
            row = result[0]
            self.assertEqual(row["cls"], "home-worktree")
            self.assertEqual(row["kind"], "report")
            self.assertIsNotNone(row["reason"])
            self.assertEqual(row["repo"], str(Path(td) / "user1" / "devel" / "repo"))

    def test_owner_derived_from_path(self):
        """The owner must be derived from the /home/<user>/... path."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wt = Path(td) / "david3" / "devel" / "odoo" / "odoo-erp" / ".claude" / "worktrees" / "agent-42"
            wt.mkdir(parents=True)
            (wt / ".git").write_text("gitdir: /fake")
            result = dg.discover_stale_home_worktrees(
                now=_NOW, home_glob=str(Path(td) / "*"))
            self.assertEqual(result[0]["owner"], "david3",
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

    def test_home_worktree_not_in_sudo_classes(self):
        """#1195 item 2: the cross-user rung is report-only, so it never runs a
        sudo operation against another account."""
        self.assertNotIn("home-worktree", dg.SUDO_CLASSES)

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
