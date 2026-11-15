"""#1318 size budget (owner 15.11.2026): a Cargo ``target/`` may hold at most its cap.

Every other build rule judges by AGE, so a project that builds every day with new
hashes never has anything old enough and grows without limit (dev1, 15.11.: 494.5 G of
761 G used were ``target/`` dirs, dstack alone 218 G). The disk guard asks on EVERY
poll, whatever the filesystem level, as the account (never as root), whether a target
is due (each at most once per MEASURE_INTERVAL_S); a due pass runs DETACHED as the
transient user unit UNIT_NAME (niced, idle I/O), never inside the poll budget:

- the cap is ``SERVICE_ACCOUNTS[acct]["target_cap_gb"]`` (validated in
  ``cli_account_policy.validate_build_budget``), default DEFAULT_CAP_GB GiB, sized from
  the measured clean builds on dev1 with headroom, so a normal build never trips it;
- over the cap, whole STALE units and ``incremental/`` crate dirs go, least recently
  used first, until the target is under TARGET_FRACTION of the cap;
- a unit is the old layout's ``<name>-<hash>`` (``deps/``, ``build/``,
  ``.fingerprint/`` entries) or Cargo 1.100's ``<profile>/build/<pkg>/<hash>/`` dir;
  its last use is the newest ``max(atime, mtime)`` in its fingerprint dir;
- never a candidate (owner 15.11.: the cap must never disturb development): anything
  used within MIN_IDLE_S (the owner's 24 h, doubled because relatime bumps an atime
  at most once a day), the newest unit of each group while used within 14 days
  (package + its source id + the unit kinds its fingerprint names, so one clean
  build stays whole; a superseded dependency version ages out), the newest
  incremental dir of a crate, a unit found in use at the last act (KEEP_S), and
  (under Cargo's held build lock) any unit with an entry a running process maps,
  holds open or sits in;
- a target still over the cap after that is noted in the guard journal once a day
  (CAP-OVER) and left alone: no footer, no ping, no harder rule;
- the detached run stops between targets at CHILD_MAX_S; a target the deadline or a
  build's lock stopped comes back in RETRY_CUT_S, quietly;
- anti-thrash: every removed key is remembered 90 days; a key that comes back was rebuilt by
  a later build, so it is logged once (CAP-THRASH) and protected for as long as it
  exists (plus MEMORY_DAYS): never a delete-rebuild loop.

The plan walks the target BEFORE the lock (a walk under the lock would hold a starting
build); the act holds ``.cargo*lock`` of the profile for at most HOLD_S at a time,
takes a ``/proc`` snapshot under it and re-checks every unit. A unit loses its
fingerprint FIRST: a kill mid-unit leaves a unit Cargo rebuilds, never one it reads as
fresh with ``out/`` gone. State lives in ``~/.claude/disk-guard/target-cap.json``.
"""
import json
import os
import pwd
import stat
import sys
import time

from watchdog import disk_guard_cargo_sweep as cs
from watchdog.disk_guard_stale_build import _cargo_targets, _hold_build_locks, profile_children

CLS = "target-cap"
DEFAULT_CAP_GB = 40
GIB = 1 << 30
DAY = 86400
TARGET_FRACTION = 0.8
MIN_IDLE_S = cs.MAX_AGE_DAYS * DAY   # owner's 24 h; relatime makes 1 day unsafe
MEMORY_DAYS = 7                   # a protected key gone this long, a target unseen this long
REMOVED_MEMORY_DAYS = 90          # a weekly config rebuilt after 8 days is still known
NEWEST_KEEP_S = 14 * DAY          # a group's newest unit is kept only while used this recently
KEEP_S = DAY                      # a unit found in use is not planned again for a day
MEASURE_INTERVAL_S = 3600         # a dstack-size walk takes ~20 s: once an hour per target
OVER_LOG_INTERVAL_S = DAY         # a target left over the cap is noted once a day
CAP_BUDGET_S = 120                # one target's act (after its walk) stops between units
HOLD_S = 2                        # a starting build waits at most this (+ one snapshot)
RETRY_CUT_S = 600                 # a cut or lock-blocked target is due again this soon
CHILD_MAX_S = 600                 # the detached run stops between targets past this
UNIT_MAX_S = 900                  # systemd kills a run past this (fingerprint-first = safe)
UNIT_NAME = "airuleset-target-cap"
UNIT_PROPERTIES = ("Nice=10", "IOSchedulingClass=idle")
STATE_NAME = "target-cap.json"
LAUNCH_NAME = "target-cap.launched"
LOCK_NAME = "target-cap.lock"
# not `-m`: the watchdog package imports this module first, so runpy would load a
# second copy as __main__ (a RuntimeWarning in the journal, two module states)
CHILD_CODE = ("import sys; from watchdog.disk_guard_target_cap import main; "
              "sys.exit(main(['--run']))")
