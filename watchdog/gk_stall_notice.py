"""#1109 req 2 — the gk role-pane SESSION-LIMIT stall notice.

The owner asked for ONE narrow exception to the general analyze-not-ping rule
(`feedback_discord_analyze_not_ping`): when a DECLARED gk ROLE pane (a
`cli_fleet` window with role review/infra/quality) has been one-glance `stuck`
for >= 20 min AND its 5 h session window is >= 90 % (or the pane shows Claude
Code's session-limit banner), the owner learns of it ONCE per episode instead of
discovering two hours later that both gk sessions were parked on the account cap
(the 22.9.2026 odoo-erp 2.318 incident: gk-infra silent 06:37 -> 08:44 at
`5h 94%`, FLOW parked on the same limit, no owner signal for 2 h).

This is a LEAF: pure classifiers (`five_hour_pct`, `_declared_gk_role`) + one
episode-tracking entry point (`gk_stall_notice`) called from
`goal.goal_lane_sweep`'s existing armed-pane loop with the ALREADY-cached
one-glance verdict + pane capture (ZERO new fetch / capture). Episode state
rides the caller's per-sid goal_lane `rec` (#531-reaped, no new namespace). The
notice is a real owner ping (NOT #546-suppressed — it is the owner-approved
`acctblock:`-class needs-a-human case, deliberately NOT in
`notify.SUPPRESSED_ALERT_PREFIXES`), routed through the SAME owner-routed
`send_fn` #710 (footer/webterm for zbynek, journalled)."""
import re

# The declared gk roles that get this owner-scoped exception (cli_fleet.WINDOW_ROLES).
_GK_ROLES = frozenset({"review", "infra", "quality"})

# >= 90 % of the 5 h session window is the "about to be parked on the cap" band.
FIVE_HOUR_STALL_PCT = 90
# The pane must have been one-glance `stuck` for at least this long before the
# owner is told — a session-limit park that resolves in a couple of minutes (the
# account switched, the reset landed) never alarms.
STALL_NOTICE_MIN_S = 20 * 60

# The footer segment, e.g. `5h 94%(4h)` / `5h 7%(4h)` (pane_text.py / pane_classify.py).
_FIVE_HOUR_RE = re.compile(r"(?:^|\s)5h\s+(\d+)%")

# episode keys on the goal_lane `rec` (JSON-persisted via the caller's `recs`).
_TS_KEY = "gks_ts"        # episode anchor (first stuck-at-limit sweep epoch)
_ALERT_KEY = "gks_alert"  # per-episode fired-flag


def five_hour_pct(captured):
    """The 5 h session-window percentage from the pane footer, or None when the
    segment is absent / unreadable (never raises)."""
    if not captured:
        return None
    m = _FIVE_HOUR_RE.search(captured)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _default_role_fn(cwd):
    """The declared (mode, role, source) for `cwd` — lazy import so this leaf
    stays import-safe (cli_concurrency pulls cli_fleet)."""
    import cli_concurrency
    return cli_concurrency.resolve_concurrency(cwd)


def _declared_gk_role(cwd, role_fn):
    """The declared gk role for `cwd` (review/infra/quality) when the pane matches
    a `cli_fleet` window (source == "role"), else None. Fail-safe None (a resolver
    fault never fires the notice)."""
    fn = role_fn or _default_role_fn
    try:
        _mode, role, src = fn(cwd)
    except Exception:  # noqa: BLE001 — any resolver fault => not a declared role
        return None
    if src == "role" and role in _GK_ROLES:
        return role
    return None


def _reset_episode(rec):
    """Clear the episode so a later re-stall alarms afresh (mirrors the #662
    stuck-alert reset on a non-stuck verdict)."""
    if isinstance(rec, dict):
        rec.pop(_TS_KEY, None)
        rec.pop(_ALERT_KEY, None)


def _limit_signal(captured):
    """(is_limited, pct) — the pane's 5 h window is >= 90 %, OR it shows Claude
    Code's session-limit banner. `pct` is the parsed window %, or None. A decide
    fault fails safe to NOT-limited (never fires the notice on an unreadable pane)."""
    pct = five_hour_pct(captured)
    if pct is not None and pct >= FIVE_HOUR_STALL_PCT:
        return True, pct
    try:
        from watchdog import decide
        limited = bool(decide.pane_session_limited(captured))
    except Exception:  # noqa: BLE001 — a decide fault fails safe to NOT-limited
        limited = False
    return limited, pct


def _reset_clock_hhmm(captured, now):
    """`HH:MM` for the session-limit reset from the banner, or None (best-effort).
    A parse fault returns None so the notice simply omits the reset clause."""
    try:
        from datetime import datetime, timezone
        from watchdog import decide
        epoch = decide.parse_reset_epoch(captured, now)
        if epoch is None:
            return None
        try:
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(epoch, ZoneInfo("Europe/Bratislava")
                                          ).strftime("%H:%M")
        except Exception:  # noqa: BLE001 — tz resolution best-effort -> UTC
            return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M")
    except Exception:  # noqa: BLE001 — a banner-parse fault -> omit the reset clause
        return None


