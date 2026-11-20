"""#1380 — stale LANE worktrees outside ``.claude/worktrees`` for the disk guard.

david3@subdev (20.11.2026): 66 worktrees of ``~/devel/odoo/odoo-erp`` in
``~/cycles-wt/<ticket>`` (~33 GB) were never drained, while the guard deleted
the owner's documents. ``stale-agent-worktree`` listed only
``<repo>/.claude/worktrees/agent-*``, and the #834 ``worktree`` rung skips a
detached HEAD and counts only ``origin/*`` reachability (a fork stream's base
lives on ``upstream/*``).

This leaf is the stale-agent-worktree rung's SECOND source
(``disk_guard_worktrees.discover_stale_agent_worktrees`` calls
:func:`lane_rows` in the same pass, with the same verdict cache and live-lane
gate, so one ``VerdictCache.save`` keeps both sources' entries). The list is
git's own: ``git worktree list --porcelain`` of every MAIN checkout, entries
outside ``.claude/worktrees``, never entry 0 or the root itself. Gates,
cheapest first:

1. not ``locked``; the directory exists;
2. no live process inside (ONE ``/proc`` pass, ``cli_live_snapshot``, #1216);
3. not a live lane (``cli_lane_live_gate.LiveLaneGate``);
4. idle longer than the #834 worktree rung's 24 h floor;
5. ``disk_guard_worktrees._classify`` through the cache: not a protected
   branch, a CLEAN tree (untracked files count), HEAD contained in ANY
   remote-tracking ref (``upstream/*`` too, so a detached HEAD qualifies), and
   :func:`final_reason` (no submodules, every IGNORED entry build output or a
   cache — a clean tree still holds ignored ``private/``, ``credentials.json``,
   ``*.tfstate``, dumps, and ``git worktree remove`` deletes them all).

Review round 1 added: the entry must really be this repo's worktree (its
``.git`` file and the admin dir point at each other, else a stale entry hides
a foreign tree), no other checkout may sit inside it, the planned HEAD rides
on the row (the executor refuses a lane that moved), candidates rotate hourly
so a slow pass never starves the same tail, and a reader error keeps the
agent rows (one skip row instead of the whole rung).

Uncached classifications stop after :data:`LANE_CLASSIFY_BUDGET_S` per pass (a
30k-file odoo checkout costs seconds per ``git status``); the rest is a
deferred skip, never cached. A repo whose admin dir names only agent lanes is
skipped with plain file reads, so an unchanged pass still spawns no git
(#1067 1g). Rows are ``cls="lane-worktree"``, ``kind="worktree-remove"``; the
executor runs ``git worktree remove`` WITHOUT ``--force``.
"""
# airuleset:script-ok helper module, imported from disk_guard_worktrees
from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path

LANE_CLS = "lane-worktree"
LANE_CLASSIFY_BUDGET_S = 15.0
_DEFERRED = "classification deferred to the next pass (poll budget) — kept"
SUBMODULES_KEPT = "worktree has populated submodules (git refuses them without --force) — kept"
# An IGNORED entry a drain may delete with a clean, pushed tree: build output,
# caches and test output. Everything else ignored is a work product (#1380).
# A dir name counts only for an entry that IS that dir (git lists an ignored
# dir as one `!!` entry, never its contents); a file below such a name counts
# only inside a self-ignoring cache dir (pytest / ruff write a `*` .gitignore,
# Cargo and others a CACHEDIR.TAG) — an ignored `build/deploy.key` inside a
# TRACKED `build/` is not build output.
REGENERABLE_IGNORED_DIRS = frozenset({
    "__pycache__", "node_modules", ".venv", "venv", "target", "build", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache",
    "htmlcov", ".gradle", ".next", ".nuxt", ".parcel-cache", ".turbo", ".eggs",
    ".hypothesis", ".playwright-mcp", "blob-report"})