_FP_SKIP = {"invoked.timestamp"}
_FP_PREFIXES = ("dep-", "output-")
_HEX = frozenset("0123456789abcdef")
_STAMP_MAPS = ("removed", "protected", "kept")
_STAMPS = ("measured", "seen", "over_logged", "skip_logged")


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
def cap_bytes(user=None, accounts=None):
    """The account's cap in bytes: its declared ``target_cap_gb`` or the default."""
    try:
        user = user or pwd.getpwuid(os.getuid()).pw_name
        if accounts is None:
            import cli_account_bootstrap
            accounts = cli_account_bootstrap.SERVICE_ACCOUNTS
        gb = (accounts.get(user) or {}).get("target_cap_gb", DEFAULT_CAP_GB)
    except Exception as e:      # noqa: BLE001 — an unreadable declaration keeps the default
        cs._warn("cap declaration unreadable, default", user, e)
        gb = DEFAULT_CAP_GB
    if type(gb) is not int or gb <= 0:
        gb = DEFAULT_CAP_GB
    return gb * GIB


# --------------------------------------------------------------------------- #
# measuring
# --------------------------------------------------------------------------- #
class Cut(Exception):
    """The pass reached its deadline: retried in RETRY_CUT_S, nothing decided."""


def _check(deadline):
    if deadline is not None and time.monotonic() > deadline:
        raise Cut()


def target_size(target, deadline=None):
    """Apparent bytes of ``target`` (``st_size``, as the guard's other rungs count):
    every inode once (Cargo hard-links uplifted binaries), never through a symlink.
    An entry gone mid-walk (a build) is skipped."""
    seen, total = set(), 0
    for dirpath, _dirs, files in os.walk(target):
        _check(deadline)
        for name in files:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except FileNotFoundError:
                continue
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += st.st_size
    return total


def _kinds(fp_dir):
    """The unit kinds a fingerprint dir names (``lib-serde``, ``test-lib-app``,
    ``run-build-script-build-script-build``) and the first ``*.json`` in it: a lib,
    its build script, its test harness and a bin of one package are separate groups."""
    out, first_json = set(), None
    with os.scandir(fp_dir) as it:
        for e in it:
            name = e.name
            if name in _FP_SKIP:
                continue
            if name.endswith(".json"):
                first_json = min(first_json or e.path, e.path)
                name = name[:-5]
            for p in _FP_PREFIXES:
                if name.startswith(p):
                    name = name[len(p):]
            out.add(name)
    return tuple(sorted(out)), first_json


