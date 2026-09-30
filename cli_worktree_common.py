"""Shared plumbing for the worktree reclaimers (#1194 split of cli_worktree_sweep).

Moved VERBATIM out of `cli_worktree_sweep.py` (#1194), which is now a thin
facade re-exporting every name below. This leaf holds what more than one
reclaimer uses:
- the shared protected-branch set;
- the #348 age-gate block, kept whole with its one rationale comment: the
  orphan gate is read by `cli_worktree_orphans`, the locked-dead gate by
  `cli_worktree_stale`;
- the #513 idle floor, read by the stale sweep, the salvage report and the
  disk-guard rung;
- env-age parsing, the `git -C` runner, porcelain parsing, base-branch;
- the admin-dir, clean-tree, recency and live-use probes.

The reclaimers themselves live in `cli_worktree_stale` (#345 sweep + #513
salvage), `cli_worktree_orphans` (#348 orphan branches), `cli_worktree_reclaim`
(#834 disk-guard rung) and `cli_lane_target_reclaim` (#545 lane target/).

Stdlib only at module level, same as the pre-split module: no top-level
`import airuleset`, so there is no import-cycle surface (internals note 1483).
`CLAUDE_DIR` is this file's own copy of the canonical one-line expression.
"""

import os
import sys
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude"


_STALE_WORKTREE_PROTECTED_BRANCHES = ("main", "dev", "master")

# #348 -- the two dominant leak shapes #345's own registered-worktree-only
# scan structurally cannot see: a branch orphaned by a hand-removed
# directory, and a locked worktree whose owning session (the harness locks
# with the MAIN SESSION's pid, never the individual worker's) has exited.
# Both extra safeguards below are AGE gates -- "at least several days" per
# the user's own decision (issue comment 5233902115) -- on top of the
# existing zero-commits-ahead/clean-tree criteria; see `discover_stale_
# worktrees`'s locked-branch handling and `discover_orphaned_worktree_
# branches` for the full five/four-signal chain each requires.
STALE_ORPHAN_BRANCH_MIN_AGE_S = 3 * 24 * 3600  # env AIRULESET_WORKTREE_ORPHAN_MIN_AGE_S
STALE_LOCKED_DEAD_MIN_AGE_S = 3 * 24 * 3600    # env AIRULESET_WORKTREE_LOCKED_DEAD_MIN_AGE_S

# #513 -- the LIVE-WORKER guard. The #345/#348 chain protected a live worker
# only via the harness worktree LOCK, but in-session `isolation:"worktree"`
# workers are NOT lock-registered (live-measured: 0 of 11 registered
# worktrees locked on dev1). An unlocked live worker is legitimately
# 0-commits-ahead-of-base AND clean until its first commit -- byte-for-byte
# indistinguishable from a dead, merged one -- so the LIVE install/push sweep
# classified it a genuine candidate and could `git worktree remove` it out
# from under the running worker (the likely cause of a live worktree
# vanishing mid-work during a session-limit re-dispatch). `_target_in_live_use`
# cannot rescue it: the agent process's cwd is the MAIN checkout, not the
# worktree, so NOTHING in /proc points inside a live worktree (verified live).
# The defensible signal that DOES separate them is recency: measured on dev1,
# live worktrees had activity 2-13 min old, dead ones 22-35 h old. So an
# UNLOCKED registered worktree is reclaimed only if it is ALSO idle for at
# least this window -- an additive skip that can never CAUSE a removal, only
# prevent one, on top of the existing 0-ahead + clean-tree-refusal criteria
# (so even the rare clock-skew edge loses zero work).
# 24h, not 6h (#513 adversarial-review MAJOR): a live 0-ahead+clean worker
# BLOCKED on a `❓` question can wait many hours for the owner's answer (an
# evening->morning wait is easily ~8-15h — questions are asked 24/7, #791,
# but the owner still answers on their own schedule) with zero git/file
# activity while it waits; a 6h window let an install/push sweep remove its
# worktree mid-wait. 24h comfortably exceeds every documented long
# owner-answer wait yet stays ~100x the measured 13-min live-idle max, so a
# genuinely-dead worktree (idle days) is still reclaimed. Env-tunable for a
# one-off aggressive reclaim.
STALE_WORKTREE_IDLE_MIN_AGE_S = 24 * 3600      # env AIRULESET_WORKTREE_IDLE_MIN_AGE_S


def _worktree_env_age_s(env_key, default_s):
    """`int(os.environ.get(env_key, default_s))` -- an unparseable override
    falls back to `default_s`, never crashes the sweep over a typo'd env
    var (mirrors `sweep_stale_worktrees`'s own cadence-interval read)."""
    try:
        return int(os.environ.get(env_key, default_s))
    except ValueError:
        return default_s


