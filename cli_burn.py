"""#433 cluster J — the burn/fable-gate/delegation CLI cluster, extracted from
airuleset.py (verbatim move + facade, the same shape as cli_vault.py (H) /
cli_autopilot_lock.py (K)). Holds `airuleset.py burn`/`delegation`/`fable-gate`
and watchdog job-16's fleet-fetch collector — user-facing/injected code, no
watchdog jobs of its own.

A self-contained LEAF: it imports only stdlib (`json`/`os`/`sys` here at module
top; `subprocess`/`datetime` locally inside the function bodies that need them,
preserved verbatim) plus the external `burn`/`watchdog` packages (also local
imports in the bodies). It deliberately does NOT `import airuleset` at module
level — that would be the CLI-mode partially-initialized-import crash proven for
the H/K splits (airuleset.py runs as `__main__`, so a top-level back-import
re-enters this leaf mid-init). It has TWO deferred outbound couplings, both
call-time (never module-init): (1) the shared `REMOTE_HOSTS` deploy registry
(which stays in airuleset.py, referenced by ~20 other functions there): the 3
functions that need it reach it via a lazily-placed deferred `import airuleset`
(the C/D "new-module-needs-old-module-symbol" technique), scoped to the code
path that actually needs it so the ~210 ms second-execution cost fires only on
the rare `--host` CLI paths and never in production (fleet_burn_job always
passes `hosts` explicitly); (2) `cli_remote.host_key_check_opts` (#680), a
deferred `from cli_remote import host_key_check_opts` inside `_remote_ssh_prefix`
so a raw-public-IP burn target (spinbike-vps) is host-key-pinned -- cli_remote
has no top-level `import airuleset`, so this coupling cannot re-enter airuleset
mid-init.
"""
import json
import os
import sys

def cmd_fable_gate(args):
    """Budget gate guarding EVERY automatic Fable dispatch (model-tiering
    policy 2026-08-25, #690 — the judgment-content tier + the airuleset
    Fable-majority; Opus 5 stays banned): exit 0 + `OPEN ...` when the Fable
    weekly + shared weekly windows have headroom (< threshold, default 90% /
    AIRULESET_FABLE_GATE_PCT — raised from 80 by #690), exit 1 + `CLOSED ...`
    otherwise (incl. missing/stale cache — fail-safe: no blind Fable burn).
    The orchestrator / autopilot supervisor runs this ONCE per qualifying
    task/batch before dispatching `model: fable`; CLOSED → the same work runs
    on claude-opus-4-8 (agent-definition frontmatter / Workflow opts.model
    full id / inheritance — never the banned bare alias)."""
    from watchdog import fable_gate
    ok, reason = fable_gate(threshold=getattr(args, "threshold", None))
    print(("OPEN " if ok else "CLOSED ") + reason)
    sys.exit(0 if ok else 1)


def _burn_remote_cmd(remote, days):
    """Pure ssh-command builder for a remote `burn` collection — invokes that
    box's OWN already-deployed `airuleset.py burn --json` (the box gets this
    module from the ordinary `push` deploy; never scp'd separately, per
    `deploy-from-clean-tree.md`). Split out from `_burn_remote` so the
    command shape is unit-testable without a real network call."""
    return _remote_ssh_prefix(remote) + [
        f"cd {remote['repo_path']} && python3 airuleset.py burn --json --days {days}"]


