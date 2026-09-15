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

# The Claude Code `isolation: worktree` directory — EVERY dispatched lane is a
# checkout under `<repo>/.claude/worktrees/`, whatever its branch is named (the
# same signal the #817 isolation self-check keys on). #1031: recognising a lane
# by this PATH (not only a `worktree-` branch prefix) is what makes a
# stream-named lane (`david3/<issue>-<slug>`, `lane5337`) count.
_WORKTREES_SEGMENT = os.sep + os.path.join(".claude", "worktrees") + os.sep

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


def _lane_is_merged(repo_root, branch, base_branch, run):
    """#998 — True when the worktree branch's tip is an ANCESTOR of the base
    (main): the lane is FINISHED (its work is integrated) and is NO LONGER a
    live lane, so overlap must ignore it. ``git merge-base --is-ancestor
    <branch> <base>`` exits 0 when true. Fail-SAFE: any error/other rc → False
    (keep it LIVE — never drop a genuinely unmerged lane from the overlap set)."""
    try:
        r = run(["git", "-C", repo_root, "merge-base", "--is-ancestor",
                 branch, base_branch])
    except Exception as e:
        print("lane-overlap: merged check for %s failed (%s)" % (branch, e),
              file=sys.stderr)
        return False
    return r.returncode == 0


def _parse_worktree_records(stdout):
    """Parse ``git worktree list --porcelain`` into a list of record dicts —
    ``{"path", "branch", "head", "detached"}`` — one per worktree, in listing
    order (the FIRST is always the main checkout). A record ends at a blank line,
    the next ``worktree`` line, OR a repeat ``branch`` line — so a degenerate
    porcelain carrying only ``branch`` lines (older callers' injected fakes,
    with or without blank-line separators) still yields one record per branch.
    ``branch`` is the LOCAL name with ``refs/heads/`` stripped but slashes KEPT
    — a stream branch (``david3/7184-…``) must stay resolvable as a git ref (the
    old ``rsplit("/",1)[-1]`` mangled it to ``7184-…``, #1031)."""
    records = []
    cur = {}
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            if cur:
                records.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            if cur:
                records.append(cur)
                cur = {}
            cur["path"] = line.split(" ", 1)[1].strip()
        elif line.startswith("HEAD "):
            cur["head"] = line.split(" ", 1)[1].strip()
        elif line.startswith("branch "):
            # a repeat branch with no intervening `worktree`/blank line starts a
            # new record (review finding 1: a bare consecutive-branch porcelain
            # must not last-wins-collapse into one record).
            if "branch" in cur:
                records.append(cur)
                cur = {}
            ref = line.split(" ", 1)[1].strip()
            cur["branch"] = (ref[len("refs/heads/"):]
                             if ref.startswith("refs/heads/") else ref)
        elif line == "detached":
            cur["detached"] = True
    if cur:
        records.append(cur)
    return records


def _resolve_base_branch(repo_root, records, run):
    """The integration base a lane's merged-ness is judged against: the repo's
    DEFAULT branch (``git symbolic-ref --quiet refs/remotes/origin/HEAD`` ->
    ``origin/<default>`` — ``develop`` on the odoo streams, ``main`` here),
    fallback to the FIRST worktree's branch (the main checkout), fallback
    ``main``. #1031: NOT the main checkout's own branch when it is a FEATURE
    branch — a stream box's main checkout sits on ``david3/…``, and judging
    merged-ness against a random feature branch is the second latent defect.
    When ``origin/HEAD`` is not locally set (a symref that clone sets but which
    can be pruned), probe the standard integration branches BEFORE the
    feature-branch fallback, so the defect is not re-introduced in that degraded
    state (review finding 2). Fails toward the fallback, never raises."""
    try:
        r = run(["git", "-C", repo_root, "symbolic-ref", "--quiet",
                 "refs/remotes/origin/HEAD"])
        if getattr(r, "returncode", 1) == 0:
            ref = (r.stdout or "").strip()
            if ref.startswith("refs/remotes/"):
                return ref[len("refs/remotes/"):]  # -> origin/<default>
            if ref:
                return ref
    except Exception as e:
        print("lane-overlap: origin/HEAD resolve failed (%s)" % e,
              file=sys.stderr)
    # origin/HEAD unset — probe the standard integration branches (develop-first
    # for the fork-no-merge streams whose main checkout IS a feature branch;
    # airuleset has no `develop`, so it correctly lands on main).
    for cand in ("origin/develop", "origin/main", "origin/master",
                 "develop", "main"):
        try:
            r = run(["git", "-C", repo_root, "rev-parse", "--verify",
                     "--quiet", cand])
        except Exception:  # noqa: BLE001 — a probe error just skips this cand
            continue
        if getattr(r, "returncode", 1) == 0 and (r.stdout or "").strip():
            return cand
    if records and records[0].get("branch"):
        return records[0]["branch"]
    return "main"


