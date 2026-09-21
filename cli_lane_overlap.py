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


def _pick_most_advanced_ref(repo_root, candidates, run):
    """Among ``candidates`` (existing refs, in preference order) return the tip
    that CONTAINS every other — the most advanced integration point. ``cand``
    contains ``other`` when ``other`` is an ancestor of ``cand`` (``git
    merge-base --is-ancestor other cand`` exits 0). When no single tip contains
    all the others the candidates have genuinely DIVERGED (e.g. a fork's
    ``origin/develop`` and the real ``upstream/develop`` that both moved on): log
    it and return the newest by commit date (``log -1 --format=%ct``). A single
    candidate is returned verbatim; an empty list yields ``None``. Fails toward
    the first candidate, never raises."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _contains(cand, other):
        try:
            r = run(["git", "-C", repo_root, "merge-base", "--is-ancestor",
                     other, cand])
        except Exception:  # noqa: BLE001 — any git error => "does not contain"
            return False
        return getattr(r, "returncode", 1) == 0

    for cand in candidates:
        if all(_contains(cand, other) for other in candidates if other != cand):
            return cand

    print("lane-overlap: base candidates diverged (%s) — newest-commit-date pick"
          % ", ".join(candidates), file=sys.stderr)

    def _cdate(ref):
        try:
            r = run(["git", "-C", repo_root, "log", "-1", "--format=%ct", ref])
            return int((r.stdout or "").strip())
        except Exception:  # noqa: BLE001 — unparsable/missing date => oldest
            return 0
    return max(candidates, key=_cdate)


def _resolve_base_branch(repo_root, records, run):
    """The integration base a lane's merged-ness AND its touched-file diff are
    judged against — the SINGLE source of truth for both (it feeds
    ``_lane_is_merged`` and ``_lane_files`` inside ``gather_live_lanes``).

    Two steps (#1031 fork follow-up):

    1. NAME — ``develop`` when ANY remote or the local repo carries a
       ``develop`` ref (the 3-branch odoo projects), detected with ``git
       for-each-ref`` (deliberately NOT ``rev-parse`` — a shape the sibling test
       fakes leave inert, so it can never false-positive off an over-permissive
       ``rev-parse origin/*`` stub); else the name from ``git symbolic-ref
       refs/remotes/origin/HEAD``; else a ``main``/``master`` probe.
    2. TIP — among the candidates ``upstream/<name>``, ``origin/<name>`` and
       local ``<name>`` that actually exist, the tip that CONTAINS the others
       (``_pick_most_advanced_ref``). So a fork-no-merge stream (``origin`` = a
       fork whose ``develop`` is STALE, ``upstream`` = the real repo its lanes
       cut from and merge into) judges against ``upstream/develop``; gk
       (``origin`` IS the real upstream) against ``origin/develop``; and the
       controller against LOCAL ``main`` when it is ahead of ``origin/main`` — a
       lane merged into local ``main`` but not yet pushed during the ~35-min
       ``push`` window must not read as unmerged and block the next sequential
       dispatch (the v0.1.293 unpushed-integration rule, now the SAME "most
       advanced" rule generalised from two candidates to three).

    NEVER the main checkout's own branch when it is a FEATURE branch (a stream
    box sits on ``david3/…``). Fails toward the legacy probe / the first
    worktree branch / ``main``, never raises."""
    def _verify(ref):
        try:
            r = run(["git", "-C", repo_root, "rev-parse", "--verify",
                     "--quiet", ref])
        except Exception:  # noqa: BLE001
            return False
        return getattr(r, "returncode", 1) == 0 and bool((r.stdout or "").strip())

    def _develop_ref_exists():
        # for-each-ref (NOT rev-parse) over local heads + every remote-tracking
        # dir, so a fork whose origin/HEAD is unset still resolves the NAME to
        # develop, and a fake that over-approves `rev-parse origin/*` cannot
        # false-positive it.
        try:
            r = run(["git", "-C", repo_root, "for-each-ref", "--format=%(refname)",
                     "refs/heads/develop", "refs/remotes/*/develop"])
        except Exception:  # noqa: BLE001
            return False
        return getattr(r, "returncode", 1) == 0 and bool((r.stdout or "").strip())

    # 1) NAME. A `develop` ref present anywhere ⇒ develop IS the integration
    # base (the 3-branch odoo model); this deliberately overrides origin/HEAD.
    # 2-branch projects integrate on `dev` (not `develop`), which the
    # `refs/remotes/*/develop` pattern does not match, so they fall through.
    name = None
    if _develop_ref_exists():
        name = "develop"
    if name is None:
        try:
            r = run(["git", "-C", repo_root, "symbolic-ref", "--quiet",
                     "refs/remotes/origin/HEAD"])
            if getattr(r, "returncode", 1) == 0:
                ref = (r.stdout or "").strip()
                if ref.startswith("refs/remotes/origin/"):
                    name = ref[len("refs/remotes/origin/"):]
                elif ref.startswith("refs/remotes/"):
                    return ref[len("refs/remotes/"):]  # a non-origin remote HEAD
                elif ref:
                    return ref
        except Exception as e:  # noqa: BLE001
            print("lane-overlap: origin/HEAD resolve failed (%s)" % e,
                  file=sys.stderr)
    if name is None:
        for cand in ("main", "master"):
            if _verify("origin/" + cand) or _verify(cand):
                name = cand
                break

    if name is None:
        # nothing resolved a name — the legacy origin/HEAD-unset probe, then the
        # first worktree branch, then main (a bare/odd repo still gets a base).
        for cand in ("origin/develop", "origin/main", "origin/master",
                     "develop", "main"):
            if _verify(cand):
                return cand
        if records and records[0].get("branch"):
            return records[0]["branch"]
        return "main"

    # 2) TIP — the most advanced of the existing {upstream, origin, local}.
    candidates = [c for c in ("upstream/" + name, "origin/" + name, name)
                  if _verify(c)]
    picked = _pick_most_advanced_ref(repo_root, candidates, run)
    if picked:
        return picked
    return "origin/" + name  # name known but no candidate verified — conventional base


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


# #1103 — the lane STATES the single liveness derivation returns. A lane is
# LIVE only while an AGENT still works it; a lane that is merged into its target
# or handed off to the gatekeeper is FINISHED (not live). The two states that
# COUNT as live (participate in the overlap set + the sequential cap) are `live`
# and `idle-unmerged` — the latter is the #998 fail-safe: an unmerged lane with
# NO positive evidence either way is kept live so a new unit never dispatches
# onto its files. `merged` and `finished` are EXCLUDED.
_LIVE_STATES = ("live", "idle-unmerged")

# The tickets-status cache freshness window the hand-off read trusts (mirrors
# gates/lanefill._CACHE_TTL_S / the footer refresh TTL).
_CACHE_TTL_S = 180

# branch names whose merged-check must be judged against `main`, not the
# integration base (`develop`): a gk hotfix / bring-upstream lane targets main,
# so it would NEVER read as "merged into develop" and pin its files forever.
_MAIN_TARGET_RX = re.compile(r"hotfix-main|bring-upstream")


def _ref_exists(repo_root, ref, run):
    """True when ``git rev-parse --verify --quiet <ref>`` resolves. Never raises."""
    try:
        r = run(["git", "-C", repo_root, "rev-parse", "--verify", "--quiet", ref])
    except Exception:  # noqa: BLE001 — any git error => the ref is not usable
        return False
    return getattr(r, "returncode", 1) == 0 and bool((r.stdout or "").strip())


def _lane_target(repo_root, branch, base_branch, run):
    """The ref a lane's merged-ness is judged against (#1103). The branch's OWN
    target, not always the integration base: (1) the branch's configured
    upstream when it resolves; (2) a ``main``-family ref for a
    ``hotfix-main``/``bring-upstream`` branch (the most advanced existing of
    upstream/origin/local main|master); (3) otherwise the integration base.
    Fails toward ``base_branch``, never raises."""
    try:
        r = run(["git", "-C", repo_root, "rev-parse", "--abbrev-ref",
                 "--symbolic-full-name", branch + "@{upstream}"])
        up = (r.stdout or "").strip() if getattr(r, "returncode", 1) == 0 else ""
        if up and up != branch:
            return up
    except Exception as e:  # noqa: BLE001 — no upstream / git error => pattern/base
        print("lane-overlap: upstream probe for %s failed (%s)" % (branch, e),
              file=sys.stderr)
    if _MAIN_TARGET_RX.search(branch or ""):
        for cand in ("upstream/main", "origin/main", "main",
                     "upstream/master", "origin/master", "master"):
            if _ref_exists(repo_root, cand, run):
                return cand
        return "main"
    return base_branch


def _worktree_process_cwds():
    """Set of realpath strings of every readable ``/proc/*/cwd`` — the live
    process cwds on this box (Linux only, best-effort). A worker actively running
    in a lane has its cwd inside that lane's worktree. Never raises; {} on any
    platform/permission failure (a process we cannot read simply is not counted —
    the fail-safe direction is to fall back to the other live signals)."""
    cwds = set()
    try:
        entries = os.listdir("/proc")
    except Exception:  # noqa: BLE001 — non-Linux / no /proc => no process signal
        return cwds
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            target = os.readlink(os.path.join("/proc", entry, "cwd"))
        except OSError:
            continue          # a process we cannot read is simply not counted
        if target.endswith(" (deleted)"):
            target = target[:-len(" (deleted)")]
        cwds.add(os.path.realpath(target))
    return cwds


def _path_has_proc(worktree_path, proc_cwds):
    """True when a live process' cwd is AT or INSIDE ``worktree_path``."""
    if not worktree_path or not proc_cwds:
        return False
    wp = os.path.realpath(worktree_path)
    for c in proc_cwds:
        if c == wp or c.startswith(wp + os.sep):
            return True
    return False


def _live_worker_agent_ids(repo_root, projects_dir=None, now=None,
                           freshness_s=None):
    """Set of worker agent-ids (``agent-<hash>``) with a FRESH LIVE subagent
    transcript under the repo's project dir. A worktree-isolated worker's
    subagent transcript stem EQUALS its worktree DIRECTORY basename
    (``.claude/worktrees/agent-<hash>`` <=> ``…/<sid>/subagents/agent-<hash>``),
    so this set matched against ``basename(worktree_path)`` is the
    transcript-evidence live signal the lane-fill gate already trusts. Reuses
    ``watchdog.count_live_workers`` per session dir (its wedged/finished/stale
    semantics — no parser duplicated). Best-effort; {} on any failure."""
    ids = set()
    try:
        import watchdog
        import watchdog.transcripts as T
        if freshness_s is None:
            from watchdog.compact import COMPACT_LIVE_WORKER_FRESHNESS_S
            freshness_s = COMPACT_LIVE_WORKER_FRESHNESS_S
        projects_dir = projects_dir or os.path.join(
            os.path.expanduser("~"), ".claude", "projects")
        now = time.time() if now is None else now
        proj = os.path.join(projects_dir, T.encode_project_dir(repo_root))
        if not os.path.isdir(proj):
            return ids
        for name in os.listdir(proj):
            if not os.path.isdir(os.path.join(proj, name, "subagents")):
                continue
            try:
                _count, evidence = watchdog.count_live_workers(
                    projects_dir, repo_root, name, now, freshness_s)
            except Exception:  # noqa: BLE001 — one bad session never sinks the set
                continue
            for lane in evidence or []:
                if getattr(lane, "state", None) == "live":
                    ids.add(lane.agent_id)
    except Exception as e:  # noqa: BLE001
        print("lane-overlap: worker-transcript evidence unavailable (%s)" % e,
              file=sys.stderr)
    return ids


def _lane_ticket_numbers(repo_root, branch, run):
    """Candidate ticket numbers for a lane: the leading number of the branch's
    last path segment (``<stream>/<N>-…`` / ``worktree-issue-<N>`` /
    ``worktree-<N>``) PLUS every ``#N`` in the lane's recent commit subjects
    (the design's two sources). A spurious number never marks a lane finished —
    it only matches when it is ALSO in the hand-off set. Never raises."""
    nums = set()
    seg = (branch or "").rsplit("/", 1)[-1]
    if seg.startswith("worktree-"):
        seg = seg[len("worktree-"):]
    if seg.startswith("issue-"):
        seg = seg[len("issue-"):]
    m = re.match(r"(\d{2,7})", seg)
    if m:
        nums.add(int(m.group(1)))
    try:
        r = run(["git", "-C", repo_root, "log", "--format=%s", "-20", branch])
    except Exception:  # noqa: BLE001 — subjects unavailable => branch number only
        return nums
    if getattr(r, "returncode", 1) == 0:
        for mm in re.finditer(r"#(\d{2,7})", r.stdout or ""):
            nums.add(int(mm.group(1)))
    return nums


def _handoff_numbers(cwd, home=None):
    """Set of ticket numbers this box has HANDED OFF (gk / merged-unreleased),
    read from the FRESH tickets-status cache — ZERO gh on the hot path
    (statusbar.cache_dir()/<cwd_key>.json; the hand-off subset is
    ``gk_numbers`` ∪ ``merged_unreleased_numbers``). Stale / missing / an older
    writer without the key => {} => a lane can never be proven finished and falls
    to ``idle-unmerged`` (fail-safe live). Never raises."""
    nums = set()
    try:
        import statusbar
        p = statusbar.cache_dir(home=home) / (statusbar.cwd_key(cwd) + ".json")
        with open(p) as f:
            entry = json.load(f)
    except Exception:  # noqa: BLE001 — absent/unreadable/malformed => empty (fail-safe)
        return nums
    if not isinstance(entry, dict):
        return nums
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)) or (time.time() - ts) > _CACHE_TTL_S:
        return nums                       # stale => fail-safe empty
    for key in ("gk_numbers", "merged_unreleased_numbers"):
        for n in (entry.get(key) or []):
            if isinstance(n, int):
                nums.add(n)
    return nums


