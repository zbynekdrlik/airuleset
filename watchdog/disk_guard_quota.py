"""#1140 — per-account QUOTA pressure for the disk guard (watchdog Job 40).

The montalu1 EDQUOT incident (24.9.2026): the account sat at its ext4 usrquota
while the filesystem was at 69 % (``level=ok``). Every drain branch in
``disk_guard.run_disk_guard`` keyed on the FILESYSTEM level, so nothing ever
drained, and the stream session itself cannot free space it has no rung for.

This leaf gives the guard a second pressure source, on SHARED-STREAM boxes only
(a workstation has no per-account quota — the caller never activates it there,
and never as root):

* ``quota_state`` reads the account's quota once per poll (``quota -w -u -p``)
  into ``status["quota_pct"]`` and decides PRESSURE (>= ``QUOTA_DRAIN_PCT``),
  GROWTH (the percentage rose since the cached reading — the same
  growth-beats-cadence rule #925 applies to the filesystem) and EXHAUSTED (the
  last quota pass could not lower the quota — back off to the hourly cadence
  instead of re-walking the ladder every poll at >= 95 %).
* ``run_quota_pass`` runs the EXISTING drain ladder through the EXISTING
  ``execute_drain`` (every scope fence, TOCTOU re-check, live-scratch refusal
  and low-yield skip unchanged), minus the box-level rungs whose files are not
  charged to this account, with the recheck re-reading the QUOTA and the stop
  target ``QUOTA_TARGET_PCT``. A quota read that fails mid-pass STOPS the pass
  (never delete on uncertainty) and is never recorded as a number.
* ``maybe_update_quota_drift`` compares what the quota CHARGES with what ``du``
  finds in the account's home + its ``/tmp/claude-<uid>`` scratch, at most
  hourly, only on a poll where no full drain runs, with the first run
  staggered per uid. It is a heuristic: a positive drift is stale vfsv1
  accounting (the #1140 root cause, re-counted by the root
  ``airuleset-quota-refresh`` timer in ``cli_resource_guards_quota``) OR files
  the account owns outside those two paths.

Best-effort throughout: a failed read or ``du`` degrades to "no quota data",
never to an exception out of the guard. The parent's helpers (``_dbg``,
``_log_line``, the status cache, ``execute_drain``) are imported lazily inside
the functions — ``disk_guard`` imports this leaf at module load.
"""

import collections
import math
import os
import subprocess

QUOTA_DRAIN_PCT = 90            # quota pressure → the drain ladder runs
QUOTA_TARGET_PCT = 80           # the quota pass stops once usage is back below this
QUOTA_DRIFT_INTERVAL_S = 3600   # drift (a `du` of the home) at most hourly
QUOTA_DRIFT_FINDING_MB = 1024   # drift above this is a finding
QUOTA_DRIFT_STAGGER_MIN = 10    # first drift run spread over this many minutes by uid
# `du` of a large home; Job 40's min_budget only gates the START, so this is
# the real bound on the extra time drift adds to a (non-drain) poll.
QUOTA_DRIFT_DU_TIMEOUT_S = 25
# Rungs whose files are ROOT/box-owned (or another user's) — deleting them can
# never lower THIS account's quota, so the quota pass skips them (the
# filesystem pass still runs them).
QUOTA_PASS_EXCLUDED_RUNGS = frozenset({
    "apt-cache", "rotated-log", "runner-update", "runner-superseded",
    "runner-checkout", "runner-diag", "docker-image", "journal", "home-worktree",
})

QuotaState = collections.namedtuple(
    "QuotaState", "pct pressure growth exhausted usage usage_fn")
_INACTIVE = QuotaState(None, False, False, False, None, None)


def _dbg(msg):
    from watchdog import disk_guard as dg
    dg._dbg(msg)


