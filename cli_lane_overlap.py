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

    ``verdict`` is ``"overlap"`` when the candidate unit shares any touched
    PATH with a live lane or an open PR, OR shares a TOPIC token with a live
    lane; else ``"clear"``. ``overlaps`` is a list of
    ``[kind, ref, detail]`` rows (kind in lane/lane-topic/pr).
    """
    path_set = {p for p in (paths or []) if p}
    topic_toks = _topic_tokens(topics)
    overlaps = []
    for lane in live_lanes or []:
        ref = lane.get("ref", "?")
        common = sorted(path_set & set(lane.get("files") or []))
        if common:
            overlaps.append(["lane", ref, common])
            continue
        lane_toks = _topic_tokens([lane.get("topic", "")])
        shared = sorted(topic_toks & lane_toks)
        if shared:
            overlaps.append(["lane-topic", ref, shared])
    for pr in open_prs or []:
        num = pr.get("number", "?")
        common = sorted(path_set & set(pr.get("files") or []))
        if common:
            overlaps.append(["pr", num, common])
    return ("overlap" if overlaps else "clear"), overlaps


def write_receipt(home, cwd_key, issues, verdict, overlaps):
    """Write the per-cwd receipt and return its path. The hook keys on
    ``checked_issues`` + ``ts`` (freshness)."""
    d = os.path.join(home, ".claude", "lane-overlap")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, cwd_key + ".json")
    rec = {
        "checked_issues": [int(i) for i in issues],
        "ts": time.time(),
        "verdict": verdict,
        "overlaps": overlaps,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)
    return path


def _run_default(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def gather_live_lanes(repo_root, run=None):
    """Live worktree lanes: each live worktree branch (worktree-*) and its
    touched files vs the repo base. Fails toward [] (logs, never raises) — the
    receipt is still written; a missing lane list only means fewer known
    overlaps, and the supervisor's own ``Independence:`` record is the durable
    authority."""
    run = run or _run_default
    lanes = []
    try:
        wt = run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    except Exception as e:
        print("lane-overlap: worktree list failed (%s)" % e, file=sys.stderr)
        return lanes
    if wt.returncode != 0:
        return lanes
    for line in (wt.stdout or "").splitlines():
        if line.startswith("branch "):
            branch = line.split(" ", 1)[1].strip().rsplit("/", 1)[-1]
            if branch.startswith("worktree-"):
                files = _lane_files(repo_root, branch, run)
                lanes.append({"ref": branch, "files": files, "topic": branch})
    return lanes


def _lane_files(repo_root, branch, run):
    """Files a worktree branch touches vs main (best-effort, [] on error)."""
    try:
        base = run(["git", "-C", repo_root, "merge-base", "main", branch])
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

    live_lanes = gather_live_lanes(repo_root)
    open_prs = gather_open_prs()
    verdict, overlaps = compute_overlap(paths, topics,
                                        live_lanes=live_lanes, open_prs=open_prs)

    home = os.path.expanduser("~")
    write_receipt(home, _cwd_key(os.getcwd()), issues, verdict, overlaps)

    if verdict == "clear":
        print("CLEAR: issues %s — no path/topic overlap with %d live lane(s) "
              "+ %d open PR(s)" % (issues, len(live_lanes), len(open_prs)))
    else:
        print("OVERLAP: issues %s —" % issues)
        for kind, ref, detail in overlaps:
            print("  %s %s: %s" % (kind, ref, ", ".join(str(d) for d in detail)))
        print("  (an overlapping lane WAITS — dispatch it into a later free "
              "slot, or merge it into the live lane; see skills/autopilot/SKILL.md)")
    return 0