def classify_lanes(repo_root, run=None, *, now=None, live_worker_ids=None,
                   proc_cwds=None, handoff_numbers=None, projects_dir=None,
                   cwd=None):
    """The SINGLE liveness derivation (#1103). Return one dict per lane worktree
    ``{"ref", "path", "files", "topic", "state"}`` where ``state`` is one of:

      ``live``          — an agent is still working it: a fresh worker transcript
                          matches the worktree OR a live process' cwd is inside it
                          (also a detached-HEAD lane mid-operation).
      ``merged``        — the tip is an ancestor of the lane's TARGET (main for a
                          hotfix/bring-upstream lane, else the integration base):
                          integrated, EXCLUDED from live.
      ``finished``      — unmerged, no live evidence, but the lane's ticket is
                          handed off (gk / merged-unreleased): done for THIS box,
                          EXCLUDED from live.
      ``idle-unmerged`` — unmerged, no evidence either way: still LIVE (the #998
                          fail-safe toward the overlap set).

    Evidence seams (each None => derived from the box; a set overrides for tests):
    ``live_worker_ids`` (agent-ids), ``proc_cwds`` (process cwd realpaths),
    ``handoff_numbers`` (handed-off ticket numbers). ``files``/``topic`` are
    computed only for the LIVE states (the excluded lanes never enter overlap).
    Fails toward [] (logs, never raises)."""
    run = run or _run_default
    now = time.time() if now is None else now
    cwd = cwd or repo_root
    out = []
    try:
        wt = run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    except Exception as e:
        print("lane-overlap: worktree list failed (%s)" % e, file=sys.stderr)
        return out
    if getattr(wt, "returncode", 1) != 0:
        return out
    records = _parse_worktree_records(wt.stdout)
    base_branch = _resolve_base_branch(repo_root, records, run)

    lane_records = [r for r in records[1:] if _is_lane_worktree(r)]
    if not lane_records:
        return out

    # Gather each evidence source ONCE (the /proc scan + transcript read are per
    # sweep, never per lane), unless the caller injected it (tests).
    if live_worker_ids is None:
        live_worker_ids = _live_worker_agent_ids(repo_root, projects_dir, now)
    if proc_cwds is None:
        proc_cwds = _worktree_process_cwds()
    if handoff_numbers is None:
        handoff_numbers = _handoff_numbers(cwd)

    for rec in lane_records:
        path = rec.get("path") or ""
        if rec.get("detached"):
            head = rec.get("head") or "detached"
            out.append({"ref": head[:12], "path": path, "files": [],
                        "topic": "(detached lane)", "state": "live"})
            continue
        branch = rec.get("branch")
        if not branch:
            continue
        target = _lane_target(repo_root, branch, base_branch, run)
        if _lane_is_merged(repo_root, branch, target, run):
            out.append({"ref": branch, "path": path, "files": [],
                        "topic": branch, "state": "merged"})
            continue
        wt_base = os.path.basename(path.rstrip(os.sep))
        alive = (wt_base in live_worker_ids) or _path_has_proc(path, proc_cwds)
        if alive:
            state = "live"
        elif _lane_ticket_numbers(repo_root, branch, run) & handoff_numbers:
            state = "finished"
        else:
            state = "idle-unmerged"
        if state in _LIVE_STATES:
            out.append({"ref": branch, "path": path,
                        "files": _lane_files(repo_root, branch, run, target),
                        "topic": _lane_topic(repo_root, branch, run),
                        "state": state})
        else:
            out.append({"ref": branch, "path": path, "files": [],
                        "topic": branch, "state": state})
    return out


