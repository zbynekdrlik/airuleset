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
from watchdog.transcripts import WorkerLane   # #571 -- structured evidence lanes

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


def _rearm_ok(cwd):
    """#478: a resolvable /goal template for a full-authority pane -- lets the
    dark-watch auto-re-arm fire (module fn, not an assigned lambda: E731)."""
    return ("/goal x", "full")


def _rearm_none(cwd):
    """#478: an UNRESOLVABLE template -> forces the dark-watch ping FALLBACK
    (the only path that still pings a workable backlog)."""
    return (None, "full")


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
# 5. goal_dark_watch — job 20 half 1: records a re-arm ONLY on a #524 CONFIRMED
#    death (else pings / stays silent), itself types nothing (the keystroke is
#    job 9's), silent on cleared/no-marker, shared janitor recovery runs first.
# --------------------------------------------------------------------------- #

class TestGoalDarkWatch(unittest.TestCase):
    CWD = "/home/newlevel/devel/darkwatch"

    def setUp(self):
        # #478: dark-watch may now RECORD a goal-arm request (auto-re-arm),
        # so isolate the goal-requests/-sync files from the live systemd
        # watchdog exactly like the sibling goal-delivery tests -- a test
        # process must never race the real ~/.claude/goal-requests.json.
        self.reqp, self.syncp = _isolate_goal_state(self)

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

    def _sweep(self, tmux, proj, state, sent, now, obl, rearm=None, reqs=None):
        # `rearm`/`reqs` are only forwarded when set, so a caller that omits
        # them exercises the production defaults path unchanged (#478).
        kw = {}
        if rearm is not None:
            kw["rearm_fn"] = rearm
        if reqs is not None:
            kw["requests_path"] = reqs
        goal.goal_dark_watch(now, run=tmux,
                             send_fn=lambda m, **k: sent.append(m),
                             projects_dir=proj, state=state,
                             sleep_fn=lambda s: None, obligation_fn=obl, **kw)

    @staticmethod
    def _obl(open_n, ts):
        """A fake obligation_fn returning a fixed (open, ts) for any cwd."""
        return lambda cwd: (open_n, ts)

    def test_524_idle_alive_flicker_is_never_typed(self):
        # #524 RED: montalu 2026-08-16 — a 75-min idle-but-ALIVE session whose
        # footer glyph flickered (dark reads, then back to ARMED) was auto-typed
        # /goal after a 1-sweep debounce. HARDENED (owner decision B): the TYPE
        # now requires K clean-dark reads over >=10 min, and ANY armed read
        # VETOES the run. Two dark sweeps (montalu's 22:31/22:32, ~71s apart)
        # must NOT type; the glyph flickering back to armed proves it alive.
        proj = self._dir()
        sid = "sess-524-idle-alive"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        idle = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)       # pane_goal_armed -> False
        armed = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                    GOAL_ARMED_CAP)     # pane_goal_armed -> True
        obl = self._obl(53, 100000)                     # workable, fresh
        reqs = self._dir() / "goal-requests.json"

        def rearm(cwd):
            return ("/goal DONE or stop after 50", "branch-merge")

        state, sent = {}, []
        self._sweep(idle, proj, state, sent, 100000, obl, rearm, reqs)   # 1st obs
        self._sweep(idle, proj, state, sent, 100071, obl, rearm, reqs)   # 2nd sweep
        self.assertEqual(
            goal.load_goal_requests(reqs), {},
            "2 dark sweeps must NOT type — the montalu 1-sweep-debounce bug")
        # The glyph flickers back to ARMED (montalu 22:34) -> VETO-ALIVE, the
        # confirmation run is reset; the True read proves the loop alive.
        goal.goal_dark_watch(100142, run=armed,
                             send_fn=lambda mm, **k: sent.append(mm),
                             projects_dir=proj, state=state,
                             sleep_fn=lambda s: None, obligation_fn=obl,
                             rearm_fn=rearm, requests_path=reqs)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "an idle-alive flicker is NEVER auto-typed")
        self.assertEqual(idle.sent, [], "zero keystrokes (idle capture)")
        self.assertEqual(armed.sent, [], "zero keystrokes (armed capture)")
        # A WORKABLE dark loop self-heals via the confirmed auto-type, so it
        # accumulates SILENTLY -- montalu got neither a type NOR a spurious
        # "loop died" ping (owner decision B: idle-but-alive is left alone).
        self.assertEqual(sent, [], "no spurious ping for a workable idle-alive flicker")

    def test_mtime_advance_vetoes_the_confirmation_run(self):
        # #524 -- an advancing transcript mtime is a structured LIVENESS proof
        # (the session wrote a turn). Even when the footer reads dark, a run
        # that sees mtime advance is VETOED (logged VETO-ALIVE:mtime-advanced),
        # reset, and NEVER typed -- a genuine dead loop keeps a FROZEN mtime.
        import os
        proj, tmux = self._dark("sess-mtime-veto")
        tpath = next(proj.rglob("sess-mtime-veto.jsonl"))
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(5, 900)
        rearm = _rearm_ok

        def sweep(now, mtime):
            os.utime(tpath, (mtime, mtime))
            return goal.goal_dark_watch(
                now, run=tmux, send_fn=lambda mm, **k: sent.append(mm),
                projects_dir=proj, state=state, sleep_fn=lambda s: None,
                obligation_fn=obl, rearm_fn=rearm, requests_path=reqs)

        sweep(1000, 500)           # debounce (first observation), mtime frozen
        sweep(1100, 500)           # clean_run=1
        sweep(1200, 500)           # clean_run=2
        logs = sweep(1300, 550)    # mtime ADVANCED -> VETO-ALIVE, run reset
        self.assertTrue(any("VETO-ALIVE:mtime-advanced" in ln for ln in logs), logs)
        self.assertNotIn("sess-mtime-veto", state.get("goal_dark_confirm", {}),
                         "the liveness veto resets the confirmation run")
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "an alive (mtime-advancing) loop is NEVER auto-typed")
        self.assertEqual(tmux.sent, [], "zero keystrokes")

    def _flicker_run(self, sid, flicker_cap, n=24):
        # A dark loop whose footer reads `flicker_cap` (armed True / busy None)
        # on every 4th sweep and GOAL_IDLE_CAP (dark) otherwise -- the run never
        # gets MIN_READS consecutive clean-dark reads, so it must NEVER type.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        idle = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        flick = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], flicker_cap)
        obl = self._obl(5, 900)
        reqs = self._dir() / "goal-requests.json"
        state, sent, now = {}, [], 1000
        for i in range(n):
            now += 100
            self._sweep(flick if (i % 4 == 3) else idle, proj, state, sent, now,
                        obl, _rearm_ok, reqs)
        return reqs, idle, flick, sent

    def test_armed_flicker_during_accumulation_never_types(self):
        # #524-review (B, mutation-locked): the armed-True VETO is the single
        # most safety-critical check -- a glyph flickering back armed = the loop
        # is ALIVE (the exact montalu incident). A dark loop that shows the glyph
        # before its confirmation run completes must NEVER be auto-typed, because
        # each armed read RESETS the run. TEETH: removing `confirm_state.pop` on
        # the armed-True branch makes this type (proven RED in review).
        reqs, idle, armed, sent = self._flicker_run("sess-armed-flicker",
                                                    GOAL_ARMED_CAP)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a periodically-armed (alive) loop is NEVER auto-typed")
        self.assertEqual(idle.sent, [], "zero keystrokes")
        self.assertEqual(armed.sent, [], "zero keystrokes")

    def test_none_read_breaks_the_consecutive_confirmation_run(self):
        # #524-review (B, mutation-locked): an undeterminable footer (busy /
        # chrome / dialog -> pane_goal_armed None) is NOT a clean-dark read, so
        # it must BREAK the consecutive confirmation run. A dark loop that reads
        # None before confirmation is never typed. TEETH: removing the
        # clean_run reset in the None branch makes this type.
        reqs, idle, busy, sent = self._flicker_run("sess-none-reset",
                                                   GOAL_BUSY_CAP)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a periodically-undeterminable loop never reaches a run")
        self.assertEqual(idle.sent, [], "zero keystrokes")

    def test_stale_attempt_cap_entries_are_reaped(self):
        # #524-review -- the attempt-cap store (goal_dark_rearm_attempts) must
        # not leak: a sid whose newest auto-type ts is older than the 24h cap
        # window, or an empty/malformed list, is reaped on the next sweep; a
        # fresh entry SURVIVES so the rolling cap is preserved (a reaper, never
        # a pop-on-episode-end). TEETH: removing the reaper leaks the stale sids.
        proj, tmux = self._dark("sess-live")
        obl = self._obl(5, 900)
        reqs = self._dir() / "goal-requests.json"
        now = 1_000_000
        state = {"goal_dark_rearm_attempts": {
            "gone-stale": [now - 25 * 3600],   # older than 24h -> reap
            "gone-empty": [],                  # empty -> reap
            "gone-bad": "corrupt",             # malformed -> reap, never raise
            "still-live": [now - 60],          # fresh -> keep
        }}
        goal.goal_dark_watch(now, run=tmux, send_fn=lambda m, **k: None,
                             projects_dir=proj, state=state,
                             sleep_fn=lambda s: None, obligation_fn=obl,
                             rearm_fn=_rearm_ok, requests_path=reqs)
        att = state["goal_dark_rearm_attempts"]
        self.assertNotIn("gone-stale", att, "a >24h attempt entry is reaped")
        self.assertNotIn("gone-empty", att, "an empty attempt entry is reaped")
        self.assertNotIn("gone-bad", att, "a malformed attempt entry is reaped")
        self.assertIn("still-live", att, "a fresh attempt entry survives")

    def test_persistently_dark_goal_with_work_remaining_is_re_armed(self):
        # #478 (reverses #403 for THIS dark-died branch): a still-dark goal
        # whose cwd still has genuinely WORKABLE obligations must be AUTO-
        # RE-ARMED -- a goal-arm request RECORDED for job 9 to deliver via
        # the verified keystroke path -- NOT merely pinged. RED against the
        # #459 ping-only behaviour this deliberately replaces. dark-watch
        # STILL types nothing (it only WRITES the request; the keystroke and
        # its recent-human gate live in deliver_goal).
        # #524 UPDATE: the OLD assertion (re-arm on the SECOND sweep) is exactly
        # the 1-sweep-debounce behaviour owner decision B overturns. A workable
        # dark loop is now RE-ARMED only after a CONFIRMED death run (K clean
        # reads over >= MIN_SPAN), and stays SILENT (no #459 ping) while
        # accumulating -- it self-heals via the auto-type. mtime stays frozen
        # (the _dark fixture writes the transcript once), so no liveness veto.
        proj, tmux = self._dark("sess-dark-rearm")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(5, 900)          # workable, fresh (cts < the 1000 start)

        def rearm(cwd):
            return ("/goal DONE or stop after 50", "full")

        now = 1000
        self._sweep(tmux, proj, state, sent, now, obl, rearm, reqs)     # debounce
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "no re-arm on the first (debounce) observation")
        # A short clean-dark run (2 sweeps -- the OLD trigger) must NOT type.
        for _ in range(2):
            now += 100
            self._sweep(tmux, proj, state, sent, now, obl, rearm, reqs)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "2 clean-dark sweeps must NOT type (confirmation not reached)")
        # Keep accumulating clean-dark reads until the run is CONFIRMED
        # (>= GOAL_DARK_CONFIRM_MIN_READS reads AND >= MIN_SPAN span).
        while not goal.load_goal_requests(reqs):
            now += 100
            self._sweep(tmux, proj, state, sent, now, obl, rearm, reqs)
            self.assertLess(now, 100000, "must confirm within a bounded run")
        self.assertEqual(sent, [], "a workable dark goal accumulates SILENTLY, never pinged")
        reqs_d = goal.load_goal_requests(reqs)
        self.assertIn("sess-dark-rearm", reqs_d, "re-arm request recorded once CONFIRMED")
        entry = reqs_d["sess-dark-rearm"]
        self.assertEqual(entry["origin"], "dark-rearm")
        self.assertEqual(entry["text"], "/goal DONE or stop after 50")
        self.assertEqual(entry["authority"], "full")
        self.assertEqual(tmux.sent, [], "dark-watch never types -- only records")

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
        # #478: a workable backlog now RE-ARMS instead of pinging. This test
        # still locks the #403/#459 ping TEXT + dedup-key, reached via the
        # #478 ping FALLBACK: workable but the /goal template cannot be
        # resolved (rearm_fn -> None) -> can't auto-fix -> ping the user
        # exactly as #459 did (first "zomrelo", then "STÁLE" re-ping).
        reqs = self._dir() / "goal-requests.json"

        def send_fn(m, **k):
            rec.append((m, k.get("dedup_key")))

        def sweep(now):
            goal.goal_dark_watch(now, run=tmux, send_fn=send_fn,
                                 projects_dir=proj, state=state,
                                 sleep_fn=lambda s: None, obligation_fn=obl,
                                 rearm_fn=lambda cwd: (None, "full"),
                                 requests_path=reqs)

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
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "the template-unresolved ping fallback records no re-arm")

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
        # #478: routed through the template-unresolved ping FALLBACK
        # (rearm_fn -> None) so the fresh-cache first ping fires as #459 (a
        # fresh workable cache would otherwise RE-ARM); the stale third sweep
        # then neither re-pings nor re-arms — the same freshness gate guards
        # both actions.
        proj, tmux = self._dark("sess-dark-stale")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        rearm = _rearm_none
        base = 1_700_000_000
        self._sweep(tmux, proj, state, sent, base, lambda c: (5, base - 10),
                    rearm, reqs)
        self._sweep(tmux, proj, state, sent, base + 1000, lambda c: (5, base - 10),
                    rearm, reqs)
        self.assertEqual(len(sent), 1)
        t = base + 1000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10
        stale_ts = t - goal.GOAL_DARK_CACHE_MAX_AGE_S - 100
        self._sweep(tmux, proj, state, sent, t, lambda c: (5, stale_ts),
                    rearm, reqs)
        self.assertEqual(len(sent), 1, "a stale cache must not re-ping")
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a stale/unresolved-template path records no re-arm")

    def test_re_pinging_is_hard_capped_per_episode(self):
        # #459 — with a cache that stays FRESH and non-empty forever (a
        # stalled session that keeps re-rendering its statusline), the ONLY
        # thing that can bound the ping count is the hard cap. (A FROZEN
        # cache instead ages out at GOAL_DARK_CACHE_MAX_AGE_S — the stale
        # test above — so the freshness gate is the practical bound for a
        # genuinely dead loop; the cap backstops the fresh-cache case.)
        proj, tmux = self._dark("sess-dark-cap")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        # #478: stay in the ping FALLBACK (workable but template unresolved),
        # the only path that still re-pings a workable backlog -- so the cap
        # is exercised exactly as #459 intended.
        rearm = _rearm_none
        now = [1_700_000_000]

        def obl(cwd):
            return (5, now[0] - 60)          # always fresh (60s before now)

        self._sweep(tmux, proj, state, sent, now[0], obl, rearm, reqs)  # first obs
        for _ in range(goal.GOAL_DARK_REPING_MAX + 8):
            now[0] += 30 * 3600                              # > final stage (24h)
            self._sweep(tmux, proj, state, sent, now[0], obl, rearm, reqs)
        self.assertEqual(len(sent), goal.GOAL_DARK_REPING_MAX,
                         "re-pings must be hard-capped per episode")

    # ----------------------------------------------------------------- #
    # #478 — AUTO-RE-ARM the dark-DIED branch (reverses #403), gated on a
    # genuinely WORKABLE obligation cache. The safeguard is the SAME #459
    # cache gate (open>0 AND fresh) — which by construction already excludes
    # an empty backlog, a user-waiting-only backlog, and skip-only tickets.
    # ----------------------------------------------------------------- #

    def test_user_cleared_goal_is_never_re_armed(self):
        # A user-CLEARED goal (newest marker `cleared`, not `set`) is skipped
        # at the mark-gate BEFORE the dark-died branch — so even with a fully
        # workable cache and a resolvable template it is NEVER re-armed. The
        # deliberate user clear (#403) is respected exactly as before; #478
        # revives ONLY the death-by-outage branch.
        proj = self._dir()
        sid = "sess-cleared-norearm"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal cleared: /goal x",
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(5, 1500)                        # workable
        rearm = _rearm_ok                               # template resolves
        for now in (1000, 2000, 2000 + goal.GOAL_DARK_REPING_SCHEDULE_S[0] + 10):
            self._sweep(tmux, proj, state, sent, now, obl, rearm, reqs)
        self.assertEqual(sent, [])
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a user-cleared goal must NEVER be auto-re-armed")

    def test_empty_backlog_is_pinged_not_re_armed(self):
        # open==0 (a genuinely-achieved / empty backlog) is NOT workable ->
        # no re-arm; the #459 first ping still fires once, then silence.
        proj, tmux = self._dark("sess-empty-norearm")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(0, 1500)                        # achieved/empty
        rearm = _rearm_ok
        self._sweep(tmux, proj, state, sent, 1000, obl, rearm, reqs)
        self._sweep(tmux, proj, state, sent, 2000, obl, rearm, reqs)
        self.assertEqual(len(sent), 1, "empty backlog gets the #459 first ping")
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "an empty/achieved backlog must never be re-armed")

    def test_user_waiting_only_backlog_is_never_re_armed(self):
        # A backlog that is ALL user-waiting (needs-answer/needs-decision)
        # surfaces as open==0 in the cache: statusbar's `open` is the WORKABLE
        # count (len(workable_rows) - gk via airuleset._partition_user_waiting),
        # so user-waiting tickets are excluded by construction. open==0 ->
        # not workable -> no re-arm (locks the "not user-waiting" half of the
        # mandated safeguard at the dark-watch layer; the cache-contract half
        # is test_statusbar.ObligationCountSafeguard).
        proj, tmux = self._dark("sess-uwait-norearm")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(0, 1500)       # workable open==0 (all remaining U-bucket)
        rearm = _rearm_ok
        self._sweep(tmux, proj, state, sent, 1000, obl, rearm, reqs)
        self._sweep(tmux, proj, state, sent, 2000, obl, rearm, reqs)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a user-waiting-only backlog must never be re-armed")

    def test_stale_or_missing_cache_is_never_re_armed(self):
        # A stale (older than GOAL_DARK_CACHE_MAX_AGE_S) or absent obligation
        # cache is NOT trusted -> never re-arm (fail toward no keystroke).
        base = 1_700_000_000
        for label, obl in (
            ("stale", self._obl(5, base - goal.GOAL_DARK_CACHE_MAX_AGE_S - 100)),
            ("missing", self._obl(None, None)),
        ):
            with self.subTest(cache=label):
                proj, tmux = self._dark("sess-%s-norearm" % label)
                sent, state = [], {}
                reqs = self._dir() / "goal-requests.json"
                rearm = _rearm_ok
                self._sweep(tmux, proj, state, sent, base, obl, rearm, reqs)
                self._sweep(tmux, proj, state, sent, base + 2000, obl, rearm, reqs)
                self.assertEqual(goal.load_goal_requests(reqs), {},
                                 "%s cache must never re-arm" % label)

    def test_re_arm_is_hard_capped_per_day_then_pings(self):
        # #524 UPDATE: the OLD model (re-arm ATTEMPTS back off on the #459 ping
        # schedule, hard-capped at GOAL_DARK_REPING_MAX per episode) is replaced
        # by a per-sid 24h ATTEMPT CAP. A session that keeps confirming dead
        # (delivery, not modelled here, never sticks) is auto-typed at most
        # GOAL_DARK_REARM_MAX_PER_DAY times per rolling 24h; a further CONFIRMED
        # cycle PINGS instead (fail toward the human). Each confirmed run resets
        # the confirmation window, so the cap -- not a ping schedule -- bounds
        # the total. Counted via a record_goal_request spy (it overwrites the
        # SAME sid each time). mtime stays frozen -> no liveness veto.
        proj, tmux = self._dark("sess-dark-rearm-cap")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        rearm = _rearm_ok
        writes = []
        real_record = goal.record_goal_request

        def spy(*a, **k):
            writes.append(1)
            return real_record(*a, **k)

        now = [1_700_000_000]

        def obl(cwd):
            return (5, now[0] - 60)                     # always workable + fresh

        with m.patch.object(goal, "record_goal_request", side_effect=spy):
            # Many clean-dark sweeps within ONE 24h window (100s apart, ~1.7h
            # total): several confirmed cycles, but only the first
            # GOAL_DARK_REARM_MAX_PER_DAY of them TYPE; the rest ping.
            for _ in range(80):
                now[0] += 100
                self._sweep(tmux, proj, state, sent, now[0], obl, rearm, reqs)
        self.assertEqual(len(writes), goal.GOAL_DARK_REARM_MAX_PER_DAY,
                         "auto-types are hard-capped per sid per rolling 24h")
        self.assertTrue(sent,
                        "a CONFIRMED dark loop past the attempt cap PINGS the human")

    def test_dry_run_re_arm_records_nothing_and_consumes_no_slot(self):
        # #478/#524 review MINOR — a dry-run sweep must never WRITE the request
        # nor CONSUME an attempt slot, and must log "would record", never the
        # real "recording re-arm". #524: the re-arm decision now requires a
        # CONFIRMED death run, so drive enough clean-dark dry-run sweeps to
        # confirm (the confirm window is tracking state, advanced in dry-run
        # exactly like seen_state/off_state already are).
        proj, tmux = self._dark("sess-dry-rearm")
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(5, 900)          # workable, fresh (cts < the 1000 start)
        state = {}

        def sweep(now):
            return goal.goal_dark_watch(
                now, run=tmux, send_fn=lambda m, **k: None, projects_dir=proj,
                state=state, sleep_fn=lambda s: None, obligation_fn=obl,
                rearm_fn=_rearm_ok, requests_path=reqs, dry_run=True)

        now, logs = 1000, []
        while not any("would record" in ln for ln in logs):
            now += 100
            logs = sweep(now)
            self.assertLess(now, 100000, "must reach the would-record branch")
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a dry-run sweep must record nothing")
        self.assertTrue(any("CONFIRMED-DEAD would record" in ln for ln in logs), logs)
        self.assertFalse(any("recording re-arm" in ln for ln in logs), logs)
        self.assertFalse(
            state.get("goal_dark_rearm_attempts", {}).get("sess-dry-rearm"),
            "a dry-run sweep must not consume an attempt slot")
        self.assertNotIn("sess-dry-rearm", state.get("goal_dark_pinged", {}),
                         "a dry-run confirmed sweep pings nothing")

    def test_default_rearm_fn_fails_toward_ping_on_resolve_error(self):
        # #478 review MINOR — an UNEXPECTED resolve_authority failure returns
        # NO template (ping fallback), never escalates authority to "full"
        # (which would type the merge-to-main template into a reduced-
        # authority stream box).
        import airuleset
        with m.patch.object(airuleset, "resolve_authority",
                            side_effect=RuntimeError("boom")):
            text, auth = goal._default_rearm_fn("/some/cwd")
        self.assertIsNone(text)
        self.assertIsNone(auth)

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

    # ----------------------------------------------------------------- #
    # #519 -- orphan-prune for state["goal_mark"] (off_state). G6 made
    # goal_mark load-bearing (resolve_goal_armed / the lane gate read it), so a
    # gone session's entry must not leak forever. Reaped ONLY when the sid is
    # NOT a live candidate pane this sweep AND its stored tmtime is aged; a live
    # pane's entry is NEVER reaped (even with a stale transcript mtime -- the
    # silently-dead-loop case dark_watch is still confirming, whose tail-proof
    # persisted mark must survive).
    # ----------------------------------------------------------------- #

    def test_519_orphan_goal_mark_entry_is_reaped(self):
        # A goal_mark entry for a session with NO live candidate pane (gone /
        # superseded by a newer transcript) and an OLD stored tmtime is an
        # orphan -> reaped. A live session's entry, visited this sweep, is kept.
        proj = self._dir()
        live = "sess-519-live"
        _write_marker_transcript(proj, self.CWD, live)
        _write_goal_marker(proj, self.CWD, live, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 100000
        state = {"goal_mark": {
            "orphan-519": {"off": 0, "mark": {"state": "set", "ts": 500},
                           "tmtime": now - 3 * 24 * 3600}}}   # 3 days old, gone
        goal.goal_dark_watch(now, run=tmux, send_fn=lambda mm, **k: None,
                             projects_dir=proj, state=state, sleep_fn=lambda s: None)
        gm = state["goal_mark"]
        self.assertNotIn("orphan-519", gm,
                         "an aged, gone-session orphan must be reaped")
        self.assertIn(live, gm, "a live session's entry must be kept")

    def test_519_live_entry_never_reaped_even_with_stale_tmtime(self):
        # The visited-this-sweep gate is PRIMARY: a live candidate pane whose
        # transcript mtime is OLD (a silently-dead loop dark_watch is confirming)
        # must NEVER be reaped -- an age-only reaper would lose its tail-proof
        # persisted mark. Mutation-lock for the visited gate: an age-only prune
        # would reap this (now - tmtime = 90000 > 24h).
        proj = self._dir()
        live = "sess-519-stale-live"
        p = _write_marker_transcript(proj, self.CWD, live)
        _write_goal_marker(proj, self.CWD, live, "Goal set: /goal x", ts_epoch=500)
        os.utime(p, (10000, 10000))    # transcript mtime far older than the TTL
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 100000
        state = {}
        goal.goal_dark_watch(now, run=tmux, send_fn=lambda mm, **k: None,
                             projects_dir=proj, state=state, sleep_fn=lambda s: None)
        self.assertIn(live, state["goal_mark"],
                      "a visited live pane is never reaped, even stale-tmtime")

    def test_519_recent_orphan_kept_by_age_gate(self):
        # The tmtime age gate is the secondary safety for a budget-DEFERRED live
        # pane: a not-visited entry with a RECENT tmtime is KEPT (only an AGED
        # orphan is reaped). Mutation-lock for the age gate: a visited-only prune
        # with no age check would reap this recent, not-visited entry.
        proj = self._dir()
        live = "sess-519-live2"
        _write_marker_transcript(proj, self.CWD, live)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        now = 100000
        state = {"goal_mark": {
            "recent-orphan-519": {"off": 0, "mark": {"state": "set", "ts": 500},
                                  "tmtime": now - 60}}}       # just 60s old
        goal.goal_dark_watch(now, run=tmux, send_fn=lambda mm, **k: None,
                             projects_dir=proj, state=state, sleep_fn=lambda s: None)
        self.assertIn("recent-orphan-519", state["goal_mark"],
                      "a not-visited but RECENT entry is kept by the age gate")

    # ----------------------------------------------------------------- #
    # #517 -- first-sight tail-limit: a state-loss / >tail-downtime first
    # sight of a session whose `Goal set:` marker sits BEYOND the 4 MB tail
    # must still seed goal_mark = armed (the bounded reverse-scan seed), or
    # the lane gate reads not-armed and silences a genuinely-armed loop.
    # ----------------------------------------------------------------- #

    def test_517_first_sight_seeds_a_goal_set_marker_beyond_the_tail(self):
        # RED today: dark_watch first-sights (empty state) a transcript whose
        # only `Goal set:` marker is > GOAL_MARK_TAIL_BYTES back. The tail
        # bootstrap misses it -> goal_mark seeds NOT armed. The reverse-scan
        # seed must find it -> mark.state == "set". A genuine >4 MB transcript
        # is used so the real def-time 4 MB tail (unpatchable) actually misses.
        proj = self._dir()
        sid = "sess-517-deep"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tpath = proj / _encode(self.CWD) / (sid + ".jsonl")
        with open(tpath, "a", encoding="utf-8") as f:
            # ~4.2 MB of non-marker filler AFTER the marker -> the marker is
            # beyond the 4 MB tail. Raw lines (not JSON) are fine: the byte
            # pre-filter skips them without a json.loads.
            f.write(("padding-line " + "x" * 180 + "\n") * 22000)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        state = {}
        goal.goal_dark_watch(100000, run=tmux, send_fn=lambda mm, **k: None,
                             projects_dir=proj, state=state, sleep_fn=lambda s: None)
        entry = state.get("goal_mark", {}).get(sid)
        self.assertIsNotNone(entry, state)
        mark = entry.get("mark") if isinstance(entry, dict) else None
        self.assertTrue(isinstance(mark, dict) and mark.get("state") == "set",
                        "first-sight must seed the deep Goal set: marker "
                        "(reverse-scan), got mark=%r" % (mark,))


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

    def test_not_armed_is_skipped_by_render_but_journals_one_glance(self):
        # GOAL_IDLE_CAP -> pane_goal_armed reads DETERMINABLY False (a plain,
        # readable footer with no ◎ /goal). #486 G3 OVERTURNS the old #475 "no
        # decision line for a not-armed candidate" invariant: the render path
        # still takes no ACTION (no nudge), but the STRUCTURED one-glance line is
        # now journalled for EVERY candidate pane -- exactly so the #486 case
        # (footer reads not-armed while a /goal is genuinely armed) can never be
        # a SILENT skip again. With no heartbeat here, the structured verdict is
        # honestly `no-heartbeat` (render agrees, no structured armed signal), so
        # this pane produces ONE one-glance line and NO nudge / keystroke.
        proj = self._dir()
        sid = "sess-lane-1"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_lane_sweep(1000, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 5)
        self.assertTrue(any(ln.startswith("one-glance ") for ln in logs), logs)
        self.assertTrue(any("-> no-heartbeat (" in ln for ln in logs), logs)
        # render path took NO action: no lane-occupancy nudge, no keystroke.
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_obscured_footer_no_longer_produces_an_armed_undeterminable_skip(self):
        # #486 G6: the render armed action gate -- and its `skip:armed-
        # undeterminable` branch -- is DELETED. An obscured footer
        # (pane_goal_armed -> None) no longer decides anything; the STRUCTURED
        # gate (goal_mark first, heartbeat fallback) does. With NO heartbeat and
        # NO goal_mark here, the structured verdict is honestly `no-heartbeat`
        # (journalled, never silent), the gate skips, and NO
        # `skip:armed-undeterminable` line is ever emitted again.
        proj = self._dir()
        sid = "sess-lane-undet"
        _write_marker_transcript(proj, self.CWD, sid)
        self.assertIsNone(wd.pane_goal_armed(GOAL_BUSY_CAP))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_BUSY_CAP)
        logs = goal.goal_lane_sweep(1000, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 5)
        self.assertFalse(any("armed-undeterminable" in ln for ln in logs), logs)
        self.assertTrue(any(ln.startswith("one-glance ") and "-> no-heartbeat" in ln
                            for ln in logs), logs)
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
             authority="full", handled=None, enters_swallowed=0):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        # #490 — the fake now models the submit end to end: a delivered nudge
        # appends the real `user` turn `send_verified` verifies against; a
        # swallowed one (`enters_swallowed` > 0) writes nothing and keeps the
        # box text, so the transcript-proof restore path can be driven.
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], captured,
                                   model_type=True, transcript_path=tpath,
                                   enters_swallowed=enters_swallowed)
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

    def test_swallowed_submit_is_not_booked_as_delivered(self):
        # #490 RED — a swallowed Enter (the box KEEPS the typed text and NO
        # `user` turn appears in the transcript) must NOT be recorded as a
        # delivered nudge, and the foreign text must be restored off the
        # user's input box. The bare-box branch used a raw `send_continue`
        # (type + Enter, no post-send read), so the live lane-fill nudge
        # booked the nudge and left its own text hanging in the prompt until
        # the user found it (2026-08-15 regression).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec, enters_swallowed=99)
        # never logged as a DELIVERED nudge (the transcript never confirmed it)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        # the delivery is journalled as unverified, retryable, not silent
        self.assertTrue(any("submit-unverified" in ln for ln in logs), logs)
        # the nudge budget is NOT consumed (a refused attempt is not a nudge)
        self.assertNotIn("ln", rec)
        # the foreign text is restored — never left in the user's input box
        self.assertEqual(tmux.box, "", tmux.sent)

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
        # #475: the ❓ early return used to be silent -> now journals a decision.
        self.assertTrue(any("skip:awaiting-user" in ln for ln in logs), logs)

    # #475 -- every previously-silent early-return path of the guard now logs a
    # `lane-occupancy <loc> -> skip:<reason>` decision (the #442c every-sweep
    # logging contract), or is a documented deliberately-silent structural N/A.
    def test_blocking_dialog_logs_skip_not_silent(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "pane_waiting_on_user", return_value=True):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:blocking-dialog" in ln for ln in logs), logs)

    def test_compacting_logs_skip_not_silent(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_pane_compacting", return_value=True):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:compacting" in ln for ln in logs), logs)

    def test_already_handled_logs_skip_not_silent(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      handled={self.SID})
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:already-handled" in ln for ln in logs), logs)

    def test_working_marker_no_tasks_logs_skip_not_silent(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:working-no-tasks" in ln for ln in logs), logs)

    def test_571_working_marker_with_structured_live_lane_does_not_defer(self):
        # #571 LOCK (a) at the goal.py level: a ⏳ marker with 0 RENDER task
        # badges but a STRUCTURED live lane (count_live_workers -- e.g. a worker
        # mid-long-tool-call, render-invisible but disk-live) must NOT
        # skip:working-no-tasks -- it PROCEEDS to the fill/saturation check. RED
        # against the pre-#571 render-badge read, which deferred on waiters<=0
        # regardless of the disk-live lane (the count call sat BELOW the branch,
        # so the patched evidence was never consulted) -- gk 16 issues / 2 lanes.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        live_ev = [WorkerLane("w1", "live", 100.0, None, "")]
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(1, live_ev)), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(any("skip:working-no-tasks" in ln for ln in logs), logs)
        # positive proof it proceeded past working-no-tasks into the fill logic
        # (1 live lane, backlog 5 -> under-saturated surplus-floor is the next
        # decision the fill path reaches).
        self.assertTrue(any("surplus-floor" in ln or "saturated" in ln
                            or "lane-occupancy nudge" in ln for ln in logs), logs)

    def test_571_genuinely_zero_structured_lanes_still_defers(self):
        # #571 LOCK (b) at the goal.py level: a ⏳ marker, 0 render badges, AND 0
        # non-stale structured lanes -> the defer is preserved (first sweep of
        # the episode), so a genuinely-idle ⏳ pane is not nudged prematurely.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:working-no-tasks" in ln for ln in logs), logs)

    def test_571_low_mem_capacity_capped_surfaces_once(self):
        # #571 LOCK (d) at the goal.py level: after M consecutive low-mem skips
        # with a genuine backlog, the ONE deduped CAPACITY-CAPPED owner-decision
        # line fires; the OOM skip:low-mem line is preserved every sweep and NO
        # nudge is typed (OOM protection unchanged).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        live_ev = [WorkerLane("w1", "live", 100.0, None, ""),
                   WorkerLane("w2", "live", 100.0, None, "")]
        rec = {"lms": goal.GOAL_LANE_LOWMEM_SURFACE_STREAK - 1}
        with m.patch.object(wd, "count_live_workers", return_value=(2, live_ev)), \
             m.patch.object(goal, "_mem_available_mb", return_value=812):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 12, now,
                                          tmtime, rec=rec)
        self.assertTrue(any("skip:low-mem" in ln for ln in logs), logs)
        self.assertTrue(any("CAPACITY-CAPPED" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])       # OOM protection preserved
        # dedup -- a later sweep in the SAME episode does NOT re-surface (rec
        # carries lms/lmsurf); the skip:low-mem line still fires every sweep.
        with m.patch.object(wd, "count_live_workers", return_value=(2, live_ev)), \
             m.patch.object(goal, "_mem_available_mb", return_value=812):
            logs2, _o2, _t2 = self._call(GOAL_ARMED_CAP, lambda cwd: 12, now,
                                         tmtime, rec=rec)
        self.assertTrue(any("skip:low-mem" in ln for ln in logs2), logs2)
        self.assertFalse(any("CAPACITY-CAPPED" in ln for ln in logs2), logs2)

    def test_571_low_mem_recovered_resets_the_capacity_episode(self):
        # #571: mem OK -> the OOM skip did not fire -> the surface episode resets
        # (lms/lmsurf cleared), so a FUTURE persistent low-mem run re-surfaces.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        live_ev = [WorkerLane("w1", "live", 100.0, None, ""),
                   WorkerLane("w2", "live", 100.0, None, "")]
        rec = {"lms": 3, "lmsurf": True}
        with m.patch.object(wd, "count_live_workers", return_value=(2, live_ev)), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            self._call(GOAL_ARMED_CAP, lambda cwd: 12, now, tmtime, rec=rec)
        self.assertEqual(rec.get("lms", 0), 0)
        self.assertFalse(rec.get("lmsurf", False))

    def test_input_box_not_idle_logs_skip_not_silent(self):
        # kind=="input", no draft, but not at an idle prompt -> the ONE
        # previously-silent _boundary_ok sub-case (every other not-ok shape
        # already logged).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "pane_at_idle_prompt", return_value=False):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:not-idle-prompt" in ln for ln in logs), logs)

    def test_gave_up_already_pinged_logs_skip_not_silent(self):
        # After the one-shot GAVE UP ping has fired (lpinged already True),
        # later sweeps used to return silently.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": True, "lna": 0}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:gave-up" in ln for ln in logs), logs)

    def test_reduced_authority_is_deliberately_silent(self):
        # A reduced-authority box has no worktree lanes -> structurally N/A,
        # deliberately silent (locks the documented no-log decision).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      authority="branch-merge")
        self.assertEqual(logs, [])
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])

    def test_wiring_guard_is_deliberately_silent(self):
        # Unwired backlog_fetch/state is a test/degraded call -> silent by design.
        proj = self._dir()
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        logs, owns = goal.goal_lane_occupancy_nudge(
            100000, lambda *a, **k: "", {}, self.SID, self.CWD, "111",
            GOAL_ARMED_CAP, tpath, 100000, "loc", None, False, None, proj,
            backlog_fetch=None, state={}, sleep_fn=lambda s: None)
        self.assertEqual(logs, [])
        self.assertFalse(owns)

    def test_no_open_backlog_refuses(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 0, now, tmtime)
        self.assertTrue(any("no measurable open backlog" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_live_worker_present_small_backlog_backs_off_509(self):
        # #509 OVERTURNS #481's tiny-backlog fill: (2 workers, backlog 5) has
        # surplus 5-2=3 < GOAL_LANE_UNDERSAT_SURPLUS(5), so the guard no longer
        # pushes for a 5th lane against a workable count too small to fill it -- it
        # skips:surplus-floor. #481's real-backlog filling survives at a genuine
        # surplus (test_undersaturated_large_surplus_still_nudges_509). This test
        # locked the pre-#509 "small-but-real backlog must be filled" invariant,
        # which is exactly what #509 deliberately narrows.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 5,
                                          now, tmtime)
        self.assertTrue(any("skip:surplus-floor" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    # ---------------------------------------------------------------- #
    # #442 re-fix 2 (REOPEN č.2 + owner directives 2026-08-14) -- the
    # COUNT-based fill-the-cap widening. Nudge when live_workers < 5 AND
    # backlog > 10 AND MemAvailable is healthy; the old guard only fired at
    # exactly 0 workers, and its `_pane_has_bg_agent` early-skip meant it
    # could never fire on a live box with visible workers at all.
    # ---------------------------------------------------------------- #

    def test_two_workers_big_backlog_with_memory_nudges(self):
        # THE live-box lock: 2 live workers (structured `count_live_workers`) +
        # backlog 37 + healthy memory must NUDGE (under-saturated fill). The pane
        # SHOWS the agent strip; post-#518 the count is the structured G2 count,
        # not the render strip, so the reopen-2 root cause (the old
        # `_pane_has_bg_agent` early-skip) can no longer suppress it.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
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
        with m.patch.object(wd, "count_live_workers", return_value=(4, [])), \
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
        with m.patch.object(wd, "count_live_workers", return_value=(5, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 37,
                                          now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("saturated (>= 5 workers)" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    # ---------------------------------------------------------------- #
    # #518 -- the gating worker count converts from the render-dependent
    # `_count_live_subagents` + `_pane_has_bg_agent` render floor to the
    # structured G2 `count_live_workers`. RED: a strip-visible box whose
    # structured count is 0 (all workers silently dead) must fire the
    # EMPTY-LANE recovery nudge, not the render-floored under-saturated skip.
    # The two lock tests hold the empty-lane / saturated decision semantics
    # (both count sources patched consistently -> green before AND after the
    # conversion, so the DECISION is what is locked, not the source).
    # ---------------------------------------------------------------- #

    def test_518_dead_workers_with_visible_strip_fire_empty_lane(self):
        # RED today: the render floor (`_pane_has_bg_agent(strip)`) floors a
        # strip-visible, transcript-0 box to 1 worker -> under-saturated ->
        # skip:surplus-floor, suppressing the empty-lane recovery nudge for a
        # box whose "workers" are all silently dead. The structured count is 0,
        # so the converted gate fires the EMPTY-LANE nudge. Mutation-lock: a
        # revert to `_count_live_subagents` + the render floor goes RED here.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 5,
                                          now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)
        # never the render-floored under-saturated skip (the pre-#518 behavior)
        self.assertFalse(any("surplus-floor" in ln for ln in logs), logs)

    def test_518_lock_empty_lane_decision_preserved(self):
        # LOCK (green before AND after): with both count sources agreeing on 0,
        # an idle box with backlog fires the empty-lane nudge exactly as today.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5,
                                          now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)

    def test_518_lock_saturated_decision_preserved(self):
        # LOCK (green before AND after): with both count sources agreeing on 5
        # (>= the floor of min(5, backlog)), the box is saturated -> silent.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "_count_live_subagents", return_value=5), \
             m.patch.object(wd, "count_live_workers", return_value=(5, [])), \
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
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
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
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
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

    # ---------------------------------------------------------------- #
    # #611 -- the 0-worker EMPTY-lane branch's 15-min idle floor is
    # STRUCTURALLY unreachable for a continuously serially-working armed
    # session (writes a turn every ~10-13min so `idle` never reaches 15m),
    # yet that is the WORST under-saturation (0 lanes + big backlog). The
    # idle floor is now BYPASSED once the WNT gate has ESCALATED (a ⏳ marker
    # + 0 structured lanes + backlog confirmed over GOAL_LANE_WNT_MAX_DEFERS
    # sweeps); a session NOT in escalation keeps the 15-min behavior. The
    # remaining delivery gates (boundary, recent-human, draft-diff, hourly
    # cooldown, MAX_NUDGES) carry the mid-dispatch safety. camera-box: I=41,
    # 0 workers, 184x skip:idle / 12h, 0 nudge -> "nikdy".
    # ---------------------------------------------------------------- #

    def test_611_wnt_escalated_zero_lane_fires_despite_fresh_transcript(self):
        # THE headline lock: ⏳ marker, 0 render badges, 0 STRUCTURED live
        # lanes, real backlog, FRESH transcript (idle=30s), and the WNT gate
        # ESCALATES this sweep (wntd seeded to max-1) -> the empty-lane fill
        # nudge FIRES, bypassing the idle floor. RED on the OLD code: escalation
        # reached the idle gate and died on skip:idle (the dead-letter).
        now = 100000
        tmtime = now - 30  # fresh: transcript written 30s ago, idle << 15min
        rec = {"wntd": goal.GOAL_LANE_WNT_MAX_DEFERS - 1}  # this sweep escalates
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now,
                                          tmtime, rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("working-no-tasks ESCALATE" in ln for ln in logs), logs)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_611_zero_lane_not_escalated_still_skips_idle(self):
        # CONTROL: the SAME fresh-transcript 0-worker shape but NOT in WNT
        # escalation (no ⏳ marker -> the working-no-tasks branch never fires ->
        # escalated=False) keeps the original 15-min idle floor -> skip:idle.
        # The bypass is gated STRICTLY on WNT escalation, never on 0 workers.
        now = 100000
        tmtime = now - 30  # fresh
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("skip:idle" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_611_wnt_below_escalation_defers_never_fires(self):
        # CONTROL: a ⏳ + 0-lane + fresh session whose WNT streak is BELOW the
        # escalation threshold DEFERS (skip:working-no-tasks) and never fires --
        # the bypass activates only AFTER the multi-sweep escalation confirms 0
        # structured lanes, never on the first ⏳ sweep (a transient render flap).
        now = 100000
        tmtime = now - 30  # fresh
        rec = {}  # wntd absent -> streak becomes 1, well below max (3)
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now,
                                          tmtime, rec=rec)
        self.assertFalse(owns)
        self.assertTrue(any("skip:working-no-tasks" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_611_escalated_gives_up_after_max_nudges_no_forever_nudge(self):
        # #611 EXPLICIT-DECISION lock: the WNT-escalated empty-lane branch is
        # still bounded by GOAL_LANE_MAX_NUDGES -- after the budget it GIVES UP
        # (one owner ping) instead of nudging a perpetually-⏳-0-lane session
        # forever. The #530 backlog-change re-arm still fires on the NON-escalated
        # fresh sweeps; it is deliberately not re-granted on the escalated path,
        # where a box that ignored 2 fill nudges needs a human, not more pokes.
        now = 100000
        tmtime = now - 30  # fresh: the escalated path would FIRE if not gave-up
        rec = {"wntd": goal.GOAL_LANE_WNT_MAX_DEFERS - 1,
               "ln": goal.GOAL_LANE_MAX_NUDGES}
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now,
                                          tmtime, rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_611_escalation_gated_on_min_backlog_no_spam(self):
        # #611 (review 🔵): WNT escalation is gated on backlog >=
        # GOAL_LANE_MIN_BACKLOG, so a ⏳ + 0-lane session with a SUB-MIN backlog
        # (1-2 held/foreign items, nothing dispatchable) DEFERS quietly
        # (skip:working-no-tasks) instead of ESCALATE -> skip:min-backlog log
        # spam every sweep. RED before the gate: it escalated then hit
        # skip:min-backlog with an unbounded wntd streak.
        now = 100000
        tmtime = now - 30  # fresh
        rec = {"wntd": goal.GOAL_LANE_WNT_MAX_DEFERS - 1}  # would escalate if backlog>=min
        with m.patch.object(wd, "transcript_last_marker", return_value="⏳"), \
             m.patch.object(wd, "_pane_live_task_count", return_value=0), \
             m.patch.object(wd, "count_live_workers", return_value=(0, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 2, now,
                                          tmtime, rec=rec)   # backlog 2 < MIN (3)
        self.assertFalse(owns)
        self.assertTrue(any("skip:working-no-tasks" in ln for ln in logs), logs)
        self.assertFalse(any("ESCALATE" in ln for ln in logs), logs)
        self.assertFalse(any("skip:min-backlog" in ln for ln in logs), logs)
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
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
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
        # old numberless "rate-limited". #530: the FIRST cooldown check is the
        # shared hard hourly cap (both branches), so a nudge 60s after the last
        # skips:hourly-cap; the value is asserted so a sign-flip mutant is caught.
        # Fired 60s ago into the 1h cap -> GOAL_LANE_INTERVAL_S - 60 remaining.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"llast": now - 60}  # fired 60s ago, inside the hourly cap
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 32,
                                          now, tmtime, rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("skip:hourly-cap remaining=%ds" % (
            goal.GOAL_LANE_INTERVAL_S - 60) in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_min_floor_saturates_below_five_when_backlog_is_small(self):
        # #481: floor = min(5, backlog). (3 workers, backlog 3) -> floor 3,
        # 3 >= 3 -> SATURATED (silent), and the decision names the ACTUAL
        # floor (3), not a fixed 5. Locks the `min` computation: a mutant
        # using a fixed 5 would read 3 < 5 -> under-saturated -> fire.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "count_live_workers", return_value=(3, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 3,
                                          now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("saturated (>= 3 workers)" in ln for ln in logs),
                        logs)
        self.assertEqual(tmux.sent, [])

    def test_busy_undersaturated_stash_abort_still_reaches_giveup(self):
        # #442-review F1: the stash-abort give-up (requirement 2 -- must stay
        # for BOTH branches) was made structurally UNREACHABLE on a busy
        # under-saturated session by the session-active reset. A busy session
        # always has idle < GOAL_LANE_IDLE_S, so the reset zeroed `lna` every
        # sweep before the streak could reach the cap. A permanently-aborting
        # lane (parked draft occupying the stash slot) on a busy box must still
        # accumulate the streak across sweeps and fire the ONE give-up ping.
        # #479 -- the abort streak is now throttled by ELAPSED TIME (an
        # escalating backoff parks each next attempt), not by raw iteration
        # count, so "across sweeps" must advance the clock past each park
        # window (max 1800s) for the streak to accumulate. The INTENT is
        # unchanged and still asserted: a permanently-aborting lane on a busy
        # box still reaches the ONE give-up ping -- just over elapsed time,
        # not once per 60s sweep. (`step` exceeds the largest backoff, so every
        # park has always elapsed by the next sweep -> the streak advances one
        # per sweep exactly as before, only on a moving clock.)
        start = 100000
        step = 2000
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        rec = {}
        state = {}
        sent = []
        all_logs = []
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192), \
             m.patch.object(wd, "deliver_with_stash", return_value=False):
            for i in range(goal.GOAL_LANE_MAX_STASH_ABORTS + 3):
                now = start + i * step
                tmtime = now - 30  # busy: fresh transcript, idle << 15min
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
        # #442-review F1 / #530: on an active empty-lane sweep the stash-abort
        # streak (lna/lnpark) always resets, and the COUNT give-up (ln/lpinged)
        # re-arms too here because this seed carries NO give-up baseline
        # (`lnbk` absent -> treated as "backlog changed" -> reset). A seed WITH
        # lnbk == backlog holds the give-up instead
        # (test_530_active_sweep_holds_giveup_when_backlog_unchanged).
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
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
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
                       sleep_fn=None, state=None):
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
                       sleep_fn=None, state=None):
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

    def test_lane_nudge_threads_state_to_deliver_with_stash(self):
        # #488: the durable park record is written/cleared INSIDE
        # deliver_with_stash (only on a definitively-ours STASH_PARKED, never
        # a pre-existing foreign slot -- review MAJOR). The lane nudge's job
        # is only to THREAD `state` through so that machinery runs; the
        # write/clear itself is proven at the deliver_with_stash level
        # (test_stash_unconditional.py::Issue488DurableParkRecord).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        seen = []

        def fake_stash(pid, text, run, captured=None, logs=None,
                       sleep_fn=None, state=None):
            seen.append(state)
            return True

        state = {"tag": "sentinel"}
        with m.patch.object(wd, "deliver_with_stash", side_effect=fake_stash):
            logs, owns, tmux = self._call(GOAL_ARMED_DRAFT_CAP,
                                          lambda cwd: 5, now, tmtime,
                                          state=state)
        self.assertTrue(owns)
        self.assertEqual(len(seen), 1, logs)
        self.assertIs(seen[0], state)

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

    # ---------------------------------------------------------------- #
    # #479 — a delivery that keeps hitting the SAME persistently-parked
    # LIVE human draft must BACK OFF, not retry every ~60s sweep (the
    # 2026-08-14 storm on zbynek-4:0.0: type-verify-failed -> retry ->
    # re-type -> re-rescue, every sweep for ~3h, until GAVE UP after 5).
    # The single reactions were already correct (never overwrite a live
    # draft, rescue before any keystroke); what was missing is REPETITION
    # damping. An abort now parks the delivery for an escalating window in
    # durable `rec['lnpark']`; within that window the nudge skips WITHOUT
    # touching the pane at all. The refusal itself is NEVER weakened --
    # deliver_with_stash still refuses the live draft, and a permanently-
    # aborting lane still reaches the give-up ping, just over elapsed time
    # instead of once per sweep.
    # ---------------------------------------------------------------- #

    def test_479_stash_abort_parks_delivery_with_escalating_backoff(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {}
        with m.patch.object(wd, "deliver_with_stash", return_value=False):
            self._call(GOAL_ARMED_DRAFT_CAP, lambda cwd: 5, now, tmtime,
                       rec=rec, state={})
        self.assertEqual(rec.get("lna"), 1)
        self.assertEqual(rec.get("lnpark"),
                         now + goal._lane_stash_abort_backoff(1))
        self.assertGreater(goal._lane_stash_abort_backoff(2),
                           goal._lane_stash_abort_backoff(1))
        self.assertGreater(goal._lane_stash_abort_backoff(3),
                           goal._lane_stash_abort_backoff(2))

    def test_479_within_backoff_window_skips_without_touching_the_pane(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"lna": 1, "lnpark": now + 200}
        calls = []

        def fake_stash(pid, text, run, captured=None, logs=None, sleep_fn=None,
                       state=None):
            calls.append(pid)
            return False

        with m.patch.object(wd, "deliver_with_stash", side_effect=fake_stash):
            logs, owns, tmux = self._call(GOAL_ARMED_DRAFT_CAP, lambda cwd: 5,
                                          now, tmtime, rec=rec, state={})
        self.assertTrue(owns)
        self.assertEqual(calls, [],
                         "within the abort-backoff window the pane must not be "
                         "touched at all -- no re-type, no re-rescue")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("abort-backoff" in ln for ln in logs), logs)

    def test_479_backoff_clears_on_successful_delivery(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"lna": 2, "lnpark": now - 1}   # park already elapsed
        with m.patch.object(wd, "deliver_with_stash", return_value=True):
            self._call(GOAL_ARMED_DRAFT_CAP, lambda cwd: 5, now, tmtime,
                       rec=rec, state={})
        self.assertNotIn("lna", rec)
        self.assertNotIn("lnpark", rec)

    def test_479_backoff_spaces_out_reattempts_across_sweeps(self):
        # the core damping: over 10 minute-apart sweeps of a permanently-
        # aborting lane, the pane is touched only a FEW times (as the backoff
        # windows elapse), never once per sweep (the 60s hammer the journal
        # shows at 15:18->15:19->15:20->15:21 on 2026-08-14).
        start = 100000
        rec = {}
        state = {}
        calls = []

        def fake_stash(pid, text, run, captured=None, logs=None, sleep_fn=None,
                       state=None):
            calls.append(pid)
            return False

        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192), \
             m.patch.object(wd, "deliver_with_stash", side_effect=fake_stash):
            for i in range(10):                     # 10 sweeps, 60s apart
                now = start + i * 60
                tmtime = now - 30                    # busy under-saturated
                tmux = DeliverGoalFakeTmux(
                    [("%9", "claude", self.CWD, "111")], GOAL_ARMED_DRAFT_CAP)
                goal.goal_lane_occupancy_nudge(
                    now, tmux, rec, self.SID, self.CWD, "111",
                    GOAL_ARMED_DRAFT_CAP, tpath, tmtime, "loc",
                    lambda msg, **k: None, False, None, proj,
                    backlog_fetch=lambda cwd: 32, state=state,
                    sleep_fn=lambda s: None)
        self.assertGreaterEqual(len(calls), 1)
        self.assertLessEqual(len(calls), 4,
                             "backoff must throttle the 60s retry hammer to a "
                             "few attempts, never once per sweep")

    # ---------------------------------------------------------------- #
    # #509 -- SURPLUS FLOOR + effectiveness (feedback) BACKOFF for the
    # UNDER-SATURATED fill nudge. FLOOR: only push for MORE lanes when the
    # workable backlog clearly exceeds the running lanes. BACKOFF: a nudge
    # that produced NO new LIVE lane (`count_live_workers` flat) widens the
    # NEXT interval, holding at the cap forever (never silent); it resets
    # when a lane appears / the backlog grows / a lane drops. Empty-lane
    # (0 workers) is UNAFFECTED (anti-silence).
    # ---------------------------------------------------------------- #

    def _undersat_call(self, backlog, now, tmtime, rec, subagents, eff_workers,
                       mem=8192):
        # Drive the under-saturated fill path deterministically. Post-#518 the
        # gating count AND the #509 effectiveness signal are BOTH
        # `count_live_workers` (= `eff_workers`); the `subagents` patch of the
        # now-unused `_count_live_subagents` is a harmless no-op kept only so the
        # existing #509 call sites (which pass `subagents == eff_workers`) stay
        # byte-identical. Healthy memory, recent-human gate neutralized.
        with m.patch.object(wd, "_count_live_subagents", return_value=subagents), \
             m.patch.object(wd, "count_live_workers",
                            return_value=(eff_workers, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=mem), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            return self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: backlog, now,
                              tmtime, rec=rec)

    def test_undersaturated_below_surplus_floor_skips_509(self):
        # 2 workers + backlog 5 -> pre-#509 floor min(5,5)=5, 2<5 -> NUDGED.
        # #509: surplus 5-2=3 < GOAL_LANE_UNDERSAT_SURPLUS(5) -> skip:surplus-floor.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._undersat_call(5, now, tmtime, {}, 2, 2)
        self.assertTrue(any("skip:surplus-floor" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_undersaturated_large_surplus_still_nudges_509(self):
        # A genuine surplus (backlog 37, 2 workers, surplus 35 >= 5) still fills --
        # #481's real-backlog filling is preserved.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._undersat_call(37, now, tmtime, {}, 2, 2)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_ineffective_nudge_widens_interval_509(self):
        # #530: two ineffective nudges (count_live_workers flat), spaced past the
        # 1h hourly cap so each fires, then a 3rd attempt 90 min after nudge 2 --
        # past the hourly cap but INSIDE the widened stage-1 (2h) interval -- must
        # skip:ineffective-backoff.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {}
        logs1, _, _ = self._undersat_call(37, now, tmtime, rec, 2, 2)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs1), logs1)
        t2 = now + goal.GOAL_LANE_INTERVAL_S + 1
        logs2, _, _ = self._undersat_call(37, t2, tmtime, rec, 2, 2)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs2), logs2)
        self.assertEqual(rec.get("lineff"), 1, rec)   # streak advanced
        t3 = t2 + 90 * 60
        logs3, _, tmux3 = self._undersat_call(37, t3, tmtime, rec, 2, 2)
        self.assertTrue(any("skip:ineffective-backoff" in ln for ln in logs3), logs3)
        self.assertEqual(tmux3.sent, [])

    def test_effective_nudge_resets_backoff_509(self):
        # A deep-backoff lane (streak 3) whose next sweep sees a NEW live lane
        # (count_live_workers 2 -> 3) resets the streak to 0 and fires at the base
        # interval. #530: called 61 min later -- past the 1h hourly cap, so with
        # the streak reset to 0 (base interval == hourly cap) it FIRES; had the
        # streak stayed 3 (4h interval) it would still be backed off.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 3, "llast": now, "lineff": 3, "lnw": 2, "lnb": 37}
        logs, owns, tmux = self._undersat_call(37, now + 61 * 60, tmtime, rec, 2, 3)
        self.assertEqual(rec.get("lineff"), 0, rec)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)

    def test_backoff_resets_when_backlog_grows_509(self):
        # A deep-backoff lane whose backlog GREW since the last nudge resets
        # (re-probe -- genuine new work). #530: 61 min later, past the hourly cap,
        # so the reset-to-base-interval nudge fires.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 3, "llast": now, "lineff": 3, "lnw": 2, "lnb": 37}
        logs, owns, tmux = self._undersat_call(50, now + 61 * 60, tmtime, rec, 2, 2)
        self.assertEqual(rec.get("lineff"), 0, rec)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)

    def test_backoff_does_not_reset_on_a_bare_lane_drop_509(self):
        # #509 adversarial review (both reviewers converged): a bare lane DROP does
        # NOT reset the streak. On an un-liftable backlog a worker completing with
        # nothing to replace it (count 3->1) is the normal "nothing to lift" churn;
        # resetting on it would re-open the burn. #530: at streak 3 the interval is
        # 240 min; 90 min elapsed -- past the 1h hourly cap but still inside the
        # deep interval -> skip:ineffective-backoff, streak unchanged, NO nudge.
        # (Under the rejected drop=reset the interval would collapse to the base
        # 1h and it would fire at 90 min.)
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 3, "llast": now, "lineff": 3, "lnw": 3, "lnb": 37}
        logs, owns, tmux = self._undersat_call(37, now + 90 * 60, tmtime, rec, 1, 1)
        self.assertEqual(rec.get("lineff"), 3, rec)   # streak NOT reset by a drop
        self.assertTrue(any("skip:ineffective-backoff" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_surplus_floor_boundary_is_exactly_five_509(self):
        # #509 review MINOR-2 (both reviewers): lock GOAL_LANE_UNDERSAT_SURPLUS==5
        # at the boundary so a `<`->`<=` off-by-one is caught. surplus 4 (backlog
        # 6, 2 workers) skips; surplus 5 (backlog 7, 2 workers) nudges (design:
        # backlog exceeds lanes by >=5 fills).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs4, _, tmux4 = self._undersat_call(6, now, tmtime, {}, 2, 2)
        self.assertTrue(any("skip:surplus-floor" in ln for ln in logs4), logs4)
        self.assertEqual(tmux4.sent, [])
        logs5, _, tmux5 = self._undersat_call(7, now, tmtime, {}, 2, 2)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs5), logs5)
        self.assertFalse(any("skip:surplus-floor" in ln for ln in logs5), logs5)

    def test_backoff_holds_at_cap_never_silent_509(self):
        # Anti-silence (#134): at the widest schedule stage the interval HOLDS and
        # STILL fires once it elapses -- never permanently silent.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        cap = goal.GOAL_LANE_INEFFECTIVE_BACKOFF_S[-1]
        seed = {"ln": 9, "llast": now, "lineff": 9, "lnw": 2, "lnb": 37}
        logs_in, _, _ = self._undersat_call(37, now + cap - 60, tmtime,
                                            dict(seed), 2, 2)
        self.assertTrue(any("skip:ineffective-backoff" in ln for ln in logs_in),
                        logs_in)
        logs_out, _, _ = self._undersat_call(37, now + cap + 60, tmtime,
                                             dict(seed), 2, 2)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs_out),
                        logs_out)

    def test_empty_lane_ignores_surplus_floor_and_backoff_509(self):
        # Anti-silence: the 0-worker EMPTY-lane nudge is UNAFFECTED by the
        # under-saturated surplus floor and never takes the effectiveness backoff.
        # #530: it now has its OWN min-backlog floor (GOAL_LANE_MIN_BACKLOG=3), so
        # this fires at backlog 3 (the empty-lane floor), never taking the
        # under-sat surplus-floor path.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 3, now, tmtime)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertFalse(any("surplus-floor" in ln for ln in logs), logs)

    # ---------------------------------------------------------------- #
    # #511 -- the STASH-ABORT give-up must NOT latch permanently on an
    # under-saturated box. Its only reset (the 0-worker idle branch) is
    # unreachable while >=1 worker runs, so once `lna` hit the cap and the
    # one-shot ping fired the lane went permanently silent -- even after the
    # wedged draft that caused the aborts cleared and a huge surplus opened
    # (gk 2026-08-16: lna=5/lpinged, park elapsed 10h, I 20 vs 2 workers,
    # `skip:gave-up` every sweep for hours across backlog GROWTH 7->8, 11->15).
    # The give-up escalates ONCE then re-probes via the #479 abort-backoff
    # park; the 0-worker count give-up stays permanent (its reset IS reachable).
    # ---------------------------------------------------------------- #

    def test_stash_abort_giveup_reprobes_and_delivers_after_park_511(self):
        # The gk latch: aborts at the cap, already pinged, park long elapsed,
        # pane now a clean deliverable idle prompt, huge surplus. The nudge must
        # RE-PROBE and DELIVER, never latch on skip:gave-up forever.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 4, "lna": goal.GOAL_LANE_MAX_STASH_ABORTS, "lpinged": True,
               "lnpark": now - 10000, "llast": now - 40000}
        logs, owns, tmux = self._undersat_call(37, now, tmtime, rec, 2, 2)
        self.assertFalse(any("skip:gave-up" in ln for ln in logs), logs)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)
        # reset-on-progress: a landed nudge clears the give-up latch so a
        # genuinely-new future abort storm re-escalates instead of re-probing
        # silently forever.
        self.assertNotIn("lna", rec)
        self.assertFalse(rec.get("lpinged"))

    def test_stash_abort_giveup_within_park_logs_backoff_not_giveup_511(self):
        # Same latch but the abort-backoff park is still ACTIVE: the sweep must
        # fall through to the #479 park (skip:abort-backoff, re-probe pending),
        # never the permanent skip:gave-up short-circuit. Proves the fall-through
        # reaches the park instead of returning early.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 4, "lna": goal.GOAL_LANE_MAX_STASH_ABORTS, "lpinged": True,
               "lnpark": now + 500, "llast": now - 40000}
        logs, owns, tmux = self._undersat_call(37, now, tmtime, rec, 2, 2)
        self.assertFalse(any("skip:gave-up" in ln for ln in logs), logs)
        self.assertTrue(any("skip:abort-backoff" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    # ---------------------------------------------------------------- #
    # #530 -- (1) empty-lane MIN-BACKLOG floor, (2) a hard 1-hour cap on
    # BOTH branches, (3) the count give-up (GOAL_LANE_MAX_NUDGES) must
    # actually HOLD -- the idle-branch reset used to wipe `ln` on mere
    # transcript freshness (which the nudge's own delivery guarantees), so
    # the give-up was structurally unreachable on any live supervisor.
    # ---------------------------------------------------------------- #

    def test_530_min_backlog_floor_skips_lone_epic(self):
        # A fully-stalled box (0 workers) with a single open umbrella epic
        # (backlog 1) must NOT be nudged -- the reported gk incident. RED on the
        # old code (no floor -> fires on backlog 1).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 1, now, tmtime)
        self.assertFalse(owns)
        self.assertTrue(any("skip:min-backlog" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_530_min_backlog_floor_skips_two_workable(self):
        # backlog 2 is still below the floor of 3 -> skip:min-backlog.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 2, now, tmtime)
        self.assertTrue(any("skip:min-backlog" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_530_min_backlog_floor_fires_at_threshold(self):
        # backlog exactly 3 is AT the floor -> still fires (boundary lock).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 3, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertFalse(any("skip:min-backlog" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_530_landed_nudge_records_backlog_baseline(self):
        # A landed empty-lane nudge must record `lnbk` (the give-up baseline) so
        # the idle-branch reset can tell "backlog unchanged" from "changed".
        # Mutation lock: without the `_lane_record_nudge` lnbk write, lnbk stays
        # absent and the give-up would reset on every fresh sweep (the old bug).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 7, now, tmtime,
                                      rec=rec)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(rec.get("lnbk"), 7, rec)

    def test_530_hourly_cap_empty_lane(self):
        # A second empty-lane nudge 1000s (past the OLD 15-min cooldown, INSIDE
        # the new 1-hour cap) after the last must skip:hourly-cap. RED on the old
        # code (15-min cooldown -> fires at 1000s).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"llast": now - 1000, "ln": 1}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(any("skip:hourly-cap" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_530_hourly_cap_under_saturated(self):
        # The SAME hard 1-hour cap applies to the under-saturated branch: a fill
        # nudge 1000s after the last skips:hourly-cap (RED on the old code, whose
        # under-sat stage-0 interval was 15 min -> fires at 1000s).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"llast": now - 1000}
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])), \
             m.patch.object(goal, "_mem_available_mb", return_value=8192):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 32,
                                          now, tmtime, rec=rec)
        self.assertTrue(any("skip:hourly-cap" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_530_active_sweep_holds_giveup_when_backlog_unchanged(self):
        # THE core trace lock: an ACTIVE (fresh-transcript) empty-lane sweep with
        # the give-up already reached and the backlog UNCHANGED since the last
        # nudge (lnbk == backlog) must NOT reset `ln`/`lpinged` -- otherwise the
        # nudge's own delivery (which refreshes mtime) wipes the counter one
        # sweep later and GOAL_LANE_MAX_NUDGES is unreachable. The stash-abort
        # streak (lna) still resets on session-active. RED on the old code (the
        # idle-branch reset zeroed ln/lpinged unconditionally on freshness).
        now = 100000
        tmtime = now - 30  # active / fresh transcript
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lnbk": 5, "lpinged": True,
               "lna": 3}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertEqual(rec.get("ln"), goal.GOAL_LANE_MAX_NUDGES)  # HELD
        self.assertTrue(rec.get("lpinged"))                         # HELD
        self.assertEqual(rec.get("lna"), 0)  # stash streak still resets on active
        self.assertTrue(any("skip:idle" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_530_active_sweep_resets_giveup_when_backlog_changed(self):
        # The reset that SHOULD fire: the backlog genuinely changed since the last
        # nudge -> fresh work to consider -> the give-up re-arms. (Green before
        # AND after: the old code also reset here, only for the wrong reason.)
        now = 100000
        tmtime = now - 30
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lnbk": 5, "lpinged": True,
               "lna": 0}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 9, now, tmtime,
                                      rec=rec)
        self.assertEqual(rec.get("ln"), 0)
        self.assertFalse(rec.get("lpinged"))
        self.assertTrue(any("skip:idle" in ln for ln in logs), logs)

    def test_530_giveup_fires_after_holding_across_active_sweep(self):
        # End-to-end of the trace: the give-up SURVIVES an active/quiet
        # oscillation (backlog unchanged) and fires GAVE UP on the next quiet
        # sweep. RED on the old code: the active sweep wiped `ln`, so the quiet
        # sweep never reached the give-up.
        now = 100000
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lnbk": 5, "lpinged": False,
               "lna": 0}
        # active sweep (fresh transcript, idle << 15min): must HOLD the counter
        self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, now - 30, rec=rec)
        self.assertEqual(rec.get("ln"), goal.GOAL_LANE_MAX_NUDGES)
        # quiet again (idle >= 15min): the held give-up fires GAVE UP
        later = now + 10000
        logs, owns, tmux = self._call(
            GOAL_ARMED_CAP, lambda cwd: 5, later,
            later - goal.GOAL_LANE_IDLE_S - 100, rec=rec)
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
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
        # #442 re-fix 2 + #481: the under-saturated text names the real worker
        # count AND the target lane floor, commands fleet dispatch, and frames
        # saturation as WORK-DRIVEN (not a fixed cap number).
        # Args: (live_workers, floor, backlog, waiters).
        rendered = goal.GOAL_LANE_UNDERSAT_NUDGE_TEXT % (2, 5, 37, 1)
        low = rendered.lower()
        self.assertIn("beží len 2", rendered)
        # #481: the target lane floor is cited alongside the seen worker count.
        self.assertIn("cieľových 5", rendered)
        self.assertIn("worktree", low)
        self.assertIn("paraleln", low)
        self.assertIn("sériovo", low)
        # work-driven, not a fixed target
        self.assertIn("prác", low)
        self.assertIn("ci", low)

    def test_min_mem_threshold_is_a_named_constant_documented(self):
        # #442 re-fix 2 / #574: the memory floor is a named, sane default
        # (~1 GB after the #574 evidence-based recalibration from the
        # uncalibrated 1536; effective floor is env-overridable via
        # _lane_min_mem_avail_mb).
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
