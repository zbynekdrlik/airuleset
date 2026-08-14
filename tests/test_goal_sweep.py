"""Job 9 (`goal_sweep`) + job 20 (`goal_dark_watch`, `goal_lane_sweep`,
`goal_lane_occupancy_nudge`) contract tests for the #403 collapse — split out
of `tests/test_goal_arm.py` by the #404 size ratchet's day-one cap; the
design contract those tests lock is the same `watchdog/goal.py` module
docstring `test_goal_arm.py`'s own header cites.
"""

import json
import os
import unittest
import unittest.mock as m
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_ARMED_DRAFT_CAP,
    GOAL_ARMED_STRIP_CAP,
    GOAL_BUSY_CAP,
    GOAL_IDLE_CAP,
    _encode,
    _isolate_goal_state,
    DeliverGoalFakeTmux,
    _write_goal_marker,
    _write_marker_transcript,
)

class TestGoalSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/goalsweep"

    def setUp(self):
        # #437: goal.goal_sweep() -> goal.deliver_goal() -> _log_goal_sync()
        # resolves goal.goal_sync_log_path() to the REAL ~/.claude/goal-
        # sync.log unless that module-level function itself is patched.
        # Only 3 of this class's 7 methods (test_sent_request_is_cleared,
        # test_skip_leaves_the_request_pending, test_expired_request_is_
        # dropped) actually reach deliver_goal -- the other 4 (malformed-
        # entry/already-handled/dry-run/kill-switch) hit an earlier
        # `continue` in goal_sweep's own early-exit chain and never call
        # it at all. Isolate UNCONDITIONALLY here anyway, not per test:
        # correctness must not depend on which of the 7 currently reaches
        # the logger, since a future 8th method could easily land on the
        # writing side -- the same unconditional shape test_goal_arm.py's
        # TestDeliverGoal/TestGoalArmCli already use for the SAME module.
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _reqp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "goal-requests.json")

    def test_sent_request_is_cleared(self):
        proj = self._dir()
        sid = "sess-sweep-1"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        self.assertTrue(any("OK (goal-sweep)" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_skip_leaves_the_request_pending(self):
        proj = self._dir()
        sid = "sess-sweep-2"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_BUSY_CAP)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        self.assertTrue(any("SKIP (goal-sweep)" in ln for ln in logs), logs)
        self.assertIn(sid, goal.load_goal_requests(reqp))

    def test_expired_request_is_dropped(self):
        proj = self._dir()
        sid = "sess-sweep-3"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        far_future = 1000 + goal.GOAL_REQUEST_MAX_AGE_S + 100
        logs = goal.goal_sweep(far_future, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        self.assertTrue(any("LAPSE" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_malformed_entry_with_no_text_is_dropped_not_retried_forever(self):
        reqp = self._reqp()
        Path(reqp).write_text(json.dumps({"sess-x": {"cwd": "/x"}}))
        goal.goal_sweep(1000, requests_path=reqp, run=lambda *a, **k: "")
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_already_handled_this_sweep_is_skipped(self):
        proj = self._dir()
        sid = "sess-sweep-4"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        handled = {sid}
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, handled=handled,
                               sleep_fn=lambda s: None)
        self.assertTrue(any("handled this sweep already" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])
        self.assertIn(sid, goal.load_goal_requests(reqp))   # left pending

    def test_dry_run_never_types(self):
        proj = self._dir()
        sid = "sess-sweep-5"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_sweep(2000, run=tmux, dry_run=True, projects_dir=proj,
                               requests_path=reqp)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("DRY-RUN" in ln for ln in logs), logs)

    def test_kill_switch_disables_the_whole_sweep(self):
        reqp = self._reqp()
        goal.record_goal_request("sess-x", "/x", "/goal x", "full", path=reqp)
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = goal.goal_sweep(1000, requests_path=reqp,
                                   run=lambda *a, **k: "")
        self.assertTrue(any("DISABLED" in ln for ln in logs), logs)
        self.assertIn("sess-x", goal.load_goal_requests(reqp))  # untouched


# --------------------------------------------------------------------------- #
# 5. goal_dark_watch — job 20 half 1: NEVER types, 2-sweep debounce, silent
#    on cleared/no-marker, and the shared janitor recovery runs first.
# --------------------------------------------------------------------------- #

class TestGoalDarkWatch(unittest.TestCase):
    CWD = "/home/newlevel/devel/darkwatch"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_kill_switch_disables_dark_watch(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = goal.goal_dark_watch(1000, run=lambda *a, **k: "")
        self.assertTrue(any("DISABLED" in ln for ln in logs), logs)

    def test_never_sends_a_keystroke_regardless_of_outcome(self):
        proj = self._dir()
        sid = "sess-dark-1"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        # First observation.
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        # Second, still-dark observation of the SAME marker -- the ping
        # fires here, but STILL zero keystrokes ever.
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])

    def test_debounced_across_two_sweeps_before_pinging(self):
        proj = self._dir()
        sid = "sess-dark-2"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []

        def send_fn(m, **k):
            sent.append(m)

        state = {}
        goal.goal_dark_watch(1000, run=tmux, send_fn=send_fn, projects_dir=proj,
                             state=state, sleep_fn=lambda s: None)
        self.assertEqual(sent, [], "must NOT ping on the first observation")
        goal.goal_dark_watch(2000, run=tmux, send_fn=send_fn, projects_dir=proj,
                             state=state, sleep_fn=lambda s: None)
        self.assertEqual(len(sent), 1, "must ping once the SAME episode "
                        "survives a second sweep")

    def test_cleared_marker_stays_silent(self):
        proj = self._dir()
        sid = "sess-dark-3"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal cleared: /goal x",
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(sent, [])

    def test_no_marker_at_all_stays_silent(self):
        proj = self._dir()
        sid = "sess-dark-4"
        _write_marker_transcript(proj, self.CWD, sid)   # no goal marker ever
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent = []
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(sent, [])

    def test_armed_footer_matching_marker_stays_silent(self):
        proj = self._dir()
        sid = "sess-dark-5"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        sent = []
        goal.goal_dark_watch(1000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        goal.goal_dark_watch(2000, run=tmux, send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, sleep_fn=lambda s: None)
        self.assertEqual(sent, [])

    def test_sweep_deadline_defers_remaining_panes(self):
        # #403 STEP 0's own requirement: this per-pane loop must respect
        # the #172/#255 wall-clock self-bound, since it walks EVERY live
        # candidate pane, unbounded by anything but the box's own pane
        # count.
        proj = self._dir()
        panes = []
        for i in range(3):
            sid = "sess-dark-budget-%d" % i
            cwd = "%s-%d" % (self.CWD, i)
            _write_marker_transcript(proj, cwd, sid)
            panes.append(("%%%d" % i, "claude", cwd, str(100 + i)))
        tmux = DeliverGoalFakeTmux(panes, GOAL_IDLE_CAP)
        clock = {"t": 0.0}

        def time_fn():
            clock["t"] += 1.0
            return clock["t"]

        logs = goal.goal_dark_watch(1000, run=tmux, projects_dir=proj,
                                    sleep_fn=lambda s: None, time_fn=time_fn,
                                    sweep_deadline=1.5)
        self.assertTrue(any("budget-exceeded" in ln for ln in logs), logs)

    def test_unbounded_when_no_deadline_given(self):
        proj = self._dir()
        sid = "sess-dark-nolimit"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_dark_watch(1000, run=tmux, projects_dir=proj,
                                    sleep_fn=lambda s: None)
        self.assertFalse(any("budget-exceeded" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 6. goal_lane_sweep / goal_lane_occupancy_nudge — job 20 half 2: the ONE
#    remaining watchdog-INITIATED keystroke. Recent-human-activity DOES
#    apply here (unlike arm delivery).
# --------------------------------------------------------------------------- #

class TestGoalLaneSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/lanesweep"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_backlog_fetch_is_a_no_op(self):
        logs = goal.goal_lane_sweep(1000, run=lambda *a, **k: "")
        self.assertEqual(logs, [])

    def test_kill_switch_disables_lane_sweep(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = goal.goal_lane_sweep(1000, run=lambda *a, **k: "",
                                        backlog_fetch=lambda cwd: 5)
        self.assertEqual(logs, [])

    def test_not_armed_is_skipped_entirely(self):
        proj = self._dir()
        sid = "sess-lane-1"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_lane_sweep(1000, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 5)
        self.assertEqual(logs, [])
        self.assertEqual(tmux.sent, [])

    def test_sweep_deadline_defers_remaining_panes(self):
        proj = self._dir()
        panes = []
        for i in range(3):
            sid = "sess-lane-budget-%d" % i
            cwd = "%s-%d" % (self.CWD, i)
            _write_marker_transcript(proj, cwd, sid)
            panes.append(("%%%d" % i, "claude", cwd, str(200 + i)))
        tmux = DeliverGoalFakeTmux(panes, GOAL_ARMED_CAP)
        clock = {"t": 0.0}

        def time_fn():
            clock["t"] += 1.0
            return clock["t"]

        logs = goal.goal_lane_sweep(1000, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 5,
                                    time_fn=time_fn, sweep_deadline=1.5)
        self.assertTrue(any("budget-exceeded" in ln for ln in logs), logs)


class TestGoalLaneOccupancyNudge(unittest.TestCase):
    CWD = "/home/newlevel/devel/lanenudge"
    SID = "sess-lane-nudge-1"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _call(self, captured, backlog_fetch, now, tmtime, rec=None, state=None,
             authority="full", handled=None):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], captured)
        with m.patch("airuleset.resolve_authority", return_value=authority):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec if rec is not None else {}, self.SID, self.CWD,
                "111", captured, tpath, tmtime, "loc", None, False, handled,
                proj, backlog_fetch=backlog_fetch,
                state=state if state is not None else {},
                sleep_fn=lambda s: None)
        return logs, owns, tmux

    def test_idle_armed_pane_with_backlog_and_no_workers_nudges(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertIn("C-s" not in tmux.keys() and True, [True])  # no stash needed
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_goal_disarmed_between_sweep_and_send_is_never_typed(self):
        # #403-review m2: the FRESH re-verify right before the send
        # (`pane_goal_armed(fresh) is not True`) had no test coverage in
        # this file at all -- dropping it left every existing test green.
        # Model a session whose goal got CLEARED while this sweep's own
        # earlier checks were still running: the sweep started with an
        # armed pane, but the capture taken immediately before typing
        # (`fresh`) shows a bare, unarmed one -- the nudge must refuse,
        # never type into a session that stopped being armed underneath
        # it.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "capture_pane", return_value=GOAL_IDLE_CAP):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("skip raced" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_recent_human_activity_refuses_the_nudge(self):
        # Unlike arm delivery, the lane-occupancy nudge IS a genuinely
        # watchdog-INITIATED action, so it keeps the recent-human-activity
        # gate.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(True, "human just typed")):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_question_marker_refuses_the_nudge(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_goal_marker(proj, self.CWD, self.SID,
                           "❓ NEEDS YOU: rozhodni sa", ts_epoch=tmtime)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "transcript_last_marker", return_value="❓"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])

    def test_no_open_backlog_refuses(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 0, now, tmtime)
        self.assertTrue(any("no measurable open backlog" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_live_worker_present_small_backlog_refuses(self):
        # #442 re-fix 2: (2 workers, backlog 5) -> silent. Under-saturated
        # (2 < GOAL_LANE_SATURATION_WORKERS) but the backlog is small
        # (5 <= GOAL_LANE_UNDERSAT_BACKLOG_MIN), so no fill nudge. This REPLACES
        # the old "occupied" reason: worker presence is now a COUNT decision, so
        # 2 workers is under-saturated, not blanket "occupied".
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=2):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("under-saturated but backlog" in ln for ln in logs),
                        logs)
        self.assertEqual(tmux.sent, [])

    # ---------------------------------------------------------------- #
    # #442 re-fix 2 (REOPEN č.2 + owner directives 2026-08-14) -- the
    # COUNT-based fill-the-cap widening. Nudge when live_workers < 5 AND
    # backlog > 10 AND MemAvailable is healthy; the old guard only fired at
    # exactly 0 workers, and its `_pane_has_bg_agent` early-skip meant it
    # could never fire on a live box with visible workers at all.
    # ---------------------------------------------------------------- #

    def test_two_workers_big_backlog_with_memory_nudges(self):
        # THE live-box lock: the pane SHOWS the agent strip (2 `◯` rows), so the
        # OLD code's `_pane_has_bg_agent` early-skip would have refused here --
        # the exact reopen-2 root cause. `_count_live_subagents`=2 + backlog 37 +
        # healthy memory must now NUDGE.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=2), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 37,
                                          now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=2" in ln for ln in logs), logs)
        self.assertTrue(any("MemAvailable=8192MB" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)
        # The under-saturated text, not the empty-lane "0 dispatched" text.
        typed = " ".join(a[-1] for a in tmux.sent if "-l" in a)
        self.assertIn("beží len 2", typed)

    def test_four_workers_big_backlog_with_memory_nudges(self):
        # Owner #456: the floor is 5, so 4 workers is still under-saturated ->
        # NUDGE (was silent under the earlier <3 draft).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=4), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 37,
                                          now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=4" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_five_workers_big_backlog_is_silent(self):
        # 5 workers >= GOAL_LANE_SATURATION_WORKERS -> saturated -> silent.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=5), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 37,
                                          now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("saturated (>= 5 workers)" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_zero_workers_small_backlog_still_nudges(self):
        # Regression: the 0-worker empty-lane nudge is UNCHANGED -- fires on ANY
        # open backlog (backlog 3 here), with no memory gate.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 3, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_undersaturated_low_memory_skips_with_diagnostic(self):
        # Under-saturated + big backlog but LOW memory -> silent, and the skip
        # is journaled with the measured value so a tight box stays diagnosable.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=2), \
             m.patch.object(goal, "_mem_available_mb", return_value=812):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 37,
                                          now, tmtime)
        self.assertTrue(owns)  # a genuine candidate, deferred for memory
        self.assertTrue(any("skip:low-mem MemAvailable=812MB" in ln
                            for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_max_nudges_gives_up_and_pings_once(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    # ---------------------------------------------------------------- #
    # #442 — the lane-fill path gets its OWN, much shorter "live
    # conversation" definition. The shared 30-min blanket window made the
    # nudge structurally self-suppressing on a box the owner glances at
    # every ~20-30 min (gk journal: presence marker 1331-1628s old on
    # every single attempt).
    # ---------------------------------------------------------------- #

    def _glance_call(self, marker_age_s, now=100000):
        """Drive the nudge through the REAL recent-human-activity check
        with a REAL presence marker aged `marker_age_s` — a unique sid so
        the /tmp marker can never collide with a live session's own, and
        removed again via addCleanup."""
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        sid = "sess-lane-glance-" + uuid.uuid4().hex[:10]
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        tpath = proj / _encode(self.CWD) / (sid + ".jsonl")
        marker = Path("/tmp/claude-user-active-%s" % sid)
        marker.write_text("")
        self.addCleanup(lambda: marker.unlink(missing_ok=True))
        t = now - marker_age_s
        os.utime(marker, (t, t))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP)
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, sid, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        return logs, owns, tmux

    def test_owner_glance_20_minutes_ago_does_not_suppress_the_nudge(self):
        # #442 RED: a presence marker 20 minutes old is an owner GLANCE,
        # not a live conversation — under the old blanket 30-min window it
        # suppressed every nudge forever; the lane path's own short window
        # must let this one through.
        logs, owns, tmux = self._glance_call(20 * 60)
        self.assertTrue(owns)
        self.assertFalse(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_presence_marker_seconds_old_still_refuses_the_nudge(self):
        # Control: a GENUINELY live conversation (marker seconds old) must
        # still refuse — the guard is narrowed, never deleted.
        logs, owns, tmux = self._glance_call(60)
        self.assertTrue(owns)
        self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    # ---------------------------------------------------------------- #
    # #442 — an at-rest draft is DELIVERABLE via deliver_with_stash (the
    # primitive exists for exactly this), never a "skip draft" dead end.
    # ---------------------------------------------------------------- #

    def test_at_rest_draft_delivers_via_stash_instead_of_skipping(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        calls = []

        def fake_stash(pid, text, run, captured=None, logs=None,
                       sleep_fn=None):
            calls.append((pid, text))
            return True

        rec = {"lna": 2}    # a prior abort streak must clear on success
        state = {}
        with m.patch.object(wd, "deliver_with_stash", side_effect=fake_stash):
            logs, owns, tmux = self._call(GOAL_ARMED_DRAFT_CAP,
                                          lambda cwd: 5, now, tmtime, rec=rec,
                                          state=state)
        self.assertTrue(owns)
        self.assertFalse(any("skip draft" in ln for ln in logs), logs)
        self.assertEqual(len(calls), 1, logs)
        self.assertEqual(calls[0][0], "111")
        self.assertEqual(rec.get("ln"), 1)
        self.assertNotIn("lna", rec)   # #442-review F2: streak cleared
        # #442-review F3: janitor provenance cleared again on success.
        self.assertNotIn("111", state.get("janitor_watch", {}))
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)

    def test_draft_stash_abort_never_consumes_the_nudge_budget(self):
        # An aborted verified delivery typed nothing (or undid itself) —
        # transient, retried next sweep, and it must NOT advance the
        # ln/llast budget (the #176 verified-and-fallible-path lesson). It
        # DOES advance the consecutive-abort streak (#442-review F2), and
        # the janitor provenance mark must PERSIST on failure so the
        # shared janitor can recover a stuck stash send (#442-review F3).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        calls = []

        def fake_stash(pid, text, run, captured=None, logs=None,
                       sleep_fn=None):
            calls.append((pid, text))
            return False

        rec = {}
        state = {}
        with m.patch.object(wd, "deliver_with_stash", side_effect=fake_stash):
            logs, owns, tmux = self._call(GOAL_ARMED_DRAFT_CAP,
                                          lambda cwd: 5, now, tmtime, rec=rec,
                                          state=state)
        self.assertTrue(owns)
        self.assertEqual(len(calls), 1, logs)
        self.assertNotIn("ln", rec)
        self.assertNotIn("llast", rec)
        self.assertEqual(rec.get("lna"), 1)
        self.assertIn("111", state.get("janitor_watch", {}))
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs),
                         logs)

    def test_consecutive_stash_aborts_reach_the_give_up_ping(self):
        # #442-review F2: without a bound, a permanently-aborting lane
        # retried silently every sweep forever and the give-up ping was
        # structurally unreachable (the nudge counter only advances on
        # success). At the abort cap the give-up branch fires instead.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_DRAFT_CAP)
        rec = {"lna": goal.GOAL_LANE_MAX_STASH_ABORTS}
        sent = []
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "deliver_with_stash",
                            side_effect=AssertionError(
                                "no delivery may be attempted past the "
                                "abort cap")):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec, self.SID, self.CWD, "111",
                GOAL_ARMED_DRAFT_CAP, tpath, tmtime, "loc",
                lambda msg, **k: sent.append(msg), False, None, proj,
                backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        self.assertTrue(owns)
        self.assertTrue(any("consecutive stash aborts" in ln for ln in logs),
                        logs)
        self.assertEqual(len(sent), 1, logs)
        self.assertIn("zlyhalo", sent[0])
        self.assertEqual(tmux.sent, [])

    def test_draft_changed_between_captures_refuses_composition(self):
        # #442-review F1: un-submitted COMPOSITION stamps neither
        # recent-activity signal (the presence marker only ever gets
        # stamped on a prompt SUBMIT), so the two-capture draft diff is
        # the one direct evidence of live typing — a box whose content
        # moved between the sweep-top capture and the pre-send one must
        # refuse, consume nothing, and never reach the stash primitive.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        grown = GOAL_ARMED_DRAFT_CAP.replace("rozpisany draft",
                                             "rozpisany draft a este kus")
        for label, top_cap, fresh_cap in (
                ("draft grew", GOAL_ARMED_DRAFT_CAP, grown),
                ("bare became draft", GOAL_ARMED_CAP, GOAL_ARMED_DRAFT_CAP)):
            with self.subTest(label):
                tmux = DeliverGoalFakeTmux(
                    [("%9", "claude", self.CWD, "111")], top_cap,
                    cap_seq=[fresh_cap])
                rec = {}
                calls = []
                with m.patch("airuleset.resolve_authority",
                             return_value="full"), \
                     m.patch.object(wd, "deliver_with_stash",
                                    side_effect=lambda *a, **k:
                                    calls.append(a) or True):
                    logs, owns = goal.goal_lane_occupancy_nudge(
                        now, tmux, rec, self.SID, self.CWD, "111", top_cap,
                        tpath, tmtime, "loc", None, False, None, proj,
                        backlog_fetch=lambda cwd: 5, state={},
                        sleep_fn=lambda s: None)
                self.assertTrue(owns)
                self.assertTrue(any("composing" in ln for ln in logs), logs)
                self.assertEqual(calls, [])
                self.assertEqual(
                    [a for a in tmux.sent if "send-keys" in " ".join(a)], [])
                self.assertNotIn("ln", rec)

    def test_non_at_rest_draft_still_skips(self):
        # #442-review F4: a draft that is NOT at rest (the free-prompt
        # shape refuses — e.g. a menu-pointer head) must still be the old
        # "skip draft" refusal, never a delivery.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_has_free_prompt", return_value=False):
            logs, owns, tmux = self._call(GOAL_ARMED_DRAFT_CAP,
                                          lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("skip draft" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])


