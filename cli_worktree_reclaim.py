"""Disk-guard reclaimable worktree DIRECTORIES (#834/#939), split out of cli_worktree_sweep (#1194).

Moved VERBATIM. `discover_reclaimable_worktrees` / `_worktree_reclaimable`
answer the disk-guard (watchdog Job 40) question: is this worktree's HEAD
reachable from origin, so its directory can be freed while the branch ref is
kept? It has the #1193 live-lane gate and the precious-ignored-file guard.
Stdlib only at module level; `airuleset._checkout_roots` and `LiveLaneGate`
are deferred imports, unchanged.
"""

import os
from pathlib import Path

from cli_worktree_common import (
    STALE_WORKTREE_IDLE_MIN_AGE_S,
    _STALE_WORKTREE_PROTECTED_BRANCHES,
    _worktree_env_age_s,
    _worktree_git,
    _worktree_in_live_use,
    _worktree_is_clean,
    _worktree_porcelain_entries,
    _worktree_recency_age_s,
    _worktree_sweep_base_branch,
)


# --------------------------------------------------------------------------- #
# #834 — RECLAIMABLE worktree DIRECTORIES (the fork-no-merge blind spot).
#
# `discover_stale_worktrees` above only reclaims a branch with ZERO commits
# ahead of base, so a fork-no-merge stream (david1) — whose lane branches are
# handed off to the gatekeeper and NEVER merged LOCALLY — has every worktree
# classified SALVAGE and NEVER reclaimed (20 G of dead dirs sat forever). The
# disk-guard (watchdog Job 40) needs the OPPOSITE, safe question: is the exact
# work in this directory PRESERVED ON ORIGIN, so the DIRECTORY is disk we can
# free while KEEPING the branch ref? The one invariant that makes "merged OR
# refs/autopilot-wip backup OR hand-off" all safe AND kills the stale-proof
# data-loss risk (a proof that refers to an ANCESTOR the worktree has since
# moved past): the worktree's HEAD commit must be REACHABLE from an origin ref
# (`git merge-base --is-ancestor HEAD <ref>`). A hand-off COMMENT is never
# itself proof — reachability is.
# --------------------------------------------------------------------------- #
_PRECIOUS_IGNORED_PATTERNS = (".env", ".env.*", "*.env", "*.key", "*.pem",
                              "local.settings*", "*.secret")
# glob pathspecs matching the patterns at ANY depth — `git status --ignored`
# (traditional) collapses a wholly-ignored DIRECTORY to one `!! dir/` entry and
# would hide a `.env` inside an ignored `config/` (#834 review 🟡), so enumerate
# per-file via `git ls-files` with these pathspecs (bounded: only precious names).
_PRECIOUS_PATHSPECS = (":(glob)**/.env", ":(glob).env", ":(glob)**/.env.*",
                       ":(glob)**/*.env", ":(glob)**/*.key", ":(glob)**/*.pem",
                       ":(glob)**/local.settings*", ":(glob)**/*.secret")


def _fs_walk_has_precious(path):
    """Bounded filesystem fallback for a worktree whose git state is unreadable
    (an orphan-gitdir): walk for a precious basename. True if one is present."""
    import fnmatch
    try:
        for dirpath, _dirs, files in os.walk(path, onerror=lambda e: None):
            for f in files:
                for pat in _PRECIOUS_IGNORED_PATTERNS:
                    if fnmatch.fnmatch(f, pat):
                        return True
    except OSError:
        return True                         # unmeasurable → fail-safe keep
    return False


def _worktree_has_precious_ignored(path, git_run=None):
    """True if a precious (`.env`/`*.key`/`local.settings*`/…) IGNORED file sits
    in the worktree — `git status --porcelain` catches untracked but NOT ignored
    files, so removing the dir would silently lose a per-worktree secret/local
    config (#834). Enumerated per-file via `git ls-files --others --ignored`
    (recurses into ignored dirs, unlike `status --ignored`'s dir-collapse —
    review 🟡). When git itself cannot read the repo (an orphan-gitdir), falls
    back to a bounded fs walk so an orphan is not falsely kept (review 🟡)."""
    git_run = git_run or _worktree_git
    out = git_run(["ls-files", "--others", "--ignored", "--exclude-standard",
                   "-z", "--", *_PRECIOUS_PATHSPECS], path)
    if out is not None:
        return bool(out.replace("\x00", "").strip())
    return _fs_walk_has_precious(path)      # git unreadable → fs fallback