REGENERABLE_IGNORED_DIR_GLOBS = ("*.egg-info", "test-results*", "playwright-report*")
REGENERABLE_IGNORED_FILE_GLOBS = ("*.pyc", "*.pyo", ".coverage", ".coverage.*", "*.o",
                                  "*.mo", "coverage.xml", "package-lock.json")
NESTED_SCAN_MAX_ENTRIES = 2_000_000          # a walk past this is "unmeasurable"


def _self_ignoring(d: str) -> bool:
    """A cache dir that ignores itself: a CACHEDIR.TAG, or a .gitignore whose
    only rule is ``*`` (what pytest and ruff write)."""
    if os.path.isfile(os.path.join(d, "CACHEDIR.TAG")):
        return True
    try:
        with open(os.path.join(d, ".gitignore"), encoding="utf-8", errors="replace") as f:
            rules = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return False
    return rules == ["*"]


def _regenerable(wt_path: str, entry: str) -> bool:
    """One ignored ``git status`` entry (a dir keeps its trailing ``/``)."""
    parts = [p for p in entry.split("/") if p]
    if not parts:
        return True
    name, is_dir = parts[-1], entry.endswith("/")
    if is_dir and (name in REGENERABLE_IGNORED_DIRS
                   or any(fnmatch.fnmatch(name, g) for g in REGENERABLE_IGNORED_DIR_GLOBS)):
        return True
    if not is_dir and any(fnmatch.fnmatch(name, g) for g in REGENERABLE_IGNORED_FILE_GLOBS):
        return True
    return any(parts[i - 1] in REGENERABLE_IGNORED_DIRS
               and _self_ignoring(os.path.join(wt_path, *parts[:i]))
               for i in range(1, len(parts)))


def _holds_git(d: str):
    """True when a ``.git`` sits anywhere under ``d`` (a nested checkout the
    checkout walk never enters: it skips target/, build/, .cache/ ...); None
    when the walk is too large to finish (unmeasurable)."""
    seen = 0
    for _dp, dns, fns in os.walk(d, onerror=lambda e: None):
        if ".git" in dns or ".git" in fns:
            return True
        seen += len(dns) + len(fns)
        if seen > NESTED_SCAN_MAX_ENTRIES:
            return None
    return False


def _index_reason(wt_path, run):
    """Hidden local edits (skip-worktree ``S`` / assume-unchanged lowercase
    tags: ``git status`` never shows them, the remove deletes them) and
    populated submodules (git refuses them), from ``git ls-files -s -v``."""
    out = run(["ls-files", "-s", "-v", "-z"], wt_path)
    if out is None:
        return "git index unreadable — kept", True
    for rec in out.split("\x00"):
        meta, _, rel = rec.partition("\t")
        f = meta.split()
        if len(f) < 2:
            continue
        if f[0] == "S" or f[0].islower():
            return ("hidden local edits possible (%s is skip-worktree/assume-unchanged) — kept"
                    % rel), False
        sub = os.path.join(wt_path, rel)
        if f[1] == "160000" and os.path.isdir(sub) and os.listdir(sub):
            return SUBMODULES_KEPT, False
    return None