def read_quota_usage():
    """``(used_kib, hard_kib)`` for this user on the first quota'd filesystem,
    or None (quota not enabled, no limit, command failed). Uses
    ``quota -w -u -p`` (machine-parseable, no name-wrap). Never raises.

    ``quota`` exits 1 when the soft limit is exceeded (``quota.c showquotas()``
    returns ``over > 0 ? 1 : 0``), so rc=1 is a VALID response with parseable
    stdout — only rc>1 or an exception is a failure. The ``*`` suffix on the
    blocks field marks over-soft and is stripped (#950 review R1)."""
    try:
        r = subprocess.run(["quota", "-w", "-u", "-p"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode > 1:
            return None
        for line in (r.stdout or "").strip().splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0].startswith("/"):
                used_kib = int(parts[1].rstrip("*"))
                hard_kib = int(parts[3])
                if hard_kib > 0:
                    return (used_kib, hard_kib)
        return None
    except Exception as e:
        _dbg("quota read failed: %r" % e)
        return None


def quota_pct(usage):
    """Usage as a ceil'd percentage of the hard limit, or None."""
    if not usage:
        return None
    used_kib, hard_kib = usage
    if not hard_kib or hard_kib <= 0:
        return None
    return int(math.ceil(100.0 * used_kib / hard_kib))


def read_quota_pct():
    """#950 API kept for callers/tests: the percentage, or None."""
    return quota_pct(read_quota_usage())


def _num(d, key):
    v = d.get(key) if isinstance(d, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _safe_read(usage_fn):
    try:
        return usage_fn()
    except Exception as e:
        _dbg("quota read: %r" % e)
        return None


def quota_state(status, home, now, active, usage_fn=None):
    """One quota reading for this poll. ``active`` is the caller's gate
    (shared-stream box AND not root); inactive → no read at all. Sets
    ``status["quota_pct"]`` and carries the previous poll's ``quota_drift`` /
    ``quota_drain`` records forward (the per-poll status rewrite would drop
    them). Returns a :class:`QuotaState`."""
    if not active:
        return _INACTIVE
    from watchdog import disk_guard as dg
    prior = dg._read_status_cache(home)
    prior = prior if isinstance(prior, dict) else {}
    for k in ("quota_drift", "quota_drain"):
        if isinstance(prior.get(k), dict):
            status[k] = prior[k]
    usage_fn = usage_fn or read_quota_usage
    usage = _safe_read(usage_fn)
    pct = quota_pct(usage)
    if pct is None:
        return QuotaState(None, False, False, False, None, usage_fn)
    status["quota_pct"] = pct
    prev = _num(prior, "quota_pct")
    pressure = pct >= QUOTA_DRAIN_PCT
    last = prior.get("quota_drain")
    before, after = _num(last, "before_pct"), _num(last, "after_pct")
    exhausted = pressure and before is not None and after is not None and after >= before
    growth = pressure and prev is not None and pct > prev
    return QuotaState(pct, pressure, growth, exhausted, usage, usage_fn)


def default_du_kib(paths):
    """Total KiB of the EXISTING ``paths`` (one filesystem each, ``du -skxc``),
    niced + idle-IO and bounded; None on any failure/timeout. ``du`` exits 1 on
    an unreadable subdirectory but still prints the total, so the total line —
    not the exit code — is the result."""
    existing = [p for p in paths if os.path.isdir(p)]
    if not existing:
        return None
    try:
        r = subprocess.run(["nice", "-n19", "ionice", "-c3", "du", "-skxc", "--"] + existing,
                           capture_output=True, text=True,
                           timeout=QUOTA_DRIFT_DU_TIMEOUT_S)
    except Exception as e:
        _dbg("quota drift du failed: %r" % e)
        return None
    for line in reversed((r.stdout or "").splitlines()):
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == "total":
            try:
                return int(parts[0])
            except ValueError:
                return None
    return None


def maybe_update_quota_drift(status, home, now, q, du_fn=None, logs=None):
    """``status["quota_drift"]`` = what the quota charges minus what ``du``
    finds in the home + ``/tmp/claude-<uid>`` (MB). Called ONLY on a poll where
    no full drain runs (never a ``du`` in front of the drain ladder). Recomputed
    at most every ``QUOTA_DRIFT_INTERVAL_S``; the FIRST computation waits for
    this uid's minute slot so accounts never ``du`` in lockstep. A drift above
    ``QUOTA_DRIFT_FINDING_MB`` is a finding: one ``QUOTA-DRIFT`` log line per
    computation (hourly at most). A failed ``du`` still stamps ``ts`` (no retry
    storm) with ``error``."""
    if q.usage is None:
        return
    from watchdog import disk_guard as dg
    uid = os.getuid()
    old_ts = _num(status.get("quota_drift"), "ts")
    if old_ts is not None and 0 <= now - old_ts < QUOTA_DRIFT_INTERVAL_S:
        return
    if old_ts is None and (int(now) // 60) % QUOTA_DRIFT_STAGGER_MIN != uid % QUOTA_DRIFT_STAGGER_MIN:
        return
    try:
        du_kib = (du_fn or default_du_kib)([str(home), "/tmp/claude-%d" % uid])
    except Exception as e:
        _dbg("quota drift du: %r" % e)
        du_kib = None
    charged_mb = q.usage[0] // 1024
    if du_kib is None:
        status["quota_drift"] = {"ts": now, "charged_mb": charged_mb,
                                 "error": "du unavailable", "finding": False}
        return
    drift_mb = (q.usage[0] - du_kib) // 1024
    finding = drift_mb > QUOTA_DRIFT_FINDING_MB
    status["quota_drift"] = {"ts": now, "charged_mb": charged_mb,
                             "du_mb": du_kib // 1024, "drift_mb": drift_mb,
                             "finding": finding}
    if not finding:
        return
    line = dg._log_line(
        now, "QUOTA-DRIFT", str(home), drift_mb * 1024 * 1024,
        "quota charges %dMB, du of home + /tmp/claude-<uid> finds %dMB — %dMB "
        "charged but not found there: stale quota accounting (the root "
        "airuleset-quota-refresh timer re-counts it, #1140) or files the "
        "account owns elsewhere (/tmp, /var/tmp)" % (charged_mb, du_kib // 1024, drift_mb))
    dg._append_log(dg._log_path(home), [line])
    if logs is not None:
        logs.append(line)


def run_quota_pass(status, home, now, dry_run, planners, q, do_action, geteuid_fn):
    """The QUOTA drain pass: the existing ladder (minus
    ``QUOTA_PASS_EXCLUDED_RUNGS``) via the existing ``execute_drain``,
    rechecking the QUOTA against ``QUOTA_TARGET_PCT``. An unreadable quota
    returns None to ``execute_drain``, which STOPS the pass. Records the
    decision as ``status["quota_drain"]`` AND in the footer cache
    (read-modify-write, so the record survives a dry-run poll too). Returns
    the log lines."""
    from watchdog import disk_guard as dg

    def recheck():
        return quota_pct(_safe_read(q.usage_fn))

    own = [(lab, p) for lab, p in planners if lab not in QUOTA_PASS_EXCLUDED_RUNGS]
    log_path = dg._log_path(home)
    logs = dg.execute_drain(status, home, own, recheck, do_action,
                            geteuid_fn=geteuid_fn, log_path=log_path, now=now,
                            dry_run=dry_run, target_pct=QUOTA_TARGET_PCT,
                            pressure="quota")
    after = recheck()
    rec = {"ts": now, "before_pct": q.pct, "after_pct": after,
           "trigger_pct": QUOTA_DRAIN_PCT, "target_pct": QUOTA_TARGET_PCT,
           "dry_run": bool(dry_run)}
    status["quota_drain"] = rec
    if after is not None:
        status["quota_pct"] = after
    line = dg._log_line(
        now, "QUOTA-DRAIN", str(home), 0,
        "quota %d%% >= %d%% → drain toward < %d%% → %s" % (
            q.pct, QUOTA_DRAIN_PCT, QUOTA_TARGET_PCT,
            ("%d%%" % after) if after is not None else "quota unreadable (recorded as unknown)"))
    logs.append(line)
    dg._append_log(log_path, [line])
    try:
        cur = dg._read_status_cache(home)
        if isinstance(cur, dict):
            cur["quota_drain"] = rec
            if after is not None:
                cur["quota_pct"] = after
            dg.write_status_cache(cur, home=home)
    except Exception as e:
        _dbg("quota_drain cache write: %r" % e)
    return logs


def run_drain_passes(status, home, now, dry_run, planners, planners_fn, q,
                      fs_pressure, statvfs_fn, dev_fn, mounts, geteuid_fn, sudo_probe_fn):
    """The full drain's passes, under the caller's lock (extracted from
    ``run_disk_guard``, #1140): the #1140 QUOTA pass first when the account's
    quota is under pressure (``disk_guard_quota.run_quota_pass``), then the
    filesystem pass when the box is. The fs pass after a quota pass gets FRESH
    planners — the quota pass consumed the shared scratch rows, and replaying
    them would journal deletions of already-gone paths (a second scratch walk,
    only when BOTH pressures hold). Returns log lines."""
    from watchdog import disk_guard as dg
    logs = []

    def recheck():
        return dg.disk_status(statvfs_fn=statvfs_fn, dev_fn=dev_fn,
                              mounts=mounts, now=now)["worst_pct"]

    # #854: probe NOPASSWD sudo ONCE per drain — the root-owned rungs
    # (apt/rotated-log/runner-*) delete via `sudo -n` where it exists, else
    # the unprivileged attempt. Never probed in a dry-run (deletes nothing).
    sudo_ok = dg._sudo_available(sudo_probe_fn) if not dry_run else False
    do_action = dg._make_do_action(dry_run, sudo_ok=sudo_ok, run_fn=None, now=now)
    if q.pressure:
        logs += run_quota_pass(status, home, now, dry_run, planners, q,
                               do_action, geteuid_fn)
        if fs_pressure:
            planners = (planners_fn(home, now) if planners_fn is not None
                        else dg._default_planners(home, now, scratch_rows=None))
    if fs_pressure:
        logs += dg.execute_drain(status, home, planners, recheck, do_action,
                                 geteuid_fn=geteuid_fn, log_path=dg._log_path(home),
                                 now=now, dry_run=dry_run)
    return logs


def carry_quota_fields(status, post):
    """A post-drain status rebuilt from ``disk_status`` knows nothing about the
    quota — carry this poll's quota fields over so the footer cache keeps them."""
    for k in ("quota_pct", "quota_drift", "quota_drain"):
        if k in status:
            post[k] = status[k]
    return post
