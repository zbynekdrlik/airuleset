"""Reclaim orphaned ``refs/autopilot-wip/*`` durability-backup refs on origin
(#504) — the follow-up both #503 adversarial reviews flagged (A🟡-2, B🟡-3).

#503 has a worktree worker push a CI-neutral durability backup of its branch to
``refs/autopilot-wip/<branch>`` after each commit (``agents/autopilot-worker.md``),
and the supervisor delete it after integrating that branch
(``skills/autopilot/SKILL.md`` Step 4). A worker that committed (so pushed a
backup) and then died on the account cap, whose ticket is then re-dispatched
FRESH (a NEW worktree + branch — the documented common recovery path) rather
than recovered via the #332 fetch path, leaves ``refs/autopilot-wip/<oldbranch>``
on origin with no reclaimer: the Step-4 delete only ever fires on a successful
integration of THAT branch. Per-ref harm is low (a custom ref outside
``refs/heads/*``, invisible to ``git branch -r`` and the GitHub branch UI, not in
the default fetch refspec, no CI) but it is unbounded and — unlike every other
leaked resource in this repo (stale worktrees → ``sweep_stale_worktrees``,
unreported merges → watchdog job 25) — had no backstop.

This is the ORIGIN-REF counterpart of ``cli_worktree_sweep.py``'s LOCAL
worktree+branch sweep (#345/#348): the same fleet-wide, per-repo, age+safety
discipline, for a REMOTE custom-ref namespace. It is a watchdog ``run_once``
job (the FREEZE that forced #345 into a plain install-time CLI function is
LIFTED — project ``CLAUDE.md``, 2026-08-15), reusing the per-repo network infra
``run_once`` already carries for ``stuck_main_sweep`` (job 28): ``repo_roots``,
the injected best-effort ``git_fetch``, ``_sweep_due`` cadence gating and
``_repo_sweep_batch`` round-robin repo batching.

Idiom (cluster C, #433): ONE top-level ``import watchdog``; every reused
package name (``_sweep_due`` / ``_repo_sweep_batch``) is read at CALL time as
``watchdog.<name>``. Circular-import-safe: ``__init__.py``'s facade re-export
loads this module mid-init, but this module dereferences NO ``watchdog``
attribute at load time — only inside function bodies, long after the package
finishes initializing.

SAFETY INVARIANT — never delete a salvageable copy (the exact thing #503 exists
to preserve). A wip ref is deleted ONLY when POSITIVELY proven either

  (a) MERGED — its tip is an ancestor of ``origin/dev|main|master``, so the work
      is durably integrated on origin and the backup is redundant remote litter;
      OR
  (b) AGED — its tip commit is older than ``max_age_s`` (default 7d), so the
      round is long over and any dead worker's copy would have been recovered
      by now.

ANYTHING uncertain — a git error, an unresolvable merge base, an unreadable
commit date, a fetch failure, an ``ls-remote`` failure — leaves the ref
UNTOUCHED and logs a decision line (#486). A stale local ``origin/main`` only
makes the merged check MORE conservative (keep longer), never wrongly deletes.
And every delete is LEASE-GUARDED (``--force-with-lease=<ref>:<sha>``): it only
removes the EXACT sha that was classified, so a (resurrected) worker that pushed
new work to the same ref between the ``ls-remote`` read and the delete has its
delete REFUSED, not its work destroyed — the read-vs-delete window on the
invariant, closed the same way ``sweep_stale_worktrees`` re-checks right before
its own destructive step (salvage-before-discarding-work.md).
"""
import os

import watchdog

WIP_REF_PREFIX = "refs/autopilot-wip/"
WIP_REF_MAX_AGE_S = 7 * 24 * 3600        # env AIRULESET_WIP_REF_MAX_AGE_S
WIP_REF_SWEEP_INTERVAL_S = 6 * 3600      # env AIRULESET_WIP_REF_SWEEP_S — orphan
                                          # cleanup is low-urgency; no hourly need
