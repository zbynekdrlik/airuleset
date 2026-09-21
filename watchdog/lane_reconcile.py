"""#844 — the POST-COMPACT lane RECONCILE rider.

WHY. The #844 bounded live-hold cap forces a `/compact` past the live-tasks veto
on a saturated `/autopilot-master` box; the step-0 experiment proved a lane's
commit + completion notification survive that. But the RESIDUAL case — a
notification genuinely lost (CC's own overflow auto-compact, or a queued
`/compact` that drains mid-flight) — must lose NOTHING: after ANY compaction the
main session may have dropped a lane's completion, so this rider REMINDS it to
integrate returned lanes from durable state (the branch + its LANE-RETURN comment,
#844 step 2), never from memory.

SHAPE — the exact sibling of `queue_arrival_recheck`/`u_freshness`: a job-20
keystroke rider that rides `goal.goal_lane_sweep`'s existing armed-pane loop (ZERO
new pane walk), owns its own `state["lane_reconcile"]` namespace, consults the
SHARED `nudge_gate` (per-kind floor + cross-kind total cap), and takes an INJECTED `reconcile_fetch(cwd)`
seam so run_once stays network-free in unit tests. It NEVER merges, NEVER relabels,
and imports NO notify — it is a keystroke into the armed session, never an owner
ping.

TRIGGER — keyed on OBSERVED compaction (the transcript's newest `isCompactSummary`
epoch, within a recency window), NOT the watchdog delivery ts: a forced compact
that QUEUES under an armed goal drains at an arbitrary LATER moment, so keying on
delivery would reconcile BEFORE the compaction happened and then dedup (the Fable
consult's #2 hazard). Deduped one attempt per observed compaction. gh/git error →
`reconcile_fetch` returns None → NO nudge (the doctrine `git worktree list` net,
#844 step 4, covers the crashed-lane case).

Module-import safety mirrors `nudge_gate.py`/`compact.py`: a top-level
`from watchdog import nudge_gate` is safe (nudge_gate imports nothing from the
package); everything else is reached lazily inside the function body.
"""
from watchdog import nudge_gate as _nudge_gate   # #797 shared cadence gate
from watchdog.queue_arrival_recheck import _budget_left  # #1041 shared sweep-budget seam

CATEGORY = "lane-reconcile"

# #1041 — the reconcile_fetch (a `git`-heavy read of returned worktree lanes,
# `_watchdog_reconcile_fetch`) self-bounds to BUDGET_S=40s / MAX_BRANCHES=6; skip it
# with `hold:budget` + UNTOUCHED state when fewer than that + margin of sweep budget
# remain, so it never runs the sweep into the unit's 120s TimeoutStartSec kill. The
# rider fires only on an OBSERVED compaction (rare), so a budget skip re-runs harmlessly.
RECONCILE_FETCH_MIN_BUDGET_S = 45

# Only react to a compaction observed within this window — a first-sweep-after-
# deploy read must not nudge for an ancient compaction, and a genuine post-compact
# reconcile is always fresh (the rider rides a ~60s sweep).
COMPACT_RECONCILE_WINDOW_S = 30 * 60

# Bounded swallow retry (mirrors the sibling riders' #714 MAX_SEND_FAILS): a
# persistently-swallowing NON-busy pane backs off rather than re-fetching forever.
MAX_SEND_FAILS = 3

# Orphan-reaper TTL for a per-sid rec whose session is gone (the #519/#531 shape).
LANE_RECONCILE_ORPHAN_TTL_S = 24 * 3600


def _entry_epoch(entry):
    """Epoch seconds of a parsed JSONL dict's ISO `timestamp`, or None."""
    if not isinstance(entry, dict):
        return None
    ts = entry.get("timestamp")
    if not ts or not isinstance(ts, str):
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# A compaction writes an `isCompactSummary: true` user summary entry (the SAME
# structural flag every sibling reader keys on). Read a BOUNDED byte-tail, never a
# whole-file `f.read()` — a saturated master's transcript reaches hundreds of MB
# (#764: cambox's 670 MB measured 1.17s whole vs 0.005s seek), and this runs every
# sweep for every armed pane. The tail is generous (4 MB / ~4000 entries) so a
# recent compaction stays findable even on a busy transcript that scrolls fast —
# addressing the "compaction scrolls out of a small window on a busy master" miss.
COMPACT_TAIL_BYTES = 4 * 1024 * 1024
COMPACT_TAIL_MAX_ENTRIES = 4000


