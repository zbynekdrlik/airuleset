"""cli_lane_liveness — the single lane-LIVENESS derivation for autopilot dispatch (#1103/#1107).

``classify_lanes`` maps every worktree lane to one of ``live | merged | finished |
idle-unmerged`` from evidence (worker transcript / process cwd / hand-off state /
merged-into-target); ``gather_live_lanes`` and ``state_summary`` are the thin
consumers built on it. Extracted VERBATIM from ``cli_lane_overlap`` (#1107) so
the derivation lives in its own leaf — consumed by the ``lane-overlap`` receipt
(``cli_lane_overlap.cmd_lane_overlap``), the sequential concurrency resolver
(``cli_concurrency``) and the watchdog finished-worktree prune rung
(``watchdog/lane_reconcile``).

The overlap/receipt seam ``_run_default`` / ``_lane_files`` / ``_lane_topic``
stays in ``cli_lane_overlap`` and is imported at the BOTTOM of this module (a
deferred import that breaks the load-time cycle with the overlap module's own
bottom-placed back-compat re-exports ``classify_lanes`` etc.; it resolves in
either import order). Pure, dependency-injected (a ``run`` callable for git),
stdlib-only.
"""
import json
import os
import re
import sys
import time

# The Claude Code `isolation: worktree` directory — EVERY dispatched lane is a
# checkout under `<repo>/.claude/worktrees/`, whatever its branch is named (the
# same signal the #817 isolation self-check keys on). #1031: recognising a lane
# by this PATH (not only a `worktree-` branch prefix) is what makes a
# stream-named lane (`david3/<issue>-<slug>`, `lane5337`) count.
_WORKTREES_SEGMENT = os.sep + os.path.join(".claude", "worktrees") + os.sep


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
    """The ref a lane's merged-ness is judged against (#1103). A
    ``hotfix-main``/``bring-upstream`` lane targets ``main`` (the most advanced
    existing of upstream/origin/local main|master), so it is never falsely read
    as "pinned live forever" against ``develop``; every OTHER lane is judged
    against the integration base.

    The branch's own ``@{upstream}`` is DELIBERATELY NOT used as the target
    (#1103 review, BLOCKER): a pushed lane branch tracks its OWN origin ref
    (``origin/<self>`` — set by ``git push -u`` / ``push.autoSetupRemote`` /
    ``gh pr create``), so a just-pushed HEAD==origin lane would be trivially an
    ancestor of its upstream and wrongly classified ``merged`` — dropping a
    genuinely live lane from the overlap set + the sequential cap. The
    ``_MAIN_TARGET_RX`` regex already covers the only case the upstream lookup
    was meant to catch (a hotfix/bring-upstream targeting main). Never raises."""
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
            # #1103 review — reuse the #565/#587 worker-liveness partition
            # (`_LANE_NOT_LIVE_STATES` = stale/finished) rather than a narrower
            # ``== "live"``: a WEDGED / UNREADABLE fresh lane is a worker still
            # in-flight (recoverable), so it counts as live evidence — failing
            # TOWARD keeping a lane live, never dropping a busy-but-not-cleanly-
            # live worker to `finished`. Falls back to the ``live`` literal only
            # if the partition constant is unavailable (older transcripts.py).
            not_live = getattr(T, "_LANE_NOT_LIVE_STATES", frozenset())
            for lane in evidence or []:
                st = getattr(lane, "state", None)
                is_live = (st not in not_live) if not_live else (st == "live")
                if st is not None and is_live:
                    ids.add(lane.agent_id)
    except Exception as e:  # noqa: BLE001
        print("lane-overlap: worker-transcript evidence unavailable (%s)" % e,
              file=sys.stderr)
    return ids


def _lane_ticket_numbers(repo_root, branch, run):
    """The lane's OWN ticket number(s). A branch WITH a leading number in its
    last path segment (``<stream>/<N>-…`` / ``worktree-issue-<N>`` /
    ``worktree-<N>``) uses ONLY that — its own ticket — and does NOT widen via
    commit subjects (#1103 review, 🟡): an incidental other-ticket ``#N`` in a
    subject like ``green(#7184): also closes #7000`` must not mark the lane
    finished off a handed-off #7000 while its own #7184 is live (that would
    erode the ``idle-unmerged`` fail-safe). A branch with NO leading number
    (e.g. ``diag/searchmore-2314``) falls back to the ``#N`` in its recent
    commit subjects — the only ticket signal it has. Never raises."""
    seg = (branch or "").rsplit("/", 1)[-1]
    if seg.startswith("worktree-"):
        seg = seg[len("worktree-"):]
    if seg.startswith("issue-"):
        seg = seg[len("issue-"):]
    m = re.match(r"(\d{2,7})", seg)
    if m:
        return {int(m.group(1))}          # the branch's own number — do not widen
    nums = set()
    try:
        r = run(["git", "-C", repo_root, "log", "--format=%s", "-20", branch])
    except Exception:  # noqa: BLE001 — subjects unavailable => no number
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

# The overlap/receipt seam kept in cli_lane_overlap (#1107 recorded lane
# decision): classify_lanes calls these by bare name. Imported at module
# BOTTOM so the load-time cycle with cli_lane_overlap's own back-compat
# re-exports resolves in either import order.
from cli_lane_overlap import (  # noqa: E402
    _lane_files,
    _lane_topic,
    _run_default,
)
