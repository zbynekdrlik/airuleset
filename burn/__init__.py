"""Token-spend attribution — `airuleset.py burn`.

Ported from a scratch analyzer that measured the 2026-07 burn behind the
whole cost-fix package (#37): ~$13,600 across all 6 managed boxes over 8
days, 76% Fable 5 (running as the MAIN session model, not the advisor shape
the rules mandate), 92% of that spend in input context (cache read + cache
write) vs 8% output. See `modules/core/model-awareness.md` for the pricing
table this module mirrors and the Opus-5-era default-tier policy this
measurement justified (`MANAGED_MODEL` in airuleset.py).

Walks `~/.claude/projects/` (Claude Code's own transcript store — every
top-level session file AND every nested `subagents/agent-*.jsonl`, #149),
parses each assistant `message.usage` entry, and aggregates by model / day /
project / main-vs-sidechain. stdlib only — no deps, so a report can be piped
back from a remote box by invoking that box's own already-deployed
`airuleset.py burn --json` over ssh (see `airuleset._burn_remote`).
"""
import datetime
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
    """Map a `message.model` id (e.g. `claude-fable-5-1[1m]`) to a PRICE key, or
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
    """Walk EVERY transcript under `root` — the top-level session file AND
    any nested transcript (`subagents/agent-*.jsonl` at any depth) reachable
    via `_split_transcripts()` (#149: the old `root/*/*.jsonl` glob was
    two-level-only and never reached a subagent transcript at all, so
    `isSidechain` — which appears ONLY there — was always False and
    `main_vs_sidechain` reported a false 100%-MAIN split). All three of
    `_split_transcripts()`'s kinds (main/sub/other) are scanned here — spend
    is spend, unlike `scan_split()` which deliberately skips "other".

    Returns the printable {in, cache_w, cache_r, out, usd, msgs} shape per
    bucket, grouped by model / day / project / main-vs-sidechain. A file
    whose mtime is older than the cutoff is skipped WITHOUT being opened
    (cheap on a directory with years of transcripts); a line without `usage`
    or with an unparsable timestamp is skipped.

    Aggregation is per-REQUEST, not per-line (#149, same fix as #150 gave
    `scan_split()`): Claude Code writes one API response as several
    transcript lines (one per content block — `thinking`, then each
    `tool_use`), each carrying a COPY of the same request-level `usage` —
    counting every line inflated turns/tokens ~2.13x on real data. Each
    file's lines are first folded into per-request state via the shared
    `_fold_usage_line()` helper (the exact mechanism `scan_split()` already
    uses), then aggregated ONCE per request — which also means an all-zero-
    usage line (the `<synthetic>` interrupt/error placeholder shape) is
    dropped before it ever reaches `by_model`/`msgs`, since
    `_fold_usage_line()` folds nothing for it. Tool-use counting is the one
    exception: a request's `tool_use` blocks are spread across its several
    lines, so `by_hour_tools` still inspects every raw LINE's content,
    independent of the request fold. A folded request whose timestamp is
    OUTSIDE `[cutoff, now]` is dropped too — both too old AND future-dated
    (a clock-skew/malformed-timestamp artifact), matching `scan_split()`'s
    own `t < cutoff or t > now` check."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    agg = defaultdict(_empty_row)
    by_day = defaultdict(_empty_row)
    by_proj = defaultdict(_empty_row)
    by_day_model = defaultdict(_empty_row)
    by_hour = defaultdict(_empty_row)
    by_hour_model = defaultdict(_empty_row)
    side = defaultdict(_empty_row)
    # #80: MAIN-agent tool-call counts per hour. The lever behind the burn is
    # the NUMBER of main-agent turns (each re-sends the whole context), so
    # the Bash:Agent ratio is the metric — counted here, in the pass that is
    # already walking every assistant entry, rather than by a second parser.
    by_hour_tools = defaultdict(lambda: [0, 0])       # hour -> [bash, agent]
    files = 0
    lines = 0
    for path, proj, _kind in _split_transcripts(root):
        try:
            mt = datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc)
            if mt < cutoff:
                continue
        except OSError:
            continue
        files += 1
        try:
            fh = open(path, "r", errors="replace")
        except OSError:
            continue
        with fh:
            order = []
            reqs = {}
            req_meta = {}
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                msg = e.get("message") or {}
                sc = bool(e.get("isSidechain"))
                # Tool-use counting sees every raw LINE (a request's blocks
                # are spread across several lines) — independent of the
                # per-request fold below.
                if not sc:
                    blocks = msg.get("content")
                    if isinstance(blocks, list):
                        ts_line = e.get("timestamp") or ""
                        try:
                            t_line = datetime.datetime.fromisoformat(
                                ts_line.replace("Z", "+00:00"))
                        except ValueError:
                            t_line = None
                        if t_line is not None and t_line >= cutoff:
                            hour_line = t_line.astimezone().strftime("%Y-%m-%dT%H:00")
                            counts = by_hour_tools[hour_line]
                            for b in blocks:
                                if not isinstance(b, dict) or b.get("type") != "tool_use":
                                    continue
                                nm = b.get("name")
                                if nm == "Bash":
                                    counts[0] += 1
                                elif nm in ("Agent", "Task"):
                                    counts[1] += 1
                rid = _fold_usage_line(e, order, reqs)
                if rid is not None:
                    meta = req_meta.setdefault(rid, {})
                    if msg.get("model"):
                        meta["model"] = msg.get("model")
                    meta["sc"] = meta.get("sc", False) or sc
            for rid in order:
                r = reqs[rid]
                t = r.get("ts")
                if t is None or t < cutoff or t > now:
                    continue
                lines += 1
                meta = req_meta.get(rid) or {}
                model = meta.get("model") or "?"
                tr = tier(model)
                i, cw, cr, o = r["in"], r["cache_w"], r["cache_r"], r["out"]
                p = PRICE.get(tr)
                usd = (i * p[0] + cw * p[1] + cr * p[2] + o * p[3]) / 1e6 if p else 0.0
                local_t = t.astimezone()
                day = local_t.strftime("%Y-%m-%d")
                hour = local_t.strftime("%Y-%m-%dT%H:00")
                sc = meta.get("sc", False)
                for d, k in (
                    (agg, model), (by_day, day), (by_proj, proj),
                    (by_day_model, day + "|" + tr),
                    (by_hour, hour), (by_hour_model, hour + "|" + model),
                    (side, ("sidechain" if sc else "main") + "|" + tr),
                ):
                    row = d[k]
                    row[0] += i
                    row[1] += cw
                    row[2] += cr
                    row[3] += o
                    row[4] += usd
                    row[5] += 1
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
        "by_hour_main_tools": {k: {"bash": v[0], "agent": v[1]}
                               for k, v in sorted(by_hour_tools.items())},
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


