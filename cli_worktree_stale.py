"""The stale-worktree sweep (#345/#348/#513), split out of cli_worktree_sweep (#1194).

Moved VERBATIM. Holds the cadence-gated install-time sweep and its CLI:
- the #348 locked-dead classification (lock-reason pid parsing, the
  /proc/<pid>/stat start-time dead check, lock age, `_classify_locked_worktree`),
  whose only caller is `discover_stale_worktrees`;
- `discover_stale_worktrees`: 0-ahead registered worktrees, with the #1193
  live-lane gate;
- `discover_salvage_worktrees`: the read-only #513 report;
- `sweep_stale_worktrees`: removal, including the #348 orphan branches from
  `cli_worktree_orphans`;
- `cmd_sweep_worktrees`: `airuleset.py sweep-worktrees`.

Its test seams (`sweep_stale_worktrees`, `discover_stale_worktrees`,
`discover_salvage_worktrees`, `_worktree_in_live_use`) are resolved in THIS
module's namespace: patch `cli_worktree_stale.X`, not the facade. Stdlib only
at module level.
"""

import json
import os
import re
import sys
from pathlib import Path

from cli_worktree_common import (
    CLAUDE_DIR,
    STALE_LOCKED_DEAD_MIN_AGE_S,
    STALE_WORKTREE_IDLE_MIN_AGE_S,
    _STALE_WORKTREE_PROTECTED_BRANCHES,
    _worktree_admin_dir,
    _worktree_env_age_s,
    _worktree_git,
    _worktree_in_live_use,
    _worktree_is_clean,
    _worktree_porcelain_entries,
    _worktree_recency_age_s,
    _worktree_sweep_base_branch,
)
from cli_worktree_orphans import (
    discover_orphaned_worktree_branches,
)


# --- Stale worktree sweep (#345) --------------------------------------------
# The harness auto-removes a worker's `.claude/worktrees/agent-<id>` ONLY on
# a NORMAL agent exit -- a worker killed by an API error / session limit
# leaves the worktree registered (locked forever, so even `git branch -D`
# refuses) with nothing that ever deletes its branch once someone removes
# the directory by hand. A round's own close-out
# (`skills/autopilot/SKILL.md` ROUND INTEGRATION step 5) only ever cleans
# branches it actually merged -- never a sibling round's dead leftovers.
# Dead workers therefore leak one worktree + one branch each, unboundedly,
# fleet-wide. This reuses #315's own `purge_stale_tier0_targets` shape
# EXACTLY: a plain, cadence-gated function (its own state file, never the
# 60s watchdog timer -- the FREEZE forbids a new job, and rate-limiting a
# plain function call needs none) wired as a non-fatal step inside
# `cmd_install()`, plus a manual/testable CLI entry point.

STALE_WORKTREE_LOG_PATH = CLAUDE_DIR / "worktree-sweep.log"
STALE_WORKTREE_STATE_PATH = CLAUDE_DIR / "worktree-sweep-state.json"
STALE_WORKTREE_MIN_INTERVAL_S = 6 * 3600      # env AIRULESET_WORKTREE_SWEEP_INTERVAL_S
# A worktree can carry several GB of build artefacts (camera-box/songplayer/
# spinbike measured 4-10GB each under #315's own target-purge finding) --
# `git worktree remove` walks and deletes that whole tree, which can genuinely
# take longer than the lightweight git-plumbing default (_worktree_git's own
# 15s) on a busy, I/O-contended box. A short timeout here would make a large,
# perfectly-removable worktree read as "refused (dirty/in use)" every sweep,
# never actually reclaiming the disk it exists to reclaim.
STALE_WORKTREE_REMOVE_TIMEOUT_S = 120


_WORKTREE_LOCK_PID_RX = re.compile(r"claude agent \S+ \(pid (\d+) start (\d+)\)")


