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
   no precious ignored file (``.env``, ``*.key``).

Uncached classifications stop after :data:`LANE_CLASSIFY_BUDGET_S` per pass (a
30k-file odoo checkout costs seconds per ``git status``); the rest is a
deferred skip, never cached. A repo whose admin dir names only agent lanes is
skipped with plain file reads, so an unchanged pass still spawns no git
(#1067 1g). Rows are ``cls="lane-worktree"``, ``kind="worktree-remove"``; the
executor runs ``git worktree remove`` WITHOUT ``--force``.
"""
# airuleset:script-ok helper module, imported from disk_guard_worktrees
from __future__ import annotations

import os
import time
from pathlib import Path

LANE_CLS = "lane-worktree"
LANE_CLASSIFY_BUDGET_S = 20.0
_DEFERRED = "classification deferred to the next pass (poll budget) — kept"
PRECIOUS_KEPT = "precious ignored file present (.env/*.key/local.settings) — kept"


def precious_ignored(wt_path: str, git_run_fn=None) -> bool:
    """A precious IGNORED file in the tree: ``git status`` never shows it and
    removing the dir would lose it. The ONE probe of the #834 worktree rung."""
    from cli_worktree_reclaim import _worktree_has_precious_ignored
    return _worktree_has_precious_ignored(wt_path, git_run_fn)


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
    for root in roots:
        if (Path(root) / ".git").is_dir() and registers_outside_worktrees(root):
            out += _repo_lane_rows(root, gate, cache, git_run_fn, dir_stats_fn, live,
                                   now, min_idle_s, deadline,
                                   seen if seen is not None else [], claimed)
    return out


def _repo_lane_rows(root, gate, cache, git_run_fn, dir_stats_fn, live, now,
                    min_idle_s, deadline, seen, claimed):
    from cli_worktree_common import _worktree_porcelain_entries, _worktree_recency_age_s
    from watchdog import disk_guard_worktrees as dgw
    root_real = os.path.realpath(str(root))
    rows = []
    for i, e in enumerate(_worktree_porcelain_entries(root, git_run=git_run_fn)):
        path = e.get("path")
        if i == 0 or not path:
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
                return dgw._classify(p, git_run_fn, dir_stats_fn)
            row.update(compute() if cache is None else cache.classify(
                path, dgw._worktree_gitdir(path), compute,
                refs_dep=lambda v: v.get("reason") == dgw._NOT_ON_ORIGIN))
            row["cls"] = LANE_CLS
        rows.append(row)
    return rows
