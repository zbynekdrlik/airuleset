"""#780 — TWO fixes on the job-20 riders, both bundle-safe (same area):

RC1: `queue_arrival_recheck` had NO per-sid nudge floor (unlike its siblings
`ops_wait_recheck`/`release_gap`), so during an active gk batch every landing
hand-off was a fresh set-delta = a re-fire nearly every FETCH TTL (~5 min;
measured 8 nudges in 2h on gk). FIX = a per-sid min-interval FLOOR + delta
ACCUMULATION: a delta inside the floor window is HELD and its new members
accumulate into the next post-floor nudge (which names ALL of them). Stays
delta-triggered, just rate-limited.

RC2: the #741 writer-side latch `compact.has_pending_request(sid)` was wired into
only the 4 goal-family writers; the 3 job-20 riders (ops_wait / release_gap /
queue_arrival) that ALSO push work into an armed loop bypassed it (0 grep hits).
FIX = consult the latch in all 3 riders' nudge branch — pending → HOLD the
keystroke, never push work through a pending compact.

RED against the pre-fix tree:
  * `_queue_decision(rec, cur, now, floor)` — the 4th `floor` arg + the `hold`
    action do not exist (TypeError / wrong action);
  * with a pending compact request each rider still TYPES its nudge (no latch),
    so `typed_texts() != []`.
GREEN once the floor + latch land.
"""

import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402,F401
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import ops_wait_recheck as ow  # noqa: E402
from watchdog import release_gap as rg  # noqa: E402
from watchdog import compact as wd_compact  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
FLOOR = 30 * 60


# =========================================================================== #
# RC1 — the per-sid nudge FLOOR + accumulation, at the PURE decider level.
# =========================================================================== #

class TestQueueDecisionFloor(unittest.TestCase):
    def test_arrival_within_floor_holds_and_keeps_old_base(self):
        # last_nudge is RECENT (well inside the floor) -> a fresh delta is HELD,
        # not nudged, and the baseline is NOT advanced (so members accumulate).
        rec = {"base": [1], "first_seen": NOW - DAY, "last_nudge": NOW - 60}
        action, out, reason, arr = qa._queue_decision(rec, [1, 2], NOW, FLOOR)
        self.assertEqual(action, "hold")
        self.assertEqual(reason, "floor")
        self.assertEqual(arr, [2])
        self.assertEqual(out["base"], [1])                 # NOT advanced
        self.assertEqual(out["last_nudge"], NOW - 60)      # preserved

    def test_two_deltas_in_one_window_accumulate_then_nudge_all(self):
        # Establish a just-nudged baseline, then TWO deltas within the floor
        # window -> both HOLD (accumulating), and the post-floor nudge names
        # BOTH new members — ONE nudge for the whole window (acceptance #1).
        rec = {"base": [1], "first_seen": NOW - DAY, "last_nudge": NOW}
        # delta A inside the floor
        a1, r1, _, arr1 = qa._queue_decision(rec, [1, 2], NOW + 100, FLOOR)
        self.assertEqual(a1, "hold")
        self.assertEqual(arr1, [2])
        # delta B inside the floor — base is still OLD so BOTH accumulate
        a2, r2, _, arr2 = qa._queue_decision(r1, [1, 2, 3], NOW + 200, FLOOR)
        self.assertEqual(a2, "hold")
        self.assertEqual(arr2, [2, 3])                     # ACCUMULATED
        self.assertEqual(r2["base"], [1])                  # still not advanced
        # floor elapsed -> ONE nudge naming both accumulated members
        a3, r3, _, arr3 = qa._queue_decision(r2, [1, 2, 3], NOW + FLOOR + 5, FLOOR)
        self.assertEqual(a3, "nudge")
        self.assertEqual(arr3, [2, 3])

    def test_first_arrival_no_last_nudge_nudges_immediately(self):
        # #733 fast-wake preserved: the floor only rate-limits the 2nd+ nudge,
        # so the FIRST arrival after a seed (no last_nudge) fires at once.
        rec = {"base": [1], "first_seen": NOW - DAY}   # no last_nudge
        action, out, reason, arr = qa._queue_decision(rec, [1, 2], NOW, FLOOR)
        self.assertEqual(action, "nudge")
        self.assertEqual(arr, [2])

    def test_floor_default_zero_is_backward_compatible(self):
        # 3-arg default floor=0 -> the pre-#780 behavior (a delta always nudges,
        # even with a recent last_nudge). Keeps the existing decider tests green.
        rec = {"base": [1], "first_seen": NOW - DAY, "last_nudge": NOW - 1}
        action, out, reason, arr = qa._queue_decision(rec, [1, 2], NOW)
        self.assertEqual(action, "nudge")

    def test_seed_and_track_carry_last_nudge(self):
        # A track (no arrival) must preserve last_nudge so the floor still
        # applies to a future arrival.
        rec = {"base": [1, 2], "first_seen": NOW - DAY, "last_nudge": NOW - 100}
        action, out, _, _ = qa._queue_decision(rec, [1, 2], NOW, FLOOR)
        self.assertEqual(action, "track")
        self.assertEqual(out["last_nudge"], NOW - 100)


