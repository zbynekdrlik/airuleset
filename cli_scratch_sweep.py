"""airuleset disk-hygiene sweeps (part 2/2) — Claude scratch/tmp sweep
(#355) + transcript gzip-at-rest retention (#410) — cluster L sub-split #2
(#433).

Extracted VERBATIM from airuleset.py (#404 point 3 module split; #433
continuation — same verbatim-move + facade-re-export pattern as the sibling
`cli_target_purge.py` and the earlier H/K/J/I/L1 leaves). airuleset.py keeps
`from cli_scratch_sweep import (...)` re-exports at the old definition
sites, so cmd_install's non-fatal sweep steps,
SUBCOMMANDS["sweep-claude-scratch"]/["sweep-transcripts"] and tests'
`airuleset.sweep_old_transcripts(...)`-style direct references all keep
working unchanged.

This is the SIBLING half of the disk-hygiene sweep region. Its sweeps reuse
the four shared disk-stat helpers that live in the BASE half — imported
directly below (`from cli_target_purge import ...`). The dependency is
strictly one-directional (this module -> cli_target_purge), never back — no
import cycle.

Deliberately SELF-CONTAINED beyond that one sibling import: stdlib only at
module level, no top-level `import airuleset` (internals note 1483) — this
half has ZERO airuleset.py-resident outbound couplings of its own (all of
them are in the base half). `CLAUDE_DIR` below is this file's own copy of
the canonical one-line expression (`Path.home() / ".claude"`), identical
value, established repo idiom.
"""

import gzip
import json
import os
import re
import shutil
import sys
import zlib
from pathlib import Path

# Test-seam note (#433): these four helpers re-bind into THIS module's own
# globals at import, so a File-B sweep resolves them HERE. A test patching a
# shared helper while driving a File-B function must target
# `cli_scratch_sweep.<helper>` (e.g. gzip.open, _target_in_live_use), NOT
# `cli_target_purge.<helper>`/`airuleset.<helper>` — both halves call
# `_target_in_live_use`, so the correct patch target differs per sweep.
from cli_target_purge import (
    _human_size,
    _target_in_live_use,
    _dir_stats,
    _min_age_days_env,
)

CLAUDE_DIR = Path.home() / ".claude"


# --- Claude scratch/tmp sweep (#355) ----------------------------------------
# Every Claude Code session writes into `/tmp/claude-<uid>/<encoded-cwd>/
# <session-id>/scratchpad/...` (the harness's own convention -- this very
# session's scratchpad lives there) plus, in practice, loose scratch files
# dropped directly at the per-uid root. Nothing has ever swept it -- the
# worktree sweep (#345/#348) is scoped strictly to `.claude/worktrees/`
# git worktrees and never touches `/tmp` at all. Measured live on THIS box:
# 42 entries, 1.4G under /tmp/claude-1000 (#355 STEP 0 comment) -- and a
# same-owner, DIFFERENTLY-NAMED sibling (`/tmp/claude-286`) sits right next
# to it, proving name-only matching is not a safe enough anchor (see
# discover_claude_scratch_candidates's own docstring).

CLAUDE_SCRATCH_LOG_PATH = CLAUDE_DIR / "claude-scratch-sweep.log"
CLAUDE_SCRATCH_STATE_PATH = CLAUDE_DIR / "claude-scratch-sweep-state.json"
CLAUDE_SCRATCH_MIN_INTERVAL_S = 24 * 3600   # env AIRULESET_CLAUDE_SCRATCH_SWEEP_INTERVAL_S
CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT = 7      # env AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS


def _claude_scratch_root(tmp_dir=None, uid=None) -> Path:
    """`<tmp_dir>/claude-<uid>` -- THIS account's own per-uid Claude Code
    scratch root (session scratchpads + loose working files -- exactly the
    directory this very session's own scratchpad lives under). `tmp_dir`
    defaults to `/tmp`; `uid` defaults to `os.getuid()`."""
    tmp_dir = Path(tmp_dir) if tmp_dir else Path("/tmp")
    uid = os.getuid() if uid is None else uid
    return tmp_dir / ("claude-%d" % uid)


def discover_claude_scratch_candidates(tmp_dir=None, uid=None, now=None,
                                       min_age_days=None, proc_dir=None):
    """Every direct child (file OR directory) of THIS account's OWN
    `/tmp/claude-<uid>/` scratch root that is safe to reclaim -- #355. A
    list of dicts `{"path", "reason", "size"?, "age_days"?}` -- `reason` is
    `None` for a genuine candidate, else WHY it was excluded.

    Safety criteria (NON-NEGOTIABLE):
      - the root must be NAMED `claude-<N>` where N is LITERALLY
        `str(uid)` for THIS account, AND independently confirmed owned
        (`st_uid`) by that SAME uid -- both checks, never just one (a
        same-owner-but-DIFFERENTLY-NAMED sibling proves name alone is not
        a safe enough anchor -- live on this very box, see the module
        comment above). If the root doesn't exist, isn't a directory, is
        itself a symlink, or the ownership check fails, this returns `[]`
        -- and critically, NO OTHER user's `/tmp` content is EVER even
        listed, let alone touched;
      - a candidate's age is the NEWEST mtime found ANYWHERE inside its
        own subtree (`_dir_stats`'s recursive newest-file walk for a
        directory, or the bare file's own mtime) -- never the top entry's
        OWN mtime alone, so a session still actively writing somewhere
        deep inside an old-looking sibling tree is never wrongly judged
        idle (this is the "nikdy scratch ŽIVEJ session" mtime/age
        poistka). An EMPTY subtree (`_dir_stats` finds no file at all --
        the harness pre-creates `<cwd>/<session>/scratchpad` empty at
        session start, before a live session has written anything) falls
        back to the DIRECTORY's OWN mtime, NEVER "infinitely stale"
        (#355 adversarial-review finding 1, MAJOR, live-confirmed on
        dev1 -- unlike #315's target/ purge, where an empty `target/`
        genuinely has zero bytes to protect, an empty scratch tree has a
        live SESSION to protect instead);
      - a symlinked child is refused outright -- never followed, never
        deleted through;
      - a candidate still needs BOTH the age floor AND a live-process
        check (`_target_in_live_use`) before being genuine.

    Known, deliberate residuals (round 1/round 2 adversarial review,
    THEORETICAL, none closed under the FREEZE -- no new watchdog job):
      - finding 4: a session tmux-parked idle for MORE than `min_age_days`
        with no cwd/fd currently held inside its own tree can still have
        real (non-empty) scratch data reclaimed -- the live-use check
        only sees processes ACTIVELY holding a reference, and mtime only
        sees recent writes, neither of which "this session is parked but
        will resume" can express. Same residual every mtime-based ager in
        this repo accepts. A parked session whose tree stayed EMPTY the
        whole time hits the SAME gap through the empty-tree fallback
        above (round-2 finding F2) -- strictly weaker (zero bytes lost;
        worst case one failed scratch write later);
      - round-2 finding F1: `_dir_stats`'s `onerror=lambda e: None` walk
        silently SKIPS a subdirectory it cannot read -- a same-uid
        mode-000 dir hiding a genuinely FRESH file, under an otherwise
        stale top-level mtime, still reads as "empty" and ages by the
        stale fallback. Live-executed and confirmed reachable, but needs
        a self-inflicted unreadable subdir to trigger; not present in any
        real fleet scratch tree observed so far.
    """
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS",
                                     CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT)
    uid = os.getuid() if uid is None else uid
    root = _claude_scratch_root(tmp_dir, uid)

    if root.is_symlink() or not root.is_dir():
        return []
    try:
        if os.stat(str(root)).st_uid != uid:
            return []
    except OSError:
        return []

    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (root, e)}]

    out = []
    for name in names:
        p = root / name
        out.append(_classify_scratch_entry(p, now, min_age_days, proc_dir))

    return out