def _worktree_lock_pid(reason):
    """Extract `(pid, start)` from a harness-style lock reason string --
    STRICTLY the harness's OWN observed shape, in FULL, via
    `re.fullmatch`: "claude agent <agent-id> (pid <N> start <M>)"
    (verified live against a real worktree lock on this box), `start`
    matching `/proc/<pid>/stat` field 22 byte-for-byte. `(None, None)`
    for ANYTHING else -- including a human-authored reason that merely
    MENTIONS a "(pid N)"-shaped substring somewhere in its own text.

    #348 adversarial-review MAJOR-1 (TRIGGERED live, no injection): the
    original `re.search`-anywhere form with an OPTIONAL `start` accepted
    a human's own `--reason "debugging crash (pid 999999) - DO NOT
    REMOVE"` (unlocked + swept) and a reason ENDING "... (pid 1 start
    999999999)" (pid 1 genuinely alive, wrong starttime, still read as
    "confirmed dead"). Anchoring the WHOLE reason to the harness's own
    literal "claude agent <id> " prefix -- and requiring `start` (never
    optional; the harness always records it) -- rejects both: neither
    trigger starts with "claude agent ", so neither can ever match,
    regardless of where inside the string a pid-shaped substring sits.
    """
    if not reason:
        return (None, None)
    match = _WORKTREE_LOCK_PID_RX.fullmatch(reason.strip())
    if not match:
        return (None, None)
    return (int(match.group(1)), int(match.group(2)))


def _proc_stat_text(pid, proc_root=None):
    """Real `/proc/<pid>/stat` reader -- None when the pid does not exist
    (already exited) or is unreadable for any other reason. `proc_root`
    (default `/proc`) exists only so a test can point this at a fake
    directory tree -- production code always uses the real filesystem.

    `errors="replace"` is deliberate, not cosmetic (#348 adversarial-
    review MINOR-2, TRIGGERED live against a real forked process with a
    `comm` set to invalid UTF-8 via `prctl(PR_SET_NAME, ...)` -- legal,
    and NOT rare on a box running arbitrary tooling): a bare
    `Path.read_text()` raises `UnicodeDecodeError`, uncaught by the
    `except OSError` here, and that raise propagates all the way out of
    `sweep_stale_worktrees`'s blanket discovery-error handler -- silently
    disabling BOTH new #348 leak categories fleet-wide the instant any
    locked worktree's recorded pid happens to be occupied by such a
    process. Every field this module ever reads out of a stat line
    (state..starttime) is plain ASCII and sits AFTER the `comm` field's
    own closing paren, so replacing invalid bytes inside `comm` with
    U+FFFD can never corrupt the numeric fields this code actually
    parses -- it must NOT return `None` on decode failure either: that
    would read as "no such process" to `_pid_is_dead`, i.e. a manufactured
    FALSE POSITIVE for a process that is very much alive."""
    proc_root = proc_root or Path("/proc")
    try:
        return (proc_root / str(pid) / "stat").read_text(errors="replace")
    except OSError:
        return None


def _pid_is_dead(pid, start=None, stat_reader=None):
    """True ONLY when POSITIVELY confirmed the exact `(pid, start)`
    session no longer exists -- False when it IS alive, None when this
    cannot be determined at all. Neither `False` nor `None` may ever be
    treated as "dead" by a caller -- both mean "do not touch this".

    `start` is `/proc/<pid>/stat` field 22 (starttime, clock ticks since
    boot) -- cross-checking it closes the PID-REUSE window: if `pid` is
    now occupied by a DIFFERENT process (a different starttime), the
    ORIGINAL (pid, start) session is genuinely gone, so this correctly
    still returns True even though the bare pid number is "in use" again.
    Without `start` (a lock reason with no recorded starttime) a live pid
    is reported alive, per the "never guess dead" contract -- there is
    nothing to disambiguate a reused pid from the original session.
    """
    stat_reader = stat_reader or _proc_stat_text
    if not isinstance(pid, int) or pid <= 0:
        return None
    raw = stat_reader(pid)
    if raw is None:
        return True   # no such process at all -- positively confirmed gone
    if start is None:
        return False  # alive; nothing to cross-check -- never guessed dead
    try:
        # comm (field 2) can itself contain spaces/parens -- split on the
        # LAST ")" to skip past it safely, exactly like every real /proc
        # parser must. state=idx0, ppid=idx1, ... starttime=idx19 of what
        # remains (field 22 overall, minus the pid+comm fields already cut).
        after_comm = raw.rsplit(")", 1)[1]
        fields = after_comm.split()
        proc_start = int(fields[19])
    except (IndexError, ValueError):
        return None   # malformed/unexpected /proc shape -- never guessed
    return proc_start != start


