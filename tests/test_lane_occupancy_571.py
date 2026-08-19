"""#571 -- lane-occupancy working-no-tasks + low-mem surfacing.

Regression locks for the two defects the ticket fixes:

  RC1 -- the `working-no-tasks` defer read the FLAPPING render badge count
  (`_pane_live_task_count`), so a worker mid-long-tool-call (render-invisible but
  disk-live) read as "0 live tasks" and SUPPRESSED the fill nudge (gk 16 issues /
  2 lanes). It must read the STRUCTURED `count_live_workers` EVIDENCE (any
  non-stale lane, the #565 evidence predicate), never the wedged-excluding count,
  and may defer only BOUNDED (escalate after N identical defers -- the #566
  livelock class).

  RC2 -- the `low-mem` skip is silent; after M consecutive skips with a genuine
  backlog it must emit ONE deduped owner-facing CAPACITY-CAPPED signal.

The deciders are PURE (facts in / verdict out), so these lock every branch with
mutation teeth. Time is injected (mtimes via os.utime); no sleeps.
"""

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from watchdog import one_glance as og            # noqa: E402
from watchdog import transcripts as tr           # noqa: E402
from watchdog.transcripts import (               # noqa: E402
    WorkerLane, count_live_workers, encode_project_dir, lane_has_live_evidence)

CWD = "/home/newlevel/devel/lane571"
SID = "sess-lane-571-aaaa-bbbb-cccc"
NOW = 2_000_000.0
FRESH = 15 * 60   # GOAL_LANE_LIVE_WINDOW_S


# --- RC1: the working-no-tasks pure decider -----------------------------------

class TestWorkingNoTasksDecision(unittest.TestCase):
    """`lane_working_no_tasks_decision` -- the branch fires only on a ⏳ marker
    with 0 RENDER badges; the verdict then keys on STRUCTURED liveness."""

    def _d(self, **over):
        base = dict(marker="⏳", render_waiters=0, structured_live=False,
                    backlog=5, defer_streak=0, max_defers=3)
        base.update(over)
        return og.lane_working_no_tasks_decision(**base)

    def test_structured_live_lane_never_defers(self):
        # LOCK (a): a ⏳ pane with 0 render badges but a STRUCTURED live lane
        # (worker mid-long-tool-call) must NOT defer -- it proceeds to the fill
        # check. The whole gk-regression fix.
        d = self._d(structured_live=True, defer_streak=2)
        self.assertFalse(d.defer)
        self.assertEqual(d.streak, 0)          # episode reset -- lanes are live

    def test_genuinely_zero_lanes_defers_bounded(self):
        # LOCK (b): genuinely 0 non-stale structured lanes under a ⏳ marker ->
        # defer preserved (bounded, streak accumulates).
        d = self._d(structured_live=False, defer_streak=0)
        self.assertTrue(d.defer)
        self.assertEqual(d.streak, 1)
        self.assertIn("skip:working-no-tasks", d.log)

    def test_nth_identical_defer_escalates_not_forever(self):
        # LOCK (c): the Nth (max) identical defer with backlog>0 STOPS deferring
        # (escalate) so the pane reaches the gated nudge path -- never an
        # unbounded identical skip loop (the #566 livelock class).
        d = self._d(structured_live=False, defer_streak=2, max_defers=3)
        self.assertFalse(d.defer)              # streak now 3 == max -> proceed
        self.assertEqual(d.streak, 3)
        self.assertIn("ESCALATE", d.log)

    def test_zero_backlog_never_escalates(self):
        # Nothing to nudge for -> keep deferring quietly even past max_defers
        # (no point proceeding to a nudge path with an empty backlog).
        d = self._d(structured_live=False, defer_streak=9, backlog=0, max_defers=3)
        self.assertTrue(d.defer)
        self.assertIn("skip:working-no-tasks", d.log)

    def test_non_working_marker_is_not_applicable_and_resets(self):
        d = self._d(marker="✅", defer_streak=2)
        self.assertFalse(d.defer)
        self.assertEqual(d.streak, 0)
        self.assertIsNone(d.log)               # branch does not fire -> silent

    def test_render_badges_present_is_not_applicable_and_resets(self):
        d = self._d(render_waiters=3, defer_streak=2)
        self.assertFalse(d.defer)
        self.assertEqual(d.streak, 0)
        self.assertIsNone(d.log)


# --- RC2: the low-mem surface pure decider ------------------------------------

