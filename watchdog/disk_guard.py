"""Disk-pressure guard — watchdog Job 40 (#834).

Every managed box carries per-CLASS disk sweeps (scratch/tmp-stray/cli-version/
worktree/transcript/state), each in its own ``cli_*.py`` module with its own
timer — and every one of them is pressure-BLIND, so a box fills to the wall
with zero signal (gk hit 91 %, subdev 90 %). This module is the ONE
pressure-driven consumer of that existing machinery: it reads ``statvfs`` on
every poll, writes a footer cache so the owner SEES ``disk NN%`` before it
bites, and — when a box crosses the drain threshold — runs an auto-drain
LADDER over the calling user's OWN ``$HOME`` (the per-USER scope: on subdev
each stream user's watchdog reclaims that user's 20 G of worktrees + 18 G of
toolchain), fail-LOUD (every action AND skip logged to ``disk-guard.log``),
never deleting on uncertainty, never crossing a filesystem.

Design: a PLAN/EXECUTE split. Each rung is a pure PLANNER returning a list of
action dicts ``{cls, path, bytes, kind, reason}`` (``kind`` ∈ delete / gzip /
worktree-remove / report / skip); ONE executor (:func:`execute_drain`) performs
each action and logs it, refuses to run as root (the root leg is #841), takes an
``flock`` single-instance lock, re-checks ``statvfs`` between rungs and stops
the moment the worst mount is back under target, and NEVER touches a class
outside the :data:`RECLAIMABLE_CLASSES` fence. The reclaimable classes are OURS
only — the CI runner, docker-at-root, ``/var/log`` and other users' homes are
SURFACED in the ≥90 % escalation, never auto-deleted (those are #841).

stdlib-only at module level; every reuse of a ``cli_*`` discovery function and
of ``watchdog.reaper`` is a DEFERRED import inside the function that needs it,
so this module has no import cycle with the ``watchdog`` package that hosts it.
"""

import fcntl
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# --- thresholds (#834 req 1) ------------------------------------------------ #
NOTICE_PCT = 75            # footer shows `disk NN%` (yellow) at/above this
DRAIN_PCT = 80             # AUTO-DRAIN at/above this
CRITICAL_PCT = 90          # machine-channel escalation at/above this (red footer)
TARGET_PCT = 75            # drain stops once the worst mount is back below this
MOUNTS = ("/", "/home", "/tmp")

# The scope FENCE (#834 — "surface-not-delete for runner/docker/logs/other
# users"). The executor NEVER acts on a class outside this literal allowlist;
# a rogue planner emitting one is skipped+logged. Every member here is OUR own
# per-user reclaimable class — never a root/cross-user one. NOTE: `docker` is
# deliberately ABSENT — a `docker image prune` for a docker-group user talks to
# the ROOT daemon and prunes shared/cross-user images (CI images, other
# streams'), which is a box-wide op, so docker stays fully in the root-leg
# follow-up (surfaced there, never auto-pruned here) — review 🔴.
RECLAIMABLE_CLASSES = frozenset({
    "scratch", "tmp-stray", "worktree", "cli-version",
    "uploads", "transcript", "toolchain", "journal",
})

DISK_GUARD_DIRNAME = "disk-guard"
STATUS_CACHE_NAME = "status.json"
LOG_NAME = "disk-guard.log"
LAST_DRAIN_NAME = "last-drain"
LOCK_NAME = ".lock"
LOG_MAX_BYTES = 512 * 1024                  # self-bounding (#834 review-bite 7)
MIN_DRAIN_INTERVAL_S = 10 * 60              # du-heavy ladder runs at most this often
UPLOADS_MAX_AGE_DAYS = 14
TRANSCRIPT_PRESSURE_MIN_AGE_DAYS = 7        # owner-authorised pressure path (#834 rung e)
TOOLCHAIN_DIRS = ("Android", ".gradle", ".android")
TOOLCHAIN_PROC_RE = "gradle|java|emulator|qemu-system"


