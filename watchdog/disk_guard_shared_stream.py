"""#965 — shared-stream-specific disk-guard discovery helpers.

Extracted from disk_guard.py to stay under the size ratchet ceiling.
These helpers are used ONLY in the prevention pass on shared-stream boxes
and are imported by disk_guard._plan_android_build / _plan_scratch_worktrees.

EACH function is a pure PLANNER returning action dicts
``{cls, path, bytes, kind, reason}`` — the same contract as every other
``discover_*`` in disk_guard.py.
"""
# airuleset:script-ok helper module, imported from disk_guard

import os
import subprocess
import time


def _safe_dir_size(path, dir_stats_fn=None):
    """Re-export from disk_guard to avoid circular imports."""
    try:
        if dir_stats_fn is not None:
            return dir_stats_fn(path)[0]
        from cli_target_purge import _dir_stats
        return _dir_stats(path)[0]
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# android build intermediates (shared-stream prevention rung)
# --------------------------------------------------------------------------- #

def discover_android_build_intermediates(home=None, dir_stats_fn=None):
    """#965: android/build, android/.cxx dirs under $HOME/devel/**/node_modules,
    plus $HOME/.gradle/caches and .gradle/daemon. NO age gate — a shared-stream
    box never legitimately holds these (#778).
    Returns rows ``{cls:"android-build", path, bytes, kind:"delete", reason}``."""
    home = home or os.path.expanduser("~")
    out = []
    # 1. .gradle/caches and .gradle/daemon
    for sub in ("caches", "daemon"):
        gdir = os.path.join(home, ".gradle", sub)
        if os.path.isdir(gdir) and not os.path.islink(gdir):
            size = _safe_dir_size(gdir, dir_stats_fn)
            if size > 0:
                out.append({"cls": "android-build", "path": gdir,
                            "bytes": size, "kind": "delete", "reason": None})
    # 2. android/build and android/.cxx under $HOME/devel (bounded walk)
    devel = os.path.join(home, "devel")
    if not os.path.isdir(devel):
        return out
    seen = set()
    for dirpath, dirnames, _files in os.walk(devel, followlinks=False):
        depth = dirpath[len(devel):].count(os.sep)
        if depth > 15:
            dirnames.clear()
            continue
        bn = os.path.basename(dirpath)
        parent_bn = os.path.basename(os.path.dirname(dirpath))
        if bn in ("build", ".cxx") and parent_bn == "android":
            rp = os.path.realpath(dirpath)
            if rp not in seen:
                seen.add(rp)
                size = _safe_dir_size(dirpath, dir_stats_fn)
                if size > 0:
                    out.append({"cls": "android-build", "path": dirpath,
                                "bytes": size, "kind": "delete", "reason": None})
            dirnames.clear()
            continue
        # Prune non-matching heavy subtrees
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "target", "dist",
                                    ".tox", ".mypy_cache", ".ruff_cache")]
    return out


# --------------------------------------------------------------------------- #
# scratch-worktrees (git worktrees under scratchpad wt* dirs)
# --------------------------------------------------------------------------- #

# 2h age gate (not the 24h of repo worktrees)
SCRATCH_WORKTREE_MIN_AGE_S = 2 * 3600
# Protected branches never removed
SCRATCH_WORKTREE_PROTECTED_BRANCHES = frozenset({
    "main", "dev", "master", "develop"})


def discover_scratch_worktrees(tmp_dir="/tmp", uid=None, now=None,
                               git_run_fn=None, dir_stats_fn=None,
                               locked_fn=None, ahead_fn=None, branch_fn=None):
    """#965: discover git worktrees living under scratchpad dirs
    ``/tmp/claude-<uid>/*/scratchpad/wt*``. Guards: unlocked, clean, 0 ahead,
    idle > 2h, not main/dev. Returns rows ``{cls:"scratch-worktree", ...}``."""
    import glob as _glob
    now = time.time() if now is None else now
    uid = os.getuid() if uid is None else uid
    out = []
    pattern = os.path.join(tmp_dir, "claude-%d" % uid, "*", "scratchpad", "wt*")
    for wt_path in sorted(_glob.glob(pattern)):
        if not os.path.isdir(wt_path):
            continue
        row = {"cls": "scratch-worktree", "path": wt_path}
        # Must have a .git file (worktree marker)
        git_marker = os.path.join(wt_path, ".git")
        if not os.path.exists(git_marker):
            row.update(bytes=0, kind="skip", reason="no .git marker")
            out.append(row)
            continue
        # Age gate: 2h
        try:
            mtime = os.stat(wt_path).st_mtime
        except OSError as e:
            row.update(bytes=0, kind="skip", reason="stat error: %s" % e)
            out.append(row)
            continue
        age = now - mtime
        if age < SCRATCH_WORKTREE_MIN_AGE_S:
            row.update(bytes=0, kind="skip",
                       reason="too recent (%.1fh < %.0fh)" % (
                           age / 3600, SCRATCH_WORKTREE_MIN_AGE_S / 3600))
            out.append(row)
            continue
        # Branch check
        wt_name = os.path.basename(wt_path)
        if branch_fn is not None:
            branch = branch_fn(wt_path)
        else:
            branch = wt_name
        if branch in SCRATCH_WORKTREE_PROTECTED_BRANCHES:
            row.update(bytes=0, kind="skip",
                       reason="protected branch %s" % branch)
            out.append(row)
            continue
        # Locked check
        if locked_fn is not None:
            is_locked = locked_fn(wt_path, wt_name)
        else:
            is_locked = False
        if is_locked:
            row.update(bytes=0, kind="skip", reason="locked")
            out.append(row)
            continue
        # Clean check (git status --porcelain)
        if git_run_fn is not None:
            porcelain = git_run_fn(
                ["git", "-C", wt_path, "status", "--porcelain"], timeout=30)
        else:
            try:
                r = subprocess.run(
                    ["git", "-C", wt_path, "status", "--porcelain"],
                    capture_output=True, text=True, timeout=30)
                porcelain = r.stdout if r.returncode == 0 else None
            except Exception:
                porcelain = None
        if porcelain is None:
            row.update(bytes=0, kind="skip",
                       reason="could not read git status — kept")
            out.append(row)
            continue
        if porcelain.strip():
            row.update(bytes=0, kind="skip", reason="dirty worktree — kept")
            out.append(row)
            continue
        # Ahead check
        if ahead_fn is not None:
            n_ahead = ahead_fn(wt_path)
        else:
            n_ahead = 0
        if n_ahead and n_ahead > 0:
            row.update(bytes=0, kind="skip",
                       reason="%d commits ahead — kept" % n_ahead)
            out.append(row)
            continue
        # Reclaimable
        size = _safe_dir_size(wt_path, dir_stats_fn)
        row.update(bytes=size, kind="scratch-worktree-remove", reason=None)
        out.append(row)
    return out
