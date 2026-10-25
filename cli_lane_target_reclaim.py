"""Merged worktree-lane target/ reclaim (#545), split out of cli_worktree_sweep (#1194).

Moved VERBATIM. `purge_merged_lane_targets` deletes only the regenerable
`target/` of a MERGED lane that has an authored commit (the Tier-0 gate and the
tier-0 bypass auto-filer were retired with the dev1 local-work doctrine,
#1137). `cmd_purge_lane_targets` is `airuleset.py sweep-lane-targets`. Its test seam `_iter_lane_target_dirs` is resolved in
THIS module's namespace: patch `cli_lane_target_reclaim.X`, not the facade.
Stdlib only at module level; the shared worktree plumbing comes from
`cli_worktree_common`.
"""

import json
import os
import shutil
import sys
from pathlib import Path

from cli_worktree_common import (
    CLAUDE_DIR,
    _STALE_WORKTREE_PROTECTED_BRANCHES,
    _worktree_env_age_s,
    _worktree_git,
    _worktree_porcelain_entries,
    _worktree_recency_age_s,
    _worktree_sweep_base_branch,
)


# --- Merged worktree-lane target/ reclaim (#545) ----------------------------
# The #345 whole-lane sweep waits the full 24h idle floor before reclaiming a
# dead lane at all -- a floor #513 correctly chose to protect a FRESH 0-ahead
# live worker (byte-identical to a dead merged one by git-ahead-count alone).
# So it never reclaims a MERGED lane's expensive `target/` (regenerable build
# output, gitignored) before 24h idle. Measured on dev1 (#545 STEP-0): 6.3 GB
# in 2 merged camera-box lanes sitting idle ~7h, never reclaimed.
#
# This reclaims ONLY the `target/` of a lane whose branch is MERGED into its
# base AND whose reflog shows a real AUTHORED commit -- the robust
# distinguisher #513 said 0-ahead cannot give: 0-ahead + authored-commit =
# the branch did feature work now in base = definitively merged-done, never a
# fresh 0-ahead worker (which has NO authored-commit reflog entry). Guarded
# four ways (0-ahead + authored-commit + not-in-live-use + idle a short
# grace) + a re-check before delete; fail-SAFE in every direction
# (merged = nothing unmerged to lose; target/ = 100% regenerable, so even a
# wrong purge costs only a rebuild). NEVER removes the worktree or the branch
# (the #345 sweep owns whole-lane removal at 24h). Cadence-gated by its own
# state file (mirrors #315/#345/#355 exactly), non-fatal cmd_install() step +
# a manual/testable `sweep-lane-targets` CLI entry.

LANE_TARGET_LOG_PATH = CLAUDE_DIR / "lane-target-reclaim.log"
LANE_TARGET_STATE_PATH = CLAUDE_DIR / "lane-target-reclaim-state.json"
LANE_TARGET_MIN_INTERVAL_S = 6 * 3600            # env AIRULESET_LANE_TARGET_INTERVAL_S
# A SHORT idle grace is safe here precisely because the authored-commit-in-
# reflog check already excludes the fresh-0-ahead-worker case the #345 24h
# floor exists for -- this grace only guards against a build lull / a
# re-dispatch race, both of which the live-use guard already catches during an
# active build. Env-tunable (`AIRULESET_LANE_TARGET_MERGED_MIN_IDLE_S`).
LANE_TARGET_MERGED_MIN_IDLE_S = 2 * 3600

def _lane_human_size(n):
    """`cli_target_purge._human_size(n)`, with a plain fallback if that
    disk-helper module is unavailable (the size text is cosmetic, never
    load-bearing) -- one place for the deferred import + fallback the three
    #545 call sites would otherwise each repeat (#545 review 🔵)."""
    try:
        from cli_target_purge import _human_size
        return _human_size(n)
    except Exception:       # noqa: BLE001 -- cosmetic degradation only
        return "%s B" % n


def _branch_reflog_has_authored_commit(repo_root, branch, git_run=None):
    """True iff `branch`'s reflog contains at least one real AUTHORED commit
    (`commit:` / `commit (initial):` / `commit (amend):`) -- the signal #513
    said a plain 0-ahead-of-base count cannot give, distinguishing a
    merged-DONE lane (its authored commits are now in base) from a FRESH
    0-ahead worker (whose reflog holds only `branch: Created from ...`, no
    authored commit yet).

    Deliberately EXCLUDES merge-commits (`commit (merge):`, `merge <x>: ...`)
    and ref-creation/reset/rebase ops: a lane that only base-synced but never
    authored feature work must NOT read as merged-done. A real merged lane
    always carries its RED/GREEN/review authored commits, so excluding merges
    never loses one.

    Reads the branch reflog via `git log -g --format=%gs` (reflog SUBJECTS
    only). Returns False on ANY read failure or an absent/pruned reflog -- an
    unmeasurable signal is treated as "keep" (fail-SAFE: the 24h whole-lane
    sweep reclaims such a lane later)."""
    git_run = git_run or _worktree_git
    out = git_run(["log", "-g", "--format=%gs", "refs/heads/%s" % branch], repo_root)
    if out is None:
        return False
    for line in out.splitlines():
        s = line.strip()
        if (s.startswith("commit:")
                or s.startswith("commit (initial):")
                or s.startswith("commit (amend):")):
            return True
    return False


