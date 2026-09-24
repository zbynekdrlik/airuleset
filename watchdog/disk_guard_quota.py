"""#1140 — per-account QUOTA pressure for the disk guard (watchdog Job 40).

The montalu1 EDQUOT incident (24.9.2026): the account sat at its ext4 usrquota
while the filesystem was at 69 % (``level=ok``). Every drain branch in
``disk_guard.run_disk_guard`` keyed on the FILESYSTEM level, so nothing ever
drained, and the stream session itself cannot free space it has no rung for.

This leaf gives the guard a second pressure source, on SHARED-STREAM boxes only
(a workstation has no per-account quota — the caller never activates it there):

* ``quota_state`` reads the account's quota once per poll (``quota -w -u -p``)
  into ``status["quota_pct"]`` and decides PRESSURE (>= ``QUOTA_DRAIN_PCT``)
  and GROWTH (the percentage rose since the cached reading — the same
  growth-beats-cadence rule #925 applies to the filesystem).
* ``run_quota_pass`` runs the EXISTING drain ladder through the EXISTING
  ``execute_drain`` (every scope fence, TOCTOU re-check, live-scratch refusal
  and low-yield skip unchanged), with the recheck re-reading the QUOTA and the
  stop target ``QUOTA_TARGET_PCT``. A quota read that fails mid-pass STOPS the
  pass (never delete on uncertainty).
* ``update_quota_drift`` compares what the quota CHARGES with what ``du`` finds
  in the account's home + its ``/tmp/claude-<uid>`` scratch, at most hourly.
  Stale vfsv1 accounting (the #1140 root cause) shows up as a drift finding
  instead of a phantom-full account; the root ``airuleset-quota-refresh``
  timer (``cli_resource_guards``) is what re-counts it.

Best-effort throughout: a failed read or ``du`` degrades to "no quota data",
never to an exception out of the guard.
"""
# airuleset:script-ok helper module, imported from disk_guard

import collections
import math
import os
import subprocess

QUOTA_DRAIN_PCT = 90            # quota pressure → the drain ladder runs
QUOTA_TARGET_PCT = 80           # the quota pass stops once usage is back below this
QUOTA_DRIFT_INTERVAL_S = 3600   # drift (a `du` of the home) at most hourly
QUOTA_DRIFT_FINDING_MB = 1024   # drift above this is a finding
QUOTA_DRIFT_DU_TIMEOUT_S = 25   # stays inside Job 40's 30 s minimum budget

QuotaState = collections.namedtuple("QuotaState", "pct pressure growth usage_fn")
_INACTIVE = QuotaState(None, False, False, None)


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
    except Exception:
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


