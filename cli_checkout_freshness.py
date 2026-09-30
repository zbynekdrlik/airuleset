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

The same module also READS the job's status file for the two visibility
surfaces, so the per-prompt footer never imports the 390 KB `watchdog`
package: `footer_segment` (`stale <N>`) and `status_lines` (`airuleset.py
status`).

SAFETY (never relaxed): fast-forward ONLY (`git merge --ff-only`), never a
reset / checkout -f / merge commit / rebase. Refused, and only reported:
an in-progress operation (merge, rebase, cherry-pick, revert, bisect,
sequencer, a held `index.lock`), an unreadable git dir, a detached HEAD, a
dirty or UNMEASURABLE working tree, a HEAD that is not an ancestor of the
target (diverged), and an incoming path that would land on an existing
untracked or ignored file — the path itself OR any of its parent
directories, with renames off and NUL-separated names (#314 F1). `--ff-only`
itself also refuses to overwrite a file edited between the check and the
merge.

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
STALE_AFTER_H_DEFAULT = 6     # footer `stale N` only past this many hours
STATUS_DEAD_S = 2 * 3600      # status older than this = dead watchdog, hide
UNMEASURABLE = object()
_DEADLINE = [None]            # monotonic deadline set by `deadline()`, or None


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


def write_json_atomic(path, data):
    """Atomic JSON write (temp + os.replace) — a reader never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def ff_child(path, remote, branch, result_path):
    """The DETACHED fast-forward (#1176 reopen): what the watchdog's transient
    `systemd-run --user` unit runs, outside the watchdog unit's 120 s kill, so
    no sweep reserve is needed. The job already proved the `ff` verdict; this
    re-checks it right before the merge (the checkout may have changed in
    between — a branch switch, an edit), merges with hooks OFF, and writes
    `{ok, reason, commits, branch, started, finished}` to `result_path` for
    the next sweep to collect. rc 0 on success, 1 otherwise; never raises."""
    started = time.time()
    commits = None
    try:
        now_on = current_branch(path)
        if now_on != branch:
            ok, reason = False, "branch changed since the verdict (on %s, expected %s)" % (
                now_on or "detached HEAD", branch)
        else:
            v = ff_verdict(path, remote)
            commits = v.behind
            if v.action == "noop" and v.reason == "up-to-date":
                ok, reason = True, "already up-to-date"
            elif v.action != "ff":
                ok, reason = False, "re-check refused: %s" % v.reason
            else:
                ok = fast_forward(path, v, run_hooks=False)
                reason = "fast-forwarded" if ok else "merge --ff-only failed"
    except Exception as exc:  # noqa: BLE001 -- recorded in the result, never lost
        ok, reason = False, "child error: %s" % exc
    _log("%s [%s] detached fast-forward: %s (%s)" % (
        path, branch, "ok" if ok else "FAILED", reason))
    write_json_atomic(result_path, {"ok": ok, "reason": reason, "commits": commits,
                                    "branch": branch, "started": started,
                                    "finished": time.time()})
    return 0 if ok else 1


def _ff_main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="cli_checkout_freshness.py ff")
    for opt in ("--path", "--remote", "--branch", "--result"):
        ap.add_argument(opt, required=True)
    a = ap.parse_args(argv)
    return ff_child(a.path, a.remote, a.branch, a.result)


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
    """The SessionStart hook's ff step (the caller already fetched origin)."""
    cwd = cwd or os.getcwd()
    branch = current_branch(cwd)
    remote = pick_remote(cwd, branch, [branch]) if branch else None
    v = ff_verdict(cwd, remote or "origin")
    applied = fast_forward(cwd, v) if v.action == "ff" else None
    line = hook_line(v, applied)
    if line:
        print(line)
    return 0


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


def compact_count(n):
    """2600 -> '2.6k', 12 -> '12', 0 / None -> '?' (lag not in commits: a
    failing fetch, a rule-file lag on an up-to-date work branch)."""
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return "?"
    return "%.1fk" % (n / 1000.0) if n >= 1000 else str(n)


def footer_segment(home=None, now=None):
    """`stale <N>` (N = commits behind of the worst checkout, `+K` more) when
    a checkout has lagged unfixably past the threshold; "" otherwise, and ""
    when the status is older than STATUS_DEAD_S (a dead watchdog never paints
    a frozen count). Local file read only; never raises."""
    try:
        now = time.time() if now is None else now
        status = read_status(home)
        ts = (status or {}).get("ts")
        if not isinstance(ts, (int, float)) or now - ts > STATUS_DEAD_S:
            return ""
        rows = stale_entries(status, now)
        if not rows:
            return ""
        more = " +%d" % (len(rows) - 1) if len(rows) > 1 else ""
        return "\033[38;5;208mstale %s%s\033[0m" % (
            compact_count(rows[0][1].get("behind")), more)
    except Exception as exc:  # noqa: BLE001 -- a footer never breaks render
        _log("footer segment failed: %s" % exc)
        return ""


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
