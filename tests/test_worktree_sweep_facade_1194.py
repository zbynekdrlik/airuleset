"""#1194 — `cli_worktree_sweep` stays a complete facade after the reclaimer split.

The module was split (#1194) from one 2002-line file into stdlib-only
leaves, one per reclaimer. Every caller keeps importing through
`cli_worktree_sweep`:
- `airuleset.py`'s re-export block;
- `watchdog/disk_guard.py`'s deferred `from cli_worktree_sweep import
  discover_reclaimable_worktrees`;
- `watchdog/lane_reconcile.py`'s `ws._worktree_is_clean` /
  `ws._worktree_in_live_use`;
- every test suite's `cli_worktree_sweep.X`.

`PRE_SPLIT_SURFACE` below is a FROZEN snapshot of every top-level name the
pre-split module defined, taken by AST from origin/main a62ab26c. It is
deliberately a literal list, never re-derived from the live module: a name
silently dropped from the facade must fail here, not quietly shrink the
expectation.
"""

import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_worktree_sweep  # noqa: E402

PRE_SPLIT_SURFACE = (
    "CLAUDE_DIR", "STALE_WORKTREE_LOG_PATH", "STALE_WORKTREE_STATE_PATH",
    "STALE_WORKTREE_MIN_INTERVAL_S", "STALE_WORKTREE_REMOVE_TIMEOUT_S",
    "_STALE_WORKTREE_PROTECTED_BRANCHES", "STALE_ORPHAN_BRANCH_MIN_AGE_S",
    "STALE_LOCKED_DEAD_MIN_AGE_S", "STALE_WORKTREE_IDLE_MIN_AGE_S",
    "_worktree_env_age_s", "_worktree_git", "_worktree_porcelain_entries",
    "_worktree_sweep_base_branch", "_WORKTREE_LOCK_PID_RX", "_worktree_lock_pid",
    "_proc_stat_text", "_pid_is_dead", "_worktree_admin_dir",
    "_worktree_lock_age_s", "_worktree_is_clean", "_worktree_recency_age_s",
    "_worktree_in_live_use", "_classify_locked_worktree",
    "_worktree_branch_ref_age_s", "discover_orphaned_worktree_branches",
    "discover_stale_worktrees", "discover_salvage_worktrees",
    "_PRECIOUS_IGNORED_PATTERNS", "_PRECIOUS_PATHSPECS", "_fs_walk_has_precious",
    "_worktree_has_precious_ignored", "_is_orphan_gitdir",
    "_worktree_head_reachable_from_origin", "_worktree_reclaimable",
    "discover_reclaimable_worktrees", "_log_stale_worktree_results",
    "sweep_stale_worktrees", "cmd_sweep_worktrees", "LANE_TARGET_LOG_PATH",
    "LANE_TARGET_STATE_PATH", "LANE_TARGET_MIN_INTERVAL_S",
    "LANE_TARGET_MERGED_MIN_IDLE_S", "TIER0_FLIP_ISO", "_tier0_flip_epoch",
    "LANE_TARGET_TIER0_BYPASS_STATE_PATH", "LANE_TARGET_TIER0_BYPASS_REFILE_S",
    "LANE_TARGET_TIER0_BYPASS_TITLE", "_lane_human_size",
    "_branch_reflog_has_authored_commit", "_iter_lane_target_dirs",
    "_log_lane_target_results", "_lane_repo_slug", "_sample_fresh_target_files",
    "_tier0_bypass_issue_body", "_default_tier0_bypass_filer",
    "_record_tier0_bypass", "purge_merged_lane_targets", "cmd_purge_lane_targets",
)


class TestFacadeSurface(unittest.TestCase):

    def test_snapshot_has_every_pre_split_name_once(self):
        self.assertEqual(len(PRE_SPLIT_SURFACE), 58)
        self.assertEqual(len(set(PRE_SPLIT_SURFACE)), 58)

    def test_every_pre_split_name_imports_from_the_facade(self):
        missing = [n for n in PRE_SPLIT_SURFACE if not hasattr(cli_worktree_sweep, n)]
        self.assertEqual(missing, [], "names dropped from the cli_worktree_sweep facade")
        for name in PRE_SPLIT_SURFACE:
            with self.subTest(name=name):
                ns = {}
                exec(f"from cli_worktree_sweep import {name}", ns)  # the caller's import shape
                self.assertIs(ns[name], getattr(cli_worktree_sweep, name))

    def test_airuleset_reexports_are_the_facade_objects(self):
        airuleset = importlib.import_module("airuleset")
        # airuleset.py defines its OWN `CLAUDE_DIR` (equal value, a separate
        # object); every other shared name comes from its re-export block (38).
        shared = [n for n in PRE_SPLIT_SURFACE
                  if n != "CLAUDE_DIR" and hasattr(airuleset, n)]
        self.assertEqual(len(shared), 38)
        for name in shared:
            with self.subTest(name=name):
                self.assertIs(getattr(airuleset, name), getattr(cli_worktree_sweep, name))


if __name__ == "__main__":
    unittest.main()
