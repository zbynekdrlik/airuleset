"""#1286: drain rung for Cargo output no other rung owns.

The Tier-0 `target/` purge (#315) was retired by #1137, so a Cargo `target/`
outside an agent worktree never aged out: dev2 held 200 GB of it with the guard
reporting `drain_exhausted`. A directory is a candidate only when Cargo's
CACHEDIR.TAG proves what it is, it has no file newer than MIN_AGE_DAYS, and no
process uses it. Cargo output is regenerable, so nothing else qualifies: a
worktree in a build scratch dir can hold uncommitted work and stays.
A young target ends its scan at the first fresh file and gives no row, so a
poll under pressure neither re-walks it whole nor logs it.
Stdlib only at module level; shared helpers are deferred imports.
"""
import os

CLS = "stale-build"
MIN_AGE_DAYS = 14
MAX_DEPTH = 3                      # ~/copy/src-tauri/target, ~/devel/app/repo/target
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


def _old_size(path, limit):
    """Total bytes of ``path``, or None at the first file newer than ``limit``."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=lambda e: None):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            if st.st_mtime > limit:
                return None
            total += st.st_size
    return total


def discover_stale_build(home=None, now=None, live_fn=None):
    """Rows ``{cls, path, bytes, reason}``; ``reason`` None means a delete candidate."""
    if live_fn is None:
        from cli_live_snapshot import batch_checker
        live_fn = batch_checker()          # #1216: one /proc pass for the batch
    home = home or os.path.expanduser("~")
    limit = now - MIN_AGE_DAYS * 86400
    rows = []
    for path in _cargo_targets(home):
        size = _old_size(path, limit)
        if size is None:
            continue
        rows.append({"cls": CLS, "path": path, "bytes": size,
                     "reason": "in live use" if live_fn(path) else None})
    return rows