def _dbg(msg):
    """A single best-effort stderr breadcrumb (comprehensive-logging.md — never
    a silent swallow; the guard is best-effort so these never raise upward)."""
    try:
        print("  disk-guard: " + msg, file=sys.stderr)
    except Exception:
        pass          # airuleset:script-ok stderr itself failing is unrecoverable + irrelevant


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _guard_dir(home=None):
    home = home or os.path.expanduser("~")
    return Path(home) / ".claude" / DISK_GUARD_DIRNAME


def _log_path(home=None):
    return str(_guard_dir(home) / LOG_NAME)


def _human(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return "%.0f%s" % (n, unit) if unit == "B" else "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%.1fT" % n


def _pct(used, denom):
    if denom <= 0:
        return 0
    return int(math.ceil(100.0 * used / denom))


def level_for(pct):
    """The pressure LEVEL for a worst-mount percentage (#834 req 1)."""
    if pct >= CRITICAL_PCT:
        return "critical"
    if pct >= DRAIN_PCT:
        return "drain"
    if pct >= NOTICE_PCT:
        return "notice"
    return "ok"


def _log_line(now, action, path, nbytes, reason):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    return "%s %s %s bytes=%s %s" % (ts, action, path, nbytes, reason or "")


def _append_log(log_path, lines):
    """Append `lines` to the decision log, best-effort, self-bounding: when the
    file exceeds LOG_MAX_BYTES it is truncated to its most recent half in place
    (a pressure guard whose OWN log grows unbounded is self-defeating)."""
    if not lines or not log_path:
        return
    try:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if p.stat().st_size > LOG_MAX_BYTES:
            data = p.read_bytes()[-(LOG_MAX_BYTES // 2):]
            nl = data.find(b"\n")
            if nl != -1:
                data = data[nl + 1:]
            p.write_bytes(b"# --- disk-guard.log rotated in place ---\n" + data)
    except OSError as e:
        print("  disk-guard: could not write log %s: %s" % (log_path, e), file=sys.stderr)


# --------------------------------------------------------------------------- #
# classifier (#834 req 1)
# --------------------------------------------------------------------------- #
def mount_stats(statvfs_fn=None, dev_fn=None, mounts=MOUNTS):
    """Per DISTINCT mount (deduped by ``st_dev`` so ``/home`` sharing ``/``'s
    device collapses to one row), a dict ``{mount, used_pct, inode_pct,
    worst_pct, dim}`` under the ``df`` formula (no root-reserved slack). A mount
    that cannot be stat'd/statvfs'd is skipped (never guessed at)."""
    statvfs_fn = statvfs_fn or os.statvfs
    dev_fn = dev_fn or (lambda p: os.stat(p).st_dev)
    seen = set()
    out = []
    for m in mounts:
        try:
            dev = dev_fn(m)
        except Exception:
            continue          # a mount that isn't present (e.g. no separate /home) — normal
        if dev in seen:
            continue
        try:
            s = statvfs_fn(m)
        except Exception as e:
            # dev resolved but statvfs failed — a real anomaly (the guard goes
            # pressure-BLIND on this mount), never silent (review 🔵).
            _dbg("statvfs failed for %s: %r — mount not measured" % (m, e))
            continue
        seen.add(dev)
        used = s.f_blocks - s.f_bfree
        used_pct = _pct(used, used + s.f_bavail)
        iused = s.f_files - s.f_ffree
        inode_pct = _pct(iused, s.f_files)
        dim = "inodes" if inode_pct > used_pct else "bytes"
        out.append({"mount": m, "used_pct": used_pct, "inode_pct": inode_pct,
                    "worst_pct": max(used_pct, inode_pct), "dim": dim})
    return out


def disk_status(statvfs_fn=None, dev_fn=None, mounts=MOUNTS, now=None):
    """Box-wide pressure status: ``{mounts, worst_pct, dim, level, ts}`` where
    ``worst_pct`` is the max over all distinct mounts of ``max(used%, inode%)``
    and ``dim`` names WHICH dimension (bytes vs inodes) is binding on the worst
    mount (#834 review-bite 6)."""
    now = time.time() if now is None else now
    ms = mount_stats(statvfs_fn, dev_fn, mounts)
    if ms:
        worst_row = max(ms, key=lambda r: r["worst_pct"])
        worst, dim = worst_row["worst_pct"], worst_row["dim"]
    else:
        worst, dim = 0, "bytes"
    return {"mounts": ms, "worst_pct": worst, "dim": dim,
            "level": level_for(worst), "ts": now}


def write_status_cache(status, home=None, path=None):
    """Write the ts-stamped footer cache the statusbar reads (every poll)."""
    p = Path(path) if path else (_guard_dir(home) / STATUS_CACHE_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status), encoding="utf-8")
    os.replace(tmp, p)


# --------------------------------------------------------------------------- #
# NEW planners (uploads, toolchain)
# --------------------------------------------------------------------------- #
def discover_stale_uploads(home=None, now=None, max_age_days=UPLOADS_MAX_AGE_DAYS,
                           uploads_dir=None):
    """Own-home ``~/uploads`` files older than `max_age_days` (#834 rung d — the
    upload inbox is a delivery CHANNEL, not storage; consumed recordings/dumps
    age out). Rows ``{cls, path, size, age_days, reason}`` — ``reason`` is None
    for a genuine candidate, else why it was kept. No ``~/uploads`` at all is
    simply nothing to do, not an error."""
    now = time.time() if now is None else now
    up = Path(uploads_dir) if uploads_dir else (Path(home or os.path.expanduser("~")) / "uploads")
    if not up.is_dir():
        return []
    cutoff = max_age_days * 86400
    out = []
    try:
        for dirpath, _dirs, files in os.walk(up, onerror=lambda e: None):
            for f in files:
                fp = os.path.join(dirpath, f)
                try:
                    st = os.lstat(fp)
                except OSError as e:
                    out.append({"cls": "uploads", "path": fp, "size": 0,
                                "reason": "could not stat: %s" % e})
                    continue
                if os.path.islink(fp):
                    out.append({"cls": "uploads", "path": fp, "size": st.st_size,
                                "reason": "symlink — never followed"})
                    continue
                age = now - st.st_mtime
                row = {"cls": "uploads", "path": fp, "size": st.st_size,
                       "age_days": age / 86400.0, "reason": None}
                if age < cutoff:
                    row["reason"] = "too recent (%.1fd < %dd)" % (age / 86400.0, max_age_days)
                out.append(row)
    except OSError as e:
        return [{"cls": "uploads", "path": None, "reason": "could not walk %s: %s" % (up, e)}]
    return out


def _default_box_class():
    try:
        from watchdog.reaper import default_box_class
        return default_box_class()
    except Exception as e:
        _dbg("box-class read failed: %r" % e)
        return None


def _default_pgrep(pattern):
    try:
        r = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", pattern],
                           capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except Exception as e:
        _dbg("pgrep failed: %r" % e)
        return "PGREP-ERROR"          # fail-safe: unknown → treat as live → skip


def _safe_dir_size(path, dir_stats_fn=None):
    try:
        if dir_stats_fn is not None:
            return dir_stats_fn(path)[0]
        from cli_target_purge import _dir_stats
        return _dir_stats(path)[0]
    except Exception as e:
        _dbg("dir-size failed for %s: %r" % (path, e))
        return 0


def discover_toolchain_dirs(home=None, box_class_fn=None, pgrep_fn=None,
                            dir_stats_fn=None):
    """Heavy build-toolchain dirs (``~/Android``, ``~/.gradle``, ``~/.android``)
    — banned on a shared-stream box (#778) yet only LAUNCH-blocked there, never
    removed (~18 G stayed). On a ``shared-stream`` box the guard REMOVES them
    (skipped+logged only while a live java/gradle/emulator process of this user
    runs); on a ``workstation`` box they are REPORT-only. Rows ``{cls, path,
    size, kind, reason}``."""
    home = home or os.path.expanduser("~")
    box_class_fn = box_class_fn or _default_box_class
    pgrep_fn = pgrep_fn or _default_pgrep
    try:
        shared = box_class_fn() == "shared-stream"
    except Exception as e:
        _dbg("box-class classify failed: %r" % e)
        shared = False
    live = ""
    if shared:
        try:
            live = pgrep_fn(TOOLCHAIN_PROC_RE) or ""
        except Exception as e:
            _dbg("toolchain pgrep failed: %r" % e)
            live = "PGREP-ERROR"
    out = []
    for d in TOOLCHAIN_DIRS:
        p = os.path.join(home, d)
        if not os.path.isdir(p):
            continue
        size = _safe_dir_size(p, dir_stats_fn)
        if not shared:
            out.append({"cls": "toolchain", "path": p, "size": size, "kind": "report",
                        "reason": "workstation box — toolchain reported, not removed (#778)"})
        elif live.strip():
            out.append({"cls": "toolchain", "path": p, "size": size, "kind": "skip",
                        "reason": "live java/gradle/emulator process present — kept"})
        else:
            out.append({"cls": "toolchain", "path": p, "size": size, "kind": "delete",
                        "reason": None})
    return out


# --------------------------------------------------------------------------- #
# rung PLANNERS (adapters over the existing per-class discovery functions)
# --------------------------------------------------------------------------- #
def _row_to_action(cls, row, kind):
    path = row.get("path")
    if path is None:
        return {"cls": cls, "path": "-", "bytes": 0, "kind": "skip",
                "reason": row.get("reason", "discovery error")}
    size = row.get("size") or 0
    reason = row.get("reason")
    if reason is not None:
        return {"cls": cls, "path": path, "bytes": size, "kind": "skip", "reason": reason}
    return {"cls": cls, "path": path, "bytes": size, "kind": kind, "reason": None}


def _plan_scratch(home, now):
    from cli_scratch_sweep import (discover_claude_scratch_candidates,
                                   discover_stray_tmp_candidates)
    actions = []
    try:
        for r in discover_claude_scratch_candidates(now=now) or []:
            actions.append(_row_to_action("scratch", r, "delete"))
    except Exception as e:
        actions.append({"cls": "scratch", "path": "-", "bytes": 0, "kind": "skip",
                        "reason": "scratch discovery error: %r" % e})
    try:
        tmp = discover_stray_tmp_candidates(now=now) or {}
        for r in tmp.get("examined", []):
            actions.append(_row_to_action("tmp-stray", r, "delete"))
    except Exception as e:
        actions.append({"cls": "tmp-stray", "path": "-", "bytes": 0, "kind": "skip",
                        "reason": "tmp-stray discovery error: %r" % e})
    return actions


def _plan_worktrees(home, now):
    from cli_worktree_sweep import discover_reclaimable_worktrees
    actions = []
    try:
        rows = discover_reclaimable_worktrees(home=home, now=now) or []
    except Exception as e:
        return [{"cls": "worktree", "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "worktree discovery error: %r" % e}]
    for r in rows:
        path = r.get("path")
        if path is None:
            continue
        reason = r.get("reason")
        size = r.get("size") or (_safe_dir_size(path) if reason is None else 0)
        if reason is not None:
            actions.append({"cls": "worktree", "path": path, "bytes": size,
                            "kind": "skip", "reason": reason})
            continue
        kind = "worktree-remove" if r.get("kind") == "worktree" else "delete"
        actions.append({"cls": "worktree", "path": path, "bytes": size, "kind": kind,
                        "reason": None, "branch": r.get("branch"), "repo": r.get("repo")})
    return actions


def _plan_cli_versions(home, now):
    from cli_target_purge import discover_cli_version_candidates
    try:
        rows = discover_cli_version_candidates(home=home, now=now) or []
    except Exception as e:
        return [{"cls": "cli-version", "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "cli-version discovery error: %r" % e}]
    return [_row_to_action("cli-version", r, "delete") for r in rows]


def _plan_uploads(home, now):
    return [_row_to_action("uploads", r, "delete")
            for r in discover_stale_uploads(home=home, now=now)]


def _plan_transcripts(home, now):
    from cli_scratch_sweep import discover_old_transcript_candidates
    try:
        rows = discover_old_transcript_candidates(
            home=home, now=now, min_age_days=TRANSCRIPT_PRESSURE_MIN_AGE_DAYS) or []
    except Exception as e:
        return [{"cls": "transcript", "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "transcript discovery error: %r" % e}]
    return [_row_to_action("transcript", r, "gzip") for r in rows]


def _plan_toolchain(home, now):
    return discover_toolchain_dirs(home=home)


def _plan_journal(home, now):
    """Per-user journald vacuum (#834 rung g, own-user half — the system journal
    + apt clean are root, #841). Returns an ACTION (kind `journal-vacuum`) so the
    subprocess runs inside `_perform_action` through the do_action seam + fence,
    never at planner time (review 🟡); dry-run therefore never vacuums."""
    return [{"cls": "journal", "path": "journalctl --user --vacuum-size=100M",
             "bytes": 0, "kind": "journal-vacuum", "reason": None}]


def _default_planners(home, now):
    """The auto-drain LADDER, cheapest/safest first (#834 rung order a→g,
    own-home + own-user only). Docker (rung h) is a box-wide/root op and stays
    in the root-leg follow-up — never auto-pruned here (review 🔴)."""
    return [
        ("scratch", lambda: _plan_scratch(home, now)),
        ("worktree", lambda: _plan_worktrees(home, now)),
        ("cli-version", lambda: _plan_cli_versions(home, now)),
        ("uploads", lambda: _plan_uploads(home, now)),
        ("transcript", lambda: _plan_transcripts(home, now)),
        ("toolchain", lambda: _plan_toolchain(home, now)),
        ("journal", lambda: _plan_journal(home, now)),
    ]


# --------------------------------------------------------------------------- #
# action execution
# --------------------------------------------------------------------------- #
def _rm_path(path):
    """Delete a file or directory, never crossing a filesystem
    (``--one-file-system``). TOCTOU re-verify (review 🟡): re-lstat immediately
    and REFUSE a path that has become a symlink since discovery (never delete
    THROUGH a symlink). Raises on failure (the caller logs it)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return                              # already gone between plan and act — fine
    import stat as _stat
    if _stat.S_ISLNK(st.st_mode):
        raise OSError("refusing to delete %s — it is now a symlink (TOCTOU)" % path)
    if os.path.isdir(path):
        subprocess.run(["rm", "-rf", "--one-file-system", "--", path],
                       check=True, capture_output=True, text=True, timeout=300)
    else:
        os.unlink(path)


def _remove_worktree_dir(a):
    """`git worktree remove` the directory (the branch REF is kept). NO
    `--force`: the tree was verified clean at plan time, so plain `remove`
    succeeds; a state that raced dirty makes git REFUSE, and that refusal is a
    SKIP (raise → logged FAIL), NEVER a raw `rm -rf` that would bulldoze it
    (review 🔴). TOCTOU re-verify: re-check `git status` clean immediately
    before removal (review 🟡)."""
    repo, path = a.get("repo"), a.get("path")
    if not repo:
        raise OSError("worktree-remove with no repo for %s" % path)
    clean = _worktree_status_clean_recheck(path)
    if clean is not True:
        raise OSError("worktree %s no longer clean/measurable at remove time — SKIP" % path)
    r = subprocess.run(["git", "-C", repo, "worktree", "remove", "--", path],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise OSError("git worktree remove refused %s: %s" % (path, (r.stderr or "").strip()))


def _worktree_status_clean_recheck(path):
    """True only when `git status --porcelain` in `path` is empty NOW; False if
    it reports changes; None if unmeasurable. A pre-delete TOCTOU guard using
    the SAME check the planner used (review 🟡)."""
    try:
        r = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() == ""


def _perform_action(a):
    kind = a.get("kind")
    path = a.get("path")
    nbytes = a.get("bytes", 0) or 0
    if kind == "gzip":
        from cli_scratch_sweep import _compress_transcript_file
        res = _compress_transcript_file(path)
        # _compress_transcript_file NEVER raises; removed=False is a failure it
        # returns as data — surface it as a FAIL, not a false success (review 🟡).
        if not (isinstance(res, dict) and res.get("removed")):
            raise OSError("gzip did not complete for %s: %s"
                          % (path, (res or {}).get("reason") if isinstance(res, dict) else res))
        return nbytes
    if kind == "worktree-remove":
        _remove_worktree_dir(a)
        return nbytes
    if kind == "journal-vacuum":
        subprocess.run(["journalctl", "--user", "--vacuum-size=100M"],
                       check=True, capture_output=True, text=True, timeout=60)
        return nbytes
    if kind == "delete":
        _rm_path(path)
        return nbytes
    return 0


def _make_do_action(dry_run):
    def _do(a):
        if dry_run:
            return a.get("bytes", 0) or 0
        return _perform_action(a)
    return _do


def execute_drain(status, home, planners, recheck_fn, do_action_fn,
                  geteuid_fn=None, log_path=None, now=None, dry_run=False):
    """Run the drain ladder. Refuses as root (per-user deletion against root's
    fs view is #841). Between rungs, re-checks the worst mount and stops once
    it is back under :data:`TARGET_PCT`. Every action AND skip is logged; a
    class outside :data:`RECLAIMABLE_CLASSES` is skip-fenced, never acted on.
    Under `dry_run` the action verbs are tagged `WOULD-…` so the audit log never
    records a deletion that did not happen (review 🟡). Returns the log lines
    (also appended to `log_path`)."""
    geteuid_fn = geteuid_fn or os.geteuid
    now = time.time() if now is None else now
    logs = []
    if geteuid_fn() == 0:
        line = _log_line(now, "REFUSE", "-", 0,
                         "euid==0 — per-user drain refused (root leg is #841)")
        logs.append(line)
        _append_log(log_path, [line])
        return logs
    dim = status.get("dim", "bytes")
    for _label, planner in planners:
        worst = recheck_fn()
        if worst < TARGET_PCT:
            line = _log_line(now, "STOP", "-", 0,
                             "worst mount %d%% < target %d%% (dim=%s) — drain complete"
                             % (worst, TARGET_PCT, dim))
            logs.append(line)
            _append_log(log_path, [line])
            break
        try:
            actions = planner()
        except Exception as e:
            line = _log_line(now, "ERROR", _label, 0, "planner error: %r" % e)
            logs.append(line)
            _append_log(log_path, [line])
            continue
        rung_lines = []
        for a in actions:
            acls = a.get("cls", _label)
            path = a.get("path", "-")
            planned = a.get("bytes", 0) or 0
            kind = a.get("kind", "delete")
            reason = a.get("reason")
            if acls not in RECLAIMABLE_CLASSES:
                rung_lines.append(_log_line(
                    now, "SKIP-FENCE", path, planned,
                    "class %r outside RECLAIMABLE_CLASSES fence" % acls))
                continue
            if kind == "skip":
                rung_lines.append(_log_line(now, "SKIP", path, planned, reason))
                continue
            if kind == "report":
                rung_lines.append(_log_line(now, "REPORT", path, planned,
                                            reason or "report-only"))
                continue
            try:
                freed = do_action_fn(a)
            except Exception as e:
                rung_lines.append(_log_line(now, "FAIL", path, planned,
                                            "action error: %r" % e))
                continue
            verb = ("WOULD-" + kind.upper()) if dry_run else kind.upper()
            rung_lines.append(_log_line(now, verb, path, planned,
                                        "freed~=%s %s" % (freed, reason or "")))
        logs.extend(rung_lines)
        _append_log(log_path, rung_lines)
    return logs


# --------------------------------------------------------------------------- #
# escalation (#834 req 1 ≥90 %, machine-channel; box-wide daily dedup)
# --------------------------------------------------------------------------- #
def _ranked_consumers(home, now):
    """Ranked (class, reclaimable-bytes) for THIS user's own home — never a
    cross-user ``du`` (a stream user cannot read other homes; #834 review-bite
    3). Best-effort; a failing planner contributes 0, never kills the list."""
    ranked = []
    for label, plan in (("worktree", lambda: _plan_worktrees(home, now)),
                        ("transcript", lambda: _plan_transcripts(home, now)),
                        ("toolchain", lambda: _plan_toolchain(home, now)),
                        ("uploads", lambda: _plan_uploads(home, now)),
                        ("cli-version", lambda: _plan_cli_versions(home, now)),
                        ("scratch", lambda: _plan_scratch(home, now))):
        total = 0
        try:
            for a in plan():
                if a.get("kind") not in ("skip", "report") and a.get("reason") is None:
                    total += a.get("bytes", 0) or 0
        except Exception as e:
            _dbg("ranked-consumers %s failed: %r" % (label, e))
            total = 0
        if total:
            ranked.append((label, total))
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def escalate(status, home, now, dry_run):
    """Machine-channel LOUD escalation at ≥90 % after the drain, deduped ONCE
    per box per day via a world-readable ``/tmp`` marker (so N stream users on
    subdev do not each alarm). No Discord ping in this lane — the owner-facing
    daily ❓ is #841; the red footer segment IS the in-session surface."""
    marker = "/tmp/airuleset-disk-guard-escalated-%s" % time.strftime(
        "%Y%m%d", time.gmtime(now))
    if os.path.exists(marker):
        return []
    ranked = _ranked_consumers(home, now)
    summary = ", ".join("%s=%s" % (c, _human(b)) for c, b in ranked[:6]) or "(none own-home)"
    line = _log_line(now, "ESCALATE", socket.gethostname(),
                     status["worst_pct"],
                     "dim=%s still-critical after drain; reclaimable-own-home: %s"
                     % (status["dim"], summary))
    _append_log(_log_path(home), [line])
    print("disk-guard ESCALATE (%s): still %d%% (%s) after drain — %s"
          % (socket.gethostname(), status["worst_pct"], status["dim"], summary),
          file=sys.stderr)
    if not dry_run:
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            os.write(fd, (line + "\n").encode("utf-8"))
            os.close(fd)
            try:
                os.chmod(marker, 0o666)
            except OSError as e:
                _dbg("escalation marker chmod failed: %r" % e)
        except FileExistsError:
            _dbg("escalation marker won by another user this poll — deduped")
        except OSError as e:
            _dbg("escalation marker write failed: %r" % e)
    return [line]


# --------------------------------------------------------------------------- #
# cadence + single-instance lock
# --------------------------------------------------------------------------- #
def _drain_due(home, now, min_interval_s=None):
    min_interval_s = MIN_DRAIN_INTERVAL_S if min_interval_s is None else min_interval_s
    p = _guard_dir(home) / LAST_DRAIN_NAME
    try:
        last = float(p.read_text().strip())
    except (OSError, ValueError):
        return True
    return (now - last) >= min_interval_s


def _mark_drained(home, now):
    try:
        p = _guard_dir(home) / LAST_DRAIN_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("%f" % now)
    except OSError as e:
        _dbg("could not record last-drain: %r" % e)


# Distinct from `None`: the lock file could not be CREATED (e.g. ENOSPC on a
# 100%-full bytes/inodes mount — the exact emergency this guard exists for), so
# we proceed LOCKLESS rather than mute the guard forever (review 🟡).
_LOCK_UNAVAILABLE = object()


def _acquire_lock(home):
    """A held fd on success; `None` when ANOTHER drain holds the lock (skip this
    poll); :data:`_LOCK_UNAVAILABLE` when the lock file itself cannot be created
    (proceed LOCKLESS — never mute the guard on a full disk)."""
    try:
        p = _guard_dir(home) / LOCK_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        _dbg("lock file uncreatable (%r) — proceeding lockless" % e)
        return _LOCK_UNAVAILABLE
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None                         # another drain holds it — skip
    except OSError as e:
        os.close(fd)
        _dbg("flock failed (%r) — proceeding lockless" % e)
        return _LOCK_UNAVAILABLE
    return fd


def _release_lock(fd):
    if fd is None or fd is _LOCK_UNAVAILABLE:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError as e:
        _dbg("lock release failed: %r" % e)


# --------------------------------------------------------------------------- #
# Job 40 entry
# --------------------------------------------------------------------------- #
def run_disk_guard(now=None, home=None, dry_run=False, statvfs_fn=None, dev_fn=None,
                   geteuid_fn=None, mounts=None, min_drain_interval_s=None):
    """Watchdog Job 40. Every poll: compute pressure + write the footer cache.
    Only at ≥80 % (and not as root, cadence-gated, single-instance): run the
    drain ladder over this user's own home; if still ≥90 % after, escalate.
    Best-effort; returns log lines for the sweep's own log."""
    now = time.time() if now is None else now
    home = home or os.path.expanduser("~")
    mounts = mounts or MOUNTS
    logs = []
    try:
        status = disk_status(statvfs_fn=statvfs_fn, dev_fn=dev_fn, mounts=mounts, now=now)
    except Exception as e:
        return ["disk-guard: status error: %r" % e]
    try:
        write_status_cache(status, home=home)
    except Exception as e:
        logs.append("disk-guard: cache write failed: %r" % e)
    if status["level"] in ("ok", "notice"):
        return logs
    geteuid_fn = geteuid_fn or os.geteuid
    if geteuid_fn() == 0:
        logs.append("disk-guard: %d%% but euid==0 — per-user drain refused (root leg #841)"
                    % status["worst_pct"])
        return logs
    # #841 leg C: at CRITICAL pressure, surface the root-level reclaimable
    # candidates the per-user drain cannot reach (read from the root reporter's
    # world-readable /run report). Cheap file read — runs even when the du-heavy
    # drain below is cadence-gated. Records a finding a SESSION raises the
    # owner-daily ❓ from; NEVER pings (notify stays out of the guard).
    if status["level"] == "critical":
        try:
            from watchdog import disk_guard_root
            logs += disk_guard_root.maybe_record_root_finding(
                status, home, now, dry_run=dry_run)
        except Exception as e:
            logs.append("disk-guard: root-finding error: %r" % e)
    if not dry_run and not _drain_due(home, now, min_drain_interval_s):
        logs.append("disk-guard: %d%% (%s) — drain cadence-gated this poll"
                    % (status["worst_pct"], status["dim"]))
        return logs
    lock = _acquire_lock(home)
    if lock is None:
        logs.append("disk-guard: %d%% — another drain holds the lock, skipping"
                    % status["worst_pct"])
        return logs
    if lock is _LOCK_UNAVAILABLE:
        logs.append("disk-guard: %d%% — lock uncreatable (disk full?), draining LOCKLESS"
                    % status["worst_pct"])
    try:
        planners = _default_planners(home, now)

        def recheck():
            return disk_status(statvfs_fn=statvfs_fn, dev_fn=dev_fn,
                               mounts=mounts, now=now)["worst_pct"]

        do_action = _make_do_action(dry_run)
        logs += execute_drain(status, home, planners, recheck, do_action,
                              geteuid_fn=geteuid_fn, log_path=_log_path(home),
                              now=now, dry_run=dry_run)
        if not dry_run:
            _mark_drained(home, now)        # never cadence-gate a REAL drain off a dry-run
        post = disk_status(statvfs_fn=statvfs_fn, dev_fn=dev_fn, mounts=mounts, now=now)
        if post["level"] == "critical":
            logs += escalate(post, home, now, dry_run)
    finally:
        _release_lock(lock)
    return logs
