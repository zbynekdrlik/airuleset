"""airuleset disk-hygiene sweeps (part 1/2) — shared disk helpers (#315) + old
Claude CLI binary sweep (#355) — cluster L sub-split #2 (#433).

The Tier-0 target/ purge (#315) that gave this module its name was retired
with the dev1 local-work doctrine (#1137); its live-use / dir-stat helpers
stay, shared by the lane-target reclaim, the scratch sweeps and the disk guard.

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as
watchdog/usage.py / burn_jobs.py / cards.py / repo_health.py and
cli_vault.py (H) / cli_autopilot_lock.py (K) / cli_burn.py (J) /
cli_quals.py+cli_quals_cmd.py (I) / cli_worktree_sweep.py (L1)).
airuleset.py keeps `from cli_target_purge import (...)` re-exports at the
old definition sites, so cmd_install's non-fatal sweep steps,
SUBCOMMANDS["sweep-cli-versions"] and tests' `airuleset.<helper>(...)`-style
direct references all keep working unchanged.

This is the BASE half of the disk-hygiene sweep region: it holds the four
shared disk-stat helpers (`_human_size`, `_target_in_live_use`,
`_dir_stats`, `_min_age_days_env`) that the sibling half
`cli_scratch_sweep.py` (claude-scratch #355 + transcript #410) forward-
imports. The dependency is strictly one-directional (cli_scratch_sweep ->
cli_target_purge), never back — no import cycle.

This module is deliberately SELF-CONTAINED: stdlib only at module level —
no top-level `import airuleset`, so there is no import-cycle surface in
either the CLI (`python3 airuleset.py`, airuleset running as `__main__`) or
the test (`import airuleset`) topology (internals note 1483). `CLAUDE_DIR`
below is this file's own copy of the canonical one-line expression
(`Path.home() / ".claude"`) that cli_worktree_sweep.py / cli_autopilot_lock.py /
watchdog/goal.py already inline locally today, established repo idiom.

One outbound coupling stays in airuleset.py and is reached via a lazily-
placed deferred `import airuleset` (internals note 1486): the PATH-repair
helper `_claude_cli_env` (used by _resolve_current_cli_version). Moving it here
would misplace a central symbol (the J/REMOTE_HOSTS decision). In CLI
`__main__` mode the deferred import triggers a one-time ~130 ms second
module execution but only on the install-time sweep paths, and is a plain
cache-hit from tests / other modules.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"


# --- Shared disk helpers (#315) -------------------------------------------

def _human_size(n) -> str:
    """1234567 -> '1.2MB'. Cheap du-style rendering for a log/report line."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0fB" % n if unit == "B" else "%.1f%s" % (n, unit)
        n /= 1024
    return "%.1fTB" % n


def _disk_usage_summary_line(path) -> str:
    """One-line disk-usage summary for `path`'s own filesystem -- #380
    point 4 (visibility): "97% full" must never be a surprise the next
    time someone reads a live box's own `install`/`push` console output.
    Extends the SAME print block every sweep step in `cmd_install()`
    already writes summaries into -- no new mechanism, no new state/log
    file, stdlib `shutil.disk_usage` only. `total == 0` (an unmeasurable
    or genuinely empty filesystem) never divides by zero."""
    du = shutil.disk_usage(str(path))
    pct_used = (100.0 * du.used / du.total) if du.total else 0.0
    return "Disk usage (%s): %.0f%% used, %s free" % (
        path, pct_used, _human_size(du.free))


