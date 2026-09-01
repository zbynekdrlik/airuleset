"""Working-day (weekend-excluded) elapsed-time helper — the shared, stdlib-only
primitive behind #607's 24h reminder contract.

The owner's contract (2026-08-21): a W ticket / gk hand-off whose answer/action
has been outstanding for more than 24h — EXCEPT the weekend — must be pushed
forward (a reminder into the thread, a nudge to the gk). "24h" is 24 WORKING
hours: Saturday and Sunday in Europe/Bratislava do not count toward the window.

This module is the SINGLE source of truth for that computation, imported by BOTH
consumers so they can never drift (the #592 shared-helper rule): the CLI stale
tag (`cli_quals._stale_ops_wait_flagged`, #570/#607 part 2) and the watchdog
gk-lane freshness push (`watchdog.handoff_alarm`, #607 part 3).

Pure + stdlib-only (`datetime` + `zoneinfo`) — NO `import airuleset`, so it stays cheap to import
on the watchdog's 60s sweep path and trivially unit-testable with injected
timestamps.
"""


def working_seconds_between(start_ts, end_ts, tz="Europe/Bratislava"):
    """Seconds elapsed between two epoch instants, EXCLUDING any wall-clock time
    that falls on a Saturday or Sunday in `tz` (Europe/Bratislava). Weekend hours
    do not count toward a working-day deadline (#607).

    Walks the local calendar days spanned by [start, end] and sums the overlap of
    the span with each WEEKDAY's local [00:00, next-00:00) window — DST-safe,
    because every boundary is a tz-aware datetime resolved through `zoneinfo`
    (Europe/Bratislava DST transitions occur at 02:00/03:00 on a Sunday, an
    already-excluded weekend day, so a weekday is always a genuine 24h span). The
    per-day loop is bounded by the number of calendar days in the span (a handful
    for a real reminder window; only ever reached on the on-demand `--ops-wait`
    / per-hand-off path, never a hot loop).

    Returns:
      - 0.0 for a non-positive span (`end_ts <= start_ts`);
      - on a tz-resolution failure, the FULL elapsed span (`end_ts - start_ts`) —
        the pre-#607 flat behavior, so a tz hiccup can never make the check MORE
        lenient than the shipped baseline and never suppresses detection outright
        (the "must still eventually fire" fail-safe direction — a tz error never
        silently swallows a reminder).
    """
    if end_ts <= start_ts:
        return 0.0
    try:
        from datetime import datetime, time as dtime, timedelta
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:
        return float(end_ts - start_ts)          # tz unavailable -> flat span

    start_dt = datetime.fromtimestamp(start_ts, zone)
    # Start at local midnight of the span's first day, step one local day at a
    # time via `.date()` + `combine` (never `+ timedelta` on the aware value,
    # which would not re-normalize the offset across a DST boundary).
    day = datetime.combine(start_dt.date(), dtime(0, 0), tzinfo=zone)
    total = 0.0
    while True:
        day_start = day.timestamp()
        if day_start >= end_ts:
            break
        nxt = datetime.combine(day.date() + timedelta(days=1), dtime(0, 0),
                               tzinfo=zone)
        nxt_start = nxt.timestamp()
        if day.weekday() < 5:                    # Mon(0)..Fri(4) — Sat/Sun excluded
            seg_start = max(start_ts, day_start)
            seg_end = min(end_ts, nxt_start)
            if seg_end > seg_start:
                total += seg_end - seg_start
        day = nxt
    return total


def working_deadline_passed(start_ts, now, window_s, tz="Europe/Bratislava"):
    """True iff more than `window_s` WORKING seconds (weekend-excluded, `tz`) have
    elapsed since `start_ts` — the shared predicate for #607's 24h-working
    reminder contract. Strictly greater, so the deadline fires just PAST the
    window, never exactly at it."""
    return working_seconds_between(start_ts, now, tz) > window_s
