"""#1055 P2 -- per-sweep subprocess budget: a COUNTER over the watchdog's
subprocess runners, plus a per-sweep MEMO namespace for identical calls.

Counter (design (a)): the watchdog fires ~130 gh/git/tmux/ps subprocesses per
60s sweep (measured on gk after the P1 transcript-read fix). This module counts
them in ONE place so a regression is visible in the journal (`subprocess: N
calls, Ws, top: label:n, ...`) next to P1's `transcript reads:` line, and so the
(e) registry budget can HOLD a job that would push a sweep over its subprocess
budget. `record_subprocess` is called from every runner (`_default_run`,
`_gh_out`, `run_counted`, `_default_git_run`, `default_ps_fetch`); a memo HIT
never records (it issues no child), so the counter shows exactly the saving the
(b) memos and (c)/(d) collapses buy.

Sweep memo (design (b)): identical subprocess calls within ONE sweep -- the same
`tmux list-panes -a`, the same per-pane `capture-pane`/`display-message`, one
`ps -u`, one `git fetch` per root -- are collapsed by memoizing their results in
a per-sweep dict. The memo is ACTIVE only between `begin_sweep_memo()` (run_once
top) and `end_sweep_memo()` (run_once bottom); a direct call OUTSIDE a sweep (a
unit test, an ad-hoc CLI path) is never memoized (`memoized` degrades to a plain
`compute()`), so behaviour there is byte-identical to today. The world cannot
change mid-sweep (a sweep is seconds), so a memo hit is decision-identical to a
fresh read -- the behaviour-lock tests pin that.

Both are module globals reset per sweep, the same pattern P1's
`_TRANSCRIPT_TAIL_CACHE` uses. stdlib only; this module imports nothing from the
package, so it is import-safe from tmux_io / airuleset / reaper / cards.
"""

import os
import time

# --- (a) subprocess counter ------------------------------------------------ #
_STATS = {"n": 0, "wall": 0.0, "by_label": {}}


def reset_subprocess_stats():
    """Zero the per-sweep counter (run_once top, like `reset_transcript_cache`)."""
    _STATS["n"] = 0
    _STATS["wall"] = 0.0
    _STATS["by_label"] = {}


def record_subprocess(label, wall=0.0):
    """Record ONE subprocess: bump the total, the wall seconds, the per-label
    count. `label` is a short command class (gh/git/tmux/ps/...). Called from
    every watchdog runner; a memo HIT does NOT call this (it ran no child). A
    non-numeric `wall` is ignored (the count still lands)."""
    _STATS["n"] += 1
    try:
        _STATS["wall"] += max(0.0, float(wall))
    except (TypeError, ValueError):
        # airuleset:script-ok a malformed wall from a caller must never break
        # the counter -- the CALL still counts; only its wall contribution is
        # dropped (wall is a diagnostic sum, not load-bearing).
        pass
    bl = _STATS["by_label"]
    bl[str(label)] = bl.get(str(label), 0) + 1


def subprocess_stats():
    """`{"n", "wall", "by_label", "top"}` -- `top` is `[(label, n), ...]` sorted
    by count desc then label, for the journal `top: label:n, ...` field."""
    bl = dict(_STATS["by_label"])
    top = sorted(bl.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"n": _STATS["n"], "wall": _STATS["wall"], "by_label": bl, "top": top}


def run_counted(argv, *, label=None, run=None, **kwargs):
    """Run ONE subprocess and record it. `run` (optional) is an alternate runner
    called `run(argv, **kwargs)` (a test injection or a wrapper); default is
    `subprocess.run`. `label` defaults to the basename of `argv[0]`. Returns
    whatever the runner returns. Records even on exception -- a failed or
    timed-out child still forked a process (and cost wall time)."""
    lbl = label
    if lbl is None:
        try:
            lbl = os.path.basename(str(argv[0]))
        except Exception:
            lbl = "?"
    t0 = time.monotonic()
    try:
        if run is not None:
            return run(argv, **kwargs)
        import subprocess
        return subprocess.run(argv, **kwargs)
    finally:
        record_subprocess(lbl, time.monotonic() - t0)


# --- (b) per-sweep memo namespace ------------------------------------------ #
_MEMO = {}
_ACTIVE = [False]


def begin_sweep_memo():
    """Activate + clear the per-sweep memo (run_once top). Only between this and
    `end_sweep_memo()` do the memoized readers collapse identical calls."""
    _MEMO.clear()
    _ACTIVE[0] = True


def end_sweep_memo():
    """Deactivate + clear the memo (run_once bottom) so a later direct call (a
    unit test in the same process) is never served a stale sweep value."""
    _MEMO.clear()
    _ACTIVE[0] = False


def sweep_memo_active():
    return _ACTIVE[0]


def sweep_memo():
    return _MEMO


def memoized(key, compute):
    """Return `compute()`, memoized under `key` for the current sweep. Outside a
    sweep (memo inactive) always calls `compute()` -- no caching, no shared
    state -- so direct/test call sites behave exactly as before. A cached value
    is returned as-is (shared object): every consumer of the memoized readers
    only iterates/slices it, never mutates it (verified per call site)."""
    if not _ACTIVE[0]:
        return compute()
    if key in _MEMO:
        return _MEMO[key]
    val = compute()
    _MEMO[key] = val
    return val