def _target_in_live_use(target_dir, proc_dir=None) -> bool:
    """Mechanical, no-guessing substitute for "is there a live event/hot-
    swap using this build right now" -- approval-scope.md forbids ever
    ASKING the user about that (the user's hardest rule: NEVER gate on
    events/prod-usage/hardware). Instead: is any RUNNING process's
    executable, current working directory, or any open file descriptor
    currently pointing inside `target_dir`? If so -- or if this cannot be
    determined at all (no /proc, a read failure) -- this returns True and
    the caller SKIPS the whole target/, exactly the camera-box "never
    touch build artefacts while an event/hot-swap runs" rule, applied
    mechanically rather than by asking.

    Matches BOTH a link strictly INSIDE `target_dir` (the `startswith`
    check) and a link EQUAL to `target_dir` itself (#315 adversarial-
    review finding 8: a process whose `cwd`/`fd`/`exe` link is exactly
    `target_dir`, no trailing component -- a shell parked in target/, or a
    backup/file-manager process holding the bare directory open --
    `"/repo/target".startswith("/repo/target/")` alone is False and would
    have missed it).

    Known accepted residual (#315 adversarial-review finding 8,
    THEORETICAL): a per-PID `EACCES` (a foreign-uid process on a
    shared-uid box, or a root-owned service) is skipped individually and
    reads as "not using it" for THAT pid -- only a TOTAL /proc failure
    returns True. Unprivileged scanning cannot see into another uid's
    `/proc/<pid>/fd`; this is a structural limit, not a bug to fix here.
    """
    try:
        resolved_bare = os.path.realpath(str(target_dir))
    except OSError:
        return True
    resolved = resolved_bare + os.sep
    proc_dir = Path(proc_dir) if proc_dir is not None else Path("/proc")
    if not proc_dir.is_dir():
        return True
    try:
        pids = [p for p in os.listdir(proc_dir) if p.isdigit()]
    except OSError:
        return True
    for pid in pids:
        pdir = proc_dir / pid
        for name in ("exe", "cwd"):
            try:
                link = os.readlink(pdir / name)
            except OSError:
                continue
            if link == resolved_bare or link.startswith(resolved):
                return True
        fd_dir = pdir / "fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if link == resolved_bare or link.startswith(resolved):
                return True
    return False


def _dir_stats(path):
    """(total_size_bytes, newest_mtime_or_None) for every regular file
    under `path`, via one bounded `os.walk`. `os.lstat` (never `stat`) on
    each entry so a symlinked file inside the tree reports the LINK's own
    metadata rather than following it out -- pairs with `os.walk`'s own
    default `followlinks=False` for directories."""
    total = 0
    newest = None
    for dirpath, dirnames, filenames in os.walk(path, topdown=True, onerror=lambda e: None):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            total += st.st_size
            if newest is None or st.st_mtime > newest:
                newest = st.st_mtime
    return total, newest


# --- Old Claude CLI binary sweep (#355) -------------------------------------
# Every managed box installs the `claude` CLI natively (ensure_claude_cli_
# installed, #263): `~/.local/bin/claude` symlinks to ONE file inside
# `~/.local/share/claude/versions/<dotted-version>` (each ~280-300MB), and
# EVERY auto-update lays down a NEW versioned file while leaving the OLD
# one behind forever -- nothing has ever swept it. On subdev's 11 stream
# accounts this measured 9-12G reclaimable (each account carrying 3-4 old
# versions); on THIS box alone it was 4 versions / 1.2G (#355 STEP 0
# comment). Mirrors #315/#345's own shape exactly: discovery separated from
# destruction, own log+state file, cadence-gated (FREEZE: no new watchdog
# job, so a plain state-file stamp rate-limits this instead), wired as a
# non-fatal cmd_install() step plus a manual/testable CLI entry point.

CLI_VERSION_LOG_PATH = CLAUDE_DIR / "cli-version-sweep.log"
CLI_VERSION_STATE_PATH = CLAUDE_DIR / "cli-version-sweep-state.json"
CLI_VERSION_MIN_INTERVAL_S = 24 * 3600     # env AIRULESET_CLI_VERSION_SWEEP_INTERVAL_S
# Deliberately generous -- current+previous are KEPT unconditionally
# regardless of age (see discover_cli_version_candidates); this floor only
# protects a version ranked BELOW previous from being reclaimed while it
# might still be mid-download/mid-update-race.
CLI_VERSION_MIN_AGE_DAYS_DEFAULT = 2       # env AIRULESET_CLI_VERSION_MIN_AGE_DAYS
_CLI_VERSION_NAME_RX = re.compile(r"^\d+(\.\d+)+$")


