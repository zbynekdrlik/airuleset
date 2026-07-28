#!/usr/bin/env python3
"""Deliverable 1 (#136): measure design-before-code compliance from primary
sources — never trust a quoted number, re-derive it.

For each closed issue in a repo: was there a `gh issue comment` whose body
classifies as root-cause + chosen-approach + rejected-alternative
(`design_gate.classify_design_comment` — the EXACT SAME classifier
`hooks/post-record-design-comment.sh` uses to write the enforcement marker,
so this measurement and the shipped gate are provably the same yardstick),
POSTED BEFORE the first commit that references that issue (`git log`,
matched via `design_gate.issue_refs` against the commit subject+body)?

Usage:
    python3 scripts/measure_design_compliance.py \\
        --repo /path/to/airuleset --repo /path/to/other-repo \\
        [--limit 40] [--cutover 2026-07-27T16:00:22+02:00]

Prints a per-repo table plus an overall + before/after summary. Read-only:
only ever calls `gh issue list|view` and `git log` — never writes anything.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import design_gate as dg                                   # noqa: E402

# The commit that restored the design step as PROSE in agents/autopilot-worker.md
# (95dbc9b, 2026-07-27T16:00:22+02:00) — the ticket's own requested split point.
DEFAULT_CUTOVER = "2026-07-27T16:00:22+02:00"


def _parse_iso(s):
    """A tolerant ISO-8601 parser covering both gh's `...Z` and git's
    `...+HH:MM` shapes -- returns an AWARE datetime, or None. String
    comparison across those two shapes is unsafe (different lengths/suffix),
    so every timestamp in this module is compared as a real datetime."""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def evaluate_issue(comments, first_commit_iso):
    """Per-issue verdict. `comments` is the raw list from
    `gh issue view --json comments`; `first_commit_iso` is the timestamp of
    the earliest commit referencing this issue, or None if none was found.

    Returns {"compliant": bool, "reason": str, "qualifying_comment_at": str|None}.
    """
    best_dt, best_raw = None, None
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        created_raw = c.get("createdAt")
        created_dt = _parse_iso(created_raw)
        if created_dt is None:
            continue
        ok, _ = dg.classify_design_comment(c.get("body"))
        if not ok:
            continue
        if best_dt is None or created_dt < best_dt:
            best_dt, best_raw = created_dt, created_raw

    if best_dt is None:
        return {"compliant": False,
                "reason": "no comment carries root cause + approach + alternative",
                "qualifying_comment_at": None}

    first_commit_dt = _parse_iso(first_commit_iso)
    if first_commit_dt is None:
        return {"compliant": False,
                "reason": "content ok, order unknown (no commit found referencing this issue)",
                "qualifying_comment_at": best_raw}

    if best_dt < first_commit_dt:
        return {"compliant": True, "reason": "ok", "qualifying_comment_at": best_raw}
    return {"compliant": False,
            "reason": "qualifying comment posted after first commit",
            "qualifying_comment_at": best_raw}


def _bucket(rows):
    n = len(rows)
    nc = sum(1 for r in rows if r["compliant"])
    return {"n_examined": n, "n_compliant": nc, "rate": (nc / n) if n else None}


def summarize(rows, cutover_iso):
    """Aggregate + before/after split around `cutover_iso`. A row with no
    resolvable `first_commit_iso` counts toward the overall total but is
    excluded from BOTH the before and after buckets (its position relative
    to the cutover is genuinely unknown — never guessed into either side)."""
    cutover_dt = _parse_iso(cutover_iso)
    before, after, unclassifiable = [], [], []
    for r in rows:
        dt = _parse_iso(r.get("first_commit_iso"))
        if dt is None or cutover_dt is None:
            unclassifiable.append(r)
        elif dt < cutover_dt:
            before.append(r)
        else:
            after.append(r)
    overall = _bucket(rows)
    return {
        "n_examined": overall["n_examined"],
        "n_compliant": overall["n_compliant"],
        "rate": overall["rate"],
        "before": _bucket(before),
        "after": _bucket(after),
        "n_unclassifiable_timing": len(unclassifiable),
    }


# --------------------------------------------------------------------------- #
# I/O — real gh/git calls, kept thin and injectable for anything that wants
# to test them without a network (the pure functions above are what's locked).
# --------------------------------------------------------------------------- #

def _gh_json(args, timeout=20):
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        return None


def owner_repo_for(repo_path):
    try:
        r = subprocess.run(["git", "-C", repo_path, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    url = (r.stdout or "").strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    m = url.replace(":", "/").split("/")
    if len(m) < 2:
        return None
    return "/".join(m[-2:])


def find_first_commit_dates(repo_path):
    """{issue_number: earliest ISO commit date referencing it} across ALL
    branches of `repo_path`'s local history — never a `--grep` regex (this
    repo's own commit shapes, `(#N)` / `Closes #N` / `#A/#B`, are exactly
    what `design_gate.issue_refs` is tuned against)."""
    try:
        r = subprocess.run(
            ["git", "-C", repo_path, "log", "--all",
             "--format=%H%x1f%aI%x1f%s%x1e"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    earliest = {}
    for rec in (r.stdout or "").split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 3:
            continue
        _, when, subject = parts[0], parts[1], parts[2]
        for n in dg.issue_refs(subject):
            prev = earliest.get(n)
            if prev is None or (_parse_iso(when) and _parse_iso(prev) and
                                _parse_iso(when) < _parse_iso(prev)):
                earliest[n] = when
    return earliest


def measure_repo(repo_path, limit=40):
    owner_repo = owner_repo_for(repo_path)
    if not owner_repo:
        return [], "no resolvable origin"
    issues = _gh_json(["issue", "list", "-R", owner_repo, "--state", "closed",
                       "-L", str(limit), "--json", "number,closedAt"])
    if issues is None:
        return [], "gh issue list failed"
    commit_dates = find_first_commit_dates(repo_path)
    rows = []
    for it in issues:
        n = it.get("number")
        if n is None:
            continue
        detail = _gh_json(["issue", "view", str(n), "-R", owner_repo,
                           "--json", "comments"])
        comments = (detail or {}).get("comments", [])
        first_commit_iso = commit_dates.get(n)
        verdict = evaluate_issue(comments, first_commit_iso)
        rows.append({"repo": owner_repo, "issue": n,
                    "first_commit_iso": first_commit_iso, **verdict})
    return rows, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="local path to a repo checkout (repeatable)")
    ap.add_argument("--limit", type=int, default=40,
                    help="max closed issues to sample per repo")
    ap.add_argument("--cutover", default=DEFAULT_CUTOVER,
                    help="the prose-restoration commit's timestamp")
    args = ap.parse_args(argv)

    if not args.repo:
        print("measure_design_compliance: pass at least one --repo", file=sys.stderr)
        return 1

    all_rows = []
    for repo_path in args.repo:
        rows, skip_reason = measure_repo(repo_path, args.limit)
        if skip_reason:
            print("%-40s SKIPPED (%s)" % (repo_path, skip_reason))
            continue
        for r in rows:
            print("%-30s #%-6d %-5s %s" % (
                r["repo"], r["issue"], "OK" if r["compliant"] else "no",
                r["reason"]))
        all_rows.extend(rows)

    s = summarize(all_rows, args.cutover)
    print()
    print("=== SUMMARY ===")
    print("examined: %d" % s["n_examined"])
    print("compliant: %d" % s["n_compliant"])
    print("rate: %s" % ("%.1f%%" % (s["rate"] * 100) if s["rate"] is not None else "n/a"))
    print("unclassifiable timing (no commit found referencing the issue): %d"
          % s["n_unclassifiable_timing"])
    print("before %s: %d examined, %d compliant (%s)" % (
        args.cutover, s["before"]["n_examined"], s["before"]["n_compliant"],
        "%.1f%%" % (s["before"]["rate"] * 100) if s["before"]["rate"] is not None else "n/a"))
    print("after  %s: %d examined, %d compliant (%s)" % (
        args.cutover, s["after"]["n_examined"], s["after"]["n_compliant"],
        "%.1f%%" % (s["after"]["rate"] * 100) if s["after"]["rate"] is not None else "n/a"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
