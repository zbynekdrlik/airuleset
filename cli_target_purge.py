"""airuleset disk-hygiene sweeps (part 1/2) — target/ purge (#315) + old
Claude CLI binary sweep (#355) — cluster L sub-split #2 (#433).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as
watchdog/usage.py / burn_jobs.py / cards.py / repo_health.py and
cli_vault.py (H) / cli_autopilot_lock.py (K) / cli_burn.py (J) /
cli_quals.py+cli_quals_cmd.py (I) / cli_worktree_sweep.py (L1)).
airuleset.py keeps `from cli_target_purge import (...)` re-exports at the
old definition sites, so cmd_install's non-fatal sweep steps,
SUBCOMMANDS["purge-targets"]/["sweep-cli-versions"] and tests'
`airuleset.purge_stale_tier0_targets(...)`-style direct references all keep
working unchanged.

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
and `REPO_DIR` below are this file's own copies of the canonical one-line
expressions (`Path.home() / ".claude"`, `Path(__file__).resolve().parent`)
that cli_worktree_sweep.py / cli_autopilot_lock.py / watchdog/goal.py
already inline locally today — identical value (this leaf is a sibling
top-level module in the same directory as airuleset.py), established repo
idiom.

Two outbound couplings stay in airuleset.py and are reached via a lazily-
placed deferred `import airuleset` (internals note 1486): the shared repo-
discovery helper `_checkout_roots` (used by discover_target_purge_candidates,
lives near `_local_checkout_for_repo`) and the PATH-repair helper
`_claude_cli_env` (used by _resolve_current_cli_version). Moving either here
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
REPO_DIR = Path(__file__).resolve().parent


# --- Tier-0 target/ retention (#315) ---------------------------------------
# Tier 0 (no-local-builds.md's DEFAULT) bans HEAVY local builds but still
# legitimately fills target/ via the cheap checks it DOES allow (cargo
# check/clippy/test --no-run -- ~500 MB/project by the skill's own
# estimate) and via historical eras (an earlier /fast-iterate window, a
# since-abandoned Tier 2 opt-in). Nothing ever purged it automatically --
# the local-builds skill's own purge rule is prose, invoked ON-DEMAND
# only (no caller anywhere in this repo) -- so growth is monotonic:
# songplayer 10.1G, spinbike 8.3G, camera-box 4.4G, ~23 GB of dead weight
# on dev1 alone, "znova a znova" (user, 2026-08-08).

TARGET_PURGE_LOG_PATH = CLAUDE_DIR / "target-purge.log"
TARGET_PURGE_STATE_PATH = CLAUDE_DIR / "target-purge-state.json"
TARGET_PURGE_MAX_AGE_DAYS_DEFAULT = 7
# Cadence gate for the AUTOMATIC install/push wiring only -- a direct CLI
# call (or dry_run) always runs regardless. FREEZE: no new watchdog job, so
# the sweep itself has to rate-limit ITSELF via a plain state-file stamp
# rather than lean on one.
TARGET_PURGE_MIN_INTERVAL_S = 24 * 3600
_TARGET_PURGE_SKIP_DIRS = (".git", "node_modules", "target")


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


def discover_target_purge_candidates(home=None, max_depth: int = 4):
    """Every `target/` directory that is a genuine cargo build artefact --
    its PARENT holds a `Cargo.toml` -- sitting inside a real checkout root
    (`_checkout_roots()`: `.git` as a directory OR a file, so a worktree/
    submodule counts too -- reused rather than re-walking `$HOME` a second
    time with a second, driftable definition of "repo root").

    Covers a workspace's own root `target/` AND a member crate's
    independent one (e.g. `sp-ui/target`) via a bounded per-repo walk.
    Never descends into `.git`, `node_modules`, or an already-found
    `target/` (no nested-target scanning -- a target/ tree has no cargo
    packages of its own worth discovering). `os.walk`'s own
    `followlinks=False` default means this can never leave the repo by
    following a symlinked directory.

    A NESTED checkout (its own `.git` inside an outer repo) is discovered
    TWICE -- once mis-attributed to the OUTER root by that root's own
    bounded walk, once correctly attributed to itself once
    `_checkout_roots()` reaches it directly -- so results are deduped by
    the target's own realpath, keeping the LAST attribution seen (#315
    adversarial-review finding 1's secondary cleanup). This is always the
    MORE SPECIFIC one: `_checkout_roots()` is a single topdown walk, which
    always yields an ancestor directory before any of its descendants, so
    the outer (less specific) attribution is always discovered FIRST.

    Returns a list of (target_dir, repo_root) Path pairs.
    """
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    seen = {}
    import airuleset
    for root in airuleset._checkout_roots(str(home)):
        root_p = Path(root)
        base_depth = str(root_p).rstrip("/").count("/")
        for dirpath, dirnames, filenames in os.walk(
                root_p, topdown=True, onerror=lambda e: None):
            depth = str(dirpath).rstrip("/").count("/") - base_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            has_target = "target" in dirnames
            dirnames[:] = [d for d in dirnames if d not in _TARGET_PURGE_SKIP_DIRS]
            if has_target and "Cargo.toml" in filenames:
                target_dir = Path(dirpath) / "target"
                try:
                    key = os.path.realpath(str(target_dir))
                except OSError:
                    key = str(target_dir)
                seen[key] = (target_dir, root_p)
    return list(seen.values())


def _tier0_via_hook(cwd, hook_path=None, timeout: int = 10) -> bool:
    """True iff `hooks/block-tier0-local-build.sh` would BLOCK a real
    `cargo build` from `cwd` -- i.e. Tier 0. That hook's own exit contract
    (its docstring): exit 2 = block (no marker, a managed Tier-0 project),
    exit 0 = allow (a Tier 1/2 marker present, OR no CLAUDE.md reachable
    at all -- an unmanaged directory, out of scope here either way).

    Literally SHELLS OUT to the real hook rather than re-implementing its
    CLAUDE.md upward-walk + marker regex a second time in Python -- #315's
    own design requirement (single source of truth for tier resolution;
    this repo has repeatedly been burned by a second, drifting
    implementation of the same check). The hook is pure bash + jq (no
    python dependency), fires in milliseconds, and is already the ONE
    place `no-local-builds.md`'s policy is enforced.

    Deliberate, accepted consequence (#315 adversarial-review finding 9,
    THEORETICAL, matches real `cargo build` behaviour exactly): a repo
    with NO CLAUDE.md of its own inherits the tier of the nearest ANCESTOR
    CLAUDE.md, same as a real build there would -- this is a property of
    the hook's own upward walk, not a gap specific to this caller.

    `cwd` must be the directory a real `cargo build` would actually run
    from (the crate directory holding the target/ in question, i.e.
    `target_dir.parent`) -- NEVER the enclosing checkout root, which can
    differ for a member crate or a nested checkout carrying its own tier
    marker (#315 adversarial-review finding 1, CRITICAL: passing the
    checkout root silently purged a Tier-1/2 crate's target/ whenever its
    own tier disagreed with its outer repo's).
    """
    hook_path = Path(hook_path) if hook_path else (REPO_DIR / "hooks" / "block-tier0-local-build.sh")
    if not hook_path.exists():
        return False
    import json as _json
    import subprocess
    payload = _json.dumps({"tool_input": {"command": "cargo build"}, "cwd": str(cwd)})
    env = dict(os.environ)
    # Strips every bypass env var the hook itself honours (currently just
    # this one) so the verdict is deterministic regardless of the CALLING
    # process's own environment -- grep hooks/block-tier0-local-build.sh
    # for `AIRULESET_ALLOW_` if it ever grows a second one.
    env.pop("AIRULESET_ALLOW_LOCAL_BUILD", None)  # deterministic regardless of caller's shell
    try:
        r = subprocess.run(["bash", str(hook_path)], input=payload,
                            capture_output=True, text=True, timeout=timeout, env=env)
    except Exception:
        return False
    return r.returncode == 2


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


def _log_target_purge_results(results, log_path, now, dry_run: bool):
    """Append one line per candidate examined (never silent -- comprehensive-
    logging.md: this is a destructive action, log everything, purge AND
    skip alike) to `log_path`. Best-effort: a log write failure never
    blocks the purge itself, but is reported (never a bare silent pass).

    A `target is None` entry (a DISCOVERY error -- the sweep never even
    got a candidate list) is logged too, as `target=-` (#315 adversarial-
    review finding 7: previously silently dropped, so a persistent
    discovery bug went completely untraceable)."""
    import datetime as _dt
    ts = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).isoformat()
    lines = []
    for r in results:
        if r.get("target") is None:
            lines.append("%s ERROR - repo=- reason=%s" % (ts, r.get("reason", "")))
            continue
        if r["purged"]:
            action = "DRYRUN-WOULD-PURGE" if dry_run else "PURGED"
        else:
            action = "SKIP"
        size = r.get("size")
        size_txt = " size=%s" % _human_size(size) if size is not None else ""
        lines.append("%s %s %s repo=%s%s reason=%s" % (
            ts, action, r["target"], r.get("repo", ""), size_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  target-purge: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def purge_stale_tier0_targets(home=None, max_age_days=None, dry_run: bool = False,
                              now=None, log_path=None, state_path=None,
                              force: bool = False, hook_path=None,
                              max_depth: int = 4, proc_dir=None,
                              candidates=None):
    """Delete a MAINTAINED Tier-0 repo's stale `target/` (workspace root or
    a member crate's own, e.g. `sp-ui/target`) -- #315.

    A candidate is purged only when ALL of these hold:
      - it is a real cargo build artefact inside a real checkout
        (`discover_target_purge_candidates`, unless `candidates=` is
        passed directly -- used by tests/callers that already have the
        pair list);
      - `_tier0_via_hook` says the repo is genuinely Tier 0 (no `=allowed`/
        `=fast-iterate` marker -- those are NEVER touched -- and NOT an
        unmanaged directory with no CLAUDE.md at all);
      - `_target_in_live_use` finds no process currently using it (the
        mechanical hot-swap/event guard -- never asks the user);
      - its newest mtime (recursively) is older than `max_age_days`
        (default 7) -- a directory with ZERO files inside is treated as
        infinitely stale (nothing to lose).

    `target_dir` is refused outright if it is itself a symlink, or if its
    RESOLVED path escapes the repo root (a symlink pointing elsewhere) --
    never followed, never deleted through.

    Returns a list of per-candidate dicts (`target`, `repo`, `purged`,
    `reason`, `size`, `age_days`) -- always, even a cadence-gated no-op run
    returns `[]`. Every candidate is appended to `log_path` (default
    ~/.claude/target-purge.log) with its size, purge or skip alike.

    Cadence: the automatic install/push wiring runs this at most once per
    `TARGET_PURGE_MIN_INTERVAL_S` (a small state file, not a new watchdog
    job -- the FREEZE forbids a new job; rate-limiting a plain function
    call needs none). `force=True` (the CLI's own manual invocation) or
    `dry_run=True` (a diagnostic run) always bypasses the gate.
    """
    import time as _time
    now = _time.time() if now is None else now
    max_age_days = TARGET_PURGE_MAX_AGE_DAYS_DEFAULT if max_age_days is None else max_age_days
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    log_path = Path(log_path) if log_path else TARGET_PURGE_LOG_PATH
    state_path = Path(state_path) if state_path else TARGET_PURGE_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        # #315 adversarial-review finding 5: a stamp in the FUTURE (an NTP
        # correction, a restored VM snapshot) makes `now - last` negative,
        # unconditionally < the interval -- clamp it to "no prior run"
        # rather than let a bad clock wedge the gate closed forever.
        if last > now:
            last = 0
        if now - last < TARGET_PURGE_MIN_INTERVAL_S:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_target_purge_candidates(home, max_depth=max_depth)
        except Exception as e:
            candidates = []
            discovery_failed = True
            results.append({"target": None, "purged": False,
                            "reason": "discovery error: %s" % e})

    for target_dir, repo_root in candidates:
        target_dir = Path(target_dir)
        repo_root = Path(repo_root)
        entry = {"target": str(target_dir), "repo": str(repo_root), "purged": False}
        try:
            if target_dir.is_symlink():
                entry["reason"] = "symlink target/ -- never followed"
                results.append(entry)
                continue
            try:
                resolved = target_dir.resolve()
                resolved.relative_to(repo_root.resolve())
            except (OSError, ValueError):
                entry["reason"] = "resolved path escapes repo root -- skipped"
                results.append(entry)
                continue

            # #315 adversarial-review finding 1 (CRITICAL): tier must be
            # resolved against the directory a REAL `cargo build` would
            # actually run from -- target_dir.parent (the crate directory)
            # -- never repo_root. A member crate (or a nested checkout)
            # carrying its OWN marker, inside a markerless outer repo,
            # must be classified by ITS OWN CLAUDE.md, exactly like a real
            # build there would be; using repo_root silently deletes a
            # Tier-1/2 crate's target/ whenever its OWN tier differs from
            # its outer repo's.
            if not _tier0_via_hook(str(target_dir.parent), hook_path=hook_path):
                entry["reason"] = "not Tier 0 (allowed/fast-iterate marker, or unmanaged)"
                results.append(entry)
                continue

            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- skipped"
                results.append(entry)
                continue

            size_bytes, newest_mtime = _dir_stats(target_dir)
            entry["size"] = size_bytes
            age_days = float("inf") if newest_mtime is None else (now - newest_mtime) / 86400.0
            entry["age_days"] = None if age_days == float("inf") else age_days

            if age_days < max_age_days:
                entry["reason"] = "fresh (%.1fd < %sd)" % (age_days, max_age_days)
                results.append(entry)
                continue

            # #315 adversarial-review finding 2: _dir_stats can take a
            # while on a large tree -- re-verify nothing started using
            # target/ in that window, immediately before the actual
            # delete, rather than trusting the check made before the walk.
            if _target_in_live_use(target_dir, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- skipped (re-checked before delete)"
                results.append(entry)
                continue

            age_txt = "empty" if age_days == float("inf") else "%.1fd" % age_days
            entry["reason"] = "stale (%s >= %sd), %s" % (
                age_txt, max_age_days, _human_size(size_bytes))
            if not dry_run:
                shutil.rmtree(target_dir)
            entry["purged"] = True
            results.append(entry)
        except Exception as e:
            entry["reason"] = "error: %s" % e
            results.append(entry)

    _log_target_purge_results(results, log_path, now, dry_run)

    # #315 adversarial-review finding 7: never stamp the cadence gate when
    # discovery itself failed -- nothing was actually examined, so the
    # sweep must retry on the VERY NEXT tick rather than sitting silent
    # (at most once/day) behind a stamp that claims a real run happened.
    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  target-purge: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_purge_targets(args):
    """`airuleset.py purge-targets [--dry-run] [--max-age-days N]` -- manual/
    testable entry point for the #315 sweep. Always `force=True` (bypasses
    the once/day cadence gate that guards the automatic install/push
    wiring -- a deliberate manual call should never be silently skipped)."""
    print("airuleset purge-targets")
    print("=" * 50)
    max_age_days = getattr(args, "max_age_days", None)
    dry_run = bool(getattr(args, "dry_run", False))
    results = purge_stale_tier0_targets(max_age_days=max_age_days, dry_run=dry_run, force=True)
    for r in results:
        if r.get("target") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        if r["purged"]:
            tag = "WOULD PURGE" if dry_run else "PURGED"
        else:
            tag = "skip"
        print("  %s: %s -- %s" % (tag, r["target"], r.get("reason", "")))
    purged = [r for r in results if r.get("purged")]
    total = sum(r.get("size", 0) or 0 for r in purged)
    print()
    verb = "would be " if dry_run else ""
    print("%d target/ dir(s) %spurged, %s %sreclaimed." % (
        len(purged), verb, _human_size(total), verb))
    print("Log: %s" % TARGET_PURGE_LOG_PATH)


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
    `discover_stale_worktrees`/`discover_target_purge_candidates`'s own
    shape exactly). A discovery-level REFUSAL (current unresolvable, the
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
