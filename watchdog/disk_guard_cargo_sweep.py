"""#1318: drain rung for stale units inside a Cargo target that is still built.

Every rebuild with a new hash ADDS files to ``<profile>/deps``, ``build/`` and
``.fingerprint/``; Cargo never removes the old ones (dev1, 13.11.2026: ~167 GB
unread for 3+ days). ``stale-build`` needs the whole target idle for 14 days and
``build-incremental`` takes only ``incremental/`` of an idle target, so neither reaches them.

The rule is cargo-sweep 0.8.0's ``--time N`` (src/fingerprint.rs), done in stdlib so
the guard holds Cargo's own build lock while it deletes:

- a profile dir is ``target/<profile>`` or ``target/<triple>/<profile>`` holding
  ``.fingerprint/`` and Cargo's ``.cargo-lock``;
- a unit hash is the 16-hex suffix of ``<name>-<hash>``; its last use is the newest
  ``max(atime, mtime)`` of the files in its ``.fingerprint/<name>-<hash>/`` dirs
  (Cargo reads them on every build, relatime bumps the atime daily);
- a hash is stale when every fingerprint dir with it is older than MAX_AGE_DAYS.
  Only those hashes go, from ``deps/``, ``build/`` and ``.fingerprint/`` together;
  a file whose hash has no fingerprint dir is never guessed at.

15.11.: dstack's target always has a running process in it, so a target-level
liveness veto freed nothing while the disk refilled. Liveness is now per FILE: under
the held build lock no build runs (one that starts waits), so only an entry a running
process maps, holds open or sits in (``/proc/*/maps|fd|exe|cwd``, ONE snapshot per
profile, #1216) stays, and its unit keeps its fingerprint for the next pass.

``incremental/<crate>-<id>/`` is rustc's own cache, keyed by a stable crate id that is
not the unit hash, so its age is read off the dir itself: files by ``max(atime,
mtime)``, dirs by mtime (a listing bumps a dir's atime). A crate dir goes when it is
older than the window, is not the most recently used dir of its crate name, and no
rustc session lock inside it is held. Deleting it costs one non-incremental compile.
"""
import fcntl
import glob
import os
import re
import shutil
import time

from watchdog.disk_guard_stale_build import (_cargo_targets, _hold_build_locks,
                                             _owning_target, profile_children)

CLS = "stale-target-artifacts"
MAX_AGE_DAYS = 2                           # relatime bumps atime once a day: 1 is unsafe
SWEEP_BUDGET_S = 30                        # one act stays inside the 45 s poll budget
_HASH_RX = re.compile(r"-([0-9a-f]{16})$")
_CRATE_RX = re.compile(r"^(.+)-([0-9a-z]+)$")
_UNIT_DIRS = ("deps", "build")             # .fingerprint goes last, see sweep_profile


def _snapshot():
    """Every path a running process maps, holds open, runs or sits in: ONE /proc
    pass. None when /proc is unusable, which keeps everything."""
    from cli_live_snapshot import live_links
    return live_links(maps=True)


def _used(snapshot, path):
    """``path`` is, or contains, a path in the snapshot. Its parent is resolved but
    the entry itself is not: a symlink entry is removed, never followed."""
    try:
        real = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    except OSError:
        return True
    exact, ancestors = snapshot
    return real in exact or real in ancestors


def _hash_of(name):
    """``libserde-0123456789abcdef.rlib`` -> ``0123456789abcdef``; None when no hash."""
    m = _HASH_RX.search(name.split(".", 1)[0])
    return m.group(1) if m else None


def profile_dirs(target):
    """Cargo profile dirs of ``target``, proven by ``.fingerprint/`` + ``.cargo-lock``."""
    return [os.path.dirname(p) for p in profile_children(target, ".fingerprint")]


def _last_use(path):
    """Newest ``max(atime, mtime)`` of the files in a fingerprint dir. An empty dir
    gives its mtime only: listing it here bumps its own atime."""
    newest = None
    with os.scandir(path) as it:
        for e in it:
            st = e.stat(follow_symlinks=False)
            t = max(st.st_atime, st.st_mtime)
            newest = t if newest is None else max(newest, t)
    return os.lstat(path).st_mtime if newest is None else newest


def _unit_ages(profile):
    """``{hash: newest last use}`` over every ``.fingerprint/<name>-<hash>`` dir."""
    ages = {}
    with os.scandir(os.path.join(profile, ".fingerprint")) as it:
        for e in it:
            h = _hash_of(e.name)
            if h is None or not e.is_dir(follow_symlinks=False):
                continue
            t = _last_use(e.path)
            ages[h] = max(ages.get(h, t), t)
    return ages


def stale_hashes(profile, now, max_age_days=MAX_AGE_DAYS):
    limit = now - max_age_days * 86400
    return {h for h, t in _unit_ages(profile).items() if t < limit}


def _freeable(path):
    """Bytes a delete of ``path`` frees: one-link files only (Cargo hard-links the
    uplifted binary), never through a symlink."""
    st = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size if st.st_nlink == 1 and not os.path.islink(path) else 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            fst = os.lstat(os.path.join(dirpath, name))
            if fst.st_nlink == 1 and not os.path.islink(os.path.join(dirpath, name)):
                total += fst.st_size
    return total


def _entries(profile, stale):
    """Paths with a stale hash, deps/ and build/ first, .fingerprint/ last."""
    out = []
    for sub in _UNIT_DIRS + (".fingerprint",):
        base = os.path.join(profile, sub)
        if not os.path.isdir(base) or os.path.islink(base):
            continue
        with os.scandir(base) as it:
            out += sorted(e.path for e in it if _hash_of(e.name) in stale)
    return out