def _is_orphan_gitdir(path):
    """True for a #537 rename-litter worktree dir: its `.git` FILE points at a
    gitdir that no longer exists (e.g. `/home/david/...` after the rename), so
    git cannot list it at all and there is no recoverable git metadata. The
    owner approved reclaiming these (comment 5503794480). Fail-safe: any read
    error → False (not proven an orphan → never removed here)."""
    gitfile = os.path.join(path, ".git")
    try:
        if not os.path.isfile(gitfile):
            return False
        txt = open(gitfile, encoding="utf-8", errors="replace").read().strip()
    except OSError:
        return False
    if not txt.startswith("gitdir:"):
        return False
    target = txt[len("gitdir:"):].strip()
    return bool(target) and not os.path.exists(target)


def _worktree_head_reachable_from_origin(path, branch, base, head, git_run):
    """A label naming WHICH origin ref has this worktree's HEAD as an ancestor,
    or None when no origin ref does (work not preserved on origin → NEVER
    reclaimed). Tries the ref NAMES first (works when they are local remote-
    tracking refs); the custom `refs/autopilot-wip/*` namespace is not fetched
    into `refs/remotes`, so it is ALSO resolved via `ls-remote` to its origin
    SHA and tested against that (the durability-backup object is local when it
    is HEAD or an ancestor of HEAD). None on any git error — never a false
    positive."""
    def is_anc(ref):
        return git_run(["merge-base", "--is-ancestor", head, ref], path) is not None

    # Only ORIGIN-backed refs count. The remote-tracking refs (origin/main,
    # origin/<base>, origin/<branch>) are on origin by construction. The custom
    # `refs/autopilot-wip/*` namespace is NOT fetched into refs/remotes, so it is
    # proven ONLY via its origin SHA from `ls-remote` — the LOCAL ref name is
    # deliberately NOT tried (a wip backup whose push to origin FAILED leaves a
    # local-only ref that would be a false "preserved on origin" proof, #834
    # review 🟡).
    candidates = []
    if branch:
        candidates.append(("origin-branch", "origin/%s" % branch))
    candidates.append(("origin-main", "origin/main"))
    if base and base not in ("main",):
        candidates.append(("origin-base", "origin/%s" % base))
    seen = set()
    for label, ref in candidates:
        if ref in seen:
            continue
        seen.add(ref)
        if is_anc(ref):
            return label
    if branch:
        ls = git_run(["ls-remote", "origin", "refs/autopilot-wip/%s" % branch], path)
        if ls and ls.strip():
            wipsha = ls.split()[0]
            if wipsha and is_anc(wipsha):
                return "autopilot-wip"
    return None


