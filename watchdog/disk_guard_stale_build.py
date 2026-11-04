"""#1286: drain rung for Cargo output no other rung owns.

The Tier-0 `target/` purge (#315) was retired by #1137, so a Cargo `target/`
outside an agent worktree never aged out: dev2 held 200 GB of it with the guard
reporting `drain_exhausted`. A directory is a candidate only when Cargo's own
files prove what it is, it has no file newer than MIN_AGE_DAYS, every entry was
readable, and no process uses it. Cargo output is regenerable, so nothing else qualifies: a
worktree in a build scratch dir can hold uncommitted work and stays.
A young target ends its scan at the first fresh file and gives no row, so a
poll under pressure neither re-walks it whole nor logs it.
A project that builds daily never ages out (dev1, 4.11.: 590 GB), so at critical disk
a second rung takes only the ``incremental/`` dirs of an idle target: they are
regenerable and cost one slower rebuild, while ``deps/`` stays.
Stdlib only at module level; shared helpers are deferred imports.
"""
import glob
import os

CLS = "stale-build"
INCREMENTAL_CLS = "build-incremental"
MIN_AGE_DAYS = 14
INCREMENTAL_MIN_IDLE_S = 600       # a build between two test runs keeps its cache
MAX_DEPTH = 3                      # ~/copy/src-tauri/target, ~/devel/app/repo/target
_PRUNE = {"node_modules"}
_TAG_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"


def _is_cargo_target(path):
    """CACHEDIR.TAG is a generic convention, so Cargo's own marker must sit beside it."""
    if os.path.basename(path) != "target":
        return False
    if not os.path.isfile(os.path.join(path, ".rustc_info.json")):
        return False
    try:
        with open(os.path.join(path, "CACHEDIR.TAG"), encoding="utf-8", errors="replace") as f:
            return f.readline().strip() == _TAG_SIGNATURE
    except OSError:
        return False


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
    """Total bytes of ``path``, or None at the first file newer than ``limit``
    or the first entry that cannot be read: an unseen file may be a fresh one."""
    unreadable = []
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=unreadable.append):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                return None
            if st.st_mtime > limit:
                return None
            total += st.st_size
    return None if unreadable else total


def still_stale(path, now):
    """The act-time re-check: a build may have run since the plan."""
    from cli_target_purge import _target_in_live_use
    limit = now - MIN_AGE_DAYS * 86400
    return _old_size(path, limit) is not None and not _target_in_live_use(path)


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


def _incremental_dirs(target):
    """``<profile>/incremental`` and ``<triple-or-custom>/<profile>/incremental``."""
    found = []
    for pattern in ("*/incremental", "*/*/incremental"):
        found += glob.glob(os.path.join(glob.escape(target), pattern))
    return sorted(d for d in found if os.path.isdir(d) and not os.path.islink(d))


def _owning_target(path):
    """The Cargo target above an incremental dir. Cargo holds ``.cargo-lock`` open there
    during a build, so liveness is asked of the target, never of ``incremental/``."""
    parent = os.path.dirname(path)
    while parent != os.path.dirname(parent):
        if _is_cargo_target(parent):
            return parent
        parent = os.path.dirname(parent)
    return None


def still_idle_incremental(path, now):
    """The act-time re-check: a build may have run since the plan."""
    from cli_target_purge import _target_in_live_use
    target = _owning_target(path)
    if target is None or _old_size(path, now - INCREMENTAL_MIN_IDLE_S) is None:
        return False
    return not _target_in_live_use(target)


def discover_idle_incremental(home=None, now=None, live_fn=None):
    """Rows ``{cls, path, bytes, reason}``; ``reason`` None means a delete candidate."""
    if live_fn is None:
        from cli_live_snapshot import batch_checker
        live_fn = batch_checker()
    home = home or os.path.expanduser("~")
    limit = now - INCREMENTAL_MIN_IDLE_S
    rows = []
    for target in _cargo_targets(home):
        sized = [(d, _old_size(d, limit)) for d in _incremental_dirs(target)]
        sized = [(d, size) for d, size in sized if size]
        if not sized:
            continue
        reason = "in live use" if live_fn(target) else None
        rows += [{"cls": INCREMENTAL_CLS, "path": d, "bytes": size, "reason": reason}
                 for d, size in sized]
    return rows


def still_reclaimable(cls, path, now):
    return (still_stale if cls == CLS else still_idle_incremental)(path, now)


def plan_idle_incremental(home, now, level, norm_action):
    """#1286 — incremental output of an idle Cargo target, at critical disk only."""
    if level != "critical":
        return []
    try:
        return [norm_action(INCREMENTAL_CLS, r, "delete")
                for r in discover_idle_incremental(home=home, now=now)]
    except Exception as e:
        return [{"cls": INCREMENTAL_CLS, "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "build-incremental discovery error: %r" % e}]
