"""airuleset `box-queue` — the ONE mechanism by which the parallel autopilot
lanes of one stream share that stream's single erp-test PROD-copy box (#1171).

Why it exists (montalu4, 2026-09-28): the #317 parallel dispatch runs several
worktree lanes per stream, but every lane must deploy to and E2E-test on ONE
shared box. With no mechanism, the supervisor improvised a prose protocol that
grew six patches in one day — a non-FIFO `flock` starved the refresh for 90
min, a lock-holder loop held the box for ~3 h, a slot was taken out of turn, a
refresh script rewrote every `HostName` in `~/.ssh/config`. This tool replaces
that prose with one tested state machine every lane runs identically.

Model (per box, `~/.claude/box-queue/<box>.json`):
  state  pristine | held:<lane> | dirty:<lane> | refreshing
  queue  strict FIFO of {lane, pid, enqueued_at, seen_at}
  holder {lane, pid, taken_at, lease_until} while held
  refresh {token, pid, started_at, deadline, from} while refreshing

  enqueue  append once (idempotent; a re-enqueue only refreshes seen_at)
  take     succeeds ONLY for the queue head AND only on a pristine box; auto-
           enqueues a lane that did not; `--wait S` polls, bounded
  renew    extends the holder's lease, never past taken_at + max_hold_s
  release  holder -> dirty:<lane> by DEFAULT (a refresh is owed — a lane
           that deployed and forgot a flag must never hand back a box
           marked pristine), or `--clean` -> pristine for a lane that
           changed nothing; a queued non-holder withdraws
  refresh  dirty -> refreshing -> run the project's refresh + health
           commands -> pristine; a failure leaves dirty with the reason.
           REFUSED on a held box; the state flock is NOT held while it
           runs (fenced by the `refreshing` state + a token instead). The
           refresh holds `<box>.refresh.lock` for its whole run and its
           commands INHERIT that fd, so "a refresh is running" means "that
           lock is held" — true even after the CLI itself was SIGKILLed
           (OOM, a tree kill) while its command still runs; the box then
           stays `refreshing`, never dirty, and no second refresh starts.
           A timeout or a caught SIGTERM/SIGHUP/SIGINT kills the command's
           process group and records dirty; after EVERY command, any other
           process still owning the lock (a step that left the group:
           GNU `timeout`, a setsid daemon) is SIGKILLed too.
  status   report (applies pending reclaims)

Liveness, applied on EVERY call before the action (so nothing can hold the box
forever): a holder whose pid died, whose lease expired, or who passed
max_hold_s is reclaimed as dirty:<lane> (it may have left the box half-
deployed); a queue entry whose pid died or that has not polled for
queue_ttl_s is dropped (so a vanished lane cannot block the head); a refresh
whose lock is no longer held (every process of it gone) returns the box to
dirty, and one still owning it past its deadline has every OWNER of the
lock SIGKILLed first (a hung command, or a leftover it spawned) — owner =
an fd whose open file description holds the flock (`/proc/<pid>/fdinfo`
`lock:` line), never a process that merely opened the file.
For an in-session lane the pid is the SHARED long-lived `claude` process,
so the lease (default 900 s, renewed between steps) is its real liveness.

Concurrency: every read-modify-write runs under an exclusive `fcntl.flock`
on `<box>.lock` (the `cli_autopilot_lock` sibling-mutex idiom, whose
`_pid_alive` / `_campaign_pid` helpers are reused, not copied) and writes are
atomic (tmp + `os.replace`). Every transition appends one line to
`decisions.log`.

Write boundary: the tool writes ONLY inside its state dir (box names are
validated so no path escapes it). What REFRESH means belongs to the project:
it writes `<box>.config.json` next to the state, e.g.

  {"refresh": ["/path/to/refresh-erp-test.sh"], "health": "curl -fsS ...",
   "lease_s": 900, "max_hold_s": 7200}

(the file must be owned by the invoking user and not world-writable — the
tool executes what it names).

The tool runs those commands (cwd = the state dir unless `cwd` is set) and
edits nothing else — never `~/.ssh/config`.

Stdlib only, no daemon, no watchdog job.
"""

import contextlib
import fcntl
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cli_autopilot_lock import _campaign_pid, _pid_alive
from cli_box_queue_proc import (
    _argv, _cmdline, _exited_unreaped, _Interrupted, _kill_group,
    _kill_lock_owners, _lock_owners, _signals_interrupt, _tail_line,
    lock_held,
)

_BOX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LANE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._#@+/-]{0,127}$")

