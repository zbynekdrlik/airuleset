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

#854 (2026-09-02, live gk 97 % incident): TWO gaps closed. (1) The drain cadence
gate was applied UNCONDITIONALLY, so at CRITICAL pressure (>= DISK_CRITICAL_PCT)
the guard just LOGGED "cadence-gated" every poll for 20+ min and freed nothing —
now, at CRITICAL, the drain runs EVERY poll (the cadence gate applies only in the
80-95 % band). (2) The ladder's rungs missed what actually fills a CI/gatekeeper
box, so even an ungated drain freed ~0 — new cache-class rungs are added, each a
pure dry-run-able selector deleting ONLY provenance-proven cache-class artifacts:
apt cache, rotated ``/var/log/*.1|*.gz``, gh-runner ``_work/_update|_temp`` +
stale ``_work/<repo>`` checkouts, docker images (0 containers AND untagged OR
> 14 d — NEVER while a ``Runner.Worker`` runs, NEVER ``docker system prune``,
NEVER a tagged in-use image), per-user ``~/.cache`` > 30 d, stale Claude Code
self-update binaries (``~/.local/share/claude/versions/*`` except the running
one), and one-off numbered lint/ruff/mcp venvs (``~/.venvs/*-<pid>`` +
``/tmp/lintvenv-*`` > 2 d). The #834 "surface-not-delete for runner/docker/log"
stance is thus DELIBERATELY narrowed by #854 to these bounded cache-class cases;
each drain logs ``disk-guard: NN% -> drain rung=<name> freed=<bytes> -> MM%``.

PERMISSION NOTE (#854): the drain still runs per-USER (``euid==0`` refuses the
whole ladder). The ROOT-owned cache classes (:data:`SUDO_CLASSES` — apt cache,
rotated ``/var/log``, another user's ``/home/gh-runner`` ``_update``/``_temp``/
checkouts) delete through ``sudo -n`` when NOPASSWD sudo is present (probed ONCE
per drain via ``sudo -n true``) — gk ``gatekeeper`` and dev1/dev2 ``newlevel``
all have it, so these rungs are FULLY effective there (the live gk reclaim was
exactly ``btmp.1`` 449 M + ``auth``/``syslog`` ``.1`` 242 M + runner ``_update``
678 M). Where NOPASSWD sudo is ABSENT they fall back to the unprivileged attempt
and FAIL-safe-LOG (no data loss, no retry, never interactive) — best-effort, and
the #841 root-ssh leg remains the reclaim path there. Own-home classes
(``~/.cache``/stale-Claude-versions/one-off-venvs/scratch/worktree) and docker
(docker-group) never need sudo and are always effective.

stdlib-only at module level; every reuse of a ``cli_*`` discovery function and
of ``watchdog.reaper`` is a DEFERRED import inside the function that needs it,
so this module has no import cycle with the ``watchdog`` package that hosts it.
"""

import fcntl
import json
import math
import os
import re as _re
import socket
import subprocess
import sys
import time
from pathlib import Path

# --- thresholds (#834 req 1) ------------------------------------------------ #
NOTICE_PCT = 75            # footer NOTICE band (footer render itself narrowed to >=90% by #854)
DRAIN_PCT = 80             # AUTO-DRAIN at/above this
CRITICAL_PCT = 90          # machine-channel escalation at/above this (red footer)
DISK_CRITICAL_PCT = 95     # #854: at/above this the drain runs EVERY poll (cadence gate bypassed)
TARGET_PCT = 75            # drain stops once the worst mount is back below this
MOUNTS = ("/", "/home", "/tmp")

# The scope FENCE. The executor NEVER acts on a class outside this literal
# allowlist; a rogue planner emitting one is skipped+logged. #834 kept this to
# OUR own per-user reclaimable classes and deliberately EXCLUDED docker/runner/
# /var/log as box-wide/root ops. #854 (live gk 97 %) NARROWS that exclusion to a
# set of bounded, provenance-proven CACHE-CLASS reclaims that the guard now DOES
# act on: apt cache, rotated logs (`*.1`/`*.gz`), gh-runner `_work/_update|_temp`
# + stale `_work/<repo>` checkouts, docker images (0-containers AND untagged OR
# unused>14d — Runner.Worker-gated, never a tagged in-use image, never `docker
# system prune`), per-user `~/.cache`, stale Claude self-update binaries, and
# one-off numbered venvs. Everything else (a runner's LIVE checkout, a tagged
# in-use image, user data) stays out of the fence and is never touched.
RECLAIMABLE_CLASSES = frozenset({
    "scratch", "tmp-stray", "worktree", "cli-version",
    "uploads", "transcript", "toolchain", "journal",
    # #854 cache-class additions:
    "apt-cache", "rotated-log", "runner-update", "docker-image",
    "runner-checkout", "user-cache", "claude-version", "oneoff-venv",
    # #862 — superseded gh-runner bin.<ver>/externals.<ver> version dirs:
    "runner-superseded",
})

# #854 — the ROOT-owned cache classes: their deletes go through `sudo -n` when
# NOPASSWD sudo is available (gk `gatekeeper`, dev1/dev2 `newlevel` all have it),
# else the unprivileged attempt (fail-safe, no retry). docker (docker-group) and
# own-home classes never need sudo, so are deliberately ABSENT here.
SUDO_CLASSES = frozenset({"apt-cache", "rotated-log", "runner-update", "runner-checkout",
                          # #862 — gh-runner home is a FOREIGN user; the delete of a
                          # superseded version dir goes through `sudo -n rm`.
                          "runner-superseded"})

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

# #854 cache-class rung parameters (all provenance-proven cache; never user data).
APT_ARCHIVES_DIR = "/var/cache/apt/archives"
VARLOG_DIR = "/var/log"
ROTATED_LOG_MIN_AGE_DAYS = 1                # `*.1`/`*.gz` older than this
GH_RUNNER_HOME = "/home/gh-runner"          # the CI runner user's home (box-level)
RUNNER_WORK_RESERVED = frozenset({          # `_work/*` entries that are NOT repo checkouts
    "_actions", "_temp", "_tool", "_update", "_diag", "_PipelineMapping",
})
RUNNER_CHECKOUT_MIN_AGE_DAYS = 7
RUNNER_WORKER_PROC_RE = "Runner.Worker"     # a live CI job — gates docker + checkout rungs
# #862 — the superseded-version rung: exe basenames of the two runner processes.
# The Worker basename IS the same literal as the pgrep RE above — alias it so
# there is ONE source string, never a silently-drifting duplicate (🔵8).
RUNNER_LISTENER_BASENAME = "Runner.Listener"
RUNNER_WORKER_BASENAME = RUNNER_WORKER_PROC_RE
RUNNER_VERSION_PREFIXES = ("bin", "externals")
# a version dir suffix is STRICTLY `<n>(.<n>){1,3}` (e.g. `2.337.0`); anything
# else (`bin.bak`, `externals.old`, `bin.2.336.0.bak`) is never a version → never
# a delete candidate (🟡3).
RUNNER_VERSION_SUFFIX_RE = _re.compile(r"^\d+(\.\d+){1,3}$")
PROC_ERROR_SENTINEL = "PROC-ERROR"          # fail-safe: unknown proc state → treat as live
DOCKER_UNUSED_MIN_AGE_DAYS = 14
USER_CACHE_MIN_AGE_DAYS = 30
CLAUDE_VERSIONS_DIR = ".local/share/claude/versions"   # under $HOME; self-update binaries
ONEOFF_VENV_MIN_AGE_DAYS = 2
# one-off numbered venvs: `~/.venvs/lint-3907`, `ruff31522`, `mcp-4574`, … + `/tmp/lintvenv-*`
ONEOFF_VENV_NAME_RE = _re.compile(r"^(lint|ruff|mcp)[-]?\d+$")
ONEOFF_VENV_TMP_RE = _re.compile(r"^lintvenv-")


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
# NEW cache-class planners (#854) — each a PURE selector; tests inject fakes /
# fake trees, so a `run_disk_guard --dry-run` NEVER deletes and a unit test
# NEVER touches a real system path.
# --------------------------------------------------------------------------- #
def _default_pgrep_any(pattern):
    """`pgrep -f <pattern>` across ALL users (the CI runner runs as a DIFFERENT
    user than this guard). Returns matching output; on any error returns the
    fail-safe sentinel so an unknown state is treated as "process live" → skip."""
    try:
        r = subprocess.run(["pgrep", "-f", pattern],
                           capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except Exception as e:
        _dbg("pgrep -f failed: %r" % e)
        return "PGREP-ERROR"          # fail-safe: unknown → treat as live → skip


def discover_apt_cache(archives_dir=APT_ARCHIVES_DIR, dir_stats_fn=None):
    """The `.deb` download cache under /var/cache/apt/archives — pure cache
    (apt re-downloads on demand). Returns ONE `apt-clean` action when the dir
    exists and holds > 0 bytes, else nothing. No dir at all is nothing to do."""
    if not os.path.isdir(archives_dir):
        return []
    size = _safe_dir_size(archives_dir, dir_stats_fn)
    if size <= 0:
        return []
    return [{"cls": "apt-cache", "path": "apt-get clean", "bytes": size,
             "kind": "apt-clean", "reason": None}]


def discover_rotated_logs(varlog_dir=VARLOG_DIR, now=None,
                          min_age_days=ROTATED_LOG_MIN_AGE_DAYS):
    """ROTATED logs under /var/log — `*.1`/`*.N` numbered rotations and `*.gz`
    compressed rotations older than `min_age_days`. NEVER a LIVE log (a name
    with no numeric/`.gz` rotation suffix), so `syslog`/`auth.log`/`btmp` itself
    are never touched — only `btmp.1`, `auth.log.1`, `syslog.1`, `*.gz`. Walks
    the top level + immediate subdirs (rotations live there); the `journal`
    subdir is skipped (journald vacuum owns it). Rows delete/skip."""
    now = time.time() if now is None else now
    d = Path(varlog_dir)
    if not d.is_dir():
        return []
    cutoff = min_age_days * 86400
    out = []

    def _consider(fp):
        name = os.path.basename(fp)
        rotated = name.endswith(".gz") or bool(_re.search(r"\.\d+$", name))
        if not rotated:
            return
        try:
            st = os.lstat(fp)
        except OSError as e:
            out.append({"cls": "rotated-log", "path": fp, "bytes": 0,
                        "reason": "could not stat: %s" % e})
            return
        if os.path.islink(fp):
            out.append({"cls": "rotated-log", "path": fp, "bytes": st.st_size,
                        "reason": "symlink — never followed"})
            return
        age = now - st.st_mtime
        row = {"cls": "rotated-log", "path": fp, "bytes": st.st_size, "reason": None}
        if age < cutoff:
            row["reason"] = "too recent (%.1fd < %dd)" % (age / 86400.0, min_age_days)
        out.append(row)

    try:
        for entry in d.iterdir():
            if entry.name == "journal":
                continue
            if entry.is_file() and not entry.is_symlink():
                _consider(str(entry))
            elif entry.is_dir() and not entry.is_symlink():
                # one unreadable subdir (e.g. a root-only /var/log/chrony) must
                # NOT abort the whole rung — log it and keep going.
                try:
                    for sub in entry.iterdir():
                        if sub.is_file() and not sub.is_symlink():
                            _consider(str(sub))
                except OSError as e:
                    out.append({"cls": "rotated-log", "path": str(entry), "bytes": 0,
                                "reason": "could not walk subdir: %s" % e})
    except OSError as e:
        return [{"cls": "rotated-log", "path": None,
                 "reason": "could not walk %s: %s" % (varlog_dir, e)}]
    return out


def discover_runner_update(runner_root=GH_RUNNER_HOME, pgrep_fn=None, dir_stats_fn=None):
    """gh-runner `_work/_update` (self-update staging) + `_work/_temp` leftovers.
    `_update` is safe to delete anytime (the runner re-stages it). BUT `_temp` is
    the LIVE job's `$RUNNER_TEMP` (step temp files / `_runner_file_commands`) —
    deleting it mid-job corrupts an in-flight CI run (review 🟡), so `_temp` is
    Runner.Worker-gated (kept while any worker is live) exactly like the checkout
    rung. No runner root / no such dirs = nothing to do."""
    root = Path(runner_root)
    if not root.is_dir():
        return []
    pgrep_fn = pgrep_fn or _default_pgrep_any
    try:
        live = pgrep_fn(RUNNER_WORKER_PROC_RE) or ""
    except Exception as e:
        _dbg("runner-update pgrep failed: %r" % e)
        live = "PGREP-ERROR"
    worker_live = bool(live.strip())
    out = []
    try:
        for rd in sorted(root.glob("actions-runner*")):
            p_update = rd / "_work" / "_update"
            if p_update.is_dir() and not p_update.is_symlink():
                out.append({"cls": "runner-update", "path": str(p_update),
                            "bytes": _safe_dir_size(str(p_update), dir_stats_fn),
                            "kind": "delete", "reason": None})
            p_temp = rd / "_work" / "_temp"
            if p_temp.is_dir() and not p_temp.is_symlink():
                if worker_live:
                    out.append({"cls": "runner-update", "path": str(p_temp), "bytes": 0,
                                "kind": "skip",
                                "reason": "Runner.Worker live — _temp is $RUNNER_TEMP, kept"})
                else:
                    out.append({"cls": "runner-update", "path": str(p_temp),
                                "bytes": _safe_dir_size(str(p_temp), dir_stats_fn),
                                "kind": "delete", "reason": None})
    except OSError as e:
        return [{"cls": "runner-update", "path": None,
                 "reason": "could not walk %s: %s" % (runner_root, e)}]
    return out


def discover_stale_runner_checkouts(runner_root=GH_RUNNER_HOME, now=None,
                                    min_age_days=RUNNER_CHECKOUT_MIN_AGE_DAYS,
                                    pgrep_fn=None, dir_stats_fn=None):
    """Stale `_work/<repo>` checkouts under each `actions-runner*` dir, older
    than `min_age_days`. SKIPPED ENTIRELY when ANY `Runner.Worker` process is
    live (a CI job may be using a checkout — never race it). Reserved runner
    dirs (`_actions`/`_temp`/`_tool`/`_update`/`_diag`/`_PipelineMapping`) are
    NEVER checkouts. Rows delete/skip."""
    now = time.time() if now is None else now
    root = Path(runner_root)
    if not root.is_dir():
        return []
    pgrep_fn = pgrep_fn or _default_pgrep_any
    try:
        live = pgrep_fn(RUNNER_WORKER_PROC_RE) or ""
    except Exception as e:
        _dbg("runner-checkout pgrep failed: %r" % e)
        live = "PGREP-ERROR"
    if live.strip():
        return [{"cls": "runner-checkout", "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "Runner.Worker live — CI job may hold a checkout, kept"}]
    cutoff = min_age_days * 86400
    out = []
    try:
        for rd in sorted(root.glob("actions-runner*")):
            work = rd / "_work"
            if not work.is_dir():
                continue
            for entry in work.iterdir():
                if entry.name in RUNNER_WORK_RESERVED:
                    continue
                if not entry.is_dir() or entry.is_symlink():
                    continue
                try:
                    age = now - entry.stat().st_mtime
                except OSError as e:
                    out.append({"cls": "runner-checkout", "path": str(entry),
                                "bytes": 0, "reason": "could not stat: %s" % e})
                    continue
                row = {"cls": "runner-checkout", "path": str(entry),
                       "bytes": _safe_dir_size(str(entry), dir_stats_fn),
                       "reason": None}
                if age < cutoff:
                    row["reason"] = "checkout too recent (%.1fd < %dd)" % (age / 86400.0, min_age_days)
                out.append(row)
    except OSError as e:
        return [{"cls": "runner-checkout", "path": None,
                 "reason": "could not walk %s: %s" % (runner_root, e)}]
    return out


# --------------------------------------------------------------------------- #
# #862 — runner-superseded: `bin.<ver>`/`externals.<ver>` version dirs a gh-runner
# self-update left behind. Each install keeps the CURRENT version (the `bin`/
# `externals` symlink target) beside the superseded one (~600 MB each); no rung
# reclaimed them, so a 95 % gk drain freed 0 B. Delete the superseded dirs, NEVER
# the symlink target nor the version a live `Runner.Listener` is executing (it may
# still hold the OLD inode mid-transition), and skip the whole root while a
# `Runner.Worker` of that root is live (a self-update runs through `_work/_update`).
# --------------------------------------------------------------------------- #
def _runner_version_from_basename(basename, prefix):
    """`bin.2.337.0` with prefix `bin` → `2.337.0`; None when `basename` is the
    bare `prefix` (the symlink) or does not carry a `<prefix>.<ver>` shape."""
    marker = prefix + "."
    if basename == prefix or not basename.startswith(marker):
        return None
    suffix = basename[len(marker):]
    if not RUNNER_VERSION_SUFFIX_RE.match(suffix):
        return None                          # not a `<n>(.<n>){1,3}` version (🟡3)
    return suffix


def _resolve_symlink_version(link, prefix):
    """The `<ver>` a `<root>/bin` or `<root>/externals` SYMLINK resolves to (its
    real target's basename), or None when the path is not a symlink or is
    unreadable — the None caller treats as a fail-safe KEEP-everything signal."""
    try:
        if not link.is_symlink():
            return None
        real = os.path.realpath(str(link))
        if not os.path.isdir(real):
            return None                      # dangling / mid-swap target (🟡5) → KEEP-all
        target = os.path.basename(real)
    except OSError as e:
        _dbg("runner-superseded symlink realpath failed: %r" % e)
        return None
    return _runner_version_from_basename(target, prefix)


def _runner_version_tuple(ver):
    """`(2, 337, 0)` from `'2.337.0'`; None when any segment is non-numeric — the
    None caller fails safe (KEEPs), so an unparseable version is never proven
    superseded (🔴1/🟡6)."""
    if not ver:
        return None
    try:
        return tuple(int(p) for p in ver.split("."))
    except ValueError:
        return None


def _runner_version_tuple_ge(vt, ct):
    """`vt >= ct` with BOTH tuples zero-padded to equal length first -- `bin.2.337`
    (segment count 2) vs a current `2.337.0` (segment count 3) must compare EQUAL,
    never as an ordering artifact of differing segment counts (#862 fix 4).
    Plain Python tuple comparison treats a shorter tuple as LESS than a longer one
    that shares its leading segments even when the missing segments are all `0`
    (`(2, 337) < (2, 337, 0)`), which would wrongly let a same-version staged dir
    fall through as a delete candidate instead of a KEEP."""
    n = max(len(vt), len(ct))
    return (vt + (0,) * (n - len(vt))) >= (ct + (0,) * (n - len(ct)))


def _runner_version_from_scoped_exe(exe, prefixes):
    """The runner `<ver>` from the FIRST path segment BELOW the root — anchored to
    a `prefixes` boundary so a version-shaped segment ELSEWHERE in the path (e.g. a
    `/opt/bin.9.9.9/` install root) never matches (🔵9). None when the first
    below-root segment is not a `<prefix>.<ver>` dir."""
    for pfx in prefixes:
        if exe.startswith(pfx):
            seg = exe[len(pfx):].split("/", 1)[0]
            for pn in RUNNER_VERSION_PREFIXES:
                v = _runner_version_from_basename(seg, pn)
                if v is not None:
                    return v
            return None
    return None


def _readlink_proc_exe(pid, run_fn=None):
    """Resolve `/proc/<pid>/exe` (which resolves the `bin` symlink to the REAL
    versioned dir the process is executing). Owner/root-only, so a same-user
    readlink is tried first, then `sudo -n readlink` (gk `gatekeeper` / dev
    `newlevel` have NOPASSWD sudo) for a cross-user gh-runner process. None on
    total failure."""
    try:
        return os.readlink("/proc/%s/exe" % pid)
    except OSError as e:
        # EXPECTED for a foreign-user process (readlink of another user's
        # /proc/<pid>/exe is owner/root-only) — fall through to `sudo readlink`.
        _dbg("runner-superseded readlink /proc/%s/exe unprivileged failed: %r" % (pid, e))
    run_fn = run_fn or subprocess.run
    try:
        r = run_fn(["sudo", "-n", "readlink", "-f", "/proc/%s/exe" % pid],
                   capture_output=True, text=True, timeout=10)
        if getattr(r, "returncode", 0) != 0:
            _dbg("runner-superseded sudo readlink rc=%r for pid %s"
                 % (getattr(r, "returncode", None), pid))
            return None                      # 🔴2: an unresolvable exe fails safe
        out = (r.stdout or "").strip()
        return out or None
    except Exception as e:
        _dbg("runner-superseded sudo readlink failed for pid %s: %r" % (pid, e))
        return None


def _default_runner_proc_exes(pgrep_fn=None, run_fn=None):
    """Resolved exe paths of every live `Runner.Listener`/`Runner.Worker`
    process. `pgrep -f` finds the PIDs cross-user (cmdline is world-readable at
    hidepid=0); `/proc/<pid>/exe` gives the real versioned dir the process runs.
    Any pgrep error → the sentinel list `[PROC_ERROR_SENTINEL]` so an unknown
    state fails safe (treated as live → KEEP)."""
    pgrep_fn = pgrep_fn or _default_pgrep_any
    pids = set()
    for pat in (RUNNER_LISTENER_BASENAME, RUNNER_WORKER_BASENAME):
        try:
            txt = pgrep_fn(pat)
        except Exception as e:
            _dbg("runner-superseded pgrep failed: %r" % e)
            return [PROC_ERROR_SENTINEL]
        if txt == "PGREP-ERROR":
            return [PROC_ERROR_SENTINEL]
        for tok in (txt or "").split():
            if tok.strip().isdigit():
                pids.add(tok.strip())
    exes = []
    for pid in pids:
        exe = _readlink_proc_exe(pid, run_fn)
        # 🔴2 FAIL-OPEN fix: a pgrep'd runner pid whose exe cannot be resolved
        # (no NOPASSWD sudo / hidepid), or whose basename is not one of the two
        # runner processes (e.g. a `... (deleted)` exe mid-self-update), makes the
        # WHOLE scan uncertain → the sentinel (rung KEEPs), never a silent drop
        # that would empty `exes` and let the rung delete a live version.
        if exe is None or os.path.basename(exe) not in (
                RUNNER_LISTENER_BASENAME, RUNNER_WORKER_BASENAME):
            _dbg("runner-superseded pid %s exe unresolvable/non-runner: %r" % (pid, exe))
            return [PROC_ERROR_SENTINEL]
        exes.append(exe)
    return exes


def _default_update_sh_argv():
    """Full argv lines of every live gh-runner `update.sh` process
    (`pgrep -af update.sh`, ALL users). Used ONLY to scope a self-update-in-flight
    KEEP to the right root (🔴1). Any error → `[PROC_ERROR_SENTINEL]` so an unknown
    proc state fails safe (treated as in-flight → skip)."""
    try:
        r = subprocess.run(["pgrep", "-af", r"update\.sh"],
                           capture_output=True, text=True, timeout=10)
        return [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    except Exception as e:
        _dbg("runner-superseded update.sh pgrep -af failed: %r" % e)
        return [PROC_ERROR_SENTINEL]


def _runner_update_in_flight(rd, prefixes, update_argv):
    """True while a gh-runner self-update is mid-flight for THIS root: the
    `<root>/_work/_update` staging dir exists, or a live `update.sh` process is
    scoped under this root. Fail-safe True on any uncertainty (🔴 1).

    `prefixes` are the caller's ROOT boundaries (realpath + raw root, each with
    a TRAILING slash, mirroring `_plan_superseded_for_root`'s own `_under_root`
    scoping) -- matched AS-IS, never with the trailing slash stripped. Stripping
    it turned the match into an unanchored substring test, so a live
    `update.sh` under `actions-runner-2` also matched the shorter
    `actions-runner` root; keeping the trailing slash anchors the match to only
    that root and its own children (#862 fix 1)."""
    try:
        if (rd / "_work" / "_update").exists():
            return True
    except OSError as e:
        _dbg("runner-superseded _work/_update check failed: %r" % e)
        return True
    if PROC_ERROR_SENTINEL in (update_argv or []):
        return True
    for line in (update_argv or []):
        if any(p and p in (line or "") for p in (prefixes or ())):
            return True
    return False


def _sum_superseded_candidate_bytes(rd, dir_stats_fn=None):
    """Summed size of the real `bin.<ver>`/`externals.<ver>` dirs under `rd` — the
    bytes a whole-root fail-safe skip is RETAINING, so a dry-run reports what was
    held back rather than a bare 0 (🔵10)."""
    total = 0
    for pn in RUNNER_VERSION_PREFIXES:
        try:
            entries = sorted(rd.glob(pn + ".*"))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
            except OSError:
                continue
            if _runner_version_from_basename(entry.name, pn) is None:
                continue
            total += _safe_dir_size(str(entry), dir_stats_fn)
    return total


def _runner_superseded_safe_to_delete(path):
    """Action-time re-verify (🟡6): resolve the parent root's bin/externals symlink
    AGAIN right before deletion and REFUSE (False) if `path` is now the current
    symlink target OR a staged version >= it. Fail-safe False (refuse) on ANY
    uncertainty."""
    try:
        clean = path.rstrip("/")
        parent = os.path.dirname(clean)
        base = os.path.basename(clean)
    except Exception:
        return False
    prefix_name = None
    for pn in RUNNER_VERSION_PREFIXES:
        if base.startswith(pn + "."):
            prefix_name = pn
            break
    if prefix_name is None:
        return False
    vt = _runner_version_tuple(_runner_version_from_basename(base, prefix_name))
    if vt is None:
        return False
    cur = _resolve_symlink_version(Path(parent) / prefix_name, prefix_name)
    ct = _runner_version_tuple(cur) if cur else None
    if ct is None:
        return False
    if _runner_version_tuple_ge(vt, ct):     # now the current target, or staged newer
        return False
    return True


def _plan_superseded_for_root(rd, exes, dir_stats_fn=None, update_argv=None):
    """Rows for ONE `actions-runner*` root. KEEP = the two symlink target versions
    + any live-Listener running version of this root + any STAGED version >= the
    current one (a self-update the runner is about to switch to, 🔴1); everything
    else (`bin.<ver>`/`externals.<ver>` real dirs) is a delete candidate. Fail-safe
    KEEP-everything (one skip row carrying the summed candidate bytes) on any
    uncertainty."""
    root_str = str(rd)
    # exe paths from /proc/<pid>/exe are realpath-resolved; scope by BOTH the
    # realpath AND the raw root path (trailing slash → no actions-runner vs -2
    # collision) so a Listener/Worker under a SYMLINKED root still matches (🟡4).
    prefixes = tuple({os.path.realpath(root_str).rstrip("/") + "/",
                      root_str.rstrip("/") + "/"})

    def _under_root(exe, basename):
        return any(exe.startswith(p) for p in prefixes) and os.path.basename(exe) == basename

    # Memoized single-slot cache: `_sum_superseded_candidate_bytes` walks every
    # `bin.<ver>`/`externals.<ver>` dir with `du` (#862 fix 3) -- at CRITICAL
    # pressure the drain ladder can re-plan this root every 60s poll while a
    # self-update/live-Worker skip persists, so compute the candidate bytes
    # ONCE per discovery call and reuse them for the skip row instead of
    # re-walking the same directories on every `_skip_root` invocation.
    _candidate_bytes_cache = []

    def _skip_root(reason):
        if not _candidate_bytes_cache:
            _candidate_bytes_cache.append(_sum_superseded_candidate_bytes(rd, dir_stats_fn))
        return [{"cls": "runner-superseded", "path": root_str,
                 "bytes": _candidate_bytes_cache[0],
                 "kind": "skip", "reason": reason}]

    # (1) a live Runner.Worker of this root (or a failed proc scan) → skip whole root
    if PROC_ERROR_SENTINEL in exes or any(_under_root(e, RUNNER_WORKER_BASENAME) for e in exes):
        return _skip_root("Runner.Worker live (or proc scan failed) for this root — "
                          "self-update may be in flight, kept (fail-safe)")

    # (1b) a self-update in flight (a `_work/_update` staging dir or a live
    # `update.sh` of this root) → skip whole root, never race the swap (🔴1)
    if _runner_update_in_flight(rd, prefixes, update_argv):
        return _skip_root("self-update in flight (_work/_update or live update.sh) — "
                          "kept (fail-safe)")

    # (2) resolve the current version of each bin/externals symlink
    keep = set()
    cur_by_prefix = {}
    for prefix_name in RUNNER_VERSION_PREFIXES:
        ver = _resolve_symlink_version(rd / prefix_name, prefix_name)
        if ver is None:
            return _skip_root("%s symlink missing/unreadable/not-a-symlink — "
                              "kept (fail-safe)" % prefix_name)
        vt = _runner_version_tuple(ver)
        if vt is None:
            return _skip_root("%s current version %s unparseable — kept (fail-safe)"
                              % (prefix_name, ver))
        cur_by_prefix[prefix_name] = (ver, vt)
        keep.add(ver)

    # (3) protect the version a live Listener of this root is actually executing
    for e in exes:
        if not _under_root(e, RUNNER_LISTENER_BASENAME):
            continue
        lver = _runner_version_from_scoped_exe(e, prefixes)
        if lver is None:
            return _skip_root("Runner.Listener live but its version is unresolvable — "
                              "kept (fail-safe)")
        keep.add(lver)

    # (4) select superseded version dirs (real dirs only; never the bare symlink,
    # never a non-version suffix, never a STAGED version >= current)
    out = []
    for prefix_name in RUNNER_VERSION_PREFIXES:
        try:
            entries = sorted(rd.glob(prefix_name + ".*"))
        except OSError as e:
            out.append({"cls": "runner-superseded", "path": None,
                        "reason": "could not glob %s.* in %s: %s" % (prefix_name, root_str, e)})
            continue
        _cur_ver, cur_tuple = cur_by_prefix[prefix_name]
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            ver = _runner_version_from_basename(entry.name, prefix_name)
            if ver is None:
                continue                     # non-version suffix → never a candidate (🟡3)
            size = _safe_dir_size(str(entry), dir_stats_fn)
            vt = _runner_version_tuple(ver)
            if vt is None:
                out.append({"cls": "runner-superseded", "path": str(entry), "bytes": size,
                            "kind": "skip", "reason": "version %s unparseable — kept" % ver})
            elif ver in keep:
                out.append({"cls": "runner-superseded", "path": str(entry), "bytes": size,
                            "kind": "skip", "reason": "current/live version %s — kept" % ver})
            elif _runner_version_tuple_ge(vt, cur_tuple):
                out.append({"cls": "runner-superseded", "path": str(entry), "bytes": size,
                            "kind": "skip",
                            "reason": "staged version %s >= current — self-update pending, kept" % ver})
            else:
                out.append({"cls": "runner-superseded", "path": str(entry),
                            "bytes": size, "kind": "delete", "reason": None})
    return out


def discover_superseded_runner_versions(runner_root=GH_RUNNER_HOME,
                                        proc_exes_fn=None, dir_stats_fn=None,
                                        update_argv_fn=None):
    """Superseded `bin.<ver>`/`externals.<ver>` dirs under each
    `<runner_root>/actions-runner*` install (same discovery as `runner-update`,
    no hardcoded names). `proc_exes_fn` returns the resolved exe paths of live
    Runner.Listener/Runner.Worker processes (default: `_default_runner_proc_exes`;
    the sentinel `PROC_ERROR_SENTINEL` in its list means the scan failed → every
    root fail-safe KEEPs). `update_argv_fn` returns the argv lines of live
    `update.sh` processes (default: `_default_update_sh_argv`) used to KEEP a root
    whose self-update is in flight (🔴1). Rows delete/skip; see
    `_plan_superseded_for_root`."""
    root = Path(runner_root)
    if not root.is_dir():
        return []
    proc_exes_fn = proc_exes_fn or _default_runner_proc_exes
    try:
        exes = list(proc_exes_fn() or [])
    except Exception as e:
        _dbg("runner-superseded proc_exes_fn failed: %r" % e)
        exes = [PROC_ERROR_SENTINEL]
    update_argv_fn = update_argv_fn or _default_update_sh_argv
    try:
        update_argv = list(update_argv_fn() or [])
    except Exception as e:
        _dbg("runner-superseded update_argv_fn failed: %r" % e)
        update_argv = [PROC_ERROR_SENTINEL]
    try:
        runner_dirs = sorted(root.glob("actions-runner*"))
    except OSError as e:
        return [{"cls": "runner-superseded", "path": None,
                 "reason": "could not walk %s: %s" % (runner_root, e)}]
    out = []
    for rd in runner_dirs:
        if rd.is_dir():
            out.extend(_plan_superseded_for_root(rd, exes, dir_stats_fn, update_argv))
    return out


def _default_docker_images():
    """Parse `docker images` → [{id, repo, tag, size, created_ts}]. Any error
    (docker absent / daemon down) returns None so the planner does nothing."""
    try:
        r = subprocess.run(
            ["docker", "images", "--no-trunc",
             "--format", "{{.ID}}\t{{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
    except Exception as e:
        _dbg("docker images failed: %r" % e)
        return None
    out = []
    for ln in (r.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 5:
            continue
        out.append({"id": parts[0].strip(), "repo": parts[1].strip(),
                    "tag": parts[2].strip(), "size": _parse_docker_size(parts[3]),
                    "created_ts": _parse_docker_created(parts[4])})
    return out


def _default_docker_ps():
    """Set of image refs (name and/or id) currently used by ANY container."""
    try:
        r = subprocess.run(["docker", "ps", "-a", "--no-trunc", "--format", "{{.Image}}"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return set()
        return {x.strip() for x in (r.stdout or "").splitlines() if x.strip()}
    except Exception as e:
        _dbg("docker ps failed: %r" % e)
        return None          # unknown → fail-safe: caller treats as "cannot prove unused"


def _parse_docker_size(s):
    s = (s or "").strip().upper()
    m = _re.match(r"^([\d.]+)\s*([KMGT]?)B?$", s)
    if not m:
        return 0
    val = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}.get(m.group(2), 1)
    return int(val * mult)


def _parse_docker_created(s):
    """`docker`'s CreatedAt `2026-06-30 16:50:03 +0200 CEST` → epoch, or None."""
    s = (s or "").strip()
    m = _re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*([+-]\d{4})?", s)
    if not m:
        return None
    try:
        import calendar
        t = time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        epoch = calendar.timegm(t)          # treat the wall-clock as UTC-ish
        if m.group(2):
            sign = 1 if m.group(2)[0] == "+" else -1
            off = int(m.group(2)[1:3]) * 3600 + int(m.group(2)[3:5]) * 60
            epoch -= sign * off
        return epoch
    except Exception as e:
        _dbg("docker created parse failed: %r" % e)
        return None


def _image_in_use(img, in_use_refs):
    # `docker images --no-trunc` yields `sha256:<hex>` ids; `docker ps` shows a
    # bare 12-hex id OR a repo:tag. Strip the `sha256:` prefix on BOTH sides
    # before comparing (review 🟡: an id-referenced in-use image was mis-read as
    # unused — docker's own `rmi` refusal saved it, but the plan was wrong).
    fullid = (img.get("id") or "").replace("sha256:", "")
    shortid = fullid[:12] if fullid else ""
    reftag = None
    if img.get("repo") and img.get("repo") != "<none>" and img.get("tag") and img.get("tag") != "<none>":
        reftag = "%s:%s" % (img["repo"], img["tag"])
    for ref in in_use_refs:
        if not ref:
            continue
        r = ref.replace("sha256:", "")
        if r == fullid or (shortid and r == shortid) or (reftag and ref == reftag):
            return True
        if shortid and (r.startswith(shortid) or shortid.startswith(r)):
            return True
    return False


def discover_docker_images(now=None, images_fn=None, ps_fn=None, pgrep_fn=None,
                           min_unused_days=DOCKER_UNUSED_MIN_AGE_DAYS):
    """Docker images SAFE to reclaim: no container references them AND
    (untagged `<none>` OR created > `min_unused_days` ago). SKIPPED ENTIRELY
    while any `Runner.Worker` process runs (a CI job may pull/build). A TAGGED,
    recent, referenced or in-use image is NEVER selected; never `docker system
    prune`. Docker absent / ps unknown → does nothing (fail-safe KEEP). Rows
    `docker-rmi`/skip; `path` is the image id `docker rmi` acts on."""
    now = time.time() if now is None else now
    pgrep_fn = pgrep_fn or _default_pgrep_any
    try:
        live = pgrep_fn(RUNNER_WORKER_PROC_RE) or ""
    except Exception as e:
        _dbg("docker rung pgrep failed: %r" % e)
        live = "PGREP-ERROR"
    if live.strip():
        return [{"cls": "docker-image", "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "Runner.Worker live — CI may pull/build, docker rung skipped"}]
    images = (images_fn or _default_docker_images)()
    if not images:
        return []                       # docker absent / no images → nothing
    in_use = (ps_fn or _default_docker_ps)()
    if in_use is None:
        return [{"cls": "docker-image", "path": "-", "bytes": 0, "kind": "skip",
                 "reason": "docker ps unreadable — cannot prove unused, kept"}]
    cutoff = min_unused_days * 86400
    out = []
    for img in images:
        if _image_in_use(img, in_use):
            out.append({"cls": "docker-image", "path": img.get("id") or "-",
                        "bytes": img.get("size") or 0, "kind": "skip",
                        "reason": "referenced by a container — kept"})
            continue
        untagged = img.get("repo") == "<none>" or img.get("tag") == "<none>"
        created = img.get("created_ts")
        old = (created is not None) and ((now - created) > cutoff)
        if untagged or old:
            why = "untagged <none>" if untagged else "unused > %dd" % min_unused_days
            out.append({"cls": "docker-image", "path": img.get("id") or "-",
                        "bytes": img.get("size") or 0, "kind": "docker-rmi",
                        "reason": None, "why": why})
        else:
            out.append({"cls": "docker-image", "path": img.get("id") or "-",
                        "bytes": img.get("size") or 0, "kind": "skip",
                        "reason": "tagged + recent (0 containers) — kept"})
    return out


def discover_stale_user_cache(home=None, now=None,
                              min_age_days=USER_CACHE_MIN_AGE_DAYS, dir_stats_fn=None):
    """Own `~/.cache` TOP-LEVEL entries not modified in `min_age_days` (pip /
    npm / playwright caches — regenerated on demand). Uses mtime (atime is
    unreliable on `noatime` mounts). Rows delete/skip. No `~/.cache` = nothing."""
    now = time.time() if now is None else now
    cache = Path(home or os.path.expanduser("~")) / ".cache"
    if not cache.is_dir():
        return []
    cutoff = min_age_days * 86400
    out = []
    try:
        for entry in cache.iterdir():
            if entry.is_symlink():
                out.append({"cls": "user-cache", "path": str(entry), "bytes": 0,
                            "reason": "symlink — never followed"})
                continue
            try:
                age = now - entry.stat().st_mtime
            except OSError as e:
                out.append({"cls": "user-cache", "path": str(entry), "bytes": 0,
                            "reason": "could not stat: %s" % e})
                continue
            row = {"cls": "user-cache", "path": str(entry),
                   "bytes": _safe_dir_size(str(entry), dir_stats_fn) if entry.is_dir()
                   else (entry.stat().st_size if entry.exists() else 0),
                   "reason": None}
            if age < cutoff:
                row["reason"] = "too recent (%.1fd < %dd)" % (age / 86400.0, min_age_days)
            out.append(row)
    except OSError as e:
        return [{"cls": "user-cache", "path": None,
                 "reason": "could not walk %s: %s" % (cache, e)}]
    return out


def _default_running_claude_version():
    """The Claude Code version currently in use — resolve via `claude --version`
    (e.g. `2.1.258 (Claude Code)` → `2.1.258`). None on any failure → the guard
    then KEEPS every version dir (fail-safe: never delete the active binary)."""
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10)
        m = _re.search(r"(\d+\.\d+\.\d+)", r.stdout or "")
        return m.group(1) if m else None
    except Exception as e:
        _dbg("claude --version failed: %r" % e)
        return None


def discover_stale_claude_versions(home=None, running_fn=None, dir_stats_fn=None,
                                   versions_dir=None):
    """Old Claude Code self-update binaries under `~/.local/share/claude/
    versions/*` — each a full CC install left behind after a self-update. Delete
    every version dir EXCEPT the currently-running one. FAIL-SAFE: when the
    running version cannot be resolved, KEEP EVERYTHING (never delete the active
    binary). A dir whose name matches the running version is always skipped; the
    `versions/` symlink target (if the layout uses one) is also never removed.
    Rows delete/skip."""
    home = home or os.path.expanduser("~")
    vdir = Path(versions_dir) if versions_dir else (Path(home) / CLAUDE_VERSIONS_DIR)
    if not vdir.is_dir():
        return []
    running = (running_fn or _default_running_claude_version)()
    out = []
    # resolve a `current`/`latest` symlink target so it is never removed
    protected = set()
    if running:
        protected.add(running)
    try:
        for entry in vdir.iterdir():
            if entry.is_symlink():
                out.append({"cls": "claude-version", "path": str(entry), "bytes": 0,
                            "reason": "symlink (active pointer) — never followed"})
                try:
                    protected.add(os.path.basename(os.path.realpath(str(entry))))
                except OSError as e:
                    _dbg("claude-version symlink realpath failed: %r" % e)
    except OSError as e:
        return [{"cls": "claude-version", "path": None,
                 "reason": "could not walk %s: %s" % (vdir, e)}]
    if running is None:
        # cannot prove which is active → keep everything (fail-safe)
        try:
            for entry in vdir.iterdir():
                if entry.is_symlink():
                    continue
                out.append({"cls": "claude-version", "path": str(entry), "bytes": 0,
                            "kind": "skip",
                            "reason": "running version unknown — kept (fail-safe)"})
        except OSError as e:
            _dbg("claude-version fail-safe walk failed: %r" % e)
        return out
    try:
        version_dirs = [e for e in vdir.iterdir() if e.is_dir() and not e.is_symlink()]
    except OSError as e:
        return [{"cls": "claude-version", "path": None,
                 "reason": "could not walk %s: %s" % (vdir, e)}]
    # FAIL-SAFE: if the resolved running version matches NO actual version dir
    # (and no symlink protected one), we cannot identify the active binary →
    # KEEP EVERYTHING rather than risk deleting the running install (review 🔵).
    if not (protected & {e.name for e in version_dirs}):
        for entry in version_dirs:
            out.append({"cls": "claude-version", "path": str(entry), "bytes": 0,
                        "kind": "skip",
                        "reason": "running version %r matches no version dir — kept (fail-safe)"
                        % running})
        return out
    for entry in version_dirs:
        if entry.name in protected:
            out.append({"cls": "claude-version", "path": str(entry), "bytes": 0,
                        "kind": "skip", "reason": "running version %s — kept" % entry.name})
            continue
        out.append({"cls": "claude-version", "path": str(entry),
                    "bytes": _safe_dir_size(str(entry), dir_stats_fn), "kind": "delete",
                    "reason": None})
    return out


def discover_oneoff_venvs(home=None, now=None, min_age_days=ONEOFF_VENV_MIN_AGE_DAYS,
                          tmp_dir="/tmp", dir_stats_fn=None):
    """One-off numbered lint/ruff/mcp venvs left behind by ad-hoc tooling:
    `~/.venvs/{lint,ruff,mcp}-<pid>` (and `lint4197`/`ruff31522` — optional
    hyphen) plus `/tmp/lintvenv-*`, older than `min_age_days`. A STABLE venv
    name (e.g. `airuleset-lint`, `lint` with no digits) is NEVER matched — only
    the numbered per-run ones. Rows delete/skip."""
    now = time.time() if now is None else now
    cutoff = min_age_days * 86400
    out = []

    def _consider(cls, path):
        try:
            st = os.lstat(path)
        except OSError as e:
            out.append({"cls": cls, "path": path, "bytes": 0,
                        "reason": "could not stat: %s" % e})
            return
        if os.path.islink(path):
            out.append({"cls": cls, "path": path, "bytes": 0,
                        "reason": "symlink — never followed"})
            return
        age = now - st.st_mtime
        row = {"cls": cls, "path": path,
               "bytes": _safe_dir_size(path, dir_stats_fn) if os.path.isdir(path) else st.st_size,
               "reason": None}
        if age < cutoff:
            row["reason"] = "too recent (%.1fd < %dd)" % (age / 86400.0, min_age_days)
        out.append(row)

    venvs = Path(home or os.path.expanduser("~")) / ".venvs"
    if venvs.is_dir():
        try:
            for entry in venvs.iterdir():
                if ONEOFF_VENV_NAME_RE.match(entry.name):
                    _consider("oneoff-venv", str(entry))
        except OSError as e:
            out.append({"cls": "oneoff-venv", "path": None,
                        "reason": "could not walk %s: %s" % (venvs, e)})
    tmp = Path(tmp_dir)
    if tmp.is_dir():
        try:
            for entry in tmp.iterdir():
                if ONEOFF_VENV_TMP_RE.match(entry.name):
                    _consider("oneoff-venv", str(entry))
        except OSError as e:
            out.append({"cls": "oneoff-venv", "path": None,
                        "reason": "could not walk %s: %s" % (tmp, e)})
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


def _norm_action(cls, row, default_kind):
    """Normalize a #854 discovery row (which carries `bytes`, an optional
    explicit `kind`, and a `reason` set only when the candidate is KEPT) into an
    executor action. A row with `reason` set is ALWAYS a skip (never acted on) —
    this is what stops a "too recent" / "kept" candidate from being deleted; a
    candidate (`reason` is None) uses its own `kind` or the rung's default."""
    path = row.get("path")
    if path is None:
        return {"cls": cls, "path": "-", "bytes": 0, "kind": "skip",
                "reason": row.get("reason", "discovery error")}
    nbytes = row.get("bytes", 0) or 0
    reason = row.get("reason")
    if reason is not None:
        return {"cls": cls, "path": path, "bytes": nbytes, "kind": "skip", "reason": reason}
    return {"cls": cls, "path": path, "bytes": nbytes,
            "kind": row.get("kind") or default_kind, "reason": None, "why": row.get("why")}


def _plan_apt_cache(home, now):
    return [_norm_action("apt-cache", r, "apt-clean") for r in discover_apt_cache()]


def _plan_rotated_logs(home, now):
    return [_norm_action("rotated-log", r, "delete") for r in discover_rotated_logs(now=now)]


def _plan_runner_update(home, now):
    return [_norm_action("runner-update", r, "delete") for r in discover_runner_update()]


def _plan_runner_checkouts(home, now):
    return [_norm_action("runner-checkout", r, "delete")
            for r in discover_stale_runner_checkouts(now=now)]


def _plan_superseded_runner_versions(home, now):
    return [_norm_action("runner-superseded", r, "delete")
            for r in discover_superseded_runner_versions()]


def _plan_docker(home, now):
    return [_norm_action("docker-image", r, "docker-rmi")
            for r in discover_docker_images(now=now)]


def _plan_user_cache(home, now):
    return [_norm_action("user-cache", r, "delete")
            for r in discover_stale_user_cache(home=home, now=now)]


def _plan_claude_versions(home, now):
    return [_norm_action("claude-version", r, "delete")
            for r in discover_stale_claude_versions(home=home)]


def _plan_oneoff_venvs(home, now):
    return [_norm_action("oneoff-venv", r, "delete")
            for r in discover_oneoff_venvs(home=home, now=now)]


def _default_planners(home, now):
    """The auto-drain LADDER, cheapest/safest first, ladder STOPS the moment the
    worst mount is back under target. #854 added the cache-class box-level rungs
    (apt / rotated logs / runner cache / docker / user-cache / stale Claude
    binaries / one-off venvs); the existing own-home rungs stay, and
    transcripts-gzip stays LAST (the most conservative reclaim). Docker + stale
    runner checkouts are Runner.Worker-gated inside their planners."""
    return [
        ("apt-cache", lambda: _plan_apt_cache(home, now)),
        ("rotated-log", lambda: _plan_rotated_logs(home, now)),
        ("runner-update", lambda: _plan_runner_update(home, now)),
        ("runner-superseded", lambda: _plan_superseded_runner_versions(home, now)),
        ("claude-version", lambda: _plan_claude_versions(home, now)),
        ("oneoff-venv", lambda: _plan_oneoff_venvs(home, now)),
        ("scratch", lambda: _plan_scratch(home, now)),
        ("uploads", lambda: _plan_uploads(home, now)),
        ("cli-version", lambda: _plan_cli_versions(home, now)),
        ("user-cache", lambda: _plan_user_cache(home, now)),
        ("journal", lambda: _plan_journal(home, now)),
        ("docker-image", lambda: _plan_docker(home, now)),
        ("runner-checkout", lambda: _plan_runner_checkouts(home, now)),
        ("worktree", lambda: _plan_worktrees(home, now)),
        ("toolchain", lambda: _plan_toolchain(home, now)),
        ("transcript", lambda: _plan_transcripts(home, now)),
    ]


# --------------------------------------------------------------------------- #
# action execution
# --------------------------------------------------------------------------- #
def _rm_path(path, sudo=False, run_fn=None):
    """Delete a file or directory, never crossing a filesystem
    (``--one-file-system``). TOCTOU re-verify (review 🟡): re-lstat immediately
    and REFUSE a path that has become a symlink since discovery (never delete
    THROUGH a symlink). When ``sudo`` (a root-owned cache class + NOPASSWD sudo
    present, #854) the delete goes through ``sudo -n rm`` for BOTH files and dirs
    (an unprivileged ``os.unlink`` cannot remove a root-owned file). Raises on
    failure (the caller logs it)."""
    run_fn = run_fn or subprocess.run
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return                              # already gone between plan and act — fine
    import stat as _stat
    if _stat.S_ISLNK(st.st_mode):
        raise OSError("refusing to delete %s — it is now a symlink (TOCTOU)" % path)
    if sudo:
        run_fn(["sudo", "-n", "rm", "-rf", "--one-file-system", "--", path],
               check=True, capture_output=True, text=True, timeout=300)
    elif os.path.isdir(path):
        run_fn(["rm", "-rf", "--one-file-system", "--", path],
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


def _sudo_available(probe_fn=None):
    """True iff ``sudo -n true`` succeeds (NOPASSWD sudo present — gk `gatekeeper`,
    dev1/dev2 `newlevel`). Probed ONCE per drain and cached by the caller. Any
    error/timeout → False (fall back to the unprivileged attempt); NEVER
    interactive (`-n`), NEVER a retry."""
    if probe_fn is None:
        def probe_fn():
            return subprocess.run(["sudo", "-n", "true"],
                                  capture_output=True, text=True, timeout=10).returncode == 0
    try:
        return bool(probe_fn())
    except Exception as e:
        _dbg("sudo -n probe failed: %r" % e)
        return False


def _perform_action(a, sudo_ok=False, run_fn=None):
    run_fn = run_fn or subprocess.run
    kind = a.get("kind")
    path = a.get("path")
    nbytes = a.get("bytes", 0) or 0
    # #854: a ROOT-owned cache class deletes via `sudo -n` when NOPASSWD sudo is
    # present; own-home/docker classes never get the prefix.
    use_sudo = bool(sudo_ok) and a.get("cls") in SUDO_CLASSES
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
    if kind == "apt-clean":                 # #854 rung (a) — `apt-get clean`
        cmd = (["sudo", "-n"] if use_sudo else []) + ["apt-get", "clean"]
        run_fn(cmd, check=True, capture_output=True, text=True, timeout=120)
        return nbytes
    if kind == "docker-rmi":                # #854 rung (d) — `path` is the image id (docker group, no sudo)
        run_fn(["docker", "rmi", path],
               check=True, capture_output=True, text=True, timeout=120)
        return nbytes
    if kind == "delete":
        # 🟡6 action-time re-verify: between plan and act a self-update may have
        # moved the symlink onto the dir we planned to reclaim — re-resolve the
        # root's symlinks NOW and REFUSE if it is a current/staged version.
        if a.get("cls") == "runner-superseded" and not _runner_superseded_safe_to_delete(path):
            raise OSError("runner-superseded re-verify refused %s — now a "
                          "current/staged version" % path)
        _rm_path(path, sudo=use_sudo, run_fn=run_fn)
        return nbytes
    return 0


def _make_do_action(dry_run, sudo_ok=False, run_fn=None):
    def _do(a):
        if dry_run:
            return a.get("bytes", 0) or 0
        return _perform_action(a, sudo_ok=sudo_ok, run_fn=run_fn)
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
    # #854: a per-rung SUMMARY line `disk-guard: NN% → drain rung=<name>
    # freed=<bytes> → MM%`. To avoid a second recheck_fn() call per rung (which
    # would exhaust a finite injected-recheck test fixture), the summary is
    # DEFERRED: rung i's "after" pct is read at rung i+1's own start-recheck, and
    # the LAST rung's after is a single recheck after the loop.
    pending = None                          # (label, before_pct, freed_total)

    def _emit_summary(summary, after_pct):
        label, before_pct, freed_total = summary
        verb = "WOULD-DRAIN" if dry_run else "DRAIN"
        line = _log_line(now, verb, "rung=" + label, freed_total,
                         "disk-guard: %d%% → drain rung=%s freed=%s → %d%%"
                         % (before_pct, label, _human(freed_total), after_pct))
        logs.append(line)
        _append_log(log_path, [line])

    for _label, planner in planners:
        worst = recheck_fn()
        if pending is not None:
            _emit_summary(pending, worst)
            pending = None
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
        rung_freed = 0
        rung_acted = 0
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
                                        "freed~=%s %s" % (freed, a.get("why") or reason or "")))
            rung_freed += (freed or 0)
            rung_acted += 1
        logs.extend(rung_lines)
        _append_log(log_path, rung_lines)
        if rung_acted > 0:                  # a rung that only skipped gets no summary
            pending = (_label, worst, rung_freed)
    if pending is not None:
        _emit_summary(pending, recheck_fn())
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
def _cadence_allows_drain(worst_pct, due, dry_run):
    """#854 — SEVERITY BEATS CADENCE. At CRITICAL pressure (>= DISK_CRITICAL_PCT)
    the drain runs EVERY poll (the cadence gate is BYPASSED) — a guard that only
    LOGGED "cadence-gated" for 20+ min at 97 % while the box filled is exactly
    the failure this fixes. In the 80-95 % band the cadence gate applies (the
    du-heavy ladder runs at most once per MIN_DRAIN_INTERVAL_S). A dry-run
    always proceeds (it deletes nothing)."""
    if dry_run:
        return True
    if worst_pct >= DISK_CRITICAL_PCT:
        return True
    return due


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
                   geteuid_fn=None, mounts=None, min_drain_interval_s=None,
                   planners_fn=None, sudo_probe_fn=None):
    """Watchdog Job 40. Every poll: compute pressure + write the footer cache.
    Only at ≥80 % (and not as root, cadence-gated, single-instance): run the
    drain ladder over this user's own home; if still ≥90 % after, escalate. At
    CRITICAL (≥90 %), also record the root-level finding surfaced from the root
    reporter's world-readable report (#841 leg C, `disk_guard_root`) — a cheap
    read that runs even when the du-heavy drain is cadence-gated, and NEVER
    pings. Best-effort; returns log lines for the sweep's own log."""
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
    # #854: severity beats cadence — at CRITICAL pressure the drain runs EVERY
    # poll; only the 80-95 % band is cadence-gated.
    if not dry_run:
        due = _drain_due(home, now, min_drain_interval_s)
        if not _cadence_allows_drain(status["worst_pct"], due, dry_run=False):
            logs.append("disk-guard: %d%% (%s) — drain cadence-gated this poll (< %d%% critical)"
                        % (status["worst_pct"], status["dim"], DISK_CRITICAL_PCT))
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
        planners = (planners_fn or _default_planners)(home, now)

        def recheck():
            return disk_status(statvfs_fn=statvfs_fn, dev_fn=dev_fn,
                               mounts=mounts, now=now)["worst_pct"]

        # #854: probe NOPASSWD sudo ONCE per drain — the root-owned rungs
        # (apt/rotated-log/runner-*) delete via `sudo -n` where it exists, else
        # the unprivileged attempt. Never probed in a dry-run (deletes nothing).
        sudo_ok = _sudo_available(sudo_probe_fn) if not dry_run else False
        do_action = _make_do_action(dry_run, sudo_ok=sudo_ok, run_fn=None)
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