def _worktree_git(args, cwd, timeout: int = 15):
    """`git -C <cwd> <args>` -- stdout text on rc==0, else None (never
    guess; every caller treats None as "skip this repo/candidate", never
    as a false positive or negative)."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(cwd)] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def _worktree_porcelain_entries(repo_root, git_run=None):
    """Parse `git worktree list --porcelain -z` -- one dict per registered
    worktree: {"path", "branch" (bare name, no `refs/heads/`, None if
    detached/bare), "locked": bool}. Entry 0 is ALWAYS the PRIMARY
    checkout -- callers must skip it by INDEX, never by path-matching (a
    renamed/symlinked primary checkout must still be protected). Returns
    [] on any read failure -- a repo git can't be read from is simply
    skipped, never guessed at.

    `-z` (NUL-delimited fields, record boundary = an empty field -- the
    NUL-mode equivalent of the blank line git's own non-`-z` format uses)
    is required, not cosmetic: #345 adversarial-review THEORETICAL-1,
    confirmed live -- a worktree whose PATH contains a literal `\\n`
    (legal on Linux) corrupts a plain newline-split parse into a phantom
    entry that can point at an UNRELATED, healthy worktree, which the
    sweep then genuinely removes. `-z` needs no escaping/quoting to be
    newline-safe by construction.
    """
    git_run = git_run or _worktree_git
    out = git_run(["worktree", "list", "--porcelain", "-z"], repo_root)
    if out is None:
        return []
    entries = []
    cur = None
    for field in out.split("\x00"):
        if field == "":
            if cur is not None:
                entries.append(cur)
                cur = None
            continue
        if field.startswith("worktree "):
            if cur is not None:
                entries.append(cur)
            cur = {"path": field[len("worktree "):], "branch": None, "locked": False,
                  "lock_reason": ""}
        elif cur is None:
            continue
        elif field.startswith("branch "):
            ref = field[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else None
        elif field == "locked" or field.startswith("locked "):
            cur["locked"] = True
            # #348 -- the reason text (when the harness locked with
            # `--reason "..."`) is what carries the owning session's own
            # pid/starttime; an unadorned `locked` (no reason at all, e.g.
            # a manual `git worktree lock` with no --reason) leaves this "".
            cur["lock_reason"] = field[len("locked "):] if field.startswith("locked ") else ""
    if cur is not None:
        entries.append(cur)
    return entries


def _worktree_sweep_base_branch(repo_root, git_run=None):
    """The MOST-ADVANCED of `dev`/`main`/`master` that exists locally
    (a strict superset of every OTHER existing one of the three) --
    falling back to the fixed preference order `dev`, `main`, `master`
    ONLY when the existing candidates have genuinely DIVERGED (neither is
    a superset of the other, so there is no safe single answer). `None`
    if none of the three exist (caller skips the whole repo).

    Deliberately NOT the ticket's own literal "ahead of main" wording --
    `skills/autopilot/SKILL.md`'s ROUND INTEGRATION step 2 documents a
    worktree worker as forked from "local main for a local-merge repo,
    local dev for a dev->main PR repo" -- most fleet repos use the
    two-branch dev+main flow (two-branch-workflow.md), where dev is
    normally AHEAD of main by whatever unreleased work is in flight.
    Comparing against bare main would count every one of those in-flight
    dev commits as "the worker's own real work" and permanently skip
    cleanup on every such repo.

    #380 CORRECTION: the ORIGINAL version of this function unconditionally
    preferred `dev` whenever it existed, reasoning "dev >= main in a
    well-behaved two-branch repo, so 0-ahead-of-dev implies 0-ahead-of-main
    too, never the reverse". That invariant is FALSE for airuleset's own
    repo -- `isolation: "worktree"` fleet-dispatch workers (#317) fork from
    and merge straight back into `main` (`cmd_push`'s `git push origin
    main`, round-integration's own merge commits), never through `dev`, so
    `dev` sits permanently BEHIND `main` here (live-measured: `dev..main`
    89 commits, `main..dev` 0). Under the old code, EVERY worktree branch
    forked from main's tip -- fully merged into main, genuinely 0 commits
    ahead of it -- still read as "N commits ahead of dev" forever, since
    dev never caught up, and was NEVER reclaimed: 10 already-merged
    `.claude/worktrees/agent-*` directories sitting on dev1 at the moment
    this was found. The fix: when MORE THAN ONE of dev/main/master exists,
    pick whichever one is not BEHIND any of the others (a real superset) --
    this is STRICTLY MORE conservative than the old dev-preferred pick in
    every case the old reasoning covered (a traditional repo where dev is
    genuinely ahead of main still resolves to dev, since dev is then the
    superset), and it additionally self-heals the airuleset-shaped case
    (main is the superset there) with no separate carve-out needed. When
    the existing candidates have genuinely DIVERGED (both carry unique
    commits -- a real, if rarer, shape: a hotfix landed on main directly
    while dev separately advanced), there is no safe single answer, so
    this falls back to the ORIGINAL fixed preference order (dev, main,
    master) rather than guessing -- identical to the pre-#380 behaviour
    for that one ambiguous case.
    """
    git_run = git_run or _worktree_git
    candidates = []
    for name in ("dev", "main", "master"):
        # #345 adversarial-review MINOR-1: `rev-parse --verify` resolves a
        # TAG named dev/main/master just as happily as a branch -- fully
        # qualify with `refs/heads/` so this can only ever resolve to a
        # genuine local branch, never a same-named tag.
        if git_run(["rev-parse", "--verify", "--quiet", "refs/heads/%s" % name],
                   repo_root) is not None:
            candidates.append(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    for name in candidates:
        others = [c for c in candidates if c != name]
        # `name` is the most-advanced candidate iff EVERY other candidate
        # has zero commits ahead of it -- i.e. `name` already contains
        # every other candidate's own history.
        if all(
            (git_run(["rev-list", "--count",
                     "refs/heads/%s..refs/heads/%s" % (name, other)],
                    repo_root) or "").strip() == "0"
            for other in others
        ):
            return name
    # Diverged -- no candidate is a superset of every other. Ambiguous;
    # fall back to the original fixed preference order rather than guess.
    return candidates[0]


def _worktree_admin_dir(repo_root, worktree_path):
    """Resolve `<repo_root>/.git/worktrees/<name>` for a REGISTERED
    worktree at `worktree_path`, by matching each admin subdir's own
    `gitdir` file (which records the worktree's own `.git` FILE path)
    against `worktree_path/.git` -- robust even when `worktree_path`
    itself no longer exists on disk (a LOCKED worktree whose directory
    was removed by hand keeps its admin entry forever -- `git worktree
    prune` never touches a locked one). None when no match is found."""
    admin_root = Path(repo_root) / ".git" / "worktrees"
    try:
        candidates = list(admin_root.iterdir())
    except OSError:
        return None
    target = str(Path(worktree_path) / ".git")
    for d in candidates:
        try:
            recorded = (d / "gitdir").read_text().strip()
        except OSError:
            continue
        if recorded == target:
            return d
    return None


def _worktree_is_clean(worktree_path, git_run):
    """True only when `git status --porcelain` in the worktree returns
    exactly empty output. False when it reports ANY change. None when the
    check itself could not run (missing directory, git failure) --
    unmeasurable is never treated as clean."""
    out = git_run(["status", "--porcelain"], worktree_path)
    if out is None:
        return None
    return out.strip() == ""


def _worktree_recency_age_s(root, worktree_path, now):
    """Smallest ABSOLUTE distance `|now - mtime|` of the worktree's OWN
    most-recent activity -- the newest of its git-admin metadata (`HEAD`/
    `index`/`logs/HEAD`/`ORIG_HEAD`, updated on every git op the worker
    runs) and its top-level directory entries (updated on every file
    write). Returns the smallest such distance, or None when NO mtime is
    measurable at all (no admin dir + unreadable worktree).

    ABSOLUTE distance, not `now - mtime` (#513 adversarial-review MINOR):
    an at-or-before-`now`-only age treated an mtime AFTER `now` as "no
    recent activity" and let the worktree be removed. That is reachable
    WITHOUT clock skew -- `now` is captured once, then `discover_stale_
    worktrees` runs `git worktree prune`+`list` per repo, so a worktree
    CREATED after `now` was captured but before its repo is listed has all
    mtimes > `now` (a real sub-second-to-seconds race on a multi-repo box);
    that worktree is a brand-new LIVE worker -- exactly the case GAP A must
    protect. Using `|now - mtime|` protects it (its offset from `now` is
    tiny) AND still reclaims a genuinely-dead worktree (its mtimes sit days
    from `now` in EITHER direction, so the distance is large). A test with
    a fixed PAST `now` (worktree mtimes ~days in the future) still yields a
    large distance → correctly not recent → still reclaimed, so no existing
    test changes. Clock skew folds in for free."""
    def _mtime(p):
        try:
            return os.lstat(str(p)).st_mtime
        except OSError:
            return None      # unreadable/absent signal -- simply not counted
    mtimes = []
    admin = _worktree_admin_dir(root, worktree_path)
    if admin is not None:
        for rel in ("HEAD", "index", "logs/HEAD", "ORIG_HEAD"):
            mtimes.append(_mtime(Path(admin) / rel))
    wt = Path(worktree_path)
    mtimes.append(_mtime(wt))
    try:
        with os.scandir(wt) as it:
            names = [e.path for e in it]
    except OSError:
        names = []           # worktree dir gone/unreadable -- admin signals still count
    for name in names:
        mtimes.append(_mtime(name))
    distances = [abs(now - m) for m in mtimes if m is not None]
    return min(distances) if distances else None


def _worktree_in_live_use(worktree_path):
    """True if any live process holds this worktree directory (cwd/fd/exe)
    -- reuses `cli_target_purge._target_in_live_use` (deferred import keeps
    this module's stdlib-only top level). A WEAK signal here (an in-session
    worker's own agent process cwd is the MAIN checkout, not the worktree,
    so this reads False for most live workers -- recency is the primary
    guard), but it DOES catch a process actively rooted in the tree (a
    running test with cwd inside it). Fail-safe: any error → True (treat as
    live, never remove)."""
    try:
        from cli_target_purge import _target_in_live_use
        return _target_in_live_use(worktree_path)
    except Exception as e:      # noqa: BLE001 -- fail-safe: unknown → treat as live
        print("  worktree-sweep: live-use check failed for %s: %s"
              % (worktree_path, e), file=sys.stderr)
        return True
