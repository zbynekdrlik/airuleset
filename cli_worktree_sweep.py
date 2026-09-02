"""airuleset stale-worktree sweep (#345/#348) — cluster L sub-split #1 (#433).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as
watchdog/usage.py / burn_jobs.py / cards.py / repo_health.py and
cli_vault.py (H) / cli_autopilot_lock.py (K) / cli_burn.py (J) /
cli_quals.py+cli_quals_cmd.py (I)). airuleset.py keeps
`from cli_worktree_sweep import (...)` re-exports at the old definition
site, so cmd_install's non-fatal sweep step, SUBCOMMANDS["sweep-worktrees"]
and tests' `airuleset.discover_stale_worktrees(...)`-style direct
references all keep working unchanged.

This module is deliberately SELF-CONTAINED: stdlib only at module level —
no top-level `import airuleset`, so there is no import-cycle surface in
either the CLI (`python3 airuleset.py`, airuleset running as `__main__`)
or the test (`import airuleset`) topology (internals note 1483). `CLAUDE_DIR`
below is this file's own copy of the canonical one-line expression
(`Path.home() / ".claude"`) that cli_autopilot_lock.py / watchdog/goal.py /
watchdog/compact.py already inline locally today — identical value,
established repo idiom.

The one outbound coupling is the shared repo-discovery helper
`_checkout_roots` (which stays in airuleset.py, near `_local_checkout_for_repo`
+ `discover_target_purge_candidates` that also use it — moving it here would
misplace a central symbol, the J/REMOTE_HOSTS decision): the two `discover_*`
functions reach it via a lazily-placed deferred `import airuleset`
(internals note 1486), which in CLI `__main__` mode triggers a one-time
~130 ms second module execution but only on the install-time sweep paths,
and is a plain cache-hit from tests / other modules.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"


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


def discover_stale_worktrees(home=None, git_run=None, now=None, pid_is_dead=None):
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
            if e.get("locked"):
                out.append(_classify_locked_worktree(
                    root, e.get("path"), branch, e.get("lock_reason"),
                    git_run, now, pid_is_dead=pid_is_dead))
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
            row["base"] = base
            out.append(row)     # reason stays None -- genuine candidate
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
                          locked=False):
    """Classify ONE worktree directory for disk-guard reclaim. Returns a row
    ``{path, branch, repo, reason, kind, reachable_via}`` — ``reason`` is None
    ONLY when the DIRECTORY is safe to free (the branch ref is always kept). The
    guards, cheapest-first: NOT locked (a locked worktree is a live session's,
    #348 — review 🔴), not in live use, idle > `min_idle_s`, HEAD readable (else
    classified `orphan-gitdir` when the gitdir points nowhere), no precious
    ignored file, clean tree, and HEAD reachable from an origin ref. The precious
    check runs AFTER the HEAD-read so an orphan (unreadable git) is never falsely
    kept by a git-error fail-safe (review 🟡)."""
    git_run = git_run or _worktree_git
    in_live_use = in_live_use or _worktree_in_live_use
    recency_fn = recency_fn or _worktree_recency_age_s
    precious_fn = precious_fn or _worktree_has_precious_ignored
    row = {"path": path, "branch": branch, "repo": root, "reason": None,
           "kind": "worktree", "reachable_via": None}

    if locked:
        row["reason"] = "locked worktree (live session, #348) — never removed"
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

    clean = _worktree_is_clean(path, git_run)
    if clean is None:
        row["reason"] = "git status unmeasurable — kept"
        return row
    if clean is False:
        row["reason"] = "dirty tree (uncommitted work) — never removed"
        return row
    if precious_fn(path):
        row["reason"] = "precious ignored file present (.env/*.key/local.settings) — kept"
        return row

    via = _worktree_head_reachable_from_origin(path, branch, base, head, git_run)
    if via is None:
        row["reason"] = ("HEAD not reachable from any origin ref "
                         "(work not preserved on origin) — kept")
        return row
    row["reachable_via"] = via
    return row                             # reason None → directory reclaimable


def discover_reclaimable_worktrees(home=None, git_run=None, now=None,
                                   min_idle_s=None, in_live_use=None):
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
                in_live_use=in_live_use, locked=bool(e.get("locked"))))
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
                        min_idle_s=min_idle_s, in_live_use=in_live_use))
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


# --- Merged worktree-lane target/ reclaim (#545) ----------------------------
# The #315 target-purge (7-day newest-mtime floor) is defeated for a worktree
# LANE by cargo's fingerprint churn (every compile-check touches
# `.rustc_info.json` + fingerprints, refreshing the newest mtime forever), and
# the #345 whole-lane sweep waits the full 24h idle floor before reclaiming a
# dead lane at all -- a floor #513 correctly chose to protect a FRESH 0-ahead
# live worker (byte-identical to a dead merged one by git-ahead-count alone).
# So neither reclaims a MERGED lane's expensive `target/` (regenerable build
# output, gitignored) before 24h idle. Measured on dev1 (#545 STEP-0): 6.3 GB
# in 2 merged camera-box lanes sitting idle ~7h, never reclaimed.
#
# This reclaims ONLY the `target/` of a lane whose branch is MERGED into its
# base AND whose reflog shows a real AUTHORED commit -- the robust
# distinguisher #513 said 0-ahead cannot give: 0-ahead + authored-commit =
# the branch did feature work now in base = definitively merged-done, never a
# fresh 0-ahead worker (which has NO authored-commit reflog entry). Guarded
# five ways (0-ahead + authored-commit + Tier-0 + not-in-live-use + idle a
# short grace) + a re-check before delete; fail-SAFE in every direction
# (merged = nothing unmerged to lose; target/ = 100% regenerable, so even a
# wrong purge costs only a rebuild). NEVER removes the worktree or the branch
# (the #345 sweep owns whole-lane removal at 24h). Cadence-gated by its own
# state file (mirrors #315/#345/#355 exactly), non-fatal cmd_install() step +
# a manual/testable `sweep-lane-targets` CLI entry.

LANE_TARGET_LOG_PATH = CLAUDE_DIR / "lane-target-reclaim.log"
LANE_TARGET_STATE_PATH = CLAUDE_DIR / "lane-target-reclaim-state.json"
LANE_TARGET_MIN_INTERVAL_S = 6 * 3600            # env AIRULESET_LANE_TARGET_INTERVAL_S
# A SHORT idle grace is safe here precisely because the authored-commit-in-
# reflog check already excludes the fresh-0-ahead-worker case the #345 24h
# floor exists for -- this grace only guards against a build lull / a
# re-dispatch race, both of which the live-use guard already catches during an
# active build. Env-tunable (`AIRULESET_LANE_TARGET_MERGED_MIN_IDLE_S`).
LANE_TARGET_MERGED_MIN_IDLE_S = 2 * 3600

# --- #545 owner addendum (2026-08-19): tier-0 bypass classification ---------
# camera-box is Tier 0 (#557: ZERO local cargo compilation), so a lane target/
# is either LEGACY (built before the #557 flip) or evidence of a live tier-0
# gate BYPASS (a `cargo` compile that ran locally AFTER the ban -- the known
# class is a build INSIDE an E2E .sh script the Bash-level hook cannot see,
# camera-box #185). The #557 `block-tier0-local-build.sh` allowlist inversion
# MERGED at 598e3826 (closed 2026-08-19T00:38:59Z); a Tier-0 lane target/ file
# NEWER than this instant is a bypass finding to RECORD + FILE before the purge
# (never silently delete it).
#
# NOTE (#545 review 🟡): `TIER0_FLIP_ISO` is the #557 MERGE instant, but the
# hook only starts BLOCKING once #557 DEPLOYS to a box (the push after merge).
# A legit build in the (one-time, now-passed) merge->deploy window has an mtime
# > this and would classify as a bypass. So the flip epoch is ENV-OVERRIDABLE
# (`AIRULESET_TIER0_FLIP_EPOCH`, resolved at CALL time): set it to the box's
# actual #557 deploy instant when they differ meaningfully, and always DRY-RUN
# first (the CLI previews WOULD-file findings) before trusting an auto-file.
TIER0_FLIP_ISO = "2026-08-19T00:38:59+00:00"


def _tier0_flip_epoch():
    """The #557 tier-0 flip instant as an epoch, `AIRULESET_TIER0_FLIP_EPOCH`
    env-override winning (resolved at CALL time, so a box whose #557 deploy
    lagged the merge can tune it), else the #557 merge instant
    (`TIER0_FLIP_ISO`)."""
    env = os.environ.get("AIRULESET_TIER0_FLIP_EPOCH")
    if env:
        try:
            return float(env)
        except ValueError:
            print("  lane-target-reclaim: bad AIRULESET_TIER0_FLIP_EPOCH %r, "
                  "falling back to the #557 default" % env, file=sys.stderr)
    import datetime as _dt
    return _dt.datetime.fromisoformat(TIER0_FLIP_ISO).timestamp()


LANE_TARGET_TIER0_BYPASS_STATE_PATH = CLAUDE_DIR / "lane-target-tier0-bypass-state.json"
# Per-repo dedup cooldown: at most ONE bypass ticket per offending repo per
# window. The natural cardinality is already ~1 (a reclaimed target is gone, so
# re-detection needs a NEW post-flip lane) -- this only bounds a dedup-state
# loss to one duplicate per window. Env-tunable.
LANE_TARGET_TIER0_BYPASS_REFILE_S = 14 * 24 * 3600
LANE_TARGET_TIER0_BYPASS_TITLE = (
    "Tier-0 gate bypass: lokalny cargo build po #557 (airuleset #545 auto-detekcia)")


def _lane_human_size(n):
    """`cli_target_purge._human_size(n)`, with a plain fallback if that
    disk-helper module is unavailable (the size text is cosmetic, never
    load-bearing) -- one place for the deferred import + fallback the three
    #545 call sites would otherwise each repeat (#545 review 🔵)."""
    try:
        from cli_target_purge import _human_size
        return _human_size(n)
    except Exception:       # noqa: BLE001 -- cosmetic degradation only
        return "%s B" % n