def _compose(role, mins, pct, reset_hhmm):
    """The owner notice text (plain Slovak)."""
    pct_part = ("session limit 5h %d%%" % pct) if pct is not None \
        else "session limit"
    reset_part = (" — reset %s" % reset_hhmm) if reset_hhmm else ""
    return ("gk %s okno stojí %d min (%s)%s; watchdog ho po resete obnoví"
            % (role, mins, pct_part, reset_part))


def gk_stall_notice(now, rec, glance, captured, cwd, sid, pid, loc, send_fn,
                    dry_run, *, role_fn=None, run=None):
    """Fire ONE per-episode owner notice for a DECLARED gk role pane stuck at the
    session limit (#1109 req 2). Returns log lines only when it acts (a per-sweep
    non-fire is already journalled by the one-glance line). `dry_run` mutates NO
    persisted episode state and never sends.

    Episode: anchored the first sweep the pane is BOTH one-glance `stuck` AND at
    the 5 h limit (>= 90 % or the session-limit banner); it fires once `now` is
    >= STALL_NOTICE_MIN_S past the anchor, deduped per episode via `gks_alert`.
    ANY exit condition (not a declared gk role pane, not `stuck`, not at the
    limit) RESETS the episode so a later re-stall alarms afresh."""
    logs = []
    # `dry_run` mutates NO persisted episode state (run_once's save_state is
    # unconditional, so an in-place reset of the live `rec` would leak, #1075-A#2).
    role = _declared_gk_role(cwd, role_fn)
    if role is None:
        if not dry_run:
            _reset_episode(rec)
        return logs
    if getattr(glance, "verdict", None) != "stuck":
        if not dry_run:
            _reset_episode(rec)      # recovered / not-a-candidate -> reset
        return logs
    limited, pct = _limit_signal(captured)
    if not limited:
        if not dry_run:
            _reset_episode(rec)      # stuck but not at the limit -> not our case
        return logs

    # stuck AND at the limit: anchor / measure the episode. A FUTURE prev_ts
    # (clock skew) is re-anchored to `now` (drop the corrupt value + restart the
    # 20-min clock) — the nudge_gate `_gate_ts` future-skew discipline, the safe
    # direction (never suppress forever on a corrupt anchor).
    prev_ts = rec.get(_TS_KEY) if isinstance(rec, dict) else None
    anchor = prev_ts if (isinstance(prev_ts, (int, float))
                         and not isinstance(prev_ts, bool)
                         and prev_ts <= now) else now
    mins = int(max(0, now - anchor) // 60)

    if dry_run:
        if now - anchor >= STALL_NOTICE_MIN_S and not (
                isinstance(rec, dict) and rec.get(_ALERT_KEY)):
            logs.append("gk-stall %s -> owner-notice DUE (dry-run, %d min, %s)"
                        % (loc, mins, role))
        return logs

    if isinstance(rec, dict) and prev_ts != anchor:
        # first stuck-at-limit sweep of the episode (prev_ts None) OR a corrupt
        # FUTURE prev_ts re-anchored to now — persist the correction either way so
        # a skewed anchor self-heals instead of muting the notice forever.
        rec[_TS_KEY] = anchor
    if now - anchor < STALL_NOTICE_MIN_S:
        return logs                  # not yet 20 min — accumulate silently
    if isinstance(rec, dict) and rec.get(_ALERT_KEY):
        return logs                  # already alerted this episode
    if send_fn is None:
        return logs                  # degraded call: retry next sweep, never latch

    reset_hhmm = _reset_clock_hhmm(captured, now)
    text = _compose(role, mins, pct, reset_hhmm)
    owner = None
    try:
        import watchdog
        from notify import stream_redirect
        owner = stream_redirect(watchdog.pane_owner(pid, run)) or None
    except Exception:  # noqa: BLE001 — owner routing best-effort (send_fn defaults)
        owner = None
    status = send_fn(text, owner=owner,
                     dedup_key="gkstall:%s:%d" % (sid, int(anchor)),
                     dry_run=dry_run)
    # Latch ONLY on a delivered status so a failed send retries next sweep.
    if status in (None, "sent", "dedup", "dry-run", "suppressed"):
        if isinstance(rec, dict):
            rec[_ALERT_KEY] = True
        logs.append("gk-stall %s -> owner notice sent (%d min, %s) [gkstall:%s:%d]"
                    % (loc, mins, role, sid, int(anchor)))
        return logs
    logs.append("gk-stall %s -> owner notice send FAILED (%s) — retry next sweep"
                % (loc, status))
    return logs
