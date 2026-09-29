"""#1176 — Job 53: CHECKOUT FRESHNESS, enforced continuously (not on an event).

Every managed session reads `CLAUDE.md`, `.claude/rules/` and skills straight
off its checkout. Until this job, the ONLY thing that moved a checkout forward
was `hooks/session-start-fetch.sh`, and only at a `startup` SessionStart —
while every managed session is launched with `claude -c` (a `resume` source)
and may then live for days. The gk FLOW checkout sat ~2 600 commits behind for
a week (22.–29.9.2026); #1127's event-path fix recurred twice. This job makes
freshness a PROPERTY the watchdog enforces every ~15 min, and makes the cases
it must not fix VISIBLE.

CHECKOUT SET (versioned data, this box + this unix account only;
`discover_checkouts`):
  * the `cwd` of every `cli_fleet` declared window of this account's box
    (bases = the window's declared `branch`, else the default base set);
  * a stream account's (`cli_fleet.AUTHORITY_BY_USER`) checkout, resolved by
    the ONE stream resolver `cli_bashrc_appliers.resolve_stream_cwd`;
  * `projects-registry.json` entries whose `host` is this box (`controller` /
    `gk` by box class, else the hostname's first label; bases = the entry's
    `work_branch` + `default_branch`).
  Deduplicated by real path. A path that is absent or not a git checkout is
  recorded `absent` (visible in `status`, never an alarm — a missing declared
  window is #1108's surface).

PER CHECKOUT (`check_checkout`), at most once per INTERVAL_S, oldest first:
  1. remote = `upstream` when configured (a fork stream's real base), else
     `origin`; a bounded `git fetch` (own process group, SIGKILLed with its
     ssh/https helpers at FETCH_TIMEOUT_S — `cli_checkout_freshness.run_git`).
  2. On a BASE branch: the SHARED safety predicate
     `cli_checkout_freshness.ff_verdict` (the SAME function the SessionStart
     hook runs — one definition of "provably safe") → `git merge --ff-only`
     when safe. Refused states (dirty, unmeasurable tree, diverged, an
     in-progress operation or a held `index.lock`, a path collision) are
     NEVER touched — only recorded with their commits behind and reason.
  3. On a WORK branch (or detached): no fast-forward. The rule-file lag is
     measured — files under `CLAUDE.md` / `.claude/` the base changed since
     the fork point (`git diff --name-only HEAD...<remote>/<base>`); a
     non-empty lag is `lagging`.
  4. A failed fetch turns an otherwise-current checkout `lagging` (it cannot
     be proven current).

VISIBILITY: `~/.claude/checkout-freshness/status.json` (atomic write after
EVERY checkout, so a sweep killed mid-way keeps what it did) holds, per
checkout, state (`current` / `lagging` / `absent` / `untracked`), branch,
base, behind, reason, `since` (first unfixable observation) and
`behind_since` (the oldest missing first-parent commit's time). The footer
`stale <N>` and the `airuleset.py status` rows read it through
`cli_checkout_freshness` (never this package). Every checked checkout emits
ONE decision line into the journal. MACHINE-CHANNEL only — never a Discord
ping (#693).

BUDGET: `MIN_BUDGET_S` gates the start (registry `hold:budget`); inside the
job a new checkout starts only while `budget_left()` >= PER_CHECKOUT_S and the
job's own wall clock is under JOB_WALL_S — the rest are due next sweep, so a
box with many checkouts rotates instead of blowing the 120 s unit budget.
"""
import json
import os
import socket
import time

import cli_checkout_freshness as cf

INTERVAL_S = 15 * 60          # per-checkout cadence
FETCH_TIMEOUT_S = 15          # one bounded fetch (the #172 per-repo bound)
PER_CHECKOUT_S = 25           # fetch + the local reads, a checkout's worst case
MIN_BUDGET_S = 30             # registry min_budget: one checkout + margin
JOB_WALL_S = 40               # the job's own ceiling per sweep
DEFAULT_BASES = ("develop", "dev", "main", "master")
RULE_PATHS = ("CLAUDE.md", ".claude")


# --------------------------------------------------------------------------- #
# checkout set
# --------------------------------------------------------------------------- #

def _registry_host_key(user, hostname=None):
    """This box's `projects-registry.json` host key: the box class for the
    control-plane / gatekeeper accounts, else the hostname's first label."""
    import cli_box_class
    import cli_fleet
    cls = cli_box_class.box_class_for_user(user, cli_fleet.AUTHORITY_BY_USER)
    if cls in ("controller", "gk"):
        return cls
    return (hostname or socket.gethostname()).split(".")[0]