def hourly_snapshot(now, root=None, host=None, user=None, days=2,
                    usage_cache_path=None):
    """Aggregate the PREVIOUS full hour (the hour immediately before the one
    `now` falls in) into the single-JSON-line shape `snapshots.jsonl` wants:
    `{"ts", "host", "user", "window_h": 1, "usd", "msgs", "avg_ctx",
    "scope": "agents", "account_email", "by_model": {<model>: <usd>, ...}}`.

    Reuses `scan()`'s per-line parser (no second transcript parser) — this
    just picks the ONE hour bucket out of its `by_hour` / `by_hour_model`
    breakdown. `days` only bounds how many transcript FILES `scan()` even
    opens (a small window trivially covers "the previous hour" since a
    file's mtime cutoff check is on WALL-CLOCK age, not on the target hour);
    it is not the reporting window itself. `now` must be a timezone-aware
    datetime (the same convention `scan()`/`local_report()` use).

    `"scope": "agents"` (#149) marks this row as belonging to the post-
    reconciliation era, where `scan()` (and therefore this figure) includes
    subagent spend alongside main-session spend. A row written before #149
    has NO `scope` key at all — `_window_stats()`/`compare_changes()` and
    `hourly_burn_alert()` both only compare rows that carry the SAME scope,
    so a before/after that straddles the deploy boundary never reads as a
    false regression or a false spike.

    `"account_email"` (#269) is the SAME field `watchdog.write_usage_cache()`
    already writes into `~/.claude/airuleset-usage-cache.json` (the field
    `statusbar.account_email_segment` renders) — read here via
    `load_usage_cache(usage_cache_path)` so fleet reporting can map a box to
    the Anthropic subscription account it is spending against.
    `usage_cache_path` defaults to the module's own `usage_cache_path()`
    (the real local cache) and is only overridden in tests; "" on any
    missing/unreadable/malformed cache — never blocks or crashes, mirroring
    `write_usage_cache()`'s own degrade-to-"" convention for this exact
    field. "Malformed" explicitly includes a cache file that parses as
    valid JSON but is NOT an object (e.g. a bare string/number/list) —
    `load_usage_cache()` only returns `None` on an unparseable file, so a
    JSON-valid-but-wrong-shape cache is caught HERE with its own
    `isinstance` check, never passed through to `.get()`."""
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
    tools = data.get("by_hour_main_tools", {}).get(hour_key) or {}
    usage_cache = load_usage_cache(usage_cache_path)
    if not isinstance(usage_cache, dict):
        usage_cache = None
    return {
        "ts": start.isoformat(),
        "host": host or os.uname().nodename,
        "user": user or os.environ.get("USER", "?"),
        "window_h": 1,
        "scope": "agents",
        "usd": round(row["usd"], 4),
        "msgs": msgs,
        "avg_ctx": avg_ctx,
        "by_model": {k: round(v, 4) for k, v in by_model.items()},
        # #80: the dispatch-ratio inputs — MAIN-agent Bash calls vs Agent/Task
        # dispatches in this hour. `main_bash / main_agent` IS the acceptance
        # metric ("under 5:1"), now readable from the file instead of from a
        # hand-run script over a 23MB transcript.
        "main_bash": int(tools.get("bash", 0)),
        "main_agent": int(tools.get("agent", 0)),
        "account_email": (usage_cache or {}).get("account_email") or "",
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


def hour_bucket_of_ts(ts_str):
    """Epoch-hour bucket (`int(epoch_seconds // 3600)`) of an ISO-8601
    timestamp STRING, converted to UTC first — comparing raw hour-of-day
    digits (or the raw string) across differing UTC offsets is exactly the
    #60 bug (gk writes `+00:00`, dev1 `+02:00` — the SAME instant renders
    with different hour digits in each). None when `ts_str` is missing,
    None, or unparsable — callers treat that as "can't verify freshness"
    and error rather than trusting it.

    The single canonical implementation — `airuleset._hour_bucket_of_ts`
    (used by `_fleet_remote_row`) and `watchdog.fleet_burn_job`'s own
    local-row freshness check (#63) both delegate here, so the "which hour
    bucket is this timestamp in" question can never drift between the two
    call sites again."""
    dt = _parse_ts(ts_str)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() // 3600)


def _window_stats(rows, start, end):
    """Mean usd / avg_ctx / msgs across snapshot rows whose `ts` falls in
    [start, end). `n=0` (all other fields None) when the window is empty —
    e.g. a change made minutes ago has no "after" data yet. Never raises on
    a malformed row (a bad `ts` is simply excluded).

    #149: ONLY rows tagged `scope == "agents"` (the post-subagent-
    reconciliation era `hourly_snapshot()` stamps) participate. A row from
    before #149 has no `scope` key and is excluded outright — never
    silently mixed with post-#149 figures, which would otherwise read a
    genuine ~2x jump at the deploy boundary as a real regression. A window
    containing only pre-#149 rows returns the SAME empty shape as a window
    with no rows at all (`n=0`) — history is never rewritten, it simply
    doesn't count toward a scope-tagged comparison."""
    sel = []
    for r in rows:
        if r.get("scope") != "agents":
            continue
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
    they mean different things to the reader.

    #149/F6 (STRICT unanimity, not best-effort): the merged row carries
    `"scope": "agents"` ONLY when EVERY CONTRIBUTING (non-error) host row
    carries it (the post-subagent-reconciliation shape `hourly_snapshot()`
    now stamps) — letting `hourly_burn_alert()`'s REL-median / weekly-step
    checks tell a genuinely new-scope fleet row apart from an old one. An
    earlier best-effort version tagged the merged row if ANY host carried
    it — a mixed old+new fleet hour (one host already reconciled, one not)
    got tagged anyway, poisoning the REL baseline with an hour that still
    partly excludes subagent spend. A merged row with ZERO contributors
    (every host errored) is never tagged either — "unanimous over an empty
    set" is not the same claim as "confirmed new-scope". It still
    self-corrects naturally: an untagged (mixed or contributor-less) row is
    simply excluded from every scope-gated comparison, exactly like any
    other pre-#149 row, until every contributing host is pushed."""
    per_host = {}
    total_usd = 0.0
    total_msgs = 0
    weighted_ctx_sum = 0.0
    total_main_bash = 0
    total_main_agent = 0
    saw_contributor = False
    all_agents_scope = True
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
        # #80: a box that has not been pushed yet sends no main_bash/
        # main_agent — count it as zero, never crash on the missing key.
        main_bash = int(row.get("main_bash", 0) or 0)
        main_agent = int(row.get("main_agent", 0) or 0)
        per_host[name] = {"usd": round(usd, 4), "msgs": msgs, "avg_ctx": avg_ctx,
                          "main_bash": main_bash, "main_agent": main_agent,
                          "by_model": row.get("by_model") or {},
                          # #269: box->account mapping — piggybacks on the
                          # SAME collection round-trip, no new ssh reads.
                          "account_email": row.get("account_email") or "",
                          # #286: this host's OWN weekly %/reset (from ITS
                          # OWN local usage cache, fetched over the SAME ssh
                          # round-trip by `_fleet_remote_row`) — present but
                          # None when unavailable (mirrors account_email's
                          # own present-but-blank convention); validated
                          # (numeric, not a guess) by `group_fleet_by_account`.
                          "weekly_pct": row.get("weekly_pct"),
                          "resets_at": row.get("resets_at"),
                          # #286-review: the WRITE TIME of the remote cache
                          # this candidate came from — `group_fleet_by_account`
                          # uses it to refuse a stale candidate, mirroring
                          # `weekly_pct`'s own present-but-None convention.
                          "weekly_ts": row.get("weekly_ts")}
        total_usd += usd
        total_msgs += msgs
        weighted_ctx_sum += avg_ctx * msgs
        total_main_bash += main_bash
        total_main_agent += main_agent
        saw_contributor = True
        if row.get("scope") != "agents":
            all_agents_scope = False
    out = {
        "ts": ts,
        "per_host": per_host,
        "total_usd": round(total_usd, 4),
        "total_msgs": total_msgs,
        "weighted_avg_ctx": int(round(weighted_ctx_sum / total_msgs)) if total_msgs else 0,
        "total_main_bash": total_main_bash,
        "total_main_agent": total_main_agent,
    }
    if saw_contributor and all_agents_scope:
        out["scope"] = "agents"
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


# #286-review — how STALE a cross-host weekly-window CANDIDATE may be before
# `group_fleet_by_account()` refuses to trust it. Mirrors
# `watchdog.usage.FABLE_GATE_MAX_AGE`'s own 6h staleness bound for this EXACT
# same cache file (`~/.claude/airuleset-usage-cache.json`) — a deliberate MIRROR,
# never a shared import: `burn` must never import `watchdog`, which already
# imports `burn` (see `usage_cache_path()`'s own docstring for why).
FLEET_WEEKLY_CANDIDATE_MAX_AGE = 6 * 3600


def _weekly_candidate_is_fresh(ts, now_epoch):
    """True iff `ts` (a host's own usage-cache WRITE time, unix epoch
    seconds — see `_fleet_remote_row`'s `weekly_ts`) is within
    `FLEET_WEEKLY_CANDIDATE_MAX_AGE` of `now_epoch`. Mirrors
    `watchdog.fable_gate()`'s own clock-skew-safe staleness check for the
    SAME cache file: age outside `[0, MAX]` — including a FUTURE `ts`
    (clock skew, a cache synced off another box), which a plain
    `age > MAX` check would wrongly call "fresh" forever — is unknown,
    never trusted. A missing/non-numeric `ts` (a legacy pre-#286 row that
    never carried one) is never fresh by omission — excluded from the
    candidate list, never guessed."""
    try:
        age = float(now_epoch) - float(ts)
    except (TypeError, ValueError):
        return False
    return 0 <= age <= FLEET_WEEKLY_CANDIDATE_MAX_AGE


def group_fleet_by_account(per_host, cache=None, now=None):
    """#269 — box->Anthropic-account mapping + subtotal, over a `per_host`
    dict (the shape `merge_fleet_row()` writes, typically the LATEST
    `fleet.jsonl` row's `per_host` — mirroring how `fleet_trend()`/
    `fleet_sustainability()` already key off "latest"). Returns a list of
    `{"account", "hosts", "total_usd", "weekly_pct", "resets_at"}` dicts:

    - Grouped by `account_email` (#212's field, carried through by
      `merge_fleet_row()` since #269) — boxes sharing the same account are
      grouped together and their `usd` subtotaled.
    - A host row that is `error`/`stale`/missing/not-a-dict contributes
      NOTHING (mirrors `merge_fleet_row()`'s own contributor exclusion from
      every total — a box that couldn't be reached must not silently
      subtract from, or falsely pad, an account's real subtotal).
    - Real accounts are sorted ALPHABETICALLY for a stable, deterministic
      order; any host whose `account_email` is blank (missing, unreadable
      local cache, or a pre-#269 row) is collected into a single trailing
      "unknown" bucket (`"account": None`) — placed LAST, never merged into
      a real account by guessing, and never dropped.
    - `weekly_pct`/`resets_at` (#286): for each REAL account, every
      contributing host's OWN carried `weekly_pct`/`resets_at` (from that
      host's OWN local usage cache — fetched over the SAME ssh round-trip
      by `airuleset._fleet_remote_row`, piggybacking on the existing
      `merge_fleet_row()` per-host whitelist, never a new remote read) is a
      candidate window, PLUS — for the ONE account matching
      `cache.get("account_email")` (the box actually RUNNING the report) —
      that box's own `cache`-derived window (the pre-#286 mechanism,
      still needed since `REMOTE_HOSTS` never includes the reporting box
      itself, so its own account otherwise has no other candidate at all;
      this LOCAL window is a live read on THIS call and is never staleness-
      gated, unlike the cross-host candidates below). A cross-host
      candidate is trusted ONLY when its own `weekly_ts` (the write time of
      the REMOTE box's usage cache — see `_fleet_remote_row`) is within
      `FLEET_WEEKLY_CANDIDATE_MAX_AGE` (6h, #286-review) of `now` — a
      candidate with no `weekly_ts` at all (a legacy pre-#286 row) is NEVER
      trusted by omission. Among the remaining FRESH candidates for an
      account, the MAX percent wins — mirroring `shared_weekly_window()`'s
      own multiple-matching-entries convention, now applied across boxes
      instead of within one cache. Zero fresh candidates (every
      contributing host's own cache was unreadable/stale/too old, AND it
      isn't the reporting box's own account) leaves the window `None` —
      rendered as "?" by `render_fleet()`, NEVER fabricated. The
      unknown-account bucket (`"account": None`) NEVER collects or receives
      a window under any circumstance, even when `cache`'s own
      `account_email` happens to also be blank (`None == None` would
      otherwise wrongly match it) — see
      `test_unknown_bucket_never_leaks_the_local_boxs_window_even_when_its_own_account_email_is_blank`.

    Never raises: an empty/None `per_host` returns `[]`. Also never raises on
    a malformed field crossing this legacy-file/ssh-tailed boundary — a
    non-string `account_email` (a corrupted local cache, a hand-edited
    `fleet.jsonl`) is treated exactly like a missing one (unknown bucket),
    never sorted/hashed as-is (the account key must be `str` or `None` for
    both `sorted()` and the dict grouping to stay safe); a non-numeric or
    boolean `weekly_pct` on a host row is silently excluded from the
    candidate list (`isinstance(x, (int, float)) and not isinstance(x, bool)`
    — `isinstance(True, int)` is `True` in Python, so the bool exclusion is
    explicit, never implicit)."""
    groups = {}
    for host, v in (per_host or {}).items():
        if not v or not isinstance(v, dict) or v.get("error"):
            continue
        acct = v.get("account_email")
        acct = acct if isinstance(acct, str) and acct else None
        g = groups.setdefault(acct, {"hosts": [], "total_usd": 0.0, "windows": []})
        g["hosts"].append(host)
        g["total_usd"] += v.get("usd", 0.0) or 0.0
        if acct is not None:
            pct = v.get("weekly_pct")
            if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                g["windows"].append((pct, v.get("resets_at"), v.get("weekly_ts")))
    cache = cache if isinstance(cache, dict) else None
    wk = shared_weekly_window(cache) if cache else None
    my_account = (cache or {}).get("account_email") or None
    if now is None:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, datetime.datetime):
        now_dt = now
    else:
        now_dt = datetime.datetime.fromtimestamp(float(now), datetime.timezone.utc)
    now_epoch = now_dt.timestamp()
    out = []
    for acct in sorted(k for k in groups if k is not None):
        g = groups[acct]
        candidates = [(pct, resets_at) for (pct, resets_at, ts) in g["windows"]
                     if _weekly_candidate_is_fresh(ts, now_epoch)]
        if wk and acct == my_account:
            candidates.append(wk)
        weekly_pct, resets_at = (max(candidates, key=lambda c: c[0])
                                 if candidates else (None, None))
        out.append({"account": acct, "hosts": sorted(g["hosts"]),
                    "total_usd": round(g["total_usd"], 4),
                    "weekly_pct": weekly_pct, "resets_at": resets_at})
    if None in groups:
        g = groups[None]
        out.append({"account": None, "hosts": sorted(g["hosts"]),
                    "total_usd": round(g["total_usd"], 4),
                    "weekly_pct": None, "resets_at": None})
    return out


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
    hours where it DID have data).

    The TOTAL comparison ALSO requires a matching `scope` (#149) — a prior
    hour whose host set happens to match latest's but whose `scope` doesn't
    (a hour straddling the subagent-reconciliation deploy) is not a real
    comparison either: live it printed a false "-21.5% (lepšie)" comparing
    a tagged latest against an untagged prev. A latest row with no `scope`
    key compares only against other untagged rows, so old-vs-old history
    stays comparable mid-rollout too."""
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
    latest_scope = latest.get("scope")
    comparable_prev = [r for r in prev
                       if _valid_host_set(r) == latest_hosts
                       and r.get("scope") == latest_scope]
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
    avg_ctx, msgs[, scope]}` `compare_changes()`/`_window_stats()` already
    understand — reuses the SAME before/after windowing arithmetic for the
    whole-fleet view (#55 point D), no duplicate logic.

    `scope` is carried through when the source fleet row carries it (key
    omitted otherwise, mirroring `merge_fleet_row()`'s own conditional
    tagging) — `_window_stats()` only counts rows tagged `scope ==
    "agents"`, so dropping this field here silently discarded every fleet
    row from `burn --compare`'s fleet half, no matter how much real history
    existed."""
    out = []
    for r in fleet_rows:
        row = {"ts": r.get("ts"), "usd": r.get("total_usd", 0.0),
               "avg_ctx": r.get("weighted_avg_ctx", 0), "msgs": r.get("total_msgs", 0)}
        if r.get("scope") is not None:
            row["scope"] = r.get("scope")
        out.append(row)
    return out


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
    with "spent nothing" was the false -39.8% trend the ticket reported.

    Also prints an "ucty" (accounts) section (#269, cross-account real
    windows since #286): the LATEST hour's `per_host` grouped by Anthropic
    subscription account via `group_fleet_by_account()` — box list + $
    subtotal per account, a real `%`/reset for EVERY account with at least
    one reachable contributing host (its own, or the reporting box's own
    for its own account), an honest "% neznamy" only when genuinely no
    host of that account could be resolved (see `group_fleet_by_account()`'s
    own docstring for why), and any host with no known account_email in its
    own trailing "neznamy ucet" bucket."""
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
    latest_per_host = (shown[-1].get("per_host") or {}) if shown else {}
    acct_groups = group_fleet_by_account(latest_per_host, cache, now=now)
    lines.append("")
    lines.append("  ucty (box -> Anthropic ucet, poslednej hodiny):")
    if not acct_groups:
        lines.append("    (ziadne dostupne data o uctoch pre tuto hodinu)")
    for g in acct_groups:
        label = g["account"] or "neznamy ucet"
        if g["weekly_pct"] is not None:
            window = ("  (tyzdenny limit %s%%, reset %s)"
                      % (g["weekly_pct"], g["resets_at"] or "?"))
        elif g["account"] is None:
            window = ""
        else:
            window = "  (% neznamy — nepodarilo sa zistit z ziadneho boxu tohto uctu)"
        lines.append("    %s: %s  celkovo=$%.2f%s"
                     % (label, ", ".join(g["hosts"]), g["total_usd"], window))
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


# --------------------------------------------------------------------------- #
# #81 (2026-07-26) -- HOURLY BURN ALERT. Job 16 above only ever WRITES the
# hourly fleet row; nothing ever LOOKS at it against a reference and pings
# on its own. The user's own words: "jedina vec, ktora to ma dnes robit, je
# to, ze si na to spomeniem... a prave pri incidente spotreba vyskoci
# najviac" (the only thing that does this today is remembering to check --
# and exactly during an incident, when spend spikes most, there's no time
# to remember). Pure comparison + Slovak message-render over already-
# collected `fleet.jsonl` rows -- `watchdog.burn_alert_job` (job 19) is the
# thin wiring/dedup/send wrapper around this, mirroring how job 16 already
# wraps `fleet_budget_alert` above. Three independent thresholds (env-
# overridable, sane defaults): an absolute $ ceiling for one hour, a
# multiple of the MEDIAN of the last N hours, and crossing a whole step of
# the weekly usage window (job 16 already stamps `weekly_pct` onto every
# row, #55) -- any one firing sends ONE combined message.
# --------------------------------------------------------------------------- #

# #149 re-baseline (measured 2026-07-31, dev1, `~/.claude/projects`, 48h
# window ending ~2026-07-31T16:xx UTC, throwaway script under
# /tmp/claude-149/): OLD (pre-#149) discovery+per-line `scan()` totaled
# $1666.09 across that window (main-tree only, no request dedup — itself
# inflated ~2.13x by the #150-class per-line duplicate counting the OLD code
# never corrected); NEW (post-#149) discovery+per-request `scan()` totaled
# $2311.90 (both trees, deduped) -- a 1.39x TOTAL ratio. The single busiest
# hour moved from $88.67 (old) to $103.62 (new, a DIFFERENT hour) -- a 1.17x
# PEAK ratio, smaller than the total ratio because request-dedup shrinks the
# main-tree contribution even as subagent spend gets added. The 1.39x TOTAL
# ratio is the more stable of the two (44 hourly samples vs one) -- it is
# what the F5 recalibration below scales by.
#
# F5 ABS recalibration (measured 2026-07-31, dev1, read-only against
# `~/.claude/burn-history/fleet.jsonl`): the FIRST cut above (20.0 * 1.39,
# rounded UP to 30.0) was calibrated against dev1's OWN hourly buckets --
# but job 19 (`watchdog.burn_alert_job`) feeds `hourly_burn_alert()` job
# 16's FLEET-WIDE MERGED `total_usd` (every managed host summed by
# `merge_fleet_row()`), a materially bigger population. Against that
# population $30 fired on 264 of 317 real old-scope (pre-#149) hourly rows
# (83%, sample: 2026-07-25..2026-07-31) -- an alert that fires on 83% of
# hours is noise, not an alarm. Recalibrated from the population that
# actually hits the threshold: those 317 old-scope `fleet.jsonl` rows have
# a p95 total_usd of $151.27; scaled by the SAME measured 1.39x old->new
# ratio above (151.27 * 1.39 ~= 210.27) and rounded to a clean $210 -- a
# threshold a p95-by-construction population crosses on roughly 1 hour in
# 20 (<5%), not 5 in 6.
BURN_ALERT_ABS_USD = 210.0         # one hour above this many USD alone triggers
BURN_ALERT_REL_MULT = 3.0          # one hour more than this many x the median
                                    # of the last BURN_ALERT_REL_WINDOW hours
BURN_ALERT_REL_WINDOW = 6
BURN_ALERT_WEEKLY_STEP_PCT = 5.0   # crossing each whole this-many-percent step
                                    # of the weekly window triggers


def _median(vals):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _weekly_step_crossed(prev_pct, cur_pct, step):
    """True iff the weekly usage % genuinely INCREASED since the previous
    hourly row and moved into a NEW whole `step`-sized bucket -- e.g.
    77 -> 80 with step=5: floor(77/5)=15, floor(80/5)=16, crossed. A drop
    (a weekly-window RESET, or a missing sample on either side) is never
    treated as a crossing -- only a genuine forward step counts."""
    if prev_pct is None or cur_pct is None or cur_pct <= prev_pct:
        return False
    return int(cur_pct // step) > int(prev_pct // step)


def hourly_burn_alert(rows, abs_usd=None, rel_mult=None, rel_window=None,
                      weekly_step_pct=None):
    """None, or `{"hour_bucket", "reasons", "message"}` for the LATEST
    hourly row in `rows` (job 16's merged `fleet.jsonl`, oldest-first,
    newest-last) -- the trigger for job 19's ONE hourly deduped Discord
    ping. `rows` empty -> None (nothing to evaluate yet). A quiet hour
    (no threshold crossed) also returns None -- the caller sends nothing.

    The three thresholds are independent; ANY one firing produces ONE
    combined message (never a ping per threshold) via `render_burn_alert`.
    `reasons` lists which threshold(s) actually fired, in plain terms, for
    the job's own log line -- never shown to the user (the message itself
    is user-facing; `reasons` is operator-facing).

    #149: the REL-median and weekly-step comparisons only consider PRIOR
    rows whose `scope` matches the LATEST row's own `scope` (F3: an earlier
    version filtered priors to a hardcoded `scope == "agents"` without ever
    checking the latest row's own scope, so tagged priors + an untagged
    latest still compared across the boundary -- fixture: tagged $1 priors
    + an untagged $50 latest read as a false 50x median spike). Comparing
    across the boundary would compare against a baseline that either
    excludes or includes subagent spend differently, roughly doubling every
    figure and firing on the discontinuity itself rather than on a real
    spike. A latest row with NO `scope` key (pre-#149) matches only other
    untagged priors, so old-vs-old history stays comparable mid-rollout
    too. ABS is unaffected -- it only ever reads the CURRENT (latest) row.
    The "previous N hours" $ figures in the rendered MESSAGE stay unscoped
    -- that is informational trend context for the reader, not a threshold
    decision.

    REL also has an explicit WARM-UP (F4): fewer than `max(3, rel_window //
    2)` same-scope prior samples means it may not fire at all, however far
    above the median the latest hour sits -- with only 1-2 priors, a single
    stale/uncharacteristic hour becomes the whole median (measured live: a
    lone $2 prior let a $7 hour read as "3x the median"). The weekly-step
    check has no equivalent warm-up threshold; its own `prev_pct is None`
    guard is already a genuine warm-up, since a step-crossing check has
    nothing to compare against without at least one prior sample."""
    if not rows:
        return None
    abs_usd = BURN_ALERT_ABS_USD if abs_usd is None else abs_usd
    rel_mult = BURN_ALERT_REL_MULT if rel_mult is None else rel_mult
    rel_window = BURN_ALERT_REL_WINDOW if rel_window is None else rel_window
    weekly_step_pct = (BURN_ALERT_WEEKLY_STEP_PCT if weekly_step_pct is None
                      else weekly_step_pct)
    latest = rows[-1]
    hb = hour_bucket_of_ts(latest.get("ts"))
    total_usd = latest.get("total_usd", 0.0) or 0.0
    prev_rows = rows[max(0, len(rows) - 1 - rel_window):-1]
    want_scope = latest.get("scope")
    scoped_prev = [r for r in rows[:-1] if r.get("scope") == want_scope]
    scoped_prev_window = scoped_prev[-rel_window:] if rel_window else scoped_prev
    prev_totals = [r.get("total_usd", 0.0) or 0.0 for r in scoped_prev_window]
    median_prev = _median(prev_totals)
    min_rel_samples = max(3, rel_window // 2) if rel_window else 3
    reasons = []
    if total_usd > abs_usd:
        reasons.append("nad absolutnym prahom $%.2f" % abs_usd)
    if (median_prev is not None and median_prev > 0
            and len(prev_totals) >= min_rel_samples
            and total_usd > rel_mult * median_prev):
        reasons.append("%.1fx median poslednych %d hodin ($%.2f)"
                       % (rel_mult, len(prev_totals), median_prev))
    prev_pct = scoped_prev[-1].get("weekly_pct") if scoped_prev else None
    cur_pct = latest.get("weekly_pct")
    if _weekly_step_crossed(prev_pct, cur_pct, weekly_step_pct):
        reasons.append("tyzdenny krok %.0f%% -> %.0f%%" % (prev_pct, cur_pct))
    if not reasons:
        return None
    return {"hour_bucket": hb, "reasons": reasons,
           "message": render_burn_alert(latest, prev_rows, prev_pct, cur_pct)}


def render_burn_alert(latest, prev_rows, prev_pct=None, cur_pct=None):
    """Slovak, phone-readable burn-alert message -- the ticket's own
    example format: `Spotreba 14:00 -- $64.88 (337 sprav), tyzdenne 77 ->
    80 %` + the top 2 hosts by $ for that hour + the $ totals of the up to
    3 hours immediately before it (most recent first). `prev_pct`/
    `cur_pct` are omitted from the header entirely when either is None
    (no weekly-window crossing to report for this hour)."""
    ts = latest.get("ts") or "?"
    hour_label = ts[11:16] if len(ts) >= 16 else ts   # "...T14:00+02:00" -> "14:00"
    total_usd = latest.get("total_usd", 0.0) or 0.0
    total_msgs = latest.get("total_msgs", 0)
    weekly_part = (", tyzdenne %.0f -> %.0f %%" % (prev_pct, cur_pct)
                  if (prev_pct is not None and cur_pct is not None) else "")
    lines = ["Spotreba %s -- $%.2f (%d sprav)%s"
            % (hour_label, total_usd, total_msgs, weekly_part)]
    per_host = latest.get("per_host") or {}
    top = sorted((kv for kv in per_host.items() if "error" not in kv[1]),
                key=lambda kv: -kv[1].get("usd", 0.0))[:2]
    if top:
        lines.append("najviac: " + " · ".join(
            "%s $%.2f (%d sprav, ctx %s)"
            % (h, v.get("usd", 0.0), v.get("msgs", 0), _fmt_int(v.get("avg_ctx", 0)))
            for h, v in top))
    prev3 = list(reversed(prev_rows[-3:]))
    if prev3:
        n = len(prev3)
        unit = "hodinu" if n == 1 else ("hodiny" if n < 5 else "hodin")
        lines.append("predchadzajuce %d %s: " % (n, unit) + " · ".join(
            "$%.2f" % (r.get("total_usd", 0.0) or 0.0) for r in prev3))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# #130 — the standing MAIN vs SUBAGENT cost meter (`airuleset.py delegation`).
#
# ROOT CAUSE this exists to fix: a PER-PROJECT, per-request-deduped MAIN vs
# SUBAGENT split over a short (default 12h) window — `scan()`'s own bucket
# set (by model / day / hour / project) had no such split at all, and
# building one required the SAME request-dedup fix #150 later gave `scan()`
# itself (below). `scan_split()` was purpose-built rather than retrofitted
# onto `scan()`'s older shape.
#
# Until #149 re-baselined it, `scan()` was ALSO left deliberately additive-
# only next to this module, for a separate reason: `scan()` fed
# `hourly_snapshot()` -> the watchdog's hourly `snapshots.jsonl` ->
# `compare_changes()`, the fleet feed, and `hourly_burn_alert()`'s live
# thresholds, and folding subagent spend into it would have roughly doubled
# every hourly figure against a history recorded without it — re-firing the
# burn alerts on the discontinuity itself rather than on a real spike, which
# #130 explicitly excluded as out of scope. #149 reconciled that
# discontinuity directly: `scan()` now walks the same subagent-reachable
# tree this module always has (`_split_transcripts()`), and every row this
# module's `hourly_snapshot()`/`merge_fleet_row()` produce carries a
# `scope` tag so pre- and post-#149 history is never compared as if it were
# one continuous series.
# --------------------------------------------------------------------------- #

# Relative units, NOT a price. This is the Opus row of PRICE divided by 5, i.e.
# deliberately TIER-NEUTRAL: the unit measures VOLUME and does not silently
# absorb model-tier drift (tracked separately). Every rendering prints it.
COST_UNIT_WEIGHTS = {"in": 1.0, "cache_w": 1.25, "cache_r": 0.1, "out": 5.0}

_SPLIT_COUNTERS = ("turns", "in", "cache_w", "cache_r", "out", "sessions")


def cost_units(row):
    """The stated weighting applied to one row. Relative units — never a price."""
    return sum(float(row.get(k, 0) or 0) * w
               for k, w in COST_UNIT_WEIGHTS.items())


def ctx_per_turn(row):
    """Mean INPUT context carried per turn = (input + cache_write + cache_read)
    / turns. Zero turns yields 0 rather than dividing by zero."""
    turns = row.get("turns", 0) or 0
    if not turns:
        return 0
    ctx = sum(int(row.get(k, 0) or 0) for k in ("in", "cache_w", "cache_r"))
    return int(round(ctx / turns))


def _split_row():
    return {k: 0 for k in _SPLIT_COUNTERS}


def _split_add(dst, src):
    """Sum RAW counters only. Derived fields (`units`, `ctx_per_turn`) are
    recomputed by `finalize_split_row` after merging — summing a mean would be
    wrong, and a remote box's JSON carries both."""
    for k in _SPLIT_COUNTERS:
        dst[k] = dst.get(k, 0) + int(src.get(k, 0) or 0)


def finalize_split_row(row):
    out = {k: int(row.get(k, 0) or 0) for k in _SPLIT_COUNTERS}
    out["units"] = round(cost_units(out), 1)
    out["ctx_per_turn"] = ctx_per_turn(out)
    return out


def _git_remote_url(cwd):
    import subprocess
    r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def repo_of_cwd(cwd, _runner=None):
    """`owner/name` for the repo checked out at `cwd`, or None.

    The project DIRECTORY NAME cannot be used for this: Claude Code encodes the
    cwd by replacing `/` with `-`, and a literal `-` in a path segment encodes
    identically — `-home-newlevel-devel-forestshop-parovanie-produktov` is
    ambiguous between two real paths. The `cwd` field recorded on the transcript
    lines themselves is unambiguous, so that is what this takes.

    Returns None (never a guess) when git is unavailable, the path is not a
    checkout, or the remote URL does not parse — a project with no resolvable
    repo simply gets no cost-per-ticket denominator.
    """
    if not cwd:
        return None
    runner = _runner or _git_remote_url
    try:
        url = runner(cwd)
    except Exception:
        return None
    if not url:
        return None
    u = str(url).strip()
    if u.endswith(".git"):
        u = u[:-4]
    if "://" in u:
        parts = u.split("://", 1)[1].split("/")[1:]
    elif "@" in u and ":" in u:
        parts = u.split(":", 1)[1].split("/")
    else:
        parts = u.split("/")
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    return "/".join(parts[-2:])


def _split_transcripts(root):
    """Yield `(path, project, kind)` for every transcript under `root`.

    `kind` is "main" for a transcript sitting directly in the project dir, and
    "sub" for one under a `subagents/` component at any depth. Anything else
    below a project dir is SKIPPED and counted, rather than silently folded into
    either bucket — if Claude Code grows a new transcript location, that shows
    up as a non-zero `skipped_other` instead of a quietly wrong split.
    """
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return
    for proj in entries:
        pdir = os.path.join(root, proj)
        if not os.path.isdir(pdir):
            continue
        for dirpath, _dirnames, filenames in os.walk(pdir):
            rel = os.path.relpath(dirpath, pdir)
            comps = [] if rel == "." else rel.split(os.sep)
            if not comps:
                kind = "main"
            elif "subagents" in comps:
                kind = "sub"
            else:
                kind = "other"
            for fn in sorted(filenames):
                if fn.endswith(".jsonl"):
                    yield os.path.join(dirpath, fn), proj, kind


def _fold_usage_line(e, order, reqs):
    """Fold one parsed transcript event's usage into a per-request accumulator.

    Requests are keyed by `requestId` (falling back to `message.id`), and each
    key keeps the LAST usage snapshot seen for it — Claude Code writes one API
    response as several transcript lines (one per content block: `thinking`,
    then each `tool_use`), each carrying a COPY of the same request-level
    `usage`; the trailing block also carries the response's real
    `output_tokens`, so keeping the last (max for `out`, since an earlier
    block can already carry the final count too) is correct for every field
    (#150 — a request counted per-line inflated turns/tokens ~2.13x on real
    data).

    A `<synthetic>` placeholder (interrupt / error) carries a usage block of
    four zeros and a UUID where a requestId would be — dropped on the ZERO
    USAGE, never on the model string, so a placeholder written without that
    marker is dropped too (measured live: counting it as a request produced a
    context of 0 for the dispatch it belonged to).

    `order`/`reqs` are mutated in place, scoped to one file's whole scan.
    Returns the folded requestId, or None if this event carried no usable
    usage — a caller checks that to know whether to also capture side data
    (cwd, model) for the folded request.
    """
    msg = e.get("message") or {}
    u = msg.get("usage") or {}
    if not u:
        return None
    if not any(int(u.get(k) or 0) for k in
               ("input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens")):
        return None
    rid = e.get("requestId") or msg.get("id")
    if rid is None:
        rid = "line:%d" % len(order)
    if rid not in reqs:
        order.append(rid)
        reqs[rid] = {}
    r = reqs[rid]
    t = _parse_ts(e.get("timestamp") or "")
    if t is not None and t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    r["ts"] = t or r.get("ts")
    r["in"] = int(u.get("input_tokens") or 0)
    r["cache_w"] = int(u.get("cache_creation_input_tokens") or 0)
    r["cache_r"] = int(u.get("cache_read_input_tokens") or 0)
    r["out"] = max(int(r.get("out", 0)), int(u.get("output_tokens") or 0))
    return rid


def scan_split(root, hours=12, now=None, repo_resolver=None):
    """Per-project MAIN vs SUBAGENT token attribution over a `hours` window.

    Every line is bucketed by ITS OWN `timestamp`. File mtime is used ONLY as a
    cheap skip filter — a file last written before the window cannot contain an
    in-window line — never as a line's time; #130 is explicit that mtime has
    produced wrong answers here before.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=hours)
    resolver = repo_of_cwd if repo_resolver is None else repo_resolver
    projects = {}
    files = 0
    lines = 0
    skipped_other = 0
    for path, proj, kind in _split_transcripts(root):
        if kind == "other":
            skipped_other += 1
            continue
        try:
            mt = datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc)
            if mt < cutoff:
                continue
        except OSError:
            continue
        files += 1
        row = _split_row()
        cwd = None
        try:
            fh = open(path, "r", errors="replace")
        except OSError:
            continue
        with fh:
            order = []
            reqs = {}
            req_cwd = {}
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                rid = _fold_usage_line(e, order, reqs)
                if rid is not None:
                    req_cwd[rid] = e.get("cwd") or req_cwd.get(rid)
            # One request may span several lines (#150) — dedupe FIRST, then
            # window-filter and aggregate by the LAST-seen timestamp of each
            # deduped request, never per raw line.
            for rid in order:
                r = reqs[rid]
                t = r.get("ts")
                if t is None or t < cutoff or t > now:
                    continue
                lines += 1
                row["turns"] += 1
                row["in"] += r["in"]
                row["cache_w"] += r["cache_w"]
                row["cache_r"] += r["cache_r"]
                row["out"] += r["out"]
                if cwd is None:
                    cwd = req_cwd.get(rid)
        if not row["turns"]:
            continue
        row["sessions"] = 1
        slot = projects.setdefault(
            proj, {"cwd": None, "main": _split_row(), "sub": _split_row()})
        if slot["cwd"] is None:
            slot["cwd"] = cwd
        _split_add(slot[kind], row)
    out = {}
    for proj, slot in sorted(projects.items()):
        out[proj] = {
            "cwd": slot["cwd"],
            "repo": resolver(slot["cwd"]) if slot["cwd"] else None,
            "main": finalize_split_row(slot["main"]),
            "sub": finalize_split_row(slot["sub"]),
        }
    return {
        "hours": hours,
        "window_start": cutoff.isoformat(),
        "window_end": now.isoformat(),
        "weights": dict(COST_UNIT_WEIGHTS),
        "files_scanned": files,
        "usage_lines": lines,
        "skipped_other": skipped_other,
        "projects": out,
    }


def split_report(hours=12, root=None):
    """`scan_split()` over THIS box's transcript store, tagged host/user so a
    multi-host `merge_splits()` can attribute per box."""
    root = root or os.path.expanduser("~/.claude/projects")
    data = scan_split(root, hours=hours)
    data["host"] = os.uname().nodename
    data["user"] = os.environ.get("USER", "?")
    return data


def _split_rows_of(rep):
    """Yield `(host, project, row)` from EITHER report shape.

    A local `split_report()` carries `projects` keyed by project, with the host
    on the report. A REMOTE box is collected by invoking its own deployed
    `airuleset.py delegation --json`, which prints the ALREADY-MERGED shape:
    `by_project` keyed `<host>:<project>`, with the host on each row. Reading
    only the first shape made every remote box parse cleanly and contribute
    nothing — a reachable-but-empty box is indistinguishable from an idle one,
    so the coordinator printed a dev1-only table as a fleet total with no WARN.
    """
    host = rep.get("host") or "?"
    projects = rep.get("projects")
    if isinstance(projects, dict):
        for proj, prow in projects.items():
            if isinstance(prow, dict):
                yield host, proj, prow
        return
    merged = rep.get("by_project")
    if isinstance(merged, dict):
        for key, prow in merged.items():
            if not isinstance(prow, dict):
                continue
            # The row's OWN host wins — it names the box the work ran on, not
            # the box that collected it.
            rhost = prow.get("host") or (
                key.split(":", 1)[0] if ":" in key else host)
            rproj = prow.get("project") or (
                key.split(":", 1)[1] if ":" in key else key)
            yield rhost, rproj, prow


def merge_splits(reports):
    """Merge N reports of EITHER shape (see `_split_rows_of`). `by_project`
    keys are `<host>:<project>` so the same project name on two boxes cannot
    collide."""
    by_project = {}
    totals = {"main": _split_row(), "sub": _split_row()}
    files = 0
    usage_lines = 0
    hours = None
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        if rep.get("hours"):
            hours = rep["hours"]
        files += int(rep.get("files_scanned", 0) or 0)
        usage_lines += int(rep.get("usage_lines", 0) or 0)
        for host, proj, prow in _split_rows_of(rep):
            key = "%s:%s" % (host, proj)
            dst = by_project.setdefault(key, {
                "host": host, "project": proj,
                "cwd": prow.get("cwd"), "repo": prow.get("repo"),
                "main": _split_row(), "sub": _split_row(),
                "closed_tickets": None, "units_per_ticket": None,
            })
            if dst.get("repo") is None:
                dst["repo"] = prow.get("repo")
            if dst.get("cwd") is None:
                dst["cwd"] = prow.get("cwd")
            for kind in ("main", "sub"):
                src = prow.get(kind) or {}
                _split_add(dst[kind], src)
                _split_add(totals[kind], src)
    for row in by_project.values():
        for kind in ("main", "sub"):
            row[kind] = finalize_split_row(row[kind])
    ordered = dict(sorted(
        by_project.items(),
        key=lambda kv: -(kv[1]["main"]["units"] + kv[1]["sub"]["units"])))
    return {
        "hours": hours,
        "weights": dict(COST_UNIT_WEIGHTS),
        "files_scanned": files,
        "usage_lines": usage_lines,
        "by_project": ordered,
        "totals": {k: finalize_split_row(v) for k, v in totals.items()},
    }


def units_per_ticket(units, closed):
    """Cost per completed ticket, or None when nothing closed in the window.

    None, never zero and never infinity: camera-box's real 12h measurement was
    131.3M units against 0 closed tickets, which is a spend with no ticket — a
    materially different statement from "cheap per ticket", and the rendering
    must not be able to blur the two."""
    if not closed:
        return None
    return float(units) / float(closed)


def _fmt_units(u):
    u = float(u or 0)
    if abs(u) >= 1e6:
        return "%.1fM" % (u / 1e6)
    if abs(u) >= 1e3:
        return "%.1fk" % (u / 1e3)
    return "%.0f" % u


def render_split(merged, hours=12):
    """Human table. States the weighting on every render and never prints a
    currency figure — the unit is relative, and #130 requires it not be
    presented as a price."""
    w = merged.get("weights") or COST_UNIT_WEIGHTS
    rows = merged.get("by_project") or {}
    totals = merged.get("totals") or {}
    grand = sum((totals.get(k) or {}).get("units", 0) for k in ("main", "sub"))
    out = []
    out.append("airuleset delegation -- MAIN vs SUBAGENT, last %sh" % hours)
    out.append("  weighting (relative units, NOT a price): "
               "input x%.2f + cache_write x%.2f + cache_read x%.2f + output x%.2f"
               % (w.get("in", 0), w.get("cache_w", 0),
                  w.get("cache_r", 0), w.get("out", 0)))
    out.append("")
    out.append("  %-34s %-5s %8s %9s %10s %10s %7s"
               % ("host:project", "row", "turns", "sessions", "units",
                  "ctx/turn", "share"))
    for key, row in rows.items():
        for kind, label in (("main", "MAIN"), ("sub", "SUB")):
            r = row.get(kind) or {}
            share = (r.get("units", 0) / grand * 100) if grand else 0.0
            out.append("  %-34s %-5s %8s %9s %10s %10s %6.1f%%"
                       % (key if kind == "main" else "", label,
                          _fmt_int(r.get("turns", 0)),
                          _fmt_int(r.get("sessions", 0)),
                          _fmt_units(r.get("units", 0)),
                          _fmt_int(r.get("ctx_per_turn", 0)),
                          share))
        closed = row.get("closed_tickets")
        if closed is not None:
            m = (row.get("main") or {}).get("units", 0)
            s = (row.get("sub") or {}).get("units", 0)
            pm = units_per_ticket(m, closed)
            ps = units_per_ticket(s, closed)
            if pm is None or ps is None:
                out.append("  %-34s per closed ticket: — (0 closed in window, "
                           "%s units spent)" % ("", _fmt_units(m + s)))
            else:
                out.append("  %-34s per closed ticket (%d closed): "
                           "MAIN %s · SUB %s"
                           % ("", closed, _fmt_units(pm), _fmt_units(ps)))
    out.append("")
    for kind, label in (("main", "MAIN"), ("sub", "SUB")):
        r = totals.get(kind) or {}
        share = (r.get("units", 0) / grand * 100) if grand else 0.0
        out.append("  TOTAL %-5s %s turns · %s units (%.1f%%) · %s ctx/turn"
                   % (label, _fmt_int(r.get("turns", 0)),
                      _fmt_units(r.get("units", 0)), share,
                      _fmt_int(r.get("ctx_per_turn", 0))))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# #131 — per-DISPATCH floor vs in-dispatch growth (`delegation --floor`).
#
# ROOT CAUSE this exists to fix: #130's `scan_split()` answers "how much does a
# subagent turn carry on average" and nothing else. Its per-file loop (see
# above) sums every usage line of a transcript into one accumulator row and
# discards per-line ordering, and `ctx_per_turn()` is a single MEAN over the
# whole window — so the split #131 asks for (the fixed prefix a dispatch starts
# with, vs the growth it accumulates) is not merely hard to read out of #130's
# output, it has no representation in it.
#
# Two counting errors are corrected here, both of which made the earlier hand
# figures wrong:
#
#   1. A transcript LINE IS NOT A TURN. Claude Code writes ONE assistant API
#      response as several lines — one per content block (`thinking`, then each
#      `tool_use`) — and every one of those lines carries a COPY of the same
#      request-level `usage`. Measured on a real transcript
#      (`agent-a8f4affe3e4d342cb.jsonl`): 236 usage lines, 111 distinct
#      `requestId`s, with one request spread over 9 lines each restating the
#      same `cache_read_input_tokens: 118,307`. Counting lines inflates turns
#      ~2x and multiply-counts identical input tokens, so everything here
#      dedupes by `requestId` first.
#
#   2. A FILE IS NOT AN IN-WINDOW DISPATCH. A dispatch that began before the
#      window has no floor inside it — its first in-window request is already
#      carrying accumulated growth. Those straddlers are excluded from every
#      distribution and reported as their own count, rather than being averaged
#      in as if they were floors.
#
# Additive, exactly as #130 was: `scan()` is untouched. `scan_split()` ALSO
# dedupes by `requestId` now (#150, fixed) — it shares the exact fold used
# here (`_fold_usage_line`, defined above `scan_split()`) rather than
# reimplementing it, so this counting error #1 no longer applies to either
# instrument. That WAS a baseline move (`scan_split()`'s reported `turns` and
# `units` dropped ~2x against history recorded line-based) — #150 took it
# deliberately, because unlike `scan()` (which #130 explicitly declined to
# re-baseline), `scan_split()` has no persisted history and no alert
# thresholds: it feeds only `split_report()` / `airuleset.py delegation`, so
# there was no baseline to protect and the reported numbers simply became
# correct.
# --------------------------------------------------------------------------- #

_DISPATCH_COUNTERS = ("in", "cache_w", "cache_r", "out")


def distribution(values):
    """n / min / p25 / median / p75 / p90 / max / mean over `values`.

    Returns None — never a row of zeros — for an empty input, because #131's
    whole complaint is that a single fabricated summary number hides the shape
    of the data. Quantiles use the nearest-rank convention, so every reported
    value is an OBSERVED one rather than an interpolation between two
    dispatches that do not exist.
    """
    vals = sorted(float(v) for v in values)
    if not vals:
        return None

    def q(p):
        idx = int(round(p * (len(vals) - 1)))
        return vals[idx]

    return {
        "n": len(vals),
        "min": int(vals[0]),
        "p25": int(q(0.25)),
        "median": int(q(0.5)),
        "p75": int(q(0.75)),
        "p90": int(q(0.9)),
        "max": int(vals[-1]),
        "mean": int(round(sum(vals) / len(vals))),
    }


def read_dispatch(path):
    """One `agent-*.jsonl` transcript reduced to ONE dispatch row.

    Requests are keyed by `requestId` (falling back to `message.id`) in
    first-appearance order, and each key keeps the LAST usage snapshot seen for
    it: the trailing content block of a response carries the request's real
    `output_tokens`, while the earlier blocks carry partial values. The input
    side is identical on every line of a request, so taking the last is also
    correct for context.

    `attribution_agent` (#211) is the FIRST `attributionAgent` value seen on
    any line -- Claude Code's own per-line stamp on a dispatched worker's
    transcript (present even though the child transcript never records
    `subagent_type` directly). It is a FALLBACK for `scan_dispatches`, used
    only when the parent-transcript join yields nothing.

    Returns None when the file holds no usage line at all.
    """
    order = []
    reqs = {}
    prompt_chars = None
    skill_chars = 0
    tool_names = 0
    cwd = None
    model = None
    attribution = None
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if cwd is None:
                cwd = e.get("cwd")
            if attribution is None:
                attribution = e.get("attributionAgent")
            etype = e.get("type")
            if etype == "attachment":
                a = e.get("attachment") or {}
                if a.get("type") == "skill_listing":
                    skill_chars = len(a.get("content") or "")
                elif a.get("type") == "deferred_tools_delta":
                    tool_names = len(a.get("addedNames") or [])
                continue
            msg = e.get("message") or {}
            if prompt_chars is None and etype == "user":
                c = msg.get("content")
                if c is not None:
                    prompt_chars = len(c if isinstance(c, str)
                                       else json.dumps(c))
            # Dedup/last-wins/synthetic-placeholder-drop is shared with
            # scan_split() (#150) — see _fold_usage_line's own docstring.
            rid = _fold_usage_line(e, order, reqs)
            if rid is not None:
                model = model or msg.get("model")
    if not order:
        return None
    ctx = lambda r: r["in"] + r["cache_w"] + r["cache_r"]  # noqa: E731
    first, last = reqs[order[0]], reqs[order[-1]]
    row = {
        "path": path,
        "agent_id": _agent_id_of(path),
        "cwd": cwd,
        "model": model,
        "turns": len(order),
        "floor": ctx(first),
        "last": ctx(last),
        "growth": ctx(last) - ctx(first),
        "total_ctx": sum(ctx(reqs[k]) for k in order),
        "started": first.get("ts"),
        "ended": last.get("ts"),
        "prompt_chars": prompt_chars or 0,
        "skill_listing_chars": skill_chars,
        "deferred_tool_names": tool_names,
        "attribution_agent": attribution,
    }
    for k in _DISPATCH_COUNTERS:
        row[k] = sum(reqs[j][k] for j in order)
    row["units"] = round(cost_units(row), 1)
    return row


def _agent_id_of(path):
    base = os.path.basename(path)
    if base.startswith("agent-") and base.endswith(".jsonl"):
        return base[len("agent-"):-len(".jsonl")]
    return None


def agent_types_from_parent(parent_path, agent_ids):
    """Map `agentId` -> the `subagent_type` its dispatch was launched with.

    The subagent transcript itself never records the type. The PARENT session
    transcript does, indirectly: the `tool_result` of the `Agent`/`Task` call
    quotes the new `agentId`, and its `tool_use_id` resolves to the `tool_use`
    block carrying `input.subagent_type`. This join is worth its cost because
    the measurement shows the dispatch floor is bimodal by agent TYPE and by
    almost nothing else.

    Deliberately UNWINDOWED — this reads the whole parent file regardless of
    any report window; only the caller's SPEND stays windowed (#211: a filed
    hypothesis that a launching `tool_use` outside the window broke the join
    did not reproduce empirically, because this was already true).

    #211: `pending[aid]` keeps EVERY candidate `tool_use_id` a matching
    `tool_result` names, in file order, not just the last one seen. A single
    overwrite was the real bug — a LATER, unrelated tool_result can mention
    the same agent id substring (measured live: a `TaskStop` call's own
    confirmation text, "Successfully stopped task: <id> ...", echoes it) and
    its tool_use_id is never in `uses` (TaskStop is not Agent/Task), so an
    unconditional overwrite silently discarded the correct EARLIER match —
    the real launch, which does resolve. Trying every candidate in order and
    keeping the first that resolves survives any number of such later
    mentions.

    An id that cannot be resolved through any candidate is simply absent
    from the mapping — callers report None (or fall back to the child
    transcript's own `attributionAgent` stamp) rather than guessing a type.
    """
    wanted = set(agent_ids or ())
    out = {}
    if not wanted or not os.path.exists(parent_path):
        return out
    uses = {}
    pending = {}
    try:
        fh = open(parent_path, "r", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            content = (e.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") in ("Agent",
                                                                     "Task"):
                    inp = b.get("input") or {}
                    uses[b.get("id")] = inp.get("subagent_type") or "(default)"
                elif b.get("type") == "tool_result":
                    blob = json.dumps(b.get("content"))
                    for aid in wanted:
                        if aid in blob:
                            pending.setdefault(aid, []).append(
                                b.get("tool_use_id"))
    # Review follow-up (#210 sibling pass): a resolved value is ALWAYS drawn
    # from `uses`, never invented — `tid in uses` is the sole resolution
    # test, and `uses` is populated ONLY by a real Agent/Task `tool_use`
    # block (the launch itself, added above). That is the launch-before-
    # mention invariant this whole function relies on: a candidate resolves
    # only because its launch was genuinely seen, never through a coincidental
    # substring match on some OTHER tool's tool_use_id.
    for aid, tids in pending.items():
        for tid in tids:
            if tid in uses:
                out[aid] = uses[tid]
                assert out[aid] in uses.values()  # cheap: drawn from uses, not invented
                break
    return out


def scan_dispatches(root, hours=12, now=None):
    """Per-DISPATCH floor / growth / turn rows over a `hours` window.

    Only dispatches whose FIRST request falls inside the window are returned —
    see the header comment. `straddling` counts the rest, so an unusually large
    straddler count is visible instead of quietly shrinking the sample.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=hours)
    rows = []
    straddling = 0
    files = 0
    by_parent = {}
    for path, proj, kind in _split_transcripts(root):
        if kind != "sub":
            continue
        try:
            mt = datetime.datetime.fromtimestamp(
                os.path.getmtime(path), datetime.timezone.utc)
            if mt < cutoff:
                continue
        except OSError:
            continue
        files += 1
        row = read_dispatch(path)
        if row is None:
            continue
        started = row.get("started")
        if started is None or started < cutoff or started > now:
            straddling += 1
            continue
        row["project"] = proj
        rows.append(row)
        # `<root>/<project>/<sid>/subagents/agent-*.jsonl` -> the parent
        # session transcript is `<root>/<project>/<sid>.jsonl`.
        session_dir = os.path.dirname(os.path.dirname(path))
        by_parent.setdefault(session_dir + ".jsonl", set()).add(row["agent_id"])
    types = {}
    for parent, ids in by_parent.items():
        types.update(agent_types_from_parent(parent, ids))
    for row in rows:
        # #211: the parent-transcript join is the primary source; fall back
        # to the child's own `attributionAgent` stamp only when the join
        # resolves nothing at all. This `or` fallback depends on a join hit
        # NEVER being falsy: `agent_types_from_parent` only ever stores
        # `inp.get("subagent_type") or "(default)"` into `uses` (never "",
        # never None), so `types.get(...)` is either a genuine non-empty
        # string or a real miss (None) — never a falsy hit that would wrongly
        # fall through to the attribution stamp.
        row["agent_type"] = (types.get(row["agent_id"])
                             or row.get("attribution_agent"))
        row["started"] = row["started"].isoformat() if row["started"] else None
        row["ended"] = row["ended"].isoformat() if row["ended"] else None
    rows.sort(key=lambda r: -r["total_ctx"])
    return {
        "hours": hours,
        "window_start": cutoff.isoformat(),
        "window_end": now.isoformat(),
        "weights": dict(COST_UNIT_WEIGHTS),
        "files_scanned": files,
        "straddling": straddling,
        "dispatches": rows,
        "distributions": {
            "floor": distribution([r["floor"] for r in rows]),
            "last": distribution([r["last"] for r in rows]),
            "growth": distribution([r["growth"] for r in rows]),
            "turns": distribution([r["turns"] for r in rows]),
        },
        "accounting": floor_growth_totals(rows),
        "by_agent_type": by_agent_type(rows),
    }


def floor_growth_totals(rows):
    """Split total subagent context into the floor re-sent every turn and the
    growth accumulated inside dispatches — in TOKENS and, separately, in COST.

    The two are very different answers and reporting only the first overstates
    the lever: the floor is cache-WRITTEN once (weight 1.25) and cache-READ on
    every later turn (weight 0.1), so its share of spend is far below its share
    of tokens. The cost figures are a stated MODEL — floor x 1.25 once, then
    floor x 0.1 x (turns - 1) — not a re-derivation from the raw buckets, since
    a request's cache_read cannot be split into "floor part" and "growth part"
    from the transcript alone.
    """
    total_ctx = sum(r["total_ctx"] for r in rows)
    floor_ctx = sum(r["floor"] * r["turns"] for r in rows)
    units = sum(float(r.get("units", 0) or 0) for r in rows)
    w = COST_UNIT_WEIGHTS
    write_units = sum(r["floor"] * w["cache_w"] for r in rows)
    read_units = sum(r["floor"] * max(0, r["turns"] - 1) * w["cache_r"]
                     for r in rows)
    floor_units = write_units + read_units
    return {
        "dispatches": len(rows),
        "context": {
            "total": total_ctx,
            "floor": floor_ctx,
            "growth": total_ctx - floor_ctx,
            "floor_share": (floor_ctx / total_ctx) if total_ctx else None,
        },
        "cost": {
            "total_units": round(units, 1),
            "floor_write_units": round(write_units, 1),
            "floor_read_units": round(read_units, 1),
            "floor_units": round(floor_units, 1),
            "floor_share": (floor_units / units) if units else None,
        },
    }


def by_agent_type(rows):
    """Floor / turns / spend grouped by `subagent_type`.

    This grouping is the finding, not a convenience: the floor is bimodal by
    agent type, so a fleet-wide mean of it describes no real dispatch.
    """
    groups = {}
    for r in rows:
        groups.setdefault(r.get("agent_type") or "(unresolved)", []).append(r)
    out = {}
    for name, g in groups.items():
        acc = floor_growth_totals(g)
        out[name] = {
            "n": len(g),
            "floor": distribution([r["floor"] for r in g]),
            "turns": distribution([r["turns"] for r in g]),
            "last": distribution([r["last"] for r in g]),
            "total_ctx": acc["context"]["total"],
            "units": acc["cost"]["total_units"],
            "floor_share_ctx": acc["context"]["floor_share"],
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["total_ctx"]))


