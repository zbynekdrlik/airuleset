"""watchdog/burn_jobs.py -- hourly burn/usage bookkeeping jobs: local
snapshot (job 13), fleet merge (job 16), fleet budget alert (job 19), and
the credential-store TTL sweep (job 29) (#433, module split cluster B).

WHY THIS FILE EXISTS. Extracted VERBATIM (a MOVE, not a rewrite -- same
discipline as #404's `watchdog/usage.py`) from `watchdog/__init__.py` as
part of #433's continuation of #404's per-service module split: the
"hourly burn/vault housekeeping" concern -- writing this host's own hourly
$/msgs/avg-context row (`burn_snapshot_job`), merging every managed box's
row into one fleet-wide row plus a budget-pace alert
(`fleet_burn_job`/`burn_alert_job`), and sweeping expired stored
credentials (`vault_purge_job`) -- is a self-contained cluster with the
next-lowest fan-in after cluster A (`watchdog/usage.py`): every one of
these four jobs is called from exactly ONE place, `run_once()`'s own job
dispatch, and none of them is called BY any other watchdog job.

Re-exported from `watchdog/__init__.py` (`from watchdog.burn_jobs import
...`, placed after every symbol these jobs depend on is already defined --
none of them depend on anything else in `watchdog/__init__.py` at all,
only stdlib + the top-level `burn` package imported locally inside each
job function, exactly as it was before the move) so every existing caller
(`run_once()`'s jobs 13/16/19/29, calling these as bare module-global
names, and the test suite's `watchdog.<name>` / `wd.<name>` attribute
access) needs zero changes beyond import-path updates where a test
patched one of these names or `_env_num` directly rather than via a
`watchdog.<name>` dotted access. Same facade pattern as
`watchdog/usage.py` (#404 cluster A): thin re-export block, `X as X`
syntax for ruff, own module docstring, zero behavior change.
"""

