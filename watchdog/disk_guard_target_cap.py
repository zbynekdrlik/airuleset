"""#1318 size budget (owner 15.11.2026): a Cargo ``target/`` may hold at most its cap.

Every other build rule judges by AGE, so a project that builds every day with new
hashes never has anything old enough and grows without limit (dev1, 15.11.: 494.5 G of
761 G used were ``target/`` dirs, dstack alone 218 G). This pass is called on EVERY
disk-guard poll, whatever the filesystem level, as the account (never as root), and
measures each target at most once per MEASURE_INTERVAL_S:

- the cap is ``SERVICE_ACCOUNTS[acct]["target_cap_gb"]`` (validated in
  ``cli_account_policy.validate_build_budget``), default DEFAULT_CAP_GB GiB, sized from
  the measured clean builds on dev1 with headroom, so a normal build never trips it;
- over the cap, whole STALE units and ``incremental/`` crate dirs go, least recently
  used first, until the target is under TARGET_FRACTION of the cap;
- a unit is the old layout's ``<name>-<hash>`` (``deps/``, ``build/``,
  ``.fingerprint/`` entries) or Cargo 1.100's ``<profile>/build/<pkg>/<hash>/`` dir;
  its last use is the newest ``max(atime, mtime)`` in its fingerprint dir;
- never a candidate (owner 15.11.: the cap must never disturb development): anything
  used within MIN_IDLE_S (24 h), the newest unit of each group (package + the unit
  kinds its fingerprint names, so one clean build stays whole), the newest
  incremental dir of a crate, and (under Cargo's held build lock) any unit with an
  entry a running process maps, holds open or sits in;
- a target still over the cap after that is noted in the guard journal once a day
  (CAP-OVER) and left alone: no footer, no ping, no harder rule;
- anti-thrash: every removed key is remembered MEMORY_DAYS; a key that comes back was
  rebuilt by a later build, so it is logged once (CAP-THRASH) and protected for the
  rest of the window: at most one rebuild per unit per window, never a loop.

The plan walks the target BEFORE the lock (a walk under the lock would hold a starting
build); the act holds ``.cargo*lock`` of the profile, takes one ``/proc`` snapshot and
re-checks every unit. A held lock means a build runs: nothing is removed. State
(measure times, memory) lives in ``~/.claude/disk-guard/target-cap.json``.
"""
import json
import os
import pwd
import time

from watchdog import disk_guard_cargo_sweep as cs
from watchdog.disk_guard_stale_build import _cargo_targets, _hold_build_locks, profile_children