def _min_age_days_env(explicit, env_key, default):
    """`explicit` if given (an actual `min_age_days=` CALL ARGUMENT always
    wins); else the env var `env_key` if it parses as a float; else
    `default`. Shared by both #355 sweeps below (#355 adversarial-review
    finding 2: the constant comments advertised `AIRULESET_CLI_VERSION_
    MIN_AGE_DAYS`/`AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS` but neither was
    ever actually read -- a silently no-op safety knob). An unparseable
    override falls back to `default`, never crashes the sweep over a
    typo'd env var (mirrors this repo's own established pattern for a
    cadence INTERVAL override, applied here to an AGE floor).

    `float("nan")` is explicitly refused too (#355 round-2 adversarial-
    review finding F3, live-executed): `"nan"` parses cleanly, but
    `age_days < nan` is `False` for EVERY value, which silently disables
    the ENTIRE age floor -- the one string that slips the docstring's own
    "never crashes" promise into "never PROTECTS" instead.
    `float("inf")` is deliberately still accepted (an operator setting it
    genuinely means "nothing is ever old enough" -- a legitimate,
    fail-SAFE disable switch, the opposite direction from `nan`)."""
    if explicit is not None:
        return explicit
    try:
        v = float(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return default
    return default if v != v else v   # v != v is the portable NaN test


def _cli_versions_dir(home=None) -> Path:
    """`~/.local/share/claude/versions/` -- the native installer's own
    layout (confirmed live, #355 STEP 0: a flat dir of version-named FILES,
    never a subdirectory-per-version)."""
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    return home / ".local" / "share" / "claude" / "versions"


def _cli_version_key(name: str):
    """Parse a dotted-decimal version NAME into a tuple of ints for sorting
    (e.g. "2.1.226" -> (2, 1, 226)). `None` when `name` does not match the
    strict `^\\d+(\\.\\d+)+$` shape -- never guessed; the caller refuses any
    entry this returns `None` for, individually, rather than assume it's
    "probably" a version."""
    if not _CLI_VERSION_NAME_RX.match(name):
        return None
    return tuple(int(p) for p in name.split("."))


def _resolve_current_cli_version(versions_dir, env=None):
    """The REAL, currently-live version FILE inside `versions_dir` --
    resolved via `shutil.which("claude")` (the same repaired-PATH
    `_claude_cli_env()` `_claude_cli_installed` already uses) followed by
    `os.path.realpath`, NEVER guessed from mtime (#355 design comment: a
    genuinely-current-but-manually-downgraded version must never look
    deletable just because a newer file happens to exist in the dir).

    Returns the resolved absolute path STRING, or `None` when it cannot be
    confidently determined -- `claude` not on PATH at all, or it resolves
    to something OUTSIDE `versions_dir` entirely (an unexpected install
    method: a system package, a different install layout). Callers MUST
    refuse the WHOLE sweep on `None`, never guess which file is "probably"
    current.

    Known, deliberate residual (#355 adversarial-review finding 5,
    THEORETICAL): unlike `_claude_cli_installed`, this deliberately does
    NOT fall back to a real LOGIN shell's own `command -v claude` (nvm/
    login-only PATH machinery) -- a box whose `claude` resolves ONLY that
    way refuses the whole CLI-version sweep FOREVER, correctly (never
    guessed), but that refusal is loud and logged as an ERROR row on
    every sweep, never silent."""
    import shutil as _shutil
    import airuleset
    e = env or airuleset._claude_cli_env()
    which = _shutil.which("claude", path=e.get("PATH", ""))
    if not which:
        return None
    try:
        resolved = os.path.realpath(which)
        vdir_resolved = os.path.realpath(str(versions_dir))
    except OSError:
        return None
    if os.path.dirname(resolved) != vdir_resolved:
        return None
    return resolved


def discover_cli_version_candidates(home=None, versions_dir=None, now=None,
                                    min_age_days=None, env=None, proc_dir=None):
    """Every installed Claude CLI version FILE under `~/.local/share/claude/
    versions/` that is safe to reclaim -- #355. A list of dicts
    `{"path", "version", "reason", "size"?, "age_days"?}` -- `reason` is
    `None` for a genuine candidate, else WHY it was excluded (mirrors
    `discover_stale_worktrees`'s own shape exactly). A discovery-level REFUSAL (current unresolvable, the
    dir unlistable) returns a SINGLE `{"path": None, "reason": ...}` row --
    the same ERROR-sentinel shape those two functions already use.

    Safety criteria (NON-NEGOTIABLE):
      - the CURRENT version (resolved via the real `~/.local/bin/claude`
        symlink target, never guessed) is NEVER a candidate;
      - the version ranked immediately BELOW current in a version-tuple-
        sorted-DESCENDING list is kept too (the rollback target) -- even
        when current is not the newest entry present (a manual downgrade,
        a newer build downloaded but not yet symlinked);
      - a THIRD, redundant guard: ANY entry whose own resolved realpath
        equals the resolved current path is kept regardless of index
        arithmetic -- belt-and-suspenders on the one truly non-negotiable
        invariant here ("NIKDY bežiacu/aktuálnu verziu");
      - an entry whose name does not parse as a plain dotted-decimal
        version, or that is not a plain regular file (never a symlink,
        never a directory), is refused INDIVIDUALLY -- unexpected layout,
        never guessed at;
      - if `versions_dir` doesn't exist at all, this returns `[]` (this box
        simply doesn't use the native install layout -- nothing to do, not
        an error); if it exists but the CURRENT version cannot be
        confidently resolved inside it, the WHOLE box is refused;
      - a surviving candidate still needs BOTH an age floor (mtime) AND a
        live-process check (`_target_in_live_use`, #315's own /proc
        exe-scan, reused verbatim -- catches a still-running OLD process
        that hasn't picked up a newer `current` yet) before being genuine.
    """
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_CLI_VERSION_MIN_AGE_DAYS",
                                     CLI_VERSION_MIN_AGE_DAYS_DEFAULT)
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    vdir = Path(versions_dir) if versions_dir else _cli_versions_dir(home)

    if not vdir.is_dir():
        return []

    try:
        names = sorted(os.listdir(vdir))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (vdir, e)}]

    current = _resolve_current_cli_version(vdir, env=env)
    if current is None:
        return [{"path": None,
                "reason": "current CLI version could not be confidently "
                          "resolved inside %s -- refusing the whole sweep "
                          "for this box" % vdir}]

    out = []
    parsed = []   # list of (key, name, path) -- name-parseable plain files only
    for name in names:
        p = vdir / name
        key = _cli_version_key(name)
        if key is None:
            out.append({"path": str(p), "version": name,
                       "reason": "name does not parse as a dotted-decimal "
                                 "version -- unexpected layout, skipped"})
            continue
        if p.is_symlink() or not p.is_file():
            out.append({"path": str(p), "version": name,
                       "reason": "not a plain regular file -- unexpected "
                                 "layout, skipped"})
            continue
        parsed.append((key, name, p))

    parsed.sort(key=lambda t: t[0], reverse=True)

    try:
        current_idx = next(i for i, (_, _, p) in enumerate(parsed)
                           if os.path.realpath(str(p)) == current)
    except StopIteration:
        # Never guess which discovered entry is "probably" current --
        # refuse the whole sweep (the ERROR row leads the result list; any
        # already-classified unexpected-layout rows above it stay reported
        # too, since a caller may still want to see the full picture).
        out.insert(0, {"path": None,
                      "reason": "resolved current version %s does not match "
                                "any discovered version entry -- refusing "
                                "the whole sweep for this box" % current})
        return out

    keep_idxs = {current_idx}
    if current_idx + 1 < len(parsed):
        keep_idxs.add(current_idx + 1)

    for i, (key, name, p) in enumerate(parsed):
        entry = {"path": str(p), "version": name, "reason": None}
        # Redundant guard (belt-and-suspenders on the non-negotiable
        # invariant): a resolved-path match against `current` is checked
        # independently of `i in keep_idxs`.
        try:
            is_current_path = os.path.realpath(str(p)) == current
        except OSError:
            is_current_path = False
        if is_current_path:
            entry["reason"] = "current version -- never deleted"
            out.append(entry)
            continue
        if i in keep_idxs:
            entry["reason"] = "rollback version (immediately below current) -- kept"
            out.append(entry)
            continue
        try:
            st = os.lstat(p)
        except OSError as e:
            entry["reason"] = "could not stat: %s" % e
            out.append(entry)
            continue
        entry["size"] = st.st_size
        age_days = (now - st.st_mtime) / 86400.0
        entry["age_days"] = age_days
        if age_days < min_age_days:
            entry["reason"] = "too recent (%.1fd < %sd)" % (age_days, min_age_days)
            out.append(entry)
            continue
        if _target_in_live_use(p, proc_dir=proc_dir):
            entry["reason"] = "in live use (or undeterminable) -- skipped"
            out.append(entry)
            continue
        out.append(entry)   # reason stays None -- genuine candidate

    return out