def _branch_reflog_has_authored_commit(repo_root, branch, git_run=None):
    """True iff `branch`'s reflog contains at least one real AUTHORED commit
    (`commit:` / `commit (initial):` / `commit (amend):`) -- the signal #513
    said a plain 0-ahead-of-base count cannot give, distinguishing a
    merged-DONE lane (its authored commits are now in base) from a FRESH
    0-ahead worker (whose reflog holds only `branch: Created from ...`, no
    authored commit yet).

    Deliberately EXCLUDES merge-commits (`commit (merge):`, `merge <x>: ...`)
    and ref-creation/reset/rebase ops: a lane that only base-synced but never
    authored feature work must NOT read as merged-done. A real merged lane
    always carries its RED/GREEN/review authored commits, so excluding merges
    never loses one.

    Reads the branch reflog via `git log -g --format=%gs` (reflog SUBJECTS
    only). Returns False on ANY read failure or an absent/pruned reflog -- an
    unmeasurable signal is treated as "keep" (fail-SAFE: the 24h whole-lane
    sweep reclaims such a lane later)."""
    git_run = git_run or _worktree_git
    out = git_run(["log", "-g", "--format=%gs", "refs/heads/%s" % branch], repo_root)
    if out is None:
        return False
    for line in out.splitlines():
        s = line.strip()
        if (s.startswith("commit:")
                or s.startswith("commit (initial):")
                or s.startswith("commit (amend):")):
            return True
    return False


