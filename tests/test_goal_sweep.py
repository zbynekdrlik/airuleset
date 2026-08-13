"""Job 9 (`goal_sweep`) + job 20 (`goal_dark_watch`, `goal_lane_sweep`,
`goal_lane_occupancy_nudge`) contract tests for the #403 collapse — split out
of `tests/test_goal_arm.py` by the #404 size ratchet's day-one cap; the
design contract those tests lock is the same `watchdog/goal.py` module
docstring `test_goal_arm.py`'s own header cites.
"""

import json
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
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
        # sync.log unless that module-level function itself is patched --
        # every test method below reaches deliver_goal (directly, or via
        # the malformed-entry/kill-switch/dry-run/already-handled early
        # exits), so isolate it centrally here rather than per test, the
        # same shape test_goal_arm.py's TestDeliverGoal/TestGoalArmCli
        # already use for the SAME module.
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

    def test_live_worker_present_refuses(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=2):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(any("occupied" in ln for ln in logs), logs)
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


# --------------------------------------------------------------------------- #
# 7. scan_goal_markers simplification — no `arm_after` key any more, and no
#    crash on a line that would have needed `_entry_asks_to_arm`/
#    `_GOAL_ASK_PROBE` (both deleted).
# --------------------------------------------------------------------------- #