def _prior_number(prior, key):
    v = prior.get(key) if isinstance(prior, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def quota_state(status, home, now, active, usage_fn=None, du_fn=None, logs=None):
    """One quota reading for this poll. ``active`` is the caller's gate
    (shared-stream box AND not root); inactive → no read at all. Sets
    ``status["quota_pct"]`` and (hourly) ``status["quota_drift"]``; appends any
    drift-finding log line to ``logs``. Returns a :class:`QuotaState`."""
    if not active:
        return _INACTIVE
    from watchdog import disk_guard as dg
    usage_fn = usage_fn or read_quota_usage
    try:
        usage = usage_fn()
    except Exception as e:
        dg._dbg("quota read: %r" % e)
        usage = None
    pct = quota_pct(usage)
    if pct is None:
        return QuotaState(None, False, False, usage_fn)
    status["quota_pct"] = pct
    prior = dg._read_status_cache(home)
    prev = _prior_number(prior, "quota_pct")
    pressure = pct >= QUOTA_DRAIN_PCT
    growth = pressure and prev is not None and pct > prev
    lines = update_quota_drift(status, home, now, usage, du_fn or default_du_kib, prior)
    if logs is not None:
        logs.extend(lines)
    return QuotaState(pct, pressure, growth, usage_fn)


def default_du_kib(paths):
    """Total KiB of the EXISTING ``paths`` (one filesystem each, ``du -skxc``),
    niced and bounded; None on any failure/timeout. ``du`` exits 1 on an
    unreadable subdirectory but still prints the total, so the total line — not
    the exit code — is the result."""
    existing = [p for p in paths if os.path.isdir(p)]
    if not existing:
        return None
    try:
        r = subprocess.run(["nice", "-n19", "du", "-skxc", "--"] + existing,
                           capture_output=True, text=True,
                           timeout=QUOTA_DRIFT_DU_TIMEOUT_S)
    except Exception:
        return None
    for line in reversed((r.stdout or "").splitlines()):
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == "total":
            try:
                return int(parts[0])
            except ValueError:
                return None
    return None


def update_quota_drift(status, home, now, usage, du_fn, prior):
    """``status["quota_drift"]`` = what the quota charges minus what ``du``
    finds in the home + ``/tmp/claude-<uid>`` (MB). Recomputed at most every
    ``QUOTA_DRIFT_INTERVAL_S``; in between the cached record is carried
    forward. A drift above ``QUOTA_DRIFT_FINDING_MB`` is a finding: one
    ``QUOTA-DRIFT`` log line per computation (hourly at most, never per poll).
    A failed ``du`` still stamps ``ts`` (no retry storm) with ``error``."""
    from watchdog import disk_guard as dg
    old = prior.get("quota_drift") if isinstance(prior, dict) else None
    old_ts = _prior_number(old, "ts") if isinstance(old, dict) else None
    if old_ts is not None and 0 <= now - old_ts < QUOTA_DRIFT_INTERVAL_S:
        status["quota_drift"] = old
        return []
    paths = [str(home), "/tmp/claude-%d" % os.getuid()]
    try:
        du_kib = du_fn(paths)
    except Exception as e:
        dg._dbg("quota drift du: %r" % e)
        du_kib = None
    charged_mb = usage[0] // 1024
    if du_kib is None:
        status["quota_drift"] = {"ts": now, "charged_mb": charged_mb,
                                 "error": "du unavailable", "finding": False}
        return []
    du_mb = du_kib // 1024
    drift_mb = (usage[0] - du_kib) // 1024
    finding = drift_mb > QUOTA_DRIFT_FINDING_MB
    status["quota_drift"] = {"ts": now, "charged_mb": charged_mb, "du_mb": du_mb,
                             "drift_mb": drift_mb, "finding": finding}
    if not finding:
        return []
    line = dg._log_line(
        now, "QUOTA-DRIFT", str(home), drift_mb * 1024 * 1024,
        "quota charges %dMB but du finds %dMB (home + /tmp/claude-<uid>) — %dMB "
        "phantom: stale quota accounting, the root airuleset-quota-refresh "
        "timer re-counts it (#1140)" % (charged_mb, du_mb, drift_mb))
    dg._append_log(dg._log_path(home), [line])
    return [line]


def run_quota_pass(status, home, now, dry_run, planners, q, do_action, geteuid_fn):
    """The QUOTA drain pass: the existing ladder via the existing
    ``execute_drain``, rechecking the QUOTA against ``QUOTA_TARGET_PCT``.
    Records the decision as ``status["quota_drain"]`` AND in the footer cache
    (read-modify-write, so the record survives a dry-run poll too). Returns
    the log lines."""
    from watchdog import disk_guard as dg
    unreadable = []

    def recheck():
        try:
            p = quota_pct(q.usage_fn())
        except Exception:
            p = None
        if p is None:
            unreadable.append(True)
            return 0            # stop the pass — never delete on uncertainty
        return p

    log_path = dg._log_path(home)
    logs = dg.execute_drain(status, home, planners, recheck, do_action,
                            geteuid_fn=geteuid_fn, log_path=log_path, now=now,
                            dry_run=dry_run, target_pct=QUOTA_TARGET_PCT,
                            pressure="quota")
    after = recheck() if not unreadable else None
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
            ("%d%%" % after) if after is not None
            else "quota unreadable mid-pass (stopped, nothing deleted on uncertainty)"))
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
        dg._dbg("quota_drain cache write: %r" % e)
    return logs


def carry_quota_fields(status, post):
    """A post-drain status rebuilt from ``disk_status`` knows nothing about the
    quota — carry this poll's quota fields over so the footer cache keeps them."""
    for k in ("quota_pct", "quota_drift", "quota_drain"):
        if k in status:
            post[k] = status[k]
    return post