DEFAULTS = {
    # Every in-session lane records the SAME long-lived `claude` pid
    # (_campaign_pid), so for a lane the LEASE is the liveness signal: a lane
    # renews between steps (a foreground step is <= 10 min) and a dead lane
    # is reclaimed within lease_s; a dead queue head blocks <= queue_ttl_s.
    "lease_s": 900.0,           # a holder must renew within this
    "max_hold_s": 7200.0,       # hard cap on one hold, renewals included
    "queue_ttl_s": 300.0,       # a queued lane must poll within this
    "refresh_timeout_s": 3600.0,
    "health_timeout_s": 900.0,
    "health_interval_s": 15.0,
}
MAX_WAIT_S = 3600.0
LOG_ROTATE_BYTES = 1024 * 1024
_REFRESH_MARGIN_S = 60.0


class BoxQueueError(Exception):
    """A usage or configuration error (CLI exit 2) — never a refusal."""


# ---------------------------------------------------------------------------
# paths, validation, config
# ---------------------------------------------------------------------------


def state_dir():
    """`AIRULESET_BOX_QUEUE_DIR` (tests) or `~/.claude/box-queue`, resolved at
    CALL time so a test's env override always wins."""
    override = os.environ.get("AIRULESET_BOX_QUEUE_DIR")
    return Path(override) if override else Path.home() / ".claude" / "box-queue"


def _check_box(box):
    if not isinstance(box, str) or not _BOX_RE.match(box):
        raise BoxQueueError(
            f"invalid box name {box!r}: letters, digits, '-' and '_' only "
            f"(max 64) — it names a file inside the state dir")
    return box


def _is_lane(lane):
    return isinstance(lane, str) and bool(_LANE_RE.match(lane))


def _check_pid(pid):
    """A real liveness pid: >1 (0, 1 and negatives would make `os.kill(pid, 0)`
    probe a process group or init — never dead)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        raise BoxQueueError(f"invalid pid {pid!r}: must be an integer > 1")
    return pid


def _check_lane(lane):
    if not _is_lane(lane):
        raise BoxQueueError(
            f"invalid lane name {lane!r}: letters, digits and ._#@+/- only "
            f"(max 128, no whitespace)")
    return lane


def _paths(box):
    d = state_dir()
    return {
        "dir": d,
        "state": d / f"{box}.json",
        "lock": d / f"{box}.lock",
        "config": d / f"{box}.config.json",
        "refresh_log": d / f"{box}.refresh.log",
        "refresh_lock": d / f"{box}.refresh.lock",
        "decisions": d / "decisions.log",
    }


def _check_cmd(key, cmd):
    if isinstance(cmd, str) and cmd.strip():
        return
    if (isinstance(cmd, list) and cmd
            and all(isinstance(a, str) and a for a in cmd)):
        return
    raise BoxQueueError(
        f"config key {key!r} must be a non-empty shell string or a list of "
        f"non-empty argv strings")


def load_config(box):
    """The project-written `<box>.config.json` merged over DEFAULTS. A missing
    file is fine (defaults, no refresh command); a malformed one is an error,
    never silently ignored."""
    path = _paths(box)["config"]
    raw = {}
    if path.exists():
        st = path.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o002:
            raise BoxQueueError(
                f"{path} must be owned by you and not world-writable — the "
                f"tool executes the commands it names")
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise BoxQueueError(f"cannot read {path}: {e}") from e
        if not isinstance(raw, dict):
            raise BoxQueueError(f"{path} must hold a JSON object")
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in raw:
            val = raw[key]
            if (isinstance(val, bool) or not isinstance(val, (int, float))
                    or not val > 0 or not math.isfinite(val)):
                raise BoxQueueError(f"{path}: {key} must be a positive number")
            cfg[key] = float(val)
    if cfg["lease_s"] > cfg["max_hold_s"]:
        raise BoxQueueError(f"{path}: lease_s must not exceed max_hold_s")
    for key in ("refresh", "health"):
        if raw.get(key) is not None:
            _check_cmd(key, raw[key])
            cfg[key] = raw[key]
    if raw.get("cwd") is not None:
        if not isinstance(raw["cwd"], str) or not raw["cwd"]:
            raise BoxQueueError(f"{path}: cwd must be a non-empty path string")
        cfg["cwd"] = raw["cwd"]
    return cfg


# ---------------------------------------------------------------------------
# state file: load / validate / save, decision log, the locked transaction
# ---------------------------------------------------------------------------


def _fresh_state(box, now, state="pristine", reason=""):
    return {"version": 1, "box": box, "state": state, "queue": [],
            "holder": None, "refresh": None, "reason": reason,
            "updated_at": now}


def _num(v):
    """A finite real number (NaN/inf would make every lease/TTL comparison
    False, i.e. a hold that never expires)."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _valid(st, box):
    """Structural + cross-field consistency. Anything off is corruption."""
    if not isinstance(st, dict) or st.get("box") != box:
        return False
    if not isinstance(st.get("reason"), str) or not isinstance(st.get("state"), str):
        return False
    q = st.get("queue")
    if not isinstance(q, list):
        return False
    lanes = set()
    for e in q:
        if not (isinstance(e, dict) and _is_lane(e.get("lane"))
                and isinstance(e.get("pid"), int) and _num(e.get("enqueued_at"))
                and _num(e.get("seen_at")) and e["lane"] not in lanes):
            return False
        lanes.add(e["lane"])
    h, r, s = st.get("holder"), st.get("refresh"), st["state"]
    if h is not None:
        if not (isinstance(h, dict) and _is_lane(h.get("lane"))
                and isinstance(h.get("pid"), int) and _num(h.get("taken_at"))
                and _num(h.get("lease_until")) and s == f"held:{h['lane']}"):
            return False
    elif s.startswith("held:"):
        return False
    if r is not None:
        if not (isinstance(r, dict) and isinstance(r.get("token"), str)
                and isinstance(r.get("pid"), int) and _num(r.get("deadline"))
                and isinstance(r.get("from"), str) and s == "refreshing"):
            return False
    elif s == "refreshing":
        return False
    return s in ("pristine", "refreshing") or s.startswith(("held:", "dirty:"))


