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
    (bases = the window's declared `branch`, else the default base set) —
    recorded `absent` when missing (visible in `status`, never an alarm; a
    missing declared window is #1108's surface);
  * a stream account's (`cli_fleet.AUTHORITY_BY_USER`) checkout, resolved by
    the ONE stream resolver `cli_bashrc_appliers.resolve_stream_cwd`;
  * `projects-registry.json` entries whose `host` names this box (the box
    class `controller`/`gk`, the hostname's first label, and `controller` for
    the control-plane host) and whose path exists under THIS account's home
    (the registry has no account field: an absent path belongs to another
    account on the same host and is skipped with a decision line).
  Deduplicated by real path.

PER CHECKOUT (`check_checkout`), at most once per INTERVAL_S, oldest first,
under a per-checkout DEADLINE (`cli_checkout_freshness.deadline`; nested
deadlines only shorten it):
  0. The path must be a checkout ROOT (`--show-toplevel`), never a plain
     directory inside some parent repository.
  1. remote = `cli_checkout_freshness.pick_remote` — the SAME rule the hook
     uses: `upstream` when it carries the base (a fork stream's real base,
     never the fork's copy — as session-start-stream-directives.sh
     resolves it), else the tracking remote, else `origin`. A bounded
     `git fetch --no-write-fetch-head` (never clobbers the session's own
     FETCH_HEAD; own process group, SIGTERM then SIGKILL; BatchMode ssh
     unless the repo sets `core.sshCommand`; a fetch that timed out gets
     FETCH_TIMEOUT_LONG_S next time when the sweep budget allows, and the
     per-checkout deadline grows by the same amount).
  2. On a BASE branch: the SHARED safety predicate
     `cli_checkout_freshness.ff_verdict` (the SAME function the SessionStart
     hook runs — one definition of "provably safe") → `git merge --ff-only`
     with the repo's merge hooks OFF. The merge is NEVER bounded or
     signalled by the sweep once started (git does not roll back files a
     checkout already wrote, so a stopped merge leaves a half-applied tree).
     It runs DETACHED (#1176 reopen — a sweep-budget reserve starved it: dev1
     spinbike 52 commits behind for 16 h, the job never had 60 s left): a
     transient `systemd-run --user --collect` unit (the ONE launcher,
     `watchdog/user_unit.py`) runs `cli_checkout_freshness.py ff`, which
     re-checks the verdict, merges and writes a result file; the NEXT sweep
     collects it (`collect_ff_results`: `current`, or `lagging` with
     `fast-forward failed: <why>`). A pending unit (`ff_pending`) blocks a
     second launch until it reports or FF_PENDING_MAX_S passes. FALLBACK
     where `systemd-run --user` is unavailable: the old in-unit merge, which
     starts only while the sweep has MERGE_RESERVE_S left, else it is
     deferred and due again next sweep — and a checkout deferred
     DEFER_PRIORITY_AFTER sweeps in a row is processed FIRST in the next
     sweep. That priority helps only when OTHER checkouts eat the budget:
     `budget_left` is the whole sweep's, so a job that STARTS below the
     reserve (the dev1 shape) still defers — only the detached path fixes
     that, and every managed box runs the watchdog as a `--user` service,
     so `systemd-run --user` is there. A client timeout is UNKNOWN (the
     bus may have started the unit), so it is recorded as pending, never
     raced by the in-unit merge. Refused states (dirty, unmeasurable tree, diverged,
     an in-progress operation or a held `index.lock`, a path collision) are
     NEVER touched — only recorded with their commits behind and reason.
  3. On a WORK branch (or detached): no fast-forward. The rule-file lag is
     measured — `CLAUDE.md` / `.claude/` files (root and nested) the base
     changed since the fork point (`git diff --name-only HEAD...<base>`); a
     non-empty lag is `lagging`.
  4. Anything that cannot be PROVEN current is `lagging` with its reason: a
     failed fetch, no remote-tracking ref for the branch, an unmeasurable
     count.

VISIBILITY: `~/.claude/checkout-freshness/status.json` (atomic write after
EVERY checkout, so a sweep killed mid-way keeps what it did) holds, per
checkout, state (`current` / `lagging` / `absent` / `untracked`), branch,
base, remote, behind, reason and `since` (the first unfixable observation).
The footer `stale <N>` and the `airuleset.py status` rows read it through
`cli_checkout_freshness` (never this package). Every checked checkout emits
ONE decision line into the journal. MACHINE-CHANNEL only — never a Discord
ping (#693). Not run on a paused box (the registry gate).

BUDGET: `MIN_BUDGET_S` gates the start (registry `hold:budget`); a new
checkout starts only while `budget_left()` >= PER_CHECKOUT_S and the job's
own wall clock is under JOB_WALL_S; its reads + fetch are clipped to the
per-checkout deadline, the long fetch retry needs LONG_RETRY_RESERVE_S and
the FALLBACK in-unit merge MERGE_RESERVE_S of sweep budget (measured to the
100 s soft cap, so a merge has >= 80 s before the unit's 120 s kill; the
detached merge needs no reserve, only the <= 10 s systemd-run client) — the rest
are due next sweep, so a box with many checkouts rotates instead of blowing
the unit budget. A held sweep still refreshes the status `ts` (a held job
is alive, never read as a dead watchdog). The value lock test pins these.

ACCEPTED RESIDUALS: an ignored file created in the milliseconds between the
collision check and the merge is overwritten by git (the #314 hook had the
same window); assume-unchanged / skip-worktree files are invisible to
`git status`; a merge wedged past FF_UNIT_MAX_S is stopped by systemd
(`RuntimeMaxSec`) — the one case a started merge is signalled, chosen over a
unit that could block its checkout's fast-forward forever.
"""
import hashlib
import json
import os
import socket
import sys
import time

import cli_checkout_freshness as cf
from watchdog import user_unit

INTERVAL_S = 15 * 60          # per-checkout cadence
FETCH_TIMEOUT_S = 15          # one bounded fetch (the #172 per-repo bound)
FETCH_TIMEOUT_LONG_S = 45     # the retry after a timed-out fetch
LONG_RETRY_RESERVE_S = FETCH_TIMEOUT_LONG_S + 2 * cf.TERM_GRACE_S + 5
PER_CHECKOUT_S = 25           # the per-checkout deadline (reads + fetch)
MERGE_RESERVE_S = 60          # FALLBACK: sweep budget to START the in-unit merge
MIN_BUDGET_S = 30             # registry min_budget: one checkout + margin
JOB_WALL_S = 40               # the job's own ceiling per sweep
FF_UNIT_MAX_S = 30 * 60       # the detached merge unit's RuntimeMaxSec (wedge only)
FF_PENDING_MAX_S = FF_UNIT_MAX_S + 120   # a pending unit with no result = failed
DEFER_PRIORITY_AFTER = 3      # FALLBACK: deferred this many sweeps -> goes first
# the detached merge runs only local git: no gh auth, no GH_* namespace
FF_UNIT_ENV_KEYS = ("PATH", "HOME", "XDG_CONFIG_HOME", "LANG", "LC_ALL")
DEFAULT_BASES = ("develop", "dev", "main", "master")
RULE_PATHS = ("CLAUDE.md", ".claude", ":(glob)**/CLAUDE.md",
              ":(glob)**/.claude/**")


# --------------------------------------------------------------------------- #
# checkout set
# --------------------------------------------------------------------------- #

def _registry_host_keys(user, hostname=None):
    """This box's `projects-registry.json` host keys: the box class for the
    control-plane / gatekeeper accounts, the hostname's first label, and
    `controller` on the control-plane host (every account there)."""
    import cli_box_class
    import cli_fleet
    short = (hostname or socket.gethostname()).split(".")[0]
    keys = {short}
    cls = cli_box_class.box_class_for_user(user, cli_fleet.AUTHORITY_BY_USER)
    if cls in ("controller", "gk"):
        keys.add(cls)
    if short == "airuleset":
        keys.add("controller")
    return keys


def _registry_entries(host_keys, registry_path=None):
    if registry_path is None:
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects-registry.json")
    try:
        with open(registry_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        cf._log("projects-registry unreadable: %s" % exc)
        return []
    return [e for e in data if isinstance(e, dict)
            and e.get("host") in host_keys and e.get("path")]


def discover_checkouts(home=None, user=None, hostname=None, windows=None,
                       registry_path=None, skipped=None):
    """`[{"path", "bases", "source"}]` — every managed checkout of THIS box and
    account, deduplicated by real path (first source wins). A registry entry
    whose path is absent under this home is appended to `skipped` (a list)."""
    import cli_concurrency
    import cli_fleet
    home = home or os.path.expanduser("~")
    user = user or cli_concurrency._current_user()
    out, seen = [], set()

    def _abs(p):
        if p.startswith("~/"):
            return os.path.join(home, p[2:])
        return p if os.path.isabs(p) else os.path.join(home, p)

    def _add(path, bases, source):
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            out.append({"path": path, "bases": [b for b in bases if b],
                        "source": source})

    wins = windows if windows is not None else cli_fleet.box_windows(user)
    for w in wins or []:
        if isinstance(w, dict) and w.get("cwd"):
            branch = w.get("branch")
            _add(_abs(w["cwd"]), [branch] if branch else DEFAULT_BASES,
                 "window:%s" % w.get("name"))
    if user in cli_fleet.AUTHORITY_BY_USER:
        from cli_bashrc_appliers import resolve_stream_cwd
        chosen, no_repo = resolve_stream_cwd(home)
        if not no_repo:
            _add(str(chosen), DEFAULT_BASES, "stream:%s" % user)
    for e in _registry_entries(_registry_host_keys(user, hostname), registry_path):
        path = _abs(e["path"])
        if not os.path.isdir(path):
            if skipped is not None:
                skipped.append("registry:%s %s absent under this account" % (
                    e.get("name"), path))
            continue
        _add(path, [e.get("work_branch"), e.get("default_branch")],
             "registry:%s" % e.get("name"))
    return out


# --------------------------------------------------------------------------- #
# one checkout
# --------------------------------------------------------------------------- #

def _oldest_ct(path, rev_range, paths=()):
    """Committer time of the OLDEST first-parent commit in `rev_range`
    (optionally limited to `paths`), or None. INFORMATION for `status` only:
    a commit date never drives `stale` (review 1 finding 8)."""
    args = ["log", "--first-parent", "--format=%ct", rev_range]
    if paths:
        args += ["--"] + list(paths)
    rc, out = cf.run_git(path, args)
    vals = [int(x) for x in out.split() if x.isdigit()] if rc == 0 else []
    return min(vals) if vals else None


def rule_lag(path, ref):
    """Rule files (`CLAUDE.md`, `.claude/`, nested too) the base changed since
    HEAD forked from it — `[paths]`, or None when unmeasurable."""
    rc, out = cf.run_git(path, ["diff", "--name-only", "HEAD..." + ref,
                                "--"] + list(RULE_PATHS))
    return [p for p in out.splitlines() if p] if rc == 0 else None


def _ff_key(path):
    return hashlib.sha1(os.path.realpath(path).encode("utf-8")).hexdigest()[:16]


def ff_unit_name(path):
    """The transient unit of this checkout's detached merge — stable per
    checkout, so a concurrent second start fails atomically ('already
    exists')."""
    return "airuleset-ff-%s" % _ff_key(path)


def ff_result_path(path, home=None):
    """Where this checkout's detached merge writes its result."""
    return os.path.join(os.path.dirname(cf.status_path(home)), "ff",
                        _ff_key(path) + ".json")


def _pending_live(pending, now):
    """A launched detached merge that may still report (`ff_pending`). A
    `launched` stamp in the future (a clock stepped back) is NOT live, or it
    would block the checkout forever."""
    t = pending.get("launched") if isinstance(pending, dict) else None
    return isinstance(t, (int, float)) and 0 <= now - t <= FF_PENDING_MAX_S


def _launch_ff_unit(path, remote, branch, home, unit_run):
    """Start the detached merge (`cli_checkout_freshness.py ff`) as a transient
    `--user` unit via the ONE shared launcher. `(launched, why)`; `why` is
    `started`/`exists` or the reason the caller must fall back. The default
    launcher is never used from a test process (the #1136/#1195 guard). An
    older result file is left alone: the collector ignores a result that
    finished before this launch, so a unit still exiting ('exists') keeps its
    answer."""
    if unit_run is None:
        from watchdog.disk_guard_escalation import running_under_pytest
        if running_under_pytest():
            return False, "systemd-run not used under test"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = ff_result_path(path, home)
    os.makedirs(os.path.dirname(result), exist_ok=True)
    child = [sys.executable, os.path.join(repo_root, "cli_checkout_freshness.py"),
             "ff", "--path", path, "--remote", remote, "--branch", branch,
             "--result", result]
    return user_unit.launch(ff_unit_name(path), repo_root, child, FF_UNIT_MAX_S,
                            run_fn=unit_run, env_keys=FF_UNIT_ENV_KEYS,
                            env_prefixes=())


def _base_branch_result(path, remote, entry, dry_run, budget_left, prev=None,
                        home=None, unit_run=None):
    """A checkout ON a base branch: the shared predicate decides; a provably
    safe fast-forward runs DETACHED (a transient unit, collected next sweep),
    or — without systemd-run — in-unit only when the sweep can afford the
    merge. Returns (state, reason, behind, deferred)."""
    prev = prev or {}
    v = cf.ff_verdict(path, remote)
    if v.action == "ff":
        if dry_run:
            return "lagging", "would fast-forward (dry-run)", v.behind, False
        pending = prev.get("ff_pending")
        if _pending_live(pending, entry["checked"]):
            entry["ff_pending"] = pending    # never a second launch
            return ("lagging", "fast-forward running in unit %s" % pending.get("unit"),
                    v.behind, False)
        launched, why = _launch_ff_unit(path, remote, v.branch, home, unit_run)
        if launched or why == user_unit.TIMEOUT_WHY:
            # a client timeout is UNKNOWN (the bus may have started the unit):
            # never race it with an in-unit merge — wait for its result instead
            unit = ff_unit_name(path)
            entry["ff_pending"] = {"unit": unit, "launched": entry["checked"],
                                   "behind": v.behind}
            note = {"exists": " (already running)",
                    user_unit.TIMEOUT_WHY: " (launch unconfirmed: systemd-run client "
                                           "timed out)"}.get(why, "")
            return ("lagging", "fast-forward running in unit %s%s" % (unit, note),
                    v.behind, False)
        left = budget_left() if budget_left is not None else None
        if left is not None and left < MERGE_RESERVE_S:
            entry["deferrals"] = int(prev.get("deferrals") or 0) + 1
            return ("lagging", "fast-forward deferred (%.0fs of sweep budget "
                    "left; %s)" % (left, why), v.behind, True)
        ok = cf.fast_forward(path, v, run_hooks=False)   # unbounded once started
        if ok:
            entry["ff"] = {"at": entry["checked"], "commits": v.behind}
            return ("current", "fast-forwarded %d commit(s) in-unit (%s)" % (v.behind, why),
                    0, False)
        return "lagging", "fast-forward failed in-unit (%s)" % why, v.behind, False
    if v.action == "noop" and v.reason == "up-to-date":
        return "current", "up-to-date", 0, False
    if v.action == "noop":
        why = {"no-target": "no %s to compare with" % v.target,
               "behind-unmeasurable": "behind count unmeasurable"}.get(v.reason, v.reason)
        return "lagging", why, v.behind, False
    detail = v.detail if isinstance(v.detail, str) or v.detail is None \
        else " ".join(v.detail)
    return "lagging", v.reason + (" (%s)" % detail if detail else ""), v.behind, False


def _work_branch_result(path, branch, remote, base):
    """A checkout on a WORK branch (or detached): never moved; the rule-file
    lag against the base is measured. Returns (state, reason, behind)."""
    if not base:
        return "untracked", "no base branch on %s" % remote, None
    ref = cf.remote_ref(remote, base)
    behind = cf.count_behind(path, ref)
    files = rule_lag(path, ref)
    label = "work branch '%s'" % branch if branch else "detached HEAD"
    if files is None:
        return "lagging", label + ": rule-file lag unmeasurable", behind
    if files:
        return ("lagging", "%s: %d rule file(s) behind %s/%s" % (
            label, len(files), remote, base), behind)
    return "current", label + ": rule files current", behind


def fetch_timeout_for(prev, budget_left, fetch_timeout=FETCH_TIMEOUT_S):
    """The fetch bound for this check: FETCH_TIMEOUT_LONG_S after a timed-out
    fetch when the sweep can afford it (LONG_RETRY_RESERVE_S), else normal."""
    left = budget_left() if budget_left is not None else None
    if (prev or {}).get("fetch_rc") == 124 and (left is None or left >= LONG_RETRY_RESERVE_S):
        return FETCH_TIMEOUT_LONG_S
    return fetch_timeout


def _fetch(path, remote, dry_run, timeout):
    """rc of ONE bounded fetch. Dry-run mutates nothing, not even remote
    refs; `--no-write-fetch-head` never clobbers the session's own
    FETCH_HEAD (a `git pull` of its own in flight)."""
    if dry_run:
        return 0
    env = cf.ssh_batch_env(path)
    rc, _ = cf.run_git(path, ["-c", "gc.auto=0", "-c", "maintenance.auto=false",
                              "fetch", "--quiet", "--no-tags",
                              "--no-write-fetch-head", "--no-recurse-submodules",
                              remote], timeout=timeout, env_extra=env)
    return rc


def _is_checkout_root(path):
    rc, out = cf.run_git(path, ["rev-parse", "--show-toplevel"])
    return rc == 0 and os.path.realpath(out.strip()) == os.path.realpath(path)


def check_checkout(c, now, prev=None, dry_run=False,
                   fetch_timeout=FETCH_TIMEOUT_S, budget_left=None, home=None,
                   unit_run=None):
    """Fetch + fast-forward-when-safe + measure ONE checkout under its own
    deadline; returns its status entry (never raises for a git failure).
    `unit_run` is the systemd-run client seam (tests)."""
    prev = prev if isinstance(prev, dict) else {}
    used = fetch_timeout_for(prev, budget_left, fetch_timeout)
    with cf.deadline(PER_CHECKOUT_S - 5 + (used - fetch_timeout)):
        return _check(c, now, prev, dry_run, used, budget_left, home, unit_run)


def _check(c, now, prev, dry_run, used, budget_left, home=None, unit_run=None):
    path = c["path"]
    entry = {"source": c.get("source"), "checked": now}
    if _pending_live(prev.get("ff_pending"), now):   # collected by a later sweep
        entry["ff_pending"] = prev["ff_pending"]
    bases = list(c.get("bases") or ())
    if not os.path.isdir(path):
        entry.update(state="absent", reason="path absent")
        return entry
    if not _is_checkout_root(path):
        entry.update(state="absent", reason="not a git checkout root")
        return entry
    branch = cf.current_branch(path)
    remote = cf.pick_remote(path, branch, bases)
    if not remote:
        entry.update(state="untracked", reason="no git remote")
        return entry
    frc = _fetch(path, remote, dry_run, used)
    on_remote = [b for b in bases if cf.ref_exists(path, cf.remote_ref(remote, b))]
    base = branch if branch in bases else (on_remote[0] if on_remote else None)
    entry.update(remote=remote, branch=branch, base=base, fetch_rc=frc)
    deferred = False
    if branch and branch in bases:
        state, reason, behind, deferred = _base_branch_result(
            path, remote, entry, dry_run, budget_left, prev, home, unit_run)
    else:
        state, reason, behind = _work_branch_result(path, branch, remote, base)
    if frc != 0:
        note = "fetch %s failed (rc %d)" % (remote, frc)
        state, reason = ("lagging", note) if state == "current" else (
            state, "%s; %s" % (reason, note))
    entry.update(state=state, reason=reason, behind=behind, fetch_timeout=used)
    if state == "lagging":
        entry["since"] = prev.get("since") if prev.get("state") == "lagging" \
            and isinstance(prev.get("since"), (int, float)) else now
        if base and behind:
            ref = cf.remote_ref(remote, base)
            entry["behind_since"] = _oldest_ct(
                path, "HEAD.." + ref, () if branch in bases else RULE_PATHS)
    if deferred:   # due again next sweep, never parked for a full interval
        entry["checked"] = prev.get("checked", 0) if isinstance(
            prev.get("checked"), (int, float)) else 0
    if prev.get("ff") and "ff" not in entry:
        entry["ff"] = prev["ff"]
    return entry


# --------------------------------------------------------------------------- #
# status file + the job
# --------------------------------------------------------------------------- #

def write_status(status, home=None):
    """Atomic write of the status file (temp + os.replace)."""
    cf.write_json_atomic(cf.status_path(home), status)


def decision_line(path, e):
    """ONE journal line per checked checkout (#486 explicit decision log)."""
    return "checkout-freshness: %s [%s -> %s] %s: %s (behind %s)" % (
        path, e.get("branch") or "-", e.get("base") or "-",
        e.get("state"), e.get("reason"), e.get("behind"))


def _read_result(path):
    """A detached merge's result dict, None when not written yet, or a failed
    result when the file is unreadable (never guessed ok)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "unreadable result: %s" % exc}
    return data if isinstance(data, dict) else {"ok": False, "reason": "malformed result"}


def _apply_result(e, res, unit, now):
    """Fold one collected result into its status entry."""
    if res.get("ok"):
        commits = res.get("commits")
        if not isinstance(commits, int) or isinstance(commits, bool):
            commits = e.get("behind")
        e.update(state="current", behind=0)
        if commits == 0:   # a no-op run (e.g. a session start merged it first)
            e["reason"] = "%s (unit %s)" % (res.get("reason") or "already up-to-date", unit)
        else:
            e["reason"] = "fast-forwarded %s commit(s) in unit %s" % (commits, unit)
            fin = res.get("finished")
            e["ff"] = {"at": fin if isinstance(fin, (int, float)) else now,
                       "commits": commits}
        e.pop("since", None)
        e.pop("behind_since", None)
    else:
        e.update(state="lagging", reason="fast-forward failed: %s" % res.get("reason"))
        e.setdefault("since", now)


def collect_ff_results(cos, now, home=None):
    """Fold every finished detached merge (`ff_pending` + its result file) into
    `cos` — every sweep, never only when the checkout is due, so a merge is
    reported one sweep after it ran. A pending unit with no result after
    FF_PENDING_MAX_S is `fast-forward failed` and may be launched again; a
    result that finished BEFORE the launch is an older run's, never this
    one's answer. Returns `(journal lines, result files to remove)` — the
    caller removes them only AFTER the status that absorbed them is written,
    so a sweep killed in between loses nothing. `cos` is updated in place."""
    logs, consumed = [], []
    for path, e in cos.items():
        pending = e.get("ff_pending") if isinstance(e, dict) else None
        if not isinstance(pending, dict):
            continue
        rpath = ff_result_path(path, home)
        res = _read_result(rpath)
        fin, launched = (res or {}).get("finished"), pending.get("launched")
        if isinstance(fin, (int, float)) and isinstance(launched, (int, float)) \
                and fin < launched:
            consumed.append(rpath)       # stale: an older run's result
            res = None
        if res is None:
            if _pending_live(pending, now):
                continue
            res = {"ok": False, "reason": (
                "unit %s left no result within %ds (if systemd stopped it mid-merge "
                "the tree may be half-applied; the next check reports it)" % (
                    pending.get("unit"), FF_PENDING_MAX_S))}
        elif os.path.lexists(rpath):
            consumed.append(rpath)
        del e["ff_pending"]
        _apply_result(e, res, pending.get("unit"), now)
        logs.append(decision_line(path, e))
    return logs, consumed


def _remove_results(paths):
    """Remove collected result files; a failure is logged, never raised — one
    bad file must not stop the job every sweep."""
    logs = []
    for p in paths:
        try:
            os.remove(p)
        except OSError as exc:
            logs.append("checkout-freshness: could not remove result %s: %s" % (p, exc))
    return logs


def run_job(now, *, dry_run=False, budget_left=None, home=None,
            checkouts=None, clock=time.monotonic,
            fetch_timeout=FETCH_TIMEOUT_S, unit_run=None):
    """Collect finished detached merges, then check every DUE checkout
    (repeatedly-deferred first, then oldest first) within the budget; returns
    the journal lines. `checkouts` / `home` / `clock` / `unit_run` are test
    seams."""
    logs = []
    if checkouts is None:
        skipped = []
        checkouts = discover_checkouts(home, skipped=skipped)
        logs += ["checkout-freshness: skip %s" % s for s in skipped]
    status = cf.read_status(home) or {}
    prev_all = status.get("checkouts") if isinstance(status.get("checkouts"), dict) else {}
    live = {c["path"] for c in checkouts}
    cos = {p: e for p, e in prev_all.items() if p in live}
    pruned = len(cos) != len(prev_all)
    collected, consumed = ([], []) if dry_run else collect_ff_results(cos, now, home)
    logs += collected
    if collected or consumed:
        write_status({"ts": now, "checkouts": cos}, home)   # absorbed first ...
        logs += _remove_results(consumed)                   # ... then removed

    def _last(c):
        t = (cos.get(c["path"]) or {}).get("checked")
        return t if isinstance(t, (int, float)) and t <= now else 0

    def _starved(c):
        n = (cos.get(c["path"]) or {}).get("deferrals")
        return isinstance(n, int) and n >= DEFER_PRIORITY_AFTER

    due = sorted((c for c in checkouts if now - _last(c) >= INTERVAL_S),
                 key=lambda c: (not _starved(c), _last(c)))
    started, done = clock(), 0
    for c in due:
        left = budget_left() if budget_left is not None else None
        if (left is not None and left < PER_CHECKOUT_S) or \
                clock() - started > JOB_WALL_S:
            logs.append("checkout-freshness: hold:budget — %d of %d due "
                        "checkout(s) left for the next sweep" % (len(due) - done, len(due)))
            if not dry_run and not done:   # held, yet alive: refresh ts
                write_status({"ts": now, "checkouts": cos}, home)
            break
        try:
            e = check_checkout(c, now, cos.get(c["path"]), dry_run,
                               fetch_timeout, budget_left, home, unit_run)
        except Exception as exc:  # noqa: BLE001 -- one checkout never kills the rest
            old = cos.get(c["path"]) or {}
            e = {"source": c.get("source"), "checked": now, "state": "lagging",
                 "reason": "check error: %s" % exc, "since": old.get("since", now)}
            if _pending_live(old.get("ff_pending"), now):
                e["ff_pending"] = old["ff_pending"]
        cos[c["path"]] = e
        done += 1
        logs.append(decision_line(c["path"], e))
        if not dry_run:
            write_status({"ts": now, "checkouts": cos}, home)
    if pruned and not (collected or consumed) and not done and not dry_run:
        write_status({"ts": status.get("ts", now), "checkouts": cos}, home)
    return logs
