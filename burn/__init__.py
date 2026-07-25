"""Token-spend attribution — `airuleset.py burn`.

Ported from a scratch analyzer that measured the 2026-07 burn behind the
whole cost-fix package (#37): ~$13,600 across all 6 managed boxes over 8
days, 76% Fable 5 (running as the MAIN session model, not the advisor shape
the rules mandate), 92% of that spend in input context (cache read + cache
write) vs 8% output. See `modules/core/model-awareness.md` for the pricing
table this module mirrors and the Opus-5-era default-tier policy this
measurement justified (`MANAGED_MODEL` in airuleset.py).

Walks `~/.claude/projects/*/*.jsonl` (Claude Code's own transcript store),
parses each assistant `message.usage` entry, and aggregates by model / day /
project / main-vs-sidechain. stdlib only — no deps, so a report can be piped
back from a remote box by invoking that box's own already-deployed
`airuleset.py burn --json` over ssh (see `airuleset._burn_remote`).
"""
import datetime
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

# per-Mtok (input, cache_write, cache_read, output) — Opus-5-era pricing, the
# SAME table `modules/core/model-awareness.md` documents. `cache_write` here
# is the 5-min rate; Opus's 1-hour $10/Mtok rate isn't separately tracked
# (transcripts don't record which TTL a given write used).
PRICE = {
    "fable": (10.0, 12.5, 1.0, 50.0),
    "opus": (5.0, 6.25, 0.5, 25.0),
    "sonnet": (2.0, 2.5, 0.2, 10.0),
    "haiku": (1.0, 1.25, 0.1, 5.0),
}


def tier(model):
    """Map a `message.model` id (e.g. `claude-opus-5[1m]`) to a PRICE key, or
    'other' when unrecognized (never crashes on an unknown/foreign model)."""
    m = (model or "").lower()
    for k in PRICE:
        if k in m:
            return k
    return "other"


def _empty_row():
    return [0, 0, 0, 0, 0.0, 0]   # in, cache_w, cache_r, out, usd, msgs


def _dump(d, top=None):
    items = sorted(d.items(), key=lambda kv: -kv[1][4])
    if top:
        items = items[:top]
    return {k: {"in": v[0], "cache_w": v[1], "cache_r": v[2], "out": v[3],
                "usd": round(v[4], 2), "msgs": v[5]} for k, v in items}