def _event(events, now, box, kind, lane="-", frm=None, to=None, reason=""):
    ts = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    move = f" {frm}->{to}" if frm is not None and to is not None else ""
    why = " ".join(str(reason).split())
    events.append(f"{ts} box={box} {kind} lane={lane}{move}"
                  + (f" reason={why}" if why else ""))


def _append(path, lines):
    """Append to a state-dir log, rotating once at LOG_ROTATE_BYTES. A failed
    rotation is reported and the append still happens (the log only grows)."""
    try:
        if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError as e:
        print(f"box-queue: could not rotate {path}: {e}", file=sys.stderr)
    with open(path, "a") as f:
        f.write("".join(ln + "\n" for ln in lines))


def _load_state(paths, box, now, events):
    """(state, changed). A missing file is a fresh pristine box. An unreadable
    or inconsistent one is moved aside and treated as DIRTY — never pristine,
    since whoever last wrote it may have left the box half-deployed."""
    p = paths["state"]
    if not p.exists():
        return _fresh_state(box, now), False
    try:
        text = p.read_text()
    except OSError as e:
        raise BoxQueueError(f"cannot read {p}: {e} — left untouched") from e
    try:
        st = json.loads(text)
    except ValueError:
        st = None
    if _valid(st, box):
        return st, False
    aside = p.with_name(f"{p.name}.corrupt-{int(now)}-{os.getpid()}")
    try:
        os.replace(p, aside)
        kept = f"kept as {aside.name}"
    except OSError as e:
        kept = f"could not move it aside: {e}"
    reason = f"state file unreadable or inconsistent ({kept})"
    _event(events, now, box, "CORRUPT", frm="?", to="dirty:unknown", reason=reason)
    return _fresh_state(box, now, state="dirty:unknown", reason=reason), True