WIP_GIT_TIMEOUT_S = 30                    # network ops (ls-remote/fetch/push)
_WIP_BASE_REMOTE_REFS = ("refs/remotes/origin/dev",
                         "refs/remotes/origin/main",
                         "refs/remotes/origin/master")


def _repo_label(root):
    """Cheap, network-free journal label for ``root`` (its basename) — never the
    ``_repo_label`` in ``repo_health.py`` (that one shells ``git`` for the
    ``owner/name`` remote, which a network-free test must not trigger)."""
    return os.path.basename(str(root).rstrip("/")) or str(root)


def _wip_git(args, cwd, timeout=WIP_GIT_TIMEOUT_S):
    """``git -C <cwd> <args>`` → ``(returncode, stdout)``; ``(None, "")`` on any
    subprocess-level failure (spawn error, timeout) — never raises, so the sweep
    can't crash a ``run_once`` poll. Unlike ``cli_worktree_sweep._worktree_git``
    (stdout-or-None), this KEEPS the return code: ``git merge-base
    --is-ancestor`` is exit-code-only (no stdout), so a stdout-or-None seam would
    conflate rc==0 'is ancestor' (empty, falsy stdout) with rc!=0 'not ancestor /
    error' (None). Injected as ``git_run`` in tests so no test shells a real
    network op."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(cwd)] + list(args),
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return (None, "")     # not a silent swallow — the sentinel IS the signal
    return (r.returncode, r.stdout)


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _ls_remote_wip_refs(root, git_run):
    """``(sha, full-refname)`` for every ``refs/autopilot-wip/*`` on origin. ``[]``
    on any ``ls-remote`` failure — a repo whose remote can't be listed is simply
    skipped, never guessed at (fail-safe: nothing classified → nothing deleted).
    Enumeration is the ONLY way to see these refs: they are outside the default
    fetch refspec, so ``git fetch`` never brings them and no local ref mirrors
    them."""
    rc, out = git_run(["ls-remote", "origin", WIP_REF_PREFIX + "*"], root)
    if rc != 0 or not out:
        return []
    refs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        # A ref value can legally carry a `^{}` peeled-tag suffix; wip refs are
        # branch tips (never tags), but guard the prefix match exactly anyway.
        if sha and ref.startswith(WIP_REF_PREFIX) and not ref.endswith("^{}"):
            refs.append((sha, ref))
    return refs


def _resolve_base_refs(root, git_run):
    """The subset of ``origin/dev|main|master`` that resolves locally (post-
    fetch) — the bases a merged wip tip would be an ancestor of. ``[]`` if none
    resolve, in which case the merged check is simply skipped and everything
    falls through to the age gate (conservative)."""
    bases = []
    for ref in _WIP_BASE_REMOTE_REFS:
        rc, out = git_run(["rev-parse", "--verify", "--quiet", ref], root)
        if rc == 0 and (out or "").strip():
            bases.append(ref)
    return bases


def _is_merged(root, sha, bases, git_run):
    """True iff ``sha`` is an ancestor of ANY base ref (the work is durably
    integrated on origin). ``git merge-base --is-ancestor`` needs ``sha`` to be a
    LOCAL object: a merged tip is reachable from ``origin/<base>`` (local after
    the fetch), while a non-merged foreign tip is not local → rc 128 (bad
    object) → treated as NOT merged. Only a POSITIVE rc==0 counts as merged, so
    a bad-object / errored check can never manufacture a merged verdict."""
    for base in bases:
        rc, _ = git_run(["merge-base", "--is-ancestor", sha, base], root)
        if rc == 0:
            return True
    return False


def _commit_age_s(root, sha, ref, now, git_run):
    """Age in seconds of ``sha``'s committer date, or ``None`` if it can't be
    determined at all (caller then KEEPS the ref — never guess an age). Tries a
    local ``git show`` first (``sha`` is local for a merged ref or one this box
    itself created); on failure fetches ONLY this ref into ``FETCH_HEAD`` and
    reads that (the minimum network to age a foreign, not-yet-local tip)."""
    rc, out = git_run(["show", "-s", "--format=%ct", sha], root)
    ct = (out or "").strip()
    if rc != 0 or not ct.isdigit():
        frc, _ = git_run(["fetch", "origin", ref], root)
        if frc != 0:
            return None
        rc2, out2 = git_run(["show", "-s", "--format=%ct", "FETCH_HEAD"], root)
        ct = (out2 or "").strip()
        if rc2 != 0 or not ct.isdigit():
            return None
    return now - int(ct)


def classify_wip_ref(root, sha, ref, bases, now, git_run, max_age_s):
    """Pure decision for ONE wip ref → ``{ref, sha, action, reason}`` where
    ``action`` ∈ {``delete-merged``, ``delete-aged``, ``keep``}. Never mutates
    anything. The safety invariant lives here: a delete verdict requires a
    POSITIVE merged proof or a POSITIVE age past ``max_age_s``; every uncertain
    or young case returns ``keep``."""
    short = sha[:8]
    if _is_merged(root, sha, bases, git_run):
        return {"ref": ref, "sha": sha, "action": "delete-merged",
                "reason": "merged into an origin base (%s) — backup redundant" % short}
    age = _commit_age_s(root, sha, ref, now, git_run)
    if age is None:
        return {"ref": ref, "sha": sha, "action": "keep",
                "reason": "unmerged, commit age unknown — kept (never guess)"}
    if age >= max_age_s:
        return {"ref": ref, "sha": sha, "action": "delete-aged",
                "reason": "unmerged, tip %dd old >= %dd gate — abandoned"
                          % (int(age // 86400), int(max_age_s // 86400))}
    return {"ref": ref, "sha": sha, "action": "keep",
            "reason": "unmerged, tip %dd old < %dd gate — may be a dead worker's "
                      "only copy, kept" % (int(age // 86400), int(max_age_s // 86400))}


def discover_orphaned_wip_refs(root, now, git_run=None, git_fetch=None,
                               max_age_s=None, logs=None):
    """Classify every ``refs/autopilot-wip/*`` on ``root``'s origin → a list of
    ``{ref, sha, action, reason}`` dicts (``[]`` if the repo has none / can't be
    listed). Pure discovery+classification — ``sweep_orphaned_wip_refs`` is the
    only function that ever pushes a delete.

    Freshens origin (best-effort ``git_fetch``) ONLY when there is at least one
    wip ref to classify — the merged check needs a fresh ``origin/<base>`` + the
    merged objects local; a fetch failure is APPENDED to ``logs`` (never
    silently swallowed) and degrades to age-gate-only, which is still safe (an
    un-fetched merged ref just reads as unmerged and waits for the age gate)."""
    git_run = git_run or _wip_git
    if max_age_s is None:
        max_age_s = _env_int("AIRULESET_WIP_REF_MAX_AGE_S", WIP_REF_MAX_AGE_S)
    refs = _ls_remote_wip_refs(root, git_run)
    if not refs:
        return []
    if git_fetch is not None:
        try:
            git_fetch(root)
        except Exception as e:
            if logs is not None:
                logs.append("wip-ref-sweep fetch-degraded %s: %r "
                            "(merged check may be stale — kept conservative)"
                            % (_repo_label(root), e))
    bases = _resolve_base_refs(root, git_run)
    return [classify_wip_ref(root, sha, ref, bases, now, git_run, max_age_s)
            for sha, ref in refs]


def _delete_wip_ref(root, ref, sha, git_run):
    """Lease-guarded delete: remove ``ref`` from origin ONLY if the remote is
    still at exactly ``sha``. ``--force-with-lease=<ref>:<sha>`` makes the delete
    a compare-and-swap — if anything (a resurrected worker) pushed new work to
    this exact ref between our ``ls-remote`` read and now, the remote is no
    longer at ``sha`` so the delete is REFUSED ("stale info", rc!=0) and the ref
    is kept, never the new work destroyed. Verified live (issue #504). Returns
    True iff the ref was actually deleted."""
    rc, _ = git_run(["push", "origin",
                    "--force-with-lease=%s:%s" % (ref, sha), ":" + ref], root)
    return rc == 0


def sweep_orphaned_wip_refs(now, state, repo_roots=None, git_run=None,
                            git_fetch=None, dry_run=False,
                            interval=None, max_age_s=None, persist=None,
                            max_repos=None):
    """run_once job: reclaim orphaned ``refs/autopilot-wip/*`` across the fleet.

    Cadence-gated (``_sweep_due`` on its OWN state key ``wip_ref_last_sweep``,
    generous default interval) and repo-batched (``_repo_sweep_batch`` on its own
    cursor), mirroring ``stuck_main_sweep``'s kill-safe shape (#172): the cadence
    marker AND the batch cursor are persisted BEFORE any network op leaves this
    process, so a systemd ``TimeoutStartSec`` kill mid-sweep can never re-attempt
    the identical expensive sweep forever, and coverage still rotates through
    every repo over successive sweeps.

    Best-effort per repo (a discover error on one repo is logged and skipped,
    never raised out). Returns log lines — a decision line PER ref (#486), so the
    journal shows exactly why each ref was kept or deleted. ``dry_run`` mutates
    no persistent state and pushes no delete (peeks the batch on a throwaway
    state copy, exactly like ``stuck_main_sweep``)."""
    git_run = git_run or _wip_git
    persist = persist or (lambda: None)
    if interval is None:
        interval = _env_int("AIRULESET_WIP_REF_SWEEP_S", WIP_REF_SWEEP_INTERVAL_S)
    logs = []
    if not watchdog._sweep_due(state, "wip_ref_last_sweep", now, interval):
        return logs
    if not dry_run:
        # #172 F4: stamp + persist the cadence marker BEFORE the os.walk /
        # any network op — a mid-sweep kill must never re-run this forever.
        state["wip_ref_last_sweep"] = now
        persist()
    repos = sorted(set(repo_roots() if callable(repo_roots) else (repo_roots or [])))
    if dry_run:
        batch = watchdog._repo_sweep_batch(repos, dict(state), "wip_ref_cursor", max_repos)
    else:
        batch = watchdog._repo_sweep_batch(repos, state, "wip_ref_cursor", max_repos)
        persist()      # cursor advance also survives a kill BEFORE any fetch

    for root in batch:
        label = _repo_label(root)
        try:
            classified = discover_orphaned_wip_refs(
                root, now, git_run=git_run, git_fetch=git_fetch,
                max_age_s=max_age_s, logs=logs)
        except Exception as e:
            logs.append("wip-ref-sweep discover-error %s: %r" % (label, e))
            continue
        for c in classified:
            short = c["sha"][:8]
            if c["action"] == "keep":
                logs.append("wip-ref KEEP %s %s [%s] -- %s"
                            % (label, c["ref"], short, c["reason"]))
                continue
            if dry_run:
                logs.append("wip-ref WOULD-DELETE %s %s [%s] -- %s"
                            % (label, c["ref"], short, c["reason"]))
                continue
            if _delete_wip_ref(root, c["ref"], c["sha"], git_run):
                logs.append("wip-ref DELETED %s %s [%s] -- %s"
                            % (label, c["ref"], short, c["reason"]))
            else:
                logs.append("wip-ref DELETE-REFUSED %s %s [%s] -- %s "
                            "(lease stale or push failed — kept)"
                            % (label, c["ref"], short, c["reason"]))
    return logs