def scan(root, days=7, now=None):
    """Walk `root/*/*.jsonl` and aggregate assistant-message usage. Returns
    the printable {in, cache_w, cache_r, out, usd, msgs} shape per bucket,
    grouped by model / day / project / main-vs-sidechain. A file whose mtime
    is older than the cutoff is skipped WITHOUT being opened (cheap on a
    directory with years of transcripts); a line without `usage` or with an
    unparsable timestamp is skipped."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    agg = defaultdict(_empty_row)
    by_day = defaultdict(_empty_row)
    by_proj = defaultdict(_empty_row)
    by_day_model = defaultdict(_empty_row)
    by_hour = defaultdict(_empty_row)
    by_hour_model = defaultdict(_empty_row)
    side = defaultdict(_empty_row)
    files = 0
    lines = 0
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
        try:
            mt = datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc)
            if mt < cutoff:
                continue
        except OSError:
            continue
        files += 1
        proj = os.path.basename(os.path.dirname(path))
        try:
            fh = open(path, "r", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                msg = e.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                lines += 1
                ts = e.get("timestamp") or ""
                try:
                    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t < cutoff:
                    continue
                model = msg.get("model") or "?"
                tr = tier(model)
                i = int(u.get("input_tokens") or 0)
                cw = int(u.get("cache_creation_input_tokens") or 0)
                cr = int(u.get("cache_read_input_tokens") or 0)
                o = int(u.get("output_tokens") or 0)
                p = PRICE.get(tr)
                usd = (i * p[0] + cw * p[1] + cr * p[2] + o * p[3]) / 1e6 if p else 0.0
                local_t = t.astimezone()
                day = local_t.strftime("%Y-%m-%d")
                hour = local_t.strftime("%Y-%m-%dT%H:00")
                sc = bool(e.get("isSidechain"))
                for d, k in (
                    (agg, model), (by_day, day), (by_proj, proj),
                    (by_day_model, day + "|" + tr),
                    (by_hour, hour), (by_hour_model, hour + "|" + model),
                    (side, ("sidechain" if sc else "main") + "|" + tr),
                ):
                    r = d[k]
                    r[0] += i
                    r[1] += cw
                    r[2] += cr
                    r[3] += o
                    r[4] += usd
                    r[5] += 1
    return {
        "files_scanned": files,
        "usage_lines": lines,
        "days": days,
        "by_model": _dump(agg),
        "by_day": dict(sorted(_dump(by_day).items())),
        "by_day_tier": dict(sorted(_dump(by_day_model).items())),
        "by_hour": dict(sorted(_dump(by_hour).items())),
        "by_hour_model": dict(sorted(_dump(by_hour_model).items())),
        "by_project": _dump(by_proj, top=12),
        "main_vs_sidechain": _dump(side),
    }


def local_report(days=7, root=None):
    """`scan()` over THIS box's own transcript store, tagged with host/user
    so a multi-host `merge_reports()` can attribute spend per box."""
    root = root or os.path.expanduser("~/.claude/projects")
    data = scan(root, days=days)
    data["host"] = os.uname().nodename
    data["user"] = os.environ.get("USER", "?")
    return data


def _blank_row():
    return {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0, "usd": 0.0, "msgs": 0}


def _add_row(dst, src):
    for k in ("in", "cache_w", "cache_r", "out", "msgs"):
        dst[k] += src.get(k, 0)
    dst["usd"] += src.get("usd", 0.0)


def merge_reports(reports):
    """Merge N per-host `local_report()`-shaped reports into one combined
    aggregate + a `by_host` breakdown. `by_project` keys are prefixed
    `<host>:<project>` so the same project name on two boxes doesn't collide."""
    combined = {
        "by_model": defaultdict(_blank_row),
        "by_day": defaultdict(_blank_row),
        "by_project": defaultdict(_blank_row),
        "by_host": defaultdict(_blank_row),
        "files_scanned": 0,
        "usage_lines": 0,
    }
    for rep in reports:
        host = rep.get("host") or "?"
        combined["files_scanned"] += rep.get("files_scanned", 0)
        combined["usage_lines"] += rep.get("usage_lines", 0)
        for k, v in (rep.get("by_model") or {}).items():
            _add_row(combined["by_model"][k], v)
            _add_row(combined["by_host"][host], v)
        for k, v in (rep.get("by_day") or {}).items():
            _add_row(combined["by_day"][k], v)
        for k, v in (rep.get("by_project") or {}).items():
            _add_row(combined["by_project"][host + ":" + k], v)
    for bucket in ("by_model", "by_day", "by_project", "by_host"):
        combined[bucket] = {
            k: {**v, "usd": round(v["usd"], 2)}
            for k, v in sorted(combined[bucket].items(), key=lambda kv: -kv[1]["usd"])
        }
    return combined


def _fmt_int(n):
    return format(int(round(n)), ",")


def render_human(combined, days):
    """Compact human table: total, per-model with % share, per-host (only
    when >1 host), per-day, top projects, and the single most diagnostic
    number — avg context tokens (cache read+write) per assistant message."""
    lines = []
    total_usd = sum(v["usd"] for v in combined["by_model"].values())
    total_msgs = sum(v["msgs"] for v in combined["by_model"].values())
    lines.append("airuleset burn -- last %d day(s)" % days)
    lines.append("  total: $%.2f across %d assistant messages (%d transcript files)"
                  % (total_usd, total_msgs, combined["files_scanned"]))
    lines.append("")
    lines.append("  by model:")
    for model, v in combined["by_model"].items():
        pct = (v["usd"] / total_usd * 100) if total_usd else 0.0
        lines.append("    %-28s $%9.2f  (%5.1f%%)  %6d msgs"
                      % (model, v["usd"], pct, v["msgs"]))
    if len(combined.get("by_host") or {}) > 1:
        lines.append("")
        lines.append("  by host:")
        for host, v in combined["by_host"].items():
            lines.append("    %-16s $%9.2f  %6d msgs" % (host, v["usd"], v["msgs"]))
    lines.append("")
    lines.append("  by day:")
    for day, v in sorted(combined["by_day"].items()):
        lines.append("    %-12s $%9.2f  %6d msgs" % (day, v["usd"], v["msgs"]))
    lines.append("")
    lines.append("  top projects:")
    top = sorted(combined["by_project"].items(), key=lambda kv: -kv[1]["usd"])[:10]
    for proj, v in top:
        lines.append("    %-40s $%9.2f  %6d msgs" % (proj, v["usd"], v["msgs"]))
    cache_tokens = sum(v["cache_r"] + v["cache_w"] for v in combined["by_model"].values())
    if total_msgs:
        lines.append("")
        lines.append("  avg context/msg: %s tokens (cache read+write / message)"
                      % _fmt_int(cache_tokens / total_msgs))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# #37 follow-up (2026-07-25) — the AUTOMATIC before/after feedback loop. The