def state_summary(classified):
    """``"live=X finished=Y idle=Z"`` for the ``lane-overlap`` printer — Y counts
    both ``finished`` and ``merged`` (both are "done, not an agent"), Z counts
    ``idle-unmerged`` (unmerged with no evidence, still live)."""
    live = sum(1 for c in classified if c.get("state") == "live")
    idle = sum(1 for c in classified if c.get("state") == "idle-unmerged")
    done = sum(1 for c in classified
               if c.get("state") in ("finished", "merged"))
    return "live=%d finished=%d idle=%d" % (live, done, idle)


def gather_live_lanes(repo_root, run=None, **seams):
    """Live worktree lanes for the OVERLAP set + the sequential cap: the subset
    of ``classify_lanes`` whose state COUNTS as live (``live`` /
    ``idle-unmerged``). A MERGED lane (#998) or a FINISHED lane (handed off,
    #1103) is EXCLUDED — liveness = the agent still working, not "the worktree
    directory still exists". Each returned dict keeps its ``ref``/``files``/
    ``topic`` contract (``compute_overlap`` / ``dispatch_gate_line`` unchanged)
    and now also carries ``state``/``path``. Fails toward [] (never raises)."""
    return [lane for lane in classify_lanes(repo_root, run=run, **seams)
            if lane.get("state") in _LIVE_STATES]


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