def import_closure_chars(path, _seen=None):
    """Characters in `path` plus every file it pulls in via a leading `@import`.

    This is what "the always-on ruleset" actually costs a dispatch:
    `~/.claude/CLAUDE.md` is a thin index and its 59 `@`-imported modules are
    the body. Each file counts ONCE (Claude Code injects it once), and a cycle
    terminates rather than recursing — both properties are locked by tests.
    Missing files count 0, so a partially-installed box under-reports instead
    of raising.
    """
    import re
    seen = _seen if _seen is not None else set()
    p = os.path.abspath(os.path.expanduser(path))
    if p in seen or not os.path.isfile(p):
        return 0
    seen.add(p)
    try:
        text = open(p, "r", errors="replace").read()
    except OSError:
        return 0
    total = len(text)
    for m in re.finditer(r"^@(\S+)", text, re.M):
        ref = m.group(1)
        if not ref.startswith(("/", "~")):
            ref = os.path.join(os.path.dirname(p), ref)
        total += import_closure_chars(ref, seen)
    return total


#: Named parts of a dispatch floor, in the order they are reported. Only these
#: are attributable from the box's own files plus the transcript; everything
#: else lands in the residual, which is NAMED rather than folded into a part.
FLOOR_PART_ORDER = ("always_on_ruleset", "project_claude_md",
                    "agent_definition", "skill_listing", "dispatch_prompt")