# managed-default-model fix only applies going forward; the user asked to
# "change things one step at a time and measure hourly whether it got better
# or worse, automatically — he must not have to check anything himself."
# `airuleset.py watchdog` writes an hourly row (`hourly_snapshot`, called
# from `watchdog.burn_snapshot_job`) to `snapshots.jsonl`; `mark_change`
# records WHEN a change was made to `changes.jsonl`; `compare_changes` /
# `render_compare` are the arithmetic + report behind `burn --compare`.
# --------------------------------------------------------------------------- #

def burn_history_dir():
    """`~/.claude/burn-history/` — resolved at CALL time (never a frozen
    module-level constant): `Path.home()` reads `$HOME` when invoked, and a
    constant computed at import time would freeze whatever `$HOME` was at
    first `import burn` — silently wrong for any test (or any caller) that
    points `$HOME` elsewhere afterward. Mirrors `local_report()`'s own
    `os.path.expanduser(...)`-inside-the-function convention."""
    return Path.home() / ".claude" / "burn-history"


def snapshots_path():
    return burn_history_dir() / "snapshots.jsonl"


def changes_path():
    return burn_history_dir() / "changes.jsonl"


def hourly_snapshot(now, root=None, host=None, user=None, days=2):
    """Aggregate the PREVIOUS full hour (the hour immediately before the one
    `now` falls in) into the single-JSON-line shape `snapshots.jsonl` wants:
    `{"ts", "host", "user", "window_h": 1, "usd", "msgs", "avg_ctx",
    "by_model": {<model>: <usd>, ...}}`.

    Reuses `scan()`'s per-line parser (no second transcript parser) — this
    just picks the ONE hour bucket out of its `by_hour` / `by_hour_model`
    breakdown. `days` only bounds how many transcript FILES `scan()` even
    opens (a small window trivially covers "the previous hour" since a
    file's mtime cutoff check is on WALL-CLOCK age, not on the target hour);
    it is not the reporting window itself. `now` must be a timezone-aware
    datetime (the same convention `scan()`/`local_report()` use)."""
    root = root or os.path.expanduser("~/.claude/projects")
    data = scan(root, days=days, now=now)
    end = now.astimezone().replace(minute=0, second=0, microsecond=0)
    start = end - datetime.timedelta(hours=1)
    hour_key = start.strftime("%Y-%m-%dT%H:00")
    row = data["by_hour"].get(hour_key) or _blank_row()
    prefix = hour_key + "|"
    by_model = {k[len(prefix):]: v["usd"]
                for k, v in data["by_hour_model"].items() if k.startswith(prefix)}
    msgs = row["msgs"]
    avg_ctx = int(round((row["cache_r"] + row["cache_w"]) / msgs)) if msgs else 0
    return {
        "ts": start.isoformat(),
        "host": host or os.uname().nodename,
        "user": user or os.environ.get("USER", "?"),
        "window_h": 1,
        "usd": round(row["usd"], 4),
        "msgs": msgs,
        "avg_ctx": avg_ctx,
        "by_model": {k: round(v, 4) for k, v in by_model.items()},
    }


def _read_jsonl(path):
    """Every parseable JSON object line in `path`, skipping blank/malformed
    lines. Never raises — a corrupt/missing history file must not break
    `burn --compare` or the hourly snapshot job."""
    rows = []
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def load_snapshots(path=None):
    return _read_jsonl(path or snapshots_path())


def load_changes(path=None):
    return _read_jsonl(path or changes_path())


