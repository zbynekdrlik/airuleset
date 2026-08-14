"""airuleset `autopilot-lock` subcommand + lock-litter sweep (#433 cluster K).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as
watchdog/usage.py / burn_jobs.py / cards.py / repo_health.py and
cli_vault.py). airuleset.py keeps `from cli_autopilot_lock import (...)`
re-exports at the old definition site, so `SUBCOMMANDS["autopilot-lock"]`,
`SUBCOMMANDS["sweep-autopilot-locks"]`, cmd_install's litter-sweep step,
main()'s AUTOPILOT_LOCK_LITTER_MIN_AGE_S_DEFAULT argparse default and
tests' `airuleset._autopilot_lock_path`-style direct references all keep
working unchanged.

This module is deliberately SELF-CONTAINED: stdlib only — no reference
back into airuleset.py, so there is no import-cycle surface in either the
CLI (`python3 airuleset.py`, airuleset running as `__main__`) or the test
(`import airuleset`) topology. `CLAUDE_DIR` below is this file's own copy
of the canonical one-line expression (`Path.home() / ".claude"`) that
watchdog/goal.py and watchdog/compact.py already inline locally today —
identical value, established repo idiom.
"""

import json
import os
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"


# ---------------------------------------------------------------------------
# autopilot-lock — cross-session INTEGRATION mutex (issue #8, narrowed by #456)
# ---------------------------------------------------------------------------


def _autopilot_lock_path(repo):
    """Repo-path-keyed lockfile under the system tempdir. Resolved (realpath)
    so relative paths, symlinks, and a trailing slash all hash to the SAME
    lock — a real cross-session lock must not fork on cosmetic path forms.

    `AIRULESET_AUTOPILOT_LOCK_DIR`, when set, overrides the lock DIRECTORY
    (same shape as `watchdog.draft_rescue_dir()`'s `AIRULESET_DRAFT_RESCUE_DIR`
    and `_is_gh_app_token_box()`'s `GH_APP_TOKEN_DIR` — #385). It exists
    because `tests/test_autopilot_lock.py` genuinely needs to exercise the
    REAL `autopilot-lock` CLI subprocess end-to-end (real `fcntl.flock`, real
    PID liveness via `os.kill`, a real steal race — none of which an
    in-process call could faithfully test), and every one of those subprocess
    runs is keyed on a FRESH `tempfile.mkdtemp()` repo path that is never
    reused and never cleaned up — leaving a permanent, un-owned lock (or
    `.mutex` sibling, or symlink, or directory-shaped artifact) in the REAL
    system `/tmp` on every single test run. Thousands of these accumulated in
    production over weeks (measured live: 8350 `.lock` + 6329 `.lock.mutex` +
    1009 `.lock-real-target` symlinks + 1027 directory-shaped locks on this
    box alone) before this override existed. Unset (real `/autopilot`
    run, and every OTHER caller) is byte-for-byte unchanged."""
    import hashlib
    import tempfile as _tempfile
    real = str(Path(repo).resolve())
    h = hashlib.sha1(real.encode()).hexdigest()
    lock_dir = os.environ.get("AIRULESET_AUTOPILOT_LOCK_DIR") or _tempfile.gettempdir()
    return Path(lock_dir) / f"airuleset-autopilot-{h}.lock"