def _last_compaction_epoch(tpath):
    """The epoch of the NEWEST `isCompactSummary` user entry in `tpath`'s BOUNDED
    byte-tail, or None (no compaction observed / unreadable). Structured signal,
    never a pane-render heuristic. Fail-safe None on any error (never raises)."""
    import watchdog
    newest = None
    try:
        entries = watchdog._read_jsonl_byte_tail(
            tpath, COMPACT_TAIL_BYTES, COMPACT_TAIL_MAX_ENTRIES)
    except Exception:
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "user" and entry.get("isCompactSummary") is True:
            ep = _entry_epoch(entry)
            if ep is not None and (newest is None or ep > newest):
                newest = ep
    return newest


# The #714 keystroke-rider guard set for a new rider is busy-gate + bounded retry
# + a NUDGE_MAX_CHARS cap: never type an unbounded blob into a live pane. The
# reconcile lists the returned branches but truncates to a bounded prefix + a
# "…and K more" tail so a big multi-lane burst stays a small keystroke.
NUDGE_MAX_CHARS = 700


def _nudge_text(branches):
    """The ONE reconcile nudge — lists the returned branches (a multi-lane
    completion burst is under-reported by a singular nudge, per the Fable
    consult), each with its ticket + topic (`issue-reference-context.md`),
    truncated to NUDGE_MAX_CHARS with an "…and K more" tail so a large burst is
    still a bounded keystroke (#714)."""
    n = len(branches)
    head = ("lane-reconcile: %d worktree lane%s returned during a compaction — "
            "integrate %s from durable state (the branch + its LANE-RETURN "
            "comment; the supervisor re-verifies before merging), never from "
            "memory: " % (n, "" if n == 1 else "s", "it" if n == 1 else "them"))
    shown = []
    used = len(head)
    for i, b in enumerate(branches):
        item = "%s (#%s, %s)" % (b[0], b[1], (b[2] or "")[:60])
        # reserve room for the "; …and K more" tail before committing this item.
        tail_reserve = len("; …and %d more" % (n - i)) if i < n else 0
        if shown and used + len("; ") + len(item) + tail_reserve > NUDGE_MAX_CHARS:
            break
        shown.append(item)
        used += (len("; ") if len(shown) > 1 else 0) + len(item)
    body = "; ".join(shown)
    more = n - len(shown)
    if more > 0:
        body += "; …and %d more (see git worktree list)" % more
    return head + body