def _worktree_lock_age_s(admin_dir, now):
    """Seconds since a worktree's OWN lock was created -- the admin dir's
    `locked` marker-file mtime (the harness writes this file ONCE, at
    dispatch time, and never touches it again). None when unmeasurable
    (no admin dir, no `locked` file, unreadable, or a future mtime from
    clock skew) -- unmeasurable is never "old enough"."""
    if admin_dir is None:
        return None
    try:
        mtime = (Path(admin_dir) / "locked").stat().st_mtime
    except OSError:
        return None
    age = now - mtime
    return age if age >= 0 else None


def _classify_locked_worktree(root, path, branch, lock_reason, git_run, now,
                              pid_is_dead=None, min_age_s=None):
    """A LOCKED worktree is normally NEVER a candidate (#345's own
    NON-NEGOTIABLE #1) -- this is the ONE deliberate, narrowly-scoped
    exception (#348): promote it to a genuine candidate (`kind:
    "locked_dead"`) only when EVERY ONE of five independent signals
    agrees, in order, each refusing (never guessing) on its own failure:

      1. `lock_reason` parses to the harness's own `(pid N start M)`
         shape -- an unparseable/manual lock refuses outright;
      2. that EXACT (pid, start) is POSITIVELY confirmed dead
         (`_pid_is_dead` -- never merely "not currently found");
      3. the lock itself is at least `min_age_s` old (several days by
         default) -- a pure time buffer, not a substitute for #2;
      4. the branch carries ZERO commits ahead of the repo's own base --
         the identical fully-qualified rev-list check every other
         candidate gets; real unmerged work is never touched;
      5. the working tree is provably clean (`git status --porcelain`
         empty) -- a dead worker's own uncommitted edits are never
         silently discarded.

    Residual risk (not closed here, stated in the ticket's design
    comment): the harness's lock always records the MAIN SESSION's pid,
    never the individual dispatched worker's -- so this can only ever
    detect a worktree whose entire owning session has exited. A worker
    whose own task finished or died while its main session stays alive
    (busy on other tickets) is unreachable by signal 2 and is simply
    never swept -- a FALSE NEGATIVE only, never a false positive: a
    still-alive (or undeterminable) pid always refuses.

    A SECOND, narrower residual (#348 adversarial-review MINOR-4,
    confirmed): a worktree that is BOTH locked (dead session, otherwise
    reclaimable) AND has had its directory removed by hand is
    permanently unreachable by EITHER mechanism at once -- signal 5
    (`git status --porcelain`) cannot run against a missing directory
    and refuses forever, while `discover_orphaned_worktree_branches`
    never sees the branch either (it is still "registered" by the
    surviving locked admin entry). False negative only, never fixed
    here -- closing it would need a directory-existence-aware variant of
    signal 5, out of this ticket's own named scope.
    """
    pid_is_dead = pid_is_dead or _pid_is_dead
    min_age_s = (_worktree_env_age_s("AIRULESET_WORKTREE_LOCKED_DEAD_MIN_AGE_S",
                                     STALE_LOCKED_DEAD_MIN_AGE_S)
                if min_age_s is None else min_age_s)
    row = {"path": path, "branch": branch, "repo": root, "reason": None,
          "kind": "locked_dead", "lock_reason": lock_reason}
    pid, start = _worktree_lock_pid(lock_reason)
    if pid is None:
        row["reason"] = "locked, no parseable session pid -- never guessed at"
        return row
    dead = pid_is_dead(pid, start)
    if dead is not True:
        row["reason"] = ("locked (active worker)" if dead is False
                         else "locked, session liveness undeterminable")
        return row
    admin_dir = _worktree_admin_dir(root, path)
    age = _worktree_lock_age_s(admin_dir, now)
    if age is None or age < min_age_s:
        row["reason"] = ("locked, dead session but lock is too recent to "
                         "reclaim (< %d d) or age unmeasurable" %
                         (min_age_s / 86400))
        return row
    if branch is None:
        row["reason"] = "locked, dead session, detached HEAD -- never guessed at"
        return row
    if branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
        row["reason"] = "protected branch name (%s)" % branch
        return row
    base = _worktree_sweep_base_branch(root, git_run=git_run)
    if not base:
        row["reason"] = "no dev/main/master to compare against"
        return row
    ahead = git_run(["rev-list", "--count",
                    "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
    if ahead is None or ahead.strip() != "0":
        row["reason"] = "locked, dead session, but has unmerged work -- never touched"
        return row
    clean = _worktree_is_clean(path, git_run)
    if clean is not True:
        row["reason"] = "locked, dead session, but tree not provably clean -- never touched"
        return row
    row["base"] = base
    return row     # reason stays None -- genuine candidate


def discover_stale_worktrees(home=None, git_run=None, now=None, pid_is_dead=None, live_gate=None):
    """Every worktree, across every managed repo under `home`, that is
    SAFE to reclaim -- a list of dicts {"path", "branch", "repo",
    "reason", "base", "kind"}. `reason` is `None` for a genuine candidate,
    else WHY it was excluded (a `--dry-run` report needs both). Pure
    discovery+classification -- `sweep_stale_worktrees` is the only
    function that ever mutates anything.

    Safety criteria (#345, NON-NEGOTIABLE):
      - never worktree-list entry 0 (the primary checkout);
      - never a branch literally named main/dev/master, wherever found;
      - never a LOCKED worktree UNLESS `_classify_locked_worktree`'s own
        5-signal dead-session chain (#348) positively confirms both the
        owning session is gone AND the branch/tree carry no real work --
        see that function's own docstring for the full criteria and the
        residual risk it explicitly does NOT close;
      - only a branch with ZERO commits ahead of `_worktree_sweep_base_branch`
        -- a branch carrying real, unmerged work is NEVER a candidate
        (salvage-before-discarding.md).
    Deliberately NOT filtered by branch-NAME shape (e.g.
    `worktree-agent-*`) -- the ticket's own evidence names five stale
    worktrees from the OLD custom-naming convention that predates
    `isolation: "worktree"` becoming the default; the objective safety
    criteria above are branch-name-agnostic and equally safe regardless
    of naming convention.
    """
    git_run = git_run or _worktree_git
    import time as _time
    now = _time.time() if now is None else now
    out = []
    import airuleset
    from cli_lane_live_gate import LiveLaneGate
    gate = live_gate or LiveLaneGate(home=home, now=now)
    for root in airuleset._checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue          # a worktree/submodule itself -- never a primary repo
        git_run(["worktree", "prune"], root)   # dangling admin-only entries -- always safe
        entries = _worktree_porcelain_entries(root, git_run=git_run)
        if len(entries) <= 1:
            continue          # nothing but the primary checkout
        base = None
        base_resolved = False
        for i, e in enumerate(entries):
            if i == 0:
                continue       # the primary worktree -- never a candidate
            branch = e.get("branch")
            row = {"path": e.get("path"), "branch": branch, "repo": root, "reason": None,
                  "kind": "worktree"}
            if branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
                row["reason"] = "protected branch name (%s)" % branch
                out.append(row)
                continue
            if e.get("locked"):     # #1193: a resumed lane can sit behind a dead-pid lock
                row = _classify_locked_worktree(root, e.get("path"), branch, e.get("lock_reason"),
                                                git_run, now, pid_is_dead=pid_is_dead)
                row["reason"] = row["reason"] or gate.keep_reason(root, row["path"])
                out.append(row)
                continue
            if branch is None:
                row["reason"] = "detached HEAD -- never guessed at"
                out.append(row)
                continue
            if not base_resolved:
                base = _worktree_sweep_base_branch(root, git_run=git_run)
                base_resolved = True
            if not base:
                row["reason"] = "no dev/main/master to compare against"
                out.append(row)
                continue
            # #345 adversarial-review MAJOR-2 (confirmed data loss): a bare
            # short name here silently resolves to a same-named TAG ahead of
            # the branch (refs/tags/ before refs/heads/ in gitrevisions ref
            # resolution order) with only a stderr warning, rc 0 -- a branch
            # carrying real commits then reads as "0 ahead" and is deleted.
            # Fully-qualify both sides so this can only ever mean the branch.
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
            row["base"] = base  # reason None = genuine candidate; #1193 a live lane is kept
            row["reason"] = gate.keep_reason(root, row["path"])
            out.append(row)
    return out


def discover_salvage_worktrees(home=None, git_run=None, now=None):
    """Read-only (#513, constraint #2). Every registered worktree across
    managed repos under `home` that the SAFE sweep can NEVER reclaim
    because it carries real work -- commits ahead of base OR an
    uncommitted (dirty) tree -- AND is genuinely ABANDONED (idle beyond the
    live-worker window AND not in live use). These are SALVAGE material:
    disk the safe sweep can never free, needing a supervisor/human decision
    (integrate, park the branch, or discard) -- NEVER auto-removed by any
    sweep. A worktree still being actively worked (real work but recently
    touched, or in live use) is EXCLUDED -- that is in-flight work.

    Rows: {path, branch, repo, ahead, dirty, size, age_s, wip_backup} --
    `ahead` commits ahead of base, `dirty` porcelain lines, `size` bytes,
    `age_s` idle age, `wip_backup` True/False/None (a
    `refs/autopilot-wip/<branch>` origin backup exists / does not / unknown)."""
    git_run = git_run or _worktree_git
    import time as _time
    now = _time.time() if now is None else now
    idle_min = _worktree_env_age_s("AIRULESET_WORKTREE_IDLE_MIN_AGE_S",
                                   STALE_WORKTREE_IDLE_MIN_AGE_S)
    out = []
    import airuleset
    for root in airuleset._checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue
        entries = _worktree_porcelain_entries(root, git_run=git_run)
        base = _worktree_sweep_base_branch(root, git_run=git_run)
        for i, e in enumerate(entries):
            if i == 0:
                continue                     # primary checkout -- never salvage
            branch = e.get("branch")
            path = e.get("path")
            if branch in _STALE_WORKTREE_PROTECTED_BRANCHES or not path:
                continue
            ahead = 0
            if branch is not None and base:
                a = git_run(["rev-list", "--count",
                            "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
                if a is not None and a.strip().isdigit():
                    ahead = int(a.strip())
            clean = _worktree_is_clean(path, git_run)
            dirty = 0
            if clean is False:
                st = git_run(["status", "--porcelain"], path)
                dirty = len([ln for ln in (st or "").splitlines() if ln.strip()])
            if ahead <= 0 and dirty <= 0:
                continue                     # no real work -- the safe sweep handles it
            rec = _worktree_recency_age_s(root, path, now)
            if rec is None or rec <= idle_min or _worktree_in_live_use(path):
                continue                     # still active / in-flight -- not salvage
            wip_backup = None
            ls = git_run(["ls-remote", "origin", "refs/autopilot-wip/%s" % branch], root)
            if ls is not None:
                wip_backup = bool(ls.strip())
            size = None
            try:
                from cli_target_purge import _dir_stats
                size = _dir_stats(path)[0]
            except Exception as ex:          # noqa: BLE001 -- size is best-effort
                print("  worktree-salvage: size unmeasurable for %s: %s"
                      % (path, ex), file=sys.stderr)
            out.append({"path": path, "branch": branch, "repo": root,
                        "ahead": ahead, "dirty": dirty, "size": size,
                        "age_s": rec, "wip_backup": wip_backup})
    return out


def _log_stale_worktree_results(results, log_path, now, dry_run: bool):
    import time as _time
    lines = []
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    for r in results:
        if r.get("path") is None:
            lines.append("%s ERROR %s" % (ts, r.get("reason", "")))
            continue
        if dry_run:
            tag = "WOULD-REMOVE" if not r.get("reason") or "dry" in r.get("reason", "") else "SKIP"
        else:
            tag = "REMOVED" if r.get("removed") else "SKIP"
        lines.append("%s %s %s branch=%s repo=%s -- %s" % (
            ts, tag, r.get("path"), r.get("branch"), r.get("repo"), r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  worktree-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_stale_worktrees(home=None, dry_run: bool = False, now=None, log_path=None,
                          state_path=None, force: bool = False, git_run=None,
                          candidates=None, pid_is_dead=None):
    """Reclaim every stale worktree `discover_stale_worktrees` classifies
    as a genuine candidate (`reason is None`) -- `git worktree remove
    <path>` NEVER passed `--force` (a dirty/untracked-file tree makes git
    itself refuse; that refusal is reported, never overridden), and only
    once THAT succeeds is the branch deleted via `git branch -D <branch>`.

    `-D` (force) here is safe and deliberate, not the same `--force` the
    ticket forbids on `worktree remove`: `discover_stale_worktrees`
    already independently proved zero commits ahead of the resolved
    base via `git rev-list --count` -- a MORE precise, base-aware check
    than `-d`'s own "merged into whatever HEAD the primary checkout
    happens to have" heuristic, which would depend on an unrelated
    coincidence (what ref the primary checkout is on at sweep time). The
    worktree directory is already gone by the time this runs, so nothing
    can add a new commit to the branch in between.

    Cadence-gated via its own state file (`STALE_WORKTREE_STATE_PATH`)
    mirroring #315's `purge_stale_tier0_targets` exactly -- never leans
    on the 60s watchdog timer (FREEZE: no new job). `force=True` (the
    CLI's own manual invocation) or `dry_run=True` always bypasses it.

    Known, deliberate residual (#345 adversarial-review THEORETICAL-2):
    `git worktree remove` reports a worktree holding ONLY gitignored files
    (a stray `.env`, a `target/` build dir) as clean and removes it, taking
    those files with it -- this is the intended disk-reclaim behaviour and
    matches git's own definition of "safe" (it still correctly REFUSES on
    any untracked, non-ignored file). A per-worktree gitignored SECRET
    would be lost this way; a dead worker's own worktree is never the
    source of truth for one.
    """
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else STALE_WORKTREE_LOG_PATH
    state_path = Path(state_path) if state_path else STALE_WORKTREE_STATE_PATH
    git_run = git_run or _worktree_git
    from cli_reclaim_guard import refuse_real_paths_under_pytest as _refuse  # #1195
    _refuse("sweep_stale_worktrees", dry_run, log_path=log_path, state_path=state_path,
            home=None if candidates is not None else (home or os.path.expanduser("~")))

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = STALE_WORKTREE_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_WORKTREE_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = STALE_WORKTREE_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_stale_worktrees(home, git_run=git_run, now=now,
                                                  pid_is_dead=pid_is_dead)
            # #348 -- the two extra leak shapes #345's own registered-
            # worktree-only scan cannot see (a hand-removed directory's
            # orphaned branch, and a locked worktree whose owning session
            # has exited). Both share this SAME discovery failure handler:
            # if either raises, nothing from EITHER source is trusted --
            # a partial discovery is not safer than none at all.
            candidates = candidates + discover_orphaned_worktree_branches(
                home, git_run=git_run, now=now)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"path": None, "removed": False,
                            "reason": "discovery error: %s" % e})

    for c in candidates:
        entry = dict(c)
        entry["removed"] = False
        kind = c.get("kind", "worktree")
        if c.get("reason"):
            results.append(entry)
            continue
        # #513 LIVE-WORKER guard -- applied to a registered worktree (kind
        # "worktree") in BOTH dry-run and live, so the two agree. NOT applied
        # to `orphan_branch` (no checkout to protect; already ref-age-gated)
        # or `locked_dead` (already passed the #348 dead-session + lock-age +
        # clean chain). A recently-active OR in-live-use worktree is kept --
        # this can only ever turn a candidate into a skip, never the reverse.
        if kind == "worktree":
            idle_min = _worktree_env_age_s("AIRULESET_WORKTREE_IDLE_MIN_AGE_S",
                                           STALE_WORKTREE_IDLE_MIN_AGE_S)
            rec = _worktree_recency_age_s(c.get("repo"), c.get("path"), now)
            if rec is not None and rec <= idle_min:
                entry["reason"] = ("active within %.1fh (idle threshold %.1fh) "
                                   "-- kept (live-worker guard)"
                                   % (rec / 3600.0, idle_min / 3600.0))
                results.append(entry)
                continue
            if _worktree_in_live_use(c.get("path")):
                entry["reason"] = "in live use (process rooted in tree) -- kept (live-worker guard)"
                results.append(entry)
                continue
        if dry_run:
            entry["reason"] = "would remove (dry-run)"
            results.append(entry)
            continue

        if kind == "orphan_branch":
            # No worktree directory at all -- straight to the branch, with
            # the SAME TOCTOU re-check every other branch delete gets
            # (salvage-before-discarding-work.md): something could have
            # started using this branch again between discovery and now.
            base = c.get("base")
            still_zero = True
            if base:
                recheck = git_run(["rev-list", "--count",
                                  "refs/heads/%s..refs/heads/%s" % (base, c["branch"])],
                                 c["repo"])
                still_zero = recheck is not None and recheck.strip() == "0"
            if not still_zero:
                entry["reason"] = "branch now carries new commits -- left in place"
                results.append(entry)
                continue
            bd = git_run(["branch", "-D", c["branch"]], c["repo"])
            entry["removed"] = bd is not None
            entry["branch_deleted"] = bd is not None
            entry["reason"] = "removed" if bd is not None else "branch delete refused -- left in place"
            results.append(entry)
            continue

        if kind == "locked_dead":
            # #348 -- a lock created by a now-provably-dead session must be
            # released before `worktree remove` can touch it at all; git
            # itself refuses to remove a locked worktree without --force,
            # which the NON-NEGOTIABLE safety core forbids passing.
            unlocked = git_run(["worktree", "unlock", c["path"]], c["repo"])
            if unlocked is None:
                entry["reason"] = "unlock refused -- left in place"
                results.append(entry)
                continue
            # falls through to the SAME remove+branch-delete flow below,
            # identical to a plain "worktree" candidate from here on.

        rc = git_run(["worktree", "remove", c["path"]], c["repo"],
                     timeout=STALE_WORKTREE_REMOVE_TIMEOUT_S)
        if rc is None:
            if kind == "locked_dead":
                # #348 adversarial-review MINOR-1 (TRIGGERED live): the
                # unlock above already succeeded -- if remove now refuses
                # (a dirty file raced in between classification and this
                # candidate's own turn, a permissions blip, a timeout),
                # leaving the worktree UNLOCKED with its forensic pid/
                # start reason gone forever would silently strip a real
                # protection, and the very next ORDINARY #345 sweep would
                # finish the job with NONE of this function's 5 safety
                # checks. Best-effort restore the ORIGINAL lock+reason;
                # the outcome is not re-verified further here -- a failed
                # re-lock just means the next sweep re-discovers this
                # worktree with an unparseable/no reason and refuses
                # again on that basis, never worse than today's state.
                relock = ["worktree", "lock", c["path"]]
                if c.get("lock_reason"):
                    relock += ["--reason", c["lock_reason"]]
                git_run(relock, c["repo"])
            entry["reason"] = "worktree remove refused (dirty tree, in use, or timed out) -- left in place"
            results.append(entry)
            continue
        entry["removed"] = True
        entry["reason"] = "removed"
        # Re-verify 0-ahead immediately before deleting the branch -- closes the
        # window between discovery's own ahead-count read and THIS candidate's
        # turn in a (possibly long) candidate list, during which something could
        # have added a genuine commit to the branch elsewhere. Mirrors #315's own
        # adversarial-review finding 2 (re-check right before the destructive
        # step, not just at discovery time) -- salvage-before-discarding-work.md.
        base = c.get("base")
        still_zero = True
        if base:
            # #345 adversarial-review MAJOR-2: fully-qualify both sides here
            # too -- the same ambiguous-short-name-resolves-to-a-tag hazard
            # applies to this re-check exactly as it does to discovery's own.
            recheck = git_run(["rev-list", "--count",
                              "refs/heads/%s..refs/heads/%s" % (base, c["branch"])], c["repo"])
            still_zero = recheck is not None and recheck.strip() == "0"
        if not still_zero:
            entry["branch_deleted"] = False
            entry["reason"] = "removed worktree, but branch now carries new commits -- branch left in place"
            results.append(entry)
            continue
        bd = git_run(["branch", "-D", c["branch"]], c["repo"])
        entry["branch_deleted"] = bd is not None
        results.append(entry)

    _log_stale_worktree_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  worktree-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_worktrees(args):
    """`airuleset.py sweep-worktrees [--dry-run]` -- manual/testable entry
    point for the #345 sweep. Always `force=True` (bypasses the cadence
    gate that guards the automatic install/push wiring -- a deliberate
    manual call should never be silently skipped)."""
    print("airuleset sweep-worktrees")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    results = sweep_stale_worktrees(dry_run=dry_run, force=True)
    for r in results:
        if r.get("path") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        # #345 adversarial-review MAJOR-1: sweep_stale_worktrees() leaves
        # `removed=False` on EVERY row in dry-run (correct -- nothing was
        # actually deleted), so keying the tag/count on `removed` alone
        # mislabelled every genuine candidate "skip" and always reported
        # "0 worktree(s) would be removed". A dry-run candidate is
        # identified by its own distinct `reason` text instead.
        acted = (str(r.get("reason", "")).startswith("would remove")
                if dry_run else bool(r.get("removed")))
        if acted:
            tag = "WOULD REMOVE" if dry_run else "REMOVED"
        else:
            tag = "skip"
        print("  %s: %s (branch %s, repo %s) -- %s" % (
            tag, r["path"], r.get("branch"), r.get("repo"), r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    print()
    verb = "would be " if dry_run else ""
    print("%d worktree(s) %sremoved." % (len(acted_rows), verb))
    print("Log: %s" % STALE_WORKTREE_LOG_PATH)

    # #513 constraint #2 -- surface abandoned worktrees carrying real work
    # (unmerged commits or a dirty tree) LOUDLY. NEVER auto-removed: the
    # supervisor/human decides (integrate, park the branch, or discard).
    # OPT-IN via --salvage: the scan walks EVERY managed repo + does a network
    # `git ls-remote` per candidate, so it is off by default (a plain sweep
    # stays fast + hermetic; existing wiring tests never trigger it).
    if not getattr(args, "salvage", False):
        return
    try:
        salvage = discover_salvage_worktrees()
    except Exception as e:      # noqa: BLE001 -- report is best-effort
        print("  salvage report unavailable: %s" % e, file=sys.stderr)
        salvage = []
    if salvage:
        print()
        print("SALVAGE (abandoned worktrees with real work -- NOT auto-removed, needs a decision):")
        try:
            from cli_target_purge import _human_size
        except Exception:       # noqa: BLE001
            _human_size = lambda n: "%s B" % n      # noqa: E731
        for s in salvage:
            backup = ("wip-backup=yes" if s["wip_backup"] is True
                      else "wip-backup=NO" if s["wip_backup"] is False
                      else "wip-backup=?")
            size = _human_size(s["size"]) if s.get("size") is not None else "?"
            print("  %s (branch %s, repo %s) -- %d ahead, %d dirty, %s, idle %.1fh, %s"
                  % (s["path"], s["branch"], s["repo"], s["ahead"], s["dirty"],
                     size, s["age_s"] / 3600.0, backup))
    else:
        print()
        print("SALVAGE: no abandoned worktrees carrying unmerged/dirty work.")
