"""Checkout freshness: the ONE shared fast-forward safety predicate (#1176).

Two callers, one definition of "provably safe to fast-forward":

  * `hooks/session-start-fetch.sh` (SessionStart startup + resume) runs
    `python3 cli_checkout_freshness.py hook` after its own `git fetch origin`
    and prints the line this module composes (the #314 wording, unchanged).
  * the watchdog Job 53 leaf `watchdog/checkout_freshness.py` calls
    `ff_verdict` / `fast_forward` for every managed checkout of the box on a
    15-min cadence, so freshness no longer depends on a session-start event
    (the #1127 recurrence: every managed session starts with `claude -c`, a
    `resume` source, and a session can live for days). Its fast-forward runs
    DETACHED — `python3 cli_checkout_freshness.py ff ...` (`ff_child`) in a
    transient `systemd-run --user` unit, outside the watchdog unit's kill
    window (the #1176 reopen: a sweep-budget reserve starved it forever).

The same module also READS the job's status file for its one visibility
surface, `status_lines` (`airuleset.py status`). The footer shows nothing
about checkout freshness (owner decision 30.9., #1176 reopen).

SAFETY (never relaxed): fast-forward ONLY (`git merge --ff-only`), never a
reset / checkout -f / merge commit / rebase. Refused, and only reported:
an in-progress operation (merge, rebase, cherry-pick, revert, bisect,
sequencer, a held `index.lock`), an unreadable git dir, a detached HEAD, a
dirty or UNMEASURABLE working tree, a HEAD that is not an ancestor of the
target (diverged), and an incoming path that would land on an existing
untracked or ignored file — the path itself OR any of its parent
directories, with renames off and NUL-separated names (#314 F1). `--ff-only`
itself also refuses to overwrite a file edited between the check and the
merge. The ONE move of a detached HEAD / merged work branch onto its base
(`reattach_verdict` / `reattach`, #1176 census) keeps every one of these
refusals and adds its own (commits outside the base, a local base ahead, the
base checked out in another worktree, a long-lived branch, HEAD moved within
REATTACH_IDLE_S); it is a local fast-forward fetch + a plain checkout, never
a reset, and the old branch ref is kept. Job 53 alone runs it (with its
session-liveness gate); the hook only prints the notice.

PROCESS SAFETY: every git child runs in its own process group. Reads run
with GIT_OPTIONAL_LOCKS=0 (a `git status` never rewrites the index a live
session is about to lock). A timeout sends SIGTERM first — git removes its
own lock files on SIGTERM — and SIGKILL only after a grace period, so a
bounded call never leaves a stale `index.lock` or ref lock behind. A caller
may set a DEADLINE (`deadline()`); every call is clipped to it.

stdlib only; imports nothing from this repo at import time.
"""
import contextlib
import json
import os
import signal
import subprocess
import sys
import time

# `index.lock` = another git process is mid-write in this checkout RIGHT NOW (a
# session's commit / merge): never race it for the lock.
IN_PROGRESS_STATES = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                      "BISECT_LOG", "rebase-apply", "rebase-merge", "sequencer",
                      "index.lock")
GIT_LOCAL_TIMEOUT_S = 10      # any local git read
TERM_GRACE_S = 5              # SIGTERM -> SIGKILL grace (git cleans its locks)
STATUS_REL = os.path.join(".claude", "checkout-freshness", "status.json")
STALE_AFTER_H_DEFAULT = 6     # a lag counts as `stale` in status past this many hours
STATUS_DEAD_S = 2 * 3600      # status older than this = dead watchdog, hide
UNMEASURABLE = object()
_DEADLINE = [None]            # monotonic deadline set by `deadline()`, or None
DEFAULT_BASES = ("develop", "dev", "main", "master")
RULE_PATHS = ("CLAUDE.md", ".claude", ":(glob)**/CLAUDE.md",
              ":(glob)**/.claude/**")
REATTACH_IDLE_S = 3600        # HEAD unmoved this long before a reattach (#1176 census)
# a session mid-operation (merge / rebase / bisect …) or an unreadable git dir
# is never told to merge the base (#1176 census review)
NO_NOTICE_REASONS = ("in-progress", "git-dir-unmeasurable")
# long-lived branches that are never "a work branch" to switch away from, even
# when a checkout's declared base is another one (gk-infra declares develop)
LONG_LIVED_BRANCHES = DEFAULT_BASES + ("staging",)


