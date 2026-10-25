"""#1103 — the finished-worktree HYGIENE SWEEP (rides Job 20, no own run_once
job). Removes a lane worktree that is FINISHED for this box — merged into its
target, or handed off with no live process — once it is clean + old, so the
`git worktree list` (which `cli_lane_liveness` derives liveness from) stops
lying. The BRANCH REF is always KEPT (the wip backup + the hand-off live on
origin); a dirty or process-holding worktree is left and journaled. Wired from
run_once's `_job_goal_lane_sweep` closure, gated on the `repo_roots` seam.

#1137 (owner 25.10.2026) removed this module's #844 post-compact lane-reconcile
NUDGE (a keystroke telling the session to integrate returned lanes): Claude
Code decides its own dispatch and integration; only the prune stays.
"""

# A lane worktree is pruned only once its branch tip COMMIT is at least this old
# — so a lane handed off / merged moments ago (or a fresh intra-sweep race) is
# never removed while a worker may still be settling.
#
# COMMIT-age, deliberately NOT the #513 ACTIVITY-recency helper
# (`_worktree_recency_age_s`) here (#1103 review): recency reads the worktree
# INDEX mtime among its signals, and this sweep's OWN clean-check
# (`git status --porcelain`, run every sweep) REFRESHES that index mtime to now
# — empirically verified (3h -> 0h after one `git status`). So a finished
# worktree re-checked each sweep would perpetually read "just active" and NEVER
# age out. Commit history is untouched by `git status`, and the prune's
# candidate set is only FINISHED/MERGED lanes (which by definition stop getting
# commits — a live lane is excluded by the classifier BEFORE the age check), so
# time-since-last-commit == time-since-hand-off, exactly the signal we want.
WORKTREE_PRUNE_MIN_AGE_S = 2 * 3600

# The sweep self-gates per repo at this cadence (git-heavy `worktree list` +
# `status` reads), mirroring the hourly self-gate of the sibling per-repo jobs
# (stuck_main / wip_ref). > age-threshold, so a skipped sweep loses nothing.
WORKTREE_PRUNE_CADENCE_S = 3600


def _prune_run_default(cmd):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def _lane_commit_age_s(repo_root, branch, run, now):
    """Seconds since the lane branch's tip commit (committer date), or None when
    unmeasurable (any error / non-zero rc / unparsable) — None is treated by the
    caller as "too young to prune" (the fail-safe direction). Status-immune (see
    WORKTREE_PRUNE_MIN_AGE_S). Never raises."""
    try:
        r = run(["git", "-C", repo_root, "log", "-1", "--format=%ct", branch])
    except Exception:  # noqa: BLE001
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    try:
        return now - int((r.stdout or "").strip())
    except (TypeError, ValueError):
        return None