RESIDUAL_IS = ("base agent system prompt + loaded tool schemas + MCP server "
               "instructions — not recoverable from a transcript")


def floor_attribution(floor, chars, chars_per_token):
    """Attribute a measured floor to named parts at a stated chars-per-token.

    `chars` are real sizes: the transcript's own `skill_listing` content and
    dispatch prompt (exact), and the `@import` closure of the global and
    project `CLAUDE.md` plus the agent definition (read off disk). The rate is
    a parameter, not a constant, so the caller states which calibration it
    used and the number can be re-derived.

    The residual is reported, never absorbed. A NEGATIVE residual means the
    parts priced above what the dispatch actually carried — that is a real
    result (the floor did not contain everything assumed), so it is surfaced as
    `over_attributed` instead of being clamped to zero.
    """
    rate = float(chars_per_token or 0)
    parts = {}
    for name in FLOOR_PART_ORDER:
        c = int(chars.get(name, 0) or 0)
        parts[name] = int(round(c / rate)) if rate else 0
    attributed = sum(parts.values())
    residual = int(floor) - attributed
    return {
        "floor": int(floor),
        "chars_per_token": rate,
        "parts": parts,
        "attributed": attributed,
        "residual": residual,
        "residual_is": RESIDUAL_IS,
        "over_attributed": residual < 0,
    }