def _is_lane_worktree(record):
    """True when a worktree record is a dispatched LANE (#1031): a Claude Code
    ``isolation: worktree`` checkout under ``<repo>/.claude/worktrees/`` (PATH
    match — name-agnostic, so a stream-named branch counts) OR a worktree whose
    branch still carries the ``worktree-`` prefix (today's controller lanes).
    The main checkout matches neither."""
    path = record.get("path") or ""
    if _WORKTREES_SEGMENT in (path + os.sep):
        return True
    branch = record.get("branch") or ""
    return branch.rsplit("/", 1)[-1].startswith("worktree-")


def gather_live_lanes(repo_root, run=None):
    """Live worktree lanes: each lane worktree (a ``.claude/worktrees/``
    isolation checkout OR a ``worktree-*`` branch — #1031) and its touched files
    vs the repo base. A MERGED lane (tip is an ancestor of the integration base
    — #998) is FINISHED, not live, and is EXCLUDED: liveness = the agent still
    working, not "the worktree directory still exists" (8/8 remaining worktrees
    were merged lanes yet lane-overlap reported a false overlap with one). A
    DETACHED-HEAD worktree under the isolation dir is a lane mid-operation and
    counts live (a mid-rebase sha's ancestry is unreliable, so no merged-check).
    Fails toward [] (logs, never raises) — the receipt is still written; a
    missing lane list only means fewer known overlaps, and the supervisor's own
    ``Independence:`` record is the durable authority."""
    run = run or _run_default
    lanes = []
    try:
        wt = run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    except Exception as e:
        print("lane-overlap: worktree list failed (%s)" % e, file=sys.stderr)
        return lanes
    if wt.returncode != 0:
        return lanes
    records = _parse_worktree_records(wt.stdout)
    base_branch = _resolve_base_branch(repo_root, records, run)
    # records[0] is the main checkout = the integration base, never a lane.
    for rec in records[1:]:
        if not _is_lane_worktree(rec):
            continue
        if rec.get("detached"):
            head = rec.get("head") or "detached"
            lanes.append({"ref": head[:12], "files": [],
                          "topic": "(detached lane)"})
            continue
        branch = rec.get("branch")
        if not branch:
            continue
        # #998 — a merged lane (tip is an ancestor of the base) is FINISHED, not
        # a live lane; overlap ignores it. Unmerged lanes stay live.
        if _lane_is_merged(repo_root, branch, base_branch, run):
            continue
        lanes.append({"ref": branch,
                      "files": _lane_files(repo_root, branch, run, base_branch),
                      "topic": _lane_topic(repo_root, branch, run)})
    return lanes


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

    live_lanes = gather_live_lanes(repo_root)
    open_prs = gather_open_prs()
    verdict, overlaps = compute_overlap(paths, topics,
                                        live_lanes=live_lanes, open_prs=open_prs)

    deps = gather_issue_deps(repo_root, issues)   # #993 item 7
    home = os.path.expanduser("~")
    write_receipt(home, _cwd_key(os.getcwd()), issues, verdict, overlaps,
                  deps=deps)

    if verdict == "clear":
        print("CLEAR: issues %s — no path/topic overlap with %d live lane(s) "
              "+ %d open PR(s)" % (issues, len(live_lanes), len(open_prs)))
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