# --------------------------------------------------------------------------- #
# bounded git runner
# --------------------------------------------------------------------------- #

def _log(msg):
    print("checkout-freshness: %s" % msg, file=sys.stderr)


def git_env(extra=None):
    """Non-interactive git (never a credential prompt), no optional index
    writes by read commands (GIT_OPTIONAL_LOCKS=0)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.setdefault("LC_ALL", "C")
    env.update(extra or {})
    return env


@contextlib.contextmanager
def deadline(seconds):
    """Clip every `run_git` inside the block to `seconds` from now — or to an
    enclosing deadline when that one is sooner (a nested block can only
    SHORTEN the budget, never extend it)."""
    prev = _DEADLINE[0]
    mine = time.monotonic() + seconds
    _DEADLINE[0] = mine if prev is None else min(prev, mine)
    try:
        yield
    finally:
        _DEADLINE[0] = prev


def time_left():
    """Seconds to the active deadline, or None when none is set."""
    d = _DEADLINE[0]
    return None if d is None else d - time.monotonic()


def _record(wall):
    """Count the call in the watchdog's per-sweep subprocess budget when the
    watchdog is the caller (never imports it: the footer stays light)."""
    budget = sys.modules.get("watchdog.subprocess_budget")
    if budget is not None:
        try:
            budget.record_subprocess("git", wall)
        except Exception as exc:  # noqa: BLE001 -- accounting never breaks git
            _log("subprocess budget record failed: %s" % exc)


def _verb(args):
    """The git subcommand for a log line (skips leading `-c k=v` pairs)."""
    rest = list(args)
    while rest[:1] == ["-c"]:
        rest = rest[2:]
    return rest[0] if rest else "?"


def _stop_group(proc, args, cwd):
    """SIGTERM the child's process group (git removes its lock files), wait
    TERM_GRACE_S, then SIGKILL whatever is left."""
    for sig, wait in ((signal.SIGTERM, TERM_GRACE_S), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except OSError as exc:
            _log("killpg %s %s: %s" % (proc.pid, sig, exc))
        try:
            proc.communicate(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            _log("git %s in %s still alive %ss after %s" % (_verb(args), cwd, wait, sig))


def run_git(cwd, args, timeout=GIT_LOCAL_TIMEOUT_S, env_extra=None,
            honor_deadline=True):
    """Run `git <args>` in `cwd`; returns `(rc, stdout)`. `timeout=None` =
    unbounded, still clipped by an active `deadline()` unless
    `honor_deadline=False` (the one caller: a started fast-forward, which
    must never be signalled half-way — git does not roll back the files it
    already wrote). Own process group; on timeout the WHOLE group is stopped
    (SIGTERM, then SIGKILL), so a `git fetch` whose ssh / https helper holds
    the pipe open can never outlive the bound. A timeout returns rc 124, a
    spawn failure rc 127; never raises."""
    left = time_left() if honor_deadline else None
    if left is not None:
        timeout = max(1.0, left) if timeout is None else max(1.0, min(timeout, left))
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            ["git"] + list(args), cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=git_env(env_extra), start_new_session=True)
    except OSError as exc:
        _log("git %s in %s could not start: %s" % (_verb(args), cwd, exc))
        return 127, ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _log("git %s in %s timed out after %.0fs — stopping its process group"
             % (_verb(args), cwd, timeout))
        _stop_group(proc, args, cwd)
        _record(time.monotonic() - started)
        return 124, ""
    _record(time.monotonic() - started)
    return proc.returncode, out.decode("utf-8", "replace")


def ssh_batch_env(cwd):
    """BatchMode ssh for an unattended fetch — unless the environment or the
    repo (`core.sshCommand`) already chose an ssh command (a deploy key)."""
    if os.environ.get("GIT_SSH_COMMAND") or os.environ.get("GIT_SSH"):
        return {}
    rc, out = run_git(cwd, ["config", "--get", "core.sshCommand"])
    if rc == 0 and out.strip():
        return {}
    return {"GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5"}


def _git_dir(cwd):
    rc, out = run_git(cwd, ["rev-parse", "--absolute-git-dir"])
    return out.strip() if rc == 0 and out.strip() else None


def current_branch(cwd):
    """The checked-out branch name, or None (detached / not a repo)."""
    rc, out = run_git(cwd, ["symbolic-ref", "--short", "-q", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else None


def remote_ref(remote, branch):
    """The FULL remote-tracking ref (never the short `origin/x`, which a local
    branch literally named `origin/x` would shadow)."""
    return "refs/remotes/%s/%s" % (remote, branch)


def ref_exists(cwd, ref):
    rc, _ = run_git(cwd, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return rc == 0


def configured_remotes(cwd):
    rc, out = run_git(cwd, ["remote"])
    return set(out.split()) if rc == 0 else set()


def pick_remote(cwd, branch, bases=()):
    """The remote the checkout's BASE lives on — ONE rule for the hook and
    the job: `upstream` when configured and it carries the base (the
    checked-out base branch, else the first of `bases` it has) — a fork
    stream's real base, never the fork's own copy, exactly as
    session-start-stream-directives.sh resolves it; else the tracking
    remote of the branch / of a local base branch; else `origin`; else any
    remote; else None."""
    remotes = configured_remotes(cwd)
    candidates = ([branch] if branch in bases else []) + [b for b in bases if b != branch]
    if "upstream" in remotes:
        for b in candidates[:1] if branch in bases else candidates:
            if ref_exists(cwd, remote_ref("upstream", b)):
                return "upstream"
    for b in candidates:
        rc, out = run_git(cwd, ["config", "--get", "branch.%s.remote" % b])
        if rc == 0 and out.strip() in remotes:
            return out.strip()
    if "origin" in remotes:
        return "origin"
    return sorted(remotes)[0] if remotes else None


def count_behind(cwd, ref):
    """Commits in `ref` not in HEAD, or None when unmeasurable."""
    rc, out = run_git(cwd, ["rev-list", "--count", "HEAD.." + ref])
    try:
        return int(out.strip()) if rc == 0 else None
    except ValueError:
        return None


def worktree_status(cwd):
    """`git status --porcelain` text, or UNMEASURABLE when the command itself
    failed (an unreadable index) — never guessed clean (#314 F4)."""
    rc, out = run_git(cwd, ["status", "--porcelain"])
    return out if rc == 0 else UNMEASURABLE


def in_progress_state(cwd):
    """The first in-progress operation marker present in the git dir, None
    when there is none, or UNMEASURABLE when the git dir cannot be read (fail
    closed). Checked BEFORE the branch lookup: HEAD may be detached mid-op."""
    gd = _git_dir(cwd)
    if not gd:
        return UNMEASURABLE
    for state in IN_PROGRESS_STATES:
        if os.path.exists(os.path.join(gd, state)):
            return state
    return None


def _z_paths(cwd, args):
    rc, out = run_git(cwd, args)
    if rc != 0:
        return None
    return [p for p in out.split("\0") if p]


def colliding_paths(cwd, ref):
    """Incoming paths (added by `ref` relative to HEAD, renames OFF, NUL
    names so quoting never hides one) that would land on an existing local
    untracked or ignored file: the path itself, or a PARENT that exists as a
    non-directory HEAD does not track away. Unmeasurable = a collision (fail
    closed)."""
    added = _z_paths(cwd, ["diff", "--no-renames", "-z", "--name-only",
                           "--diff-filter=A", "HEAD", ref])
    deleted = _z_paths(cwd, ["diff", "--no-renames", "-z", "--name-only",
                             "--diff-filter=D", "HEAD", ref])
    if added is None or deleted is None:
        return ["<diff unmeasurable>"]
    gone = set(deleted)
    hits = []
    for p in added:
        if os.path.lexists(os.path.join(cwd, p)):
            hits.append(p)
            continue
        parts = p.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            full = os.path.join(cwd, parent)
            if parent not in gone and os.path.lexists(full) and (
                    os.path.islink(full) or not os.path.isdir(full)):
                hits.append(parent)
                break
    return sorted(set(hits))


# --------------------------------------------------------------------------- #
# the shared safety predicate
# --------------------------------------------------------------------------- #

class Verdict(object):
    """`action`: "ff" (provably safe, fast-forward to `ref`), "noop" (nothing
    to do: up to date, detached, no target) or "refuse" (unsafe — `reason`
    says why). `target` is the display name (`origin/main`), `ref` the full
    ref. `behind` is None when unmeasured."""
    __slots__ = ("action", "reason", "branch", "target", "ref", "behind", "detail")

    def __init__(self, action, reason, branch=None, target=None, ref=None,
                 behind=None, detail=None):
        self.action = action
        self.reason = reason
        self.branch = branch
        self.target = target
        self.ref = ref
        self.behind = behind
        self.detail = detail

    def __repr__(self):
        return "Verdict(%s, %s, %s->%s, behind=%s)" % (
            self.action, self.reason, self.branch, self.target, self.behind)


def ff_verdict(cwd, remote="origin"):
    """Is fast-forwarding the CURRENT branch of `cwd` to `<remote>/<branch>`
    provably safe? The one predicate the hook and the watchdog job share;
    reads only (no fetch — the caller fetched). Order matters and is the #314
    order: in-progress op, detached HEAD, target, behind, tree state,
    ancestry, path collision."""
    state = in_progress_state(cwd)
    if state is UNMEASURABLE:
        return Verdict("refuse", "git-dir-unmeasurable")
    if state:
        return Verdict("refuse", "in-progress", detail=state)
    branch = current_branch(cwd)
    if not branch:
        return Verdict("noop", "detached")
    target, ref = "%s/%s" % (remote, branch), remote_ref(remote, branch)
    if not ref_exists(cwd, ref):
        return Verdict("noop", "no-target", branch, target, ref)
    behind = count_behind(cwd, ref)
    if behind is None:
        return Verdict("noop", "behind-unmeasurable", branch, target, ref)
    if behind == 0:
        return Verdict("noop", "up-to-date", branch, target, ref, 0)
    status = worktree_status(cwd)
    if status is UNMEASURABLE:
        return Verdict("refuse", "unmeasurable", branch, target, ref, behind)
    if status.strip():
        return Verdict("refuse", "dirty", branch, target, ref, behind)
    rc, _ = run_git(cwd, ["merge-base", "--is-ancestor", "HEAD", ref])
    if rc != 0:
        return Verdict("refuse", "diverged", branch, target, ref, behind)
    collide = colliding_paths(cwd, ref)
    if collide:
        return Verdict("refuse", "collision", branch, target, ref, behind,
                       detail=collide)
    return Verdict("ff", "safe", branch, target, ref, behind)


def fast_forward(cwd, verdict, run_hooks=True):
    """Apply a `ff` verdict with `git merge --ff-only` (a hard net of its own:
    it only ever moves the ref along its own history and refuses rather than
    overwrite a file edited since the check). NEVER bounded or signalled once
    started: git does not roll back the files a checkout already wrote, so a
    stopped merge would leave a half-applied tree (review 2). The CALLER
    decides whether it can afford to start one. `run_hooks=False` skips the
    repo's merge hooks for an unattended caller. True on success."""
    if verdict.action != "ff":
        return False
    pre = [] if run_hooks else ["-c", "core.hooksPath=/dev/null"]
    rc, _ = run_git(cwd, pre + ["merge", "--ff-only", "--quiet", verdict.ref],
                    timeout=None, honor_deadline=False)
    if rc != 0:
        _log("merge --ff-only of %s in %s failed (rc %d)" % (verdict.ref, cwd, rc))
    return rc == 0


def rule_lag(cwd, ref):
    """Rule files (`CLAUDE.md`, `.claude/`, nested too) `ref` changed since
    HEAD forked from it — `[paths]`, or None when unmeasurable."""
    rc, out = run_git(cwd, ["diff", "--name-only", "HEAD..." + ref, "--"]
                      + list(RULE_PATHS))
    return [p for p in out.splitlines() if p] if rc == 0 else None


def base_on_remote(cwd, remote, branch, bases=DEFAULT_BASES):
    """The base a checkout on `branch` (None = detached) compares with: the
    branch itself when it is a base, else the first of `bases` the remote
    carries; None when there is none."""
    if branch in bases:
        return branch
    for b in bases:
        if ref_exists(cwd, remote_ref(remote, b)):
            return b
    return None


def head_short(cwd):
    """The abbreviated HEAD sha, or None."""
    rc, out = run_git(cwd, ["rev-parse", "--short", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else None


def notice_line(branch, n, remote, base, why=None, sha=None):
    """The ONE rule-lag notice (#1176 census item 3/4): the SessionStart hook
    prints it and Job 53 types it into the stream's own pane — same words.
    `why` is the refusal that keeps a BASE branch behind (it cannot switch to
    itself): a dirty tree is told to commit / stash first, a diverged one or a
    path collision only to merge. A DETACHED checkout (`branch` None, `sha` its
    HEAD) is told the exact way back and not to restore an old SHA after tests
    (montalu1 30.9.: the session kept re-detaching onto a 2-day-old SHA)."""
    noun, verb = (("súbor", "je") if n == 1 else ("súbory", "sú") if 2 <= n <= 4
                  else ("súborov", "je"))
    if not branch:
        return ("checkout-freshness: tvoj checkout je detached na %s (bez vetvy), %d %s "
                "s pravidlami (CLAUDE.md / .claude/) %s pozadu za %s/%s — spusti "
                "`git checkout %s && git merge --ff-only %s/%s` a po testoch "
                "neobnovuj staré SHA, vráť sa na %s." % (
                    sha or "?", n, noun, verb, remote, base, base, remote, base, base))
    on = "vetve %s" % branch
    if branch == base and why in (None, "dirty", "unmeasurable"):
        fix = "commitni alebo odlož lokálne zmeny a zmerguj %s/%s" % (remote, base)
    elif branch == base:
        fix = "zmerguj %s/%s (vlastné commity alebo lokálne súbory v ceste)" % (
            remote, base)
    else:
        fix = "zmerguj %s/%s do vetvy alebo prejdi na %s" % (remote, base, base)
    return ("checkout-freshness: tvoj checkout je na %s, %d %s s pravidlami "
            "(CLAUDE.md / .claude/) %s pozadu za %s/%s — %s." % (
                on, n, noun, verb, remote, base, fix))


def reattach_line(branch, base):
    """What Job 53 tells the session after it moved the checkout onto `base`
    (#1176 census review: a reattach is never silent)."""
    was = "vetvy %s (tá ostala zachovaná)" % branch if branch else "detached HEAD"
    return ("checkout-freshness: presunul som tvoj checkout z %s na %s a posunul ho "
            "na aktuálne pravidlá — ďalšie commity pôjdu na %s, na vlastnú prácu "
            "si vytvor vetvu." % (was, base, base))


def base_in_other_worktree(cwd, base):
    """True when `refs/heads/<base>` is checked out in ANOTHER worktree of the
    same repository (git refuses both the local fetch into it and the checkout
    — a reattach there could only fail, forever); None when unmeasurable."""
    rc, out = run_git(cwd, ["worktree", "list", "--porcelain"])
    if rc != 0:
        return None
    here, path = os.path.realpath(cwd), None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = os.path.realpath(line[len("worktree "):])
        elif line == "branch refs/heads/%s" % base and path != here:
            return True
    return False


def _head_moved_at(cwd):
    """When HEAD last moved (checkout / commit / reset): the time of its newest
    REFLOG entry (`%gd` with `--date=unix` -> `HEAD@{<epoch>}`; `%ct` would be
    the commit's own date); None when unmeasurable (no reflog)."""
    rc, out = run_git(cwd, ["log", "-g", "-1", "--date=unix", "--format=%gd", "HEAD"])
    sel = out.strip()
    if rc != 0 or not (sel.endswith("}") and "@{" in sel):
        return None
    try:
        return int(sel[sel.rindex("@{") + 2:-1])
    except ValueError:
        return None


def reattach_verdict(cwd, remote, base, now=None, idle_s=REATTACH_IDLE_S):
    """Is moving a DETACHED HEAD or a WORK branch onto `base` and
    fast-forwarding it provably safe (#1176 census items 1+2)? The `ff_verdict`
    refusals (in-progress, dirty / unmeasurable tree, collision) plus: HEAD is
    an ancestor of `<remote>/<base>` (no commit outside the base: nothing is
    left behind), the local base is not ahead of it (a local fetch can
    fast-forward it), and HEAD has been idle for `idle_s` (a session that just
    ran `checkout -b` is never switched back under its feet). Action
    "reattach" (`detail` = the base), "noop" or "refuse"."""
    state = in_progress_state(cwd)
    if state is UNMEASURABLE:
        return Verdict("refuse", "git-dir-unmeasurable")
    if state:
        return Verdict("refuse", "in-progress", detail=state)
    branch = current_branch(cwd)
    target, ref = "%s/%s" % (remote, base), remote_ref(remote, base)
    if branch == base:
        return Verdict("noop", "on-base", branch, target, ref)
    if branch in LONG_LIVED_BRANCHES:
        return Verdict("refuse", "long-lived-branch", branch, target, ref,
                       detail="%s is never switched away from" % branch)
    if not ref_exists(cwd, ref):
        return Verdict("noop", "no-target", branch, target, ref)
    behind = count_behind(cwd, ref)
    if not behind:
        return Verdict("noop", "up-to-date" if behind == 0 else "behind-unmeasurable",
                       branch, target, ref, behind)
    status = worktree_status(cwd)
    if status is UNMEASURABLE or status.strip():
        return Verdict("refuse", "dirty" if status is not UNMEASURABLE else "unmeasurable",
                       branch, target, ref, behind)
    rc, out = run_git(cwd, ["rev-list", "--count", ref + "..HEAD"])
    if rc != 0 or out.strip() != "0":
        return Verdict("refuse", "unmerged", branch, target, ref, behind,
                       detail="%s commit(s) outside %s" % (out.strip() or "?", target))
    local = "refs/heads/" + base
    if ref_exists(cwd, local):
        rc, out = run_git(cwd, ["rev-list", "--count", ref + ".." + local])
        if rc != 0 or out.strip() != "0":
            return Verdict("refuse", "local-base-ahead", branch, target, ref, behind,
                           detail="local %s is ahead of %s" % (base, target))
    other = base_in_other_worktree(cwd, base)
    if other is not False:
        return Verdict("refuse", "base-in-other-worktree", branch, target, ref, behind,
                       detail="%s is checked out in another worktree" % base
                       if other else "worktree list unmeasurable")
    collide = colliding_paths(cwd, ref)
    if collide:
        return Verdict("refuse", "collision", branch, target, ref, behind, detail=collide)
    moved = _head_moved_at(cwd)
    if moved is None:
        return Verdict("refuse", "head-activity-unmeasurable", branch, target, ref, behind,
                       detail="no HEAD reflog")
    now = time.time() if now is None else now
    if now - moved < idle_s:
        return Verdict("refuse", "recently-active", branch, target, ref, behind,
                       detail="HEAD moved %ds ago" % (now - moved))
    return Verdict("reattach", "safe", branch, target, ref, behind, detail=base)


def reattach(cwd, verdict):
    """Apply a `reattach` verdict: fast-forward the LOCAL base to the remote
    base with a local fetch (`git fetch . <ref>:refs/heads/<base>` refuses
    anything but a fast-forward), then `git checkout <base>` with hooks off.
    The old branch ref is kept. Never bounded or signalled once started (the
    `fast_forward` rule). True on success."""
    if verdict.action != "reattach":
        return False
    base = verdict.detail
    rc, _ = run_git(cwd, ["-c", "gc.auto=0", "-c", "maintenance.auto=false",
                          "-c", "core.hooksPath=/dev/null", "fetch", "--quiet",
                          "--no-write-fetch-head", "--no-recurse-submodules", ".",
                          "%s:refs/heads/%s" % (verdict.ref, base)],
                    timeout=None, honor_deadline=False)
    if rc != 0:
        _log("local fast-forward of %s to %s in %s failed (rc %d)" % (base, verdict.ref, cwd, rc))
        return False
    rc, _ = run_git(cwd, ["-c", "core.hooksPath=/dev/null", "checkout", "--quiet", base, "--"],
                    timeout=None, honor_deadline=False)
    if rc != 0:
        _log("checkout of %s in %s failed (rc %d)" % (base, cwd, rc))
        return False
    rc, _ = run_git(cwd, ["config", "--get", "branch.%s.remote" % base])
    if rc != 0 and verdict.ref.startswith("refs/remotes/origin/"):
        # a base the local fetch just created tracks origin; never another
        # remote (a fork's `upstream` would make a bare push target the project)
        run_git(cwd, ["branch", "--quiet", "--set-upstream-to=%s" % verdict.ref, base])
    return True


def write_json_atomic(path, data):
    """Atomic JSON write (temp + os.replace) — a reader never sees half a file;
    a failed replace removes its temp file and re-raises."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    try:
        os.replace(tmp, path)
    except OSError:
        os.unlink(tmp)
        raise


def _child_apply(path, remote, mode, base):
    """Re-check + apply for `ff_child`: `(ok, reason, commits)`."""
    v = reattach_verdict(path, remote, base) if mode == "reattach" \
        else ff_verdict(path, remote)
    if v.action == "noop" and v.reason == "up-to-date" and mode == "ff":
        return True, "already up-to-date", 0
    if v.action != ("reattach" if mode == "reattach" else "ff"):
        return False, "re-check refused: %s" % v.reason, v.behind
    if mode == "reattach":
        ok = reattach(path, v)
        return ok, ("reattached to %s" % base) if ok else "reattach failed", v.behind
    ok = fast_forward(path, v, run_hooks=False)
    return ok, "fast-forwarded" if ok else "merge --ff-only failed", v.behind


def ff_child(path, remote, branch, result_path, mode="ff", base=None):
    """The DETACHED fast-forward (#1176 reopen): what the watchdog's transient
    `systemd-run --user` unit runs, outside the watchdog unit's 120 s kill, so
    no sweep reserve is needed. The job already proved the verdict; this
    re-checks it right before acting (the checkout may have changed in
    between — a branch switch, an edit). `mode` "ff" merges the checked-out
    base with hooks OFF; "reattach" (#1176 census) moves a detached HEAD
    (`branch` "") or a merged work branch onto `base`. Writes `{ok, reason,
    commits, branch, mode, base, started, finished}` to `result_path` for the
    next sweep to collect. rc 0 on success, 1 otherwise; never raises."""
    started = time.time()
    commits = None
    base = base or branch
    try:
        now_on = current_branch(path)
        if (now_on or "") != (branch or ""):
            ok, reason = False, "branch changed since the verdict (on %s, expected %s)" % (
                now_on or "detached HEAD", branch or "detached HEAD")
        else:
            ok, reason, commits = _child_apply(path, remote, mode, base)
    except Exception as exc:  # noqa: BLE001 -- recorded in the result, never lost
        ok, reason = False, "child error: %s" % exc
    _log("%s [%s] detached %s: %s (%s)" % (
        path, branch or "detached HEAD", mode, "ok" if ok else "FAILED", reason))
    try:
        write_json_atomic(result_path, {"ok": ok, "reason": reason, "commits": commits,
                                        "branch": branch, "mode": mode, "base": base,
                                        "started": started, "finished": time.time()})
    except OSError as exc:   # the sweep then reports "left no result"
        _log("could not write the result %s: %s" % (result_path, exc))
        return 1
    return 0 if ok else 1


def _ff_main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="cli_checkout_freshness.py ff")
    for opt in ("--path", "--remote", "--branch", "--result"):
        ap.add_argument(opt, required=True)
    ap.add_argument("--mode", choices=("ff", "reattach"), default="ff")
    ap.add_argument("--base", default=None)
    a = ap.parse_args(argv)
    return ff_child(a.path, a.remote, a.branch, a.result, a.mode, a.base)


def hook_line(verdict, applied=None):
    """The #314 SessionStart line for a verdict (empty = print nothing). The
    wording is the hook's historical contract (tests assert it)."""
    b, t, n = verdict.branch, verdict.target, verdict.behind
    r = verdict.reason
    if r == "in-progress" and verdict.detail == "index.lock":
        return ("WARNING: another git process holds the index lock "
                "(.git/index.lock) — leaving the repository untouched")
    if r == "in-progress":
        return ("WARNING: repository has an in-progress git operation "
                "(merge/rebase/cherry-pick/revert/bisect) — leaving it untouched")
    if r == "git-dir-unmeasurable":
        return "WARNING: could not read the git directory — leaving it untouched"
    if verdict.action == "noop":
        return ""
    head = "WARNING: Branch '%s' is %s commit(s) behind %s" % (b, n, t)
    if r == "unmeasurable":
        return head + (" (could not determine working tree state — "
                       "not fast-forwarding)")
    if r == "dirty":
        return head + " (working tree dirty — not fast-forwarding)"
    if r == "diverged":
        return ("WARNING: Branch '%s' has diverged from %s (%s commit(s) "
                "behind) — not fast-forwarding" % (b, t, n))
    if r == "collision":
        return head + (" (origin adds file(s) that already exist locally: %s"
                       " — not fast-forwarding)" % " ".join(verdict.detail))
    if applied:
        return "Fast-forwarded '%s' to %s (%s commit(s))" % (b, t, n)
    return head + " (fast-forward attempt failed)"


def hook_main(cwd=None):
    """The SessionStart hook's ff step (the caller already fetched origin),
    then — whenever the checkout is still not on a current base (a work
    branch, a detached HEAD, a refused fast-forward) — the #1176 census
    rule-lag notice, the SAME line Job 53 types into the stream's pane."""
    cwd = cwd or os.getcwd()
    branch = current_branch(cwd)
    remote = pick_remote(cwd, branch, [branch]) if branch else None
    v = ff_verdict(cwd, remote or "origin")
    applied = fast_forward(cwd, v) if v.action == "ff" else None
    line = hook_line(v, applied)
    if line:
        print(line)
    if not applied and v.reason not in NO_NOTICE_REASONS:
        notice = hook_notice(cwd, branch, v.reason if v.action == "refuse" else None)
        if notice:
            print(notice)
    return 0


def hook_notice(cwd, branch, why=None):
    """The rule-lag notice for this checkout, or "" (no remote / no base /
    no rule file behind / unmeasurable)."""
    bases = ([branch] if branch in DEFAULT_BASES else []) + list(DEFAULT_BASES)
    remote = pick_remote(cwd, branch, bases)
    base = base_on_remote(cwd, remote, branch) if remote else None
    if not base or not ref_exists(cwd, remote_ref(remote, base)):
        return ""
    files = rule_lag(cwd, remote_ref(remote, base))
    if not files:
        return ""
    return notice_line(branch, len(files), remote, base, why,
                       sha=None if branch else head_short(cwd))


# --------------------------------------------------------------------------- #
# status file readers (footer + `airuleset.py status`)
# --------------------------------------------------------------------------- #

def status_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), STATUS_REL)


def read_status(home=None):
    """The job's status dict, or None (absent / unreadable / not a dict)."""
    try:
        with open(status_path(home), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        _log("status file unreadable: %s" % exc)
        return None
    return data if isinstance(data, dict) else None


def stale_after_s():
    """N hours before an unfixable lag turns visible (env
    AIRULESET_STALE_CHECKOUT_H, default 6; a bad or non-positive value falls
    back to the default)."""
    try:
        hours = float(os.environ.get("AIRULESET_STALE_CHECKOUT_H",
                                     STALE_AFTER_H_DEFAULT))
    except ValueError:
        hours = STALE_AFTER_H_DEFAULT
    if not hours > 0:
        hours = STALE_AFTER_H_DEFAULT
    return hours * 3600


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def stale_entries(status, now, after_s=None):
    """Checkouts observed `lagging` (could not be made current) continuously
    for >= `after_s`, measured from the FIRST unfixable observation (`since`)
    — never from a commit date, which says nothing about when the checkout
    fell behind. Worst (most commits behind) first."""
    after_s = stale_after_s() if after_s is None else after_s
    rows = []
    for path, e in ((status or {}).get("checkouts") or {}).items():
        if not isinstance(e, dict) or e.get("state") != "lagging":
            continue
        since = _num(e.get("since"))
        if since is not None and now - since >= after_s:
            rows.append((path, e))
    rows.sort(key=lambda r: -(_num(r[1].get("behind")) or 0))
    return rows



def _age(now, ts):
    if not isinstance(ts, (int, float)):
        return "?"
    h = (now - ts) / 3600.0
    return "%.1fh" % h if h < 48 else "%dd" % int(h // 24)


def status_lines(home=None, now=None):
    """`airuleset.py status` rows: one summary line, then one line per
    checkout that is not current (path, branch -> base, behind, reason, age).
    Empty list when the job never ran on this box."""
    now = time.time() if now is None else now
    status = read_status(home)
    if not status:
        return []
    cos = status.get("checkouts") or {}
    odd = [(p, e) for p, e in sorted(cos.items())
           if isinstance(e, dict) and e.get("state") != "current"]
    lag = [p for p, e in odd if e.get("state") == "lagging"]
    stale = {p for p, _ in stale_entries(status, now)}
    out = ["checkout-freshness: %d checkout(s), %d lagging, %d stale (>%gh), "
           "checked %s ago" % (len(cos), len(lag), len(stale),
                               stale_after_s() / 3600, _age(now, status.get("ts")))]
    for p, e in odd:
        tag = "" if e.get("state") == "lagging" else "(%s) " % e.get("state")
        out.append("  %s%s%s [%s -> %s] behind %s: %s (since %s)" % (
            "STALE " if p in stale else "", tag, p,
            e.get("branch") or "detached", e.get("base") or "?",
            e.get("behind"), e.get("reason"), _age(now, e.get("since"))))
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["hook"]:
        sys.exit(hook_main())
    if sys.argv[1:2] == ["ff"]:
        sys.exit(_ff_main(sys.argv[2:]))
    print("usage: python3 cli_checkout_freshness.py hook | ff --path P --remote R "
          "--branch B --result F", file=sys.stderr)
    sys.exit(2)