def _save_state(path, st):
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w") as f:
            json.dump(st, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _kill_stuck_refresh(paths):
    """A refresh past its deadline whose lock is still owned: its CLI died
    (SIGKILL/OOM) and its command hangs, or a leftover kept the inherited
    fd. Kill every owner and return (why, note): `why` when the lock is now
    free (the caller reaps the box to dirty), else a `note`."""
    killed, left = _kill_lock_owners(paths["refresh_lock"])
    if not left and not lock_held(paths["refresh_lock"]):
        return (f"refresh deadline passed — killed stuck refresh "
                f"process(es) {killed}"), ""
    return None, (f"past its deadline — {paths['refresh_lock'].name} still "
                  f"owned by {left or 'an unreadable process'}")


def _reap(st, now, cfg, box, events, paths, own_refresh=False):
    """Apply every liveness rule. Returns True when anything changed.
    `own_refresh`: the caller is a refresh CLI that holds the refresh lock
    itself — it retires a stale record / records its own result, so the
    lock-based refresh liveness (which would see its own lock) is skipped."""
    changed = False
    h = st["holder"]
    if h is not None:
        why = None
        if not _pid_alive(h["pid"]):
            why = f"holder pid {h['pid']} dead"
        elif now >= h["taken_at"] + cfg["max_hold_s"]:
            why = f"max hold {cfg['max_hold_s']:.0f}s exceeded"
        elif now >= h["lease_until"]:
            why = "lease expired"
        elif h["lease_until"] > now + cfg["max_hold_s"]:
            why = "lease timestamp in the future"
        if why:
            frm, st["state"] = st["state"], f"dirty:{h['lane']}"
            st["holder"], st["reason"] = None, f"reclaimed: {why}"
            _event(events, now, box, "RECLAIM", h["lane"], frm, st["state"], why)
            changed = True
    r = st["refresh"]
    if r is not None and not own_refresh:
        why = None
        if not lock_held(paths["refresh_lock"]):
            why = (f"refresh process {r['pid']} died" if not _pid_alive(r["pid"])
                   else "refresh lock released without a result")
        elif now >= r["deadline"]:
            why, note = _kill_stuck_refresh(paths)
            if note and st["reason"] != note:
                st["reason"] = note
                _event(events, now, box, "REFRESH-LATE", reason=note)
                changed = True
        if why:
            st["state"] = r["from"] if r["from"].startswith("dirty:") else "dirty:refresh"
            st["refresh"], st["reason"] = None, f"refresh interrupted: {why}"
            _event(events, now, box, "REFRESH-LOST", frm="refreshing",
                   to=st["state"], reason=why)
            changed = True
    keep = []
    for e in st["queue"]:
        why = None
        if not _pid_alive(e["pid"]):
            why = f"pid {e['pid']} dead"
        elif now - e["seen_at"] >= cfg["queue_ttl_s"]:
            why = f"not polled for {cfg['queue_ttl_s']:.0f}s"
        elif e["seen_at"] > now + cfg["queue_ttl_s"]:
            why = "seen_at timestamp in the future"
        if why:
            _event(events, now, box, "DROP", e["lane"], reason=why)
            changed = True
        else:
            keep.append(e)
    st["queue"] = keep
    return changed


@contextlib.contextmanager
def _locked(paths):
    paths["dir"].mkdir(parents=True, exist_ok=True)
    fd = os.open(str(paths["lock"]), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _transact(box, cfg, now_fn, fn, own_refresh=False):
    """Lock, load, reap, apply `fn(st, events, now) -> (result, changed)`,
    save when anything changed, log every event. The clock is read INSIDE
    the lock so FIFO stamps are monotonic across racing writers."""
    paths = _paths(box)
    with _locked(paths):
        now = now_fn()
        events = []
        st, changed = _load_state(paths, box, now, events)
        changed = _reap(st, now, cfg, box, events, paths, own_refresh) or changed
        result, touched = fn(st, events, now)
        if changed or touched:
            st["updated_at"] = now
            _save_state(paths["state"], st)
        if events:
            _append(paths["decisions"], events)
    result.setdefault("state", st["state"])
    return result


def _require_box_config(box, cfg):
    if not cfg.get("refresh"):
        raise BoxQueueError(
            f"unknown box {box!r}: no {_paths(box)['config']} with a "
            f"\"refresh\" command. The project writes it; lanes use exactly "
            f"that --box name (a mistyped name would get a queue of its own)")


def _clock(now):
    return time.time if now is None else (lambda: now)


def _find(queue, lane):
    for i, e in enumerate(queue):
        if e["lane"] == lane:
            return i
    return None


def _lost_detail(st, lane):
    if st["state"] == f"dirty:{lane}":
        return f" — the box was reclaimed as {st['state']} ({st['reason']})"
    return ""


# ---------------------------------------------------------------------------
# the transitions
# ---------------------------------------------------------------------------


def enqueue(box, lane, pid, now=None):
    _check_box(box)
    _check_lane(lane)
    _check_pid(pid)
    cfg = load_config(box)
    _require_box_config(box, cfg)

    def fn(st, events, t):
        h = st["holder"]
        if h is not None and h["lane"] == lane:
            return {"ok": True, "position": 0,
                    "msg": f"{lane} already holds {box}"}, False
        i = _find(st["queue"], lane)
        if i is not None:
            st["queue"][i].update(pid=int(pid), seen_at=t)
            return {"ok": True, "position": i + 1,
                    "msg": f"{lane} already queued for {box} "
                           f"(position {i + 1}/{len(st['queue'])})"}, True
        st["queue"].append({"lane": lane, "pid": int(pid),
                            "enqueued_at": t, "seen_at": t})
        _event(events, t, box, "ENQUEUE", lane)
        n = len(st["queue"])
        return {"ok": True, "position": n,
                "msg": f"{lane} queued for {box} (position {n}/{n})"}, True

    return _transact(box, cfg, _clock(now), fn)


def _take_once(box, lane, pid, lease, cfg, now_fn):
    def fn(st, events, t):
        h = st["holder"]
        if h is not None and h["lane"] == lane:
            return {"ok": True, "lease_until": h["lease_until"],
                    "msg": f"{lane} already holds {box}"}, False
        i = _find(st["queue"], lane)
        if i is None:
            st["queue"].append({"lane": lane, "pid": int(pid),
                                "enqueued_at": t, "seen_at": t})
            _event(events, t, box, "ENQUEUE", lane)
            i = len(st["queue"]) - 1
        else:
            st["queue"][i].update(pid=int(pid), seen_at=t)
        n = len(st["queue"])
        if st["state"] != "pristine":
            owed = (" — a refresh is owed (supervisor: box-queue refresh "
                    f"--box {box})" if st["state"].startswith("dirty:") else "")
            return {"ok": False, "position": i + 1,
                    "msg": f"{box} is {st['state']}{owed}; {lane} waits "
                           f"at position {i + 1}/{n}"}, True
        if i != 0:
            return {"ok": False, "position": i + 1,
                    "msg": f"{lane} is not the queue head (position "
                           f"{i + 1}/{n}, head={st['queue'][0]['lane']})"}, True
        st["queue"].pop(0)
        until = min(t + lease, t + cfg["max_hold_s"])
        st["holder"] = {"lane": lane, "pid": int(pid), "taken_at": t,
                        "lease_until": until}
        frm, st["state"], st["reason"] = st["state"], f"held:{lane}", ""
        _event(events, t, box, "TAKE", lane, frm, st["state"])
        return {"ok": True, "lease_until": until,
                "msg": f"{lane} holds {box} (lease {lease:.0f}s — renew "
                       f"before it lapses, release when done)"}, True

    return _transact(box, cfg, now_fn, fn)


def _lease(cfg, lease_s):
    lease = cfg["lease_s"] if lease_s is None else float(lease_s)
    if not (0 < lease <= cfg["max_hold_s"]):
        raise BoxQueueError(
            f"--lease must be within 0..{cfg['max_hold_s']:.0f}s (max_hold_s)")
    return lease


def take(box, lane, pid, wait_s=0.0, lease_s=None, poll_s=5.0,
         now_fn=time.time, sleep_fn=time.sleep):
    """Strict FIFO: only the queue head takes, and only a pristine box.
    `wait_s` > 0 polls (each poll also refreshes the lane's seen_at), bounded
    by `wait_s` itself."""
    _check_box(box)
    _check_lane(lane)
    _check_pid(pid)
    if not (0 <= wait_s <= MAX_WAIT_S):
        raise BoxQueueError(f"--wait must be within 0..{MAX_WAIT_S:.0f}s")
    if not poll_s > 0:
        raise BoxQueueError("--poll must be positive")
    cfg = load_config(box)
    _require_box_config(box, cfg)
    if poll_s > cfg["queue_ttl_s"] / 2:
        raise BoxQueueError(
            f"--poll must be <= queue_ttl_s/2 ({cfg['queue_ttl_s'] / 2:.0f}s), "
            f"or the lane's own queue entry lapses between polls")
    lease = _lease(cfg, lease_s)
    deadline = now_fn() + wait_s
    while True:
        res = _take_once(box, lane, pid, lease, cfg, now_fn)
        remaining = deadline - now_fn()
        if res["ok"] or remaining <= 0:
            return res
        sleep_fn(min(poll_s, remaining))


def renew(box, lane, lease_s=None, now=None):
    _check_box(box)
    _check_lane(lane)
    cfg = load_config(box)
    lease = _lease(cfg, lease_s)

    def fn(st, events, t):
        h = st["holder"]
        if h is None or h["lane"] != lane:
            return {"ok": False, "msg": f"{lane} does not hold {box}"
                    + _lost_detail(st, lane)}, False
        cap = h["taken_at"] + cfg["max_hold_s"]
        h["lease_until"] = min(t + lease, cap)
        _event(events, t, box, "RENEW", lane)
        capped = " (capped at max_hold_s)" if t + lease > cap else ""
        return {"ok": True, "lease_until": h["lease_until"],
                "msg": f"{lane} lease on {box} renewed{capped}"}, True

    return _transact(box, cfg, _clock(now), fn)


def release(box, lane, clean=False, reason="", now=None):
    """Hand the box back: dirty:<lane> unless `clean` (the lane's explicit
    claim it changed nothing). Fail-safe default = dirty (#1171)."""
    _check_box(box)
    _check_lane(lane)
    cfg = load_config(box)

    def fn(st, events, t):
        h = st["holder"]
        if h is not None and h["lane"] == lane:
            st["holder"] = None
            frm = st["state"]
            if clean:
                st["state"], st["reason"] = "pristine", ""
            else:
                st["state"] = f"dirty:{lane}"
                st["reason"] = reason or f"released dirty by {lane}"
            _event(events, t, box, "RELEASE", lane, frm, st["state"], reason)
            return {"ok": True, "msg": f"{lane} released {box} -> "
                                       f"{st['state']}"}, True
        i = _find(st["queue"], lane)
        if i is not None:
            st["queue"].pop(i)
            _event(events, t, box, "WITHDRAW", lane)
            return {"ok": True, "msg": f"{lane} left the {box} queue"}, True
        return {"ok": False, "msg": f"{lane} neither holds nor waits for "
                                    f"{box}" + _lost_detail(st, lane)}, False

    return _transact(box, cfg, _clock(now), fn)


def _sweep_leftovers(lock_path, log_path):
    """After a command, kill any OTHER process still owning the refresh lock:
    a step that left the process group (GNU `timeout`, a setsid daemon)
    would otherwise keep changing the box — and keep the lock — after the
    refresh is recorded done. Returns the owners that could not be killed."""
    killed, left = _kill_lock_owners(lock_path)
    if killed or left:
        _append(log_path, [f"--- killed leftover process(es) {killed} still "
                           f"owning the refresh lock"
                           + (f"; STILL OWNED by {left}" if left else "")])
    return left


def _run(cmd, timeout, cfg, box, log_path, label, lock_fd, lock_path):
    """(rc | None, error-or-empty). The command runs in its OWN process group
    (killed whole on timeout or interruption) and inherits `lock_fd`, so the
    refresh lock stays held for as long as any part of it lives. Output goes
    to the state-dir refresh log."""
    env = dict(os.environ, BOX_QUEUE_BOX=box)
    cwd = cfg.get("cwd") or str(state_dir())
    _append(log_path, [f"--- {datetime.now(timezone.utc).isoformat()} {label}: {cmd}"])
    with open(log_path, "a") as logf:
        try:
            proc = subprocess.Popen(_argv(cmd), stdout=logf, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=cwd, env=env,
                                    start_new_session=True, pass_fds=(lock_fd,))
        except OSError as e:
            return None, f"{label} could not start: {e}"
        try:
            exited = _exited_unreaped(proc, timeout)
        except BaseException:
            _kill_group(proc)
            _sweep_leftovers(lock_path, log_path)
            raise
        rc = _kill_group(proc)   # exited: only sweeps leftovers; else: the timeout kill
        left = _sweep_leftovers(lock_path, log_path)
        if not exited:
            return None, f"{label} timed out after {timeout:.0f}s"
        if left:
            return None, f"{label}: leftover process(es) {left} could not be killed"
        return rc, ""


def _run_refresh(box, cfg, now_fn, sleep_fn, lock_fd):
    """(ok, why). The refresh command once, then the health command polled
    until it passes or health_timeout_s runs out."""
    log_path, lock_path = _paths(box)["refresh_log"], _paths(box)["refresh_lock"]
    rc, err = _run(cfg["refresh"], cfg["refresh_timeout_s"], cfg, box, log_path,
                   "refresh", lock_fd, lock_path)
    if rc != 0:
        tail = _tail_line(log_path)
        why = err or f"refresh command rc={rc}"
        return False, why + (f"; last output: {tail}" if tail else "")
    if not cfg.get("health"):
        return True, ""
    deadline = now_fn() + cfg["health_timeout_s"]
    last = "no run"
    while True:
        remaining = deadline - now_fn()
        if remaining <= 0:
            return False, (f"health check never passed within "
                           f"{cfg['health_timeout_s']:.0f}s ({last})")
        rc, err = _run(cfg["health"], max(remaining, 1.0), cfg, box, log_path,
                       "health", lock_fd, lock_path)
        if rc == 0:
            return True, ""
        last = err or f"health rc={rc}"
        sleep_fn(min(cfg["health_interval_s"], max(deadline - now_fn(), 0.0)))


def refresh(box, force=False, now_fn=time.time, sleep_fn=time.sleep):
    """dirty -> refreshing -> (project refresh + health) -> pristine, or back
    to dirty with the reason. Refused while held or already refreshing; a
    pristine box is a no-op unless `force`."""
    _check_box(box)
    cfg = load_config(box)
    if not cfg.get("refresh"):
        raise BoxQueueError(
            f"no refresh command configured for {box}: the project writes "
            f"{_paths(box)['config']} with a \"refresh\" (and ideally a "
            f"\"health\") command")
    token = uuid.uuid4().hex
    budget = (cfg["refresh_timeout_s"] + cfg["health_timeout_s"]
              + cfg["health_interval_s"] + _REFRESH_MARGIN_S)

    def start(st, events, t):
        s = st["state"]
        if s == "refreshing":
            # We hold the refresh lock EXCLUSIVELY (taken before this
            # transaction), so no process of the recorded refresh is alive:
            # the record is stale. Retire it and refresh on top of it.
            old = st["refresh"]
            s = old["from"] if old["from"].startswith("dirty:") else "dirty:refresh"
            st["refresh"], st["state"] = None, s
            _event(events, t, box, "REFRESH-LOST", frm="refreshing", to=s,
                   reason="stale refreshing record — its refresh lock was free")
        if s.startswith("held:"):
            return {"ok": False, "started": False,
                    "msg": f"{box} is {s} — a held box is never refreshed"}, False
        if s == "pristine" and not force:
            return {"ok": True, "started": False,
                    "msg": f"{box} is already pristine — nothing to refresh"}, False
        st["refresh"] = {"token": token, "pid": os.getpid(), "started_at": t,
                         "deadline": t + budget, "from": s}
        st["state"] = "refreshing"
        _event(events, t, box, "REFRESH-START", frm=s, to="refreshing")
        return {"ok": True, "started": True}, True

    paths = _paths(box)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(paths["refresh_lock"]), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not _grab_refresh_lock(lock_fd):
            return _grab_refused(box, cfg, now_fn, paths)
        res = _transact(box, cfg, now_fn, start, own_refresh=True)
        if not res.get("started"):
            return res
        with _signals_interrupt():
            try:
                ok, why = _run_refresh(box, cfg, now_fn, sleep_fn, lock_fd)
            except _Interrupted as e:
                ok, why = False, f"refresh interrupted by signal {e.signum}"
        result = _transact(box, cfg, now_fn, _finisher(box, token, ok, why),
                           own_refresh=True)
    finally:
        # Closing our fd drops the lock unless a command that inherited it
        # still runs — exactly the liveness signal the reaper reads.
        os.close(lock_fd)
    if lock_held(paths["refresh_lock"]):
        leak = (f"a background process started by the refresh command still "
                f"holds {paths['refresh_lock']} (it inherited the fd; `fuser` "
                f"names it) — the next refresh waits for it. Start local "
                f"daemons with their fds closed.")
        print(f"box-queue: WARNING {leak}", file=sys.stderr)
        events = []
        _event(events, now_fn(), box, "LOCK-LEAK", reason=leak)
        _append(paths["decisions"], events)
    return result


def _grab_refused(box, cfg, now_fn, paths):
    """The refresh lock is owned by someone else. With a live `refreshing`
    record that is a running refresh; without one it is a leftover of an
    earlier refresh whose CLI was SIGKILLed — named, so the supervisor can
    kill it (a refresh that just took the lock but has not yet recorded
    itself looks the same, so the tool does not kill it on its own)."""
    state = _transact(box, cfg, now_fn, lambda st, _e, _t: ({}, False))["state"]
    if state == "refreshing":
        return {"ok": False, "msg": f"a refresh of {box} still runs; not "
                                    f"starting a second one"}
    owners = _lock_owners(paths["refresh_lock"])
    named = ", ".join(f"{pid} ({_cmdline(pid)})" for pid in owners) or "unknown"
    return {"ok": False, "msg": f"{box} is {state} but process(es) {named} still "
            f"own {paths['refresh_lock']} with no refresh recorded — a leftover "
            f"of an earlier refresh; kill it, then refresh again"}


def _grab_refresh_lock(lock_fd):
    """Non-blocking; a few short retries ride out a reaper's momentary probe
    of the same lock. False = a refresh (or its orphaned command) runs."""
    for attempt in range(10):
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if attempt < 9:
                time.sleep(0.1)
    return False


def _finisher(box, token, ok, why):
    def finish(st, events, t):
        r = st["refresh"]
        if st["state"] != "refreshing" or r is None or r["token"] != token:
            return {"ok": False, "msg": f"refresh of {box} finished but the box "
                    f"moved to {st['state']} meanwhile ({st['reason']}); "
                    f"result discarded"}, False
        st["refresh"] = None
        if ok:
            st["state"], st["reason"] = "pristine", ""
            _event(events, t, box, "REFRESH-OK", frm="refreshing", to="pristine")
            return {"ok": True, "msg": f"{box} refreshed -> pristine"}, True
        st["state"] = r["from"] if r["from"].startswith("dirty:") else "dirty:refresh"
        st["reason"] = f"refresh failed: {why}"
        _event(events, t, box, "REFRESH-FAIL", frm="refreshing",
               to=st["state"], reason=why)
        return {"ok": False, "msg": f"refresh of {box} FAILED ({why}); box "
                                    f"stays {st['state']}"}, True

    return finish


def status(box=None, now=None):
    """One box's state (pending reclaims applied), or every known box when
    `box` is None. A box with no state file yet reads as a fresh pristine
    box without touching disk."""
    if box is None:
        d = state_dir()
        names = sorted(p.name[:-len(".json")] for p in d.glob("*.json")
                       if not p.name.endswith(".config.json")) if d.is_dir() else []
        return [status(n, now=now) for n in names if _BOX_RE.match(n)]
    _check_box(box)
    t = time.time() if now is None else now
    if not _paths(box)["state"].exists():
        return _fresh_state(box, t)
    cfg = load_config(box)
    snap = {}

    def fn(st, events, _t):
        snap.update(json.loads(json.dumps(st)))
        return {}, False

    _transact(box, cfg, _clock(now), fn)
    return snap


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(st, now):
    h = st["holder"]
    held = "-"
    if h:
        left = max(h["lease_until"] - now, 0)
        held = f"{h['lane']} (pid {h['pid']}, lease {left:.0f}s left)"
    lines = [f"BOX {st['box']}  state={st['state']}  holder={held}  "
             f"queue={len(st['queue'])}"]
    for i, e in enumerate(st["queue"], 1):
        lines.append(f"  {i}. {e['lane']} (pid {e['pid']}, waiting "
                     f"{max(now - e['enqueued_at'], 0):.0f}s)")
    if st["reason"]:
        lines.append(f"  reason: {st['reason']}")
    return "\n".join(lines)


def register_parser(sub):
    """The `box-queue` argparse subparser (kept here so main() grows by no
    parser block)."""
    p = sub.add_parser(
        "box-queue",
        help="#1171: FIFO queue + lease + pristine/dirty state for the ONE "
             "shared test box of a stream (enqueue|take|renew|release|"
             "refresh|status)")
    p.add_argument("bq_action", choices=["enqueue", "take", "renew", "release",
                                         "refresh", "status"])
    p.add_argument("--box", default=None, help="box name, e.g. erp-test-montalu4")
    p.add_argument("--lane", default=None,
                   help="lane id (the worktree branch or ticket number)")
    p.add_argument("--pid", type=int, default=None,
                   help="liveness pid to record (default: the long-lived "
                        "claude process of this session)")
    p.add_argument("--wait", type=float, default=0.0,
                   help="take: poll up to this many seconds for your turn")
    p.add_argument("--poll", type=float, default=5.0, help="take: poll interval")
    p.add_argument("--lease", type=float, default=None,
                   help="take/renew: lease seconds (default from config)")
    p.add_argument("--clean", action="store_true",
                   help="release: this lane changed NOTHING on the box — hand it "
                        "back pristine (default: dirty, a refresh is owed)")
    p.add_argument("--reason", default="", help="release: why the box is dirty")
    p.add_argument("--force", action="store_true",
                   help="refresh: also refresh a box that reads pristine")
    p.add_argument("--json", action="store_true", help="status: JSON output")
    return p


def _dispatch(args):
    action = args.bq_action
    if not args.box:
        raise BoxQueueError(f"{action} needs --box")
    if action == "refresh":
        return refresh(args.box, force=args.force)
    if not args.lane:
        raise BoxQueueError(f"{action} needs --lane")
    pid = args.pid if args.pid is not None else _campaign_pid()
    if action == "enqueue":
        return enqueue(args.box, args.lane, pid)
    if action == "take":
        return take(args.box, args.lane, pid, wait_s=args.wait,
                    lease_s=args.lease, poll_s=args.poll)
    if action == "renew":
        return renew(args.box, args.lane, lease_s=args.lease)
    return release(args.box, args.lane, clean=args.clean, reason=args.reason)


def cmd_box_queue(args):
    """`airuleset.py box-queue …` — exit 0 ok, 1 refused / not your turn /
    refresh failed, 2 usage or config error."""
    try:
        if args.bq_action == "status":
            res = status(args.box)
            now = time.time()
            if args.json:
                print(json.dumps(res, indent=1, sort_keys=True))
            elif isinstance(res, list):
                print("\n".join(_render(s, now) for s in res) or "no boxes")
            else:
                print(_render(res, now))
            return 0
        res = _dispatch(args)
    except BoxQueueError as e:
        print(f"box-queue: {e}", file=sys.stderr)
        return 2
    print(res["msg"], file=sys.stdout if res["ok"] else sys.stderr)
    return 0 if res["ok"] else 1