CLS = "target-cap"
DEFAULT_CAP_GB = 40
GIB = 1 << 30
TARGET_FRACTION = 0.8
MIN_IDLE_S = 24 * 3600            # owner 15.11.: anything used today stays, cap or not
MEMORY_DAYS = 7
MEASURE_INTERVAL_S = 3600         # a dstack-size walk takes ~20 s: once an hour per target
OVER_LOG_INTERVAL_S = 86400       # a target left over the cap is noted once a day
CAP_BUDGET_S = 10                 # the act (after the walk) stops between units past this
RETRY_CUT_S = 600                 # a pass the budget cut short is due again this soon
STATE_NAME = "target-cap.json"
_FP_SKIP = {"invoked.timestamp"}
_FP_PREFIXES = ("dep-", "output-")
_HEX = frozenset("0123456789abcdef")


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
def target_size(target):
    """Bytes of ``target``: every inode once (Cargo hard-links uplifted binaries),
    never through a symlink. An entry that vanishes mid-walk (a build) is skipped."""
    seen, total = set(), 0
    for dirpath, _dirs, files in os.walk(target):
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
    ``run-build-script-build-script-build``): a lib, its build script, its test
    harness and a bin of one package are separate groups."""
    out = set()
    with os.scandir(fp_dir) as it:
        for e in it:
            name = e.name
            if name in _FP_SKIP:
                continue
            if name.endswith(".json"):
                name = name[:-5]
            for p in _FP_PREFIXES:
                if name.startswith(p):
                    name = name[len(p):]
            out.add(name)
    return tuple(sorted(out))


class Unit:
    """One removable thing: a unit or an incremental crate dir."""
    __slots__ = ("key", "group", "last", "entries", "fp_dirs", "incremental", "profile")

    def __init__(self, key, group, last, entries, fp_dirs, incremental, profile):
        self.key, self.group, self.last = key, group, last
        self.entries, self.fp_dirs = entries, fp_dirs
        self.incremental, self.profile = incremental, profile

    def paths(self):
        return self.entries + self.fp_dirs


def _old_layout(target, profile):
    """``<name>-<hash>`` units of the pre-1.100 layout."""
    fps = cs._fingerprints(profile)
    entries = {}
    for sub in cs._UNIT_DIRS:
        for h, paths in cs._grouped(os.path.join(profile, sub), set(fps)).items():
            entries.setdefault(h, []).extend(paths)
    units = []
    for h, dirs in fps.items():
        if not dirs:
            continue
        first = os.path.basename(dirs[0])
        group = ("u", profile, first[:-17], _kinds(dirs[0]))
        units.append(Unit(os.path.relpath(dirs[0], target), group,
                          max(cs._last_use(d) for d in dirs),
                          sorted(entries.get(h, [])), dirs, False, profile))
    return units


def _new_layout(target, profile):
    """Cargo 1.100's ``build/<pkg>/<hash>/`` units (``fingerprint/``, ``out/``, ``run/``)."""
    base = os.path.join(profile, "build")
    units = []
    if not os.path.isdir(base) or os.path.islink(base):
        return units
    with os.scandir(base) as pkgs:
        for pkg in pkgs:
            if not pkg.is_dir(follow_symlinks=False) or cs._hash_of(pkg.name):
                continue
            with os.scandir(pkg.path) as hashes:
                for h in hashes:
                    fp = os.path.join(h.path, "fingerprint")
                    if (len(h.name) != 16 or not set(h.name) <= _HEX
                            or not h.is_dir(follow_symlinks=False)
                            or not os.path.isdir(fp) or os.path.islink(fp)):
                        continue
                    units.append(Unit(os.path.relpath(h.path, target),
                                      ("u", profile, pkg.name, _kinds(fp)),
                                      cs._last_use(fp), [h.path], [], False, profile))
    return units


def _incremental(target, profile):
    base = os.path.join(profile, "incremental")
    units = []
    if not os.path.isdir(base) or os.path.islink(base):
        return units
    with os.scandir(base) as it:
        for e in it:
            m = cs._CRATE_RX.match(e.name)
            if m and e.is_dir(follow_symlinks=False):
                units.append(Unit(os.path.relpath(e.path, target), ("i", profile, m.group(1)),
                                  cs._crate_last_use(e.path), [e.path], [], True, profile))
    return units


def profiles(target):
    """Cargo profile dirs (a ``.cargo-lock`` beside them) of either layout."""
    found = set()
    for name in (".fingerprint", "build", "incremental"):
        found.update(os.path.dirname(p) for p in profile_children(target, name))
    return sorted(found)


def collect(target):
    """Every unit and incremental dir of ``target``."""
    out = []
    for profile in profiles(target):
        out += _old_layout(target, profile) + _new_layout(target, profile)
        out += _incremental(target, profile)
    return out


def _freeable(unit):
    """Bytes removing ``unit`` frees; an entry gone since the walk frees nothing."""
    total = 0
    for p in unit.paths():
        try:
            total += cs._freeable(p)
        except FileNotFoundError:
            continue
    return total


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def plan(target, cap, now, protected=(), size=None):
    """``(size, chosen units, projected size)``. Under the cap nothing is chosen.
    Liveness is NOT asked here: the act asks it under the held lock."""
    size = target_size(target) if size is None else size
    if size <= cap:
        return size, [], size
    by_group = {}
    for u in collect(target):
        by_group.setdefault(u.group, []).append(u)
    cands = []
    for units in by_group.values():
        units.sort(key=lambda u: u.last)
        cands += [u for u in units[:-1]
                  if u.last < now - MIN_IDLE_S and u.key not in protected]
    cands.sort(key=lambda u: u.last)
    goal = cap * TARGET_FRACTION
    projected, chosen = size, []
    for u in cands:
        if projected <= goal:
            break
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


