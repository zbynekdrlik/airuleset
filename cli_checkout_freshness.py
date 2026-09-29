"""Checkout freshness: the ONE shared fast-forward safety predicate (#1176).

Two callers, one definition of "provably safe to fast-forward":

  * `hooks/session-start-fetch.sh` (SessionStart startup + resume) runs
    `python3 -m cli_checkout_freshness hook` after its own `git fetch origin`
    and prints the line this module composes (the #314 wording, unchanged).
  * the watchdog Job 53 leaf `watchdog/checkout_freshness.py` calls
    `ff_verdict` / `fast_forward` for every managed checkout of the box on a
    15-min cadence, so freshness no longer depends on a session-start event
    (the #1127 recurrence: every managed session starts with `claude -c`, a
    `resume` source, and a session can live for days).

The same module also READS the job's status file for the two visibility
surfaces, so the per-prompt footer never imports the 390 KB `watchdog`
package: `footer_segment` (`stale <N>`) and `status_lines` (`airuleset.py
status`).

SAFETY (never relaxed): fast-forward ONLY (`git merge --ff-only`), never a
reset / checkout -f / merge commit / rebase. Refused, and only reported:
an in-progress operation (merge, rebase, cherry-pick, revert, bisect,
sequencer), a detached HEAD, a dirty or UNMEASURABLE working tree, a HEAD
that is not an ancestor of the target (diverged), and an origin commit that
adds a path already present on disk (an ignored or untracked local file would
be silently clobbered, #314 F1). `--ff-only` itself also refuses to overwrite
a file edited between the check and the merge.

stdlib only; imports nothing from this repo.
"""
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
GIT_LOCAL_TIMEOUT_S = 10      # any local git read / the ff merge itself
STATUS_REL = os.path.join(".claude", "checkout-freshness", "status.json")
STALE_AFTER_H_DEFAULT = 6     # footer `stale N` only past this many hours
STATUS_DEAD_S = 2 * 3600      # status older than this = dead watchdog, hide
MIN_OBSERVED_S = 900          # unfixable for >= one job period before `stale`
UNMEASURABLE = object()


# --------------------------------------------------------------------------- #
# bounded git runner
# --------------------------------------------------------------------------- #

