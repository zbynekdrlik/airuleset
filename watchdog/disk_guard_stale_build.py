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
regenerable and cost one slower rebuild, while ``deps/`` stays. One pass takes the
largest deletable target, because the ladder rechecks the disk only between rungs.
Stdlib only at module level; shared helpers are deferred imports.
"""
import contextlib
import fcntl
import glob
import os

CLS = "stale-build"
INCREMENTAL_CLS = "build-incremental"
MIN_AGE_DAYS = 14
INCREMENTAL_MIN_IDLE_S = 3600      # a session between two builds keeps its cache
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


def _subdirs(path):
    """Real subdirectories only: a symlink here would lead outside the target."""
    with os.scandir(path) as it:
        return [e.path for e in it if e.is_dir(follow_symlinks=False)]


def _incremental_dirs(target):
    """``<profile>/incremental`` and ``<triple-or-custom>/<profile>/incremental``, where
    Cargo's own lock file beside it proves that it is a Cargo profile dir."""
    found = []
    for first in _subdirs(target):
        for parent in [first] + _subdirs(first):
            path = os.path.join(parent, "incremental")
            if (os.path.isdir(path) and not os.path.islink(path)
                    and os.path.isfile(os.path.join(parent, ".cargo-lock"))):
                found.append(path)
    return sorted(found)


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
    inside = os.path.join(os.path.realpath(target), os.path.relpath(path, target))
    return os.path.realpath(path) == inside and not _target_in_live_use(target)


def _hold_build_locks(profile_dir):
    """Exclusive holds on Cargo's lock files, or None when a build holds one. A build
    that starts during the delete then waits for the lock instead of failing."""
    fds = []
    try:
        locks = glob.glob(os.path.join(glob.escape(profile_dir), ".cargo*lock"))
        for lock in locks:
            fds.append(os.open(lock, os.O_RDONLY))
            fcntl.flock(fds[-1], fcntl.LOCK_EX | fcntl.LOCK_NB)
        if locks:
            return fds
    except OSError:
        pass
    for fd in fds:
        os.close(fd)
    return None


@contextlib.contextmanager
def reclaim_guard(cls, path, now):
    """Yields whether ``path`` may go now. The liveness read runs before the holds:
    our own open lock files would read as a live process in the target."""
    from cli_target_purge import _target_in_live_use
    if cls == CLS:
        yield still_stale(path, now)
    elif cls != INCREMENTAL_CLS:
        yield not _target_in_live_use(path)
    else:
        fds = _hold_build_locks(os.path.dirname(path)) if still_idle_incremental(path, now) else None
        try:
            yield fds is not None
        finally:
            for fd in fds or ():
                os.close(fd)


def _can_delete(path):
    """Every dir under ``path`` is writable here and no other holder has its build locks.
    Else the same target would fail or be refused on every later pass."""
    for dirpath, _dirnames, _filenames in os.walk(path):
        if not os.access(dirpath, os.W_OK | os.X_OK):
            return False
    fds = _hold_build_locks(os.path.dirname(path))
    for fd in fds or ():
        os.close(fd)
    return fds is not None


def _idle_rows(target, limit):
    """The target's idle, deletable incremental dirs: ``[(dir, bytes)]``."""
    sized = [(d, _old_size(d, limit)) for d in _incremental_dirs(target)]
    sized = [(d, size) for d, size in sized if size]
    return sized


def discover_idle_incremental(home=None, now=None, live_fn=None):
    """Rows ``{cls, path, bytes, reason}``; ``reason`` None means a delete candidate.
    Candidates come from the largest target that can really be deleted."""
    if live_fn is None:
        from cli_live_snapshot import batch_checker
        live_fn = batch_checker()
    home = home or os.path.expanduser("~")
    limit = now - INCREMENTAL_MIN_IDLE_S
    rows, candidates = [], []

    def _keep(target, reason):
        rows.append({"cls": INCREMENTAL_CLS, "path": target, "bytes": 0, "reason": reason})

    for target in _cargo_targets(home):
        try:
            if live_fn(target):
                if _incremental_dirs(target):
                    _keep(target, "in live use")
                continue
            sized = _idle_rows(target, limit)
            if sized and all(_can_delete(d) for d, _size in sized):
                candidates.append(sized)
            elif sized:
                _keep(target, "cannot delete or lock")
        except OSError as e:
            _keep(target, "unreadable: %s" % e)
    if candidates:
        largest = max(candidates, key=lambda sized: sum(size for _d, size in sized))
        rows += [{"cls": INCREMENTAL_CLS, "path": d, "bytes": size, "reason": None}
                 for d, size in largest]
    return rows


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


def keep_live_tmp_test(rows):
    """A test-runner dir with an old newest file can still belong to a running process."""
    if any(r.get("path") and r.get("reason") is None for r in rows):
        from cli_live_snapshot import batch_checker
        live_fn = batch_checker()
        for row in rows:
            if row.get("path") and row.get("reason") is None and live_fn(row["path"]):
                row["reason"] = "in live use"
    return rows
