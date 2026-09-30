"""airuleset worktree reclaimers: thin facade (#1194 split; #345/#348/#433 history).

`cli_worktree_sweep.py` grew to 2002 lines hosting four independent
reclaimers. #1194 split it VERBATIM, with no behaviour change, into
stdlib-only leaves:

- `cli_worktree_common`: shared plumbing (git runner, porcelain parsing,
  base-branch, admin-dir, clean-tree, recency and live-use probes, shared
  constants);
- `cli_worktree_stale`: the #345 stale-worktree sweep, its #348 locked-dead
  classification, the #513 salvage report and `cmd_sweep_worktrees`;
- `cli_worktree_orphans`: the #348 orphaned-branch discovery;
- `cli_worktree_reclaim`: the #834/#939 disk-guard reclaimable-directory rung;
- `cli_lane_target_reclaim`: the #545 merged-lane `target/` reclaim +
  `cmd_purge_lane_targets`.

This module only re-exports every pre-split top-level name, so these all keep
working unchanged:
- `airuleset.py`'s `from cli_worktree_sweep import (...)` block;
- `watchdog/disk_guard.py`'s deferred import of
  `discover_reclaimable_worktrees`;
- `watchdog/lane_reconcile.py`'s `ws._worktree_is_clean` /
  `ws._worktree_in_live_use`;
- every test's `cli_worktree_sweep.X` read.

`tests/test_worktree_sweep_facade_1194.py` locks the surface.

PATCH SEAMS FOLLOW THE CODE. A function reads its globals from the leaf that
defines it, so `mock.patch.object(cli_worktree_sweep, X)` does NOT reach a
leaf's internal call. To intercept a leaf's internal call, patch the leaf,
e.g. `cli_worktree_stale.sweep_stale_worktrees` or
`cli_lane_target_reclaim._iter_lane_target_dirs`.
"""

from cli_worktree_common import (  # noqa: F401 -- re-export (#1194 facade)
    CLAUDE_DIR as CLAUDE_DIR,
    _STALE_WORKTREE_PROTECTED_BRANCHES as _STALE_WORKTREE_PROTECTED_BRANCHES,
    STALE_ORPHAN_BRANCH_MIN_AGE_S as STALE_ORPHAN_BRANCH_MIN_AGE_S,
    STALE_LOCKED_DEAD_MIN_AGE_S as STALE_LOCKED_DEAD_MIN_AGE_S,
    STALE_WORKTREE_IDLE_MIN_AGE_S as STALE_WORKTREE_IDLE_MIN_AGE_S,
    _worktree_env_age_s as _worktree_env_age_s,
    _worktree_git as _worktree_git,
    _worktree_porcelain_entries as _worktree_porcelain_entries,
    _worktree_sweep_base_branch as _worktree_sweep_base_branch,
    _worktree_admin_dir as _worktree_admin_dir,
    _worktree_is_clean as _worktree_is_clean,
    _worktree_recency_age_s as _worktree_recency_age_s,
    _worktree_in_live_use as _worktree_in_live_use,
)
from cli_worktree_orphans import (  # noqa: F401 -- re-export (#1194 facade)
    _worktree_branch_ref_age_s as _worktree_branch_ref_age_s,
    discover_orphaned_worktree_branches as discover_orphaned_worktree_branches,
)
from cli_worktree_stale import (  # noqa: F401 -- re-export (#1194 facade)
    STALE_WORKTREE_LOG_PATH as STALE_WORKTREE_LOG_PATH,
    STALE_WORKTREE_STATE_PATH as STALE_WORKTREE_STATE_PATH,
    STALE_WORKTREE_MIN_INTERVAL_S as STALE_WORKTREE_MIN_INTERVAL_S,
    STALE_WORKTREE_REMOVE_TIMEOUT_S as STALE_WORKTREE_REMOVE_TIMEOUT_S,
    _WORKTREE_LOCK_PID_RX as _WORKTREE_LOCK_PID_RX,
    _worktree_lock_pid as _worktree_lock_pid,
    _proc_stat_text as _proc_stat_text,
    _pid_is_dead as _pid_is_dead,
    _worktree_lock_age_s as _worktree_lock_age_s,
    _classify_locked_worktree as _classify_locked_worktree,
    discover_stale_worktrees as discover_stale_worktrees,
    discover_salvage_worktrees as discover_salvage_worktrees,
    _log_stale_worktree_results as _log_stale_worktree_results,
    sweep_stale_worktrees as sweep_stale_worktrees,
    cmd_sweep_worktrees as cmd_sweep_worktrees,
)
from cli_worktree_reclaim import (  # noqa: F401 -- re-export (#1194 facade)
    _PRECIOUS_IGNORED_PATTERNS as _PRECIOUS_IGNORED_PATTERNS,
    _PRECIOUS_PATHSPECS as _PRECIOUS_PATHSPECS,
    _fs_walk_has_precious as _fs_walk_has_precious,
    _worktree_has_precious_ignored as _worktree_has_precious_ignored,
    _is_orphan_gitdir as _is_orphan_gitdir,
    _worktree_head_reachable_from_origin as _worktree_head_reachable_from_origin,
    _worktree_reclaimable as _worktree_reclaimable,
    discover_reclaimable_worktrees as discover_reclaimable_worktrees,
)
from cli_lane_target_reclaim import (  # noqa: F401 -- re-export (#1194 facade)
    LANE_TARGET_LOG_PATH as LANE_TARGET_LOG_PATH,
    LANE_TARGET_STATE_PATH as LANE_TARGET_STATE_PATH,
    LANE_TARGET_MIN_INTERVAL_S as LANE_TARGET_MIN_INTERVAL_S,
    LANE_TARGET_MERGED_MIN_IDLE_S as LANE_TARGET_MERGED_MIN_IDLE_S,
    TIER0_FLIP_ISO as TIER0_FLIP_ISO,
    _tier0_flip_epoch as _tier0_flip_epoch,
    LANE_TARGET_TIER0_BYPASS_STATE_PATH as LANE_TARGET_TIER0_BYPASS_STATE_PATH,
    LANE_TARGET_TIER0_BYPASS_REFILE_S as LANE_TARGET_TIER0_BYPASS_REFILE_S,
    LANE_TARGET_TIER0_BYPASS_TITLE as LANE_TARGET_TIER0_BYPASS_TITLE,
    _lane_human_size as _lane_human_size,
    _branch_reflog_has_authored_commit as _branch_reflog_has_authored_commit,
    _iter_lane_target_dirs as _iter_lane_target_dirs,
    _log_lane_target_results as _log_lane_target_results,
    _lane_repo_slug as _lane_repo_slug,
    _sample_fresh_target_files as _sample_fresh_target_files,
    _tier0_bypass_issue_body as _tier0_bypass_issue_body,
    _default_tier0_bypass_filer as _default_tier0_bypass_filer,
    _record_tier0_bypass as _record_tier0_bypass,
    purge_merged_lane_targets as purge_merged_lane_targets,
    cmd_purge_lane_targets as cmd_purge_lane_targets,
)