def prune_finished_worktrees(repo_root, run=None, *, now=None, dry_run=False,
                             state=None, cadence_s=WORKTREE_PRUNE_CADENCE_S,
                             age_min_s=WORKTREE_PRUNE_MIN_AGE_S,
                             live_worker_ids=None, proc_cwds=None,
                             handoff_numbers=None, projects_dir=None, cwd=None,
                             clean_fn=None, live_use_fn=None, age_fn=None):
    """Remove FINISHED/MERGED lane worktrees under ``repo_root`` that are clean,
    process-free and idle longer than ``age_min_s``; keep (and journal) any that
    are a live lane (#1193 ``cli_lane_live_gate``, now chiefly the grace
    window: #1229 keeps a live commit-less lane out of MERGED), dirty, in live use or
    too young. The branch ref is ALWAYS kept
    (``git worktree remove`` never ``--force``). Returns one decision-log line
    per removed/kept candidate; [] when nothing qualifies or the cadence gate
    holds. Never raises — a hygiene sweep must never crash the watchdog sweep.

    The DISCRIMINATOR (which worktrees are candidates) is the ONE liveness
    derivation (``cli_lane_liveness.classify_lanes``, #367 one-derivation) —
    a lane it counts live is never a candidate. The removal-safety LEAF plumbing is REUSED from the hardened
    worktree sweeper (``cli_worktree_sweep``): ``_worktree_is_clean`` (dirty =>
    keep) and ``_worktree_in_live_use`` (cwd/fd/exe => keep — stronger than the
    classifier's cwd-only signal). The age guard is COMMIT-age
    (``_lane_commit_age_s``), deliberately NOT ``_worktree_recency_age_s`` — the
    clean-check's own ``git status`` refreshes the index mtime the recency helper
    reads (see WORKTREE_PRUNE_MIN_AGE_S). ``clean_fn``/``live_use_fn``/``age_fn``
    are test seams; each None => the default (framework helper or commit-age)."""
    import os
    import time as _time
    import cli_lane_liveness as lo
    import cli_worktree_sweep as ws
    from cli_lane_live_gate import LiveLaneGate
    run = run or _prune_run_default
    now = _time.time() if now is None else now
    logs = []

    # per-repo hourly self-gate (skip the git-heavy read on the 59/60 sweeps
    # that need not run); an untimed caller (tests) passes cadence_s=0.
    if state is not None and cadence_s:
        wp_state = state.setdefault("worktree_prune", {})
        last = wp_state.get(repo_root)
        if isinstance(last, (int, float)) and (now - last) < cadence_s:
            return logs
        wp_state[repo_root] = now

    def _git_run(args, wt_cwd):
        try:
            r = run(["git", "-C", str(wt_cwd)] + list(args))
        except Exception:  # noqa: BLE001 — unmeasurable, never a false "clean"
            return None
        return r.stdout if getattr(r, "returncode", 1) == 0 else None

    clean_fn = clean_fn or (lambda p: ws._worktree_is_clean(p, _git_run))
    live_use_fn = live_use_fn or ws._worktree_in_live_use
    age_fn = age_fn or (lambda ref: _lane_commit_age_s(repo_root, ref, run, now))

    if proc_cwds is None:
        proc_cwds = lo._worktree_process_cwds()
    gate = LiveLaneGate(projects_dir=projects_dir, now=now, ids=live_worker_ids)
    live_worker_ids, err = gate.evidence(repo_root)
    if err:                                  # #1193 unreadable liveness => prune nothing
        return logs + ["worktree-prune %s -> skip:lane-liveness-unknown (%s)" % (repo_root, err)]
    try:
        lanes = lo.classify_lanes(repo_root, run=run, now=now,
                                  live_worker_ids=live_worker_ids,
                                  proc_cwds=proc_cwds,
                                  handoff_numbers=handoff_numbers,
                                  projects_dir=projects_dir, cwd=cwd)
    except Exception as e:  # noqa: BLE001 — never crash the sweep
        logs.append("worktree-prune %s -> skip:classify-failed (%r)"
                    % (repo_root, e))
        return logs

    for lane in lanes:
        if lane.get("state") not in ("merged", "finished"):
            continue
        path = lane.get("path")
        if not path or not os.path.isdir(path):
            continue
        ref = lane.get("ref")
        # removal-safety guards, in ascending cost: live lane (#1193), live-use
        # (cwd/fd/exe), clean tree, idle-age. A True from live_use, a not-True from clean
        # (dirty OR unmeasurable), or an age below the floor => KEEP + journal.
        if why := gate.keep_reason(repo_root, path):
            logs.append("worktree-prune kept (%s): %s" % (why, path))
            continue
        if live_use_fn(path):
            logs.append("worktree-prune kept (in live use): %s" % path)
            continue
        if clean_fn(path) is not True:
            logs.append("worktree-prune kept (dirty/unmeasurable tree): %s" % path)
            continue
        age = age_fn(ref)
        if age is None or age < age_min_s:
            logs.append("worktree-prune kept (<%dh since last commit): %s"
                        % (age_min_s // 3600, path))
            continue
        if dry_run:
            logs.append("worktree-prune would remove (%s, branch kept): %s"
                        % (lane.get("state"), path))
            continue
        try:
            r = run(["git", "-C", repo_root, "worktree", "remove", path])
        except Exception as e:  # noqa: BLE001
            logs.append("worktree-prune kept (remove error %r): %s" % (e, path))
            continue
        if getattr(r, "returncode", 1) == 0:
            logs.append("worktree-prune removed (%s, branch %s kept): %s"
                        % (lane.get("state"), ref, path))
        else:
            logs.append("worktree-prune kept (remove rc=%s): %s"
                        % (getattr(r, "returncode", "?"), path))
    return logs
