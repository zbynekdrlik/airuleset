#!/usr/bin/env python3
"""Audit bounce trends per stream.

Usage:
    python3 scripts/audit_bounce_rule_updates.py --rounds --repo owner/name
    python3 scripts/audit_bounce_rule_updates.py --rounds --repo owner/name --json
    python3 scripts/audit_bounce_rule_updates.py --rounds --repo owner/name --window 14

Reports per-stream bounce count trends and flags:
- treadmill!  — >= 2 bounce-adds on the SAME ticket within 24 h

The 24 h rule (#957): every repeated bounce class (same finding type
bounced >= 2x across a stream's tickets) MUST get a mechanical prevention
(hook / gate / script / Prevencia rule) within 24 h of recurrence.  A
stream with a rising bounce count violates this.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo root for imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def canonical_stream(raw_name):
    """Canonicalize a stream name via rename aliases (#537).

    Returns the rename TARGET when ``raw_name`` is a known old name, or
    ``raw_name`` itself otherwise.  This groups old and new names under the
    NEW canonical form — e.g. both ``montalu`` and ``montalu1`` map to
    ``montalu1`` (if that alias exists in STREAM_RENAME_ALIASES)."""
    aliases = airuleset.STREAM_RENAME_ALIASES
    # Old name -> canonical new name.
    if raw_name in aliases:
        return aliases[raw_name]
    # Already the new name (or no alias).
    return raw_name


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_bounce_issues(repo, runner=None):
    """Fetch open+recently-updated issues with prio:bounce or
    ready-for-review labels.

    Returns a list of dicts with keys:
        number, title, labels (list of label-name strings),
        streams (list of canonical stream names).
    """
    run = runner or airuleset._gh_out

    issues = []
    seen = set()
    for label in ("prio:bounce", "ready-for-review"):
        raw = run("issue", "list", "-R", repo, "--state", "all",
                  "--label", label, "-L", "200",
                  "--json", "number,title,labels", timeout=20)
        if not raw:
            continue
        try:
            rows = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for row in rows:
            num = row.get("number")
            if num in seen:
                continue
            seen.add(num)
            raw_labels = row.get("labels", [])
            label_names = []
            for lb in raw_labels:
                if isinstance(lb, dict):
                    label_names.append(lb.get("name", ""))
                else:
                    label_names.append(str(lb))
            streams = [canonical_stream(n[len("stream:"):])
                       for n in label_names if n.startswith("stream:")]
            issues.append({
                "number": num,
                "title": row.get("title", ""),
                "labels": label_names,
                "streams": streams or ["unknown"],
            })
    return issues


def fetch_bounce_events_for_issue(number, repo, runner=None):
    """Fetch prio:bounce label-add event timestamps for one issue.

    Returns a list of datetime (UTC).  None entries represent events
    with unparseable timestamps (counted but excluded from window math).
    """
    run = runner or airuleset._gh_out

    events_path = "repos/%s/issues/%s/events" % (repo, number)
    events_raw = run("api", events_path, "--paginate", timeout=30)
    return cli_quals._bounce_label_events(events_raw)


# ---------------------------------------------------------------------------
# Trend computation
# ---------------------------------------------------------------------------

def compute_trends(issues_with_events, window_days=7):
    """Compute per-stream bounce count trends.

    ``issues_with_events`` is a list of dicts, each carrying:
        streams (list[str]), bounce_timestamps (list[datetime|None]).

    Returns a dict:
        {stream: {recent_bounces, prior_bounces,
                  trend, treadmill_issues}}
    """
    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(days=window_days)
    prior_start = now - timedelta(days=2 * window_days)

    per_stream = {}

    for item in issues_with_events:
        for stream in item.get("streams", ["unknown"]):
            if stream not in per_stream:
                per_stream[stream] = {
                    "recent_bounces": 0,
                    "prior_bounces": 0,
                    "treadmill_issues": [],
                }
            entry = per_stream[stream]

            # Count bounces per window (skip None timestamps).
            ts_list = [ts for ts in item.get("bounce_timestamps", [])
                       if ts is not None]
            for ts in ts_list:
                if ts >= recent_start:
                    entry["recent_bounces"] += 1
                elif ts >= prior_start:
                    entry["prior_bounces"] += 1

            # Treadmill detection: >= 2 bounce-adds on SAME ticket within 24h.
            sorted_ts = sorted(ts_list)
            for i in range(1, len(sorted_ts)):
                if (sorted_ts[i] - sorted_ts[i - 1]) <= timedelta(hours=24):
                    num = item.get("number", 0)
                    if num not in entry["treadmill_issues"]:
                        entry["treadmill_issues"].append(num)
                    break

    # Compute trends.
    for stream, data in per_stream.items():
        rb, pb = data["recent_bounces"], data["prior_bounces"]

        if rb == 0 and pb == 0:
            data["trend"] = "none"
        elif rb > pb:
            data["trend"] = "rising"
        elif rb < pb:
            data["trend"] = "falling"
        else:
            data["trend"] = "flat"

    return per_stream


# ---------------------------------------------------------------------------
# First-pass rate (#963)
# ---------------------------------------------------------------------------

def first_pass_rate(trends, issues_with_events):
    """Compute per-stream first-pass rate.

    first-pass rate = share of tickets with ZERO prio:bounce events
    (never bounced = round 1 = first-pass success).

    Returns {stream: float} where 1.0 = 100% first-pass.
    """
    if not trends:
        return {}

    # Count per-stream: tickets total and tickets with 0 bounce events.
    per_stream_total = {}
    per_stream_zero = {}

    for item in issues_with_events:
        n_bounces = len(item.get("bounce_timestamps", []))
        for stream in item.get("streams", ["unknown"]):
            if stream not in trends:
                continue
            per_stream_total[stream] = per_stream_total.get(stream, 0) + 1
            if n_bounces == 0:
                per_stream_zero[stream] = (
                    per_stream_zero.get(stream, 0) + 1)

    result = {}
    for stream in trends:
        total = per_stream_total.get(stream, 0)
        if total == 0:
            result[stream] = 0.0
        else:
            result[stream] = per_stream_zero.get(stream, 0) / total
    return result


# ---------------------------------------------------------------------------
# Bypass-token metric (#1003)
# ---------------------------------------------------------------------------
# A bypass token (`# airuleset:<kebab>-ok <reason>`) used by a lane is a
# FINDING against the RULE, not an approval (owner ruling, odoo-erp gk
# 12.9.2026): the hook forced a token on a LEGITIMATE change, so the daily
# count of these tokens in MERGED commit messages must trend to 0 as each
# false-positive class is fixed at its cause. This view surfaces that count,
# per day per repo, alongside the existing #957/#963 flow metric. NO new
# script -- it lives in this one.

BYPASS_TOKEN_RE = re.compile(r"airuleset:[a-z-]*-ok")


def fetch_bypass_commits(repo, window_days=7, runner=None):
    """Fetch merged commits (default branch) in the last `window_days` via the
    GitHub commits API. Returns the parsed list (the API shape), or [] on any
    failure. `runner` is injectable for tests (defaults to airuleset._gh_out)."""
    run = runner or airuleset._gh_out
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    raw = run("api", "repos/%s/commits?since=%s&per_page=100" % (repo, since),
              "--paginate", timeout=60)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def count_bypass_tokens(commits):
    """Pure: count airuleset bypass tokens (airuleset:<kebab>-ok) in merged
    commit messages. `commits` is the GitHub commits-API list shape. Returns
    ``{"per_day": {YYYY-MM-DD: n}, "per_kind": {token: n}, "total": n}`` --
    days/kinds with zero hits are simply absent.

    Accepted caveats (observability only, never a gate):
    - (review-1 F4) a commit that merely DOCUMENTS the token syntax (a
      doctrine/hook-comment edit mentioning `airuleset:secret-ok`) is counted
      too — over-counting is the CONSERVATIVE direction (surfaces more for
      review, never HIDES a real bypass).
    - (review-2 F4) this view sees only tokens IN COMMIT MESSAGES, so it covers
      commit-message bypasses (`test-skip-ok` on an --allow-empty merge commit,
      `no-design`/`no-test` in a message) but is BLIND to command-line-only
      bypasses that never enter git history — `airuleset:secret-ok` (a trailing
      `git add`/commit shell comment) and `airuleset:scope-gate-ok` (a
      `gh issue create` comment). Those classes need their own audit-log view;
      this one does not claim to count them."""
    per_day, per_kind, total = {}, {}, 0
    for c in commits or []:
        if not isinstance(c, dict):
            continue
        cm = c.get("commit") or {}
        msg = cm.get("message", "") or ""
        date = ((cm.get("committer") or {}).get("date")
                or (cm.get("author") or {}).get("date") or "")
        day = date[:10] if date else "unknown"
        toks = BYPASS_TOKEN_RE.findall(msg)
        if not toks:
            continue
        per_day[day] = per_day.get(day, 0) + len(toks)
        for t in toks:
            per_kind[t] = per_kind.get(t, 0) + 1
        total += len(toks)
    return {"per_day": per_day, "per_kind": per_kind, "total": total}


def print_bypasses_text(counts):
    """TSV: day  bypass_tokens, then a total row and a per-token breakdown."""
    print("day\tbypass_tokens")
    for day in sorted(counts["per_day"]):
        print("%s\t%d" % (day, counts["per_day"][day]))
    print("total\t%d" % counts["total"])
    if counts["per_kind"]:
        print("# by token (trend each to 0 by fixing its false-positive class):")
        for kind in sorted(counts["per_kind"]):
            print("#   %s\t%d" % (kind, counts["per_kind"][kind]))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def format_weekly_report(trends, issues_with_events):
    """Format a compact markdown table for pasting on a tracking issue.

    Columns: stream | recent | prior | trend | first-pass | treadmill
    """
    rates = first_pass_rate(trends, issues_with_events)
    lines = []
    lines.append("| stream | recent | prior | trend | first-pass | "
                 "treadmill |")
    lines.append("|--------|--------|-------|-------|------------|"
                 "-----------|")

    for stream in sorted(trends):
        d = trends[stream]
        rate_pct = "%.0f%%" % (rates.get(stream, 0.0) * 100)
        treadmill = ""
        if d["treadmill_issues"]:
            treadmill = ",".join(str(n) for n in d["treadmill_issues"])
        lines.append("| %s | %d | %d | %s | %s | %s |" % (
            stream, d["recent_bounces"], d["prior_bounces"],
            d["trend"], rate_pct, treadmill))

    return "\n".join(lines)


def print_text(trends):
    """Print TSV: stream  recent  prior  trend  [flags]."""
    print("stream\trecent\tprior\ttrend\tflags")
    for stream in sorted(trends):
        d = trends[stream]
        flags = []
        if d["treadmill_issues"]:
            flags.append("treadmill!(%s)" % ",".join(
                str(n) for n in d["treadmill_issues"]))
        print("%s\t%d\t%d\t%s\t%s" % (
            stream, d["recent_bounces"], d["prior_bounces"],
            d["trend"], " ".join(flags)))


def print_json(trends):
    """Print JSON output."""
    json.dump(trends, sys.stdout, indent=2, default=str)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Audit bounce trends per stream.")
    p.add_argument("--rounds", action="store_true",
                   help="Print per-stream bounce trend")
    p.add_argument("--bypasses", action="store_true",
                   help="Count airuleset bypass tokens (airuleset:...-ok) in "
                        "merged commit messages, per day (must trend to 0)")
    p.add_argument("--repo", required=True,
                   help="GitHub repo (owner/name)")
    p.add_argument("--window", type=int, default=7,
                   help="Window size in days (default: 7)")
    output_fmt = p.add_mutually_exclusive_group()
    output_fmt.add_argument("--json", dest="json_out", action="store_true",
                            help="Output as JSON")
    output_fmt.add_argument("--weekly-report", dest="weekly_report",
                            action="store_true",
                            help="Print a compact markdown table with "
                                 "first-pass rate")
    args = p.parse_args(argv)

    if args.bypasses:
        commits = fetch_bypass_commits(args.repo, window_days=args.window)
        counts = count_bypass_tokens(commits)
        if args.json_out:
            json.dump(counts, sys.stdout, indent=2)
            print()
        else:
            print_bypasses_text(counts)
        return

    if not args.rounds:
        p.print_help()
        return

    issues = fetch_bounce_issues(args.repo)
    if not issues:
        if args.json_out:
            print("{}")
        else:
            print("No bounce-lane issues found.")
        return

    # Enrich each issue with bounce timestamps.
    for issue in issues:
        issue["bounce_timestamps"] = fetch_bounce_events_for_issue(
            issue["number"], args.repo)

    trends = compute_trends(issues, window_days=args.window)

    if args.weekly_report:
        print(format_weekly_report(trends, issues))
    elif args.json_out:
        print_json(trends)
    else:
        print_text(trends)


if __name__ == "__main__":
    main()