class TestLowMemSurfaceDecision(unittest.TestCase):
    """`lane_low_mem_surface_decision` -- fires ONE owner signal after M
    consecutive low-mem skips with a genuine backlog, deduped once per episode."""

    def _d(self, **over):
        base = dict(low_mem=True, backlog=12, min_backlog=3, streak=0,
                    max_streak=5, already_surfaced=False)
        base.update(over)
        return og.lane_low_mem_surface_decision(**base)

    def test_accumulates_then_surfaces_exactly_once(self):
        # LOCK (d): Mth consecutive skip -> surface; the very next skip is
        # deduped (surfaced already), so the signal fires EXACTLY once.
        d = self._d(streak=4, max_streak=5)            # streak becomes 5 == max
        self.assertTrue(d.surface)
        self.assertTrue(d.surfaced)
        self.assertEqual(d.streak, 5)
        d2 = self._d(streak=5, already_surfaced=True)  # next sweep, still low-mem
        self.assertFalse(d2.surface)                   # deduped -- fired once
        self.assertTrue(d2.surfaced)

    def test_below_streak_accumulates_without_surfacing(self):
        d = self._d(streak=1, max_streak=5)
        self.assertFalse(d.surface)
        self.assertEqual(d.streak, 2)

    def test_thin_backlog_never_surfaces(self):
        d = self._d(streak=9, backlog=2, min_backlog=3)   # backlog < min
        self.assertFalse(d.surface)

    def test_mem_recovered_resets_episode(self):
        # low_mem False -> the OOM skip did not fire this sweep -> episode over.
        d = self._d(low_mem=False, streak=4, already_surfaced=True)
        self.assertFalse(d.surface)
        self.assertEqual(d.streak, 0)
        self.assertFalse(d.surfaced)


# --- the #565 shared evidence predicate ---------------------------------------

class TestLaneHasLiveEvidence(unittest.TestCase):
    def _lane(self, state):
        return WorkerLane("a", state, 10.0, None, "")

    def test_any_non_stale_lane_is_live(self):
        for state in ("live", "wedged", "unreadable"):
            self.assertTrue(
                lane_has_live_evidence([self._lane("stale"), self._lane(state)]),
                state)

    def test_all_stale_or_empty_is_not_live(self):
        self.assertFalse(lane_has_live_evidence([self._lane("stale")]))
        self.assertFalse(lane_has_live_evidence([]))
        self.assertFalse(lane_has_live_evidence(None))


# --- LOCK (e): count_live_workers determinism + the 12-min-tool-call window ----

class TestCountLiveWorkersDeterminism(unittest.TestCase):
    def setUp(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)

    def _write_worker(self, agent_id, age_s, last_line=None):
        d = self.root / encode_project_dir(CWD) / SID / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        p = d / ("agent-" + agent_id + ".jsonl")
        line = last_line or json.dumps(
            {"type": "assistant", "message": {"role": "assistant",
             "content": [{"type": "text", "text": "working"}]}})
        p.write_text(line + "\n")
        os.utime(p, (NOW - age_s, NOW - age_s))
        return p

    def test_repeated_reads_over_same_fixture_are_identical(self):
        # LOCK (e): the flap the regression showed (0->4->0->5) was a RENDER
        # artifact; the structured on-disk read is DETERMINISTIC -- same fixture,
        # same count AND same per-lane states, every read.
        self._write_worker("live1", age_s=100)
        self._write_worker("stale1", age_s=FRESH + 500)
        r1 = count_live_workers(self.root, CWD, SID, NOW, FRESH)
        r2 = count_live_workers(self.root, CWD, SID, NOW, FRESH)
        self.assertEqual(r1[0], r2[0])
        self.assertEqual([(l.agent_id, l.state) for l in r1[1]],
                         [(l.agent_id, l.state) for l in r2[1]])
        self.assertEqual(r1[0], 1)

    def test_worker_mid_12min_tool_call_counts_live(self):
        # LOCK (a) at the reader level: a worker in a 12-min tool call writes
        # nothing for 720s, but 720 < FRESH(900), so it MUST count live -- the
        # #565 window must strictly exceed the 10-min Bash cap. (An ABSOLUTE-age
        # fixture PAST the cap, per the #565 lesson -- never relative to FRESH.)
        self._write_worker("longcall", age_s=720)
        count, ev = count_live_workers(self.root, CWD, SID, NOW, FRESH)
        self.assertEqual(count, 1)
        self.assertTrue(lane_has_live_evidence(ev))

    def test_worker_past_the_window_is_stale_not_live(self):
        self._write_worker("old", age_s=FRESH + 1)
        count, ev = count_live_workers(self.root, CWD, SID, NOW, FRESH)
        self.assertEqual(count, 0)
        self.assertFalse(lane_has_live_evidence(ev))


if __name__ == "__main__":
    unittest.main()