def _source_id(json_path):
    """The package's source id (the fingerprint JSON's ``path`` hash): ``syn`` 1.x
    and 2.x share name and kinds but not this. Read with O_NOATIME, so reading never
    makes a stale unit look used; None when that is impossible (not the owner)."""
    if json_path is None:
        return None
    try:            # O_NONBLOCK: a FIFO named *.json must never hang the poll
        fd = os.open(json_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | getattr(os, "O_NOATIME", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > (1 << 20):
            return None
        data = json.loads(os.read(fd, st.st_size + 1).decode("utf-8", "replace"))
        return str(data.get("path")) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def _group(profile, pkg, fp_dir):
    kinds, first_json = _kinds(fp_dir)
    return ("u", profile, pkg, _source_id(first_json), kinds)


class Unit:
    """One removable thing: a unit or an incremental crate dir."""
    __slots__ = ("key", "group", "last", "entries", "fp_dirs", "incremental", "profile")

    def __init__(self, key, group, last, entries, fp_dirs, incremental, profile):
        self.key, self.group, self.last = key, group, last
        self.entries, self.fp_dirs = entries, fp_dirs
        self.incremental, self.profile = incremental, profile

    def paths(self):
        return self.entries + self.fp_dirs


def _old_layout(target, profile, deadline=None):
    """``<name>-<hash>`` units of the pre-1.100 layout. A unit that vanishes under
    the walk (a build is replacing it) is skipped: it is in use, not a candidate."""
    fps = cs._fingerprints(profile)
    entries = {}
    for sub in cs._UNIT_DIRS:
        for h, paths in cs._grouped(os.path.join(profile, sub), set(fps)).items():
            entries.setdefault(h, []).extend(paths)
    units = []
    for h, dirs in fps.items():
        _check(deadline)
        if not dirs:
            continue
        pkg = os.path.basename(dirs[0])[:-17]
        try:
            units.append(Unit(os.path.relpath(dirs[0], target), _group(profile, pkg, dirs[0]),
                              max(cs._last_use(d) for d in dirs),
                              sorted(entries.get(h, [])), dirs, False, profile))
        except FileNotFoundError:
            continue        # replaced by a running build right now: in use
    return units


def _new_layout(target, profile, deadline=None):
    """Cargo 1.100's ``build/<pkg>/<hash>/`` units (``fingerprint/``, ``out/``, ``run/``)."""
    base = os.path.join(profile, "build")
    units = []
    if not os.path.isdir(base) or os.path.islink(base):
        return units
    with os.scandir(base) as pkgs:
        for pkg in pkgs:
            if not pkg.is_dir(follow_symlinks=False) or cs._hash_of(pkg.name):
                continue
            try:
                hashes = list(os.scandir(pkg.path))
            except FileNotFoundError:
                continue    # removed under the walk
            for h in hashes:
                _check(deadline)
                fp = os.path.join(h.path, "fingerprint")
                if (len(h.name) != 16 or not set(h.name) <= _HEX
                        or not h.is_dir(follow_symlinks=False)
                        or not os.path.isdir(fp) or os.path.islink(fp)):
                    continue
                try:
                    units.append(Unit(os.path.relpath(h.path, target),
                                      _group(profile, pkg.name, fp),
                                      cs._last_use(fp), [h.path], [fp], False, profile))
                except FileNotFoundError:
                    continue    # replaced by a running build right now: in use
    return units


def _incremental(target, profile, deadline=None):
    """``incremental/<crate>-<id>`` dirs (rustc's cache; the unit-hash rules do not
    apply, so ``cs.stale_incremental``'s age-only list is not reused)."""
    base = os.path.join(profile, "incremental")
    units = []
    if not os.path.isdir(base) or os.path.islink(base):
        return units
    with os.scandir(base) as it:
        for e in it:
            _check(deadline)
            m = cs._CRATE_RX.match(e.name)
            if m and e.is_dir(follow_symlinks=False):
                try:
                    units.append(Unit(os.path.relpath(e.path, target),
                                      ("i", profile, m.group(1)), cs._crate_last_use(e.path),
                                      [e.path], [], True, profile))
                except FileNotFoundError:
                    continue    # rustc is rotating this session dir: in use
    return units


def profiles(target):
    """Cargo profile dirs (a ``.cargo-lock`` beside them) of either layout. A profile
    with no lock file is never touched: there is nothing to hold against a build."""
    found = set()
    for name in (".fingerprint", "build", "incremental"):
        found.update(os.path.dirname(p) for p in profile_children(target, name))
    return sorted(found)


def collect(target, deadline=None):
    """Every unit and incremental dir of ``target``."""
    out = []
    for profile in profiles(target):
        out += _old_layout(target, profile, deadline) + _new_layout(target, profile, deadline)
        out += _incremental(target, profile, deadline)
    return out


def _freeable(unit):
    """Bytes removing ``unit`` frees; an entry gone since the walk frees nothing."""
    total = 0
    for p in dict.fromkeys(unit.paths()):
        if any(p != q and p.startswith(q + os.sep) for q in unit.entries):
            continue                   # a new-layout fingerprint inside its unit dir
        try:
            total += cs._freeable(p)
        except FileNotFoundError:
            continue
    return total


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def plan(target, cap, now, exclude=(), size=None, deadline=None):
    """``(size, chosen units, projected size)``. Under the cap nothing is chosen.
    Liveness is NOT asked here: the act asks it under the held lock. A group's
    newest unit is kept only while used within NEWEST_KEEP_S: a superseded
    dependency version (its own source id, so its own group) must not stay forever."""
    size = target_size(target, deadline) if size is None else size
    if size <= cap:
        return size, [], size
    by_group = {}
    for u in collect(target, deadline):
        by_group.setdefault(u.group, []).append(u)
    cands = []
    for units in by_group.values():
        units.sort(key=lambda u: u.last)
        keep = 1 if units[-1].last >= now - NEWEST_KEEP_S else 0
        cands += [u for u in units[:len(units) - keep]
                  if u.last < now - MIN_IDLE_S and u.key not in exclude]
    cands.sort(key=lambda u: u.last)
    goal = cap * TARGET_FRACTION
    projected, chosen = size, []
    for u in cands:
        if projected <= goal:
            break
        _check(deadline)
        freed = _freeable(u)
        if freed:
            chosen.append(u)
            projected -= freed
    return size, chosen, projected


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def _state_path(home):
    return os.path.join(home, ".claude", "disk-guard", STATE_NAME)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _clean_rec(rec):
    """One target's record with every malformed field dropped (a torn or
    hand-edited file must never break the pass, #1318 review)."""
    if not isinstance(rec, dict):
        return {}
    out = {k: rec[k] for k in _STAMPS if _num(rec.get(k))}
    for k in _STAMP_MAPS:
        m = rec.get(k)
        if isinstance(m, dict):
            out[k] = {key: t for key, t in m.items() if isinstance(key, str) and _num(t)}
    return out


def load_state(home):
    """The cap memory, shape-checked; a missing or broken file starts empty."""
    try:
        with open(_state_path(home), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as e:
        cs._warn("state unreadable, starting empty", _state_path(home), e)
        data = {}
    data = data if isinstance(data, dict) else {}
    targets = data.get("targets") if isinstance(data.get("targets"), dict) else {}
    listed = data.get("list") if isinstance(data.get("list"), list) else None
    return {"targets": {t: _clean_rec(r) for t, r in targets.items() if isinstance(t, str)},
            "listed": data["listed"] if _num(data.get("listed")) else 0,
            "list": [t for t in listed if isinstance(t, str)] if listed is not None else None}


def save_state(home, state):
    p = _state_path(home)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, p)


def _recreated(rec, target, now, note):
    """Removed keys that exist again were rebuilt: protected for as long as they
    exist and MEMORY_DAYS after, noted ONCE per pass with a count (a project idle
    for weeks can bring back thousands at once). Old memory is dropped."""
    limit = now - MEMORY_DAYS * DAY
    removed = {k: t for k, t in rec.get("removed", {}).items()
               if t >= now - REMOVED_MEMORY_DAYS * DAY}
    protected = {}
    for key, t in rec.get("protected", {}).items():
        if os.path.lexists(os.path.join(target, key)):
            protected[key] = now
        elif t >= limit:
            protected[key] = t
    back = sorted(k for k in removed if os.path.lexists(os.path.join(target, k)))
    for key in back:
        protected[key] = now
        del removed[key]
    if back:
        note("CAP-THRASH", target, 0, "%d unit(s) rebuilt after the cap removed them, "
             "now kept while they exist: %s" % (len(back), ", ".join(back[:3])))
    rec["removed"], rec["protected"] = removed, protected
    return protected


# --------------------------------------------------------------------------- #
# acting
# --------------------------------------------------------------------------- #
def _unit_last(unit):
    """The unit's last use now, or None when its fingerprint is gone."""
    if unit.incremental:
        return cs._crate_last_use(unit.entries[0]) if os.path.isdir(unit.entries[0]) else None
    fp = [d for d in unit.fp_dirs if os.path.isdir(d) and not os.path.islink(d)]
    return max(cs._last_use(d) for d in fp) if fp else None


def _still_removable(unit, snapshot, now):
    """The act-time re-check under the held lock: unused since the plan, idle
    MIN_IDLE_S, no entry in use, and no rustc session lock held."""
    paths = [p for p in unit.paths() if os.path.lexists(p)]
    last = _unit_last(unit) if paths else None
    if last is None or last > unit.last or last >= now - MIN_IDLE_S:
        return False
    if any(cs._used(snapshot, p) for p in paths):
        return False
    return not unit.incremental or cs._session_locks_free(unit.entries[0])


def _remove(unit):
    """The fingerprint FIRST, then the rest. A kill or a failure after it leaves
    output with no fingerprint (Cargo rebuilds it; an orphan file only leaks space),
    never a fingerprint over a half-gone ``out/`` that Cargo reads as fresh (it then
    fails the build, measured with Cargo 1.100 in the #1318 review)."""
    if unit.incremental:
        return cs._remove(unit.entries[0])
    freed = 0
    for fp in unit.fp_dirs:
        if os.path.lexists(fp):
            freed += cs._remove(fp)
    for entry in unit.entries:
        if os.path.lexists(entry):
            freed += cs._remove(entry)
    return freed


def _one(u, target, snapshot, now, note, removed, kept):
    """Re-check and remove one unit under the held lock; returns the bytes freed."""
    path = os.path.join(target, u.key)
    try:
        ok = _still_removable(u, snapshot, now)
    except FileNotFoundError:
        return 0            # a build replaced it a moment ago: in use, not "kept"
    if not ok:
        if any(os.path.lexists(q) for q in u.paths()):
            kept[u.key] = now
            note("CAP-KEEP", path, 0, "used or in use since the plan; kept a day")
        return 0
    try:
        got = _remove(u)
    except OSError as e:
        cs._warn("cannot remove", path, e)
        if u.fp_dirs and not any(os.path.lexists(d) for d in u.fp_dirs):
            removed[u.key] = now     # its fingerprint is gone: a rebuild is known
        return 0
    if not os.path.lexists(path):
        removed[u.key] = now
    note("CAP-REMOVE", path, got, "last used %s"
         % time.strftime("%Y-%m-%d %H:%M", time.localtime(u.last)))
    return got


def _act_profile(target, profile, units, now, deadline, note, removed, kept):
    """One profile's units, the build lock held at most HOLD_S at a time.
    Returns ``(freed, locked, cut)``."""
    freed, i = 0, 0
    while i < len(units):
        if time.monotonic() > deadline:
            return freed, False, True
        fds = _hold_build_locks(profile)
        if fds is None:
            return freed, True, False
        try:
            snapshot = cs._snapshot()
            if snapshot is None:
                note("CAP-SKIP", profile, 0, "/proc unusable, nothing removed")
                return freed, False, False
            hold_end = min(time.monotonic() + HOLD_S, deadline)
            start = i              # at least one unit per hold: a slow snapshot never spins
            while i < len(units) and (i == start or time.monotonic() <= hold_end):
                freed += _one(units[i], target, snapshot, now, note, removed, kept)
                i += 1
        finally:
            for fd in fds:
                os.close(fd)
    return freed, False, False


def _act(target, chosen, now, deadline, note, removed, kept):
    """Remove the chosen units profile by profile. ``(freed, locked, cut)``."""
    freed, locked = 0, False
    by_profile = {}
    for u in chosen:
        by_profile.setdefault(u.profile, []).append(u)
    for profile, units in sorted(by_profile.items()):
        got, was_locked, was_cut = _act_profile(target, profile, units, now, deadline,
                                                note, removed, kept)
        freed += got
        locked = locked or was_locked
        if was_cut:
            return freed, locked, True
    return freed, locked, False


def enforce_target(target, cap, now, rec, note, dry_run=False, act_s=CAP_BUDGET_S,
                   deadline=None):
    """One target's pass: ``{path, bytes, cap, state, why}``; ``state`` is ``ok``
    (at most the cap), ``reduced`` (now under TARGET_FRACTION of it), ``reducing``
    (the deadline or a build's lock stopped it with stale units left), ``refused``
    (the mount cannot be trusted) or ``over``."""
    refusal = cs.mount_refusal(target)
    if refusal:
        return {"path": target, "bytes": 0, "cap": cap, "state": "refused", "why": refusal}
    protected = (rec.get("protected", {}) if dry_run
                 else _recreated(rec, target, now, note))
    kept = {k: t for k, t in rec.get("kept", {}).items() if t >= now - KEEP_S}
    try:
        size, chosen, projected = plan(target, cap, now, set(protected) | set(kept),
                                       deadline=deadline)
    except Cut:
        return {"path": target, "bytes": 0, "cap": cap, "state": "reducing", "retry": True}
    row = {"path": target, "bytes": size, "cap": cap,
           "plan_bytes": size - projected, "plan_units": len(chosen)}
    if size <= cap:
        row["state"] = "ok"
        return row
    reachable = projected <= cap * TARGET_FRACTION
    if dry_run:
        row["state"] = "reduced" if reachable else "over"
        return row
    act_end = time.monotonic() + act_s           # the walk above is not charged to it
    act_end = act_end if deadline is None else min(act_end, deadline)
    try:
        freed, locked, cut = _act(target, chosen, now, act_end, note,
                                  rec.setdefault("removed", {}), kept)
    finally:
        rec["kept"] = kept
    row.update(bytes=size - freed, freed=freed, retry=locked or cut)
    if size - freed <= cap * TARGET_FRACTION:
        row["state"] = "reduced"
    elif locked or cut:
        row["state"] = "reducing"
    else:
        row["state"] = "over"
        row["why"] = "everything else was used within %d h or is kept" % (MIN_IDLE_S // 3600)
    return row


def _targets(home, now, state, dry_run):
    """The home's Cargo targets: walked at most once per MEASURE_INTERVAL_S."""
    if dry_run or state.get("list") is None or now - state.get("listed", 0) >= MEASURE_INTERVAL_S:
        found = _cargo_targets(home)
        if not dry_run:
            state["list"], state["listed"] = found, now
            for t in found:
                state["targets"].setdefault(t, {})["seen"] = now
        return found
    return list(state["list"])


def _daily(rec, key, now, note, *args):
    if now - rec.get(key, 0) >= OVER_LOG_INTERVAL_S:
        rec[key] = now
        note(*args)


def _settle(rec, row, target, cap, now, note):
    """Bookkeeping after one target: retry time, the daily notes."""
    if row.get("retry"):
        rec["measured"] = now - MEASURE_INTERVAL_S + RETRY_CUT_S
    if row["state"] in ("ok", "reduced"):
        rec.pop("over_logged", None)
        rec.pop("skip_logged", None)
    elif row["state"] == "over":
        _daily(rec, "over_logged", now, note, "CAP-OVER", target, row["bytes"],
               "over the %d GiB cap; %s; next note in 24 h" % (cap // GIB, row["why"]))
    elif row["state"] == "refused":
        _daily(rec, "skip_logged", now, note, "CAP-SKIP", target, 0,
               "%s; next note in 24 h" % row["why"])


def enforce(home, now, dry_run=False, cap=None, act_s=CAP_BUDGET_S, deadline=None):
    """Every DUE Cargo target under ``home`` (all of them in a dry run), until
    ``deadline``: ``(rows, decision tuples)``. The state is saved before any walk and
    after every target. Over the cap after a pass = one CAP-OVER a day."""
    lines = []

    def note(action, path, nbytes, reason):
        lines.append((action, path, nbytes, reason))

    state = load_state(home)
    tstate = state["targets"]
    if not dry_run:
        try:        # before any walk: an unwritable state (disk full) costs no walk
            save_state(home, state)
        except OSError as e:
            note("CAP-SKIP", _state_path(home), 0, "state unwritable, pass skipped: %r" % e)
            return [], lines
    found = _targets(home, now, state, dry_run)
    due = sorted((t for t in found if dry_run
                  or now - tstate.get(t, {}).get("measured", 0) >= MEASURE_INTERVAL_S),
                 key=lambda t: tstate.get(t, {}).get("measured", 0))
    cap = cap_bytes() if cap is None else cap
    rows = []
    for target in due:
        if deadline is not None and time.monotonic() > deadline:
            break                   # the rest stay due for the next run
        rec = tstate.setdefault(target, {})
        if not dry_run:
            rec["measured"] = now
        try:
            row = enforce_target(target, cap, now, rec, note, dry_run=dry_run, act_s=act_s,
                                 deadline=deadline)
        except FileNotFoundError:   # a build replaced part of the tree under the walk
            row = {"path": target, "bytes": 0, "cap": cap, "state": "reducing", "retry": True}
        except OSError as e:
            row = {"path": target, "bytes": 0, "cap": cap, "state": "refused",
                   "why": "unreadable: %s" % e}
        rows.append(row)
        if not dry_run:
            _settle(rec, row, target, cap, now, note)
            try:
                save_state(home, state)
            except OSError as e:
                note("CAP-SKIP", _state_path(home), 0, "state unwritable: %r" % e)
                return rows, lines
    if not dry_run:
        for gone in [t for t, r in tstate.items()
                     if now - r.get("seen", now) > MEMORY_DAYS * DAY]:
            del tstate[gone]
        try:
            save_state(home, state)
        except OSError as e:
            note("CAP-SKIP", _state_path(home), 0, "state unwritable: %r" % e)
    return rows, lines


# --------------------------------------------------------------------------- #
# the poll hook and the detached run
# --------------------------------------------------------------------------- #
def due(home, now):
    """Read-only: does any target (or the target list) need a pass now?"""
    state = load_state(home)
    if state["list"] is None or now - state["listed"] >= MEASURE_INTERVAL_S:
        return True
    return any(now - state["targets"].get(t, {}).get("measured", 0) >= MEASURE_INTERVAL_S
               for t in state["list"])


def _launch(home):
    """Start the pass as the transient ``--user`` unit UNIT_NAME (the ONE launcher,
    ``watchdog/user_unit.py``): its own cgroup, niced and idle-I/O, killed after
    UNIT_MAX_S. A fixed unit name is the single-flight."""
    from watchdog import user_unit
    from watchdog.disk_guard_escalation import running_under_pytest
    if running_under_pytest():      # a test must never start a real pass on a real home
        return False, "systemd-run not used under test"
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return user_unit.launch(UNIT_NAME, repo,
                            [sys.executable, "-c", CHILD_CODE],
                            UNIT_MAX_S, env_keys=("PATH", "HOME", "LANG", "LC_ALL"),
                            env_prefixes=(), properties=UNIT_PROPERTIES)


def run_pass(home, now, dry_run, logs, is_root, launch_fn=None):
    """The disk-guard poll hook (every poll, any fs level): never as root, never in
    a dry run, never raises. It only decides whether a pass is due and starts the
    detached run: a dstack-size pass takes longer than a whole poll (the #1318
    re-review measured it), so it never runs inside the poll budget. A launch is
    retried at most every RETRY_CUT_S; only a failed launch reaches the poll log."""
    if is_root or dry_run:
        return
    try:
        if not due(home, now):
            return
        stamp = os.path.join(os.path.dirname(_state_path(home)), LAUNCH_NAME)
        try:
            last = os.stat(stamp).st_mtime
        except FileNotFoundError:
            last = 0
        if now - last < RETRY_CUT_S:
            return
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w", encoding="utf-8") as f:
            f.write("%d\n" % now)
        os.utime(stamp, (now, now))
        ok, why = (launch_fn or _launch)(home)
        if not ok:
            logs.append("disk-guard: target-cap launch failed (%s); retry in %d min"
                        % (why, RETRY_CUT_S // 60))
    except Exception as e:      # noqa: BLE001 — one pass must never break the poll
        logs.append("disk-guard: target-cap error: %r" % e)


def run_detached(home=None, now=None):
    """The detached run: every due target until CHILD_MAX_S, decisions to the guard
    journal. One run at a time (an flock beside the state, besides the unit name).
    Returns the journal lines it wrote."""
    import fcntl
    from watchdog import disk_guard as dg
    home = home or os.path.expanduser("~")
    now = time.time() if now is None else now
    if os.geteuid() == 0:
        return []
    os.makedirs(os.path.dirname(_state_path(home)), exist_ok=True)
    fd = os.open(os.path.join(os.path.dirname(_state_path(home)), LOCK_NAME),
                 os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return []               # another run is at it
        _rows, lines = enforce(home, now, deadline=time.monotonic() + CHILD_MAX_S)
        out = [dg._log_line(now, a, p, b, r) for a, p, b, r in lines]
        if out:
            dg._append_log(dg._log_path(home), out)
        return out
    finally:
        os.close(fd)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args != ["--run"]:
        sys.stderr.write("usage: main(['--run'])  (the detached run, see _launch)\n")
        return 2
    out = run_detached()
    removed = sum(1 for ln in out if " CAP-REMOVE " in ln)
    print("target-cap: %d journal line(s), %d unit(s) removed" % (len(out), removed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
