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
  A unit goes WHOLE (``deps/``, ``build/`` entries, then its fingerprint dirs);
  a file whose hash has no fingerprint dir is never guessed at.

15.11.: dstack's target always has a running process in it, so a target-level
liveness veto freed nothing while the disk refilled. Liveness is now per unit, under
the held build lock (Cargo holds ``.cargo-lock`` at least shared for a whole compile;
one that starts waits): a unit with ANY entry a running process maps, holds open or
sits in (``/proc/*/maps|fd|exe|cwd``, ONE snapshot, #1216) is skipped whole. The
snapshot cannot see another uid's or another mount namespace's process; the age rule
is the real safety (a file used within the window is never stale).

``incremental/<crate>-<id>/`` is rustc's own cache, keyed by a stable crate id that is
not the unit hash, so its age is read off the dir itself: files by ``max(atime,
mtime)``, dirs by mtime (a listing bumps a dir's atime). That age is "last compiled",
not "last used": a crate dir goes when older than the window, is not the most recently
used dir of its crate name, and no rustc session lock inside it is held. Deleting it
costs one non-incremental compile of that crate, never a broken build.

The age rule needs atime, and the lock needs Cargo to take it: a ``noatime`` mount and
a network filesystem (Cargo locks nothing there) are refused.
"""
import fcntl
import glob
import os
import re
import shutil
import sys
import time

from watchdog.disk_guard_stale_build import (_cargo_targets, _hold_build_locks,
                                             _owning_target, profile_children)

CLS = "stale-target-artifacts"
MAX_AGE_DAYS = 2                           # relatime bumps atime once a day: 1 is unsafe
SWEEP_BUDGET_S = 30                        # one act stays inside the 45 s poll budget
_HASH_RX = re.compile(r"-([0-9a-f]{16})$")
_CRATE_RX = re.compile(r"^(.+)-([0-9a-z]+)$")
_UNIT_DIRS = ("deps", "build")
_NO_LOCK_FS = ("nfs", "cifs", "smb", "fuse")     # Cargo takes no lock on NFS


def _warn(what, path, err):
    """A delete that failed is retried next pass; the journal says why."""
    sys.stderr.write("disk-guard %s: %s %s: %r\n" % (CLS, what, path, err))


def _snapshot():
    """Every path a running process maps, holds open, runs or sits in: ONE /proc
    pass. None when /proc is unusable, which keeps everything."""
    from cli_live_snapshot import live_links
    return live_links(maps=True)


def _used(snapshot, path):
    """``path`` is, or contains, a path in the snapshot. Its parent is resolved but
    the entry itself is not: a symlink entry is removed, never followed."""
    real = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    exact, ancestors = snapshot
    return real in exact or real in ancestors


def mount_refusal(path, mounts="/proc/self/mounts"):
    """Why the age rule or Cargo's lock cannot be trusted on ``path``'s mount, or None."""
    try:
        with open(mounts, encoding="utf-8", errors="replace") as f:
            rows = [line.split() for line in f]
    except OSError:
        return "mount table unreadable"
    real = os.path.realpath(path)
    best = None
    for row in rows:
        if len(row) < 4:
            continue
        point = row[1].replace("\\040", " ")
        inside = real == point or real.startswith(point.rstrip("/") + "/")
        if inside and (best is None or len(point) > len(best[0])):
            best = (point, row[2], row[3].split(","))
    if best is None:
        return "mount not found"
    if best[1].startswith(_NO_LOCK_FS):
        return "%s mount: Cargo takes no build lock" % best[1]
    return "noatime mount: last use unknown" if "noatime" in best[2] else None


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


def _grouped(base, hashes=None):
    """``{hash: [entry paths]}`` of one dir (names only), never through a symlink."""
    out = {}
    if not os.path.isdir(base) or os.path.islink(base):
        return out
    with os.scandir(base) as it:
        for e in it:
            h = _hash_of(e.name)
            if h is not None and (hashes is None or h in hashes):
                out.setdefault(h, []).append(e.path)
    return {h: sorted(paths) for h, paths in out.items()}


def _fingerprints(profile):
    """``{hash: [.fingerprint/<name>-<hash> dirs]}``."""
    fp = os.path.join(profile, ".fingerprint")
    return {h: [p for p in paths if os.path.isdir(p) and not os.path.islink(p)]
            for h, paths in _grouped(fp).items()}


def _is_stale(dirs, limit):
    return bool(dirs) and max(_last_use(d) for d in dirs) < limit


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


def stale_incremental(profile, limit):
    """``incremental/<crate>-<id>`` dirs unused since ``limit``, never the most
    recently used dir of a crate name."""
    base = os.path.join(profile, "incremental")
    if not os.path.isdir(base) or os.path.islink(base):
        return []
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
    """No rustc holds a session lock in ``crate`` (rustc uses flock). Under Cargo's
    lock none can; this guards a rustc run outside Cargo."""
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


def plan_profile(profile, now, snapshot):
    """``(units, crates, kept)``: the stale units ``(hash, entries, fingerprint
    dirs)`` and incremental crate dirs a sweep may remove, and how many stale ones
    stay because a process uses them or a rustc session lock is held. The plan row
    and the act both come from here, so they never disagree."""
    limit = now - MAX_AGE_DAYS * 86400
    fps = _fingerprints(profile)
    stale = {h for h, dirs in fps.items() if _is_stale(dirs, limit)}
    entries = {}
    for sub in _UNIT_DIRS:
        for h, paths in _grouped(os.path.join(profile, sub), stale).items():
            entries.setdefault(h, []).extend(paths)
    units, crates, kept = [], [], 0
    for h in sorted(stale):
        unit = (h, entries.get(h, []), fps[h])
        if any(_used(snapshot, p) for p in unit[1] + unit[2]):
            kept += 1
        else:
            units.append(unit)
    for crate in stale_incremental(profile, limit):
        if _used(snapshot, crate) or not _session_locks_free(crate):
            kept += 1
        else:
            crates.append(crate)
    return units, crates, kept


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


def _measure(profile, now, snapshot):
    """``(bytes, units, incremental dirs, kept)`` a sweep would give now."""
    units, crates, kept = plan_profile(profile, now, snapshot)
    size = sum(_freeable(p) for _h, entries, dirs in units for p in entries + dirs)
    size += sum(_freeable(c) for c in crates)
    return size, len(units), len(crates), kept


_UNSET = object()


def discover_stale_units(home=None, now=None, snapshot=_UNSET):
    """Rows ``{cls, path, bytes, reason, why}``; ``reason`` None means a candidate.
    Without a usable /proc snapshot every target is kept and not walked."""
    if snapshot is _UNSET:
        snapshot = _snapshot()
    home = home or os.path.expanduser("~")
    rows, candidates = [], []
    for target in _cargo_targets(home):
        refusal = "/proc unusable" if snapshot is None else mount_refusal(target)
        if refusal:
            rows.append({"cls": CLS, "path": target, "bytes": 0, "reason": refusal})
            continue
        try:
            for profile in profile_dirs(target):
                size, units, incs, kept = _measure(profile, now, snapshot)
                if size:
                    candidates.append({
                        "cls": CLS, "path": profile, "bytes": size, "reason": None,
                        "why": "%d unit(s) + %d incremental dir(s) unused %d+ d; %d in use kept"
                               % (units, incs, MAX_AGE_DAYS, kept)})
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


def _remove_unit(entries, fp_dirs):
    """A unit's ``deps/`` then ``build/`` entries, then its fingerprint dirs; it
    stops at the first failure. The fingerprint goes whenever anything of the unit
    may be gone (a half-removed ``build/<pkg>-<hash>/out`` whose ``output`` file is
    left reads FRESH to Cargo); it stays only when nothing was removed (a file
    unlink is all or nothing), so the next pass retries the whole unit."""
    freed, touched, failed = 0, False, False
    for path in entries:
        try:
            freed += _remove(path)
            touched = True
        except OSError as e:
            _warn("cannot remove", path, e)
            failed = True
            touched = touched or os.path.isdir(path)
            break
    if touched or not failed:
        for path in fp_dirs:
            try:
                freed += _remove(path)
            except OSError as e:
                _warn("cannot remove fingerprint", path, e)
    return freed


def _sweep_locked(profile, now, snapshot, units, crates):
    """The deletes, under the held build lock, whole units only. Each unit and
    crate dir is re-checked here (age, use, session lock); the work under the lock
    is bounded by SWEEP_BUDGET_S between units."""
    deadline = time.monotonic() + SWEEP_BUDGET_S
    limit = now - MAX_AGE_DAYS * 86400
    freed, done = 0, 0
    fps_now = _fingerprints(profile)
    for h, entries, _dirs in units:
        if done and time.monotonic() > deadline:
            return freed
        done += 1
        try:
            dirs = fps_now.get(h, [])
            current = [p for p in entries if os.path.lexists(p)]
            if not _is_stale(dirs, limit) or any(_used(snapshot, p) for p in current + dirs):
                continue
            freed += _remove_unit(current, dirs)
        except OSError as e:
            _warn("cannot re-check unit", h, e)
    for crate in crates:
        if done and time.monotonic() > deadline:
            return freed
        done += 1
        try:
            if (os.path.isdir(crate) and _crate_last_use(crate) < limit
                    and not _used(snapshot, crate) and _session_locks_free(crate)):
                freed += _remove(crate)
        except OSError as e:
            _warn("cannot remove incremental dir", crate, e)
    return freed


def sweep_profile(profile, now):
    """The act: freed bytes, or -1 when a build holds the lock, /proc is unusable,
    the mount is refused, or the profile is reached through a symlink. The plan is
    walked BEFORE the lock (the walk would hold a starting build); the snapshot and
    a per-unit re-check run under it."""
    target = _owning_target(os.path.join(profile, "deps"))
    if (target is None or os.path.realpath(profile) != os.path.join(
            os.path.realpath(target), os.path.relpath(profile, target))
            or mount_refusal(profile)):
        return -1
    probe = _snapshot()
    if probe is None:
        return -1
    units, crates, _kept = plan_profile(profile, now, probe)
    fds = _hold_build_locks(profile)
    if fds is None:
        return -1
    try:
        snapshot = _snapshot()
        return -1 if snapshot is None else _sweep_locked(profile, now, snapshot, units, crates)
    finally:
        for fd in fds:
            os.close(fd)
