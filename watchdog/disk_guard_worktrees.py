"""#968 — stale agent-worktree discovery for the disk-guard drain ladder.

Extracted from disk_guard.py to stay under the size ratchet ceiling.
Discovers finished ``<repo>/.claude/worktrees/agent-*`` worktrees whose
HEAD is preserved on origin (contained in some ``refs/remotes/origin/*``
ref) and classifies them for reclaim.  Runs on EVERY box class (the gk
case in the ticket).

EACH function is a pure PLANNER returning action dicts
``{cls, path, bytes, kind, reason}`` — the same contract as every other
``discover_*`` in disk_guard.py.

Also extends the #965 scratch-worktrees discovery with a containment
criterion (HEAD on origin as an ALTERNATIVE to zero-ahead) so merged PRs
whose upstream ref was deleted are still reclaimable.
"""
# airuleset:script-ok helper module, imported from disk_guard
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def _safe_dir_size(path, dir_stats_fn=None):
    """Best-effort directory size in bytes."""
    try:
        if dir_stats_fn is not None:
            return dir_stats_fn(path)[0]
        from cli_target_purge import _dir_stats
        return _dir_stats(path)[0]
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# stale agent worktrees (drain rung — every box class)
# --------------------------------------------------------------------------- #

# Protected branches — never removed by the agent-worktree rung.
_PROTECTED_BRANCHES = frozenset({"main", "dev", "master", "develop"})


def _is_agent_worktree_dir(name: str) -> bool:
    """True for directories named ``agent-*``."""
    return name.startswith("agent-")


def _worktree_gitdir(wt_path: str):
    """Parse the gitdir path from a worktree's ``.git`` file. Returns None
    on any error."""
    git_marker = os.path.join(wt_path, ".git")
    try:
        content = Path(git_marker).read_text().strip()
        if content.startswith("gitdir:"):
            return content.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _is_locked(wt_path: str) -> bool:
    """True when the worktree has a ``locked`` file in its gitdir.
    Fail-safe: any error -> True (treat as locked, never remove)."""
    gd = _worktree_gitdir(wt_path)
    if gd is None:
        return True
    try:
        return os.path.exists(os.path.join(gd, "locked"))
    except OSError:
        return True