def goal_lane_reconcile_recheck(now, run, lrecs, sid, cwd, pid, tpath, loc,
                                dry_run, handled, reconcile_fetch, state,
                                sleep_fn=None, captured=None,
                                batch_collect=None, budget_left_fn=None):
    """Audit ONE armed candidate pane after a compaction and, on a NEW observed
    compaction with returned worktree lanes, deliver ONE reconcile nudge. Called
    from `goal.goal_lane_sweep`'s armed-pane loop with the resolved pane context
    (ZERO new pane walk). Mutates `lrecs[sid]` (persisted by `state`); returns a
    list of decision log lines (#486 — every verdict logged). `dry_run` mutates
    no persistent state and sends nothing.

    `reconcile_fetch(cwd)` is the injected seam (network kept out of run_once unit
    tests, like `queue_fetch`): returns a list of `(branch, issue_num, title)`
    tuples for worktree branches ahead of the integration branch carrying a
    LANE-RETURN comment, or None when unmeasurable — None fails safe to NO nudge.

    `captured` (#714): the pane capture the caller already read — the BUSY-PANE
    gate; None (unwired/older caller) skips the gate.

    FULL-authority ONLY: only a gk/full box INTEGRATES worktree lanes (a reduced
    stream hands off). An unresolvable authority fails safe to skip."""
    logs = []
    import watchdog

    # A compaction must have been OBSERVED for this session, recently, and not
    # already reconciled — cheap, before authority/fetch. (The transcript read is
    # a small tail read, done every sweep like the sibling riders' own reads.)
    comp_ts = _last_compaction_epoch(tpath)
    if comp_ts is None:
        logs.append("lane-reconcile %s -> skip:no-compaction-observed" % loc)
        return logs
    if now - comp_ts > COMPACT_RECONCILE_WINDOW_S:
        logs.append("lane-reconcile %s -> skip:stale-compaction (%ds ago)"
                    % (loc, int(now - comp_ts)))
        return logs
    rec = lrecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    last = rec.get("last_reconcile_ts")
    if isinstance(last, (int, float)) and abs(last - comp_ts) < 1.0:
        logs.append("lane-reconcile %s -> skip:already-reconciled "
                    "(this compaction)" % loc)
        return logs

    # FULL-authority gate (only a gk/full box integrates worktree lanes), cheap,
    # before any fetch. Same gate as release_gap/queue_arrival (#616 MIRROR).
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception as e:
        logs.append("lane-reconcile %s -> skip:authority-unresolved (%r)"
                    % (loc, e))
        return logs
    if authority != "full":
        logs.append("lane-reconcile %s -> skip:not-full-authority (%s)"
                    % (loc, authority))
        return logs

    # #923 BATCH MODE: common delivery guards handled once by the caller.
    if batch_collect is None:
        if not watchdog.nudges_enabled(CATEGORY):   # #1023 per-kind switch
            logs.append("lane-reconcile %s -> skip:kind-off (%s)" % (loc, CATEGORY))
            return logs
        from watchdog import compact as _compact
        if _compact.pending_compact_hold(sid, now):   # #848 bounded
            logs.append("lane-reconcile %s -> hold:compact-pending "
                        "(a new /compact is pending; reconcile after it delivers)"
                        % loc)
            return logs
        if handled is not None and sid in handled:
            logs.append("lane-reconcile %s -> skip:already-handled "
                        "(another sweep job typed this pane; retry next sweep)" % loc)
            return logs
        from watchdog import ops_wait_recheck as _ops
        # #1023: idle-pane only — a busy Waiting pane always defers (aged override gone)
        if _ops._pane_busy_waiting(captured):
            logs.append("lane-reconcile %s -> hold:busy (waiting on background "
                        "agents — retry next idle tick)" % loc)
            return logs
        if not _nudge_gate.gate_ok(state, sid, CATEGORY, now):
            logs.append("lane-reconcile %s -> %s; retry next sweep"
                        % (loc, _nudge_gate.floor_hold_reason(
                            state, sid, CATEGORY, now)))
            return logs

    # #1041 — SWEEP-BUDGET guard: the reconcile_fetch is a git-heavy read (its own
    # 40s/6-branch budget); starting it with too little sweep budget left runs the
    # sweep into the unit's TimeoutStartSec=2min kill. Skip with `hold:budget` +
    # UNTOUCHED state (no fetch, no dedup advance) — the compaction is re-observed
    # next, less-loaded sweep. None (unwired/legacy) => no guard.
    _left = _budget_left(budget_left_fn)
    if _left is not None and _left < RECONCILE_FETCH_MIN_BUDGET_S:
        logs.append("lane-reconcile %s -> hold:budget (%ds left, need >=%ds; "
                    "state untouched, retry next sweep)"
                    % (loc, int(_left), RECONCILE_FETCH_MIN_BUDGET_S))
        return logs

    # This compaction is now the one we ACT on. Fetch the returned lanes.
    try:
        branches = reconcile_fetch(cwd)
    except Exception as e:
        # A fetch that RAISES is unmeasurable — one attempt per compaction, do
        # NOT nudge (the doctrine net covers it), advance the dedup anchor so a
        # persistent error never re-fetches every sweep for the SAME compaction.
        if not dry_run:
            rec["last_reconcile_ts"] = comp_ts
            rec["lts"] = now
            lrecs[sid] = rec
        logs.append("lane-reconcile %s -> skip:fetch-error (%r) — no nudge"
                    % (loc, e))
        return logs
    if branches is None:
        if not dry_run:
            rec["last_reconcile_ts"] = comp_ts
            rec["lts"] = now
            lrecs[sid] = rec
        logs.append("lane-reconcile %s -> skip:fetch-unmeasurable — no nudge"
                    % loc)
        return logs
    if not branches:
        if not dry_run:
            rec["last_reconcile_ts"] = comp_ts
            rec["lts"] = now
            lrecs[sid] = rec
        logs.append("lane-reconcile %s -> skip:nothing-to-reconcile "
                    "(no returned lanes ahead of integration)" % loc)
        return logs

    if dry_run:
        logs.append("lane-reconcile %s -> WOULD-NUDGE (%d lane(s): %s)"
                    % (loc, len(branches),
                       ", ".join(str(b[0]) for b in branches)))
        return logs

    text = _nudge_text(branches)
    # #923 BATCH COLLECT: contribute text, defer delivery+state to caller.
    if batch_collect is not None:
        def _on_deliver(_r=rec, _lr=lrecs, _s=sid, _n=now, _h=handled,
                        _st=state, _p=pid, _ct=comp_ts):
            watchdog._janitor_clear_watch(_st, _p)
            _r["last_reconcile_ts"] = _ct
            _r["send_fails"] = 0
            _r["lts"] = _n
            _lr[_s] = _r
            if _h is not None:
                _h.add(_s)
        batch_collect.append((CATEGORY, text, _on_deliver))
        logs.append("lane-reconcile %s -> batch-collected (%d lane(s))"
                    % (loc, len(branches)))
        return logs
    watchdog._janitor_mark_watch(state, pid, now)
    send_out = {}
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out, nudge=CATEGORY,
                                state=state, now=now)  # #1022 wedge / #1092 pane budget
    delivered = ok or bool(send_out.get("delivered_unconfirmed"))
    if not delivered:
        # #1092 (c) F6 — a per-pane budget REFUSAL typed nothing: not a swallow,
        # never book a give-up; retry once the budget frees.
        if send_out.get("pane_budget_held"):
            logs.append("lane-reconcile %s -> held (pane-budget, not typed; "
                        "retry next sweep)" % loc)
            return logs
        # A genuine swallow leaves the dedup anchor unadvanced -> retries next
        # sweep, bounded by MAX_SEND_FAILS so a persistently-swallowing NON-busy
        # pane backs off (accepts the compaction) rather than re-fetching forever.
        fails = rec.get("send_fails")
        fails = (fails + 1) if isinstance(fails, int) and not isinstance(fails, bool) else 1
        rec["send_fails"] = fails
        rec["lts"] = now
        if fails >= MAX_SEND_FAILS:
            rec["last_reconcile_ts"] = comp_ts   # give up on this compaction
            logs.append("lane-reconcile %s -> swallowed x%d — backing off "
                        "(accepting this compaction)" % (loc, fails))
        else:
            logs.append("lane-reconcile %s -> swallowed x%d — retry next sweep"
                        % (loc, fails))
        lrecs[sid] = rec
        return logs
    watchdog._janitor_clear_watch(state, pid)
    rec["last_reconcile_ts"] = comp_ts
    rec["send_fails"] = 0
    rec["lts"] = now
    lrecs[sid] = rec
    _nudge_gate.mark_sent(state, sid, CATEGORY, now)
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("lane-reconcile nudge %s -> %d lane(s): %s%s"
                % (loc, len(branches), ", ".join(str(b[0]) for b in branches),
                   note))
    return logs


