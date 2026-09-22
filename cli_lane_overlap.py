"""cli_lane_overlap — the independence-check helper for autopilot dispatch (#992/#993).

Before the supervisor dispatches a lane it runs

    python3 airuleset.py lane-overlap --paths <p1,p2> --topics <t1,t2> --issue <N> [--issue <M>]

which compares the candidate unit's touched PATHS and TOPIC against every LIVE
lane (worktree branches + their `refs/autopilot-wip/*` backups) and every open
PR's file list, prints ``CLEAR`` or ``OVERLAP: <what>``, and writes a per-cwd
RECEIPT that ``hooks/block-dispatch-over-wdrain.sh`` reads to enforce that the
independence check was actually run for the dispatched issue(s) — the mechanical
half of #992 requirement 2 (no new hook: the existing dispatch gate reads the
receipt).

Pure, dependency-injected (a ``run`` callable for git/gh), stdlib-only. The
verdict is INFORMATIONAL — the tool always exits 0 on success and records the
verdict; the SUPERVISOR reads the printed OVERLAP and decides (an overlapping
lane waits, per the SKILL.md doctrine). The hook only checks the receipt PRESENCE
+ freshness + issue coverage, never the verdict.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

# Receipt freshness must match the hook's window (block-dispatch-over-wdrain.sh
# OVERLAP_TTL). 30 min, the same TTL the sibling wdrain receipt uses.
RECEIPT_TTL_S = 1800

_STOPWORDS = {
    "the", "and", "for", "with", "into", "from", "this", "that", "area",
    "rework", "fix", "add", "issue", "ticket", "one", "pr", "lane",
}


def _cwd_key(cwd):
    """sha1[:12] of the cwd — must match statusbar.cwd_key / the hook."""
    return hashlib.sha1((cwd or "").encode("utf-8")).hexdigest()[:12]


def _topic_tokens(topics):
    """Normalise a list of topic strings into a set of comparable word tokens
    (lowercased, >=4 chars, alnum runs, minus common stopwords) so a topic
    clash is a real shared subject, not an incidental short word."""
    toks = set()
    for t in topics or []:
        for w in re.split(r"[^0-9A-Za-zÀ-ž]+", (t or "").lower()):
            if len(w) >= 4 and w not in _STOPWORDS:
                toks.add(w)
    return toks


def compute_overlap(paths, topics, *, live_lanes, open_prs):
    """Return ``(verdict, overlaps)``.

    ``verdict``:
    - ``"overlap"`` — the candidate unit shares a touched PATH with a LIVE lane,
      or a TOPIC token with a live lane. A live lane is in-flight work; the next
      lane WAITS for a free slot (SKILL.md doctrine).
    - ``"clear-with-resync"`` (#1078 item 2) — the ONLY overlaps are with OPEN
      PR file lists (no live-lane / lane-topic hit). An open PR is FINISHED work
      awaiting review/merge, not an occupied lane: dispatch is allowed and the
      next lane RESYNCs (``git merge origin/<base>`` once the PR lands, rebase
      before hand-off) rather than idling. On 2026-09-18 three montalu lanes
      waited hours for PR #7558/#7566/#7585 to merge — the exact idle this fixes.
    - ``"clear"`` — no overlap at all.

    ``overlaps`` is a list of ``[kind, ref, detail]`` rows (kind in
    lane/lane-topic/pr).
    """
    path_set = {p for p in (paths or []) if p}
    topic_toks = _topic_tokens(topics)
    overlaps = []
    live_hit = False
    for lane in live_lanes or []:
        ref = lane.get("ref", "?")
        common = sorted(path_set & set(lane.get("files") or []))
        if common:
            overlaps.append(["lane", ref, common])
            live_hit = True
            continue
        lane_toks = _topic_tokens([lane.get("topic", "")])
        shared = sorted(topic_toks & lane_toks)
        if shared:
            overlaps.append(["lane-topic", ref, shared])
            live_hit = True
    for pr in open_prs or []:
        num = pr.get("number", "?")
        common = sorted(path_set & set(pr.get("files") or []))
        if common:
            overlaps.append(["pr", num, common])
    if not overlaps:
        return "clear", overlaps
    # A live-lane (path or topic) hit means WAIT; only PR-file overlaps and no
    # live lane means the next lane may dispatch + resync (#1078 item 2).
    return ("overlap" if live_hit else "clear-with-resync"), overlaps


def write_receipt(home, cwd_key, issues, verdict, overlaps, deps="satisfied"):
    """Write the per-cwd receipt and return its path. The hook keys on
    ``checked_issues`` + ``ts`` (freshness). ``deps`` (#993 item 7) records the
    dispatched unit's dependency state — ``"satisfied"`` (every ``Depends-on:``
    ref closed / none) or the list of unsatisfied blocking refs — so the receipt
    proves the deps were resolved before dispatch, not only the file overlap."""
    d = os.path.join(home, ".claude", "lane-overlap")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, cwd_key + ".json")
    rec = {
        "checked_issues": [int(i) for i in issues],
        "ts": time.time(),
        "verdict": verdict,
        "overlaps": overlaps,
        "deps": deps,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)
    return path


def _run_default(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def gather_issue_deps(repo_root, issues, run=None):
    """#993 item 7 — resolve the dispatched issues' ``Depends-on:`` state for the
    receipt: ``"satisfied"`` (all closed / none), ``"unknown"`` (resolution
    machinery unavailable — an HONEST fail-safe, never a false ``satisfied``,
    #993 review 7), or a list of unsatisfied blocking refs. A per-dep gh error
    counts that dep unsatisfied via ``cli_work_class.dep_wait``'s own fail-safe.
    The receipt's deps field is INFORMATIONAL; the dispatchable gate itself lives
    in the picker (`--list dep-wait`) / `--count-dispatchable` / the nudges."""
    run = run or _run_default
    try:
        import cli_work_class as wc
        import airuleset
        slug = airuleset._repo_slug(cwd=repo_root)
    except Exception as e:
        print("lane-overlap: dep resolution unavailable (%s)" % e, file=sys.stderr)
        return "unknown"

    def wc_runner(argv, _cwd):
        try:
            r = run(argv)
        except Exception:
            return ""
        return (r.stdout or "") if getattr(r, "returncode", 1) == 0 else ""

    return wc.resolve_issue_deps(issues, slug, wc_runner, repo_root)


def _lane_files(repo_root, branch, run, base_branch):
    """Files a worktree branch touches vs the integration base (best-effort,
    [] on error)."""
    try:
        base = run(["git", "-C", repo_root, "merge-base", base_branch, branch])
        if base.returncode != 0:
            return []
        b = (base.stdout or "").strip()
        diff = run(["git", "-C", repo_root, "diff", "--name-only", b, branch])
        if diff.returncode != 0:
            return []
        return [ln.strip() for ln in (diff.stdout or "").splitlines() if ln.strip()]
    except Exception as e:
        print("lane-overlap: diff for %s failed (%s)" % (branch, e), file=sys.stderr)
        return []


def _lane_topic(repo_root, branch, run):
    """A lane's topic = its last commit subject (the branch NAME is an opaque
    hash → no real topic match, #993-review 🔵). Branch name on error."""
    try:
        r = run(["git", "-C", repo_root, "log", "-1", "--format=%s", branch])
        if r.returncode == 0 and (r.stdout or "").strip():
            return r.stdout.strip()
    except Exception as e:
        print("lane-overlap: topic for %s failed (%s)" % (branch, e), file=sys.stderr)
    return branch


def gather_open_prs(run=None):
    """Open PRs with their file lists (best-effort, [] on error)."""
    run = run or _run_default
    try:
        r = run(["gh", "pr", "list", "--state", "open", "--limit", "50",
                 "--json", "number,title,files"])
        if r.returncode != 0:
            return []
        rows = json.loads(r.stdout or "[]")
    except Exception as e:
        print("lane-overlap: gh pr list failed (%s)" % e, file=sys.stderr)
        return []
    out = []
    for row in rows:
        files = [f.get("path") for f in (row.get("files") or [])
                 if isinstance(f, dict) and f.get("path")]
        out.append({"number": row.get("number"),
                    "title": row.get("title") or "",
                    "files": files})
    return out


def _split_csv(v):
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def cmd_lane_overlap(args):
    paths = _split_csv(getattr(args, "paths", None))
    topics = _split_csv(getattr(args, "topics", None))
    issues = []
    for i in (getattr(args, "issue", None) or []):
        for part in re.split(r"[,\s]+", str(i)):
            part = part.lstrip("#")
            if part.isdigit():
                issues.append(int(part))
    if not issues:
        print("lane-overlap: --issue <N> is required (the unit being checked)",
              file=sys.stderr)
        return 1
    if not paths:
        print("lane-overlap: --paths <p1,p2> is required", file=sys.stderr)
        return 1

    repo_root = os.getcwd()
    try:
        import airuleset
        repo_root = airuleset._repo_root() or repo_root
    except Exception as e:  # optional dependency / not in a repo — cwd is fine
        print("lane-overlap: repo-root fallback to cwd (%s)" % e, file=sys.stderr)

    classified = classify_lanes(repo_root)
    live_lanes = [lane for lane in classified if lane.get("state") in _LIVE_STATES]
    open_prs = gather_open_prs()
    verdict, overlaps = compute_overlap(paths, topics,
                                        live_lanes=live_lanes, open_prs=open_prs)
    # #1103 — the per-state lane breakdown, so the receipt/printer show WHY a
    # box's live count dropped (finished/merged lanes excluded, idle still live).
    print("lanes: %s" % state_summary(classified))

    deps = gather_issue_deps(repo_root, issues)   # #993 item 7
    home = os.path.expanduser("~")
    write_receipt(home, _cwd_key(os.getcwd()), issues, verdict, overlaps,
                  deps=deps)

    if verdict == "clear":
        print("CLEAR: issues %s — no path/topic overlap with %d live lane(s) "
              "+ %d open PR(s)" % (issues, len(live_lanes), len(open_prs)))
    elif verdict == "clear-with-resync":
        # #1078 item 2: PR-only overlap — dispatch allowed, the prompt MUST carry
        # a Resync obligation. Name every overlapping PR + its clashing files.
        pr_refs = ", ".join(
            "PR #%s (%s)" % (ref, ", ".join(str(d) for d in detail))
            for kind, ref, detail in overlaps if kind == "pr")
        print("CLEAR-WITH-RESYNC: issues %s — %s — dispatch allowed; the prompt "
              "must carry `Resync: merge origin/<base> once the PR lands, rebase "
              "before hand-off`" % (issues, pr_refs))
    else:
        print("OVERLAP: issues %s —" % issues)
        for kind, ref, detail in overlaps:
            print("  %s %s: %s" % (kind, ref, ", ".join(str(d) for d in detail)))
        print("  (an overlapping lane WAITS — dispatch it into a later free "
              "slot, or merge it into the live lane; see skills/autopilot/SKILL.md)")
    if deps == "satisfied":
        print("  deps: satisfied")
    elif deps == "unknown":
        print("  deps: unknown (resolution unavailable — verify Depends-on by "
              "hand before dispatch, #993 item 7)")
    else:
        print("  deps: WAITING on %s — a dep-wait unit is NOT dispatchable "
              "until its Depends-on refs close (#993 item 7)"
              % ", ".join(str(d) for d in deps))
    return 0

# Back-compat re-exports (#1107): the lane-liveness derivation moved to
# cli_lane_liveness (its canonical home). Re-exported here for one release so
# out-of-tree callers of cli_lane_overlap keep working; cmd_lane_overlap uses
# classify_lanes/state_summary/_LIVE_STATES below. Bottom-placed so the
# load-time cycle with the leaf's own bottom import resolves either order.
from cli_lane_liveness import (  # noqa: E402,F401
    _LIVE_STATES,
    classify_lanes,
    gather_live_lanes,
    state_summary,
)