def load_state(home):
    """The cap memory; a missing or torn file starts empty (it is only memory)."""
    try:
        with open(_state_path(home), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        cs._warn("state unreadable, starting empty", _state_path(home), e)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(home, state):
    p = _state_path(home)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, p)


def _recreated(rec, target, now, note):
    """Keys removed earlier that exist again: a build recreated them. Each moves to
    ``protected`` (logged once); memory older than MEMORY_DAYS is dropped."""
    limit = now - MEMORY_DAYS * 86400
    removed = {k: t for k, t in rec.get("removed", {}).items() if t >= limit}
    protected = {k: t for k, t in rec.get("protected", {}).items() if t >= limit}
    for key in sorted(removed):
        if os.path.lexists(os.path.join(target, key)):
            protected[key] = now
            del removed[key]
            note("CAP-THRASH", os.path.join(target, key), 0,
                 "rebuilt after the cap removed it; kept %d d" % MEMORY_DAYS)
    rec["removed"], rec["protected"] = removed, protected
    return protected


# --------------------------------------------------------------------------- #
# acting
# --------------------------------------------------------------------------- #
def _unit_last(unit):
    """The unit's last use now, or None when its fingerprint is gone."""
    if unit.incremental:
        return cs._crate_last_use(unit.entries[0]) if os.path.isdir(unit.entries[0]) else None
    fp = unit.fp_dirs or [os.path.join(unit.entries[0], "fingerprint")]
    fp = [d for d in fp if os.path.isdir(d) and not os.path.islink(d)]
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
    """The fingerprint goes FIRST in the new layout: a unit whose ``out/`` is half
    gone must never keep a fingerprint that reads fresh (the old layout's
    ``_remove_unit`` drops it whenever anything was touched)."""
    if unit.incremental:
        return cs._remove(unit.entries[0])
    if not unit.fp_dirs:
        fp = os.path.join(unit.entries[0], "fingerprint")
        freed = cs._remove(fp) if os.path.lexists(fp) else 0
        return freed + cs._remove(unit.entries[0])
    return cs._remove_unit([p for p in unit.entries if os.path.lexists(p)],
                           [d for d in unit.fp_dirs if os.path.lexists(d)])


def _act(target, chosen, now, deadline, note, removed):
    """Remove the chosen units profile by profile under the profile's build locks.
    Returns ``(freed, locked profiles, cut short)``."""
    freed, locked, cut = 0, 0, False
    by_profile = {}
    for u in chosen:
        by_profile.setdefault(u.profile, []).append(u)
    for profile, units in sorted(by_profile.items()):
        if time.monotonic() > deadline:
            return freed, locked, True
        fds = _hold_build_locks(profile)
        if fds is None:
            locked += 1
            note("CAP-LOCKED", profile, 0, "a build holds the lock; next poll")
            continue
        try:
            snapshot = cs._snapshot()
            if snapshot is None:
                note("CAP-SKIP", profile, 0, "/proc unusable, nothing removed")
                continue
            for u in units:
                if time.monotonic() > deadline:
                    cut = True
                    break
                if not _still_removable(u, snapshot, now):
                    note("CAP-KEEP", os.path.join(target, u.key), 0,
                         "used or in use since the plan")
                    continue
                try:
                    got = _remove(u)
                except OSError as e:
                    cs._warn("cannot remove", u.key, e)
                    continue
                freed += got
                if not os.path.lexists(os.path.join(target, u.key)):
                    removed[u.key] = now
                note("CAP-REMOVE", os.path.join(target, u.key), got, "last used %s"
                     % time.strftime("%Y-%m-%d %H:%M", time.localtime(u.last)))
        finally:
            for fd in fds:
                os.close(fd)
    return freed, locked, cut