class TestNudgeFloorHelper(unittest.TestCase):
    def test_floor_env_override(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_QUEUE_ARRIVAL_NUDGE_FLOOR_S": "1800"}):
            self.assertEqual(qa._nudge_floor(), 1800)

    def test_floor_clamped_to_min(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_QUEUE_ARRIVAL_NUDGE_FLOOR_S": "1"}):
            self.assertEqual(qa._nudge_floor(), qa.QUEUE_ARRIVAL_NUDGE_FLOOR_MIN_S)


# =========================================================================== #
# RC1 — the FLOOR at the orchestrator level (hold vs post-floor nudge).
# =========================================================================== #

class _QAOrch(unittest.TestCase):
    CWD = "/home/newlevel/devel/qafloor"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name,
                          "AIRULESET_QUEUE_ARRIVAL_NUDGE_FLOOR_S": str(FLOOR)})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-780-qa")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, now, qrecs, fetch, tmux, *, handled=None, state=None):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return qa.goal_queue_arrival_recheck(
                now, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, handled if handled is not None else set(),
                queue_fetch=fetch,
                state=state if state is not None else {},
                sleep_fn=lambda *a, **k: None)

    def test_delta_within_floor_holds_no_keystroke(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY,
                            "last_nudge": NOW - 60}}
        tmux = self._tmux()
        logs = self._run(NOW, qrecs, lambda cwd: [1, 2], tmux)
        self.assertTrue(any("hold:floor" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1])   # accumulate — not advanced

    def test_delta_after_floor_nudges_accumulated_members(self):
        # base [1], floor elapsed since last_nudge; two members accumulated ->
        # ONE nudge naming both, base promoted to the full union on delivery.
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY,
                            "last_nudge": NOW - FLOOR - 100}}
        tmux = self._tmux()
        logs = self._run(NOW, qrecs, lambda cwd: [1, 2, 3], tmux, handled=set())
        self.assertTrue(any("queue-arrival nudge" in ln for ln in logs), logs)
        typed = "".join(tmux.typed_texts())
        self.assertIn("#2", typed)
        self.assertIn("#3", typed)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2, 3])
        self.assertEqual(qrecs[self.sid]["last_nudge"], NOW)   # floor anchor set


# =========================================================================== #
# RC2 — the #741 compact latch, wired into ALL THREE job-20 riders.
# =========================================================================== #

class _LatchRiderBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/latch780"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-780-latch")
        self.sid = self.tpath.stem
        # Point has_pending_request at a hermetic temp store (the #741 pattern).
        cd = TemporaryDirectory()
        self.addCleanup(cd.cleanup)
        self.creqp = Path(cd.name) / "compact-requests.json"
        pp = m.patch.object(wd_compact, "compact_requests_path",
                            return_value=self.creqp)
        pp.start()
        self.addCleanup(pp.stop)

    def _seed_compact(self):
        wd_compact.record_compact_request(self.sid, self.CWD, now=NOW,
                                          path=self.creqp, origin="self-callback")

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)


class TestQueueArrivalLatch(_LatchRiderBase):
    def test_pending_compact_holds_the_arrival_nudge(self):
        self._seed_compact()
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs = qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, set(), queue_fetch=lambda cwd: [1, 2],
                state={}, sleep_fn=lambda *a, **k: None)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1])   # not advanced -> retry

    def test_no_pending_compact_still_nudges(self):
        # sanity: WITHOUT a pending compact the arrival nudge fires as before.
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        with m.patch("airuleset.resolve_authority", return_value="full"):
            qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, set(), queue_fetch=lambda cwd: [1, 2],
                state={}, sleep_fn=lambda *a, **k: None)
        self.assertIn("stuck-check:", "".join(tmux.typed_texts()))


class TestOpsWaitLatch(_LatchRiderBase):
    def test_pending_compact_holds_the_ops_wait_nudge(self):
        self._seed_compact()
        wrecs = {self.sid: {"first_seen": NOW - DAY}}
        tmux = self._tmux()
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs = ow.goal_ops_wait_recheck(
                NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, set(), ops_wait_fetch=lambda cwd: [101, 102],
                state={}, sleep_fn=lambda *a, **k: None, cadence=1, i_count=5)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])


class TestReleaseGapLatch(_LatchRiderBase):
    def test_pending_compact_holds_the_release_gap_nudge(self):
        self._seed_compact()
        rrecs = {self.sid: {"first_seen": NOW - DAY}}
        tmux = self._tmux()
        rstate = {"ahead": 3, "in_flight": False, "train": False}
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs = rg.goal_release_gap_recheck(
                NOW, tmux, rrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, set(), release_state_fetch=lambda cwd: rstate,
                state={}, sleep_fn=lambda *a, **k: None, cadence=1, min_ahead=1)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])


if __name__ == "__main__":
    unittest.main()