def mark_change(text, path=None, host=None, now=None):
    """Append `{"ts", "host", "text"}` to `changes.jsonl` — records WHEN a
    change was made so `--compare` can measure before vs after it. `now`
    lets a caller back-date a mark from a known event (e.g. a git commit
    timestamp) recorded after the fact; defaults to the current time."""
    path = Path(path or changes_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    row = {"ts": now.isoformat(), "host": host or os.uname().nodename, "text": text}
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    return str(path)


def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _window_stats(rows, start, end):
    """Mean usd / avg_ctx / msgs across snapshot rows whose `ts` falls in
    [start, end). `n=0` (all other fields None) when the window is empty —
    e.g. a change made minutes ago has no "after" data yet. Never raises on
    a malformed row (a bad `ts` is simply excluded)."""
    sel = []
    for r in rows:
        t = _parse_ts(r.get("ts"))
        if t is not None and start <= t < end:
            sel.append(r)
    n = len(sel)
    if not n:
        return {"n": 0, "usd_h": None, "avg_ctx": None, "msgs_h": None}
    return {
        "n": n,
        "usd_h": sum(r.get("usd", 0.0) for r in sel) / n,
        "avg_ctx": sum(r.get("avg_ctx", 0) for r in sel) / n,
        "msgs_h": sum(r.get("msgs", 0) for r in sel) / n,
    }


def compare_changes(snapshots, changes, window_hours=6):
    """Per recorded change (chronological), the mean $/h, avg_ctx and msgs/h
    for `window_hours` immediately BEFORE vs AFTER it, from the hourly
    `snapshots` rows. A change with an unparsable `ts` is skipped — never
    raises on ragged input, this feeds a Slovak terminal report the user
    checks casually."""
    win = datetime.timedelta(hours=window_hours)
    dated = sorted(
        (c for c in changes if _parse_ts(c.get("ts")) is not None),
        key=lambda c: _parse_ts(c["ts"]))
    out = []
    for c in dated:
        t = _parse_ts(c["ts"])
        out.append({
            "ts": c["ts"], "host": c.get("host", "?"), "text": c.get("text", ""),
            "before": _window_stats(snapshots, t - win, t),
            "after": _window_stats(snapshots, t, t + win),
        })
    return out


def _pct_delta(before, after):
    if before is None or after is None or not before:
        return None
    return (after - before) / before * 100.0


def _fmt_window(w):
    if not w["n"]:
        return "ziadne data"
    return "$%.2f/h * ctx %s * %.1f msg/h (n=%d vzoriek)" % (
        w["usd_h"], _fmt_int(w["avg_ctx"]), w["msgs_h"], w["n"])


def _fmt_delta(before, after, lower_is_better=True):
    pct = _pct_delta(before, after)
    if pct is None:
        return ""
    arrow = "lepšie" if (pct < 0) == lower_is_better else "horšie"
    if pct == 0:
        arrow = "bez zmeny"
    return "%+.1f%% (%s)" % (pct, arrow)


def render_compare(results, window_hours=6, fleet_results=None):
    """Slovak, terminal-readable before/after report — the user's automatic
    feedback loop: for every recorded change, mean $/h, avg context and
    msgs/h in the `window_hours` immediately before vs after it, with the
    delta and a lepšie/horšie direction on cost + context (lower is
    better; msgs/h is shown without a verdict — more messages isn't
    inherently good or bad).

    `fleet_results` (optional, #55 point D) is the SAME `compare_changes()`
    shape computed over the whole monitored fleet instead of just this host
    (via `fleet_compare_rows()` + `compare_changes()` — no separate
    before/after arithmetic). Matched to `results` by the change's `ts` (both
    lists were built from the identical `changes` argument, so they carry the
    same set of timestamps) — when found, an extra "Sada" block is printed
    under that change's own before/after."""
    if not results:
        return ("airuleset burn --compare -- zatial nie su zaznamenane ziadne "
                "zmeny. Pouzi `airuleset.py burn --mark \"<text>\"` po kazdej "
                "zmene, ktoru chces takto sledovat.")
    lines = ["airuleset burn --compare -- pred/po pre kazdu zaznamenanu zmenu "
             "(okno %dh)" % window_hours, ""]
    fleet_by_ts = {r["ts"]: r for r in (fleet_results or [])}
    for r in results:
        lines.append("Zmena: %s" % r["text"])
        lines.append("  Kedy: %s (host %s)" % (r["ts"], r["host"]))
        b, a = r["before"], r["after"]
        lines.append("  Pred: %s" % _fmt_window(b))
        lines.append("  Po:   %s" % _fmt_window(a))
        if b["n"] and a["n"]:
            usd_d = _fmt_delta(b["usd_h"], a["usd_h"])
            ctx_d = _fmt_delta(b["avg_ctx"], a["avg_ctx"])
            msgs_pct = _pct_delta(b["msgs_h"], a["msgs_h"])
            msgs_d = ("%+.1f%%" % msgs_pct) if msgs_pct is not None else "?"
            lines.append("  Zmena: $/h %s | ctx %s | msg/h %s"
                         % (usd_d, ctx_d, msgs_d))
        fr = fleet_by_ts.get(r["ts"])
        if fr:
            fb, fa = fr["before"], fr["after"]
            lines.append("  Sada (cela monitorovana sada):")
            lines.append("    Pred: %s" % _fmt_window(fb))
            lines.append("    Po:   %s" % _fmt_window(fa))
            if fb["n"] and fa["n"]:
                lines.append("    Zmena: $/h %s" % _fmt_delta(fb["usd_h"], fa["usd_h"]))
        lines.append("")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# #55 follow-up (2026-07-25) — FLEET monitoring. Job 13 above only ever
# measured THIS box. The user's ask: "zacat aj v hodinovych intervaloch
# vyhodnocovat stav spotreby tokenov cez monitorovanu sadu claude targetov" —
# i.e. dev1, dev2, gatekeeper, and montalu/marek/david@subdev, merged into ONE
# hourly view, with an automatic sustainability read against the weekly
# token-usage cache and a budget-exceeded ping. The network/ssh collection
# lives in airuleset.py (`_watchdog_fleet_fetch`, mirroring `_burn_remote`) —
# everything HERE stays a pure function over already-collected data, per the
# ticket's own testing note ("burn/ je cista funkcia nad datami").
# --------------------------------------------------------------------------- #

def fleet_path():
    return burn_history_dir() / "fleet.jsonl"


def load_fleet(path=None):
    return _read_jsonl(path or fleet_path())


def usage_cache_path():
    """`~/.claude/airuleset-usage-cache.json` — the SAME file
    `watchdog.write_usage_cache()` maintains (path string duplicated
    deliberately: `burn` must never import `watchdog`, which already imports
    `burn` — see `hourly_snapshot()`'s call from `watchdog.burn_snapshot_job`
    — so importing the constant back would be circular). Resolved at CALL
    time like every other path helper in this module."""
    return Path.home() / ".claude" / "airuleset-usage-cache.json"


def load_usage_cache(path=None):
    """Best-effort read of the watchdog's usage cache; None on any
    missing/corrupt file. Never raises."""
    path = path or usage_cache_path()
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def shared_weekly_window(cache):
    """(percent, resets_at) for the ACCOUNT-WIDE weekly window (model is
    falsy — the per-model weekly windows, e.g. Fable's, are a DIFFERENT
    number) — or None when no such window is present. Across multiple
    matching entries takes the MAX percent, mirroring
    `watchdog.fable_gate`'s own shared-window selection."""
    best = None
    for w in (cache or {}).get("windows") or []:
        if w.get("group") != "weekly" or w.get("model"):
            continue
        pct = w.get("percent")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        if best is None or pct > best[0]:
            best = (pct, w.get("resets_at"))
    return best


def merge_fleet_row(ts, host_rows, weekly_pct=None, resets_at=None):
    """Pure merge of one collection cycle's per-host rows into the single
    fleet.jsonl row shape: `{ts, per_host, total_usd, total_msgs,
    weighted_avg_ctx}` (+ `weekly_pct`/`resets_at` when given — carried
    forward hourly so `observed_pct_per_day()` gets a genuine time series
    with no separate history file). A host whose row is missing/None or
    carries an `"error"` key is excluded from the totals but still appears in
    `per_host` as `{"error": <msg>}` — one bad host (ssh timeout, fail2ban
    ban, corrupt JSON) never drops the rest of the fleet. A row that isn't
    even a dict (a remote line that parsed to a bare number/list/string) is
    reported as `"malformed row"` rather than crashing — never raises.

    A `"stale": True` marker on an input row (set by
    `airuleset._fleet_remote_row` when the remote's latest line is for the
    WRONG hour — #60) is carried through into `per_host` unchanged, so
    `render_fleet` can render it distinctly (a dash, "no sample for this
    hour yet") from a hard collection failure (`ERR`, "couldn't reach the
    host at all") — both are excluded from the totals the same way, but
    they mean different things to the reader."""
    per_host = {}
    total_usd = 0.0
    total_msgs = 0
    weighted_ctx_sum = 0.0
    for name, row in (host_rows or {}).items():
        if row is None or row == {}:
            per_host[name] = {"error": "no data"}
            continue
        if not isinstance(row, dict):
            per_host[name] = {"error": "malformed row"}
            continue
        if row.get("error"):
            entry = {"error": row.get("error")}
            if row.get("stale"):
                entry["stale"] = True
            per_host[name] = entry
            continue
        usd = float(row.get("usd", 0.0) or 0.0)
        msgs = int(row.get("msgs", 0) or 0)
        avg_ctx = int(row.get("avg_ctx", 0) or 0)
        per_host[name] = {"usd": round(usd, 4), "msgs": msgs, "avg_ctx": avg_ctx,
                          "by_model": row.get("by_model") or {}}
        total_usd += usd
        total_msgs += msgs
        weighted_ctx_sum += avg_ctx * msgs
    out = {
        "ts": ts,
        "per_host": per_host,
        "total_usd": round(total_usd, 4),
        "total_msgs": total_msgs,
        "weighted_avg_ctx": int(round(weighted_ctx_sum / total_msgs)) if total_msgs else 0,
    }
    if weekly_pct is not None:
        out["weekly_pct"] = weekly_pct
    if resets_at is not None:
        out["resets_at"] = resets_at
    return out


def weekly_budget(cache, now=None):
    """{'weekly_pct', 'resets_at', 'remaining_days', 'budget_pct_per_day'}
    from the usage cache's shared weekly window — `budget_pct_per_day` is the
    remaining allowance spread evenly over the remaining days (the rate that
    would land at exactly 100% right at reset). None when no weekly window is
    cached at all (fresh box, cache not yet written)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    wk = shared_weekly_window(cache)
    if not wk:
        return None
    pct, resets_at = wk
    reset_dt = _parse_ts(resets_at)
    if reset_dt is None:
        return {"weekly_pct": pct, "resets_at": resets_at,
                "remaining_days": None, "budget_pct_per_day": None}
    if reset_dt.tzinfo is None:
        reset_dt = reset_dt.replace(tzinfo=datetime.timezone.utc)
    remaining_days = max((reset_dt - now).total_seconds() / 86400.0, 0.0)
    budget = ((100.0 - pct) / remaining_days) if remaining_days > 0 else None
    return {"weekly_pct": pct, "resets_at": resets_at,
            "remaining_days": round(remaining_days, 2),
            "budget_pct_per_day": round(budget, 2) if budget is not None else None}


def _valid_host_set(row):
    """The set of hosts in `row['per_host']` that carry a REAL (non-error,
    non-stale) sample — the "which hosts actually contributed to this hour's
    total" set #60 needs to decide whether two hours are even comparable."""
    return frozenset(h for h, v in (row.get("per_host") or {}).items()
                     if not (v or {}).get("error"))


def fleet_trend(rows, n_prev=3):
    """Latest fleet row vs the mean of the up-to-`n_prev` rows immediately
    before it — total AND per-host $ (lower is better, `_fmt_delta`'s
    default). None when fewer than 2 rows exist (nothing to compare yet).

    The TOTAL comparison (#60) is restricted to previous hours whose set of
    HOSTS-WITH-A-REAL-SAMPLE exactly matches the latest hour's — comparing
    totals across hours where a different subset of hosts responded produces
    a meaningless delta (the live incident: 5/6 hosts double-counted a stale
    row, and the total's coincidental stability read as "-39.8% (lepšie)").
    When NO previous hour in the window has a matching host set, `total`
    reports `"comparable": False` with a Slovak `"reason"` instead of a
    percent. The by-host comparison is UNAFFECTED — each host's own trend
    already tolerates a missing/error sample on either side (a host that
    goes stale for one hour still gets its own before/after compared on the
    hours where it DID have data)."""
    if len(rows) < 2:
        return None
    latest = rows[-1]
    prev = rows[max(0, len(rows) - 1 - n_prev):-1]
    if not prev:
        return None

    def _mean(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    latest_hosts = _valid_host_set(latest)
    comparable_prev = [r for r in prev if _valid_host_set(r) == latest_hosts]
    total_latest = latest.get("total_usd")
    if comparable_prev:
        total_prev_mean = _mean(r.get("total_usd") for r in comparable_prev)
        total_out = {
            "latest": total_latest, "prev_mean": total_prev_mean,
            "delta": _fmt_delta(total_prev_mean, total_latest)
                    if total_prev_mean is not None else "",
            "comparable": True,
        }
    else:
        total_out = {
            "latest": total_latest, "prev_mean": None, "delta": "",
            "comparable": False,
            "reason": "neporovnatelne (ina mnozina hostov malo vzorku)",
        }
    out = {"total": total_out, "by_host": {}}
    hosts = set()
    for r in [latest] + prev:
        hosts.update((r.get("per_host") or {}).keys())

    def _usd_of(row, host):
        ph = (row.get("per_host") or {}).get(host)
        if not ph or "error" in ph:
            return None
        return ph.get("usd")

    for h in hosts:
        prev_mean = _mean(_usd_of(r, h) for r in prev)
        latest_val = _usd_of(latest, h)
        out["by_host"][h] = {
            "latest": latest_val, "prev_mean": prev_mean,
            "delta": _fmt_delta(prev_mean, latest_val)
                    if (prev_mean is not None and latest_val is not None) else "",
        }
    return out


def observed_pct_per_day(rows):
    """Observed weekly-% consumption rate (%/day), from the OLDEST and NEWEST
    fleet rows that carry a `weekly_pct` sample (each hourly collection
    stamps the CURRENT usage-cache percent onto its own row — a genuine
    hourly time series with no separate history file). None when fewer than
    2 such samples exist yet, their timestamps don't parse, or they collapse
    to the same instant."""
    samples = [(r.get("ts"), r.get("weekly_pct")) for r in rows
              if r.get("weekly_pct") is not None and _parse_ts(r.get("ts")) is not None]
    if len(samples) < 2:
        return None
    samples.sort(key=lambda s: _parse_ts(s[0]))
    t0, p0 = samples[0]
    t1, p1 = samples[-1]
    d0, d1 = _parse_ts(t0), _parse_ts(t1)
    hours = (d1 - d0).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return (p1 - p0) / hours * 24.0


def fleet_sustainability(rows, cache, now=None):
    """Combine `weekly_budget()` + `observed_pct_per_day()` into one verdict:
    'sedi' (observed pace <= budget), 'prekracuje rozpocet' (observed pace >
    budget), or a Slovak explanation of why no verdict is possible yet
    (no cache / not enough hourly samples)."""
    budget = weekly_budget(cache, now=now)
    if not budget:
        return {"budget": None, "observed_pct_per_day": None,
                "verdict": "chyba usage cache (este nie je zapisana)"}
    observed = observed_pct_per_day(rows)
    if observed is None:
        return {"budget": budget, "observed_pct_per_day": None,
                "verdict": "zatial nedostatok hodinovych vzoriek na odhad tempa"}
    bpd = budget.get("budget_pct_per_day")
    if bpd is None:
        verdict = "neznamy rozpocet (chyba cas resetu)"
    elif observed <= bpd:
        verdict = "sedi"
    else:
        verdict = "prekracuje rozpocet"
    return {"budget": budget, "observed_pct_per_day": round(observed, 2), "verdict": verdict}


def fleet_budget_alert(rows, cache, now=None):
    """None, or `{'message', 'top_host', 'top_model'}` when the observed
    %/day pace exceeds the sustainable budget — the trigger for job 16's ONE
    hourly deduped Discord ping (#55 point C: "tempo, ktore by vycerpalo
    weekly pred resetom"). `top_host`/`top_model` are the latest row's
    biggest $ contributors, named in the ping so the user knows where to
    look."""
    s = fleet_sustainability(rows, cache, now=now)
    budget = s.get("budget")
    observed = s.get("observed_pct_per_day")
    if not budget or observed is None or budget.get("budget_pct_per_day") is None:
        return None
    if observed <= budget["budget_pct_per_day"]:
        return None
    latest = rows[-1] if rows else {}
    per_host = latest.get("per_host") or {}
    top_host, top_host_usd = None, -1.0
    top_model, top_model_usd = None, -1.0
    for h, v in per_host.items():
        if "error" in v:
            continue
        if v.get("usd", 0.0) > top_host_usd:
            top_host_usd, top_host = v.get("usd", 0.0), h
        for model, usd in (v.get("by_model") or {}).items():
            if usd > top_model_usd:
                top_model_usd, top_model = usd, model
    msg = ("⚠️ **Sada tokenov — tempo %.2f%%/den prekracuje rozpocet %.2f%%/den** "
          "(tyzdenny limit %.0f%%, reset za %.1f dna). Najviac minul: %s%s."
          % (observed, budget["budget_pct_per_day"], budget["weekly_pct"],
             budget.get("remaining_days") or 0.0, top_host or "?",
             (" (%s)" % top_model) if top_model else ""))
    return {"message": msg, "top_host": top_host, "top_model": top_model}


def fleet_compare_rows(fleet_rows):
    """Normalize fleet.jsonl rows into the snapshot-shaped `{ts, usd,
    avg_ctx, msgs}` `compare_changes()`/`_window_stats()` already understand
    — reuses the SAME before/after windowing arithmetic for the whole-fleet
    view (#55 point D), no duplicate logic."""
    return [{"ts": r.get("ts"), "usd": r.get("total_usd", 0.0),
             "avg_ctx": r.get("weighted_avg_ctx", 0), "msgs": r.get("total_msgs", 0)}
            for r in fleet_rows]


def render_fleet(rows, hours=24, cache=None, now=None):
    """Slovak, terminal-readable fleet report (`airuleset.py burn --fleet`):
    the last `hours` hourly rows (per host $ + total, plus how many of the
    monitored hosts actually have a sample for that hour — #60), the trend
    (latest vs mean of the previous 3 — refuses to show a percent when the
    host set differs, #60), and the sustainability verdict against the
    weekly usage-cache budget.

    A host's per_host entry renders as `—` (dash) when it is STALE (#60:
    no sample yet for this hour — `"stale": True`, set by
    `airuleset._fleet_remote_row`'s hour-match check) — distinct from both
    `ERR` (a hard collection failure: ssh unreachable, bad JSON) and
    `$0.00` (a real, verified zero-usage sample); conflating "no data yet"
    with "spent nothing" was the false -39.8% trend the ticket reported."""
    if not rows:
        return ("airuleset burn --fleet -- zatial nie su zaznamenane ziadne "
                "fleet snapshoty. Bezi len na koordinatorovi (dev1) — pockaj "
                "aspon jednu hodinu po nasadeni #55.")
    shown = rows[-hours:] if hours else rows
    lines = ["airuleset burn --fleet -- monitorovana sada, poslednych %d "
             "hodinovych vzoriek" % len(shown), ""]
    for r in shown:
        parts = []
        per_host = r.get("per_host") or {}
        valid_n = 0
        for h, v in sorted(per_host.items()):
            if v.get("stale"):
                parts.append("%s=—" % h)
            elif "error" in v:
                parts.append("%s=ERR" % h)
            else:
                parts.append("%s=$%.2f" % (h, v.get("usd", 0.0)))
                valid_n += 1
        note = ("  (%d/%d hostov ma vzorku pre tuto hodinu)" % (valid_n, len(per_host))
                if per_host else "")
        lines.append("  %s  total=$%.2f  msgs=%d  ctx=%s  [%s]%s"
                     % (r.get("ts", "?"), r.get("total_usd", 0.0), r.get("total_msgs", 0),
                        _fmt_int(r.get("weighted_avg_ctx", 0)), ", ".join(parts), note))
    trend = fleet_trend(rows)
    if trend:
        lines.append("")
        lines.append("  trend (posledna hodina vs priemer predoslych 3):")
        t = trend["total"]
        if t.get("comparable", True):
            lines.append("    total: $%.2f (priemer $%.2f)  %s"
                         % (t["latest"] or 0.0, t["prev_mean"] or 0.0, t["delta"]))
        else:
            lines.append("    total: $%.2f  %s"
                         % (t["latest"] or 0.0, t.get("reason", "neporovnatelne")))
        for h, v in sorted(trend["by_host"].items()):
            if v["latest"] is None:
                continue
            lines.append("    %-16s $%.2f (priemer $%.2f)  %s"
                         % (h, v["latest"], v["prev_mean"] or 0.0, v["delta"]))
    sus = fleet_sustainability(rows, cache, now=now)
    lines.append("")
    lines.append("  udrzatelnost:")
    b = sus.get("budget")
    if b:
        lines.append("    tyzdenny limit: %.0f%%  (reset za %.2f dna)"
                     % (b["weekly_pct"], b["remaining_days"] or 0.0))
        if b.get("budget_pct_per_day") is not None:
            lines.append("    rozpocet: %.2f %%/den" % b["budget_pct_per_day"])
    if sus.get("observed_pct_per_day") is not None:
        lines.append("    aktualne tempo: %.2f %%/den" % sus["observed_pct_per_day"])
    lines.append("    verdikt: %s" % sus.get("verdict", "?"))
    return "\n".join(lines)
