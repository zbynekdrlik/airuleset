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
                day = t.astimezone().strftime("%Y-%m-%d")
                sc = bool(e.get("isSidechain"))
                for d, k in (
                    (agg, model), (by_day, day), (by_proj, proj),
                    (by_day_model, day + "|" + tr),
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
