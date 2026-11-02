"""#1286: drain rung for build output that no other rung owns.

The Tier-0 `target/` purge (#315) was retired by #1137, so a Cargo `target/`
outside an agent worktree never aged out: dev2 held 233 GB of it with the guard
reporting `drain_exhausted`. A directory is a candidate only when it proves what
it is (Cargo's CACHEDIR.TAG, or a direct child of a build scratch root), has no
file newer than MIN_AGE_DAYS, and no process uses it. Everything else stays.
Stdlib only at module level; shared helpers are deferred imports.
"""
import os

CLS = "stale-build"
MIN_AGE_DAYS = 14
MAX_DEPTH = 4                      # ~/copy/src-tauri/target, ~/devel/app/repo/target
SCRATCH_ROOTS = ("gk-scratch",)    # direct children are throwaway build lanes
_PRUNE = {"node_modules"}


def _is_cargo_target(path):
    return (os.path.basename(path) == "target"
            and os.path.isfile(os.path.join(path, "CACHEDIR.TAG")))


def _cargo_targets(home):
    """Cargo target dirs under ``home``. Never follows a symlink, never enters a
    hidden dir, and never descends into a match."""
    found = []
    base = os.path.join(home, "")
    for dirpath, dirnames, _files in os.walk(home, followlinks=False):
        depth = dirpath[len(base):].count(os.sep) + 1 if dirpath != home else 0
        keep = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if name.startswith(".") or name in _PRUNE or os.path.islink(full):
                continue
            if _is_cargo_target(full):
                found.append(full)
            elif depth < MAX_DEPTH:
                keep.append(name)
        dirnames[:] = keep
    return found


def _scratch_children(home):
    out = []
    for root in SCRATCH_ROOTS:
        base = os.path.join(home, root)
        if os.path.islink(base) or not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if not os.path.islink(full):
                out.append(full)
    return out


def _stats(path, dir_stats_fn):
    if os.path.isdir(path):
        return dir_stats_fn(path)
    st = os.lstat(path)
    return st.st_size, st.st_mtime


def discover_stale_build(home=None, now=None, dir_stats_fn=None, live_fn=None):
    """Rows ``{cls, path, bytes, reason}``; ``reason`` None means a delete candidate."""
    if dir_stats_fn is None:
        from cli_target_purge import _dir_stats as dir_stats_fn
    if live_fn is None:
        from cli_live_snapshot import batch_checker
        live_fn = batch_checker()          # #1216: one /proc pass for the batch
    home = home or os.path.expanduser("~")
    limit = now - MIN_AGE_DAYS * 86400
    rows = []
    for path in _cargo_targets(home) + _scratch_children(home):
        try:
            size, newest = _stats(path, dir_stats_fn)
        except OSError as e:
            rows.append({"cls": CLS, "path": path, "bytes": 0, "reason": "unreadable: %s" % e})
            continue
        if newest is not None and newest > limit:
            reason = "newest file younger than %d days" % MIN_AGE_DAYS
        elif live_fn(path):
            reason = "in live use"
        else:
            reason = None
        rows.append({"cls": CLS, "path": path, "bytes": size, "reason": reason})
    return rows