def render_floor(report, hours=12):
    """Human table for `delegation --floor`. Distributions, never one mean."""
    d = report.get("distributions") or {}
    acc = report.get("accounting") or {}
    ctx = acc.get("context") or {}
    cost = acc.get("cost") or {}
    w = report.get("weights") or COST_UNIT_WEIGHTS
    out = []
    out.append("airuleset delegation --floor -- per-DISPATCH floor vs "
               "in-dispatch growth, last %sh" % hours)
    out.append("  one row per dispatch (agent-*.jsonl); requests deduped by "
               "requestId, because one API response is written as several "
               "transcript lines")
    out.append("  %d dispatches STARTED in window · %d straddling (began "
               "earlier, no floor inside the window) · %d files read"
               % (len(report.get("dispatches") or []),
                  report.get("straddling", 0),
                  report.get("files_scanned", 0)))
    out.append("  weighting (relative units, NOT a price): "
               "input x%.2f + cache_write x%.2f + cache_read x%.2f + "
               "output x%.2f" % (w.get("in", 0), w.get("cache_w", 0),
                                 w.get("cache_r", 0), w.get("out", 0)))
    out.append("")
    out.append("  %-10s %5s %10s %10s %10s %10s %10s %10s"
               % ("", "n", "min", "p25", "median", "p75", "p90", "max"))
    for key, label in (("floor", "FLOOR"), ("last", "last turn"),
                       ("growth", "growth"), ("turns", "turns")):
        row = d.get(key)
        if not row:
            out.append("  %-10s (no dispatches)" % label)
            continue
        out.append("  %-10s %5d %10s %10s %10s %10s %10s %10s"
                   % (label, row["n"], _fmt_int(row["min"]),
                      _fmt_int(row["p25"]), _fmt_int(row["median"]),
                      _fmt_int(row["p75"]), _fmt_int(row["p90"]),
                      _fmt_int(row["max"])))
    out.append("")
    if ctx.get("floor_share") is None:
        out.append("  no dispatches in window — no floor/growth split")
    else:
        out.append("  CONTEXT TOKENS: floor re-sent every turn %s (%.1f%%) · "
                   "in-dispatch growth %s (%.1f%%)"
                   % (_fmt_units(ctx["floor"]), 100 * ctx["floor_share"],
                      _fmt_units(ctx["growth"]),
                      100 * (1 - ctx["floor_share"])))
        out.append("  COST UNITS (floor written once x%.2f, then re-read "
                   "x%.2f): floor %s (%.1f%%) · rest %s"
                   % (w.get("cache_w", 0), w.get("cache_r", 0),
                      _fmt_units(cost["floor_units"]),
                      100 * (cost["floor_share"] or 0),
                      _fmt_units((cost["total_units"] or 0)
                                 - cost["floor_units"])))
    types = report.get("by_agent_type") or {}
    if types:
        out.append("")
        out.append("  %-26s %5s %12s %12s %12s"
                   % ("subagent_type", "n", "floor med", "turns med",
                      "units"))
        for name, row in types.items():
            fl = (row.get("floor") or {}).get("median", 0)
            tu = (row.get("turns") or {}).get("median", 0)
            out.append("  %-26s %5d %12s %12s %12s"
                       % (name[:26], row["n"], _fmt_int(fl), _fmt_int(tu),
                          _fmt_units(row["units"])))
    return "\n".join(out)
