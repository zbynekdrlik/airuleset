"""Orphaned worktree-branch discovery (#348), split out of cli_worktree_sweep (#1194).

Moved VERBATIM. `discover_orphaned_worktree_branches` finds a local branch
with NO registered worktree at all (a hand-removed directory left it behind);
`sweep_stale_worktrees` in `cli_worktree_stale` is its only consumer and does
the deletion. Stdlib only at module level; the shared repo-discovery helper
`airuleset._checkout_roots` is reached via a deferred `import airuleset`
(internals note 1486), unchanged from the pre-split module.
"""

from pathlib import Path

from cli_worktree_common import (
    STALE_ORPHAN_BRANCH_MIN_AGE_S,
    _STALE_WORKTREE_PROTECTED_BRANCHES,
    _worktree_env_age_s,
    _worktree_git,
    _worktree_porcelain_entries,
    _worktree_sweep_base_branch,
)


def _worktree_branch_ref_age_s(repo_root, branch, now):
    """Seconds since a branch's own LOOSE ref file was last written
    (`.git/refs/heads/<branch>`'s mtime). None when the ref is PACKED (no
    individual loose file exists -- no per-branch mtime is recoverable at
    all) or the mtime is in the future (clock skew) -- unmeasurable is
    NEVER treated as "old enough" to touch."""
    try:
        mtime = (Path(repo_root) / ".git" / "refs" / "heads" / branch).stat().st_mtime
    except OSError:
        return None
    age = now - mtime
    return age if age >= 0 else None


def discover_orphaned_worktree_branches(home=None, git_run=None, now=None,
                                        min_age_s=None):
    """Every local branch, across every managed repo under `home`, with NO
    registered worktree pointing at it at all -- the #348 root cause: `git
    worktree prune` (already run at the top of `discover_stale_worktrees`'s
    own per-repo pass) silently cleans up the dangling ADMIN entry once a
    worktree directory is removed by hand, but never the branch it leaves
    behind. Same output shape as `discover_stale_worktrees`'s own rows --
    `{"branch", "repo", "reason", "base", "kind": "orphan_branch",
    "path": ""}` -- `path` is deliberately the empty string, never `None`
    (which `sweep_stale_worktrees`'s own discovery-error sentinel row
    already reserves), since there is no worktree directory to remove.

    Safety criteria, STRICTER than a registered worktree candidate (#348's
    own named residual risk: a bare, 0-commit branch is byte-for-byte
    identical whether it is a dead worker's abandoned leftover or a
    human's freshly `git branch`'d, not-yet-checked-out intent):
      - never `main`/`dev`/`master`;
      - never a branch a worktree entry (including the PRIMARY checkout)
        already references -- that candidate belongs to
        `discover_stale_worktrees`, never this function;
      - the branch's own ref-file mtime is at least `min_age_s` (several
        days by default) old -- a PACKED ref (no loose-file mtime at all)
        refuses outright, never guessed at;
      - zero commits ahead of the resolved base, via the SAME fully-
        qualified `refs/heads/<base>..refs/heads/<branch>` comparison
        `discover_stale_worktrees` already uses (so #345's own same-
        named-tag hardening covers this path for free).

    Residual (#348 adversarial-review MINOR-3, confirmed): `git pack-
    refs`/`git gc --auto` deletes the branch's own loose ref FILE (the
    thing the age check's mtime comes from) with nothing recreating it
    short of new activity on that branch -- so once a genuinely-old,
    genuinely-reclaimable orphan gets swept up in a routine repack, it
    reads "age unmeasurable (packed ref)" and is refused FOREVER after,
    never becoming eligible again on its own. Safe direction only (a
    packed ref can never look artificially OLDER than it is, only
    unmeasurable) -- it just means this sweep will under-fire on any
    repo whose git gc runs routinely, which is worth knowing, not fixing
    here.
    """
    min_age_s = (_worktree_env_age_s("AIRULESET_WORKTREE_ORPHAN_MIN_AGE_S",
                                     STALE_ORPHAN_BRANCH_MIN_AGE_S)
                if min_age_s is None else min_age_s)
    git_run = git_run or _worktree_git
    import time as _time
    now = _time.time() if now is None else now
    out = []
    import airuleset
    for root in airuleset._checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue          # a worktree/submodule itself -- never a primary repo
        registered = set()
        for e in _worktree_porcelain_entries(root, git_run=git_run):
            b = e.get("branch")
            if b:
                registered.add(b)
        listing = git_run(["for-each-ref", "--format=%(refname:short)",
                          "refs/heads/"], root)
        if listing is None:
            continue
        base = None
        base_resolved = False
        for branch in listing.splitlines():
            branch = branch.strip()
            if not branch or branch in registered:
                continue
            row = {"path": "", "branch": branch, "repo": root, "reason": None,
                  "kind": "orphan_branch"}
            if branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
                row["reason"] = "protected branch name (%s)" % branch
                out.append(row)
                continue
            age = _worktree_branch_ref_age_s(root, branch, now)
            if age is None or age < min_age_s:
                row["reason"] = ("orphan branch too recent to reclaim (< %d d) "
                                 "or age unmeasurable (packed ref)" %
                                 (min_age_s / 86400))
                out.append(row)
                continue
            if not base_resolved:
                base = _worktree_sweep_base_branch(root, git_run=git_run)
                base_resolved = True
            if not base:
                row["reason"] = "no dev/main/master to compare against"
                out.append(row)
                continue
            ahead = git_run(["rev-list", "--count",
                            "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
            if ahead is None:
                row["reason"] = "could not measure commits ahead of %s" % base
                out.append(row)
                continue
            ahead = ahead.strip()
            if ahead != "0":
                row["reason"] = "%s commit(s) ahead of %s -- has real work" % (ahead, base)
                out.append(row)
                continue
            row["base"] = base
            out.append(row)     # reason stays None -- genuine candidate
    return out
