"""#1318: drain rung for stale units inside a Cargo target that is still built.

Every rebuild with a new hash ADDS files to ``<profile>/deps``, ``build/`` and
``.fingerprint/``; Cargo never removes the old ones (dev1, 13.11.2026: ~167 GB
unread for 3+ days). ``stale-build`` needs the whole target idle for 14 days and
``build-incremental`` takes only ``incremental/``, so neither reaches them.

The rule is cargo-sweep 0.8.0's ``--time N`` (src/fingerprint.rs), done in stdlib so
the guard holds Cargo's own build lock while it deletes:

- a profile dir is ``target/<profile>`` or ``target/<triple>/<profile>`` holding
  ``.fingerprint/`` and Cargo's ``.cargo-lock``;
- a unit hash is the 16-hex suffix of ``<name>-<hash>``; its last use is the newest
  ``max(atime, mtime)`` of the files in its ``.fingerprint/<name>-<hash>/`` dirs
  (Cargo reads them on every build, relatime bumps the atime daily);
- a hash is stale when every fingerprint dir with it is older than MAX_AGE_DAYS.
  Only those hashes go, from ``deps/``, ``build/`` and ``.fingerprint/`` together;
  a file whose hash has no fingerprint dir is never guessed at, and
  ``incremental/`` is left to build-incremental.

One row per profile dir; the act re-checks liveness, holds every ``.cargo*lock`` of
the profile (a build that starts waits) and recomputes the stale set under the lock.
"""
import os
import re
import shutil

from watchdog.disk_guard_stale_build import (_cargo_targets, _hold_build_locks,
                                             _owning_target, profile_children)

CLS = "stale-target-artifacts"
MAX_AGE_DAYS = 3
_HASH_RX = re.compile(r"-([0-9a-f]{16})$")
_UNIT_DIRS = ("deps", "build")             # .fingerprint goes last, see _sweep


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


def _measure(profile, now):
    """``(bytes, units)`` a sweep of ``profile`` would free now."""
    stale = stale_hashes(profile, now)
    if not stale:
        return 0, 0
    return sum(_freeable(p) for p in _entries(profile, stale)), len(stale)


def discover_stale_units(home=None, now=None, live_fn=None):
    """Rows ``{cls, path, bytes, reason, why}``; ``reason`` None means a candidate.
    A live target gives one kept row and is not walked."""
    if live_fn is None:
        from cli_live_snapshot import batch_checker
        live_fn = batch_checker()
    home = home or os.path.expanduser("~")
    rows, candidates = [], []
    for target in _cargo_targets(home):
        try:
            if live_fn(target):
                rows.append({"cls": CLS, "path": target, "bytes": 0, "reason": "in live use"})
                continue
            for profile in profile_dirs(target):
                size, units = _measure(profile, now)
                if size:
                    candidates.append({"cls": CLS, "path": profile, "bytes": size,
                                       "reason": None,
                                       "why": "%d unit(s) unused %d+ d" % (units, MAX_AGE_DAYS)})
        except OSError as e:
            rows.append({"cls": CLS, "path": target, "bytes": 0, "reason": "unreadable: %s" % e})
    return rows + sorted(candidates, key=lambda r: -r["bytes"])


def plan_stale_units(home, now, level, norm_action):
    """#1318 — at critical disk only, like build-incremental (#1286)."""
    if level != "critical":
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


def sweep_profile(profile, now):
    """The act: freed bytes, or -1 when a build runs or the target is in use.
    The liveness read comes before the holds: our own open lock files would read
    as a live process in the target."""
    from cli_target_purge import _target_in_live_use
    target = _owning_target(os.path.join(profile, "deps"))
    if (target is None or os.path.realpath(profile) != os.path.join(
            os.path.realpath(target), os.path.relpath(profile, target))
            or _target_in_live_use(target)):
        return -1
    fds = _hold_build_locks(profile)
    if fds is None:
        return -1
    try:
        freed, failed = 0, set()
        for path in _entries(profile, stale_hashes(profile, now)):
            h = _hash_of(os.path.basename(path))
            if h in failed:         # keep its fingerprint: the next pass retries the unit
                continue
            try:
                freed += _remove(path)
            except OSError:
                failed.add(h)
        return freed
    finally:
        for fd in fds:
            os.close(fd)