def _worktree_reclaimable(root, path, branch, base, git_run, now,
                          min_idle_s=STALE_WORKTREE_IDLE_MIN_AGE_S,
                          in_live_use=None, recency_fn=None, precious_fn=None,
                          locked=False, live_gate=None):
    """Classify ONE worktree directory for disk-guard reclaim. Returns a row
    ``{path, branch, repo, reason, kind, reachable_via}`` — ``reason`` is None
    ONLY when the DIRECTORY is safe to free (the branch ref is always kept). The
    guards, cheapest-first: NOT locked (a locked worktree is a live session's,
    #348 — review 🔴), not a live lane (``live_gate``, #1193), not in live use,
    idle > `min_idle_s`, HEAD readable (else classified `orphan-gitdir` when the
    gitdir points nowhere), no precious ignored file, clean tree, and HEAD
    reachable from an origin ref. The precious check runs AFTER the HEAD-read so
    an orphan (unreadable git) is never falsely kept by a git-error fail-safe
    (review 🟡)."""
    git_run = git_run or _worktree_git
    in_live_use = in_live_use or _worktree_in_live_use
    recency_fn = recency_fn or _worktree_recency_age_s
    precious_fn = precious_fn or _worktree_has_precious_ignored
    row = {"path": path, "branch": branch, "repo": root, "reason": None,
           "kind": "worktree", "reachable_via": None}

    kept = live_gate.keep_reason(root, path) if live_gate else None
    if locked or kept:
        row["reason"] = kept or "locked worktree (live session, #348) — never removed"
        return row
    if in_live_use(path):
        row["reason"] = "live process cwd/fd inside — never removed"
        return row
    rec = recency_fn(root, path, now)
    if rec is None or rec <= min_idle_s:
        row["reason"] = "too recent / recency unmeasurable (< 24h idle) — kept"
        return row

    head = git_run(["rev-parse", "HEAD"], path)
    if head is None or not head.strip():
        # git cannot resolve HEAD. A #537 orphan-gitdir (gitdir points nowhere)
        # is reclaimable litter — but only after a precious-file fs scan (review
        # 🟡: the orphan branch must precede + still run the precious check).
        if _is_orphan_gitdir(path):
            if precious_fn(path):
                row["reason"] = "orphan-gitdir but precious ignored file present — kept"
                return row
            row["kind"] = "orphan-gitdir"
            row["reason"] = None
            return row
        row["reason"] = "HEAD unresolvable and not an orphan-gitdir — kept (uncertain)"
        return row
    head = head.strip()

    if precious_fn(path):
        row["reason"] = "precious ignored file present (.env/*.key/local.settings) — kept"
        return row

    # #939: check reachability BEFORE the clean-tree gate. A returned worker's
    # worktree is typically dirty (scratch/temp/build files) but its HEAD is fully
    # preserved on origin — the DIRECTORY is safe to free (the branch ref is always
    # kept). Only when HEAD is NOT reachable do we need the tree for salvage. The
    # old order (clean-tree BEFORE reachability) permanently blocked 100% of
    # returned workers' worktrees (55 worktrees / 20G on david3).
    via = _worktree_head_reachable_from_origin(path, branch, base, head, git_run)
    if via is None:
        row["reason"] = ("HEAD not reachable from any origin ref "
                         "(work not preserved on origin) — kept")
        return row
    row["reachable_via"] = via

    clean = _worktree_is_clean(path, git_run)
    if clean is False:
        row["dirty"] = True                # decision-logged: dirty but reachable
    return row                             # reason None → directory reclaimable


def discover_reclaimable_worktrees(home=None, git_run=None, now=None,
                                   min_idle_s=None, in_live_use=None, live_gate=None):
    """Every worktree DIRECTORY across managed repos under `home` that the
    disk-guard may reclaim (rows with ``reason is None``), plus the skipped ones
    with WHY (a pressure-log needs both). Covers registered worktrees AND #537
    orphan-gitdir litter dirs that `git worktree list` no longer knows. The
    branch ref is always kept — only the directory is freed."""
    git_run = git_run or _worktree_git
    import time as _time
    now = _time.time() if now is None else now
    if min_idle_s is None:
        min_idle_s = _worktree_env_age_s("AIRULESET_WORKTREE_IDLE_MIN_AGE_S",
                                         STALE_WORKTREE_IDLE_MIN_AGE_S)
    out = []
    import airuleset
    from cli_lane_live_gate import LiveLaneGate
    gate = live_gate or LiveLaneGate(home=home, now=now)
    for root in airuleset._checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue
        # NO `git worktree prune` here — a discovery function must not mutate
        # (it also runs under --dry-run; review 🔵). prune only cleans dangling
        # admin entries, which this classifier does not depend on.
        entries = _worktree_porcelain_entries(root, git_run=git_run)
        base = _worktree_sweep_base_branch(root, git_run=git_run)
        known = set()
        for i, e in enumerate(entries):
            path = e.get("path")
            if path:
                known.add(os.path.realpath(path))
            if i == 0:
                continue                   # primary checkout — never a candidate
            branch = e.get("branch")
            if branch in _STALE_WORKTREE_PROTECTED_BRANCHES or not path:
                continue
            if branch is None:
                continue                   # detached HEAD — no branch ref to keep, never guessed (review 🔵)
            out.append(_worktree_reclaimable(
                root, path, branch, base, git_run, now, min_idle_s=min_idle_s,
                in_live_use=in_live_use, locked=bool(e.get("locked")), live_gate=gate))
        # #537 orphan-gitdir dirs git no longer lists: scan the worktrees dir.
        wt_root = Path(root) / ".claude" / "worktrees"
        if wt_root.is_dir():
            try:
                children = list(wt_root.iterdir())
            except OSError:
                children = []
            for d in children:
                if not d.is_dir() or os.path.realpath(str(d)) in known:
                    continue
                if _is_orphan_gitdir(str(d)):
                    out.append(_worktree_reclaimable(
                        root, str(d), None, base, git_run, now,
                        min_idle_s=min_idle_s, in_live_use=in_live_use, live_gate=gate))
    return out