import datetime
import json
import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Job 13 — HOURLY BURN SNAPSHOT (#37 follow-up, 2026-07-25). The user's
# standing directive: change things one step at a time and measure hourly
# whether it got better or worse, AUTOMATICALLY — he must not have to check
# anything himself. Once per hour, append this host's $/msgs/avg-context/
# by-model row for the PREVIOUS full hour to `burn-history/snapshots.jsonl`
# — the raw feed `airuleset.py burn --compare` reads. Reuses
# `burn.hourly_snapshot()` (itself built on `burn.scan()`'s existing
# per-line parser) — no duplicate transcript parsing anywhere in this path.
# --------------------------------------------------------------------------- #


def burn_snapshot_job(now, state, snapshot_path=None, transcripts_root=None,
                      host=None, user=None, dry_run=False, usage_cache_path=None):
    """Job 13 — see the section comment. Guarded by `state['burn_snapshot_hour']`
    so the 60s sweep cadence writes AT MOST once per UTC-epoch hour, no matter
    how many times this fires inside that hour. `dry_run`: compute + log, but
    never write the file or claim the hour (so a later real sweep still
    writes it). Best-effort: exceptions are the caller's (run_once's)
    responsibility to catch, same as every other job.

    `usage_cache_path` (#269) is a thin pass-through to
    `burn.hourly_snapshot()`'s own param (which stamps `account_email` onto
    the row) — unset (the production default, `cmd_watchdog`'s real wiring)
    means the real local `~/.claude/airuleset-usage-cache.json`; tests pass
    an isolated path so they never read this box's own real cache file."""
    import burn as burn_mod
    hour_bucket = int(now // 3600)
    if state.get("burn_snapshot_hour") == hour_bucket:
        return []
    now_dt = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    row = burn_mod.hourly_snapshot(now_dt, root=transcripts_root, host=host, user=user,
                                   usage_cache_path=usage_cache_path)
    if dry_run:
        return ["[dry-run] burn-snapshot %s $%.2f %d msgs avg_ctx=%d (not written)"
               % (row["host"], row["usd"], row["msgs"], row["avg_ctx"])]
    path = Path(snapshot_path or burn_mod.snapshots_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    state["burn_snapshot_hour"] = hour_bucket
    return ["burn-snapshot %s $%.2f %d msgs avg_ctx=%d -> %s"
           % (row["host"], row["usd"], row["msgs"], row["avg_ctx"], path)]


# --------------------------------------------------------------------------- #
# Job 29 — HOURLY CREDENTIAL-STORE SWEEP (#144). `airuleset.py secret` stores a
# credential 0600 under ~/.claude/secrets/ with a TTL, but the only thing that
# ever enforced that TTL was the next `secret` invocation — so the normal
# one-off shape (request, exec, never run it again) left the value on disk
# indefinitely, which is precisely the property the channel exists to provide.
# This box already runs a sweep every 60s; expiry belongs here rather than in a
# CLI nobody is obliged to call again. Detection-and-delete only: no keystrokes,
# no pings, nothing to a pane.
# --------------------------------------------------------------------------- #


def vault_purge_job(now, state, purge_fn=None, backstop_fn=None, dry_run=False):
    """Job 29 — delete every stored credential past its TTL, at most hourly,
    AND (#529) flag a credential whose promised durable copy never landed.

    `purge_fn` is injected (cmd_watchdog passes `filedrop.vault.purge`) so the
    job never imports a store path in a test, and so an existing caller that
    knows nothing about it sees no behavior change — the same "wired = on"
    convention as jobs 3/7/8/11/13. `backstop_fn` (cmd_watchdog passes
    `filedrop.vault.durable_backstop`) is the same-shape opt-in for the
    durable-persistence artifact-gate.

    THE GRANULARITY IS THE HONEST TTL (#153 finding 3). The gate is an HOUR
    bucket, so a value whose `keep` is SHORTER than an hour outlives its own
    expiry: at the 60s minimum it can sit on disk for up to ~1h before this
    sweep reaches it. The CLI's opportunistic `purge()` shortens that only when
    someone happens to run `airuleset.py secret` in the meantime, which for the
    one-off shape of this feature is usually never. The guarantee is "a value
    does not lie on disk indefinitely" — never "it is gone the second its TTL
    passes". Anything needing the stricter property must call `secret forget`.

    THE BACKSTOP (#529) is the #134 artifact-gate for "delivery without
    persistence": a credential requested with `--persist` whose durable
    ~/.secrets/<name> file never materialised (paste-time persist failed, or
    the file was deleted). It is a PURE READ (`backstop_fn` never opens the
    credential value), so it runs even in a dry run — it mutates nothing — and
    it re-fires every hour while the file is missing (never a one-shot latch),
    so a diagnostic dry run can never silence it (contrast the #516 trap).
    Shares this job's hour gate: the artifact check is slow-changing, so hourly
    is plenty.
    """
    if purge_fn is None:
        return []
    hour_bucket = int(now // 3600)
    if state.get("vault_purge_hour") == hour_bucket:
        return []
    logs = []
    if backstop_fn is not None:
        try:
            missing = backstop_fn() or []
        except Exception:               # best-effort telemetry, same as any job
            missing = []
        for name, path in missing:
            logs.append(
                "vault-durable MISSING: %s promised %s but no file landed "
                "(delivery without persistence)" % (name, path))
    if dry_run:
        logs.append("[dry-run] vault-purge (not swept)")
        return logs
    gone = purge_fn() or []
    state["vault_purge_hour"] = hour_bucket
    if gone:
        logs.append("vault-purge expired %d: %s" % (len(gone), ", ".join(gone)))
    return logs


# --------------------------------------------------------------------------- #
# Job 16 — HOURLY FLEET BURN (#55, 2026-07-25 follow-up to job 13). The
# user's ask: "zacat aj v hodinovych intervaloch vyhodnocovat stav spotreby
# tokenov cez monitorovanu sadu claude targetov" — job 13 above only ever
# measures THIS box. This job runs ONLY on the coordinator (cmd_watchdog
# wires `fleet_fetch` ONLY when `os.uname().nodename == "dev1"` — every OTHER
# managed box already writes ITS OWN hourly row via job 13, so this job just
# TAILS each box's already-written `snapshots.jsonl` over ssh
# (`airuleset._watchdog_fleet_fetch`, injected as `fleet_fetch` — never
# re-scans transcripts remotely) and merges them into ONE combined
# `~/.claude/burn-history/fleet.jsonl` row per hour (`burn.merge_fleet_row`).
# When the observed weekly-%/day pace exceeds the budget implied by the
# watchdog's own usage cache (`burn.fleet_budget_alert`), fires ONE deduped
# Discord ping — never spam, at most once per hour_bucket.
#
# #60 follow-up (2026-07-25): the fetch is now HOUR-MATCHED — `fetch(hosts,
# hour_bucket)` passes the SAME epoch-hour bucket this job itself uses for
# its own once-per-hour guard, so `_fleet_remote_row` can reject a remote's
# stale tail line instead of silently counting it twice. And the job now
# WAITS until `FLEET_BURN_DELAY_MINUTES` past the hour boundary before doing
# any collection at all — at HH:00 a remote's OWN job 13 may simply not have
# written this hour's row YET, which would otherwise make "missing sample"
# the NORMAL state on every collection.
# --------------------------------------------------------------------------- #

FLEET_BURN_DELAY_MINUTES = 5


def fleet_burn_job(now, state, hosts, send_fn, fetch=None, local_snapshot_path=None,
                   fleet_path=None, usage_cache=None, owner=None, dry_run=False):
    """Job 16 — see the section comment. Guarded by `state['fleet_burn_hour']`,
    the SAME at-most-once-per-UTC-hour convention job 13 uses, PLUS a wait
    until `FLEET_BURN_DELAY_MINUTES` past the hour boundary (#60 point 4) —
    before that, this cycle no-ops WITHOUT claiming the hour, so the next
    sweep (60s later) retries until a remote box's own job 13 has had time to
    write. `fetch(hosts, want_hour_bucket)` is the INJECTED remote collector
    (real impl: `airuleset._watchdog_fleet_fetch`) — hour-matched against the
    LAST COMPLETED hour (#63; see `want_hour_bucket` below), so a failing OR
    stale host must come back as `{"error": ...}` in its own slot, never
    raise; a fetch that DOES raise is caught here too so one broken collector
    never drops the whole row (still writes local-only data). `dry_run`:
    compute + log, but never write the file, claim the hour, or send the
    budget alert — mirrors `burn_snapshot_job`'s dry-run contract exactly.

    #63: job 13 (`burn_snapshot_job`) stamps its row with the hour that JUST
    completed (`bucket(now) - 1`), never the current still-open one — so this
    job must collect against that SAME completed-hour bucket
    (`want_hour_bucket = hour_bucket - 1`), for EVERY host including dev1's
    own local `snapshots.jsonl` tail row (previously trusted unconditionally,
    with no freshness check at all — the asymmetry behind "dev1 always has a
    number, every remote column is permanently --"). `hour_bucket` itself
    stays the CURRENT hour purely as the once-per-hour state guard (unchanged
    from #60/#55) — it is never used to select data."""
    import burn as burn_mod
    hour_bucket = int(now // 3600)
    if state.get("fleet_burn_hour") == hour_bucket:
        return []
    now_utc = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    if now_utc.minute < FLEET_BURN_DELAY_MINUTES:
        return []
    want_hour_bucket = hour_bucket - 1
    host_rows = {}
    local_rows = burn_mod.load_snapshots(local_snapshot_path)
    if local_rows:
        last = local_rows[-1]
        name = last.get("host") or "dev1"
        if burn_mod.hour_bucket_of_ts(last.get("ts")) == want_hour_bucket:
            host_rows[name] = last
        else:
            host_rows[name] = {"error": "no local sample for hour %s (latest %s)"
                                        % (want_hour_bucket, last.get("ts")),
                               "stale": True}
    fetch = fetch or (lambda hs, hb: {})
    try:
        remote_rows = fetch(hosts, want_hour_bucket) or {}
    except Exception as e:
        remote_rows = {h.get("name", "?"): {"error": "fetch raised: %r" % (e,)}
                       for h in (hosts or [])}
    host_rows.update(remote_rows)
    completed_hour_utc = datetime.datetime.fromtimestamp(
        want_hour_bucket * 3600, datetime.timezone.utc)
    ts = completed_hour_utc.astimezone().isoformat()
    cache = usage_cache if usage_cache is not None else burn_mod.load_usage_cache()
    wk = burn_mod.shared_weekly_window(cache) if cache else None
    weekly_pct, resets_at = wk if wk else (None, None)
    row = burn_mod.merge_fleet_row(ts, host_rows, weekly_pct=weekly_pct, resets_at=resets_at)
    if dry_run:
        return ["[dry-run] fleet-burn ts=%s total=$%.2f hosts=%d (not written)"
               % (ts, row["total_usd"], len(host_rows))]
    path = Path(fleet_path or burn_mod.fleet_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    state["fleet_burn_hour"] = hour_bucket
    logs = ["fleet-burn ts=%s total=$%.2f hosts=%d -> %s"
           % (ts, row["total_usd"], len(host_rows), path)]
    all_rows = burn_mod.load_fleet(path)
    alert = burn_mod.fleet_budget_alert(
        all_rows, cache,
        now=datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    if alert:
        status = send_fn(alert["message"], owner=owner,
                         dedup_key="fleet-burn-budget:%d" % hour_bucket, dry_run=dry_run)
        logs.append("fleet-budget-alert -> %s" % status)
    return logs


# --------------------------------------------------------------------------- #
# Job 19 -- HOURLY BURN ALERT (#81, 2026-07-26 follow-up to job 16). Job 16
# above only ever WRITES the merged hourly fleet row; nothing ever LOOKS at
# it against a reference and pings on its own -- "the only thing that does
# that today is remembering to check, and exactly during an incident, when
# spend spikes most, there's no time to remember" (the ticket's own words).
# Runs right after job 16 in run_once, on the SAME dev1-only coordinator
# gate (cmd_watchdog computes it, this module stays host-agnostic, mirroring
# job 16's own convention) -- every other managed box never writes
# fleet.jsonl at all, so the job would simply see an empty file there.
#
# Plain JSONL read + comparison (`burn.hourly_burn_alert`) + one Discord
# POST -- no agent, no model, so it survives the operator being busy
# fighting whatever incident is actually driving the spend up.
#
# #546 (owner directive 2026-08-18): token-BURN / spend-budget is subscription
# monitoring another project now owns -- airuleset does not Discord-alert on it.
# So the `send_fn` POST below (job 19 `burn-alert:` AND job 16
# `fleet-burn-budget:`) is SUPPRESSED at `notify.send()` (SUPPRESSED_ALERT_
# PREFIXES): it returns "suppressed", posts nothing, and logs a machine-channel
# decision. The evaluation logic is unchanged (still runs, still journals) --
# only the phone ping is dropped.
# --------------------------------------------------------------------------- #

def _env_num(name, default, cast=float):
    """Resolve one env-overridable numeric threshold, falling back to
    `default` on a missing or unparsable value. Shared by every
    `AIRULESET_BURN_ALERT_*` threshold below -- avoids four near-identical
    try/except blocks."""
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def burn_alert_job(now, state, send_fn, fleet_path=None, owner=None,
                   dry_run=False, abs_usd=None, rel_mult=None,
                   rel_window=None, weekly_step_pct=None):
    """Job 19 -- see the section comment. Reads the CURRENT latest row of
    `fleet.jsonl` (job 16's merge, run immediately before this in
    `run_once`) -- if that row's hour bucket was already evaluated
    (`state['burn_alert_hour']`), this is a no-op, the SAME at-most-once-
    per-hour convention jobs 13/16 already use ("druhe spustenie v tej
    istej hodine neposle nic"). Otherwise claims the bucket and checks it
    via `burn.hourly_burn_alert`; a triggered hour sends ONE combined
    Discord ping, deduped a SECOND time via `send_fn`'s own `dedup_key`
    (mirrors job 16's `fleet-burn-budget` dedup) so a lost/reset `state`
    can never double-post either. A quiet hour still claims the bucket
    (never re-evaluated) and sends nothing -- "ticha hodina neposiela
    nic". `dry_run`: compute + log, but never claim the hour or send
    (mirrors `fleet_burn_job`'s own dry-run contract exactly). Best-
    effort: exceptions are the caller's (run_once's) responsibility to
    catch, same as every other job."""
    import burn as burn_mod
    fleet_path = fleet_path or burn_mod.fleet_path()
    rows = burn_mod.load_fleet(fleet_path)
    if not rows:
        return []
    hb = burn_mod.hour_bucket_of_ts(rows[-1].get("ts"))
    if hb is None or state.get("burn_alert_hour") == hb:
        return []
    if abs_usd is None:
        abs_usd = _env_num("AIRULESET_BURN_ALERT_ABS_USD", burn_mod.BURN_ALERT_ABS_USD)
    if rel_mult is None:
        rel_mult = _env_num("AIRULESET_BURN_ALERT_REL_MULT", burn_mod.BURN_ALERT_REL_MULT)
    if rel_window is None:
        rel_window = _env_num("AIRULESET_BURN_ALERT_REL_WINDOW",
                              burn_mod.BURN_ALERT_REL_WINDOW, cast=int)
    if weekly_step_pct is None:
        weekly_step_pct = _env_num("AIRULESET_BURN_ALERT_WEEKLY_STEP_PCT",
                                   burn_mod.BURN_ALERT_WEEKLY_STEP_PCT)
    alert = burn_mod.hourly_burn_alert(rows, abs_usd=abs_usd, rel_mult=rel_mult,
                                       rel_window=rel_window,
                                       weekly_step_pct=weekly_step_pct)
    if dry_run:
        return ["[dry-run] burn-alert hour=%s %s (not claimed, not sent)"
               % (hb, "TRIGGERED" if alert else "quiet")]
    state["burn_alert_hour"] = hb
    if not alert:
        return ["burn-alert hour=%s quiet" % hb]
    status = send_fn(alert["message"], owner=owner,
                     dedup_key="burn-alert:%d" % hb, dry_run=dry_run)
    return ["burn-alert hour=%s TRIGGERED (%s) -> %s"
           % (hb, "; ".join(alert["reasons"]), status)]