def _remote_ssh_prefix(remote):
    """The identity/sshpass selection shared by every remote collection.

    ONE place, so a second collector (`_delegation_remote_cmd`, #130) reuses
    the sanctioned ssh shape byte-for-byte instead of inventing a parallel one
    — `hooks/block-subdev-ssh-misuse.sh` guards exactly this.

    #342: the same per-connection retry-cap hardening a sibling ticket
    already added to `_deploy_to_all_remotes` and
    `provision_subdev_soniox_key()` — BatchMode=yes on the identity branch
    so a failed pubkey attempt against an unprovisioned/misconfigured
    account fails IMMEDIATELY instead of falling through to an interactive
    password/keyboard-interactive retry, and NumberOfPasswordPrompts=1 on
    the sshpass branch so a wrong/unprovisioned password is tried ONCE
    instead of openssh's own default of 3 (sshpass happily re-supplies the
    same password on every re-prompt). Deliberately NOT porting that
    sibling ticket's cross-account "never re-probe a known-bad host this
    run" tracking set here — `_burn_remote`/`_delegation_remote` each open
    EXACTLY ONE connection per host per call and never retry a failed one
    (a `--host all` run still visits every host, but only once each, same
    as a single-host run), so there is no in-process retry-storm shape for
    that tracking set to guard against; and job 16's fleet fetch (the third
    caller, via `_fleet_remote_cmd` below) already gates each host to at
    most one attempt per UTC hour — regardless of whether that attempt
    succeeds or fails, since `fleet_burn_job` claims the hour unconditionally
    once its `fetch()` returns — spreading any retries comfortably inside a
    typical fail2ban findtime window without new state."""
    # #680: route the inline StrictHostKeyChecking=no through the #669 pin
    # helper (the ONE source) -- a raw-public-IP target carrying a committed
    # host_keys pin (spinbike-vps) is verified STRICTLY; every tailscale/subdev
    # host keeps the unchanged =no. Deferred import (call-time, not module-init)
    # so cli_burn's leaf-with-no-top-level-airuleset invariant is preserved --
    # cli_remote is fully imported by the time any burn ssh runs. Spliced BEFORE
    # BatchMode/NumberOfPasswordPrompts (distinct options, but keeps the pin
    # first per #669's first-value-wins guidance).
    from cli_remote import host_key_check_opts
    hostkey_opts = host_key_check_opts(remote)
    identity = remote.get("identity")
    if identity:
        return (["ssh", "-i", os.path.expanduser(identity)]
                + hostkey_opts
                + ["-o", "BatchMode=yes",
                   f"{remote['user']}@{remote['host']}"])
    return (["sshpass", "-p", "newlevel", "ssh"]
            + hostkey_opts
            + ["-o", "NumberOfPasswordPrompts=1",
               f"{remote['user']}@{remote['host']}"])


def _burn_remote(remote, days):
    """Collect one remote box's burn report over ssh. Fail-safe: any ssh
    error, non-zero exit, or unparsable stdout prints a WARN to stderr and
    returns None — one unreachable box never aborts the whole report."""
    import subprocess
    cmd = _burn_remote_cmd(remote, days)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"  WARN: burn collection failed for {remote['name']}: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  WARN: burn collection failed for {remote['name']}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print(f"  WARN: burn collection returned invalid JSON for {remote['name']}",
              file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# #55 follow-up — fleet-wide hourly collection for the watchdog's job 16
# (fleet_burn_job). Unlike `_burn_remote` above (which re-runs a full
# `airuleset.py burn --json --days N` scan remotely — heavy, and NOT what an
# hourly poll needs), this just TAILS the box's own job-13 output
# (`~/.claude/burn-history/snapshots.jsonl`, already written locally every
# hour by every managed box) — cheap, no remote transcript scanning. Reuses
# the EXACT same identity/sshpass selection as `_burn_remote_cmd` — never
# invent a new ssh shape (hooks/block-subdev-ssh-misuse.sh guards this).
#
# #286 follow-up (2026-08-09) — the SAME ssh round-trip ALSO tails the
# remote's own `~/.claude/airuleset-usage-cache.json` (a marker line, never
# a second connection — #269's own design comment rejected a separate
# second ssh call for exactly the doubled-cost reason), so
# `group_fleet_by_account()` can resolve a real weekly %/reset for EVERY
# reachable account, not just the reporting box's own.
# --------------------------------------------------------------------------- #

# Separates the snapshot-tail output from the usage-cache output in ONE
# combined stdout — chosen to be something no real JSON line or shell
# output would ever legitimately contain.
_FLEET_CACHE_MARKER = "===AIRULESET-FLEET-CACHE==="


def _fleet_remote_cmd(remote):
    """Pure ssh-command builder — split out for unit-testability, mirroring
    `_burn_remote_cmd`'s own split. One ssh call, two commands: the
    pre-existing snapshot tail (byte-identical substring, still asserted
    verbatim by TestFleetRemoteCmd), then the marker, then a best-effort
    cat of the remote's own usage cache (`2>/dev/null || true` — a
    missing/unreadable cache must never make the WHOLE ssh call fail,
    since the snapshot half is still perfectly good data on its own).

    #342: this used to duplicate `_remote_ssh_prefix()`'s identity/sshpass
    branching inline instead of calling it — so this docstring's own claim
    of reusing "the EXACT same identity/sshpass selection" was false, and
    a hardening fix landing on `_remote_ssh_prefix()` alone would silently
    NOT reach job 16's fleet fetch. Calling the shared builder directly
    makes the claim true by construction and guarantees this stays in sync
    with `_burn_remote_cmd`/`_delegation_remote_cmd` automatically.
    (Merge note, #342+#286: the #286 combined-command extension above rides
    the SAME shared builder — the extended remote_cmd string is the one
    argument appended after the shared prefix.)"""
    remote_cmd = (
        "tail -n 1 ~/.claude/burn-history/snapshots.jsonl; "
        "echo '" + _FLEET_CACHE_MARKER + "'; "
        "cat ~/.claude/airuleset-usage-cache.json 2>/dev/null || true"
    )
    return _remote_ssh_prefix(remote) + [remote_cmd]