def _registry_entries(host_key, registry_path=None):
    if registry_path is None:
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects-registry.json")
    try:
        with open(registry_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        cf._log("projects-registry unreadable: %s" % exc)
        return []
    return [e for e in data if isinstance(e, dict)
            and e.get("host") == host_key and e.get("path")]


def discover_checkouts(home=None, user=None, hostname=None, windows=None,
                       registry_path=None):
    """`[{"path", "bases", "source"}]` — every managed checkout of THIS box and
    account, deduplicated by real path (first source wins)."""
    import cli_concurrency
    import cli_fleet
    home = home or os.path.expanduser("~")
    user = user or cli_concurrency._current_user()
    out, seen = [], set()

    def _add(rel_or_abs, bases, source):
        p = rel_or_abs
        if p.startswith("~/"):
            p = os.path.join(home, p[2:])
        elif not os.path.isabs(p):
            p = os.path.join(home, p)
        key = os.path.realpath(p)
        if key in seen:
            return
        seen.add(key)
        out.append({"path": p, "bases": [b for b in bases if b],
                    "source": source})

    wins = windows if windows is not None else cli_fleet.box_windows(user)
    for w in wins or []:
        if isinstance(w, dict) and w.get("cwd"):
            branch = w.get("branch")
            _add(w["cwd"], [branch] if branch else DEFAULT_BASES,
                 "window:%s" % w.get("name"))
    if user in cli_fleet.AUTHORITY_BY_USER:
        from cli_bashrc_appliers import resolve_stream_cwd
        chosen, no_repo = resolve_stream_cwd(home)
        if not no_repo:
            _add(str(chosen), DEFAULT_BASES, "stream:%s" % user)
    for e in _registry_entries(_registry_host_key(user, hostname), registry_path):
        _add(e["path"], [e.get("work_branch"), e.get("default_branch")],
             "registry:%s" % e.get("name"))
    return out


# --------------------------------------------------------------------------- #
# one checkout
# --------------------------------------------------------------------------- #

def pick_remote(path, bases=()):
    """The remote the base lives on: `upstream` when configured AND already
    carrying one of `bases` (a fork stream whose box can read the real base —
    the #847 evidence rule, so a box that cannot fetch upstream never turns
    permanently stale on it), else `origin`, else a bare `upstream`, else
    None."""
    configured = [r for r in ("upstream", "origin")
                  if cf.run_git(path, ["remote", "get-url", r])[0] == 0]
    if "upstream" in configured and any(
            cf.ref_exists(path, "upstream/%s" % b) for b in bases):
        return "upstream"
    if "origin" in configured:
        return "origin"
    return configured[0] if configured else None


def _oldest_ct(path, rev_range, paths=()):
    """Committer time of the OLDEST first-parent commit in `rev_range`
    (optionally limited to `paths`), or None."""
    args = ["log", "--first-parent", "--format=%ct", rev_range]
    if paths:
        args += ["--"] + list(paths)
    rc, out = cf.run_git(path, args)
    vals = [int(x) for x in out.split() if x.isdigit()] if rc == 0 else []
    return min(vals) if vals else None


def rule_lag(path, target):
    """Rule files (`CLAUDE.md`, `.claude/`) the base changed since HEAD forked
    from it — `[paths]`, or None when unmeasurable."""
    rc, out = cf.run_git(path, ["diff", "--name-only", "HEAD..." + target,
                                "--"] + list(RULE_PATHS))
    return [p for p in out.splitlines() if p] if rc == 0 else None


def _base_branch_result(path, remote, entry, dry_run):
    """A checkout ON a base branch: the shared predicate decides; fast-forward
    only when provably safe. Returns (state, reason, behind, target)."""
    v = cf.ff_verdict(path, remote)
    if v.action == "ff":
        if dry_run:
            return "lagging", "would fast-forward (dry-run)", v.behind, v.target
        if cf.fast_forward(path, v):
            entry["ff"] = {"at": entry["checked"], "commits": v.behind}
            return "current", "fast-forwarded %d commit(s)" % v.behind, 0, v.target
        return "lagging", "fast-forward failed", v.behind, v.target
    if v.action == "noop":
        if v.reason == "behind-unmeasurable":
            return "lagging", "behind count unmeasurable", None, v.target
        return "current", v.reason, v.behind or 0, v.target
    reason = v.reason if not v.detail else "%s (%s)" % (
        v.reason, v.detail if isinstance(v.detail, str) else " ".join(v.detail))
    return "lagging", reason, v.behind, v.target


def _work_branch_result(path, branch, remote, base):
    """A checkout on a WORK branch (or detached): never moved; the rule-file
    lag against the base is measured. Returns (state, reason, behind, target,
    rule_since)."""
    if not base:
        return "untracked", "no base branch on %s" % remote, None, None, None
    target = "%s/%s" % (remote, base)
    behind = cf.count_behind(path, target)
    files = rule_lag(path, target)
    label = "work branch '%s'" % branch if branch else "detached HEAD"
    if files is None:
        return "lagging", label + ": rule-file lag unmeasurable", behind, target, None
    if files:
        since = _oldest_ct(path, "HEAD.." + target, RULE_PATHS)
        return ("lagging", "%s: %d rule file(s) behind %s" % (
            label, len(files), target), behind, target, since)
    return "current", label + ": rule files current", behind, target, None


def check_checkout(c, now, prev=None, dry_run=False,
                   fetch_timeout=FETCH_TIMEOUT_S):
    """Fetch + fast-forward-when-safe + measure ONE checkout; returns its
    status entry (never raises for a git failure)."""
    path = c["path"]
    entry = {"source": c.get("source"), "checked": now}
    prev = prev if isinstance(prev, dict) else {}
    if not os.path.isdir(path):
        entry.update(state="absent", reason="path absent")
        return entry
    rc, _ = cf.run_git(path, ["rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        entry.update(state="absent", reason="not a git checkout")
        return entry
    remote = pick_remote(path, c.get("bases") or ())
    if not remote:
        entry.update(state="untracked", reason="no origin/upstream remote")
        return entry
    frc = 0   # dry-run mutates nothing, not even remote-tracking refs
    if not dry_run:
        frc, _ = cf.run_git(path, ["-c", "gc.auto=0", "-c",
                                   "maintenance.auto=false", "fetch", "--quiet",
                                   "--no-tags", "--no-recurse-submodules", remote],
                            timeout=fetch_timeout)
    branch = cf.current_branch(path)
    bases = [b for b in c.get("bases") or ()
             if cf.ref_exists(path, "%s/%s" % (remote, b))]
    base = branch if branch in bases else (bases[0] if bases else None)
    entry.update(remote=remote, branch=branch, base=base, fetch_ok=frc == 0)
    rule_since = None
    if branch and branch in (c.get("bases") or ()):
        state, reason, behind, target = _base_branch_result(
            path, remote, entry, dry_run)
    else:
        state, reason, behind, target, rule_since = _work_branch_result(
            path, branch, remote, base)
    if frc != 0:
        note = "fetch %s failed (rc %d)" % (remote, frc)
        state, reason = ("lagging", note) if state == "current" else (
            state, "%s; %s" % (reason, note))
    entry.update(state=state, reason=reason, behind=behind)
    if state == "lagging":
        entry["since"] = prev.get("since") if prev.get("state") == "lagging" \
            and isinstance(prev.get("since"), (int, float)) else now
        entry["behind_since"] = rule_since or (
            _oldest_ct(path, "HEAD.." + target) if target and behind else None)
    if prev.get("ff") and "ff" not in entry:
        entry["ff"] = prev["ff"]
    return entry


# --------------------------------------------------------------------------- #
# status file + the job
# --------------------------------------------------------------------------- #

def write_status(status, home=None):
    """Atomic write of the status file (temp + os.replace)."""
    path = cf.status_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def decision_line(path, e):
    """ONE journal line per checked checkout (#486 explicit decision log)."""
    return "checkout-freshness: %s [%s -> %s] %s: %s (behind %s)" % (
        path, e.get("branch") or "-", e.get("base") or "-",
        e.get("state"), e.get("reason"), e.get("behind"))


def run_job(now, *, dry_run=False, budget_left=None, home=None,
            checkouts=None, clock=time.monotonic,
            fetch_timeout=FETCH_TIMEOUT_S):
    """Check every DUE checkout (oldest first) within the budget; returns the
    journal lines. `checkouts` / `home` / `clock` are test seams."""
    if checkouts is None:
        checkouts = discover_checkouts(home)
    status = cf.read_status(home) or {}
    prev_all = status.get("checkouts") if isinstance(status.get("checkouts"), dict) else {}
    live = {c["path"] for c in checkouts}
    cos = {p: e for p, e in prev_all.items() if p in live}
    pruned = len(cos) != len(prev_all)

    def _last(c):
        t = (cos.get(c["path"]) or {}).get("checked")
        return t if isinstance(t, (int, float)) and t <= now else 0

    due = sorted((c for c in checkouts if now - _last(c) >= INTERVAL_S), key=_last)
    logs, started = [], clock()
    done = 0
    for c in due:
        left = budget_left() if budget_left is not None else None
        if (left is not None and left < PER_CHECKOUT_S) or \
                clock() - started > JOB_WALL_S:
            logs.append("checkout-freshness: hold:budget — %d of %d due "
                        "checkout(s) left for the next sweep" % (len(due) - done, len(due)))
            break
        e = check_checkout(c, now, cos.get(c["path"]), dry_run, fetch_timeout)
        cos[c["path"]] = e
        done += 1
        logs.append(decision_line(c["path"], e))
        if not dry_run:
            write_status({"ts": now, "checkouts": cos}, home)
    if pruned and not done and not dry_run:
        write_status({"ts": status.get("ts", now), "checkouts": cos}, home)
    return logs