def _classify_scratch_entry(p, now, min_age_days, proc_dir):
    """Shared per-entry safety classification for `discover_claude_scratch_
    candidates` (#355) AND `discover_stray_worktree_tmp_candidates` (#380) --
    the TWO discovery functions share this ONE implementation of every
    safety rule (symlink-never-followed, empty-tree mtime fallback,
    age-floor, live-process guard) so a future safety fix in one applies
    to both automatically; only the ROOT GLOB differs between the two
    callers, never the per-entry logic. Returns `{"path", "reason", ...}`,
    `reason` is `None` for a genuine candidate."""
    entry = {"path": str(p), "reason": None}
    if p.is_symlink():
        entry["reason"] = "symlink entry -- never followed, never deleted through"
        return entry
    try:
        if p.is_dir():
            size_bytes, newest_mtime = _dir_stats(p)
            if newest_mtime is None:
                # #355 adversarial-review finding 1 (MAJOR, live-
                # confirmed on dev1): the harness pre-creates
                # <cwd>/<session>/scratchpad EMPTY at session start,
                # before a live session has written anything -- an
                # empty tree must NEVER read as "infinitely stale"
                # (unlike #315's target/ purge, where an empty
                # target/ genuinely has zero bytes to protect and
                # "always reclaimable" is correct there). Fall back
                # to the DIRECTORY's OWN mtime so a tree created
                # seconds ago stays protected by the age floor
                # exactly like a non-empty one would.
                newest_mtime = os.lstat(p).st_mtime
        else:
            st = os.lstat(p)
            size_bytes, newest_mtime = st.st_size, st.st_mtime
    except OSError as e:
        entry["reason"] = "could not stat: %s" % e
        return entry
    entry["size"] = size_bytes
    age_days = (now - newest_mtime) / 86400.0
    entry["age_days"] = age_days
    if age_days < min_age_days:
        entry["reason"] = "too recent (%.1fd < %sd)" % (age_days, min_age_days)
        return entry
    if _target_in_live_use(p, proc_dir=proc_dir):
        entry["reason"] = "in live use (or undeterminable) -- skipped"
        return entry
    return entry   # reason stays None -- genuine candidate


# --- Stray worktree tmp sweep (#380) -----------------------------------
# The ticket names `/tmp/wt-*` as a litter shape observed on gk/subdev
# (~1GB gk, 6x per subdev account) alongside .claude/worktrees leaks -- a
# DIFFERENT root than #355's own `/tmp/claude-<uid>/` scope
# (`_claude_scratch_root`), which never lists a bare top-level `/tmp/wt-*`
# entry at all. What genuinely GENERATES this shape on gk/subdev could
# NOT be confirmed from dev1 alone (no ssh access; dev1 itself has zero
# `/tmp/wt-*` entries right now -- see the #380 design comment for what
# was and was not confirmed, incl. a grep of this repo's own code and the
# installed Claude Code binary's strings, neither of which found a
# `wt-`-prefixed tmpdir generator). Rather than wait on an unconfirmed
# origin, this sweep is GENERIC by PATTERN (not by origin): any top-level
# `<tmp_dir>/wt-*` entry owned by the current uid is classified with the
# EXACT SAME safety criteria #355's own scratch sweep already uses
# (`_classify_scratch_entry`) -- symlink-never-followed, empty-tree
# mtime fallback, age floor, live-process guard. An empty match on dev1
# costs nothing; if the shape is confirmed live on gk/subdev at the next
# push, this genuinely reclaims it there.

def discover_stray_worktree_tmp_candidates(tmp_dir=None, uid=None, now=None,
                                           min_age_days=None, proc_dir=None):
    """Every top-level `<tmp_dir>/wt-*` entry OWNED by THIS account --
    #380. Same shape/return as `discover_claude_scratch_candidates`, same
    safety rules via the shared `_classify_scratch_entry` helper -- this
    function supplies only a DIFFERENT glob root, never different safety
    logic. Ownership is checked via `os.lstat` (never following a
    symlink) BEFORE classification, so a foreign-uid `wt-*` entry is
    never even listed, let alone touched -- mirrors #355's own
    `_claude_scratch_root` double-check (name AND ownership), just with
    no fixed name to check since `wt-*` is a bare prefix any user on a
    shared box could create."""
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS",
                                     CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT)
    uid = os.getuid() if uid is None else uid
    root = Path(tmp_dir) if tmp_dir else Path("/tmp")

    if not root.is_dir():
        return []
    try:
        candidates = sorted(root.glob("wt-*"))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (root, e)}]

    out = []
    for p in candidates:
        try:
            lst = os.lstat(str(p))
        except OSError:
            continue   # gone between glob and lstat -- nothing to report
        if lst.st_uid != uid:
            continue   # never even classify a foreign-owned wt-* entry
        out.append(_classify_scratch_entry(p, now, min_age_days, proc_dir))

    return out


