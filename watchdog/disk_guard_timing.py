"""#1067 slice 1e — per-step timing and a wall budget for watchdog Job 40.

Live incident (montalu1@subdev, 24.9.2026 20:11): the sweep logged
``job start: disk_guard`` and then nothing for 115 s until systemd killed the
unit on its start timeout, so every later job of that tick was lost and the
journal could only say "somewhere inside disk_guard". ``run_disk_guard`` ran
several unlabelled, potentially heavy steps (the quota read, the scratch
discovery, the quota drift ``du``, the prevention pass, the drain ladder's
rungs, the top-consumers walk) with no duration log and no budget of its own.

One :class:`PollTimer` per ``run_disk_guard`` call:

* ``step(label, sink)`` times one step and appends
  ``disk-guard: step <label> took <N.N>s`` to ``sink`` (the sweep's ``logs``,
  printed to the journal) when it took >= :data:`STEP_LOG_MIN_S`. A healthy
  poll adds no line. The line is written even when the step raises.
* A step the process is KILLED in (systemd's timeout — the incident shape)
  never reaches its ``took`` line, so every step also leaves a breadcrumb file
  (``step-inflight`` in the guard dir) naming the innermost running step; a
  finished step restores the enclosing one or removes the file. The next
  poll's timer reads a leftover breadcrumb and reports
  ``disk-guard: previous poll (at <iso>) was killed inside step <label>``.
* ``over_budget(sink, remaining, next_label)`` is checked BETWEEN rungs by
  ``execute_drain``. The budget is :data:`DISK_GUARD_BUDGET_S`, capped by the
  caller's ``budget_s`` (the sweep's remaining soft-cap budget — disk_guard
  may start late in the sweep). At least ONE rung runs per poll (the
  ``_SweepBudget`` first-op guarantee), so a poll whose earlier steps spent
  the budget still makes progress. The first over-budget check logs one
  ``budget exceeded`` line, sets ``cut_short`` (``run_disk_guard`` then does
  not stamp the drain cadence marker, so the next due poll drains again) and
  records the first deferred rung as a RESUME point (``drain-resume``).
* ``resume_start(labels, sink)`` — the first ladder of the next poll starts at
  that resume point (when it is in this ladder and younger than
  :data:`RESUME_TTL_S`), so tail rungs are never starved by slow head rungs.
  The point is consumed on read.

A single spinning step still cannot be interrupted here (that would need a
child process — the design's rejected Approach 2); it now names itself.

``clock_fn`` is injectable (default ``time.monotonic``) so tests advance a fake
clock — no real sleeps. File I/O is best-effort: a failure is logged through
the guard's debug channel and never stops the guard.
"""

import json
import math
import time
from contextlib import contextmanager
from pathlib import Path

STEP_LOG_MIN_S = 5            # a step at/above this logs its label + duration
DISK_GUARD_BUDGET_S = 45      # poll wall budget, before the sweep's own cap
RESUME_TTL_S = 3600           # an older resume point is ignored
INFLIGHT_NAME = "step-inflight"
RESUME_NAME = "drain-resume"


def _dbg(msg):
    from watchdog import disk_guard as dg
    dg._dbg(msg)


def _unlink(path):
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        _dbg("disk-guard timing: unlink %s failed: %r" % (path, e))


def _take_json(path):
    """Read and remove a small JSON state file; None when absent or unreadable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        _dbg("disk-guard timing: read %s failed: %r" % (path, e))
        data = None
    _unlink(path)
    return data if isinstance(data, dict) else None


def _put_json(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError as e:
        _dbg("disk-guard timing: write %s failed: %r" % (path, e))


class PollTimer:
    """Wall-clock bookkeeping for one ``run_disk_guard`` poll."""

    def __init__(self, clock_fn=None, budget_s=None, step_log_min_s=None,
                 state_dir=None, now=None):
        self.clock = clock_fn or time.monotonic
        self.budget_s = (DISK_GUARD_BUDGET_S if budget_s is None
                         else min(DISK_GUARD_BUDGET_S, budget_s))
        self.step_log_min_s = STEP_LOG_MIN_S if step_log_min_s is None else step_log_min_s
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.now = time.time() if now is None else now
        self.start = self.clock()
        self.last_label = "start"
        self.cut_short = False
        self.rungs = 0
        self._stack = []
        self._resume_read = False
        self.lines = []
        crumb = self._take(INFLIGHT_NAME)
        if crumb and crumb.get("label"):
            at = crumb.get("poll")
            at = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
                  if isinstance(at, (int, float)) else "?")
            self.lines.append("disk-guard: previous poll (at %s) was killed inside "
                              "step %s" % (at, crumb["label"]))

    def _take(self, name):
        return _take_json(self.state_dir / name) if self.state_dir is not None else None

    def _put(self, name, data):
        if self.state_dir is None:
            return
        if data is None:
            _unlink(self.state_dir / name)
        else:
            _put_json(self.state_dir / name, data)

    def elapsed(self):
        return self.clock() - self.start

    @contextmanager
    def step(self, label, sink):
        t0 = self.clock()
        self._stack.append(label)
        self._put(INFLIGHT_NAME, {"label": label, "poll": self.now})
        try:
            yield
        finally:
            took = self.clock() - t0
            self._stack.pop()
            self._put(INFLIGHT_NAME, {"label": self._stack[-1], "poll": self.now}
                      if self._stack else None)
            self.last_label = label
            if took >= self.step_log_min_s:
                sink.append("disk-guard: step %s took %.1fs" % (label, took))

    def ran_rung(self):
        self.rungs += 1

    def resume_start(self, labels, sink):
        """Index in ``labels`` to start this ladder at: the previous poll's
        resume point when it is fresh and in this ladder, else 0. Only the
        first ladder of a poll consults (and consumes) it."""
        if self._resume_read:
            return 0
        self._resume_read = True
        cur = self._take(RESUME_NAME)
        if not cur or cur.get("label") not in labels:
            return 0
        ts = cur.get("ts")
        if not isinstance(ts, (int, float)) or not 0 <= self.now - ts < RESUME_TTL_S:
            return 0
        sink.append("disk-guard: resuming ladder at %s (deferred by the previous poll)"
                    % cur["label"])
        return labels.index(cur["label"])

    def over_budget(self, sink, remaining, next_label):
        """True when the poll is past its budget and at least one rung already
        ran. The first True logs one line naming the last finished step, the
        poll's elapsed seconds (rounded up) and the ``remaining`` rungs of the
        ladder that asked, and records ``next_label`` as the resume point;
        later calls on the same poll (the fs pass after a quota pass) stay
        silent."""
        if self.rungs == 0:
            return False
        elapsed = self.elapsed()
        if elapsed <= self.budget_s:
            return False
        if not self.cut_short:
            self.cut_short = True
            sink.append("disk-guard: budget exceeded after %s (%ds) — %d rung(s) "
                        "deferred to next poll"
                        % (self.last_label, math.ceil(elapsed), remaining))
            self._put(RESUME_NAME, {"label": next_label, "ts": self.now})
        return True


class _NullTimer:
    """Stand-in when ``execute_drain`` is called without a poll timer (a direct
    caller or a test): no timing lines, no files, never over budget."""

    cut_short = False

    @contextmanager
    def step(self, _label, _sink):
        yield

    def ran_rung(self):
        return None

    def resume_start(self, _labels, _sink):
        return 0

    def over_budget(self, _sink, _remaining, _next_label):
        return False


NULL_TIMER = _NullTimer()