def _log_cli_version_sweep_results(results, log_path, now, dry_run: bool):
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
        lines.append("%s %s %s version=%s -- %s" % (
            ts, tag, r.get("path"), r.get("version"), r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  cli-version-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_stale_cli_versions(home=None, versions_dir=None, dry_run: bool = False,
                             now=None, log_path=None, state_path=None,
                             force: bool = False, min_age_days=None,
                             candidates=None, env=None, proc_dir=None):
    """Reclaim every stale CLI version `discover_cli_version_candidates`
    classifies as a genuine candidate (`reason is None`) -- #355. Never
    `--force`-deletes anything the discovery step already excluded;
    re-verifies "still a plain regular file, not in live use" immediately
    before EACH delete (a TOCTOU re-check, mirroring #315's own
    re-verify-before-delete pattern) rather than trusting discovery-time
    state.

    Cadence-gated via its own state file, mirroring #315/#345 exactly --
    never leans on the 60s watchdog timer (FREEZE: no new job).
    `force=True` (the CLI's own manual invocation) or `dry_run=True` always
    bypasses the gate."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else CLI_VERSION_LOG_PATH
    state_path = Path(state_path) if state_path else CLI_VERSION_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = CLI_VERSION_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_CLI_VERSION_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = CLI_VERSION_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_cli_version_candidates(
                home, versions_dir=versions_dir, now=now,
                min_age_days=min_age_days, env=env, proc_dir=proc_dir)
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
        try:
            if p.is_symlink() or not p.is_file():
                entry["reason"] = ("no longer a plain regular file -- refused "
                                   "(re-checked before delete)")
                results.append(entry)
                continue
            if _target_in_live_use(p, proc_dir=proc_dir):
                entry["reason"] = ("in live use (or undeterminable) -- refused "
                                   "(re-checked before delete)")
                results.append(entry)
                continue
            p.unlink()
            entry["removed"] = True
            entry["reason"] = "removed"
        except OSError as e:
            entry["reason"] = "delete failed: %s" % e
        results.append(entry)

    _log_cli_version_sweep_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  cli-version-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_cli_versions(args):
    """`airuleset.py sweep-cli-versions [--dry-run] [--min-age-days N]` --
    manual/testable entry point for the #355 CLI-version sweep. Always
    `force=True` (bypasses the cadence gate that guards the automatic
    install/push wiring -- a deliberate manual call should never be
    silently skipped)."""
    print("airuleset sweep-cli-versions")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_days = getattr(args, "min_age_days", None)
    results = sweep_stale_cli_versions(dry_run=dry_run, force=True, min_age_days=min_age_days)
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
        print("  %s: %s (version %s) -- %s" % (
            tag, r["path"], r.get("version"), r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    total = sum(r.get("size", 0) or 0 for r in acted_rows)
    print()
    verb = "would be " if dry_run else ""
    print("%d CLI version(s) %sremoved, %s %sreclaimed." % (
        len(acted_rows), verb, _human_size(total), verb))
    print("Log: %s" % CLI_VERSION_LOG_PATH)