def enforce_target(target, cap, now, rec, note, dry_run=False, act_s=CAP_BUDGET_S):
    """One target's pass: ``{path, bytes, cap, state, why}``; ``state`` is ``ok``
    (at most the cap), ``reduced`` (now under TARGET_FRACTION of it), ``reducing``
    (the act budget ran out with stale units left) or ``over``."""
    refusal = cs.mount_refusal(target)
    if refusal:
        return {"path": target, "bytes": 0, "cap": cap, "state": "over", "why": refusal}
    protected = (rec.get("protected", {}) if dry_run
                 else _recreated(rec, target, now, note))
    size, chosen, projected = plan(target, cap, now, protected)
    row = {"path": target, "bytes": size, "cap": cap,
           "plan_bytes": size - projected, "plan_units": len(chosen)}
    if size <= cap:
        row["state"] = "ok"
        return row
    reachable = projected <= cap * TARGET_FRACTION
    if dry_run:
        row["state"] = "reduced" if reachable else "over"
        return row
    deadline = time.monotonic() + act_s          # the walk above is not charged to it
    freed, locked, cut = _act(target, chosen, now, deadline, note,
                              rec.setdefault("removed", {}))
    row.update(bytes=size - freed, freed=freed, cut=cut)
    if size - freed <= cap * TARGET_FRACTION:
        row["state"] = "reduced"
        return row
    if cut and not locked:
        row["state"] = "reducing"        # more stale units to go: back in RETRY_CUT_S
        return row
    row["state"] = "over"
    row["why"] = ("a build held the lock" if locked
                  else "everything else was used within %d h" % (MIN_IDLE_S // 3600))
    return row


def enforce(home, now, dry_run=False, cap=None, act_s=CAP_BUDGET_S):
    """Every Cargo target under ``home`` whose last measurement is MEASURE_INTERVAL_S
    old: ``(rows, decision tuples)``. A target left over the cap is logged once a
    day (CAP-OVER) and then left alone; nothing else is ever surfaced."""
    lines = []

    def note(action, path, nbytes, reason):
        lines.append((action, path, nbytes, reason))

    cap = cap_bytes() if cap is None else cap
    state = load_state(home)
    targets_state = state.setdefault("targets", {})
    rows = []
    found = _cargo_targets(home)
    seen = set(found)
    due = [t for t in found if dry_run
           or now - targets_state.get(t, {}).get("measured", 0) >= MEASURE_INTERVAL_S]
    if not dry_run:     # one target per poll: a dstack-size walk is ~20 s
        due = sorted(due, key=lambda t: targets_state.get(t, {}).get("measured", 0))[:1]
    for target in due:
        rec = targets_state.setdefault(target, {})
        try:
            row = enforce_target(target, cap, now, rec, note, dry_run=dry_run, act_s=act_s)
        except OSError as e:
            row = {"path": target, "bytes": 0, "cap": cap, "state": "over",
                   "why": "unreadable: %s" % e}
        rows.append(row)
        if dry_run:
            continue
        # a pass the budget cut short comes back in RETRY_CUT_S, not in an hour
        rec["measured"] = (now - MEASURE_INTERVAL_S + RETRY_CUT_S
                           if row.get("cut") else now)
        if row["state"] in ("ok", "reduced"):
            rec.pop("over_logged", None)
        elif row["state"] == "over" and now - rec.get("over_logged", 0) >= OVER_LOG_INTERVAL_S:
            rec["over_logged"] = now
            note("CAP-OVER", target, row["bytes"], "over the %d GiB cap; %s; next note in 24 h"
                 % (cap // GIB, row.get("why", "?")))
    for gone in [t for t in targets_state if t not in seen]:
        del targets_state[gone]
    if not dry_run:
        try:
            save_state(home, state)
        except OSError as e:
            note("CAP-SKIP", _state_path(home), 0, "state unwritable: %r" % e)
    return rows, lines


def run_pass(home, now, dry_run, logs, is_root):
    """The disk-guard poll hook (every poll, any level): never as root, never
    raises. Decisions go to the guard's journal file; the poll log gets a one-line
    summary only when something was removed (owner 15.11.: no new surface)."""
    if is_root:
        return
    try:
        from watchdog import disk_guard as dg
        _rows, lines = enforce(home, now, dry_run=dry_run)
        out = [dg._log_line(now, a, p, b, r) for a, p, b, r in lines]
        removed = [b for a, _p, b, _r in lines if a == "CAP-REMOVE"]
        if removed:
            logs.append("disk-guard: target cap removed %d stale unit(s), %s"
                        % (len(removed), dg._human(sum(removed))))
        if out and not dry_run:
            dg._append_log(dg._log_path(home), out)
    except Exception as e:      # noqa: BLE001 — one pass must never break the poll
        logs.append("disk-guard: target-cap error: %r" % e)