def final_reason(wt_path: str, git_run_fn=None):
    """The last gates of every worktree source before a reclaim, ``(reason,
    error)`` to keep the tree or None: hidden local edits and populated
    submodules (:func:`_index_reason`), untracked files, any IGNORED entry
    that is not build output, a cache or test output (:func:`_regenerable`),
    and a nested checkout inside an ignored dir. ``error`` marks an unreadable
    answer (never cached). Every worktree remover calls it: the disk guard's
    three sources and its executor, the Job 20 lane prune, the #345 sweep."""
    from cli_worktree_common import _worktree_git
    run = git_run_fn or _worktree_git
    kept = _index_reason(wt_path, run)
    if kept is not None:
        return kept
    # `--ignored=matching`: an ignored DIR is one entry, and a dir that is not
    # itself ignored is never listed for its ignored contents (`ls-files
    # --directory` listed `pkg/` for `pkg/__pycache__/`). Any non-`!!` record
    # (an untracked file appeared) is never regenerable.
    out = run(["status", "--porcelain", "--ignored=matching", "--untracked-files=normal", "-z"],
              wt_path)
    if out is None:
        return "ignored files unreadable — kept", True
    recs = [r for r in out.split("\x00") if r]
    dirty = [r[3:] for r in recs if not r.startswith("!! ")]
    if dirty:
        return ("dirty worktree (untracked %s) — kept" % ", ".join(dirty[:3])), False
    bad = [r[3:] for r in recs if not _regenerable(wt_path, r[3:])]
    if bad:
        return ("non-regenerable ignored file(s) %s — kept" % ", ".join(bad[:3])), False
    for r in recs:
        if r.endswith("/"):
            nested = _holds_git(os.path.join(wt_path, r[3:]))
            if nested is None:
                return "ignored %s too large to scan for a nested checkout — kept" % r[3:], False
            if nested:
                return "holds another checkout inside ignored %s — kept" % r[3:], False
    return None