class TestGoalLaneNudgeDoctrine(unittest.TestCase):
    """#442 — the nudge TEXT must teach the fleet-dispatch doctrine
    (skills/autopilot SKILL.md), not just poke: parallel worktree worker
    dispatch, the account-wide concurrent-agent cap of 8, and
    serialize-only integration."""

    def test_nudge_text_commands_fleet_dispatch_doctrine(self):
        rendered = goal.GOAL_LANE_NUDGE_TEXT % (7, 2)
        low = rendered.lower()
        self.assertIn("worktree", low)
        self.assertIn("paraleln", low)
        self.assertIn("8", rendered)
        self.assertIn("sériovo", low)

    def test_undersat_nudge_text_is_work_driven_and_names_the_count(self):
        # #442 re-fix 2: the under-saturated text names the real worker count,
        # commands fleet dispatch, AND frames saturation as WORK-DRIVEN (not a
        # fixed cap number).
        rendered = goal.GOAL_LANE_UNDERSAT_NUDGE_TEXT % (2, 37, 1)
        low = rendered.lower()
        self.assertIn("beží len 2", rendered)
        self.assertIn("worktree", low)
        self.assertIn("paraleln", low)
        self.assertIn("sériovo", low)
        # work-driven, not a fixed target
        self.assertIn("prác", low)
        self.assertIn("ci", low)

    def test_min_mem_threshold_is_a_named_constant_documented(self):
        # #442 re-fix 2: the memory floor is a named, sane default (~1.5 GB).
        self.assertGreaterEqual(goal.GOAL_LANE_MIN_MEM_AVAIL_MB, 1024)
        self.assertLessEqual(goal.GOAL_LANE_MIN_MEM_AVAIL_MB, 4096)
        self.assertEqual(goal.GOAL_LANE_SATURATION_WORKERS, 5)

    def test_mem_available_mb_reads_proc_meminfo(self):
        # On any managed Linux box this reads a real positive MB value.
        val = goal._mem_available_mb()
        self.assertIsInstance(val, int)
        self.assertGreater(val, 0)

    def test_mem_available_mb_fails_open_on_read_error(self):
        # Fail-OPEN: unreadable meminfo -> None, so the caller does NOT block.
        def boom(*a, **k):
            raise OSError("no /proc/meminfo")
        with m.patch("builtins.open", side_effect=boom):
            self.assertIsNone(goal._mem_available_mb())

    def test_lane_live_convo_window_is_minutes_not_the_30min_blanket(self):
        self.assertLessEqual(goal.GOAL_LANE_LIVE_CONVO_S, 5 * 60)
        self.assertLess(goal.GOAL_LANE_LIVE_CONVO_S,
                        wd.GOAL_AUTOARM_RECENT_HUMAN_S)


# --------------------------------------------------------------------------- #
# 7. scan_goal_markers simplification — no `arm_after` key any more, and no
#    crash on a line that would have needed `_entry_asks_to_arm`/
#    `_GOAL_ASK_PROBE` (both deleted).
# --------------------------------------------------------------------------- #