def _is_clean(wt_path: str, git_run_fn=None) -> bool | None:
    """True when ``git status --porcelain`` is empty. None on error."""
    if git_run_fn is not None:
        out = git_run_fn(["status", "--porcelain"], wt_path)
        if out is None:
            return None
        return not out.strip()
    try:
        r = subprocess.run(
            ["git", "-C", wt_path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return not r.stdout.strip()
    except Exception:
        return None


def _head_contained_in_origin(wt_path: str, git_run_fn=None) -> str | None:
    """Return the name of an ``origin/*`` ref that contains this worktree's
    HEAD, or None when no origin ref does (work not preserved on origin).
    Uses ``git branch -r --contains HEAD`` — the containment test the
    ticket specifies."""
    if git_run_fn is not None:
        out = git_run_fn(["branch", "-r", "--contains", "HEAD"], wt_path)
        if out and out.strip():
            return out.strip().splitlines()[0].strip()
        return None
    try:
        r = subprocess.run(
            ["git", "-C", wt_path, "branch", "-r", "--contains", "HEAD"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return None


def _in_live_use(wt_path: str, live_check_fn=None) -> bool:
    """True when a live process has cwd inside ``wt_path``.
    Fail-safe: any error -> True (never remove a possibly-live tree)."""
    if live_check_fn is not None:
        return live_check_fn(wt_path)
    try:
        from cli_scratch_sweep import _target_in_live_use
        return _target_in_live_use(wt_path)
    except Exception:
        return True


def _branch_name(wt_path: str, git_run_fn=None) -> str | None:
    """The branch name of a worktree (via ``git rev-parse --abbrev-ref HEAD``).
    None on error."""
    if git_run_fn is not None:
        out = git_run_fn(["rev-parse", "--abbrev-ref", "HEAD"], wt_path)
        return out.strip() if out else None
    try:
        r = subprocess.run(
            ["git", "-C", wt_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def discover_stale_agent_worktrees(home=None, now=None,
                                    git_run_fn=None, dir_stats_fn=None,
                                    live_check_fn=None):
    """Discover ``<repo>/.claude/worktrees/agent-*`` worktrees ready for
    reclaim.  A worktree is reclaimable (``reason is None``) when:

    (a) unlocked (no ``locked`` file in gitdir),
    (b) ``git status --porcelain`` clean,
    (c) HEAD contained in some ``refs/remotes/origin/*`` ref
        (``git branch -r --contains HEAD`` non-empty),
    (d) no live process with cwd inside the worktree.

    NO idle age gate — finished agent worktrees are immediately reclaimable
    once their work is on origin.

    Returns ``[{cls:"stale-agent-worktree", path, bytes, kind, reason, ...}]``.
    """
    now = time.time() if now is None else now
    home = home or os.path.expanduser("~")
    out: list[dict] = []

    import airuleset
    for root in airuleset._checkout_roots(home):
        wt_root = Path(root) / ".claude" / "worktrees"
        if not wt_root.is_dir():
            continue
        try:
            children = sorted(wt_root.iterdir())
        except OSError:
            continue
        for d in children:
            if not d.is_dir() or not _is_agent_worktree_dir(d.name):
                continue
            path = str(d)
            row: dict = {"cls": "stale-agent-worktree", "path": path,
                         "repo": str(root)}

            # (a) locked?
            if _is_locked(path):
                row.update(bytes=0, kind="skip",
                           reason="locked worktree — kept")
                out.append(row)
                continue

            # (d) live use? (before git ops — cheapest check)
            if _in_live_use(path, live_check_fn):
                row.update(bytes=0, kind="skip",
                           reason="live process cwd inside — kept")
                out.append(row)
                continue

            # branch check — never remove main/dev
            branch = _branch_name(path, git_run_fn)
            if branch and branch in _PROTECTED_BRANCHES:
                row.update(bytes=0, kind="skip",
                           reason="protected branch %s — kept" % branch)
                out.append(row)
                continue

            # (b) clean?
            clean = _is_clean(path, git_run_fn)
            if clean is None:
                row.update(bytes=0, kind="skip",
                           reason="git status unreadable — kept")
                out.append(row)
                continue
            if not clean:
                row.update(bytes=0, kind="skip",
                           reason="dirty worktree — kept")
                out.append(row)
                continue

            # (c) HEAD on origin?
            via = _head_contained_in_origin(path, git_run_fn)
            if via is None:
                row.update(bytes=0, kind="skip",
                           reason="HEAD not contained in any origin ref — kept")
                out.append(row)
                continue

            # Reclaimable
            size = _safe_dir_size(path, dir_stats_fn)
            row.update(bytes=size, kind="worktree-remove",
                       reason=None, branch=branch, contained_in=via)
            out.append(row)

    return out


# --------------------------------------------------------------------------- #
# scratch-worktree containment extension (#968)
# --------------------------------------------------------------------------- #

def head_contained_in_origin_scratch(wt_path: str, contained_fn=None) -> bool:
    """For a scratch worktree, check whether HEAD is contained in any origin
    ref — the alternative to the zero-ahead criterion.  Returns True when
    HEAD is on origin (reclaimable even with commits ahead of upstream)."""
    if contained_fn is not None:
        return contained_fn(wt_path)
    return _head_contained_in_origin(wt_path) is not None


# --------------------------------------------------------------------------- #
# dynamic critical threshold (#968 item 3)
# --------------------------------------------------------------------------- #

SMALL_DISK_BYTES = 64 * 1024 * 1024 * 1024   # 64 GB
SMALL_DISK_CRITICAL_PCT = 85


def effective_critical_pct(statvfs_fn=None):
    """Return the critical-pressure threshold: 85 % on root filesystems
    <= 64 GB (small-disk boxes like gk cx23 38 GB), 90 % on larger disks."""
    from watchdog.disk_guard import CRITICAL_PCT
    try:
        fn = statvfs_fn or os.statvfs
        st = fn("/")
        total = st.f_frsize * st.f_blocks
        if total <= SMALL_DISK_BYTES:
            return SMALL_DISK_CRITICAL_PCT
    except Exception:
        pass
    return CRITICAL_PCT


# --------------------------------------------------------------------------- #
# swap warning (#968 item 5)
# --------------------------------------------------------------------------- #

def check_swap_warning(meminfo_path="/proc/meminfo", swapfile_path="/swapfile"):
    """Return a warning dict when ``/swapfile`` > 2x MemTotal, else None.
    Pure read — no action taken."""
    try:
        mem_total = None
        with open(meminfo_path) as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    mem_total = int(parts[1]) * 1024  # kB -> bytes
                    break
        if mem_total is None or mem_total <= 0:
            return None
        try:
            swap_size = os.stat(swapfile_path).st_size
        except FileNotFoundError:
            return None
        if swap_size > 2 * mem_total:
            return {
                "swap_oversized": True,
                "swapfile_bytes": swap_size,
                "memtotal_bytes": mem_total,
                "ratio": round(swap_size / mem_total, 1),
            }
    except Exception:
        pass
    return None
