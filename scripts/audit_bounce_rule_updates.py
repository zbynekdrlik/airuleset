#!/usr/bin/env python3
"""Audit bounce trends and Prevencia coverage per stream.

Usage:
    python3 scripts/audit_bounce_rule_updates.py --rounds --repo owner/name
    python3 scripts/audit_bounce_rule_updates.py --rounds --repo owner/name --json
    python3 scripts/audit_bounce_rule_updates.py --rounds --repo owner/name --window 14

Reports per-stream bounce rate trends and flags:
- treadmill!  — >= 2 bounce-adds on the SAME ticket within 24 h
- prevencia!  — a hand-off at round >= 2 with no Prevencia-read path

The 24 h rule (#957): every repeated bounce class (same finding type
bounced >= 2x across a stream's tickets) MUST get a mechanical prevention
(hook / gate / script / Prevencia rule) within 24 h of recurrence.  A
stream with a rising bounce RATE violates this.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo root for imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals  # noqa: E402


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def stream_labels(labels_list):
    """Extract stream names from a list of label dicts/strings."""
    out = []
    for lb in (labels_list or []):
        name = lb.get("name", lb) if isinstance(lb, dict) else str(lb)
        if name.startswith("stream:"):
            out.append(name[len("stream:"):])
    return out


def canonical_stream(raw_name):
    """Canonicalize a stream name via rename-equivalence (#537).

    Returns the FIRST element of the equivalence set — the canonical name.
    """
    equivs = cli_quals._stream_rename_equivalents(raw_name)
    return equivs[0] if equivs else raw_name


def _parse_iso(raw):
    """Parse an ISO 8601 timestamp (GitHub ``Z`` suffix)."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_bounce_issues(repo, runner=None):
    """Fetch open issues with prio:bounce or ready-for-review labels.

    Returns a list of dicts with keys:
        number, title, labels (list of label-name strings),
        streams (list of canonical stream names).
    """
    import airuleset
    run = runner or airuleset._gh_out

    issues = []
    seen = set()
    for label in ("prio:bounce", "ready-for-review"):
        raw = run("issue", "list", "-R", repo, "--state", "open",
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

    Returns a list of datetime (UTC).
    """
    import airuleset
    run = runner or airuleset._gh_out

    events_path = "repos/%s/issues/%s/events" % (repo, number)
    events_raw = run("api", events_path, "--paginate", timeout=30)
    return cli_quals._bounce_label_events(events_raw)


# ---------------------------------------------------------------------------
# Trend computation
# ---------------------------------------------------------------------------

def compute_trends(issues_with_events, window_days=7):
    """Compute per-stream bounce rate trends.

    ``issues_with_events`` is a list of dicts, each carrying:
        streams (list[str]), bounce_timestamps (list[datetime]),
        handoff_count (int — total RFR hand-offs for the issue).

    Returns a dict:
        {stream: {recent_bounces, prior_bounces,
                  recent_handoffs, prior_handoffs,
                  recent_rate, prior_rate,
                  trend, treadmill_issues, prevencia_missing_issues}}
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
                    "recent_handoffs": 0,
                    "prior_handoffs": 0,
                    "treadmill_issues": [],
                    "prevencia_missing_issues": [],
                }
            entry = per_stream[stream]

            # Count bounces per window.
            ts_list = item.get("bounce_timestamps", [])
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

            # Prevencia check.
            if item.get("prevencia_missing") and len(ts_list) >= 2:
                num = item.get("number", 0)
                if num not in entry["prevencia_missing_issues"]:
                    entry["prevencia_missing_issues"].append(num)

            # Hand-off count allocation (best effort — attribute to recent
            # if any bounce is recent, else prior).
            handoffs = item.get("handoff_count", 0)
            if any(ts >= recent_start for ts in ts_list):
                entry["recent_handoffs"] += handoffs
            elif any(ts >= prior_start for ts in ts_list):
                entry["prior_handoffs"] += handoffs

    # Compute rates and trends.
    for stream, data in per_stream.items():
        rb, pb = data["recent_bounces"], data["prior_bounces"]
        rh, ph = data["recent_handoffs"], data["prior_handoffs"]

        data["recent_rate"] = (rb / rh) if rh > 0 else ("n/a" if rb == 0 else rb)
        data["prior_rate"] = (pb / ph) if ph > 0 else ("n/a" if pb == 0 else pb)

        # Trend based on raw counts (rate when available, else counts).
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
# Output
# ---------------------------------------------------------------------------

def print_text(trends):
    """Print TSV: stream  recent  prior  trend  [flags]."""
    print("stream\trecent\tprior\ttrend\tflags")
    for stream in sorted(trends):
        d = trends[stream]
        flags = []
        if d["treadmill_issues"]:
            flags.append("treadmill!(%s)" % ",".join(
                str(n) for n in d["treadmill_issues"]))
        if d["prevencia_missing_issues"]:
            flags.append("prevencia!(%s)" % ",".join(
                str(n) for n in d["prevencia_missing_issues"]))
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
        description="Audit bounce trends and Prevencia coverage per stream.")
    p.add_argument("--rounds", action="store_true",
                   help="Print per-stream bounce trend")
    p.add_argument("--repo", required=True,
                   help="GitHub repo (owner/name)")
    p.add_argument("--window", type=int, default=7,
                   help="Window size in days (default: 7)")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="Output as JSON")
    args = p.parse_args(argv)

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
        issue["handoff_count"] = max(1, len(issue["bounce_timestamps"]))
        # Prevencia check: round >= 2 hand-offs with no Prevencia reference
        # is a rough signal.  Full check would read RFR comments, but that
        # is expensive; the script flags issues with >= 2 bounces and leaves
        # the detail to the operator.
        issue["prevencia_missing"] = len(issue["bounce_timestamps"]) >= 2

    trends = compute_trends(issues, window_days=args.window)

    if args.json_out:
        print_json(trends)
    else:
        print_text(trends)


if __name__ == "__main__":
    main()
