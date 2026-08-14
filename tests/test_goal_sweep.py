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

    # ----------------------------------------------------------------- #
    # #459 — STAGED re-ping. The FIRST ping is unchanged (locked by the
    # tests above); a persistently-dark goal is then re-pinged on a
    # widening schedule, but ONLY while the per-cwd obligation cache proves
    # work still remains — so an achieved backlog (open==0) never nags.
    # ----------------------------------------------------------------- #

    def _dark(self, sid):
        """A dark-goal fixture: transcript+`Goal set:` marker, an idle pane
        with NO `◎ /goal` footer (pane_goal_armed -> False)."""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        return proj, tmux

    def _sweep(self, tmux, proj, state, sent, now, obl):
        goal.goal_dark_watch(now, run=tmux,
                             send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, state=state,
                             sleep_fn=lambda s: None, obligation_fn=obl)

    @staticmethod
    def _obl(open_n, ts):
        """A fake obligation_fn returning a fixed (open, ts) for any cwd."""
        return lambda cwd: (open_n, ts)

    def test_persistently_dark_goal_with_work_remaining_is_re_pinged(self):
        # #459's actual fix, and the RED driver: a still-dark goal whose cwd
        # still has open obligations must be RE-pinged after the schedule
        # interval — RED against the pre-#459 one-shot dedup (ping once, then
        # skip forever), so the away user who missed the first ping is
        # reminded within their waking window.
        proj, tmux = self._dark("sess-dark-reping")
        sent, state = [], {}
        obl = self._obl(5, 1500)             # 5 open obligations, fresh cache
        self._sweep(tmux, proj, state, sent, 1000, obl)   # first observation
        self._sweep(tmux, proj, state, sent, 2000, obl)   # first ping fires
        self.assertEqual(len(sent), 1, "first ping fires on the 2-sweep debounce")
        t = 2000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10
        self._sweep(tmux, proj, state, sent, t, obl)      # schedule cleared
        self.assertEqual(len(sent), 2, "a still-dark goal with work remaining must "
                         "be re-pinged after the schedule interval")

    def test_first_ping_text_and_dedup_key_are_byte_for_byte_403(self):
        # #459 preserves #403's FIRST ping exactly: the message is the original
        # "zomrelo potichu" text (never the "STÁLE mŕtvy" reminder), and the
        # dedup_key stays goal-dark:sid:mark WITHOUT a :count suffix — so a
        # legacy on-disk dedup marker from pre-#459 code never yields a
        # duplicate first ping across the deploy boundary. Re-pings append
        # :count. Teeth against silently changing either.
        proj, tmux = self._dark("sess-dark-firstping")
        rec, state = [], {}
        obl = self._obl(5, 1500)

        def send_fn(m, **k):
            rec.append((m, k.get("dedup_key")))

        def sweep(now):
            goal.goal_dark_watch(now, run=tmux, send_fn=send_fn,
                                 projects_dir=proj, state=state,
                                 sleep_fn=lambda s: None, obligation_fn=obl)

        sweep(1000)                                           # first observation
        sweep(2000)                                           # first ping
        self.assertEqual(len(rec), 1)
        first_msg, first_key = rec[0]
        self.assertIn("zomrelo potichu", first_msg)
        self.assertNotIn("STÁLE", first_msg)
        # legacy shape: exactly "goal-dark:<sid>:<mark>" (two colons after label)
        self.assertEqual(first_key.count(":"), 2, first_key)
        sweep(2000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10)  # re-ping #2
        self.assertEqual(len(rec), 2)
        reping_msg, reping_key = rec[1]
        self.assertIn("STÁLE", reping_msg)
        self.assertEqual(reping_key, first_key + ":2", reping_key)

    def test_achieved_backlog_zero_obligations_gets_one_ping_never_nags(self):
        # #459 safety — an ACHIEVED loop is transcript-identical to a stall
        # (both mark=set / footer dark, no cleared marker). The cache's
        # open==0 is the discriminator: the ONE ping still fires (unchanged),
        # then NEVER a re-ping. Teeth against a re-ping-regardless mutant.
        proj, tmux = self._dark("sess-dark-achieved")
        sent, state = [], {}
        obl = self._obl(0, 1500)             # backlog empty -> achieved
        self._sweep(tmux, proj, state, sent, 1000, obl)
        self._sweep(tmux, proj, state, sent, 2000, obl)
        self.assertEqual(len(sent), 1)
        self._sweep(tmux, proj, state, sent,
                    2000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10, obl)
        self.assertEqual(len(sent), 1, "an achieved backlog (open==0) must never nag")

    def test_unavailable_obligation_cache_does_not_re_ping(self):
        # #459 fail-safe — cache absent/unreadable => cannot confirm work
        # remains => no re-ping (the first ping already went out). Fail
        # toward no-nag. Teeth against a re-ping-on-unknown mutant.
        proj, tmux = self._dark("sess-dark-nocache")
        sent, state = [], {}
        obl = self._obl(None, None)
        self._sweep(tmux, proj, state, sent, 1000, obl)
        self._sweep(tmux, proj, state, sent, 2000, obl)
        self.assertEqual(len(sent), 1)
        self._sweep(tmux, proj, state, sent,
                    2000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10, obl)
        self.assertEqual(len(sent), 1, "an unavailable cache must not re-ping")

    def test_stale_obligation_cache_does_not_re_ping(self):
        # #459 — an obligation cache older than GOAL_DARK_CACHE_MAX_AGE_S is
        # not trusted for the re-ping gate (work may have been done elsewhere
        # while the loop sat dark for days). Teeth against dropping freshness.
        proj, tmux = self._dark("sess-dark-stale")
        sent, state = [], {}
        base = 1_700_000_000
        self._sweep(tmux, proj, state, sent, base, lambda c: (5, base - 10))
        self._sweep(tmux, proj, state, sent, base + 1000, lambda c: (5, base - 10))
        self.assertEqual(len(sent), 1)
        t = base + 1000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10
        stale_ts = t - goal.GOAL_DARK_CACHE_MAX_AGE_S - 100
        self._sweep(tmux, proj, state, sent, t, lambda c: (5, stale_ts))
        self.assertEqual(len(sent), 1, "a stale cache must not re-ping")

    def test_re_pinging_is_hard_capped_per_episode(self):
        # #459 — with a cache that stays FRESH and non-empty forever (a
        # stalled session that keeps re-rendering its statusline), the ONLY
        # thing that can bound the ping count is the hard cap. (A FROZEN
        # cache instead ages out at GOAL_DARK_CACHE_MAX_AGE_S — the stale
        # test above — so the freshness gate is the practical bound for a
        # genuinely dead loop; the cap backstops the fresh-cache case.)
        proj, tmux = self._dark("sess-dark-cap")
        sent, state = [], {}
        now = [1_700_000_000]

        def obl(cwd):
            return (5, now[0] - 60)          # always fresh (60s before now)

        self._sweep(tmux, proj, state, sent, now[0], obl)    # first observation
        for _ in range(goal.GOAL_DARK_REPING_MAX + 8):
            now[0] += 30 * 3600                              # > final stage (24h)
            self._sweep(tmux, proj, state, sent, now[0], obl)
        self.assertEqual(len(sent), goal.GOAL_DARK_REPING_MAX,
                         "re-pings must be hard-capped per episode")

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
    # #442 THIRD GAP (2026-08-14) -- the top-of-function idle gate
    # (`idle < GOAL_LANE_IDLE_S`, 15 min) early-returned with EMPTY logs
    # BEFORE workers/backlog were counted, so a BUSY under-saturated
    # session (turns spinning -> transcript mtime always fresh) never
    # reached the fill-the-cap decision and journalled NOTHING. Plus
    # GOAL_LANE_MAX_NUDGES was a permanent give-up. The fix moves the idle
    # gate into the branch decision (0-worker keeps 15 min; under-saturated
    # has NO idle floor and NO permanent give-up), and every reaching sweep
    # logs its decision with numbers.
    # ---------------------------------------------------------------- #

    def test_busy_undersaturated_fires_despite_fresh_transcript(self):
        # THE headline lock: a BUSY session (FRESH tmtime, idle=30s) with 2
        # workers, big backlog, healthy memory must FIRE -- exactly the gk
        # live-box state (2 workers, I 32, journal empty 20+ min). On the OLD
        # code the top idle gate early-returned SILENTLY here (logs == [],
        # nothing typed); this is the reopen-3 root cause.
        now = 100000
        tmtime = now - 30  # fresh: transcript written 30s ago, idle << 15min
        with m.patch.object(wd, "_count_live_subagents", return_value=2), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 32,
                                          now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=2" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_zero_worker_active_session_logs_skip_idle_not_silent(self):
        # The 0-worker EMPTY-lane branch keeps its 15-min idle requirement --
        # a box being actively typed into may be mid-dispatch. But it must no
        # longer be SILENT: an active 0-worker sweep now logs `skip:idle` with
        # the numbers (the old code returned empty logs -> undiagnosable).
        now = 100000
        tmtime = now - 30  # active
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("skip:idle" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_undersaturated_has_no_permanent_giveup(self):
        # A session that stays under-saturated for hours must keep being
        # pushed: GOAL_LANE_MAX_NUDGES is NOT a give-up for the fill-the-cap
        # branch. With ln already at the cap, an under-saturated box still
        # FIRES (cooldown-gated only), never "GAVE UP". On the OLD code this
        # gave up and went silent.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES}
        with m.patch.object(wd, "_count_live_subagents", return_value=2), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 32,
                                          now, tmtime, rec=rec)
        self.assertTrue(owns)
        self.assertFalse(any("GAVE UP" in ln for ln in logs), logs)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_undersaturated_cooldown_logs_remaining(self):
        # Under-saturated but within the per-fire cooldown window -> skip, and
        # the skip is journalled with the remaining seconds (item 3), not the
        # old numberless "rate-limited".
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"llast": now - 60}  # fired 60s ago, inside GOAL_LANE_INTERVAL_S
        with m.patch.object(wd, "_count_live_subagents", return_value=2), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 32,
                                          now, tmtime, rec=rec)
        self.assertTrue(owns)
        # #442-review F2: assert the VALUE, not just the substring, so a
        # sign-flip mutant (remaining=-840s) is caught. Fired 60s ago into a
        # 15-min window -> 840s remaining.
        self.assertTrue(any("skip:cooldown remaining=%ds"
                            % (goal.GOAL_LANE_INTERVAL_S - 60) in ln
                            for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_undersaturated_small_backlog_logs_skip_backlog_small(self):
        # The under-saturated small-backlog skip carries the `skip:backlog-small`
        # decision name (item 3) alongside its existing wording.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=2):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now,
                                          tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("skip:backlog-small" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_busy_undersaturated_stash_abort_still_reaches_giveup(self):
        # #442-review F1: the stash-abort give-up (requirement 2 -- must stay
        # for BOTH branches) was made structurally UNREACHABLE on a busy
        # under-saturated session by the session-active reset. A busy session
        # always has idle < GOAL_LANE_IDLE_S, so the reset zeroed `lna` every
        # sweep before the streak could reach the cap. A permanently-aborting
        # lane (parked draft occupying the stash slot) on a busy box must still
        # accumulate the streak across sweeps and fire the ONE give-up ping.
        now = 100000
        tmtime = now - 30  # busy: fresh transcript, idle << 15min
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        rec = {}
        state = {}
        sent = []
        all_logs = []
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "_count_live_subagents", return_value=2), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192), \
             m.patch.object(wd, "deliver_with_stash", return_value=False):
            for _ in range(goal.GOAL_LANE_MAX_STASH_ABORTS + 2):
                tmux = DeliverGoalFakeTmux(
                    [("%9", "claude", self.CWD, "111")], GOAL_ARMED_DRAFT_CAP)
                logs, owns = goal.goal_lane_occupancy_nudge(
                    now, tmux, rec, self.SID, self.CWD, "111",
                    GOAL_ARMED_DRAFT_CAP, tpath, tmtime, "loc",
                    lambda msg, **k: sent.append(msg), False, None, proj,
                    backlog_fetch=lambda cwd: 32, state=state,
                    sleep_fn=lambda s: None)
                all_logs += logs
        self.assertEqual(len(sent), 1, "give-up ping must fire exactly once")
        self.assertTrue(any("GAVE UP after" in ln and "stash abort" in ln
                            for ln in all_logs), all_logs)

    def test_zero_worker_active_rearms_giveup_counters(self):
        # #442-review F1: the session-active give-up re-arm (clear ln/lna/
        # lpinged) is PRESERVED for the 0-worker branch -- an active empty-lane
        # box that had given up must reset so it re-arms once it goes quiet.
        now = 100000
        tmtime = now - 30  # active
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lna": 3, "lpinged": True}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now,
                                      tmtime, rec=rec)
        self.assertFalse(owns)
        self.assertEqual(rec.get("ln"), 0)
        self.assertEqual(rec.get("lna"), 0)
        self.assertFalse(rec.get("lpinged"))
        self.assertTrue(any("skip:idle" in ln for ln in logs), logs)

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
        # On any managed Linux box this reads a real positive MB value that is
        # genuinely in MEGABYTES -- i.e. below the box's own MemTotal expressed
        # in MB. A mutant dropping the kB->MB `// 1024` returns a raw-kB value
        # (~thousands of times MemTotal-in-MB), which fails the `< total_mb`
        # bound below (#442-review M1: the conversion must have teeth).
        val = goal._mem_available_mb()
        self.assertIsInstance(val, int)
        self.assertGreater(val, 0)
        total_kb = None
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    break
        self.assertIsNotNone(total_kb, "/proc/meminfo has no MemTotal line")
        self.assertLess(val, total_kb // 1024)

    def test_mem_available_mb_converts_kb_to_mb(self):
        # #442-review M1: deterministic teeth for the kB->MB `// 1024` step,
        # independent of the box's real memory. MemAvailable 8388608 kB is
        # exactly 8192 MB; a mutant returning raw kB would report 8388608.
        meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemFree:          512000 kB\n"
            "MemAvailable:    8388608 kB\n"
            "Buffers:          128000 kB\n"
        )
        with m.patch("builtins.open", m.mock_open(read_data=meminfo)):
            val = goal._mem_available_mb()
        self.assertEqual(val, 8192)

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