def _proc_parent_pid(pid):
    """Linux-only /proc read (both managed machines are Linux). Returns None
    off-Linux or on any read failure — callers fall back gracefully."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except Exception:
        return None
    return None


def _proc_comm(pid):
    """Linux-only /proc read of a process's command name (`/proc/<pid>/comm`).
    Returns None off-Linux or on any read failure — callers fall back
    gracefully. Used by `_campaign_pid` to recognize the long-lived `claude`
    (or `node`) process regardless of how many ephemeral shell layers sit
    between it and this process."""
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except Exception:
        return None


_CAMPAIGN_LONG_LIVED_COMMS = {"claude", "node"}
_CAMPAIGN_ANCESTRY_MAX_HOPS = 10


def _campaign_pid():
    """The PID that should stay alive for the WHOLE autopilot campaign (the
    span between an `acquire` call and the LATER, separate `release` call).

    Each Claude Code Bash tool call spawns a fresh ephemeral shell that dies
    the instant that one tool call returns — so os.getppid() alone (this
    process's immediate parent) is USELESS for staleness detection: it would
    already look "dead" moments after `acquire` prints success. The
    long-lived `claude` CLI process itself, which persists for the entire
    session, sits further up the ancestry chain.

    This WALKS the ancestry (by `comm` name, not a fixed hop count) until it
    finds a known long-lived process. A FIXED one-hop walk (the previous
    implementation) is correct only when there is EXACTLY one ephemeral
    shell layer between this process and `claude` — an EXTRA layer (e.g. a
    `bash -c '...'` wrapper invoking this command) makes a fixed-hop walk
    land on ANOTHER ephemeral shell instead of `claude`. That shell dies the
    instant its own tool call returns, so the recorded holder PID looks
    stale almost immediately, and a concurrent `/autopilot` session on the
    same repo can steal the "live" lock — reintroducing the exact #8
    collision this lock exists to prevent. Bounded by
    `_CAMPAIGN_ANCESTRY_MAX_HOPS` as a sanity cap (real ancestry chains are
    a handful of hops); if no long-lived process is ever found, the last
    pid reached is returned (never None/0) — same fail-safe shape as the
    old implementation's `grandparent or ppid`.
    """
    pid = os.getppid()
    seen = set()
    for _ in range(_CAMPAIGN_ANCESTRY_MAX_HOPS):
        if not pid or pid in seen:
            break
        seen.add(pid)
        if _proc_comm(pid) in _CAMPAIGN_LONG_LIVED_COMMS:
            return pid
        parent = _proc_parent_pid(pid)
        if not parent or parent == pid:
            break
        pid = parent
    return pid


def _pid_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else — still alive
    except Exception:
        return False


def _autopilot_lock_read(lock_path):
    try:
        return json.loads(lock_path.read_text())
    except Exception:
        return {}


def cmd_autopilot_lock(args):
    """Cross-session INTEGRATION mutex for /autopilot (issue #8; narrowed by
    #456 from a round-scope dispatch lock to integration-only).

    Under #456 this lock guards the merge->gates->push INTEGRATION cycle
    ONLY — one integration at a time per repo across ALL sessions.  DISPATCH
    is NEVER gated by it: continuous refill fires new worktree lanes whenever
    bundle-safe backlog remains, and N lanes running concurrently is the
    point.  The "serial per repo" rule (skills/autopilot/SKILL.md,
    two-branch-workflow.md) previously had only SESSION-LOCAL enforcement (a
    supervisor checks its own agent strip) — a SEPARATE `/autopilot` session
    on the same repo has no visibility into that and can run a colliding
    merge/push on the same repo at the same instant (camera-box #495, and the
    #499/#500-vs-#505 collision).

    `acquire` FAILS (exit 1) when a LIVE holder exists; a DEAD holder's lock
    is stolen (logged) and acquisition proceeds. `release` only removes a
    lock it actually owns (matched by pid) — it never touches someone
    else's lock, and is a no-op success when nothing is locked. `status` is
    a read-only report. The acquire critical section (check-then-write) is
    guarded by a brief `fcntl.flock` on a sibling `.mutex` file so two
    concurrent `acquire` calls on the SAME repo can't both win a
    stale-steal race — the lock's real persistence across the
    acquire/release CLI-invocation gap comes from the recorded holder PID
    staying alive (see `_campaign_pid`), not from the OS-held flock itself
    (which necessarily releases the instant this short-lived CLI process
    exits).
    """
    import fcntl
    from datetime import datetime, timezone

    action = args.action
    repo = args.repo or "."
    lock_path = _autopilot_lock_path(repo)
    holder_pid = args.pid if getattr(args, "pid", None) is not None else _campaign_pid()

    if action == "status":
        if not lock_path.exists():
            print(f"UNLOCKED {lock_path}")
            sys.exit(0)
        holder = _autopilot_lock_read(lock_path)
        alive = _pid_alive(holder.get("pid"))
        state = "LOCKED" if alive else "LOCKED (stale — holder pid dead)"
        print(f"{state} pid={holder.get('pid')} session={holder.get('session', '')} "
              f"since={holder.get('acquired_at', '')} repo={holder.get('repo', '')}")
        sys.exit(0)

    if action == "release":
        if not lock_path.exists():
            print(f"already unlocked: {lock_path}")
            sys.exit(0)
        holder = _autopilot_lock_read(lock_path)
        if holder.get("pid") == holder_pid:
            lock_path.unlink(missing_ok=True)
            print(f"RELEASED {lock_path}")
            sys.exit(0)
        print(f"REFUSING to release — held by a DIFFERENT holder "
              f"(pid={holder.get('pid')}, session={holder.get('session', '')}); "
              f"not releasing a lock this caller does not own.", file=sys.stderr)
        sys.exit(1)

    if action == "acquire":
        payload = {
            "pid": holder_pid,
            "session": args.session or "",
            "repo": str(Path(repo).resolve()),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        mutex_path = str(lock_path) + ".mutex"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        mfd = os.open(mutex_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(mfd, fcntl.LOCK_EX)
            if lock_path.is_dir():
                # A stale directory-shaped artifact (an older mkdir-style
                # lock implementation, or a manual mkdir) — `write_text`
                # below cannot write through a directory, so this must be
                # resolved BEFORE the exists()/read()/steal flow, never
                # discovered as an unhandled IsADirectoryError crash (#248,
                # hit live on dev2). An EMPTY directory is self-healed
                # (removed, acquisition proceeds exactly as if the path
                # never existed); a NON-EMPTY one is refused with a clear
                # message — deleting unknown directory contents is not this
                # command's call to make.
                try:
                    is_empty = not any(lock_path.iterdir())
                except OSError:
                    is_empty = False
                removed = False
                if is_empty:
                    try:
                        lock_path.rmdir()
                        removed = True
                    except OSError:
                        # A symlink to an empty directory reports is_dir()
                        # True and iterdir() succeeds, yet rmdir() itself
                        # raises NotADirectoryError (verified empirically) —
                        # a TOCTOU race (something repopulated the directory
                        # between the check above and here) raises
                        # "Directory not empty" the same way. Either way,
                        # fall through to the same clean refusal below —
                        # never an unhandled crash.
                        removed = False
                if not removed:
                    print(f"ERROR: lock path {lock_path} exists as a "
                          f"directory that could not be safely removed "
                          f"(non-empty, a symlink, or a filesystem race) — "
                          f"refusing to acquire. Inspect and remove it "
                          f"manually if safe: rm -rf {lock_path}",
                          file=sys.stderr)
                    sys.exit(1)
            elif lock_path.exists():
                holder = _autopilot_lock_read(lock_path)
                if _pid_alive(holder.get("pid")):
                    print(f"BLOCKED: {payload['repo']} is being INTEGRATED right "
                          f"now by another live session (holder pid={holder.get('pid')}, "
                          f"session={holder.get('session', '')}, "
                          f"since={holder.get('acquired_at', '')}). The #8 lock "
                          f"guards INTEGRATION exclusivity ONLY (narrowed by #456) — "
                          f"parallel worktree DISPATCH is never gated by it. exit 1 "
                          f"means: do NOT integrate this repo this turn; KEEP "
                          f"DISPATCHING new lanes and re-check next turn "
                          f"(`autopilot-lock status --repo {repo}`).",
                          file=sys.stderr)
                    sys.exit(1)
                # Holder's pid is dead — steal it, log the steal.
                steal_log = Path.home() / "devel" / "airuleset" / "audits" / "autopilot-lock-steals.log"
                steal_log.parent.mkdir(parents=True, exist_ok=True)
                with open(steal_log, "a") as f:
                    f.write(f"{datetime.now(timezone.utc).isoformat()}  "
                            f"repo={payload['repo']}  stole from dead "
                            f"pid={holder.get('pid')} session={holder.get('session', '')}\n")
            lock_path.write_text(json.dumps(payload))
            print(f"ACQUIRED {lock_path} pid={holder_pid}")
            sys.exit(0)
        finally:
            fcntl.flock(mfd, fcntl.LOCK_UN)
            os.close(mfd)


# --- Autopilot-lock litter one-time cleanup (#409, follow-up to #385) ------
# #385 fixed the ONGOING leak (test-spawned `autopilot-lock` subprocesses now
# redirect via AIRULESET_AUTOPILOT_LOCK_DIR) but never touched what had
# ALREADY accumulated in the real system tempdir before that fix landed --
# thousands of `.lock`/`.lock.mutex`/`.lock-real-target`/directory-shaped
# artifacts (measured live, #409 STEP 0: 5128 `.lock` files, 6799
# `.lock.mutex`, 1105 legacy directory-shaped locks, 544 `.lock` symlinks
# paired with 544 `.lock-real-target` directories). Nothing has ever swept
# it.
#
# The ticket's own suggested discriminator ("does the recorded repo path
# still exist") is EMPIRICALLY WRONG for this population: measured against
# all 5128 real `.lock` files, 100% still show their referenced repo
# tempdir as existing, because the `tempfile.mkdtemp()` directories the
# leaky pre-#385 tests created were THEMSELVES never cleaned up either (a
# second, compounding leak of the identical population). Using it as the
# sole discriminator would make this whole sweep a near-total no-op.
#
# The discriminator that actually works, and is SAFE BY CONSTRUCTION:
# `cmd_autopilot_lock`'s own `acquire` action ALREADY trusts
# `not _pid_alive(holder.get("pid"))` to decide "this holder is dead, safe
# to steal" (see the "Holder's pid is dead — steal it" branch above).
# Reusing that EXACT check to decide "safe to delete outright" instead of
# merely "safe to steal" cannot make this sweep MORE willing to disturb a
# lock than the already-shipped, already-trusted acquire() path is -- PID
# reuse can only make a genuinely-dead holder's recorded pid spuriously
# read as "alive" (a false negative, i.e. a harmless skip), never the
# reverse (a truly-alive holder's pid cannot spontaneously read as dead via
# os.kill(pid, 0) unless it has genuinely exited).
#
# One honest caveat on that "never the reverse" claim (#409 review finding
# 8): `acquire()` holds an flock on the `.mutex` sibling across its whole
# check-then-write critical section; this sweep is serialized against
# NOTHING, and `Path.write_text()` (acquire's own write) is non-atomic
# (truncate-then-write) -- so a truly-alive holder mid-write could in
# principle be caught with an EMPTY or partially-written lock file, which
# `_autopilot_lock_read` reads as `{}` and `_pid_alive(None)` reads as
# dead. The window is a handful of microseconds and needs a genuinely
# concurrent acquire on the EXACT same repo; the `min_age_s` floor above
# (default 1h) makes it vanishingly unlikely in practice, but the
# invariant is "PID reuse is the only false-negative source" for the
# STEADY-STATE case, not an absolute guarantee against every race.

AUTOPILOT_LOCK_LITTER_LOG_PATH = CLAUDE_DIR / "autopilot-lock-litter-sweep.log"
AUTOPILOT_LOCK_LITTER_STATE_PATH = CLAUDE_DIR / "autopilot-lock-litter-sweep-state.json"
AUTOPILOT_LOCK_LITTER_MIN_INTERVAL_S = 24 * 3600   # env AIRULESET_AUTOPILOT_LOCK_LITTER_SWEEP_INTERVAL_S
# Pure defense-in-depth (the real safety is the pid-liveness/emptiness
# discriminator itself, which can never be MORE permissive than
# cmd_autopilot_lock's own already-shipped acquire() self-heal logic) --
# refuses to even consider anything younger than this, in case a genuinely
# fresh acquire() somehow lands in the exact sweep window.
AUTOPILOT_LOCK_LITTER_MIN_AGE_S_DEFAULT = 3600     # env AIRULESET_AUTOPILOT_LOCK_LITTER_MIN_AGE_S


def _autopilot_lock_litter_min_age_s(explicit):
    """`explicit` if given; else the env override if it parses as a finite
    float; else the default. Same shared shape as `_min_age_days_env`
    above, just for a seconds-scale floor (never crashes on a typo'd env
    var, mirrors this repo's own established "unparseable override falls
    back to default" convention)."""
    if explicit is not None:
        return explicit
    raw = os.environ.get("AIRULESET_AUTOPILOT_LOCK_LITTER_MIN_AGE_S")
    if raw:
        try:
            v = float(raw)
        except ValueError:
            v = None
        if v is not None and v == v and v not in (float("inf"), float("-inf")):
            return v
    return AUTOPILOT_LOCK_LITTER_MIN_AGE_S_DEFAULT


def discover_autopilot_lock_litter(lock_dir=None, now=None, min_age_s=None):
    """Every `airuleset-autopilot-*`-named path in `lock_dir` (defaults to
    the SAME directory `_autopilot_lock_path()` itself resolves --
    `AIRULESET_AUTOPILOT_LOCK_DIR` when set, else the real system tempdir)
    that is safe to delete outright as pre-#385 litter. A list of dicts
    `{"path", "reason", "kind"?, "age_s"?}` -- `reason` is `None` for a
    genuine candidate, else WHY it was excluded (same discovery-shape
    contract as `discover_cli_version_candidates`/`discover_old_transcript_
    candidates` above). Pure and read-only -- deletes nothing.

    Per-shape safety criteria (NON-NEGOTIABLE):
      - a regular `.lock` FILE is litter iff its recorded holder pid is
        NOT alive (`not _pid_alive`) -- the exact discriminator
        `cmd_autopilot_lock`'s own `acquire` action already trusts to
        decide "safe to steal" (see the module comment above);
      - a `.lock` SYMLINK (the `TestDirectoryShapedLockPath` fixture's own
        symlink-to-empty-dir edge case, pre-#385, carries no JSON payload
        to read a pid from) is litter iff its `<name>-real-target`
        directory exists and is EMPTY;
      - a directory-shaped `.lock` (the pre-#248 legacy mkdir-style
        implementation) is litter iff EMPTY -- mirrors `acquire()`'s own
        directory self-heal verbatim;
      - a `.lock.mutex` sibling is litter iff its OWN base `.lock` (in
        whichever shape currently occupies that path) is either ABSENT
        entirely, or present AND itself confirmed litter above -- never
        touched while its base still represents a real, live-or-
        undetermined lock (that repo could legitimately re-acquire later,
        and the mutex costs nothing to keep);
      - a `.lock-real-target` directory is litter iff EMPTY and its paired
        `.lock` symlink is either absent or itself confirmed litter;
      - every candidate additionally needs `mtime` age >= `min_age_s`
        (default 3600s, env AIRULESET_AUTOPILOT_LOCK_LITTER_MIN_AGE_S) --
        pure defense-in-depth on top of the discriminators above.
    """
    import stat
    import tempfile as _tempfile
    import time as _time
    now = _time.time() if now is None else now
    min_age_s = _autopilot_lock_litter_min_age_s(min_age_s)
    if lock_dir is not None:
        ldir = Path(lock_dir)
    else:
        ldir = Path(os.environ.get("AIRULESET_AUTOPILOT_LOCK_DIR") or _tempfile.gettempdir())

    if not ldir.is_dir():
        return []

    try:
        names = sorted(os.listdir(ldir))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (ldir, e)}]

    prefix = "airuleset-autopilot-"
    lock_names, mutex_names, target_names = [], [], []
    for name in names:
        if not name.startswith(prefix):
            continue
        if name.endswith(".lock.mutex"):
            mutex_names.append(name)
        elif name.endswith(".lock-real-target"):
            target_names.append(name)
        elif name.endswith(".lock"):
            lock_names.append(name)

    def _too_recent(age_s):
        return age_s < min_age_s

    def _age_msg(age_s):
        return "too recent (%.0fs < %.0fs)" % (age_s, min_age_s)

    def _dir_empty(p):
        try:
            return not any(p.iterdir()), None
        except OSError as e:
            return False, "could not inspect directory: %s" % e

    out = []
    lock_litter = {}   # hash stem -> True (confirmed litter) / False (present, not litter)

    for name in lock_names:
        p = ldir / name
        stem = name[len(prefix):-len(".lock")]
        try:
            st = os.lstat(p)
        except OSError as e:
            out.append({"path": str(p), "reason": "could not stat: %s" % e, "kind": "lock"})
            continue
        age_s = now - st.st_mtime
        reason = None
        if st.st_uid != os.getuid():
            # #409 review finding 6: /tmp is sticky-bit -- a foreign-owned
            # artifact can never be unlinked by us anyway; refusing it here
            # (instead of attempting-and-failing every sweep, forever) avoids
            # permanent, unactionable "delete failed: Errno 1" churn on the
            # shared subdev/gk boxes (3 managed users each).
            kind = ("lock-symlink" if os.path.islink(p) else
                   "lock-dir" if p.is_dir() else "lock")
            reason = "owned by another user -- refused"
        elif os.path.islink(p):
            kind = "lock-symlink"
            target = ldir / (name + "-real-target")
            if _too_recent(age_s):
                reason = _age_msg(age_s)
            elif not target.is_dir():
                reason = "symlink target missing or not a directory -- refused"
            else:
                empty, err = _dir_empty(target)
                if err:
                    reason = err
                elif not empty:
                    reason = "symlink target directory not empty -- refused"
        elif p.is_dir():
            kind = "lock-dir"
            if _too_recent(age_s):
                reason = _age_msg(age_s)
            else:
                empty, err = _dir_empty(p)
                if err:
                    reason = err
                elif not empty:
                    reason = ("directory not empty -- refused (unknown contents, "
                             "not this sweep's call to make)")
        else:
            kind = "lock"
            if _too_recent(age_s):
                reason = _age_msg(age_s)
            elif not stat.S_ISREG(st.st_mode):
                # #409 review finding 1: a FIFO/socket/device node matching
                # this name pattern would hang _autopilot_lock_read()'s
                # open()/read() FOREVER (a FIFO blocks waiting for a writer
                # that never comes) -- proven live via an alarm-guarded
                # probe. /tmp is world-writable+sticky, this sweep matches
                # by NAME PREFIX ONLY, and cmd_install() runs it LIVE on
                # every push -- refuse anything not a plain regular file
                # before ever trying to read it.
                reason = "not a regular file -- refused"
            else:
                holder = _autopilot_lock_read(p)
                if _pid_alive(holder.get("pid")):
                    reason = "holder pid=%s still alive -- refused" % holder.get("pid")
        out.append({"path": str(p), "kind": kind, "age_s": age_s, "reason": reason})
        lock_litter[stem] = reason is None

    for name in mutex_names:
        p = ldir / name
        stem = name[len(prefix):-len(".lock.mutex")]
        try:
            st = os.lstat(p)
        except OSError as e:
            out.append({"path": str(p), "reason": "could not stat: %s" % e, "kind": "mutex"})
            continue
        age_s = now - st.st_mtime
        reason = None
        if st.st_uid != os.getuid():
            reason = "owned by another user -- refused"
        elif _too_recent(age_s):
            reason = _age_msg(age_s)
        elif lock_litter.get(stem) is False:
            reason = "base .lock still present and not litter -- refused"
        out.append({"path": str(p), "kind": "mutex", "age_s": age_s, "reason": reason})

    for name in target_names:
        p = ldir / name
        stem = name[len(prefix):-len(".lock-real-target")]
        try:
            st = os.lstat(p)
        except OSError as e:
            out.append({"path": str(p), "reason": "could not stat: %s" % e, "kind": "real-target"})
            continue
        age_s = now - st.st_mtime
        reason = None
        if st.st_uid != os.getuid():
            reason = "owned by another user -- refused"
        elif _too_recent(age_s):
            reason = _age_msg(age_s)
        elif p.is_symlink() or not p.is_dir():
            reason = "not a plain directory -- refused"
        else:
            empty, err = _dir_empty(p)
            if err:
                reason = err
            elif not empty:
                reason = "directory not empty -- refused"
            elif lock_litter.get(stem) is False:
                reason = "paired .lock symlink still present and not litter -- refused"
        out.append({"path": str(p), "kind": "real-target", "age_s": age_s, "reason": reason})

    return out


def _log_autopilot_lock_litter_sweep_results(results, log_path, now, dry_run: bool):
    """Append one line per row that ACTUALLY MATTERS -- #409 review finding
    2: the pre-#385 backlog on a real box is DOMINATED by rows that are
    permanently unreclaimable (a pid-1/agetty-pinned lock that can never
    die, a non-empty legacy directory) -- measured live, ~2800 of ~14000
    real rows on one box alone. Logging every routine "not litter, refused"
    SKIP would re-write the IDENTICAL line for those rows every 24h sweep,
    forever, on every managed box, growing this file without bound (the
    module's own claim that the sweep "becomes a permanent no-op" is true
    only of its ACTIONS, never of a naive unbounded log). A genuine ERROR
    (could not stat/list the directory) and a delete that discovery
    cleared but the delete call itself then failed on ARE still logged --
    those are the only two shapes worth a human ever reading this file
    for."""
    import time as _time
    lines = []
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    for r in results:
        if r.get("path") is None:
            lines.append("%s ERROR %s" % (ts, r.get("reason", "")))
            continue
        acted = (str(r.get("reason", "")).startswith("would remove")
                if dry_run else bool(r.get("removed")))
        failed_delete = (not dry_run and not r.get("removed")
                        and str(r.get("reason", "")).startswith("delete failed"))
        if not acted and not failed_delete:
            continue
        tag = ("WOULD-REMOVE" if dry_run else
              ("REMOVED" if r.get("removed") else "SKIP"))
        lines.append("%s %s %s kind=%s -- %s" % (
            ts, tag, r.get("path"), r.get("kind"), r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  autopilot-lock-litter-sweep: could not write log %s: %s" % (log_path, e),
              file=sys.stderr)


def sweep_autopilot_lock_litter(lock_dir=None, dry_run: bool = False, now=None,
                                log_path=None, state_path=None, force: bool = False,
                                min_age_s=None, candidates=None):
    """Reclaim every pre-#385 autopilot-lock litter artifact `discover_
    autopilot_lock_litter` classifies as a genuine candidate (`reason is
    None`) -- #409. Never deletes anything discovery already excluded;
    re-verifies immediately before EACH delete (a TOCTOU re-check,
    mirroring #315/#355's own re-verify-before-delete pattern) rather than
    trusting discovery-time state -- the pid-liveness re-check on a
    regular `.lock` file matters most here, since a fresh `acquire()`
    could in principle steal that exact lock in the gap between discovery
    and delete; directory removal (`rmdir()`) is naturally TOCTOU-safe on
    its own (POSIX refuses on a non-empty directory).

    Cadence-gated via its own state file, mirroring #315/#345/#355 exactly
    -- never leans on the 60s watchdog timer (FREEZE: no new job).
    `force=True` (the CLI's own manual invocation) or `dry_run=True`
    always bypasses the gate. Idempotent for its ACTIONS (never re-removes
    what a prior sweep already removed); NOT a full no-op even once the
    reclaimable backlog is cleared, since a real fraction of pre-#385 rows
    (a pid-1-pinned lock, a non-empty legacy directory) is permanently
    unreclaimable and keeps getting re-discovered -- see `_log_
    autopilot_lock_litter_sweep_results` for how that residual stays a
    cheap, silent re-scan instead of an unbounded log."""
    import stat
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else AUTOPILOT_LOCK_LITTER_LOG_PATH
    state_path = Path(state_path) if state_path else AUTOPILOT_LOCK_LITTER_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = AUTOPILOT_LOCK_LITTER_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get(
                "AIRULESET_AUTOPILOT_LOCK_LITTER_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = AUTOPILOT_LOCK_LITTER_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_autopilot_lock_litter(lock_dir, now=now, min_age_s=min_age_s)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"path": None, "removed": False,
                            "reason": "discovery error: %s" % e})

    for c in candidates:
        entry = dict(c)
        entry["removed"] = False
        if c.get("path") is None:
            results.append(entry)
            continue
        if c.get("reason"):
            results.append(entry)
            continue
        if dry_run:
            entry["reason"] = "would remove (dry-run)"
            results.append(entry)
            continue

        p = Path(c["path"])
        kind = c.get("kind")
        try:
            if kind == "lock":
                if p.is_symlink() or p.is_dir() or not p.is_file():
                    entry["reason"] = "no longer a plain regular lock file -- refused (re-checked)"
                    results.append(entry)
                    continue
                holder = _autopilot_lock_read(p)
                if _pid_alive(holder.get("pid")):
                    entry["reason"] = "holder became alive -- refused (re-checked)"
                    results.append(entry)
                    continue
                p.unlink()
            elif kind == "lock-symlink":
                if not p.is_symlink():
                    entry["reason"] = "no longer a symlink -- refused (re-checked)"
                    results.append(entry)
                    continue
                # #409 review finding 7: re-verify the TARGET's emptiness too,
                # not just symlink-ness -- a fresh writer could have started
                # populating it since discovery.
                target = Path(str(p) + "-real-target")
                if target.is_dir():
                    try:
                        target_non_empty = any(target.iterdir())
                    except OSError:
                        target_non_empty = True   # can't confirm empty -- refuse
                    if target_non_empty:
                        entry["reason"] = "symlink target became non-empty -- refused (re-checked)"
                        results.append(entry)
                        continue
                p.unlink()
            elif kind in ("lock-dir", "real-target"):
                if p.is_symlink() or not p.is_dir():
                    entry["reason"] = "no longer a plain directory -- refused (re-checked)"
                    results.append(entry)
                    continue
                p.rmdir()   # POSIX-atomic: raises on its own if non-empty
            elif kind == "mutex":
                # #409 review finding 7: re-verify the base .lock's state
                # too, not just delete unconditionally -- a fresh acquire()
                # could have re-created the base lock (as a regular file
                # with a live holder, the common shape) since discovery.
                base = p.parent / p.name[: -len(".mutex")]
                try:
                    bst = os.lstat(base)
                except OSError:
                    bst = None
                if bst is not None:
                    if stat.S_ISREG(bst.st_mode):
                        bholder = _autopilot_lock_read(base)
                        if _pid_alive(bholder.get("pid")):
                            entry["reason"] = "base .lock became alive -- refused (re-checked)"
                            results.append(entry)
                            continue
                    else:
                        # re-created as a symlink/directory shape -- a fresh
                        # acquire raced in since discovery; refuse rather
                        # than re-run the full classification here.
                        entry["reason"] = "base .lock reappeared -- refused (re-checked)"
                        results.append(entry)
                        continue
                p.unlink()
            else:
                entry["reason"] = "unknown kind -- refused"
                results.append(entry)
                continue
            entry["removed"] = True
            entry["reason"] = "removed"
        except OSError as e:
            entry["reason"] = "delete failed: %s" % e
        results.append(entry)

    _log_autopilot_lock_litter_sweep_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  autopilot-lock-litter-sweep: could not write state %s: %s" % (state_path, e),
                  file=sys.stderr)

    return results


def cmd_sweep_autopilot_locks(args):
    """`airuleset.py sweep-autopilot-locks [--dry-run] [--min-age-s N]` --
    manual/testable entry point for the #409 one-time autopilot-lock
    litter cleanup (a follow-up to #385, which stopped the ongoing leak
    but never swept what had already accumulated). Always `force=True`
    (bypasses the cadence gate that guards the automatic install/push
    wiring -- a deliberate manual call should never be silently skipped)."""
    print("airuleset sweep-autopilot-locks")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_s = getattr(args, "min_age_s", None)
    results = sweep_autopilot_lock_litter(dry_run=dry_run, force=True, min_age_s=min_age_s)
    for r in results:
        if r.get("path") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        acted = (str(r.get("reason", "")).startswith("would remove")
                if dry_run else bool(r.get("removed")))
        if acted:
            tag = "WOULD REMOVE" if dry_run else "REMOVED"
        else:
            tag = "skip"
        print("  %s: %s (kind %s) -- %s" % (
            tag, r["path"], r.get("kind"), r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    print()
    verb = "would be " if dry_run else ""
    print("%d autopilot-lock litter artifact(s) %sremoved." % (len(acted_rows), verb))
    print("Log: %s" % AUTOPILOT_LOCK_LITTER_LOG_PATH)