def _iter_lane_target_dirs(home, git_run):
    """Yield (repo_root, lane_path, branch, target_dir) for every registered
    worktree LANE (a non-primary worktree whose path is under
    `.claude/worktrees/`) that carries a `target/` entry. The primary
    checkout is skipped by INDEX (entry 0), never by path-match -- a human's
    main-checkout `target/` is the #315 sweep's job (7-day floor), never this
    aggressive merged-lane path."""
    import airuleset
    for root in airuleset._checkout_roots(home):
        if not (Path(root) / ".git").is_dir():
            continue                         # only PRIMARY checkouts list worktrees
        entries = _worktree_porcelain_entries(root, git_run=git_run)
        for i, e in enumerate(entries):
            if i == 0:
                continue                     # primary checkout -- never
            path = e.get("path")
            branch = e.get("branch")
            if not path or branch is None or branch in _STALE_WORKTREE_PROTECTED_BRANCHES:
                continue
            if "/.claude/worktrees/" not in str(path).replace(os.sep, "/"):
                continue                     # only in-tree lane worktrees
            target_dir = Path(path) / "target"
            if not target_dir.exists() and not target_dir.is_symlink():
                continue
            yield Path(root), Path(path), branch, target_dir


def _log_lane_target_results(results, log_path, now, dry_run: bool):
    """Append one line per lane target examined (purge AND skip alike --
    comprehensive-logging.md: a destructive action logs everything). A
    `target is None` row (a discovery error) logs as `target=-`. Best-effort:
    a log-write failure is reported, never a silent pass, and never blocks the
    reclaim."""
    import datetime as _dt
    ts = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).isoformat()
    lines = []
    for r in results:
        if r.get("target") is None:
            lines.append("%s ERROR - repo=- reason=%s" % (ts, r.get("reason", "")))
            continue
        if r.get("purged"):
            action = "DRYRUN-WOULD-PURGE" if dry_run else "PURGED"
        else:
            action = "SKIP"
        size = r.get("size")
        size_txt = " size=%s" % _lane_human_size(size) if size is not None else ""
        tier0_txt = ""
        if "tier0_bypass" in r:              # only set on a purge candidate (#545)
            if r.get("tier0_bypass"):
                # Log the mtime + fresh-file count too, so the bypass evidence
                # is DURABLE even when the ticket-file failed (the ticket body
                # is then lost with the deleted target/ -- #545 review C5).
                nm = r.get("newest_mtime")
                nm_txt = ""
                if nm is not None:
                    try:
                        nm_txt = " mtime=%s" % _dt.datetime.fromtimestamp(
                            nm, tz=_dt.timezone.utc).isoformat()
                    except (OverflowError, OSError, ValueError):
                        nm_txt = " mtime=%s" % nm
                fresh_n = len(r.get("fresh_sample") or [])
                tier0_txt = " tier0=BYPASS(%s)%s fresh>=%d" % (
                    r.get("tier0_bypass_filed") or "?", nm_txt, fresh_n)
            else:
                tier0_txt = " tier0=legacy"
        lines.append("%s %s %s branch=%s repo=%s%s%s reason=%s" % (
            ts, action, r["target"], r.get("branch"), r.get("repo"),
            size_txt, tier0_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  lane-target-reclaim: could not write log %s: %s" % (log_path, e),
              file=sys.stderr)


def _lane_repo_slug(root, git_run):
    """`owner/name` for a lane's primary checkout, parsed from its `origin`
    remote (`git remote get-url origin`) -- https OR ssh form. Returns None
    when there is no origin or the URL is unparseable, so a bypass finding is
    then RECORDED but never FILED on a guessed repo (fail-SAFE: never file the
    wrong repo)."""
    url = git_run(["remote", "get-url", "origin"], root)
    if not url:
        return None
    m = re.search(r"[:/]([^/:]+)/([^/:]+?)(?:\.git)?/?$", url.strip())
    if not m:
        return None
    return "%s/%s" % (m.group(1), m.group(2))


def _sample_fresh_target_files(target_dir, flip_epoch, limit=6):
    """Up to `limit` (relpath, mtime) pairs for FILES under `target/` whose
    mtime is strictly AFTER `flip_epoch` -- the concrete evidence a post-#557
    cargo compile ran. Bounded (returns as soon as `limit` is reached); a
    per-file stat error is skipped (fail-safe)."""
    out = []
    base = str(target_dir)
    for dirpath, _dirs, files in os.walk(base, onerror=lambda e: None):
        for f in files:
            fp = os.path.join(dirpath, f)
            try:
                mt = os.lstat(fp).st_mtime
            except OSError:
                continue
            if mt > flip_epoch:
                out.append((os.path.relpath(fp, base), mt))
                if len(out) >= limit:
                    return out
    return out


def _tier0_bypass_issue_body(branch, lane, target_dir, newest_mtime, fresh_sample):
    """The body of the auto-filed tier-0 bypass ticket (#545): plain evidence
    (newest mtime + a fresh-file sample) + the known hook-gap class + a
    mandatory `Scope-gate:` line."""
    import datetime as _dt

    def _iso(ts):
        try:
            return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(ts)

    lines = [
        "Automaticka detekcia (airuleset #545): worktree-lane `target/` na "
        "**Tier-0** projekte obsahuje build artefakty NOVSIE ako #557 tier-0 "
        "flip (%s) -- dokaz, ze lokalna `cargo` kompilacia prebehla PO zakaze "
        "(#557: ziadne lokalne cargo buildy), teda obisla tier-0 gate." % TIER0_FLIP_ISO,
        "",
        "- Lane branch: `%s`" % branch,
        "- Lane: `%s`" % lane,
        "- target/: `%s`" % target_dir,
        "- Najnovsi target/ mtime: %s  (flip baseline: %s)" % (
            _iso(newest_mtime), TIER0_FLIP_ISO),
        "",
        "Vzorka cerstvych (post-flip) suborov:",
    ]
    if fresh_sample:
        for rel, mt in fresh_sample:
            lines.append("  - `%s`  (%s)" % (rel, _iso(mt)))
    else:
        lines.append("  - (ziadna vzorka -- mtime evidencia vyssie)")
    lines += [
        "",
        "Znama trieda hook-gapu: `cargo build` VNUTRI E2E `.sh` skriptu, ktory "
        "`block-tier0-local-build.sh` (Bash-level hook) nevidi -- camera-box "
        "#185. Najdi a oprav miesto, ktore kompiluje lokalne, aby tier-0 gate "
        "platil aj tam.",
        "",
        "(Pozn.: `target/` tejto zmergovanej lane airuleset automaticky "
        "reklamoval -- je 100% regenerovatelny; toto je LEN nahlasenie "
        "hook-gapu, nie strata dat.)",
        "",
        "Scope-gate: cross-cutting",
    ]
    return "\n".join(lines)


def _default_tier0_bypass_filer(repo_slug, title, body):
    """Real `gh issue create -R <owner/name>` filer for a tier-0 bypass finding
    (#545). Returns the created issue URL, or None on ANY failure (fail-SAFE:
    the caller reports the failure, never records it as 'filed')."""
    import subprocess as _sp
    try:
        r = _sp.run(["gh", "issue", "create", "-R", repo_slug,
                     "--title", title, "--body", body],
                    capture_output=True, text=True, timeout=60)
    except Exception as e:      # noqa: BLE001 -- a filing crash never blocks reclaim
        print("  lane-target-reclaim: tier-0 bypass filing errored: %s" % e,
              file=sys.stderr)
        return None
    if r.returncode != 0:
        print("  lane-target-reclaim: tier-0 bypass filing failed (rc=%d): %s" % (
            r.returncode, (r.stderr or "").strip()), file=sys.stderr)
        return None
    return (r.stdout or "").strip() or "filed"


def _record_tier0_bypass(root, branch, lane, target_dir, newest_mtime,
                         fresh_sample, now, git_run, issue_filer,
                         bypass_state_path, dry_run, filed_this_run=None):
    """Resolve the offending repo, dedup per-repo (ACROSS runs via the local
    state file's `LANE_TARGET_TIER0_BYPASS_REFILE_S` cooldown; WITHIN a run via
    the in-memory `filed_this_run` set, which stays correct even if the state
    write below fails -- the disk-pressure scenario this whole feature runs in,
    #545 review C6), and file ONE ticket via `issue_filer`. Records BEFORE the
    caller's delete. Returns a human status string; NEVER raises -- a bypass
    finding must not block the reclaim."""
    slug = _lane_repo_slug(root, git_run)
    if not slug:
        return "repo slug unresolved -- recorded, not filed"
    if dry_run:
        return "dry-run -- would file on %s (not filed)" % slug
    if filed_this_run is not None and slug in filed_this_run:
        return "already tracked on %s (this run)" % slug

    refile_s = _worktree_env_age_s("AIRULESET_LANE_TARGET_TIER0_BYPASS_REFILE_S",
                                   LANE_TARGET_TIER0_BYPASS_REFILE_S)
    try:
        st = json.loads(Path(bypass_state_path).read_text())
        filed = st.get("filed", {}) if isinstance(st, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        filed = {}
    prev = filed.get(slug)
    if isinstance(prev, dict):
        try:
            ts = float(prev.get("ts", 0))
        except (TypeError, ValueError):
            ts = 0
        if 0 <= now - ts < refile_s:        # a future-dated stamp re-files (fail-safe)
            if filed_this_run is not None:
                filed_this_run.add(slug)
            return "already tracked on %s (dedup)" % slug

    title = LANE_TARGET_TIER0_BYPASS_TITLE
    body = _tier0_bypass_issue_body(branch, lane, target_dir, newest_mtime, fresh_sample)
    try:
        url = issue_filer(slug, title, body)
    except Exception as e:      # noqa: BLE001 -- a filer crash never blocks reclaim
        print("  lane-target-reclaim: tier-0 bypass filer raised: %s" % e,
              file=sys.stderr)
        url = None
    if not url:
        return "filing FAILED on %s -- recorded, not filed" % slug

    if filed_this_run is not None:          # dedup the rest of THIS run even if
        filed_this_run.add(slug)            # the disk write below fails (C6)
    filed[slug] = {"ts": now, "issue": url}
    try:
        Path(bypass_state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(bypass_state_path).write_text(json.dumps({"filed": filed}))
    except OSError as e:
        print("  lane-target-reclaim: could not write bypass state %s: %s" % (
            bypass_state_path, e), file=sys.stderr)
    return "filed on %s: %s" % (slug, url)


def purge_merged_lane_targets(home=None, dry_run: bool = False, now=None,
                              log_path=None, state_path=None, force: bool = False,
                              git_run=None, tier0_fn=None, proc_dir=None,
                              hook_path=None, flip_epoch=None, issue_filer=None,
                              bypass_state_path=None):
    """Reclaim the `target/` of a worktree LANE whose branch is MERGED into
    its base (#545). A candidate is purged only when ALL of these hold, in
    order (each refusing -- never guessing -- on its own failure):

      - it is a `target/` inside a real non-primary worktree LANE under
        `.claude/worktrees/` (`_iter_lane_target_dirs`);
      - `target/` is not itself a symlink, and its resolved path does not
        escape the lane (never followed, never deleted through);
      - the branch carries ZERO commits ahead of the repo's resolved base
        (`_worktree_sweep_base_branch` -- dev/main/master superset) -- real
        unmerged work is never touched;
      - the branch reflog shows a real AUTHORED commit
        (`_branch_reflog_has_authored_commit`) -- 0-ahead + authored = merged-
        DONE, never a fresh 0-ahead live worker (#513's own ambiguity);
      - the lane is genuinely Tier 0 (`_tier0_via_hook` on `target/`'s parent
        -- the SAME single-source-of-truth check #315 uses; a Tier-1/2 lane's
        target/ is never touched);
      - no live process is rooted in `target/` (`_target_in_live_use` --
        catches an active `cargo` build with open fds);
      - the lane is idle for at least `LANE_TARGET_MERGED_MIN_IDLE_S`
        (`_worktree_recency_age_s` -- belt-and-suspenders; the authored-commit
        check already excludes the fresh-worker case the #345 24h floor
        protects, so a SHORT grace is safe here).

    Immediately before `shutil.rmtree`, `target/` is re-checked for symlink +
    live use (a TOCTOU re-check, mirroring #315). Deletes `target/` ONLY --
    never the worktree, never the branch (the #345 sweep owns whole-lane
    removal at the 24h floor).

    #545 owner addendum (tier-0 bypass classification): each purge candidate is
    classified against `flip_epoch` (the #557 tier-0 flip, `_tier0_flip_epoch`)
    BEFORE the delete. A `target/` whose newest artifact is NEWER than the flip
    is a tier-0 gate BYPASS (a local cargo compile that ran after the ban -- a
    hook gap the Bash-level guard cannot see, camera-box #185): it is RECORDED
    and FILED (via `issue_filer`, deduped per-repo against `bypass_state_path`)
    on the lane's own `origin` repo, then STILL purged (the deletion scope is
    unchanged -- Approach 1 only). A pre-flip (legacy) target purges silently.

    Returns a list of per-lane dicts (`target`, `lane`, `branch`, `repo`,
    `purged`, `reason`, `size`, `age_s`; plus `newest_mtime`/`tier0_bypass` and,
    on a bypass, `fresh_sample`/`tier0_bypass_filed`). Cadence-gated by its own
    state file; `force=True` (a manual CLI call) or `dry_run=True` bypasses the
    gate (a dry-run classifies but never files and never deletes)."""
    import time as _time
    now = _time.time() if now is None else now
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    log_path = Path(log_path) if log_path else LANE_TARGET_LOG_PATH
    state_path = Path(state_path) if state_path else LANE_TARGET_STATE_PATH
    git_run = git_run or _worktree_git
    grace = _worktree_env_age_s("AIRULESET_LANE_TARGET_MERGED_MIN_IDLE_S",
                                LANE_TARGET_MERGED_MIN_IDLE_S)
    flip_epoch = _tier0_flip_epoch() if flip_epoch is None else flip_epoch
    issue_filer = issue_filer or _default_tier0_bypass_filer
    bypass_state_path = (Path(bypass_state_path) if bypass_state_path
                         else LANE_TARGET_TIER0_BYPASS_STATE_PATH)

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = LANE_TARGET_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_LANE_TARGET_INTERVAL_S", interval))
        except ValueError:
            interval = LANE_TARGET_MIN_INTERVAL_S
        if now - last < interval:
            return []

    # Deferred imports keep this module's stdlib-only top level (the SAME
    # one-directional cli_worktree_sweep -> cli_target_purge coupling that
    # `_worktree_in_live_use` already uses -- never a cycle).
    try:
        from cli_target_purge import (_target_in_live_use, _dir_stats,
                                       _tier0_via_hook)
    except Exception as e:      # noqa: BLE001 -- fail-safe: no disk helpers -> reclaim nothing
        print("  lane-target-reclaim: disk helpers unavailable, skipping: %s" % e,
              file=sys.stderr)
        return []
    if tier0_fn is None:
        def tier0_fn(cwd):
            return _tier0_via_hook(cwd, hook_path=hook_path)

    results = []
    filed_this_run = set()          # #545 C6: within-run per-repo dedup, disk-safe
    discovery_failed = False
    try:
        lane_targets = list(_iter_lane_target_dirs(home, git_run))
    except Exception as e:      # noqa: BLE001
        lane_targets = []
        discovery_failed = True
        results.append({"target": None, "purged": False,
                        "reason": "discovery error: %s" % e})

    for root, lane, branch, target_dir in lane_targets:
        entry = {"target": str(target_dir), "lane": str(lane), "branch": branch,
                 "repo": str(root), "purged": False, "reason": None,
                 "size": None, "age_s": None}
        try:
            if target_dir.is_symlink():
                entry["reason"] = "symlink target/ -- never followed"
                results.append(entry)
                continue
            try:
                resolved = target_dir.resolve()
                resolved.relative_to(lane.resolve())
            except (OSError, ValueError):
                entry["reason"] = "resolved target/ escapes lane -- skipped"
                results.append(entry)
                continue

            base = _worktree_sweep_base_branch(root, git_run=git_run)
            if not base:
                entry["reason"] = "no base branch (dev/main/master) -- skipped"
                results.append(entry)
                continue
            ahead = git_run(["rev-list", "--count",
                            "refs/heads/%s..refs/heads/%s" % (base, branch)], root)
            if ahead is None or not ahead.strip().isdigit():
                entry["reason"] = "ahead-count unmeasurable -- skipped"
                results.append(entry)
                continue
            if int(ahead.strip()) > 0:
                entry["reason"] = "%s commit(s) ahead of %s -- unmerged, kept" % (
                    ahead.strip(), base)
                results.append(entry)
                continue

            if not _branch_reflog_has_authored_commit(root, branch, git_run=git_run):
                entry["reason"] = ("0-ahead but no authored commit in reflog "
                                   "-- fresh worker (never merged-done), kept")
                results.append(entry)
                continue

            if not tier0_fn(str(target_dir.parent)):
                entry["reason"] = "not Tier 0 (allowed/fast-iterate marker, or unmanaged) -- kept"
                results.append(entry)
                continue

            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- kept"
                results.append(entry)
                continue

            rec = _worktree_recency_age_s(str(root), str(lane), now)
            entry["age_s"] = rec
            if rec is None:
                entry["reason"] = "lane recency unmeasurable -- kept"
                results.append(entry)
                continue
            if rec < grace:
                entry["reason"] = ("active within %.1fh (grace %.1fh) -- kept"
                                   % (rec / 3600.0, grace / 3600.0))
                results.append(entry)
                continue

            size_bytes, newest_mtime = _dir_stats(target_dir)
            entry["size"] = size_bytes
            entry["newest_mtime"] = newest_mtime

            # Re-check symlink + live use immediately before the delete (a
            # TOCTOU re-check -- something could have started a build, or
            # swapped target/ for a symlink, since the checks above). Mirrors
            # #315's own re-verify-before-delete.
            if target_dir.is_symlink():
                entry["reason"] = "symlink target/ (re-checked) -- refused"
                results.append(entry)
                continue
            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (re-checked before delete) -- kept"
                results.append(entry)
                continue

            # --- Tier-0 bypass classification (#545 owner addendum) ---------
            # A merged Tier-0 lane target/ with build artifacts NEWER than the
            # #557 flip is evidence a cargo compile ran locally after the ban
            # (a hook gap the Bash-level guard cannot see -- camera-box #185).
            # RECORD + FILE the finding BEFORE the delete; never silently purge
            # a bypass. Deletion scope itself is UNCHANGED (owner: Approach 1
            # only). A pre-flip (legacy) target purges silently.
            bypass = newest_mtime is not None and newest_mtime > flip_epoch
            entry["tier0_bypass"] = bypass
            if bypass:
                fresh_sample = _sample_fresh_target_files(target_dir, flip_epoch)
                entry["fresh_sample"] = fresh_sample
                entry["tier0_bypass_filed"] = _record_tier0_bypass(
                    root, branch, lane, target_dir, newest_mtime, fresh_sample,
                    now, git_run, issue_filer, bypass_state_path, dry_run,
                    filed_this_run=filed_this_run)

            entry["reason"] = "merged-lane target/ reclaimed (%s idle, %s)" % (
                "%.1fh" % (rec / 3600.0), _lane_human_size(size_bytes))
            if not dry_run:
                shutil.rmtree(target_dir)
            entry["purged"] = True
            results.append(entry)
        except Exception as e:      # noqa: BLE001 -- one bad lane never aborts the sweep
            entry["reason"] = "error: %s" % e
            results.append(entry)

    _log_lane_target_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  lane-target-reclaim: could not write state %s: %s" % (state_path, e),
                  file=sys.stderr)

    return results


def cmd_purge_lane_targets(args):
    """`airuleset.py sweep-lane-targets [--dry-run]` -- manual/testable entry
    point for the #545 merged-lane target/ reclaim. Always `force=True`
    (bypasses the cadence gate that guards the automatic install/push wiring
    -- a deliberate manual call should never be silently skipped)."""
    print("airuleset sweep-lane-targets")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    results = purge_merged_lane_targets(dry_run=dry_run, force=True)
    for r in results:
        if r.get("target") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        if r.get("purged"):
            tag = "WOULD PURGE" if dry_run else "PURGED"
        else:
            tag = "skip"
        print("  %s: %s (branch %s) -- %s" % (
            tag, r["target"], r.get("branch"), r.get("reason", "")))
    purged = [r for r in results if r.get("purged")]
    total = sum(r.get("size", 0) or 0 for r in purged)
    print()
    verb = "would be " if dry_run else ""
    print("%d merged-lane target/ dir(s) %sreclaimed, %s %sfreed." % (
        len(purged), verb, _lane_human_size(total), verb))
    # Tier-0 classification report (#545): surface every bypass finding (a
    # merged lane whose target/ held post-#557 build artifacts).
    bypasses = [r for r in results if r.get("tier0_bypass")]
    legacy = [r for r in purged if r.get("tier0_bypass") is False]
    if bypasses:
        print()
        print("  TIER-0 BYPASS: %d lane(s) with fresh (post-#557) target/ "
              "artifacts -- a local cargo build escaped the tier-0 gate:" % len(bypasses))
        for r in bypasses:
            print("    %s (branch %s) -- %s" % (
                r["target"], r.get("branch"), r.get("tier0_bypass_filed") or "?"))
    if legacy:
        print("  Tier-0: %d reclaimed lane(s) were legacy (pre-#557), not a bypass."
              % len(legacy))
    print("Log: %s" % LANE_TARGET_LOG_PATH)
