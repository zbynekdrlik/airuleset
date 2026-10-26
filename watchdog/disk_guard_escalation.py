"""When watchdog Job 40's disk state is owner-actionable, and what it names.

The trigger is the footer's own owner-actionable state (#925): >= SEVERE_PCT,
or the drain reported ``drain_exhausted`` at >= the box's OWN effective
drain-critical level (``disk_guard_worktrees.effective_critical_pct``: 85 % on
a <= 64 GB root like gk, 90 % above; issue 1136), not on a shared-stream box
(each account's drain sees only its own data). ``disk_guard.severe_journal``
then writes ONE ``SEVERE`` journal line with :func:`severe_detail`. No ticket
is filed (#1137) and nothing pings (#693/#850): the footer ``disk NN%`` is the
owner's surface.
"""

import os

MAX_SKIPPED_ROWS = 10       # skipped rungs named on the line, capped


def default_critical_pct():
    """The box's own effective drain-critical level, the SAME helper the
    ``drain_exhausted`` bookkeeping (``disk_guard_post._record_exhausted``)
    uses, so the line fires exactly where the drain reports exhaustion."""
    from watchdog import disk_guard_worktrees as _dgw
    return _dgw.effective_critical_pct()


def is_severe(status, severe_pct, critical_pct, box_class=None):
    """True at >= ``severe_pct``, or when the drain reported
    ``drain_exhausted`` (strictly ``True``) at >= ``critical_pct`` on a box
    that is not ``shared-stream``."""
    worst = status.get("worst_pct", 0)
    if worst >= severe_pct:
        return True
    return (status.get("drain_exhausted") is True and worst >= critical_pct
            and box_class != "shared-stream")


def running_under_pytest():
    """True under pytest (``PYTEST_CURRENT_TEST``, set by pytest for every
    test and never in production). The one detection every "refuse the real
    default under pytest" guard reuses (the worktree reclaimers' real-home
    refusal, #1195 ``cli_reclaim_guard``)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def severe_detail(status, top, human, target_pct):
    """One line: why the state is severe, the rungs the drain had to skip,
    the reclaimable candidates still on disk, and the largest live session
    scratchpad (#1209: protected space is invisible in the reclaimable list)."""
    if status.get("drain_exhausted") is True:
        parts = ["drain exhausted (freed nothing it may delete)"]
    else:
        parts = ["still above %d%% after the drain" % target_pct]
    skipped = [r for r in status.get("drain_skipped_rungs") or [] if isinstance(r, dict)]
    if skipped:
        rows = ["%s %s: %s" % (r.get("rung") or r.get("cls") or "?", r.get("path") or "?",
                               r.get("reason") or "?") for r in skipped[:MAX_SKIPPED_ROWS]]
        if len(skipped) > MAX_SKIPPED_ROWS:
            rows.append("... %d more" % (len(skipped) - MAX_SKIPPED_ROWS))
        parts.append("skipped: " + "; ".join(rows))
    parts.append("reclaimable: " + (", ".join(
        "%s=%s" % (p, human(b)) for p, b in (top or [])) or "(none)"))
    lls = status.get("largest_live_scratch")
    if isinstance(lls, dict) and lls.get("path"):
        parts.append("largest live scratchpad (kept): %s=%s"
                     % (lls["path"], human(lls.get("bytes") or 0)))
    return " | ".join(parts)