def _iter_lane_target_dirs(home, git_run):
    """Yield (repo_root, lane_path, branch, target_dir) for every registered
    worktree LANE (a non-primary worktree whose path is under
    `.claude/worktrees/`) that carries a `target/` entry. The primary
    checkout is skipped by INDEX (entry 0), never by path-match -- a human's
    main-checkout `target/` is never touched by this aggressive merged-lane
    path."""
    import airuleset
    for root in airuleset._checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue                         # only PRIMARY checkouts list worktrees
        entries = _worktree_porcelain_entries(root, git_run=git_run)
        for i, e in enumerate(entries):
            if i == 0:
                continue                     # primary checkout -- never
            path = e.get("path")
            branch = e.get("branch")
            if not path or branch is None or branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
                continue
            if "/.claude/worktrees/" not in str(path).replace(os.sep, "/"):
                continue                     # only in-tree lane worktrees
            target_dir = Path(path) / "target"
            if not target_dir.exists() and not target_dir.is_symlink():
                continue
            yield Path(root), Path(path), branch, target_dir


def _log_lane_target_results(results, log_path, now, dry_run: bool):
    """Append one line per lane target examined (purge AND skip alike --
    comprehensive-logging.md: a destructive action logs everything). A
    `target is None` row (a discovery error) logs as `target=-`. Best-effort:
    a log-write failure is reported, never a silent pass, and never blocks the
    reclaim."""
    import datetime as _dt
    ts = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).isoformat()
    lines = []
    for r in results:
        if r.get("target") is None:
            lines.append("%s ERROR - repo=- reason=%s" % (ts, r.get("reason", "")))
            continue
        if r.get("purged"):
            action = "DRYRUN-WOULD-PURGE" if dry_run else "PURGED"
        else:
            action = "SKIP"
        size = r.get("size")
        size_txt = " size=%s" % _lane_human_size(size) if size is not None else ""
        lines.append("%s %s %s branch=%s repo=%s%s reason=%s" % (
            ts, action, r["target"], r.get("branch"), r.get("repo"),
            size_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  lane-target-reclaim: could not write log %s: %s" % (log_path, e),
              file=sys.stderr)


def purge_merged_lane_targets(home=None, dry_run: bool = False, now=None,
                              log_path=None, state_path=None, force: bool = False,
                              git_run=None, proc_dir=None):
    """Reclaim the `target/` of a worktree LANE whose branch is MERGED into
    its base (#545). A candidate is purged only when ALL of these hold, in
    order (each refusing -- never guessing -- on its own failure):

      - it is a `target/` inside a real non-primary worktree LANE under
        `.claude/worktrees/` (`_iter_lane_target_dirs`);
      - `target/` is not itself a symlink, and its resolved path does not
        escape the lane (never followed, never deleted through);
      - the branch carries ZERO commits ahead of the repo's resolved base
        (`_worktree_sweep_base_branch` -- dev/main/master superset) -- real
        unmerged work is never touched;
      - the branch reflog shows a real AUTHORED commit
        (`_branch_reflog_has_authored_commit`) -- 0-ahead + authored = merged-
        DONE, never a fresh 0-ahead live worker (#513's own ambiguity);
      - no live process is rooted in `target/` (`_target_in_live_use` --
        catches an active `cargo` build with open fds);
      - the lane is idle for at least `LANE_TARGET_MERGED_MIN_IDLE_S`
        (`_worktree_recency_age_s` -- belt-and-suspenders; the authored-commit
        check already excludes the fresh-worker case the #345 24h floor
        protects, so a SHORT grace is safe here).

    Immediately before `shutil.rmtree`, `target/` is re-checked for symlink +
    live use (a TOCTOU re-check). Deletes `target/` ONLY -- never the worktree,
    never the branch (the #345 sweep owns whole-lane removal at the 24h floor).

    Returns a list of per-lane dicts (`target`, `lane`, `branch`, `repo`,
    `purged`, `reason`, `size`, `age_s`). Cadence-gated by its own state file;
    `force=True` (a manual CLI call) or `dry_run=True` bypasses the gate (a
    dry-run never deletes)."""
    import time as _time
    now = _time.time() if now is None else now
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    log_path = Path(log_path) if log_path else LANE_TARGET_LOG_PATH
    state_path = Path(state_path) if state_path else LANE_TARGET_STATE_PATH
    git_run = git_run or _worktree_git
    grace = _worktree_env_age_s("AIRULESET_LANE_TARGET_MERGED_MIN_IDLE_S",
                                LANE_TARGET_MERGED_MIN_IDLE_S)
    from cli_reclaim_guard import refuse_real_paths_under_pytest as _refuse  # #1195
    _refuse("purge_merged_lane_targets", dry_run, home=home, log_path=log_path,
            state_path=state_path)

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = LANE_TARGET_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_LANE_TARGET_INTERVAL_S", interval))
        except ValueError:
            interval = LANE_TARGET_MIN_INTERVAL_S
        if now - last < interval:
            return []

    # Deferred imports keep this module's stdlib-only top level (the SAME
    # one-directional cli_worktree_sweep -> cli_target_purge coupling that
    # `_worktree_in_live_use` already uses -- never a cycle).
    try:
        from cli_target_purge import _target_in_live_use, _dir_stats
    except Exception as e:      # noqa: BLE001 -- fail-safe: no disk helpers -> reclaim nothing
        print("  lane-target-reclaim: disk helpers unavailable, skipping: %s" % e,
              file=sys.stderr)
        return []

    results = []
    discovery_failed = False
    try:
        lane_targets = list(_iter_lane_target_dirs(home, git_run))
    except Exception as e:      # noqa: BLE001
        lane_targets = []
        discovery_failed = True
        results.append({"target": None, "purged": False,
                        "reason": "discovery error: %s" % e})

    for root, lane, branch, target_dir in lane_targets:
        entry = {"target": str(target_dir), "lane": str(lane), "branch": branch,
                 "repo": str(root), "purged": False, "reason": None,
                 "size": None, "age_s": None}
        try:
            if target_dir.is_symlink():
                entry["reason"] = "symlink target/ -- never followed"
                results.append(entry)
                continue
            try:
                resolved = target_dir.resolve()
                resolved.relative_to(lane.resolve())
            except (OSError, ValueError):
                entry["reason"] = "resolved target/ escapes lane -- skipped"
                results.append(entry)
                continue

            base = _worktree_sweep_base_branch(root, git_run=git_run)
            if not base:
                entry["reason"] = "no base branch (dev/main/master) -- skipped"
                results.append(entry)
                continue
            ahead = git_run(["rev-list", "--count",
                            "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
            if ahead is None or not ahead.strip().isdigit():
                entry["reason"] = "ahead-count unmeasurable -- skipped"
                results.append(entry)
                continue
            if int(ahead.strip()) > 0:
                entry["reason"] = "%s commit(s) ahead of %s -- unmerged, kept" % (
                    ahead.strip(), base)
                results.append(entry)
                continue

            if not _branch_reflog_has_authored_commit(root, branch, git_run=git_run):
                entry["reason"] = ("0-ahead but no authored commit in reflog "
                                   "-- fresh worker (never merged-done), kept")
                results.append(entry)
                continue

            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- kept"
                results.append(entry)
                continue

            rec = _worktree_recency_age_s(str(root), str(lane), now)
            entry["age_s"] = rec
            if rec is None:
                entry["reason"] = "lane recency unmeasurable -- kept"
                results.append(entry)
                continue
            if rec < grace:
                entry["reason"] = ("active within %.1fh (grace %.1fh) -- kept"
                                   % (rec / 3600.0, grace / 3600.0))
                results.append(entry)
                continue

            size_bytes = _dir_stats(target_dir)[0]
            entry["size"] = size_bytes

            # Re-check symlink + live use immediately before the delete (a
            # TOCTOU re-check -- something could have started a build, or
            # swapped target/ for a symlink, since the checks above).
            if target_dir.is_symlink():
                entry["reason"] = "symlink target/ (re-checked) -- refused"
                results.append(entry)
                continue
            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (re-checked before delete) -- kept"
                results.append(entry)
                continue

            entry["reason"] = "merged-lane target/ reclaimed (%s idle, %s)" % (
                "%.1fh" % (rec / 3600.0), _lane_human_size(size_bytes))
            if not dry_run:
                shutil.rmtree(target_dir)
            entry["purged"] = True
            results.append(entry)
        except Exception as e:      # noqa: BLE001 -- one bad lane never aborts the sweep
            entry["reason"] = "error: %s" % e
            results.append(entry)

    _log_lane_target_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  lane-target-reclaim: could not write state %s: %s" % (state_path, e),
                  file=sys.stderr)

    return results


def cmd_purge_lane_targets(args):
    """`airuleset.py sweep-lane-targets [--dry-run]` -- manual/testable entry
    point for the #545 merged-lane target/ reclaim. Always `force=True`
    (bypasses the cadence gate that guards the automatic install/push wiring
    -- a deliberate manual call should never be silently skipped)."""
    print("airuleset sweep-lane-targets")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    results = purge_merged_lane_targets(dry_run=dry_run, force=True)
    for r in results:
        if r.get("target") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        if r.get("purged"):
            tag = "WOULD PURGE" if dry_run else "PURGED"
        else:
            tag = "skip"
        print("  %s: %s (branch %s) -- %s" % (
            tag, r["target"], r.get("branch"), r.get("reason", "")))
    purged = [r for r in results if r.get("purged")]
    total = sum(r.get("size", 0) or 0 for r in purged)
    print()
    verb = "would be " if dry_run else ""
    print("%d merged-lane target/ dir(s) %sreclaimed, %s %sfreed." % (
        len(purged), verb, _lane_human_size(total), verb))
    print("Log: %s" % LANE_TARGET_LOG_PATH)
