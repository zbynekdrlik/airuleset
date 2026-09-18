"""#780 (RC2 kept) + #1023 (RC1 re-based): job-20 rider cadence + compact latch.

RC1 (#780 → #1023): `queue_arrival_recheck`'s per-JOB 30-min nudge floor sat BELOW
the owner's 1 h rule; #1023 DELETES it (`QUEUE_ARRIVAL_NUDGE_FLOOR_S`, `_nudge_floor`,
the `floor` param + `last_nudge` machinery) and moves the floor into the ONE shared
per-pane-per-KIND 60-min `nudge_gate` floor consulted via `gate_ok`. Delta
ACCUMULATION is preserved: `_queue_decision` returns a `nudge` verdict with `base`
kept OLD, and when `gate_ok` holds the keystroke the orchestrator returns before
advancing `base`, so members arriving inside the floor window accumulate into the
next post-floor nudge. These RC1 tests now lock that behaviour through the shared
gate (`TestPerJobFloorRemoved1023` + the `_QAOrch` accumulation tests).

RC2 (#741, UNCHANGED): the writer-side compact latch `compact.pending_compact_hold`
is consulted in all 3 job-20 riders' nudge branch — pending → HOLD the keystroke,
never push work through a pending compact.
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
from watchdog import nudge_gate as ng  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
FLOOR = 60 * 60   # #1023: the per-pane-per-kind floor (replaces the deleted 30-min per-job floor)


# =========================================================================== #
# RC1 (#1023) — the per-JOB 30-min floor is DELETED; the shared per-pane-per-KIND
# 60-min `nudge_gate` floor holds a same-kind repeat, and accumulation is
# preserved (base kept OLD while the floor holds the keystroke). These orchestrator
# tests lock that the floor + accumulation still work through the shared gate.
# =========================================================================== #

class TestPerJobFloorRemoved1023(unittest.TestCase):
    def test_queue_decision_takes_no_floor_arg(self):
        # the per-job floor param + the floor `hold`/`last_nudge` machinery are gone
        rec = {"base": [1], "first_seen": NOW - DAY}
        action, out, reason, arr = qa._queue_decision(rec, [1, 2], NOW)
        self.assertEqual(action, "nudge")   # a delta always yields a nudge verdict
        self.assertEqual(arr, [2])
        self.assertEqual(out["base"], [1])          # base kept OLD (accumulation)
        self.assertNotIn("last_nudge", out)         # dead field removed

    def test_per_job_floor_symbols_removed(self):
        self.assertFalse(hasattr(qa, "QUEUE_ARRIVAL_NUDGE_FLOOR_S"))
        self.assertFalse(hasattr(qa, "QUEUE_ARRIVAL_NUDGE_FLOOR_MIN_S"))
        self.assertFalse(hasattr(qa, "_nudge_floor"))


class _QAOrch(unittest.TestCase):
    CWD = "/home/newlevel/devel/qafloor"

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
                                              "sess-1023-qa")
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
        # a queue-arrival nudge was confirmed-delivered NOW-60 (nudge_cadence) —
        # the shared per-kind floor holds a new arrival with hold:floor, base kept.
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        ng.mark_sent(state, self.sid, "queue-arrival", NOW - 60)
        tmux = self._tmux()
        logs = self._run(NOW, qrecs, lambda cwd: [1, 2], tmux, state=state)
        self.assertTrue(any("hold:floor" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1])   # accumulate — not advanced

    def test_delta_after_floor_nudges_accumulated_members(self):
        # #1023 fix-forward 2 (owner "3"): the 3 h total cap now counts the SAME
        # kind, so the accumulated nudge fires only once the last queue-arrival
        # send is past the 3 h total gap (not merely past the 60-min floor); two
        # members accumulated -> ONE nudge naming both, base promoted on delivery.
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        ng.mark_sent(state, self.sid, "queue-arrival", NOW - 3 * FLOOR - 100)
        tmux = self._tmux()
        logs = self._run(NOW, qrecs, lambda cwd: [1, 2, 3], tmux,
                         handled=set(), state=state)
        self.assertTrue(any("queue-arrival nudge" in ln for ln in logs), logs)
        typed = "".join(tmux.typed_texts())
        self.assertIn("#2", typed)
        self.assertIn("#3", typed)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2, 3])
        # the shared floor clock is stamped on the confirmed delivery
        self.assertEqual(state["nudge_cadence"][self.sid]["queue-arrival"], NOW)

    def test_accumulation_across_the_floor_end_to_end(self):
        # #1023 acceptance, updated for fix-forward 2 (owner "3"): deliver at t0
        # (no prior floor); a new arrival at t0+45min is HELD (hold:floor, base
        # kept); at t0+61min it is STILL held — now by the 3 h total cap, which
        # counts the SAME kind (hold:total-cap, base kept); only past the 3 h
        # total gap does the single accumulated nudge name the member.
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        # t0: first arrival delivers (fast-wake) and stamps the floor.
        self._run(NOW, qrecs, lambda cwd: [1, 2], self._tmux(),
                  handled=set(), state=state)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])
        # t0+45min: a new arrival is within the 60-min floor -> hold, base kept.
        logs = self._run(NOW + 45 * 60, qrecs, lambda cwd: [1, 2, 3],
                         self._tmux(), handled=set(), state=state)
        self.assertTrue(any("hold:floor" in ln for ln in logs), logs)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])   # accumulating
        # t0+61min: the per-kind floor has elapsed but the 3 h total cap holds
        # this SAME-kind repeat (hold:total-cap), base still kept.
        logs = self._run(NOW + 61 * 60, qrecs, lambda cwd: [1, 2, 3],
                         self._tmux(), handled=set(), state=state)
        self.assertTrue(any("hold:total-cap" in ln for ln in logs), logs)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])   # still accumulating
        # t0+181min: past the 3 h total cap -> the post-floor nudge names #3.
        tmux = self._tmux()
        self._run(NOW + 181 * 60, qrecs, lambda cwd: [1, 2, 3],
                  tmux, handled=set(), state=state)
        self.assertIn("#3", "".join(tmux.typed_texts()))
        self.assertEqual(qrecs[self.sid]["base"], [1, 2, 3])


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