def _prune_lane_reconcile_orphans(lrecs, visited_sids, now,
                                  ttl_s=LANE_RECONCILE_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["lane_reconcile"]`. Reap ONLY
    when BOTH: (1) the sid was NOT a live candidate pane THIS sweep
    (`visited_sids`), AND (2) it is malformed OR its `lts` age anchor is older than
    `ttl_s`. The visited gate is PRIMARY. A FUTURE `lts` (clock skew) is kept (the
    safe direction, #519). Never raises. Faithful mirror of the sibling reapers."""
    if not isinstance(lrecs, dict):
        return
    for sid in [k for k, v in list(lrecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        lrecs.pop(sid, None)


# ---------------------------------------------------------------------------
# #1103 — the finished-worktree HYGIENE SWEEP (a rung in the lane-reconcile job
# family, no new run_once job). Removes a lane worktree that is FINISHED for
# this box — merged into its target, or handed off with no live process — once
# it is clean + old, so the `git worktree list` (which `cli_lane_overlap`
# derives liveness from) stops lying. The BRANCH REF is always KEPT (the wip
# backup + the hand-off live on origin); a dirty or process-holding worktree is
# left and journaled. Wired from run_once's existing `_job_goal_lane_sweep`
# closure, gated on the already-wired `repo_roots` seam — so goal.py is not
# touched and the run_once registry is unchanged.
# ---------------------------------------------------------------------------

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
    are dirty, in live use or too young. The branch ref is ALWAYS kept
    (``git worktree remove`` never ``--force``). Returns one decision-log line
    per removed/kept candidate; [] when nothing qualifies or the cadence gate
    holds. Never raises — a hygiene sweep must never crash the watchdog sweep.

    The DISCRIMINATOR (which worktrees are candidates) is the SAME liveness
    derivation the receipt uses (``cli_lane_overlap.classify_lanes``, #367
    one-derivation) — a lane counted live by the overlap check is never a
    candidate. The removal-safety LEAF plumbing is REUSED from the hardened
    worktree sweeper (``cli_worktree_sweep``): ``_worktree_is_clean`` (dirty =>
    keep) and ``_worktree_in_live_use`` (cwd/fd/exe => keep — stronger than the
    classifier's cwd-only signal). The age guard is COMMIT-age
    (``_lane_commit_age_s``), deliberately NOT ``_worktree_recency_age_s`` — the
    clean-check's own ``git status`` refreshes the index mtime the recency helper
    reads (see WORKTREE_PRUNE_MIN_AGE_S). ``clean_fn``/``live_use_fn``/``age_fn``
    are test seams; each None => the default (framework helper or commit-age)."""
    import os
    import time as _time
    import cli_lane_overlap as lo
    import cli_worktree_sweep as ws
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
        # removal-safety guards, in ascending cost: live-use (cwd/fd/exe), then
        # clean tree, then idle-age. A True from live_use, a not-True from clean
        # (dirty OR unmeasurable), or an age below the floor => KEEP + journal.
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
