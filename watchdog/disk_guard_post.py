"""Post-drain bookkeeping for watchdog Job 40 (``disk_guard.run_disk_guard``).

Extracted verbatim-in-behaviour from ``run_disk_guard`` (#1067 slice 1e, which
also had to teach it about a drain the poll budget cut short, and
``disk_guard.py`` sits at its size-ratchet ceiling). After the drain passes:

* the #925/#968 ``drain_exhausted`` flag + ``drain_exhausted_streak`` — the
  consecutive count of drains at >= the effective critical level that freed
  nothing (the footer badge shows at streak >= 2) — plus the #980 per-rung
  skip reasons and the post-drain ``worst_pct``/``level``/``ts`` go into the
  footer cache. A CUT-SHORT drain (#1067) freed nothing because its rungs were
  DEFERRED, not exhausted: it keeps the previous flag and streak;
* the #892 top-consumers walk refreshes ``top_consumers`` in the cache (only on
  the default planners path, and not past the budget — a cut-short poll keeps
  the list the cache already carries);
* at CRITICAL, the #849 escalation and the #895 severe ticket (>= SEVERE_PCT)
  get ONE top-consumers list (#896-899) — a fresh walk, or on a cut-short poll
  the cached list, so no heavy walk runs after the budget is spent.

Every ``disk_guard`` helper is looked up on the module at call time, so the
existing test seams (``monkeypatch.setattr(dg, "_collect_top_consumers", …)``,
``dg.escalate``…) keep working.
"""


def _record_exhausted(status, post, home, statvfs_fn, cut_short):
    from watchdog import disk_guard as dg
    from watchdog.disk_guard_worktrees import effective_critical_pct
    cur = dg._read_status_cache(home)
    prev = cur.get("drain_exhausted_streak", 0) if isinstance(cur, dict) else 0
    if not isinstance(prev, int):
        prev = 0
    if cut_short:
        post["drain_exhausted"] = bool(isinstance(cur, dict) and cur.get("drain_exhausted"))
        post["drain_exhausted_streak"] = prev
    else:
        crit = effective_critical_pct(statvfs_fn)
        is_exhausted = (post["worst_pct"] >= crit
                        and post["worst_pct"] >= status.get("worst_pct", 0))
        post["drain_exhausted"] = is_exhausted
        post["drain_exhausted_streak"] = prev + 1 if is_exhausted else 0
    try:
        if isinstance(cur, dict):
            cur["drain_exhausted"] = post["drain_exhausted"]
            cur["drain_exhausted_streak"] = post["drain_exhausted_streak"]
            cur["drain_skipped_rungs"] = status.get("drain_skipped_rungs", [])
            cur["worst_pct"] = post["worst_pct"]
            cur["level"] = post["level"]
            cur["ts"] = post["ts"]
            dg.write_status_cache(cur, home=home)
    except Exception as e:
        dg._dbg("drain_exhausted cache write: %r" % e)


def _cached_top(status):
    """The carried ``top_consumers`` rows as the ``(path, bytes)`` pairs the
    escalation and the severe ticket take."""
    return [(r["path"], r["bytes"]) for r in status.get("top_consumers") or []
            if isinstance(r, dict) and "path" in r and "bytes" in r]


def after_drain(status, home, now, dry_run, timer, planners_fn, scratch_rows,
                statvfs_fn, dev_fn, mounts, top_consumers_fn, severe_run_fn):
    """Everything ``run_disk_guard`` does after the drain passes; returns the
    log lines. ``timer`` is the poll's ``disk_guard_timing.PollTimer``."""
    from watchdog import disk_guard as dg
    from watchdog import disk_guard_quota as dgq
    logs = []
    post = dg.disk_status(statvfs_fn=statvfs_fn, dev_fn=dev_fn, mounts=mounts, now=now)
    if not dry_run:
        _record_exhausted(status, post, home, statvfs_fn, timer.cut_short)
    # #892: only on the DEFAULT planners path (an injected planners_fn = test;
    # the walk would re-discover scratch, doubling the du walk).
    if planners_fn is None and not timer.cut_short:
        try:
            with timer.step("top-consumers", logs):
                top = dg._collect_top_consumers(home, now, limit=3, scratch_rows=scratch_rows)
            post["top_consumers"] = [{"path": p, "bytes": b} for p, b in top]
            post["top_consumers_ts"] = now
            if "largest_live_scratch" in status:
                post["largest_live_scratch"] = status["largest_live_scratch"]
            # #980: keep the per-rung skip reasons _record_exhausted just wrote
            post["drain_skipped_rungs"] = status.get("drain_skipped_rungs", [])
            dg.write_status_cache(dgq.carry_quota_fields(status, post), home=home)
        except Exception as e:
            logs.append("disk-guard: top-consumers post-drain write error: %r" % e)
    if post["level"] == "critical":
        if timer.cut_short:
            top_now = _cached_top(status)
        else:
            with timer.step("top-consumers", logs):
                top_now = (top_consumers_fn(home, now, limit=5) if top_consumers_fn is not None
                           else dg._collect_top_consumers(home, now, limit=5,
                                                          scratch_rows=scratch_rows))
        logs += dg.escalate(post, home, now, dry_run,
                            top_consumers_fn=lambda *_a, **_kw: top_now)
        # #895: the injectable filer seam — a caller exercising this path MUST
        # inject a recorder; the unset default reaches the REAL `gh`.
        if post["worst_pct"] >= dg.SEVERE_PCT:
            logs += dg.file_severe_ticket(post, home, now, top_now,
                                          dry_run=dry_run, run_fn=severe_run_fn)
    return logs
