"""Merged worktree-lane target/ reclaim (#545), split out of cli_worktree_sweep (#1194).

Moved VERBATIM. `purge_merged_lane_targets` deletes only the regenerable
`target/` of a MERGED lane that has an authored commit, and records a tier-0
bypass finding first. `cmd_purge_lane_targets` is `airuleset.py
sweep-lane-targets`. Its test seam `_iter_lane_target_dirs` is resolved in
THIS module's namespace: patch `cli_lane_target_reclaim.X`, not the facade.
Stdlib only at module level; the shared worktree plumbing comes from
`cli_worktree_common`.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

from cli_worktree_common import (
    CLAUDE_DIR,
    _STALE_WORKTREE_PROTECTED_BRANCHES,
    _worktree_env_age_s,
    _worktree_git,
    _worktree_porcelain_entries,
    _worktree_recency_age_s,
    _worktree_sweep_base_branch,
)


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
    from cli_reclaim_guard import refuse_real_paths_under_pytest as _refuse  # #1195
    _refuse("purge_merged_lane_targets", dry_run, home=home, log_path=log_path,
            state_path=state_path, bypass_state_path=bypass_state_path)

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
