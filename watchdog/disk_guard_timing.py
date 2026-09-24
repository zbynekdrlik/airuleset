"""#1067 slice 1e — per-step timing and a wall budget for watchdog Job 40.

Live incident (montalu1@subdev, 24.9.2026 20:11): the sweep logged
``job start: disk_guard`` and then nothing for 115 s until systemd killed the
unit on its start timeout, so every later job of that tick was lost and the
journal could only say "somewhere inside disk_guard". ``run_disk_guard`` runs
several unlabelled, potentially heavy steps (the scratch discovery, the quota
drift ``du``, the prevention pass, the drain ladder's planners, the top
consumers walk) with no duration log and no disk_guard-level budget.

One :class:`PollTimer` per ``run_disk_guard`` call:

* ``step(label, sink)`` times one step and appends
  ``disk-guard: step <label> took <N.N>s`` to ``sink`` (the sweep's ``logs``
  list, printed to the journal) when it took >= :data:`STEP_LOG_MIN_S`. A
  healthy poll adds no line. The line is written even when the step raises.
* ``over_budget(sink, remaining)`` is checked BETWEEN rungs by
  ``execute_drain``. Once the poll's wall time (from the timer's creation, i.e.
  the start of ``run_disk_guard``) exceeds :data:`DISK_GUARD_BUDGET_S`, the
  caller stops running planners; the FIRST such call logs one
  ``budget exceeded`` line and sets ``cut_short`` so ``run_disk_guard`` does
  not stamp the drain cadence marker (the next due poll drains again).

A single step that spins cannot be interrupted here (that would need a child
process — the rejected Approach 2 of the design); it now names itself, and the
steps after it no longer pile onto an already-late poll.

``clock_fn`` is injectable (default ``time.monotonic``) so tests advance a fake
clock — no real sleeps.
"""

import time
from contextlib import contextmanager

STEP_LOG_MIN_S = 5            # a step at/above this logs its label + duration
DISK_GUARD_BUDGET_S = 45      # poll wall budget, well under the sweep's systemd timeout


class PollTimer:
    """Wall-clock bookkeeping for one ``run_disk_guard`` poll."""

    def __init__(self, clock_fn=None, budget_s=None, step_log_min_s=None):
        self.clock = clock_fn or time.monotonic
        self.budget_s = DISK_GUARD_BUDGET_S if budget_s is None else budget_s
        self.step_log_min_s = STEP_LOG_MIN_S if step_log_min_s is None else step_log_min_s
        self.start = self.clock()
        self.last_label = "start"
        self.cut_short = False

    def elapsed(self):
        return self.clock() - self.start

    @contextmanager
    def step(self, label, sink):
        t0 = self.clock()
        try:
            yield
        finally:
            took = self.clock() - t0
            self.last_label = label
            if took >= self.step_log_min_s:
                sink.append("disk-guard: step %s took %.1fs" % (label, took))

    def over_budget(self, sink, remaining):
        """True when the poll is past its budget. The first True logs one line
        naming the last finished step, the poll's elapsed seconds and the
        ``remaining`` rungs of the ladder that asked; later calls on the same
        poll (the fs pass after a quota pass) stay silent."""
        elapsed = self.elapsed()
        if elapsed <= self.budget_s:
            return False
        if not self.cut_short:
            self.cut_short = True
            sink.append("disk-guard: budget exceeded after %s (%ds) — %d rung(s) "
                        "deferred to next poll" % (self.last_label, elapsed, remaining))
        return True


class _NullTimer:
    """Stand-in when ``execute_drain`` is called without a poll timer (a direct
    caller or a test): no timing lines, never over budget."""

    cut_short = False

    @contextmanager
    def step(self, _label, _sink):
        yield

    def over_budget(self, _sink, _remaining):
        return False


NULL_TIMER = _NullTimer()