def _hour_bucket_of_ts(ts_str):
    """Epoch-hour bucket (`int(epoch_seconds // 3600)`) of an ISO-8601
    timestamp STRING, converted to UTC first — comparing raw hour-of-day
    digits (or the raw string) across differing UTC offsets is exactly the
    #60 bug (gk writes `+00:00`, dev1 `+02:00` — the SAME instant renders
    with different hour digits in each). None when `ts_str` is missing,
    None, or unparsable — the caller (`_fleet_remote_row`) treats that as
    "can't verify freshness" and errors rather than trusting it.

    Thin wrapper — the canonical implementation is `burn.hour_bucket_of_ts`
    (#63: shared with `watchdog.fleet_burn_job`'s own local-row freshness
    check, so the convention can never drift between the two call sites)."""
    import burn as burn_mod
    return burn_mod.hour_bucket_of_ts(ts_str)


def _parse_fleet_cache_section(text):
    """Best-effort parse of the `_FLEET_CACHE_MARKER`-delimited usage-cache
    half of a `_fleet_remote_row` ssh reply — mirrors `hourly_snapshot()`'s
    own degrade-to-None convention for a missing/malformed local cache read
    (never blocks, never crashes, never guesses). `None` on an empty
    section (no marker in stdout at all — every pre-#286 fixture/caller),
    unparsable JSON, or a JSON value that parses but isn't an object (the
    SAME "valid JSON, wrong shape" guard `hourly_snapshot()` already
    applies to its own local cache read)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        cache = json.loads(text)
    except ValueError:
        return None
    if not isinstance(cache, dict):
        return None
    return cache


def _fleet_remote_row(remote, want_hour_bucket, timeout=15):
    """One remote host's latest hourly burn-snapshot row FOR THE SPECIFIC
    `want_hour_bucket` (an epoch-hour index — see `_hour_bucket_of_ts`), or
    `{"error": ...}` on ANY failure: ssh, timeout, empty file, bad JSON, OR
    a STALE/mismatched-hour row (#60). The remote's tail line existing does
    NOT mean it is fresh for the hour being collected — the remote may not
    have written this hour's row yet (job 16 now waits until HH:05 to give
    the remote's own job 13 time to write it), or the remote's clock/offset
    may differ from ours (`_hour_bucket_of_ts` always converts to UTC
    before comparing — never the raw string/local hour-of-day). A
    stale/mismatched row is returned as `{"error": ..., "stale": True}` so
    callers (`merge_fleet_row`/`render_fleet`) can render it distinctly
    from a hard collection failure (`—` vs `ERR`) — this IS the #60 fix:
    silently reusing an old row produced a false fleet trend/total (5/6
    hosts double-counting the same stale sample read as "-39.8%
    (lepšie)"). A single unreachable/stale box must never crash the fleet
    job or the rest of the watchdog sweep. Never raises.

    #286: the ssh reply's stdout carries a SECOND section after
    `_FLEET_CACHE_MARKER` — the remote's own usage cache. Parsed
    best-effort ONLY when the snapshot half is fresh (an error/stale row
    already contributes nothing to `group_fleet_by_account`, so there is
    nothing useful to attach weekly data to — and skipping it keeps the
    #60 stale/error contract exactly as strict as before, never softened
    by a cache section happening to be present). On a fresh row: backfills
    `account_email` ONLY when the snapshot row itself is missing it (a
    legacy pre-#269 row — the snapshot's own value always wins when
    present, since it is the more directly-attributable source), and adds
    `weekly_pct`/`resets_at` via `burn.shared_weekly_window()` — the SAME
    account-wide-window selector `fleet_burn_job`/`cmd_burn` already use,
    never a new one. Also carries the cache's OWN `ts` (its write time,
    unix epoch) through as `weekly_ts` — an adversarial review of this
    same #286 branch flagged that `group_fleet_by_account()`'s cross-host
    MAX-percent selection had no way to tell a fresh candidate from a
    stale one (a remote box whose watchdog stopped refreshing its cache
    could otherwise win over a fresher, correct sample from another box on
    the same account); `weekly_ts` is what lets it gate on that."""
    import subprocess
    cmd = _fleet_remote_cmd(remote)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
    if result.returncode != 0:
        return {"error": (result.stderr or "").strip()[:200] or "ssh failed"}
    snap_part, _, cache_part = (result.stdout or "").partition(_FLEET_CACHE_MARKER)
    lines = snap_part.strip().splitlines()
    if not lines:
        return {"error": "no snapshot data yet"}
    try:
        row = json.loads(lines[-1])
    except ValueError:
        return {"error": "invalid JSON from remote"}
    if not isinstance(row, dict):
        return {"error": "unexpected JSON shape from remote"}
    row_hour = _hour_bucket_of_ts(row.get("ts"))
    if row_hour != want_hour_bucket:
        return {"error": "no sample for hour %s (latest %s)" % (want_hour_bucket, row.get("ts")),
               "stale": True}
    cache = _parse_fleet_cache_section(cache_part)
    if cache:
        import burn as burn_mod
        row = dict(row)
        if not row.get("account_email"):
            row["account_email"] = cache.get("account_email") or ""
        wk = burn_mod.shared_weekly_window(cache)
        if wk:
            row["weekly_pct"], row["resets_at"] = wk
            row["weekly_ts"] = cache.get("ts")
    return row


def _watchdog_fleet_fetch(hosts=None, want_hour_bucket=None):
    """Real remote collector used by cmd_watchdog's job 16 wiring — one row
    per REMOTE_HOSTS entry, hour-matched against `want_hour_bucket` (#60).
    Defaults to the CURRENT UTC epoch-hour when not given — the plain
    top-level/manual-invocation case; `fleet_burn_job` always passes its own
    `now`-derived bucket explicitly (this repo's convention of threading
    `now` through every job for determinism/testability — see
    `_fleet_remote_row`). Never raises; a single bad or stale host degrades
    to `{"error": ...}` in its own slot rather than dropping the whole
    fleet."""
    # #433 cluster J: REMOTE_HOSTS is a shared deploy registry that stays in
    # airuleset.py; reach it via a deferred import (never a module-top
    # back-import — CLI-mode partial-init crash). #537: `_deployable_hosts()`
    # both resolves the `hosts is None` default (to REMOTE_HOSTS) AND filters
    # out `"pending": True` rename targets — a pending account does NOT exist
    # on the box, so ssh'ing it in this HOURLY fleet-burn (watchdog job 16) is
    # a fail2ban strike (#341/#300/#326), the exact hazard the pending flag
    # exists to prevent. The import is no longer lazy, but airuleset is already
    # resident here in BOTH real call contexts (run_once's own `import
    # airuleset` in the watchdog path; cmd_burn/cmd_delegation import it too),
    # so no second-execution cost is added.
    import airuleset
    hosts = airuleset._deployable_hosts(hosts)
    if want_hour_bucket is None:
        import datetime
        want_hour_bucket = int(datetime.datetime.now(datetime.timezone.utc).timestamp() // 3600)
    return {h["name"]: _fleet_remote_row(h, want_hour_bucket) for h in hosts}


def cmd_burn(args):
    """Token-spend report from local transcripts — the measurement behind the
    2026-07-25 cost-fix package (Opus-5-default MANAGED_MODEL, this
    diagnostic, the statusline context/cost segment): ~$13,600 across all 6
    managed boxes over 8 days, 76% Fable 5 running as MAIN (not advisor), 92%
    of that in input context. The local box is always included; `--host
    <name>` (or `--host all`) also collects a remote box over ssh by
    invoking ITS OWN deployed `airuleset.py burn --json` — never scp (the
    clean-tree hook would block it anyway).

    `--mark "<text>"` / `--compare` are the follow-up AUTOMATIC feedback
    loop (#37): `--mark` records that a change was made NOW (or at
    `--mark-ts <iso>` for backdating from a known event, e.g. a git commit
    timestamp) to `~/.claude/burn-history/changes.jsonl`; `--compare` reads
    that alongside the watchdog's hourly `snapshots.jsonl` (AND, when
    present, the fleet-wide `fleet.jsonl` — #55 point D) and prints, per
    change, the mean $/h, avg context and msgs/h in `--window` hours (default
    6) before vs after it — so the user never has to check anything himself,
    the report just tells him whether a change made things better or worse.

    `--fleet [--hours N]` (#55) prints the monitored-fleet hourly report:
    per-host + total $ for the last N hours (default 24), the trend (latest
    hour vs mean of the previous 3), and a sustainability verdict against the
    watchdog's weekly usage-cache budget. The fleet.jsonl feed is written by
    watchdog job 16 (`fleet_burn_job`), coordinator-only (dev1)."""
    import burn
    if getattr(args, "mark", None):
        ts = None
        mark_ts = getattr(args, "mark_ts", None)
        if mark_ts:
            import datetime
            ts = datetime.datetime.fromisoformat(mark_ts)
        path = burn.mark_change(args.mark, now=ts)
        print("Marked: %s -> %s" % (args.mark, path))
        return
    if getattr(args, "compare", False):
        window = getattr(args, "window", None) or 6
        changes = burn.load_changes()
        results = burn.compare_changes(burn.load_snapshots(), changes, window_hours=window)
        fleet_rows = burn.load_fleet()
        fleet_results = None
        if fleet_rows:
            fleet_results = burn.compare_changes(burn.fleet_compare_rows(fleet_rows),
                                                 changes, window_hours=window)
        print(burn.render_compare(results, window_hours=window, fleet_results=fleet_results))
        return
    if getattr(args, "fleet", False):
        hours = getattr(args, "hours", None) or 24
        print(burn.render_fleet(burn.load_fleet(), hours=hours, cache=burn.load_usage_cache()))
        return
    days = getattr(args, "days", None) or 7
    reports = [burn.local_report(days=days)]
    host_arg = getattr(args, "host", None)
    if host_arg:
        import airuleset  # #433 cluster J: REMOTE_HOSTS lives in airuleset.py
        # #537: a pending (not-yet-live rename) account doesn't exist, so it is
        # never a valid target — filter it out of `--host all` AND the per-name
        # lookup (a premature `--host montalu1@subdev` falls to the normal
        # "unknown host" path, listing only real choices). fail2ban safety.
        live_hosts = airuleset._deployable_hosts()
        if host_arg == "all":
            targets = live_hosts
        else:
            targets = [h for h in live_hosts if h["name"] == host_arg]
            if not targets:
                names = ", ".join(h["name"] for h in live_hosts)
                print(f"ERROR: unknown --host '{host_arg}' — choices: {names}, all",
                      file=sys.stderr)
                sys.exit(1)
        for remote in targets:
            print(f"Collecting burn from {remote['name']}...", file=sys.stderr)
            rep = _burn_remote(remote, days)
            if rep:
                reports.append(rep)
    combined = burn.merge_reports(reports)
    if getattr(args, "json", False):
        print(json.dumps(combined, indent=1))
    else:
        print(burn.render_human(combined, days))


# --------------------------------------------------------------------------- #
# #130 — `airuleset.py delegation`: the standing MAIN vs SUBAGENT cost meter.
#
# The ruleset's central move is to push work out of the main agent because a
# main turn re-sends the whole conversation. That reasoning has never been
# checked against what the subagents themselves cost, and it could not be:
# `burn.scan()` is structurally blind to subagent transcripts (see the header
# comment on `burn.scan_split`). This is the instrument, not a gate — it
# reports, it never blocks, and it changes no threshold anywhere.
# --------------------------------------------------------------------------- #

def _delegation_remote_cmd(remote, hours):
    """Pure ssh-command builder — invokes the remote box's OWN deployed
    `airuleset.py delegation --json`, exactly as `_burn_remote_cmd` does for
    `burn`, sharing the same identity/sshpass prefix. READ-ONLY on the remote:
    it scans that box's transcripts and prints JSON, writes nothing."""
    return _remote_ssh_prefix(remote) + [
        f"cd {remote['repo_path']} && python3 airuleset.py delegation "
        f"--json --hours {hours}"]


def _delegation_remote(remote, hours):
    """Collect one remote box's split report. Fail-safe like `_burn_remote`:
    any ssh error, non-zero exit or unparsable stdout WARNs and returns None,
    so one unreachable box never aborts the fleet report."""
    import subprocess
    cmd = _delegation_remote_cmd(remote, hours)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"  WARN: delegation collection failed for {remote['name']}: {e}",
              file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  WARN: delegation collection failed for {remote['name']}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print("  WARN: delegation collection returned invalid JSON for "
              f"{remote['name']}", file=sys.stderr)
        return None


def _gh_closed_issues_json(repo):
    import subprocess
    r = subprocess.run(
        ["gh", "issue", "list", "-R", repo, "--state", "closed",
         "--limit", "500", "--json", "number,closedAt"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout


def _closed_ticket_count(repo, start, end, _runner=None):
    """Issues on `repo` closed inside [start, end], or None.

    None — never 0 — when `gh` is unavailable or errors: a fabricated
    zero-ticket denominator would render as "spend with no ticket", which is a
    real and serious finding, and it must never be manufactured by a missing
    tool."""
    import burn
    import datetime
    runner = _runner or _gh_closed_issues_json
    try:
        raw = runner(repo)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = burn._parse_ts(r.get("closedAt"))
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        if start <= t <= end:
            n += 1
    return n


def _attach_ticket_counts(merged, hours, _counter=None):
    """Join closed-ticket counts onto each project row that resolved a repo.

    Opt-in (`--tickets`) because it needs network + `gh` auth the base
    measurement must not depend on. A project whose repo did not resolve keeps
    `closed_tickets: None` and renders no per-ticket line, rather than
    borrowing someone else's denominator."""
    import datetime
    counter = _counter or _closed_ticket_count
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(hours=hours)
    cache = {}
    for row in (merged.get("by_project") or {}).values():
        repo = row.get("repo")
        if not repo:
            continue
        if repo not in cache:
            cache[repo] = counter(repo, start, end)
        row["closed_tickets"] = cache[repo]
        if cache[repo]:
            import burn
            total = row["main"]["units"] + row["sub"]["units"]
            row["units_per_ticket"] = burn.units_per_ticket(total, cache[repo])
    return merged


def cmd_delegation(args):
    """Per-box, per-project MAIN vs SUBAGENT token attribution over a window.

    Reports turns, the four token sums, a weighted (relative, never a price)
    cost unit, mean context per turn, and — with `--tickets` — cost per closed
    ticket, for MAIN and SUBAGENT separately. `--host <name>` / `--host all`
    also collects the remote fleet over ssh via each box's own deployed copy.
    """
    import burn
    hours = getattr(args, "hours", None) or 12
    root = getattr(args, "root", None)
    if getattr(args, "floor", False):
        # #131 — a DIFFERENT question from the standing meter's, so it gets its
        # own report rather than extra columns on the by-project table: this one
        # is per dispatch and local-only (a remote box's split is already
        # folded per project and cannot be decomposed back into dispatches).
        rep = burn.scan_dispatches(root or os.path.expanduser(
            "~/.claude/projects"), hours=hours)
        if getattr(args, "json", False):
            print(json.dumps(rep, indent=1))
        else:
            print(burn.render_floor(rep, hours=hours))
        return
    reports = [burn.split_report(hours=hours, root=root)]
    host_arg = getattr(args, "host", None)
    if host_arg:
        import airuleset  # #433 cluster J: REMOTE_HOSTS lives in airuleset.py
        # #537: a pending (not-yet-live rename) account doesn't exist, so it is
        # never a valid target — filter it out of `--host all` AND the per-name
        # lookup (a premature `--host montalu1@subdev` falls to the normal
        # "unknown host" path, listing only real choices). fail2ban safety.
        live_hosts = airuleset._deployable_hosts()
        if host_arg == "all":
            targets = live_hosts
        else:
            targets = [h for h in live_hosts if h["name"] == host_arg]
            if not targets:
                names = ", ".join(h["name"] for h in live_hosts)
                print(f"ERROR: unknown --host '{host_arg}' — choices: {names}, all",
                      file=sys.stderr)
                sys.exit(1)
        for remote in targets:
            print(f"Collecting delegation split from {remote['name']}...",
                  file=sys.stderr)
            rep = _delegation_remote(remote, hours)
            if rep:
                reports.append(rep)
    merged = burn.merge_splits(reports)
    if getattr(args, "tickets", False):
        _attach_ticket_counts(merged, hours)
    if getattr(args, "json", False):
        print(json.dumps(merged, indent=1))
    else:
        print(burn.render_split(merged, hours=hours))