def rotate(items, now):
    """``items`` started at an hourly offset, so the classification budget
    never spends every pass on the same head of the list."""
    items = list(items)
    if not items:
        return items
    k = int(now // 3600) % len(items)
    return items[k:] + items[:k]


def _owned(root, path) -> bool:
    """``path/.git`` names an admin dir of ``root`` whose ``gitdir`` names
    ``path/.git`` back: really this repo's worktree, never a foreign tree that
    took a stale entry's place."""
    from cli_worktree_common import _worktree_admin_dir
    from watchdog import disk_guard_worktrees as dgw
    admin, gd = _worktree_admin_dir(root, path), dgw._worktree_gitdir(path)
    try:
        return bool(admin and gd) and os.path.realpath(gd) == os.path.realpath(str(admin))
    except (OSError, ValueError):
        return False


def _batch_live_check():
    """A ``path -> bool`` live-use check reading ``/proc`` once per pass.
    Fail-safe: a reader error reads as live."""
    try:
        from cli_live_snapshot import batch_checker
        check = batch_checker()
    except Exception:
        return lambda _p: True

    def live(path):
        try:
            return check(path)
        except Exception:
            return True
    return live


def in_claude_worktrees(path) -> bool:
    parts = os.path.normpath(str(path)).split(os.sep)
    return any(a == ".claude" and b == "worktrees" for a, b in zip(parts, parts[1:]))


def registers_outside_worktrees(root) -> bool:
    """True when ``root``'s ``.git/worktrees/*/gitdir`` names a worktree
    outside ``.claude/worktrees``. A plain file read: a repo holding only
    agent lanes spawns no ``git worktree list``. A read error answers True
    (the porcelain stays the authority); a missing ``gitdir`` file is a
    broken admin entry that names no path at all."""
    admin = Path(root) / ".git" / "worktrees"
    try:
        names = list(admin.iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True
    for d in names:
        try:
            gitfile = (d / "gitdir").read_text().strip()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if not in_claude_worktrees(os.path.dirname(gitfile)):
            return True
    return False


def lane_rows(roots, *, gate, cache, git_run_fn=None, dir_stats_fn=None,
              live_check_fn=None, now=None, seen=None,
              lane_classify_budget_s=LANE_CLASSIFY_BUDGET_S, lane_min_idle_s=None):
    """Rows for every out-of-tree registered worktree of every MAIN checkout
    in ``roots`` (``.git`` a directory). ``seen`` (the caller's cache-keep
    list) gains every path judged."""
    from cli_worktree_common import STALE_WORKTREE_IDLE_MIN_AGE_S, _worktree_env_age_s
    now = time.time() if now is None else now
    min_idle_s = lane_min_idle_s
    if min_idle_s is None:
        min_idle_s = _worktree_env_age_s("AIRULESET_WORKTREE_IDLE_MIN_AGE_S",
                                         STALE_WORKTREE_IDLE_MIN_AGE_S)
    live = live_check_fn or _batch_live_check()
    deadline = time.monotonic() + lane_classify_budget_s
    claimed: set = set()
    out: list[dict] = []
    seen = seen if seen is not None else []
    real_roots = [os.path.realpath(str(r)) for r in roots]
    for root in rotate(roots, now):
        if not ((Path(root) / ".git").is_dir() and registers_outside_worktrees(root)):
            continue
        try:
            out += _repo_lane_rows(root, gate, cache, git_run_fn, dir_stats_fn, live,
                                   now, min_idle_s, deadline, seen, claimed, real_roots)
        except Exception as e:  # airuleset:script-ok one repo's reader must not drop the rung
            from watchdog import disk_guard_worktrees as dgw
            out.append({"cls": LANE_CLS, "path": str(root), "repo": str(root),
                        **dgw._skip("lane source error: %r — kept" % e, error=True)})
    return out


def _nested_checkout(real, real_roots):
    inside = real.rstrip(os.sep) + os.sep
    return next((r for r in real_roots if r.startswith(inside)), None)


def _repo_lane_rows(root, gate, cache, git_run_fn, dir_stats_fn, live, now,
                    min_idle_s, deadline, seen, claimed, real_roots):
    from cli_worktree_common import _worktree_porcelain_entries, _worktree_recency_age_s
    from watchdog import disk_guard_worktrees as dgw
    root_real = os.path.realpath(str(root))
    rows = []
    for e in rotate(_worktree_porcelain_entries(root, git_run=git_run_fn)[1:], now):
        path = e.get("path")
        if not path:
            continue
        real = os.path.realpath(path)
        if real == root_real or real in claimed or in_claude_worktrees(real):
            continue
        claimed.add(real)
        seen.append(path)
        row = {"cls": LANE_CLS, "path": path, "repo": str(root),
               "branch": e.get("branch") or "(detached)"}
        rec = None
        if e.get("locked"):
            row.update(dgw._skip("locked worktree — kept"))
        elif not os.path.isdir(path):
            row.update(dgw._skip("registered worktree directory missing — kept"))
        elif not _owned(root, path):
            row.update(dgw._skip("not a worktree of %s (stale entry) — kept" % root))
        elif nested := _nested_checkout(real, real_roots):
            row.update(dgw._skip("holds another checkout (%s) — kept" % nested))
        elif live(path):
            row.update(dgw._skip("live process cwd inside — kept"))
        elif why := gate.keep_reason(root, path):
            row.update(dgw._skip(why))
        elif (rec := _worktree_recency_age_s(root, path, now)) is None or rec <= min_idle_s:
            row.update(dgw._skip("too recent / recency unmeasurable (< %dh idle) — kept"
                                 % (min_idle_s // 3600)))
        else:
            def compute(p=path):
                if time.monotonic() > deadline:
                    return dgw._skip(_DEFERRED, error=True)     # error: never cached
                from cli_worktree_common import _worktree_git
                head = (git_run_fn or _worktree_git)(["rev-parse", "HEAD"], p)
                v = dgw._classify(p, git_run_fn, dir_stats_fn)
                if v.get("kind") == "worktree-remove":
                    if not (head or "").strip():
                        return dgw._skip("git rev-parse HEAD unreadable — kept", error=True)
                    v["head"] = head.strip()        # the executor refuses a lane that moved
                return v
            row.update(compute() if cache is None else cache.classify(
                path, dgw._worktree_gitdir(path), compute,
                refs_dep=lambda v: v.get("reason") == dgw._NOT_ON_ORIGIN))
            row["cls"] = LANE_CLS
        rows.append(row)
    return rows
