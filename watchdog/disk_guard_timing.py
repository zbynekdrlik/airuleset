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
  (``step-inflight`` in the guard dir, with the writer's pid) naming the
  innermost running step; a finished step restores the enclosing one or
  removes the file. The next poll's timer reads a leftover breadcrumb and
  reports ``disk-guard: previous poll (at <iso>) was killed inside step
  <label>`` — unless the writer is still alive (a concurrent manual
  ``watchdog --once``), whose breadcrumb is left alone.
* ``over_budget(sink, remaining, next_label, ladder)`` is checked BETWEEN rungs
  by ``execute_drain``. The budget is :data:`DISK_GUARD_BUDGET_S`, capped by
  the caller's ``budget_s`` (the sweep's remaining soft-cap budget —
  disk_guard may start late in the sweep). Each ladder that runs this poll
  runs at least ONE rung (the ``_SweepBudget`` first-op guarantee), so a poll
  whose earlier steps spent the budget still makes progress. (A cut-short
  quota pass skips the fs ladder for that poll — ``run_drain_passes``.) The
  first over-budget check logs one ``budget exceeded`` line, sets ``cut_short``
  (``run_disk_guard`` then does not stamp the drain cadence marker, so the
  next due poll drains again) and records the first deferred rung as that
  LADDER's resume point (``drain-resume`` holds one entry per ladder:
  ``prevention`` / ``quota`` / ``fs`` — the ladders share rung labels, so a
  point never crosses ladders).
* ``resume_start(ladder, labels, sink)`` — the next run of that ladder starts
  at its resume point (when it is in the ladder and 0 <= age <
  :data:`RESUME_TTL_S`), so tail rungs are never starved by slow head rungs.
  The entry is consumed on read; other ladders' entries stay.

A dry-run poll gets no ``state_dir``: it neither reads nor writes the resume
points or the breadcrumb of the real guard.

A single spinning step still cannot be interrupted here (that would need a
child process — the design's rejected Approach 2); it now names itself.

``clock_fn`` is injectable (default ``time.monotonic``) so tests advance a fake
clock — no real sleeps. File I/O is best-effort (atomic temp+rename writes): a
failure is logged through the guard's debug channel and never stops the guard.
"""

import json
import math
import os
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
    """Remove ``path``; False when it could not be removed."""
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        _dbg("disk-guard timing: unlink %s failed: %r" % (path, e))
        return False


def _load_json(path):
    """A small JSON state file as a dict; None when absent or unreadable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        _dbg("disk-guard timing: read %s failed: %r" % (path, e))
        return None
    return data if isinstance(data, dict) else None


def put_text(path, text):
    """Atomic write (temp file + rename): a kill or ENOSPC mid-write never
    leaves a truncated file behind. Best-effort; also the guard's cadence
    stamps (#1067 1f)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text)
        os.replace(tmp, path)
    except OSError as e:
        _dbg("disk-guard timing: write %s failed: %r" % (path, e))
        _unlink(tmp)


def _put_json(path, data):
    put_text(path, json.dumps(data))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True             # exists, not ours to signal
    return True


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
        self.rungs = {}                 # ladder -> rungs run this poll
        self._stack = []
        self.lines = []
        self._report_killed_step()

    def _report_killed_step(self):
        """A breadcrumb left by a DEAD earlier poll names the step it was
        killed in. A live writer's (a concurrent run) is left alone; one that
        cannot be removed (unwritable dir) is not reported, or every poll
        would repeat it."""
        crumb = self._load(INFLIGHT_NAME)
        if not crumb or not crumb.get("label"):
            return
        pid = crumb.get("pid")
        if isinstance(pid, int) and pid != os.getpid() and _pid_alive(pid):
            return
        if not _unlink(self.state_dir / INFLIGHT_NAME):
            return
        at = crumb.get("poll")
        at = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
              if isinstance(at, (int, float)) else "?")
        self.lines.append("disk-guard: previous poll (at %s) was killed inside "
                          "step %s" % (at, crumb["label"]))

    def _load(self, name):
        return _load_json(self.state_dir / name) if self.state_dir is not None else None

    def _put(self, name, data):
        if self.state_dir is None:
            return
        if data is None:
            _unlink(self.state_dir / name)
        else:
            _put_json(self.state_dir / name, data)

    def _crumb(self, label):
        return {"label": label, "poll": self.now, "pid": os.getpid()}

    def elapsed(self):
        return self.clock() - self.start

    @contextmanager
    def step(self, label, sink):
        t0 = self.clock()
        self._stack.append(label)
        self._put(INFLIGHT_NAME, self._crumb(label))
        try:
            yield
        finally:
            took = self.clock() - t0
            self._stack.pop()
            self._put(INFLIGHT_NAME, self._crumb(self._stack[-1]) if self._stack else None)
            self.last_label = label
            if took >= self.step_log_min_s:
                sink.append("disk-guard: step %s took %.1fs" % (label, took))

    def ran_rung(self, ladder):
        self.rungs[ladder] = self.rungs.get(ladder, 0) + 1

    def resume_start(self, ladder, labels, sink):
        """Index in ``labels`` to start ``ladder`` at: its resume point when
        fresh and in this ladder, else 0. The ladder's entry is consumed;
        other ladders' entries stay."""
        points = self._load(RESUME_NAME)
        cur = points.pop(ladder, None) if points else None
        if cur is None:
            return 0
        self._put(RESUME_NAME, points or None)
        if not isinstance(cur, dict) or cur.get("label") not in labels:
            return 0
        ts = cur.get("ts")
        if not isinstance(ts, (int, float)) or not 0 <= self.now - ts < RESUME_TTL_S:
            return 0
        sink.append("disk-guard: resuming ladder at %s (deferred by the previous poll)"
                    % cur["label"])
        return labels.index(cur["label"])

    def over_budget(self, sink, remaining, next_label, ladder):
        """True when the poll is past its budget and at least one rung of
        ``ladder`` already ran. The first True logs one line naming the last
        finished step, the poll's elapsed seconds (rounded up) and the
        ``remaining`` rungs of that ladder; later calls on the same poll stay
        silent. Every True records ``next_label`` as the ladder's resume point."""
        if not self.rungs.get(ladder):
            return False
        elapsed = self.elapsed()
        if elapsed <= self.budget_s:
            return False
        if not self.cut_short:
            self.cut_short = True
            sink.append("disk-guard: budget exceeded after %s (%ds) — %d rung(s) "
                        "deferred to next poll"
                        % (self.last_label, math.ceil(elapsed), remaining))
        points = self._load(RESUME_NAME) or {}
        points[ladder] = {"label": next_label, "ts": self.now}
        self._put(RESUME_NAME, points)
        return True


class _NullTimer:
    """Stand-in when ``execute_drain`` is called without a poll timer (a direct
    caller or a test): no timing lines, no files, never over budget."""

    cut_short = False

    @contextmanager
    def step(self, _label, _sink):
        yield

    def ran_rung(self, _ladder):
        return None

    def resume_start(self, _ladder, _labels, _sink):
        return 0

    def over_budget(self, _sink, _remaining, _next_label, _ladder):
        return False


NULL_TIMER = _NullTimer()
