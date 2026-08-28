"""#741 — the writer-side latch (`compact.has_pending_request`) HOLDS every
work-pushing goal writer while a `/compact` is pending for that session, so a
drained-boundary compact is delivered in a quiet pane before any next-batch work
is pushed in. Job 7 (a human's Discord answer) is the sole exception.

RED against the pre-#741 tree: none of the goal writers consult the compact
store, so each keeps typing / re-arming into a pane with a pending /compact.
"""

import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                    # noqa: E402,F401
from watchdog import goal                                # noqa: E402
from watchdog import compact as wd_compact               # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _encode,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

# The repoke disarm harness -- a 5-repoke armed pane that WOULD type `/goal
# clear`; with a pending compact it must HOLD instead.
from test_goal_question_repoke import (  # noqa: E402
    _repoke_entries,
    _write_entries,
)


class _LatchBase(unittest.TestCase):
    """Isolated goal state + a temp compact-requests store the latch reads."""

    CWD = "/home/newlevel/devel/latch741"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.creqp = Path(d.name) / "compact-requests.json"
        # `has_pending_request(sid)` (no path) resolves `compact_requests_path()`;
        # point it at our temp store so the latch read is hermetic.
        p = m.patch.object(wd_compact, "compact_requests_path",
                           return_value=self.creqp)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _seed_compact(self, sid, now=1000):
        wd_compact.record_compact_request(sid, self.CWD, now=now,
                                          path=self.creqp, origin="self-callback")


class TestGoalSweepLatch(_LatchBase):
    def _sweep(self, proj, sid, cap=GOAL_IDLE_CAP):
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   cap, model_type=True)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=self.reqp, sleep_fn=lambda s: None)
        return logs, tmux

    def test_pending_compact_holds_the_goal_arm(self):
        proj = self._dir()
        sid = "sess-sweep-hold"
        _write_marker_transcript(proj, self.CWD, sid)
        self._seed_compact(sid)
        logs, tmux = self._sweep(proj, sid)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], "no keystroke while a compact is pending")
        self.assertIn(sid, goal.load_goal_requests(self.reqp),
                      "the goal-arm request is left pending, not dropped")

    def test_no_pending_compact_delivers_as_before(self):
        # CONTROL: without a pending compact the same idle pane arms normally --
        # so the hold is caused by the latch, not the harness.
        proj = self._dir()
        sid = "sess-sweep-nohold"
        _write_marker_transcript(proj, self.CWD, sid)
        logs, _ = self._sweep(proj, sid)
        self.assertFalse(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertTrue(any("OK (goal-sweep)" in ln for ln in logs), logs)


class TestLaneNudgeLatch(_LatchBase):
    SID = "sess-lane-hold"

    def _call(self, proj, seed_compact):
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        if seed_compact:
            self._seed_compact(self.SID)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=str(tpath))
        sent = []
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                100000, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, 100000 - goal.GOAL_LANE_IDLE_S - 100, "loc",
                lambda msg, **k: sent.append(msg), False, None, proj,
                backlog_fetch=lambda cwd: 5, state={}, sleep_fn=lambda s: None)
        return logs, owns, sent, tmux

    def test_pending_compact_holds_the_next_batch_nudge(self):
        proj = self._dir()
        logs, owns, sent, tmux = self._call(proj, seed_compact=True)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertFalse(owns, "a held nudge does not claim ownership")
        self.assertEqual(sent, [], "no owner ping while a compact is pending")
        self.assertEqual(tmux.sent, [], "no keystroke while a compact is pending")

    def test_control_no_pending_compact_does_not_hold(self):
        proj = self._dir()
        logs, _owns, _sent, _tmux = self._call(proj, seed_compact=False)
        self.assertFalse(any("hold:compact-pending" in ln for ln in logs), logs)


class TestDarkWatchLatch(_LatchBase):
    def _dark(self, proj, tmux, state, now):
        return goal.goal_dark_watch(
            now, run=tmux, state=state, projects_dir=proj,
            send_fn=lambda msg, **k: None, sleep_fn=lambda s: None,
            rearm_fn=lambda cwd: ("/goal x", "full"),
            obligation_fn=lambda cwd: (5, now), human_ts_fn=lambda tp: None)

    def test_pending_compact_holds_the_dark_rearm(self):
        # The silently-dark shape (mark=set + un-armed footer) that dark_watch
        # normally debounces toward a re-arm. With a pending compact the latch
        # HOLDS before that branch: no debounce entry, no re-arm, no keystroke.
        proj = self._dir()
        sid = "sess-dark-hold"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 100000.0
        self._seed_compact(sid)
        state = {}
        logs = self._dark(proj, tmux, state, now)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertNotIn(sid, state.get("goal_dark_seen", {}),
                         "held before the dark debounce that would re-arm")
        self.assertNotIn(sid, goal.load_goal_requests(self.reqp),
                         "no re-arm request recorded while a compact is pending")
        self.assertEqual(tmux.typed_texts(), [], "no keystroke")

    def test_control_no_pending_compact_reaches_the_debounce(self):
        proj = self._dir()
        sid = "sess-dark-nohold"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        state = {}
        logs = self._dark(proj, tmux, state, 100000.0)
        self.assertFalse(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertIn(sid, state.get("goal_dark_seen", {}),
                      "without a pending compact the dark debounce runs")


class TestQuestionRepokeLatch(_LatchBase):
    def _run(self, proj, sid, tpath):
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=str(tpath))
        state = {}
        logs = goal.goal_question_repoke_watch(
            100000.0, run=tmux, state=state, projects_dir=proj,
            sleep_fn=lambda s: None, human_ts_fn=lambda tp: None)
        return logs, tmux, state

    def test_pending_compact_holds_the_disarm(self):
        # 5 byte-identical re-pokes on an armed pane WOULD type `/goal clear`;
        # with a pending compact the latch HOLDS the disarm keystroke.
        proj = self._dir()
        sid = "sess-repoke-hold"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(5))
        self._seed_compact(sid)
        logs, tmux, state = self._run(proj, sid, tpath)
        self.assertTrue(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertNotIn("/goal clear", tmux.typed_texts(),
                         "the disarm keystroke is held while a compact is pending")
        self.assertNotIn(sid, state.get("goal_disarmed_q", {}))

    def test_control_no_pending_compact_disarms(self):
        proj = self._dir()
        sid = "sess-repoke-nohold"
        tpath = _write_entries(proj, self.CWD, sid, _repoke_entries(5))
        logs, tmux, _state = self._run(proj, sid, tpath)
        self.assertFalse(any("hold:compact-pending" in ln for ln in logs), logs)
        self.assertIn("/goal clear", tmux.typed_texts(),
                      "without a pending compact the stuck loop is disarmed")


class TestJob7StillDelivers741(unittest.TestCase):
    """Job 7 (a human's Discord answer, watchdog/discord_replies.py) is the SOLE
    latch exception -- the human's answer always lands. Structural lock: the
    delivery path must NOT consult the compact latch."""

    def test_discord_replies_delivery_has_no_compact_latch(self):
        import inspect
        from watchdog import discord_replies
        src = inspect.getsource(discord_replies)
        self.assertNotIn("has_pending_request", src,
                         "job 7 must deliver regardless of a pending compact")


if __name__ == "__main__":
    unittest.main()