def _log_claude_scratch_results(results, log_path, now, dry_run: bool):
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
        size = r.get("size")
        size_txt = " size=%s" % _human_size(size) if size is not None else ""
        lines.append("%s %s %s%s -- %s" % (
            ts, tag, r.get("path"), size_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  claude-scratch-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_claude_scratch(tmp_dir=None, uid=None, dry_run: bool = False,
                         now=None, log_path=None, state_path=None,
                         force: bool = False, min_age_days=None,
                         candidates=None, proc_dir=None):
    """Reclaim every stale claude scratch/tmp path EITHER
    `discover_claude_scratch_candidates` (#355, `/tmp/claude-<uid>/*`) OR
    `discover_stray_worktree_tmp_candidates` (#380, top-level `/tmp/wt-*`)
    classifies as a genuine candidate (`reason is None`). Re-verifies
    "still not a symlink, still exists, still not in live use"
    immediately before EACH delete (a TOCTOU re-check), rather than
    trusting discovery-time state.

    #380: the two discovery sources are combined the SAME way
    `sweep_stale_worktrees` already combines `discover_stale_worktrees` +
    `discover_orphaned_worktree_branches` -- if EITHER raises, nothing
    from EITHER source is trusted (a partial discovery is not safer than
    none at all); this ships the /tmp/wt-* litter shape to every managed
    box through the SAME cadence gate, log, and state file, no new
    mechanism.

    Cadence-gated via its own state file, mirroring #315/#345/the CLI-
    version sweep exactly -- never leans on the 60s watchdog timer
    (FREEZE: no new job). `force=True` (the CLI's own manual invocation) or
    `dry_run=True` always bypasses the gate."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else CLAUDE_SCRATCH_LOG_PATH
    state_path = Path(state_path) if state_path else CLAUDE_SCRATCH_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0
        interval = CLAUDE_SCRATCH_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_CLAUDE_SCRATCH_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = CLAUDE_SCRATCH_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_claude_scratch_candidates(
                tmp_dir, uid=uid, now=now, min_age_days=min_age_days, proc_dir=proc_dir)
            candidates = candidates + discover_stray_worktree_tmp_candidates(
                tmp_dir, uid=uid, now=now, min_age_days=min_age_days, proc_dir=proc_dir)
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
            if p.is_symlink():
                entry["reason"] = "symlink entry -- refused (re-checked before delete)"
                results.append(entry)
                continue
            if not p.exists():
                entry["reason"] = "already gone"
                results.append(entry)
                continue
            if _target_in_live_use(p, proc_dir=proc_dir):
                entry["reason"] = ("in live use (or undeterminable) -- refused "
                                   "(re-checked before delete)")
                results.append(entry)
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            entry["removed"] = True
            entry["reason"] = "removed"
        except OSError as e:
            entry["reason"] = "delete failed: %s" % e
        results.append(entry)

    _log_claude_scratch_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  claude-scratch-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_claude_scratch(args):
    """`airuleset.py sweep-claude-scratch [--dry-run] [--min-age-days N]` --
    manual/testable entry point for the #355 scratch/tmp sweep. Always
    `force=True` (bypasses the cadence gate that guards the automatic
    install/push wiring)."""
    print("airuleset sweep-claude-scratch")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_days = getattr(args, "min_age_days", None)
    results = sweep_claude_scratch(dry_run=dry_run, force=True, min_age_days=min_age_days)
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
        print("  %s: %s -- %s" % (tag, r["path"], r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would remove")
                     if dry_run else r.get("removed"))]
    total = sum(r.get("size", 0) or 0 for r in acted_rows)
    print()
    verb = "would be " if dry_run else ""
    print("%d claude scratch path(s) %sremoved, %s %sreclaimed." % (
        len(acted_rows), verb, _human_size(total), verb))
    print("Log: %s" % CLAUDE_SCRATCH_LOG_PATH)


# --- Stray tempfile.mkdtemp litter sweep (#513) ----------------------------
# The batch-38 ENOSPC (`mkdtemp` `/tmp/tmpuoq3_vff`, tests/conftest.py's own
# `TemporaryDirectory()` fixture) was NOT inode/space exhaustion (44% inodes,
# 20G free) -- it was the ext4 htree DIRECTORY-INDEX cap: a `/tmp` holding
# 500k+ direntries returns ENOSPC on the next `mkdtemp` in `/tmp` even with
# free space/inodes (explains the transient "isolated rerun green"). Live on
# dev1: 503,113 uid-owned `tmp[a-z0-9_]{8}` entries, aged days -- test runs'
# own leaked `tempfile.mkdtemp`/`mkstemp` scratch. `sweep_claude_scratch`
# only targets `/tmp/claude-<uid>/*` + `/tmp/wt-*`, never these. This is the
# same discovery/action-split, own-log+state, cadence-gated shape, but with
# THREE deliberate differences forced by scale + breadth:
#   - the precise Python-tempfile signature regex `^tmp[a-z0-9_]{8}$` (NOT
#     a bare `tmp*` glob -- that also matches tmux-*, tmp.* and any app's
#     tempfile) + uid-ownership = "clearly-owned stale artifact" (constraint #3);
#   - a SINGLE inverted /proc live-use scan (`_scan_live_tmp_tops`) instead of
#     `_target_in_live_use` PER entry (500k x a full /proc walk is infeasible),
#     and TOP-LEVEL mtime for age instead of a recursive `_dir_stats` walk;
#   - REPORT-ONLY by default (a loud count/reclaimable summary), live delete
#     only under AIRULESET_TMP_PYTEST_RECLAIM_LIVE=1 -- mirrors the transcript
#     `AIRULESET_TRANSCRIPT_COMPRESS_LIVE=1` gate for a new broad destructive
#     sweep, so the supervisor reviews the report before any reclaim runs.

# Python's `tempfile._RandomNameSequence` alphabet is EXACTLY
# lowercase+digits+underscore, 8 chars -- so `^tmp[a-z0-9_]{8}$` is the
# precise signature of a `tempfile.mkdtemp`/`mkstemp` name and excludes
# `tmux-*` (dash), `tmp.*` (mktemp-shell dot), `tmpc` etc. by construction.
_TMP_MKDTEMP_RX = re.compile(r"^tmp[a-z0-9_]{8}$")
TMP_STRAY_LOG_PATH = CLAUDE_DIR / "tmp-stray-sweep.log"
TMP_STRAY_STATE_PATH = CLAUDE_DIR / "tmp-stray-sweep-state.json"
TMP_STRAY_MIN_INTERVAL_S = 24 * 3600       # env AIRULESET_TMP_STRAY_SWEEP_INTERVAL_S
TMP_STRAY_MAX_SCAN_DEFAULT = 20000         # env AIRULESET_TMP_STRAY_MAX_SCAN -- per-run classify cap
TMP_STRAY_LIVE_ENV = "AIRULESET_TMP_PYTEST_RECLAIM_LIVE"


def _scan_live_tmp_tops(tmp_dir=None, proc_dir=None):
    """ONE /proc pass -> the set of TOP-LEVEL `<tmp_dir>/<child>` paths any
    live process currently references (cwd/exe/any open fd points at or
    inside them). This inverts `_target_in_live_use` (which walks all of
    /proc PER target -- infeasible for 500k candidates): scan /proc once,
    membership-test each candidate against the returned set. Returns None
    when liveness cannot be determined -- the caller treats None as 'every
    candidate is in live use' (fail-safe), exactly `_target_in_live_use`'s
    own total-failure contract.

    None on TOTAL failure (no /proc, unreadable /proc dir) AND on TOTAL
    LOCKOUT (#513 adversarial-review MAJOR-1): pids were listed but NOT A
    SINGLE cwd/exe/fd link was readable across ALL of them -- a hardened
    /proc (`hidepid=2`) where this account can see no process's links at
    all, so an empty result would be a FALSE 'nothing live' rather than a
    real one. Returning None there fails safe (skip everything) instead of
    reclaiming a dir some unreadable process is using.

    Accepted residual (identical to `_target_in_live_use`'s own): a PARTIAL
    lockout -- a FOREIGN-uid process (whose `/proc/<pid>/fd` this account
    cannot read) holding one of THIS uid's `tmp[a-z0-9_]{8}` dirs open --
    is not detected, so under live=True that aged dir could be reclaimed
    while the foreign process holds it. Bounded harm: deletion is uid-gated
    so only THIS account's own dirs are ever touched and this account's OWN
    pids ARE readable (so its own currently-open tempdirs stay protected);
    the foreign holder's open fd survives the unlink on Linux (it keeps
    working on the now-unlinked inode); and the default is report-only."""
    tmp_root = os.path.realpath(str(tmp_dir)) if tmp_dir else "/tmp"
    prefix = tmp_root.rstrip("/") + os.sep
    proc = Path(proc_dir) if proc_dir is not None else Path("/proc")
    if not proc.is_dir():
        return None
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    tops = set()
    reads_ok = 0        # any successful cwd/exe/fd readlink at all (#513 MAJOR-1)

    def _consider(link):
        if link == tmp_root or link.startswith(prefix):
            rest = link[len(prefix):] if link.startswith(prefix) else ""
            first = rest.split(os.sep, 1)[0] if rest else ""
            if first:
                tops.add(prefix + first)

    for pid in pids:
        pdir = proc / pid
        for name in ("cwd", "exe"):
            try:
                link = os.readlink(pdir / name)
            except OSError:
                continue     # a vanished/foreign-uid pid -- skip it, never guess
            reads_ok += 1
            _consider(link)
        try:
            fds = os.listdir(pdir / "fd")
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(pdir / "fd" / fd)
            except OSError:
                continue
            reads_ok += 1
            _consider(link)
    if pids and reads_ok == 0:
        return None          # total lockout -- undeterminable, fail safe
    return tops


def discover_stray_tmp_candidates(tmp_dir=None, uid=None, now=None,
                                  min_age_days=None, proc_dir=None, max_scan=None):
    """Classify `<tmp_dir>/tmp[a-z0-9_]{8}` entries owned by THIS uid.
    ONE `os.scandir` pass: counts EVERY name-regex match (`total_matched`,
    the loud problem size), and for up to `max_scan` of them records a row
    `{path, reason, size?, age_days?}` (`reason` None = genuine candidate).
    Safety quad-gate per entry: precise tempfile regex (already matched) +
    uid-owned + symlink-refused + top-level mtime age >= floor + not in the
    `_scan_live_tmp_tops` live set. Returns
    `{total_matched, examined:[rows], capped}` (+ `examined_error: True`
    only when the top-level `os.scandir` itself failed)."""
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS",
                                     CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT)
    uid = os.getuid() if uid is None else uid
    tmp_dir = Path(tmp_dir) if tmp_dir else Path("/tmp")
    max_scan = TMP_STRAY_MAX_SCAN_DEFAULT if max_scan is None else max_scan
    result = {"total_matched": 0, "examined": [], "capped": False}
    if not tmp_dir.is_dir():
        return result
    tmp_root = os.path.realpath(str(tmp_dir))
    live_tops = _scan_live_tmp_tops(tmp_dir, proc_dir)
    min_age_s = min_age_days * 86400.0
    try:
        scanner = os.scandir(tmp_dir)
    except OSError as e:
        # #513 adversarial-review MINOR: mark the discovery as errored so
        # sweep_stray_tmp does NOT advance its 24h cadence state on a total
        # scan failure (it would otherwise suppress a retry for a day).
        result["examined_error"] = True
        result["examined"].append({"path": None, "reason": "could not scandir %s: %s" % (tmp_dir, e)})
        return result
    with scanner:
        for e in scanner:
            name = e.name
            if not _TMP_MKDTEMP_RX.match(name):
                continue
            result["total_matched"] += 1
            if len(result["examined"]) >= max_scan:
                result["capped"] = True
                continue        # keep counting the true total; stop classifying
            row = {"path": e.path, "reason": None}
            result["examined"].append(row)
            try:
                if e.is_symlink():
                    row["reason"] = "symlink -- never followed, never deleted through"
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError as ex:
                row["reason"] = "could not stat: %s" % ex
                continue
            if st.st_uid != uid:
                row["reason"] = "owned by another uid -- never touched"
                continue
            age_s = now - st.st_mtime
            row["age_days"] = age_s / 86400.0
            if st.st_mtime > now or age_s < min_age_s:
                row["reason"] = "too recent (%.1fd < %sd)" % (row["age_days"], min_age_days)
                continue
            live_key = os.path.join(tmp_root, name)
            if live_tops is None or live_key in live_tops:
                row["reason"] = "in live use (or undeterminable) -- skipped"
                continue
            # reason stays None -- genuine candidate
    return result


def _log_tmp_stray_summary(summary, log_path, now):
    import time as _time
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    line = ("%s total_matched=%d classified=%d reclaimable=%d removed=%d "
            "in_use=%d too_recent=%d capped=%s live=%s"
            % (ts, summary["total_matched"], summary["classified"],
               summary["reclaimable"], summary["removed"], summary["in_use"],
               summary["too_recent"], summary["capped"], summary["live"]))
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        print("  tmp-stray-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_stray_tmp(tmp_dir=None, uid=None, dry_run: bool = False, now=None,
                    log_path=None, state_path=None, force: bool = False,
                    min_age_days=None, proc_dir=None, max_scan=None, live=None):
    """Reclaim aged, uid-owned `tempfile.mkdtemp/mkstemp` litter
    (`/tmp/tmp[a-z0-9_]{8}`) -- the ext4 htree ENOSPC source (#513).

    REPORT-ONLY by default: deletion runs ONLY when `live` is True (from
    `AIRULESET_TMP_PYTEST_RECLAIM_LIVE=1` when `live` is left None) AND not
    `dry_run` -- mirrors the transcript-compress LIVE gate. Re-verifies
    "still not a symlink, still exists, still uid-owned, still not in live
    use" immediately before EACH delete (a TOCTOU re-check via a fresh
    `_scan_live_tmp_tops`). Cadence-gated via its own state file; `force`
    or `dry_run` bypasses the gate. Returns a SUMMARY dict (never a row per
    entry -- 500k rows is not a report)."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else TMP_STRAY_LOG_PATH
    state_path = Path(state_path) if state_path else TMP_STRAY_STATE_PATH
    if live is None:
        live = os.environ.get(TMP_STRAY_LIVE_ENV) == "1"
    uid = os.getuid() if uid is None else uid

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0
        interval = TMP_STRAY_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_TMP_STRAY_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = TMP_STRAY_MIN_INTERVAL_S
        if now - last < interval:
            return {"total_matched": 0, "classified": 0, "reclaimable": 0,
                    "removed": 0, "in_use": 0, "too_recent": 0, "capped": False,
                    "live": live, "reclaimed_bytes": 0, "skipped_cadence": True}

    disc = discover_stray_tmp_candidates(tmp_dir, uid=uid, now=now,
                                         min_age_days=min_age_days, proc_dir=proc_dir,
                                         max_scan=max_scan)
    examined = [r for r in disc["examined"] if r.get("path") is not None]
    genuine = [r for r in examined if r.get("reason") is None]
    in_use = sum(1 for r in examined if "in live use" in str(r.get("reason", "")))
    too_recent = sum(1 for r in examined if "too recent" in str(r.get("reason", "")))
    summary = {"total_matched": disc["total_matched"], "classified": len(examined),
               "reclaimable": len(genuine), "removed": 0, "in_use": in_use,
               "too_recent": too_recent, "capped": disc["capped"], "live": live,
               "reclaimed_bytes": 0}

    if live and not dry_run and genuine:
        live_tops = _scan_live_tmp_tops(tmp_dir, proc_dir)
        for r in genuine:
            p = Path(r["path"])
            try:
                if p.is_symlink() or not p.exists():
                    continue
                lst = os.lstat(str(p))
                if lst.st_uid != uid:
                    continue
                key = os.path.realpath(str(p))
                if live_tops is None or key in live_tops:
                    continue      # raced into live use since discovery -- refuse
                # #513 adversarial-review NIT: a directory's own st_size is
                # the ~4KB dir-entry, not the tree it holds. Measure the real
                # recursive size just before removing (bounded -- only the
                # genuine, <= max_scan reclaimables, and only under live=True).
                if p.is_dir():
                    sz = _dir_stats(p)[0]
                    shutil.rmtree(p)
                else:
                    try:
                        sz = lst.st_size
                    except OSError:
                        sz = 0
                    p.unlink()
                summary["removed"] += 1
                summary["reclaimed_bytes"] += sz
            except OSError as e:
                print("  tmp-stray-sweep: delete failed %s: %s" % (p, e), file=sys.stderr)

    _log_tmp_stray_summary(summary, log_path, now)
    if not dry_run and not disc.get("examined_error"):
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  tmp-stray-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)
    return summary


def cmd_sweep_stray_tmp(args):
    """`airuleset.py sweep-stray-tmp [--dry-run] [--min-age-days N]` --
    manual entry point for the #513 stray-tempfile sweep. Always
    `force=True`. REPORT-ONLY unless AIRULESET_TMP_PYTEST_RECLAIM_LIVE=1 is
    set (loud instruction printed when it is not)."""
    print("airuleset sweep-stray-tmp")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_days = getattr(args, "min_age_days", None)
    s = sweep_stray_tmp(dry_run=dry_run, force=True, min_age_days=min_age_days)
    print("  matched (total tmp[a-z0-9_]{8} entries):  %d" % s["total_matched"])
    print("  classified this run:                      %d%s"
          % (s["classified"], "  (CAPPED -- more remain for next run)" if s["capped"] else ""))
    print("  reclaimable (aged, uid-owned, not live):  %d" % s["reclaimable"])
    print("  in live use / too recent (kept):          %d / %d" % (s["in_use"], s["too_recent"]))
    if s["live"]:
        print("  REMOVED: %d (%s reclaimed)" % (s["removed"], _human_size(s["reclaimed_bytes"])))
    else:
        print("  REPORT-ONLY: nothing deleted. Set %s=1 to reclaim live."
              % TMP_STRAY_LIVE_ENV)
    print("Log: %s" % TMP_STRAY_LOG_PATH)


# --- Stray hook-state litter sweep: /tmp/airuleset-* (#548) -----------------
# The SIBLING of `sweep_stray_tmp` above, for the OTHER half of the dev1
# inode-exhaustion incident: 136,516 `/tmp/airuleset-*` entries (measured
# 2026-08-18) -- per-session hook state (dedup/poll/block-count markers, run
# files, plus test-suite `mkdtemp(prefix="airuleset-...")` dirs), which mostly
# HARDCODE `/tmp/airuleset-*` (e.g. block-main-implementation.sh's
# `RUN_FILE="/tmp/airuleset-main-bash-run-<sid>"`, 46k on dev1) so the #548 CORE
# TMPDIR redirect never catches them. Dead sessions leave them forever; nothing
# fleet-wide reaped them (#494's age-reclaim lives only in a test helper).
# `sweep_stray_tmp`'s `^tmp[a-z0-9_]{8}$` regex never matches them; job 22
# (`cleanup_stale_exec_markers`) covers only the 2 EXEC-PERMISSION prefixes.
#
# SAME proven quad-gate as `sweep_stray_tmp` (precise-prefix + uid-owned +
# symlink-refused + top-mtime age >= floor + `_scan_live_tmp_tops` single
# inverted /proc scan) + a TOCTOU re-check before EACH delete. TWO deliberate
# differences from the tmp* sibling:
#   - the age FLOOR defaults to 3 DAYS (not 7): a live session re-writes its
#     hook state within minutes, so a 3-day-old marker is overwhelmingly a dead
#     session's -- the mtime gate IS the liveness signal here (a state file is
#     NOT held open by its live session, so the /proc scan only backstops a
#     mid-write file, which is <3d anyway);
#   - the EXEC-PERMISSION marker families (`airuleset-main-exec-*`,
#     `airuleset-fable-exec-*`) are EXCLUDED outright -- job 22 owns them with a
#     per-file live-SESSION check (revoking a live session's deliberately
#     granted exec exception mid-work is the one hazard mtime alone can't gate).
#   Reaps FILES and DIRS both (markers are files; `mkdtemp(prefix=)` state is a
#   dir). Regular-file/dir only via `os.lstat`, NEVER a content read (#409 FIFO
#   lesson -- this reaper never opens a candidate at all).

_AIRULESET_STATE_RX = re.compile(r"^airuleset-")
# Excluded: the exec-permission markers that job 22 (cleanup_stale_exec_markers)
# reaps with a per-file live-session check. A prefix match (not a bare
# `-exec-` substring) so an unrelated future `airuleset-execution-*` is unaffected.
AIRULESET_STATE_EXCLUDE_PREFIXES = ("airuleset-main-exec-", "airuleset-fable-exec-")
AIRULESET_STATE_LOG_PATH = CLAUDE_DIR / "airuleset-state-sweep.log"
AIRULESET_STATE_STATE_PATH = CLAUDE_DIR / "airuleset-state-sweep-state.json"
AIRULESET_STATE_MIN_INTERVAL_S = 24 * 3600     # env AIRULESET_STATE_SWEEP_INTERVAL_S
AIRULESET_STATE_MIN_AGE_DAYS_DEFAULT = 3       # env AIRULESET_STATE_MIN_AGE_DAYS
AIRULESET_STATE_MAX_SCAN_DEFAULT = 20000       # per-run classify cap
AIRULESET_STATE_LIVE_ENV = "AIRULESET_STATE_RECLAIM_LIVE"


def _is_excluded_airuleset_state(name):
    return any(name.startswith(p) for p in AIRULESET_STATE_EXCLUDE_PREFIXES)


def discover_stray_airuleset_state_candidates(tmp_dir=None, uid=None, now=None,
                                              min_age_days=None, proc_dir=None,
                                              max_scan=None):
    """Classify `<tmp_dir>/airuleset-*` entries owned by THIS uid, EXCLUDING the
    exec-permission marker families. Same ONE-`os.scandir`-pass + `{total_matched,
    examined:[rows], capped}` return shape as `discover_stray_tmp_candidates`;
    `reason` None = genuine candidate. Reaps both files and dirs."""
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_STATE_MIN_AGE_DAYS",
                                     AIRULESET_STATE_MIN_AGE_DAYS_DEFAULT)
    uid = os.getuid() if uid is None else uid
    tmp_dir = Path(tmp_dir) if tmp_dir else Path("/tmp")
    max_scan = AIRULESET_STATE_MAX_SCAN_DEFAULT if max_scan is None else max_scan
    result = {"total_matched": 0, "examined": [], "capped": False}
    if not tmp_dir.is_dir():
        return result
    tmp_root = os.path.realpath(str(tmp_dir))
    live_tops = _scan_live_tmp_tops(tmp_dir, proc_dir)
    min_age_s = min_age_days * 86400.0
    try:
        scanner = os.scandir(tmp_dir)
    except OSError as e:
        result["examined_error"] = True
        result["examined"].append({"path": None, "reason": "could not scandir %s: %s" % (tmp_dir, e)})
        return result
    with scanner:
        for e in scanner:
            name = e.name
            if not _AIRULESET_STATE_RX.match(name) or _is_excluded_airuleset_state(name):
                continue
            result["total_matched"] += 1
            if len(result["examined"]) >= max_scan:
                result["capped"] = True
                continue        # keep counting the true total; stop classifying
            row = {"path": e.path, "reason": None}
            result["examined"].append(row)
            try:
                if e.is_symlink():
                    row["reason"] = "symlink -- never followed, never deleted through"
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError as ex:
                row["reason"] = "could not stat: %s" % ex
                continue
            if st.st_uid != uid:
                row["reason"] = "owned by another uid -- never touched"
                continue
            age_s = now - st.st_mtime
            row["age_days"] = age_s / 86400.0
            if st.st_mtime > now or age_s < min_age_s:
                row["reason"] = "too recent (%.1fd < %sd)" % (row["age_days"], min_age_days)
                continue
            live_key = os.path.join(tmp_root, name)
            if live_tops is None or live_key in live_tops:
                row["reason"] = "in live use (or undeterminable) -- skipped"
                continue
            # reason stays None -- genuine candidate
    return result


def _log_airuleset_state_summary(summary, log_path, now):
    import time as _time
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    line = ("%s total_matched=%d classified=%d reclaimable=%d removed=%d "
            "in_use=%d too_recent=%d capped=%s live=%s"
            % (ts, summary["total_matched"], summary["classified"],
               summary["reclaimable"], summary["removed"], summary["in_use"],
               summary["too_recent"], summary["capped"], summary["live"]))
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        print("  airuleset-state-sweep: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_airuleset_state(tmp_dir=None, uid=None, dry_run: bool = False, now=None,
                          log_path=None, state_path=None, force: bool = False,
                          min_age_days=None, proc_dir=None, max_scan=None, live=None):
    """Reclaim aged, uid-owned `/tmp/airuleset-*` hook-state litter (>3d) --
    #548. REPORT-ONLY unless `live` is True (from `AIRULESET_STATE_RECLAIM_LIVE=1`
    when `live` is left None). Re-verifies "still not a symlink, still exists,
    still uid-owned, still not in live use" immediately before EACH delete (a
    TOCTOU re-check via a fresh `_scan_live_tmp_tops`). Cadence-gated via its own
    state file; `force`/`dry_run` bypasses the gate. Returns a SUMMARY dict
    (never a row per entry). Mirror of `sweep_stray_tmp`, for a DIFFERENT prefix
    + a 3d floor + files-and-dirs both."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else AIRULESET_STATE_LOG_PATH
    state_path = Path(state_path) if state_path else AIRULESET_STATE_STATE_PATH
    if live is None:
        live = os.environ.get(AIRULESET_STATE_LIVE_ENV) == "1"
    uid = os.getuid() if uid is None else uid

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0
        interval = AIRULESET_STATE_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_STATE_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = AIRULESET_STATE_MIN_INTERVAL_S
        if now - last < interval:
            return {"total_matched": 0, "classified": 0, "reclaimable": 0,
                    "removed": 0, "in_use": 0, "too_recent": 0, "capped": False,
                    "live": live, "reclaimed_bytes": 0, "skipped_cadence": True}

    disc = discover_stray_airuleset_state_candidates(
        tmp_dir, uid=uid, now=now, min_age_days=min_age_days, proc_dir=proc_dir,
        max_scan=max_scan)
    examined = [r for r in disc["examined"] if r.get("path") is not None]
    genuine = [r for r in examined if r.get("reason") is None]
    in_use = sum(1 for r in examined if "in live use" in str(r.get("reason", "")))
    too_recent = sum(1 for r in examined if "too recent" in str(r.get("reason", "")))
    summary = {"total_matched": disc["total_matched"], "classified": len(examined),
               "reclaimable": len(genuine), "removed": 0, "in_use": in_use,
               "too_recent": too_recent, "capped": disc["capped"], "live": live,
               "reclaimed_bytes": 0}

    if live and not dry_run and genuine:
        live_tops = _scan_live_tmp_tops(tmp_dir, proc_dir)
        for r in genuine:
            p = Path(r["path"])
            try:
                if p.is_symlink() or not p.exists():
                    continue
                lst = os.lstat(str(p))
                if lst.st_uid != uid:
                    continue
                key = os.path.realpath(str(p))
                if live_tops is None or key in live_tops:
                    continue      # raced into live use since discovery -- refuse
                if p.is_dir():
                    sz = _dir_stats(p)[0]
                    shutil.rmtree(p)
                else:
                    try:
                        sz = lst.st_size
                    except OSError:
                        sz = 0
                    p.unlink()
                summary["removed"] += 1
                summary["reclaimed_bytes"] += sz
            except OSError as e:
                print("  airuleset-state-sweep: delete failed %s: %s" % (p, e), file=sys.stderr)

    _log_airuleset_state_summary(summary, log_path, now)
    if not dry_run and not disc.get("examined_error"):
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  airuleset-state-sweep: could not write state %s: %s" % (state_path, e), file=sys.stderr)
    return summary


# --- Transcript gzip-at-rest retention (#410, split from #380 point 3) -----
# #376 force-set `cleanupPeriodDays=3650` fleet-wide, disabling Claude
# Code's own native transcript auto-delete (the previous 30-day default)
# with NOTHING wired in to replace what it used to (silently) do --
# ~/.claude/projects/**/*.jsonl now grows completely unbounded. This is
# the size-aware retention layer: gzip-at-rest for OLD top-level session
# transcripts -- NEVER deletes, fully reversible via a plain `gunzip`
# ("história nesmie miznúť" -- the user's own explicit requirement,
# 2026-08-11). Same sweep shape as #315/#345/#355 above (discovery
# separated from action, own log+state file, cadence-gated -- FREEZE: no
# new watchdog job, no new hook), wired as a non-fatal cmd_install() step
# plus a manual/testable CLI entry point.
#
# SCOPE (v1, deliberate -- see the #410 design comment on the ticket):
# MAIN (top-level, per-project) transcripts ONLY -- the direct *.jsonl
# children of a `~/.claude/projects/<encoded-cwd>/` directory, the exact
# population `/resume`/`claude --continue`/claude-history ever read.
# Anything under a `subagents/` component is NEVER touched here: it costs
# zero real disk reclamation today (live census on dev1, 2026-08-12: 0
# reclaimable bytes -- every subagent transcript on this box is under 30
# days old), and this repo's own playbook documents numerous ad-hoc
# forensic/corpus-scanning scripts that glob `**/*.jsonl` recursively
# with no time-window bound, none of which have been audited here for
# `.gz`-awareness. Revisit once real subagent-transcript disk pressure
# exists.
#
# claude-history's own `.jsonl.gz` READ support (find_transcripts/
# _read_jsonl, above in this file's CLAUDE_HISTORY_SCRIPT_CONTENT) landed
# in an EARLIER, standalone commit -- history-browsing of a compressed
# file was never in a broken state at any point in this ticket's history.
#
# `/resume`'s own horizon: Claude Code's native /resume command lists
# `~/.claude/projects/<key>/*.jsonl` directly -- a compressed OLD
# transcript disappears from that listing (claude-history remains the
# fallback that CAN still read it). Accepted per the approved design:
# a 30+-day-old session is already well beyond /resume's own practical
# use window in this repo's real usage pattern.

TRANSCRIPT_COMPRESS_LOG_PATH = CLAUDE_DIR / "transcript-compress-sweep.log"
TRANSCRIPT_COMPRESS_STATE_PATH = CLAUDE_DIR / "transcript-compress-sweep-state.json"
TRANSCRIPT_COMPRESS_MIN_INTERVAL_S = 24 * 3600   # env AIRULESET_TRANSCRIPT_COMPRESS_SWEEP_INTERVAL_S
# Matches CC's own FORMER native default (30 days) -- this sweep is
# provably gentler than stock cleanup once was.
TRANSCRIPT_COMPRESS_MIN_AGE_DAYS_DEFAULT = 30    # env AIRULESET_TRANSCRIPT_MIN_AGE_DAYS
TRANSCRIPT_COMPRESS_MIN_SIZE_BYTES_DEFAULT = 100 * 1024   # env AIRULESET_TRANSCRIPT_MIN_SIZE_BYTES


def _claude_projects_dir(home=None) -> Path:
    """`~/.claude/projects/` -- every Claude Code session transcript
    directory, one per encoded cwd (matches `encode_project_dir`)."""
    home = Path(home or os.environ.get("HOME") or os.path.expanduser("~"))
    return home / ".claude" / "projects"


def _min_size_bytes_env(explicit, env_key, default):
    """Same shape as `_min_age_days_env` (above), for a byte-count floor
    -- an unparseable or negative override falls back to `default` rather
    than silently disabling the floor."""
    if explicit is not None:
        return explicit
    try:
        v = int(os.environ.get(env_key, default))
    except (TypeError, ValueError):
        return default
    return default if v < 0 else v


def discover_old_transcript_candidates(home=None, projects_dir=None, now=None,
                                       min_age_days=None, min_size_bytes=None,
                                       proc_dir=None):
    """Every MAIN (top-level, per-project) `.jsonl` transcript that is
    safe to gzip-at-rest -- #410. A list of dicts `{"path", "reason",
    "size"?, "age_days"?}` -- `reason` is `None` for a genuine candidate,
    else WHY it was excluded (same discovery-shape contract as
    `discover_cli_version_candidates`/`discover_stale_worktrees` above).

    Safety criteria (NON-NEGOTIABLE):
      - only a `.jsonl` file DIRECTLY inside a project directory is ever
        considered -- an already-compressed `.jsonl.gz` sibling is never
        re-matched by the glob at all, and a `subagents/` descendant is
        never walked into (v1 scope, see the module comment above);
      - the NEWEST `.jsonl` file in its OWN project directory is NEVER a
        candidate, regardless of age (protects `/resume`/`claude
        --continue` in a dormant project) -- computed per-directory, so a
        genuinely old but still-newest-in-its-dir file is always excluded;
      - a symlink entry is refused individually, never followed;
      - `mtime` age >= `min_age_days` (default 30, env
        AIRULESET_TRANSCRIPT_MIN_AGE_DAYS);
      - `size` >= `min_size_bytes` (default 100KB, env
        AIRULESET_TRANSCRIPT_MIN_SIZE_BYTES);
      - a surviving candidate still needs a live-process check
        (`_target_in_live_use`, #315's own /proc exe/cwd/fd scan, REUSED
        VERBATIM -- never a new mechanism) before being genuine.
    """
    import time as _time
    now = _time.time() if now is None else now
    min_age_days = _min_age_days_env(min_age_days, "AIRULESET_TRANSCRIPT_MIN_AGE_DAYS",
                                     TRANSCRIPT_COMPRESS_MIN_AGE_DAYS_DEFAULT)
    min_size_bytes = _min_size_bytes_env(min_size_bytes, "AIRULESET_TRANSCRIPT_MIN_SIZE_BYTES",
                                         TRANSCRIPT_COMPRESS_MIN_SIZE_BYTES_DEFAULT)
    pdir = Path(projects_dir) if projects_dir else _claude_projects_dir(home)

    if not pdir.is_dir():
        return []

    try:
        names = sorted(os.listdir(pdir))
    except OSError as e:
        return [{"path": None, "reason": "could not list %s: %s" % (pdir, e)}]

    out = []
    for name in names:
        d = pdir / name
        if not d.is_dir():
            continue
        try:
            paths = sorted(d.glob("*.jsonl"))
        except OSError:
            continue
        rows = []   # (mtime, path, size, is_symlink)
        for p in paths:
            is_link = p.is_symlink()
            try:
                st = os.lstat(p)   # never follow -- report the LINK's own metadata
            except OSError as e:
                out.append({"path": str(p), "reason": "could not stat: %s" % e})
                continue
            rows.append((st.st_mtime, p, st.st_size, is_link))
        if not rows:
            continue
        rows.sort(key=lambda t: t[0], reverse=True)
        # #410 review F5: exclude by MTIME, not by object identity --
        # `p == newest_path` only ever protects the ONE row that
        # happened to sort first; a genuine mtime TIE (two files last
        # written in the same wall-clock second, with nothing newer in
        # the dir) left the other tied file eligible, contradicting this
        # function's own docstring ("the NEWEST... is NEVER a
        # candidate"). Comparing against the newest mtime VALUE excludes
        # every row that ties for newest, not just the first one found.
        newest_mtime = rows[0][0]
        for mtime, p, size, is_link in rows:
            if mtime == newest_mtime:
                continue   # newest (or tied-for-newest) in its own dir -- never a candidate
            entry = {"path": str(p), "reason": None,
                    "age_days": (now - mtime) / 86400.0, "size": size}
            if is_link:
                entry["reason"] = "symlink entry -- never followed, never compressed"
                out.append(entry)
                continue
            if entry["age_days"] < min_age_days:
                entry["reason"] = "too recent (%.1fd < %sd)" % (entry["age_days"], min_age_days)
                out.append(entry)
                continue
            if size < min_size_bytes:
                entry["reason"] = "below size floor (%d B < %d B)" % (size, min_size_bytes)
                out.append(entry)
                continue
            if _target_in_live_use(p, proc_dir=proc_dir):
                entry["reason"] = "in live use (or undeterminable) -- skipped"
                out.append(entry)
                continue
            out.append(entry)   # reason stays None -- genuine candidate

    return out


def _compress_transcript_file(path, now=None):
    """Compress ONE transcript `path` (a plain `.jsonl` file) to
    `<path>.gz`, following the approved design's compress-verify-swap
    protocol -- #410. NEVER deletes the original until a fully
    independent decompress+hash verification confirms the bytes actually
    ON DISK round-trip exactly to the source.

    Steps: re-stat the source and capture (size, mtime); refuse a
    symlink outright; stream-read the source ONCE into BOTH a gzip
    writer (targeting `<name>.jsonl.gz.tmp` in the SAME directory -- same
    filesystem, so the later swap is a true atomic rename) and a running
    SHA-256; close both, then re-open the just-written `.tmp` file FRESH
    (a completely separate read pass off disk) and stream-hash the
    DEcompressed output -- the two digests must match exactly, or nothing
    proceeds; re-`stat()` the source and refuse if its (size, mtime)
    changed since step 1 (a TOCTOU race -- a resumed session writing to
    it mid-sweep); fsync + `os.replace()` (atomic rename) the tmp onto
    the final `.jsonl.gz` path, `os.utime()`-stamped to the ORIGINAL's
    mtime; only THEN `os.unlink()` the original.

    ANY failure at ANY step removes the `.tmp` (if it exists), leaves the
    original COMPLETELY untouched, and returns the reason -- the caller
    is expected to continue to its next candidate, never abort the whole
    sweep on one file's failure.

    Returns `{"path", "removed": bool, "reason"}` -- `removed=True` means
    the original `.jsonl` was safely replaced by a verified `.jsonl.gz`.
    """
    import hashlib
    import tempfile
    import time as _time
    now = _time.time() if now is None else now
    p = Path(path)
    entry = {"path": str(p), "removed": False, "reason": None}

    if p.is_symlink():
        entry["reason"] = "symlink -- refused (re-checked before compress)"
        return entry
    try:
        orig_st = os.stat(p)
    except OSError as e:
        entry["reason"] = "could not stat before compress: %s" % e
        return entry
    orig_size, orig_mtime = orig_st.st_size, orig_st.st_mtime

    # #410 review F1 (CRITICAL once live): a PREDICTABLE tmp name
    # (`<name>.gz.tmp`) collides across concurrent sweeps of the SAME
    # file -- a second writer truncates the first's still-being-hashed
    # tmp file, and the first's own verify-hash can then match the
    # SECOND writer's (also in-progress) bytes, reporting "compressed"
    # for a `.gz` that does not actually round-trip. `tempfile.mkstemp`
    # gives every call a UNIQUE name in the SAME directory (same
    # filesystem, so the later `os.replace()` swap stays a true atomic
    # rename) -- each concurrent writer then verifies only its OWN
    # bytes, collapsing the race to a harmless double-compress (the
    # second writer's later TOCTOU re-stat of the ORIGINAL, once the
    # first writer has already unlinked it, correctly refuses).
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(p.parent), prefix=p.name + ".", suffix=".gz.tmp")
    except OSError as e:
        entry["reason"] = "could not create unique tmp file: %s" % e
        return entry
    tmp_path = Path(tmp_name)
    final_path = p.with_name(p.name + ".gz")

    def _cleanup_tmp():
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as e:
            print("  transcript-compress: could not remove leftover tmp %s: %s"
                  % (tmp_path, e), file=sys.stderr)

    orig_hash = hashlib.sha256()
    try:
        with os.fdopen(tmp_fd, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=orig_mtime) as gz:
                with open(p, "rb") as src:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        orig_hash.update(chunk)
                        gz.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
    except OSError as e:
        entry["reason"] = "compress failed: %s" % e
        _cleanup_tmp()
        return entry

    # Independent decompress-verify: a FRESH read pass off disk -- proves
    # the bytes ON DISK round-trip, never merely trusting the writer.
    # #410 review F4: a truncated/corrupt tmp (a torn write, a concurrent
    # writer's clobber -- see the F1 fix above) raises EOFError or
    # zlib.error from inside gzip's own decompressor, NEITHER of which is
    # an OSError -- catching only OSError let that exception ESCAPE this
    # function uncaught (a genuine correctness bug, not just an
    # unhandled edge case: this function's own docstring promises "ANY
    # failure at ANY step... returns the reason", and an uncaught raise
    # here also aborts the WHOLE sweep loop in sweep_old_transcripts,
    # never reaching later candidates -- see the per-candidate try/except
    # added there for the matching fix).
    verify_hash = hashlib.sha256()
    try:
        with gzip.open(tmp_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                verify_hash.update(chunk)
    except (OSError, EOFError, zlib.error) as e:
        entry["reason"] = "verify-decompress failed: %s" % e
        _cleanup_tmp()
        return entry

    if orig_hash.digest() != verify_hash.digest():
        entry["reason"] = "hash mismatch after decompress -- refusing to touch the original"
        _cleanup_tmp()
        return entry

    # TOCTOU re-check -- has the source changed since we started reading it?
    try:
        recheck_st = os.stat(p)
    except OSError as e:
        entry["reason"] = "could not re-stat before swap: %s" % e
        _cleanup_tmp()
        return entry
    if recheck_st.st_size != orig_size or recheck_st.st_mtime != orig_mtime:
        entry["reason"] = "source changed during compression (resumed session?) -- refused"
        _cleanup_tmp()
        return entry

    try:
        os.replace(str(tmp_path), str(final_path))
        os.utime(str(final_path), (now, orig_mtime))
    except OSError as e:
        entry["reason"] = "atomic swap failed: %s" % e
        _cleanup_tmp()
        return entry

    try:
        p.unlink()
    except OSError as e:
        # The verified .gz already exists on disk at this point -- only
        # the original's own unlink failed (race/permissions). Report it
        # honestly rather than claim success.
        entry["reason"] = "compressed .gz written but original unlink failed: %s" % e
        return entry

    entry["removed"] = True
    entry["reason"] = "compressed"
    return entry


def _log_transcript_compress_results(results, log_path, now, dry_run: bool):
    import time as _time
    lines = []
    ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now))
    for r in results:
        if r.get("path") is None:
            lines.append("%s ERROR %s" % (ts, r.get("reason", "")))
            continue
        if dry_run:
            tag = "WOULD-COMPRESS" if str(r.get("reason", "")).startswith("would compress") else "SKIP"
        else:
            tag = "COMPRESSED" if r.get("removed") else "SKIP"
        size = r.get("size")
        size_txt = " size=%s" % _human_size(size) if size is not None else ""
        lines.append("%s %s %s%s -- %s" % (
            ts, tag, r.get("path"), size_txt, r.get("reason", "")))
    if not lines:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print("  transcript-compress: could not write log %s: %s" % (log_path, e), file=sys.stderr)


def sweep_old_transcripts(home=None, projects_dir=None, dry_run: bool = True,
                          now=None, log_path=None, state_path=None,
                          force: bool = False, min_age_days=None,
                          min_size_bytes=None, candidates=None, proc_dir=None):
    """Compress every stale MAIN transcript `discover_old_transcript_
    candidates` classifies as a genuine candidate (`reason is None`) --
    #410. NEVER DELETES anything -- gzip-at-rest only, per the approved
    design's own non-negotiable ("história nesmie miznúť"). Re-verifies
    "still a plain regular file, not in live use" immediately before EACH
    compress attempt (a TOCTOU re-check, mirroring #315/#355's own
    re-verify-before-act pattern) on top of `_compress_transcript_file`'s
    OWN internal re-stat/hash verification.

    `dry_run` DEFAULTS TO `True` -- unlike its #355 sibling sweeps
    (which default to live) -- belt-and-suspenders on top of
    `cmd_install()`'s own env-var gate: #410's hard constraint is that
    live compression must never happen by accident anywhere in this PR,
    so even a caller that forgets to pass `dry_run=` explicitly stays
    safe by construction. A caller must explicitly pass `dry_run=False`
    to ever touch a real file.

    Cadence-gated via its own state file, mirroring #315/#345/#355
    exactly -- never leans on the 60s watchdog timer (FREEZE: no new
    job). `force=True` (the CLI's own manual invocation) or `dry_run=True`
    always bypasses the gate."""
    import time as _time
    now = _time.time() if now is None else now
    log_path = Path(log_path) if log_path else TRANSCRIPT_COMPRESS_LOG_PATH
    state_path = Path(state_path) if state_path else TRANSCRIPT_COMPRESS_STATE_PATH

    if not force and not dry_run:
        try:
            st = json.loads(state_path.read_text())
            last = float(st.get("last_run", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            last = 0
        if last > now:
            last = 0            # a future-dated stamp must not wedge the gate forever
        interval = TRANSCRIPT_COMPRESS_MIN_INTERVAL_S
        try:
            interval = int(os.environ.get("AIRULESET_TRANSCRIPT_COMPRESS_SWEEP_INTERVAL_S", interval))
        except ValueError:
            interval = TRANSCRIPT_COMPRESS_MIN_INTERVAL_S
        if now - last < interval:
            return []

    results = []
    discovery_failed = False
    if candidates is None:
        try:
            candidates = discover_old_transcript_candidates(
                home, projects_dir=projects_dir, now=now,
                min_age_days=min_age_days, min_size_bytes=min_size_bytes,
                proc_dir=proc_dir)
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
            entry["reason"] = "would compress (dry-run)"
            results.append(entry)
            continue

        p = Path(c["path"])
        try:
            if p.is_symlink() or not p.is_file():
                entry["reason"] = ("no longer a plain regular file -- refused "
                                   "(re-checked before compress)")
                results.append(entry)
                continue
            if _target_in_live_use(p, proc_dir=proc_dir):
                entry["reason"] = ("in live use (or undeterminable) -- refused "
                                   "(re-checked before compress)")
                results.append(entry)
                continue
        except OSError as e:
            entry["reason"] = "re-check failed: %s" % e
            results.append(entry)
            continue

        # #410 review F4: a per-candidate backstop -- _compress_transcript_
        # file()'s own internal try/except already covers every
        # documented failure mode (including EOFError/zlib.error, per
        # the F3/F4 fix above), but ANY unexpected exception escaping it
        # must still never abort the WHOLE sweep loop -- that would
        # leave the log/cadence state unwritten and every remaining
        # candidate in this sweep silently unprocessed with no record.
        try:
            compressed = _compress_transcript_file(p, now=now)
        except Exception as e:
            compressed = {"removed": False,
                         "reason": "unexpected error during compress: %s" % e}
        entry.update(compressed)
        results.append(entry)

    _log_transcript_compress_results(results, log_path, now, dry_run)

    if not dry_run and not discovery_failed:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"last_run": now}))
        except OSError as e:
            print("  transcript-compress: could not write state %s: %s" % (state_path, e), file=sys.stderr)

    return results


def cmd_sweep_transcripts(args):
    """`airuleset.py sweep-transcripts [--dry-run] [--min-age-days N]
    [--min-size-bytes N]` -- manual/testable entry point for the #410
    gzip-at-rest transcript-compression sweep. Always `force=True`
    (bypasses the cadence gate that guards the automatic install/push
    wiring -- a deliberate manual call should never be silently skipped).

    `--dry-run` is OPT-IN here (default False -- LIVE), matching #355's
    own sibling sweep commands' established convention exactly: an
    explicit, human-typed subcommand NAME is the real "opt-in" signal for
    a manual invocation, `--dry-run` is the safety escape hatch on top of
    it -- unlike `cmd_install()`'s own AUTOMATIC wiring, which stays
    report-only by default (see the module comment above)."""
    print("airuleset sweep-transcripts")
    print("=" * 50)
    dry_run = bool(getattr(args, "dry_run", False))
    min_age_days = getattr(args, "min_age_days", None)
    min_size_bytes = getattr(args, "min_size_bytes", None)
    results = sweep_old_transcripts(dry_run=dry_run, force=True,
                                    min_age_days=min_age_days,
                                    min_size_bytes=min_size_bytes)
    for r in results:
        if r.get("path") is None:
            print("  ERROR: %s" % r.get("reason", ""))
            continue
        acted = (str(r.get("reason", "")).startswith("would compress")
                if dry_run else bool(r.get("removed")))
        if acted:
            tag = "WOULD COMPRESS" if dry_run else "COMPRESSED"
        else:
            tag = "skip"
        print("  %s: %s -- %s" % (tag, r["path"], r.get("reason", "")))
    acted_rows = [r for r in results
                 if (str(r.get("reason", "")).startswith("would compress")
                     if dry_run else r.get("removed"))]
    total = sum(r.get("size", 0) or 0 for r in acted_rows)
    print()
    verb = "would be " if dry_run else ""
    print("%d transcript(s) %scompressed, %s %sreclaimed (never deleted -- gzip-at-rest)." % (
        len(acted_rows), verb, _human_size(total), verb))
    print("Log: %s" % TRANSCRIPT_COMPRESS_LOG_PATH)


def _run_transcript_compress_step(sweep_fn=None):
    """`cmd_install()`'s step 12 body, factored OUT of that function so a
    test can call this directly (with `sweep_fn` mocked) and assert the
    REAL `dry_run` kwarg the wiring passes under each env state -- #410
    review F2. The pre-fix test re-implemented these same two gating
    lines INSIDE itself and called the mock directly, which is
    tautological (it proves the test's OWN copy of the logic works, not
    that `cmd_install()` actually calls it that way); calling this exact
    function is what makes the assertion about the real wiring. Report-
    only (`AIRULESET_TRANSCRIPT_COMPRESS_LIVE` unset/not "1") is the
    default in every real deployment -- see the module comment + the
    #410 design comment for why."""
    sweep_fn = sweep_fn or sweep_old_transcripts
    live_compress = os.environ.get("AIRULESET_TRANSCRIPT_COMPRESS_LIVE") == "1"
    tc_results = sweep_fn(dry_run=not live_compress)
    if live_compress:
        tc_removed = [r for r in tc_results if r.get("removed")]
        if tc_removed:
            total = sum(r.get("size", 0) or 0 for r in tc_removed)
            print(f"  Compressed {len(tc_removed)} old transcript(s), "
                  f"{_human_size(total)} reclaimed (never deleted -- gzip-at-rest; "
                  f"log: {TRANSCRIPT_COMPRESS_LOG_PATH})")
    else:
        tc_candidates = [r for r in tc_results
                         if str(r.get("reason", "")).startswith("would compress")]
        if tc_candidates:
            total = sum(r.get("size", 0) or 0 for r in tc_candidates)
            print(f"  Transcript compression: REPORT-ONLY -- found "
                  f"{len(tc_candidates)} candidate(s), {_human_size(total)} "
                  f"reclaimable. Set AIRULESET_TRANSCRIPT_COMPRESS_LIVE=1 to "
                  f"enable live compression, after user sign-off (#410).")