def _crate_last_use(path):
    """Newest use of an incremental crate dir: files by ``max(atime, mtime)``,
    dirs by mtime only (a listing bumps a dir's atime)."""
    newest = os.lstat(path).st_mtime
    for dirpath, dirnames, files in os.walk(path):
        for name in dirnames:
            newest = max(newest, os.lstat(os.path.join(dirpath, name)).st_mtime)
        for name in files:
            st = os.lstat(os.path.join(dirpath, name))
            newest = max(newest, st.st_atime, st.st_mtime)
    return newest


def stale_incremental(profile, now, max_age_days=MAX_AGE_DAYS):
    """``incremental/<crate>-<id>`` dirs unused for the window, never the most
    recently used dir of a crate name."""
    base = os.path.join(profile, "incremental")
    if not os.path.isdir(base) or os.path.islink(base):
        return []
    limit = now - max_age_days * 86400
    by_crate = {}
    with os.scandir(base) as it:
        for e in it:
            m = _CRATE_RX.match(e.name)
            if m and e.is_dir(follow_symlinks=False):
                by_crate.setdefault(m.group(1), []).append((_crate_last_use(e.path), e.path))
    out = []
    for dirs in by_crate.values():
        dirs.sort()
        out += [p for t, p in dirs[:-1] if t < limit]
    return sorted(out)


def _session_locks_free(crate):
    """No rustc holds a session lock in ``crate``. Under Cargo's lock none can; this
    guards a rustc run outside Cargo."""
    for lock in glob.glob(os.path.join(glob.escape(crate), "*.lock")):
        try:
            fd = os.open(lock, os.O_RDONLY)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        finally:
            os.close(fd)
    return True


def _measure(profile, now, snapshot):
    """``(bytes, units, incremental dirs, entries in use)`` a sweep would give now."""
    size, used = 0, 0
    stale = stale_hashes(profile, now)
    for path in _entries(profile, stale):
        if _used(snapshot, path):
            used += 1
        else:
            size += _freeable(path)
    incs = 0
    for crate in stale_incremental(profile, now):
        if _used(snapshot, crate):
            used += 1
        else:
            size += _freeable(crate)
            incs += 1
    return size, len(stale), incs, used


_UNSET = object()


def discover_stale_units(home=None, now=None, snapshot=_UNSET):
    """Rows ``{cls, path, bytes, reason, why}``; ``reason`` None means a candidate.
    Without a usable /proc snapshot every target is kept and not walked."""
    if snapshot is _UNSET:
        snapshot = _snapshot()
    home = home or os.path.expanduser("~")
    rows, candidates = [], []
    for target in _cargo_targets(home):
        if snapshot is None:
            rows.append({"cls": CLS, "path": target, "bytes": 0, "reason": "in live use"})
            continue
        try:
            for profile in profile_dirs(target):
                size, units, incs, used = _measure(profile, now, snapshot)
                if size:
                    candidates.append({
                        "cls": CLS, "path": profile, "bytes": size, "reason": None,
                        "why": "%d unit(s) + %d incremental dir(s) unused %d+ d; %d in use kept"
                               % (units, incs, MAX_AGE_DAYS, used)})
        except OSError as e:
            rows.append({"cls": CLS, "path": target, "bytes": 0, "reason": "unreadable: %s" % e})
    return rows + sorted(candidates, key=lambda r: -r["bytes"])


def plan_stale_units(home, now, level, norm_action):
    """#1318 — from the drain level (80 %): unused units cost no rebuild, and
    waiting for critical (90 %) let dev1 reach the edge every week (owner 13.11.)."""
    if level not in ("drain", "critical"):
        return []
    try:
        return [norm_action(CLS, r, "delete") for r in discover_stale_units(home=home, now=now)]
    except Exception as e:
        return [{"cls": CLS, "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "stale-target-artifacts discovery error: %r" % e}]


def _remove(path):
    """One entry, never through a symlink. Returns the bytes it freed."""
    freed = _freeable(path)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)
    return freed


def _sweep_locked(profile, now, snapshot):
    """The deletes, under the held build lock. Stops at SWEEP_BUDGET_S; a cut
    leaves fingerprints the next pass finds."""
    deadline = time.monotonic() + SWEEP_BUDGET_S
    freed, started = 0, False
    keep, failed = set(), set()     # hashes whose fingerprint stays / whose unit stops
    fp_dir = os.path.join(profile, ".fingerprint")
    inc_dir = os.path.join(profile, "incremental")
    units = _entries(profile, stale_hashes(profile, now))
    for path in units + stale_incremental(profile, now):
        if started and time.monotonic() > deadline:
            break
        parent = os.path.dirname(path)
        h = None if parent == inc_dir else _hash_of(os.path.basename(path))
        if h in failed or (parent == fp_dir and h in keep):
            continue
        if _used(snapshot, path) or (h is None and not _session_locks_free(path)):
            keep.add(h)                 # the next pass retries the unit
            continue
        started = True
        try:
            freed += _remove(path)
        except OSError:
            failed.add(h)
            keep.add(h)
    return freed


def sweep_profile(profile, now):
    """The act: freed bytes, or -1 when a build holds the lock, /proc is unusable,
    or the profile is reached through a symlink. The snapshot is taken under the
    lock, so no build can start between it and the deletes."""
    target = _owning_target(os.path.join(profile, "deps"))
    if target is None or os.path.realpath(profile) != os.path.join(
            os.path.realpath(target), os.path.relpath(profile, target)):
        return -1
    fds = _hold_build_locks(profile)
    if fds is None:
        return -1
    try:
        snapshot = _snapshot()
        return -1 if snapshot is None else _sweep_locked(profile, now, snapshot)
    finally:
        for fd in fds:
            os.close(fd)