def git_env():
    """Non-interactive git: never a credential prompt, never an ssh password
    prompt (unless the user configured their own ssh command)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("LC_ALL", "C")
    if not env.get("GIT_SSH_COMMAND") and not env.get("GIT_SSH"):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=5"
    return env


def _log(msg):
    print("checkout-freshness: %s" % msg, file=sys.stderr)


def run_git(cwd, args, timeout=GIT_LOCAL_TIMEOUT_S):
    """Run `git <args>` in `cwd`; returns `(rc, stdout)`. The child runs in
    its OWN process group and the WHOLE group is SIGKILLed on timeout, so a
    `git fetch` whose ssh / https helper grandchild holds the pipe open can
    never outlive the bound (a plain `subprocess.run(timeout=)` kills only
    the direct child and then blocks reading the inherited pipe). A timeout
    returns rc 124, a spawn failure rc 127; never raises."""
    try:
        proc = subprocess.Popen(
            ["git"] + list(args), cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=git_env(), start_new_session=True)
    except OSError as exc:
        _log("git %s in %s could not start: %s" % (args[0], cwd, exc))
        return 127, ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _log("git %s in %s timed out after %ss — killing its process group"
             % (args[0], cwd, timeout))
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError as exc:
            _log("killpg %s: %s" % (proc.pid, exc))
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            _log("git %s in %s still not reaped after SIGKILL" % (args[0], cwd))
        return 124, ""
    return proc.returncode, out.decode("utf-8", "replace")


def _git_dir(cwd):
    rc, out = run_git(cwd, ["rev-parse", "--absolute-git-dir"])
    return out.strip() if rc == 0 and out.strip() else None


def current_branch(cwd):
    """The checked-out branch name, or None (detached / not a repo)."""
    rc, out = run_git(cwd, ["symbolic-ref", "--short", "-q", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else None


def ref_exists(cwd, ref):
    rc, _ = run_git(cwd, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    return rc == 0


def count_behind(cwd, target):
    """Commits in `target` not in HEAD, or None when unmeasurable."""
    rc, out = run_git(cwd, ["rev-list", "--count", "HEAD.." + target])
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
    """The first in-progress operation marker present in the git dir, else
    None. Checked BEFORE the branch lookup: HEAD may be detached mid-op."""
    gd = _git_dir(cwd)
    if not gd:
        return None
    for state in IN_PROGRESS_STATES:
        if os.path.exists(os.path.join(gd, state)):
            return state
    return None


def colliding_paths(cwd, target):
    """Paths `target` ADDS relative to HEAD that already exist on disk. An
    unmeasurable diff is returned as a collision (fail closed)."""
    rc, out = run_git(cwd, ["diff", "--name-only", "--diff-filter=A",
                            "HEAD", target])
    if rc != 0:
        return ["<diff unmeasurable>"]
    return [p for p in out.splitlines()
            if p and os.path.lexists(os.path.join(cwd, p))]


# --------------------------------------------------------------------------- #
# the shared safety predicate
# --------------------------------------------------------------------------- #

class Verdict(object):
    """`action`: "ff" (provably safe, fast-forward to `target`), "noop"
    (nothing to do: up to date, detached, no target) or "refuse" (unsafe —
    `reason` says why). `behind` is None when unmeasured."""
    __slots__ = ("action", "reason", "branch", "target", "behind", "detail")

    def __init__(self, action, reason, branch=None, target=None, behind=None,
                 detail=None):
        self.action = action
        self.reason = reason
        self.branch = branch
        self.target = target
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
    if state:
        return Verdict("refuse", "in-progress", detail=state)
    branch = current_branch(cwd)
    if not branch:
        return Verdict("noop", "detached")
    target = "%s/%s" % (remote, branch)
    if not ref_exists(cwd, target):
        return Verdict("noop", "no-target", branch, target)
    behind = count_behind(cwd, target)
    if behind is None:
        return Verdict("noop", "behind-unmeasurable", branch, target)
    if behind == 0:
        return Verdict("noop", "up-to-date", branch, target, 0)
    status = worktree_status(cwd)
    if status is UNMEASURABLE:
        return Verdict("refuse", "unmeasurable", branch, target, behind)
    if status.strip():
        return Verdict("refuse", "dirty", branch, target, behind)
    rc, _ = run_git(cwd, ["merge-base", "--is-ancestor", "HEAD", target])
    if rc != 0:
        return Verdict("refuse", "diverged", branch, target, behind)
    collide = colliding_paths(cwd, target)
    if collide:
        return Verdict("refuse", "collision", branch, target, behind,
                       detail=collide)
    return Verdict("ff", "safe", branch, target, behind)


def fast_forward(cwd, verdict):
    """Apply a `ff` verdict with `git merge --ff-only` (a hard net of its own:
    it only ever moves the ref along its own history and refuses rather than
    overwrite a file edited since the check). True on success."""
    if verdict.action != "ff":
        return False
    rc, _ = run_git(cwd, ["merge", "--ff-only", "--quiet", verdict.target])
    return rc == 0


def hook_line(verdict, applied=None):
    """The #314 SessionStart line for a verdict (empty = print nothing). The
    wording is the hook's historical contract (tests assert it)."""
    b, t, n = verdict.branch, verdict.target, verdict.behind
    r = verdict.reason
    if r == "in-progress":
        return ("WARNING: repository has an in-progress git operation "
                "(merge/rebase/cherry-pick/revert/bisect) — leaving it untouched")
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
    v = ff_verdict(cwd, "origin")
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
    """Checkouts BEHIND for >= `after_s` — measured from the oldest missing
    commit (`behind_since`), else from the first unfixable observation
    (`since`) — that have ALSO been observed unfixable for at least one job
    period (a single dirty-tree snapshot is never an alarm). Worst first."""
    after_s = stale_after_s() if after_s is None else after_s
    rows = []
    for path, e in ((status or {}).get("checkouts") or {}).items():
        if not isinstance(e, dict) or e.get("state") != "lagging":
            continue
        since = _num(e.get("since"))
        if since is None or now - since < MIN_OBSERVED_S:
            continue
        start = min(_num(e.get("behind_since")) or since, since)
        if now - start >= after_s:
            rows.append((path, e))
    rows.sort(key=lambda r: -(r[1].get("behind") or 0))
    return rows


def compact_count(n):
    """2600 -> '2.6k', 12 -> '12', None -> '?'."""
    if not isinstance(n, int) or isinstance(n, bool):
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
    lag = [(p, e) for p, e in sorted(cos.items())
           if isinstance(e, dict) and e.get("state") == "lagging"]
    stale = {p for p, _ in stale_entries(status, now)}
    out = ["checkout-freshness: %d checkout(s), %d lagging, %d stale (>%gh), "
           "checked %s ago" % (len(cos), len(lag), len(stale),
                               stale_after_s() / 3600, _age(now, status.get("ts")))]
    for p, e in lag:
        since = _num(e.get("since"))
        start = min(_num(e.get("behind_since")) or since, since) if since else None
        out.append("  %s%s [%s -> %s] behind %s: %s (%s)" % (
            "STALE " if p in stale else "", p, e.get("branch") or "detached",
            e.get("base") or "?", e.get("behind"), e.get("reason"),
            _age(now, start)))
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["hook"]:
        sys.exit(hook_main())
    print("usage: python3 -m cli_checkout_freshness hook", file=sys.stderr)
    sys.exit(2)
