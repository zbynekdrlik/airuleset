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
        logs = goal.goal_sweep(1000, requests_path=reqp, run=lambda *a, **k: "")
        self.assertEqual(goal.load_goal_requests(reqp), {})
        # #624 -- the drop is no longer a SILENT branch: it emits one decision
        # line naming the reason, like every other pending-entry disposition.
        self.assertTrue(any("malformed" in ln for ln in logs), logs)

    def test_non_dict_entry_is_dropped_not_silently_skipped(self):
        # #624-review -- a corrupt NON-dict entry is malformed like the
        # empty-text one: it must emit a decision line AND be cleared, never a
        # silent `continue` that re-skips (and would re-log) it every sweep.
        reqp = self._reqp()
        Path(reqp).write_text(json.dumps({"sess-nd": "not-a-dict"}))
        logs = goal.goal_sweep(1000, requests_path=reqp, run=lambda *a, **k: "")
        self.assertTrue(any("non-dict" in ln for ln in logs), logs)
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

    # ------------------------------------------------------------------ #
    # #624 -- delivery decision line OBSERVABILITY. The line was the ONE
    # goal-family journal line keyed on the bare opaque sid (every sibling
    # -- stale-rearm / lane-occupancy / ops-wait-recheck / one-glance -- is
    # keyed `<verb> <loc> ...`), so a loc-scoped grep of the journal could
    # never find it and it read as silence (the ticket's "the two cases are
    # indistinguishable from outside"). Every disposition line must now carry
    # the family loc (`_pane_location`, which the fake renders as `sess:0.0`).
    # ------------------------------------------------------------------ #
    def test_skip_decision_line_carries_the_project_loc(self):
        proj = self._dir()
        sid = "sess-sweep-loc"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_BUSY_CAP)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        line = next((ln for ln in logs if "(goal-sweep)" in ln), None)
        self.assertIsNotNone(line, logs)
        self.assertIn("sess:0.0", line)

    def test_sent_decision_line_carries_the_project_loc(self):
        proj = self._dir()
        sid = "sess-sweep-loc2"
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        line = next((ln for ln in logs if "OK (goal-sweep)" in ln), None)
        self.assertIsNotNone(line, logs)
        self.assertIn("sess:0.0", line)

    def test_recent_human_skip_line_carries_loc_and_reason_detail(self):
        # The LIVE montalu1 case: a stale-rearm request CORRECTLY deferring on
        # a genuine human presence. The journal line must SAY the reason detail
        # (the `presence marker Ns old` deliver_goal already computes for
        # goal-sync.log), so case-1 is self-evident without a second file.
        proj = self._dir()
        sid = "sess-sweep-rh-" + uuid.uuid4().hex[:8]
        _write_marker_transcript(proj, self.CWD, sid)
        reqp = self._reqp()
        goal.record_goal_request(sid, self.CWD, "/goal x", "branch-merge",
                                 now=1000, path=reqp, origin="stale-rearm")
        marker = "/tmp/claude-user-active-%s" % sid
        Path(marker).write_text("")
        self.addCleanup(lambda: Path(marker).unlink(missing_ok=True))
        # 100s before now=2000 -> within GOAL_AUTOARM_RECENT_HUMAN_S (1800s).
        os.utime(marker, (1900, 1900))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None)
        line = next((ln for ln in logs if "skip:recent-human" in ln), None)
        self.assertIsNotNone(line, logs)
        self.assertIn("sess:0.0", line)
        self.assertIn("presence marker", line)


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

    def _dark_flag(self, sid, flag_ts=600):
        """#766: the `_dark` dark-goal fixture PLUS a `🏁 BACKLOG EMPTY:`
        completion turn AFTER the arm (ts_epoch=500) -- a genuinely-ACHIEVED
        loop, distinct from `_dark` (no 🏁 -> transcript-identical to a stall)."""
        from datetime import datetime, timezone
        proj = self._dir()
        tpath = _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        iso = datetime.fromtimestamp(flag_ts, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "assistant", "timestamp": iso,
                "message": {"id": "msg_done",
                            "content": "🏁 BACKLOG EMPTY: 0 open, main green"}})
                    + "\n")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)
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

    def test_766_achieved_with_backlog_flag_is_never_pinged(self):
        # #766 SIBLING to the pin above: the SAME open==0 achieved state, but
        # this loop printed a `🏁 BACKLOG EMPTY:` proof AFTER the arm -> it is
        # provably FULFILLED, not a silent stall. The non-🏁 pin still pings once
        # (achieved is transcript-identical to a stall WITHOUT the 🏁); this
        # 🏁-carrying loop gets ZERO pings + an explicit FULFILLED-SILENT log --
        # the veto. Two sweeps: sweep1 open==0 but the cache reads not-fresh
        # (now<cts), sweep2 fresh -> the veto fires exactly when open==0 AND
        # fresh, never on the pre-fix #459 second-sweep ping.
        proj, tmux = self._dark_flag("sess-766-flag")
        obl = self._obl(0, 1500)                     # backlog empty -> achieved
        state, pings, logs = {}, [], []
        for now in (1000, 2000):
            logs += goal.goal_dark_watch(
                now, run=tmux, send_fn=lambda mm, **k: pings.append(mm),
                projects_dir=proj, state=state, sleep_fn=lambda s: None,
                obligation_fn=obl,
                rearm_fn=lambda cwd: ("/goal x", "full"))
        self.assertEqual(pings, [],
                         "a 🏁-proven achieved loop must never be pinged")
        self.assertTrue(any("FULFILLED-SILENT" in ln for ln in logs),
                        "the veto emits an explicit FULFILLED-SILENT log")
        self.assertEqual(tmux.sent, [], "the veto types no keystroke")

    def test_766_stale_open_zero_with_flag_still_pings(self):
        # #766 FAIL-SAFE TEETH (review 🟡): a 🏁 loop whose open==0 cache is
        # STALE (aged past GOAL_DARK_CACHE_MAX_AGE_S) is UNPROVABLE-achieved ->
        # today's behavior, the #459 ping STILL fires; the FULFILLED-SILENT veto
        # needs a FRESH open==0. Locks the `and fresh` conjunct of the sentinel
        # gate (dropping it would silence a stale-cache 🏁 loop forever). Mutant:
        # remove `and fresh` from the open==0 branch -> this test goes RED.
        proj, tmux = self._dark_flag("sess-766-stale")
        base = 1_700_000_000
        stale = base - goal.GOAL_DARK_CACHE_MAX_AGE_S - 100
        obl = self._obl(0, stale)                    # open==0 but STALE cache
        state, pings, logs = {}, [], []
        for now in (base, base + 1000):
            logs += goal.goal_dark_watch(
                now, run=tmux, send_fn=lambda mm, **k: pings.append(mm),
                projects_dir=proj, state=state, sleep_fn=lambda s: None,
                obligation_fn=obl, rearm_fn=lambda cwd: ("/goal x", "full"))
        self.assertEqual(len(pings), 1,
                         "a STALE open==0 cache is unprovable -> the #459 ping fires")
        self.assertFalse(any("FULFILLED-SILENT" in ln for ln in logs),
                         "an unproven (stale) achieved state must NOT be vetoed")

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

    def test_dark_episode_pings_at_most_once_804(self):
        # #804 item 4 -- the STAGED re-ping (GOAL_DARK_REPING_SCHEDULE_S,
        # count>1) is DELETED: every dark ping is notify-SUPPRESSED (goal-dark
        # in SUPPRESSED_ALERT_PREFIXES, #704) and the #795 daily re-ask is
        # retired, so a staged re-ping composed a message notify drops -- dead
        # code. The FIRST ping stays; a dark episode now pings AT MOST ONCE, no
        # matter how long it stays dark with a fresh workable cache. RED on the
        # pre-#804 staged schedule (which re-pinged at every elapsed stage);
        # GREEN once the reping path is removed. No reference to the deleted
        # constant -- a 100h/sweep jump is past any conceivable schedule.
        proj, tmux = self._dark("sess-dark-once-804")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        rearm = _rearm_none                       # template unresolved -> ping fallback
        now = [1_700_000_000]

        def obl(cwd):
            return (5, now[0] - 60)               # always fresh + workable

        self._sweep(tmux, proj, state, sent, now[0], obl, rearm, reqs)   # first obs
        now[0] += 100
        self._sweep(tmux, proj, state, sent, now[0], obl, rearm, reqs)   # FIRST ping
        self.assertEqual(len(sent), 1, "the first ping fires")
        for _ in range(12):
            now[0] += 100 * 3600                  # far past any staged schedule
            self._sweep(tmux, proj, state, sent, now[0], obl, rearm, reqs)
        self.assertEqual(len(sent), 1,
                         "a dark episode pings AT MOST ONCE, never re-pings (#804)")

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

    def _dark_awaiting(self, sid, tail="❓ NEEDS YOU: mám pokračovať?"):
        """#737 C -- a dark-armed loop whose LAST assistant turn ended with a
        `tail` marker (default a ❓ awaiting-user question). The transcript mtime
        stays frozen (written once), so the mtime liveness veto cannot see it --
        the tail marker is the ONLY awaiting-user signal."""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid, marker_text=tail)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        return proj, tmux

    def test_awaiting_user_dark_loop_is_never_re_armed(self):
        # #737 C -- a session PARKED on a ❓ question to the owner is ALIVE-
        # waiting, not a dead loop (its transcript is static so the mtime veto
        # cannot see it). dark-watch must NEVER re-arm/ping it, however workable
        # the backlog (the montalu6 incident: one-glance said awaiting-user while
        # dark-watch declared CONFIRMED-DEAD + re-armed). RED: pre-fix a workable
        # confirmed-dead run records a re-arm.
        proj, tmux = self._dark_awaiting("sess-737-await")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(5, 900)          # WORKABLE -> would re-arm without the veto
        for t in range(40):
            self._sweep(tmux, proj, state, sent, 1000 + t * 100, obl, _rearm_ok, reqs)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a session parked on ❓ must NEVER be re-armed")
        self.assertEqual(sent, [], "and never pinged -- the owner is already there")
        self.assertEqual(tmux.sent, [], "and never a keystroke")

    def test_non_question_dark_tail_still_re_arms(self):
        # #737 C CONTROL -- the veto is ❓-SPECIFIC: a genuinely dead loop whose
        # last turn was a ✅ DONE (not awaiting the user) is STILL re-armed.
        proj, tmux = self._dark_awaiting("sess-737-done", tail="✅ DONE: hotovo")
        sent, state = [], {}
        reqs = self._dir() / "goal-requests.json"
        obl = self._obl(5, 900)
        for t in range(40):
            self._sweep(tmux, proj, state, sent, 1000 + t * 100, obl, _rearm_ok, reqs)
            if goal.load_goal_requests(reqs):
                break
        self.assertIn("sess-737-done", goal.load_goal_requests(reqs),
                      "a non-awaiting dead loop must still be re-armed")

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

    def test_804_dead_rostered_stream_produces_a_dead_session_census_line(self):
        # #804 -- a stream EXPECTED to be armed (in the roster) but with NO live
        # candidate pane this sweep is a mode-5 death: the census surfaces it so
        # it can never go dark silently ("sam sa vypne a nikto to nevidí").
        from watchdog import roster
        now = 100000
        r = {}
        roster.upsert(r, "/home/newlevel/devel/deadstream", "old-sid", "full",
                      now - 7200)
        roster.save_roster(r)
        # NO panes at all -> visited_cwds empty -> the rostered cwd is dead.
        tmux = DeliverGoalFakeTmux([], GOAL_IDLE_CAP)
        logs = goal.goal_lane_sweep(now, run=tmux, projects_dir=self._dir(),
                                    backlog_fetch=lambda cwd: 5, state={})
        self.assertTrue(any("-> dead-session" in ln and "deadstream" in ln
                            for ln in logs), logs)

    def test_804_dead_session_line_is_cadenced_not_every_sweep(self):
        from watchdog import roster
        now = 100000
        r = {}
        roster.upsert(r, "/home/newlevel/devel/deadstream", "s", "full", now)
        roster.save_roster(r)
        tmux = DeliverGoalFakeTmux([], GOAL_IDLE_CAP)
        l1 = goal.goal_lane_sweep(now, run=tmux, projects_dir=self._dir(),
                                  backlog_fetch=lambda cwd: 5, state={})
        self.assertTrue(any("-> dead-session" in ln for ln in l1), l1)
        # a second sweep 5 min later must NOT re-log (< GOAL_ROSTER_CENSUS_S)
        l2 = goal.goal_lane_sweep(now + 300, run=tmux, projects_dir=self._dir(),
                                  backlog_fetch=lambda cwd: 5, state={})
        self.assertFalse(any("-> dead-session" in ln for ln in l2), l2)
        # a sweep past the cadence window re-surfaces it.
        l3 = goal.goal_lane_sweep(now + goal.GOAL_ROSTER_CENSUS_S + 10, run=tmux,
                                  projects_dir=self._dir(),
                                  backlog_fetch=lambda cwd: 5, state={})
        self.assertTrue(any("-> dead-session" in ln for ln in l3), l3)

    def test_804_census_skipped_when_the_sweep_budget_broke(self):
        # #804-review 🟡: a sweep budget break leaves deferred panes' cwds OUT of
        # visited_cwds, so the DEAD-SESSION census must NOT run that sweep (it
        # would falsely flag every deferred LIVE stream). Force the break on the
        # first pane and assert no dead-session line for the rostered cwd.
        from watchdog import roster
        now = 100000
        r = {}
        roster.upsert(r, self.CWD, "s", "full", now)
        roster.save_roster(r)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_lane_sweep(now, run=tmux, projects_dir=self._dir(),
                                    backlog_fetch=lambda cwd: 5, state={},
                                    sweep_deadline=0, time_fn=lambda: 1)
        self.assertTrue(any("budget-exceeded" in ln for ln in logs), logs)
        self.assertFalse(any("-> dead-session" in ln for ln in logs), logs)

    def test_804_definite_goal_clear_drops_the_roster_entry(self):
        # #804-review 🔴: a DEFINITE goal-clear (armed is False) means the stream
        # is no longer expected-armed -> drop it, so a deliberately retired stream
        # is never later falsely flagged dead (nor mis-drives a future resurrect).
        from watchdog import roster
        now = 1_000_000
        proj = self._dir()
        tpath = _write_marker_transcript(proj, self.CWD, "sess-cleared")
        sid = tpath.stem
        r = {}
        roster.upsert(r, self.CWD, sid, "full", now - 500)
        roster.save_roster(r)
        # A CLEARED goal_mark -> a DEFINITE not-armed verdict (glance.goal_armed
        # is False), not the transient armed-unknown None.
        state = {"goal_mark": {sid: {"off": 0,
                                     "mark": {"state": "cleared", "ts": now}}}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(now, run=tmux, projects_dir=proj, state=state,
                                 backlog_fetch=lambda cwd: 5)
        self.assertNotIn(self.CWD, roster.load_roster())

    def test_804_armed_pane_is_upserted_into_the_roster(self):
        # A CONFIRMED-armed candidate pane refreshes its durable roster entry, so
        # the census has an accurate expected-armed fact if the session later dies.
        from watchdog import roster
        now = 1_000_000
        proj = self._dir()
        tpath = _write_marker_transcript(proj, self.CWD, "sess-armed-roster")
        sid = tpath.stem
        old = now - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        # dark_watch's tail-proof goal_mark says the /goal IS armed (#486 G6).
        state = {"goal_mark": {sid: {"off": 0, "mark": {"state": "set", "ts": now}}}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="branch-merge"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(now, run=tmux, projects_dir=proj, state=state,
                                 backlog_fetch=lambda cwd: 5)
        reg = roster.load_roster()
        self.assertIn(self.CWD, reg)
        self.assertEqual(reg[self.CWD]["sid"], sid)
        self.assertEqual(reg[self.CWD]["authority"], "branch-merge")
        self.assertEqual(reg[self.CWD]["armed_ts"], now)

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
             authority="full", handled=None, enters_swallowed=0,
             authority_raises=None):
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
        # #618: authority_raises models the PRODUCTION None path (resolve_authority
        # never returns None — it defaults "full" — so None only arises from the
        # except branch); return_value=None models a defensive read of that None.
        auth_patch = (m.patch("airuleset.resolve_authority",
                              side_effect=authority_raises)
                      if authority_raises is not None
                      else m.patch("airuleset.resolve_authority",
                                   return_value=authority))
        with auth_patch:
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

    def test_814_delivered_unconfirmed_is_booked_not_retried(self):
        # #814 RED -- send_verified returns False but sets
        # out["delivered_unconfirmed"]=True (the Enter SUBMITTED and cleared the
        # box; only the transcript `user`-turn confirm raced -- the normal case
        # injecting into a cycling armed loop). The lane branch called
        # send_verified WITHOUT `out=`, so this delivered-but-unconfirmed submit
        # read as a FAILURE -> `submit-unverified (n/5)` backoff -> the retry
        # re-typed the IDENTICAL nudge next sweep -> the duplicate `lane-check:`
        # the owner saw (live gk 2026-09-01). GREEN: booked ONCE, not retried --
        # the exact #594 sibling wiring u_freshness/release_gap/queue_arrival have.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        # transcript_path=None: Enter clears the box (delivered) but writes NO
        # `user` turn -> send_verified False + out["delivered_unconfirmed"]=True.
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=None)
        rec = {}
        state = {}
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state=state, sleep_fn=lambda s: None)
        # booked as a DELIVERED nudge (delivered_unconfirmed -> _lane_record_nudge)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        # NOT misread as a failure: no submit-unverified backoff, streak untouched
        self.assertFalse(any("submit-unverified" in ln for ln in logs), logs)
        self.assertNotIn("lna", rec)
        # the shared cadence clock advanced (mark_sent) so a sibling defers
        self.assertEqual(
            state.get("nudge_cadence", {}).get(self.SID, {}).get("lane-occupancy"),
            now, state)

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
             m.patch.object(wd, "count_live_workers", return_value=(1, live_ev)):
            logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertFalse(any("skip:working-no-tasks" in ln for ln in logs), logs)
        # positive proof it proceeded past working-no-tasks into the batch
        # decision (#726: 1 live lane -> a batch is running -> skip:batch-running
        # is the next decision the path reaches).
        self.assertTrue(any("skip:batch-running" in ln for ln in logs), logs)

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

    def test_804_giveup_holds_during_backoff_window(self):
        # #804 mode-1: within the give-up backoff window the box still holds
        # (skip:gave-up) -- no nudge -- but the log now says it will RE-ARM.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": True, "lna": 0,
               "lgts": now - 100}   # gave up 100s ago, backoff[0]=1h not elapsed
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(owns)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip:gave-up (backoff" in ln for ln in logs), logs)

    def test_804_giveup_re_arms_a_nudge_after_the_backoff_elapses(self):
        # #804 mode-1 RED (pre-#804 this held `skip:gave-up` FOREVER): once the
        # backoff window elapses the give-up RE-ARMS one bounded nudge attempt --
        # a dead-stuck armed loop is never permanently silent again.
        #
        # #804-review 🔴: the rec carries the FROZEN landed-nudge signature every
        # real gave-up box has (`llast` >1h old, `lsw`=0, `lsb`=backlog) -- so the
        # re-arm MUST also pop `lsw`/`lsb`, else the #670 dedup swallows the nudge
        # at `skip:dedup-unchanged` and the "retry chain" is inert (the exact bug
        # the reviewer caught). This test now goes RED if the dedup-pop is dropped.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": True, "lna": 0,
               "lgts": now - (goal.GOAL_LANE_GIVEUP_BACKOFF_S[0] + 100),
               "llast": now - goal.GOAL_LANE_INTERVAL_S - 100,  # past the hourly cap
               "lsw": 0, "lsb": 5}                              # frozen dedup sig
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("giveup-backoff elapsed" in ln for ln in logs), logs)
        # the re-arm falls through to a fresh nudge that actually LANDS (not
        # swallowed by skip:dedup-unchanged)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertFalse(any("skip:dedup-unchanged" in ln for ln in logs), logs)
        # the backoff schedule widened for the NEXT give-up cycle
        self.assertEqual(rec.get("lgn"), 1, rec)

    def test_804_pre_804_latched_rec_without_lgts_starts_a_window(self):
        # #804-review 🟡: a rec latched BEFORE #804 shipped (lpinged, no lgts) --
        # every box already stuck at deploy -- must not hold forever with a lying
        # countdown; it starts the first backoff window instead of never re-arming.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": True, "lna": 0}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertTrue(owns)
        self.assertTrue(any("backoff window started" in ln for ln in logs), logs)
        self.assertEqual(rec.get("lgts"), now, rec)   # window anchored now
        self.assertEqual(tmux.sent, [])               # still held this sweep

    def test_branch_merge_box_also_nudges(self):
        # #618: a reduced-authority STREAM box (branch-merge — montalu/marek)
        # DOES run a parallel worktree fleet under /autopilot (SKILL fleet
        # default; owner's #618 expected behaviour), so it must get the
        # lane-occupancy nudge exactly like a full-authority box. The old
        # `authority != "full"` early-skip (a stale full-only assumption)
        # silently starved montalu1 of every saturation nudge — ZERO
        # lane-occupancy journal lines live. Rewritten from the former
        # `test_reduced_authority_is_deliberately_silent` (which locked the
        # wrong behaviour) with that justification.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      authority="branch-merge")
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_fork_no_merge_box_also_nudges(self):
        # #618: a fork-no-merge STREAM box (david) also fleets parallel
        # worktree lanes (each produces a fork branch + hand-off), so it too
        # must reach the nudge, never the silent authority skip.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      authority="fork-no-merge")
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)

    def test_unresolvable_authority_is_still_deliberately_silent(self):
        # #618: the remaining guard — an UNRESOLVABLE authority (resolve_authority
        # raised → None) is a degraded/unknown box, never a lane decision worth
        # journalling. Only None skips silently now; the three real profiles all
        # nudge.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      authority=None)
        self.assertEqual(logs, [])
        self.assertFalse(owns)
        self.assertEqual(tmux.sent, [])

    def test_authority_resolution_raising_is_silent(self):
        # #618: the PRODUCTION shape of the None case — resolve_authority RAISES
        # (its own try/except sets authority=None). Same silent, no-keystroke
        # skip as the returned-None case above.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      authority_raises=RuntimeError("boom"))
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

    def test_five_workers_big_backlog_is_silent(self):
        # 5 workers >= GOAL_LANE_SATURATION_WORKERS -> saturated -> silent.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch.object(wd, "count_live_workers", return_value=(5, [])):
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
             m.patch.object(wd, "count_live_workers", return_value=(5, [])):
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
    # BEFORE workers/backlog were counted, so a BUSY session (turns spinning ->
    # transcript mtime always fresh) never reached the nudge decision and
    # journalled NOTHING. #619 removed the empty-lane idle floor; every reaching
    # sweep logs its decision with numbers. (#726 retired the under-saturated
    # fill nudge -- a running batch is now skip:batch-running.)
    # ---------------------------------------------------------------- #

    def test_619_zero_worker_active_session_fires_no_more_skip_idle(self):
        # #619 OVERTURNS the pre-#619 "empty-lane keeps its 15-min idle floor"
        # lock: an active (fresh-transcript) 0-worker box with a backlog is the
        # busy-solo under-saturation the fill nudge must reach, so it FIRES instead
        # of logging skip:idle. `skip:idle` is retired for the empty-lane branch
        # (montalu1: 114x skip:idle/9h, 0 fill nudge). Keystroke safety is carried
        # by _lane_boundary_ok (only delivered at an idle prompt) + the hourly cap.
        now = 100000
        tmtime = now - 30  # active / fresh transcript
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    # ---------------------------------------------------------------- #
    # #611/#619 -- the 0-worker EMPTY-lane branch's 15-min idle floor was
    # STRUCTURALLY unreachable for a continuously serially-working armed
    # session (writes a turn every few min so `idle` never reaches 15m), yet
    # that is the WORST under-saturation (0 lanes + big backlog). #611 added a
    # WNT-escalated BYPASS; #619 removed the idle floor for the empty-lane
    # branch ENTIRELY (the bypass is subsumed -- the floor was structurally
    # self-suppressing AND the marker flaps so the 3-sweep escalation streak
    # rarely accumulated). The surviving delivery gates (boundary, recent-human,
    # draft-diff, hourly cooldown, MAX_NUDGES) carry the mid-dispatch safety. The
    # WNT gate still DEFERS a ⏳-0-lane box for a few sweeps then STOPS deferring
    # (the ESCALATE log), reaching the nudge like any other empty-lane sweep.
    # ---------------------------------------------------------------- #

    def test_611_wnt_escalated_zero_lane_fires_despite_fresh_transcript(self):
        # ⏳ marker, 0 render badges, 0 STRUCTURED live lanes, real backlog, FRESH
        # transcript (idle=30s), and the WNT gate stops deferring this sweep (wntd
        # seeded to max-1, the ESCALATE log) -> the empty-lane fill nudge FIRES.
        # (#619: it would fire even without the escalation now -- the idle floor is
        # gone -- but this locks that the ESCALATE branch still reaches the nudge.)
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

    def test_619_zero_lane_fires_without_wnt_escalation(self):
        # #619 OVERTURNS the #611 control ("non-escalated fresh 0-worker keeps the
        # idle floor"): the empty-lane fill nudge no longer depends on WNT
        # escalation to fire on a fresh transcript. A non-⏳ 0-worker box (the
        # working-no-tasks branch never fires, so the pre-#611 escalated bypass
        # would not apply) now FIRES anyway -- the 15-min idle floor the escalation
        # used to bypass is gone entirely, so the bypass mechanism is subsumed.
        now = 100000
        tmtime = now - 30  # fresh
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

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
        # #611 EXPLICIT-DECISION lock: the empty-lane branch is bounded by
        # GOAL_LANE_MAX_NUDGES -- after the budget it GIVES UP (one owner ping)
        # instead of nudging a perpetually-⏳-0-lane session forever. #620: the
        # give-up is now reset ONLY on lane appearance (workers>0), never on a
        # backlog change, so a box that ignored 2 fill nudges and never dispatched
        # stays given-up (needs a human) rather than re-arming every sweep.
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

    # ---------------------------------------------------------------- #
    # #619 / #620 -- busy-solo (0 lanes, big backlog, session solving
    # tickets INLINE so the transcript is always fresh). The empty-lane
    # 15-min idle floor was structurally unreachable here (114x skip:idle
    # /9h on montalu1, 0 fill nudge, #611 escalated-bypass dead because the
    # marker flaps), AND the give-up counter reset on every backlog change
    # (all 4 landed nudges logged (1/2), the give-up owner ping never fired).
    # ---------------------------------------------------------------- #

    def test_619_empty_lane_fires_despite_fresh_transcript_no_escalation(self):
        # #619 THE headline lock: a busy-solo empty-lane box (0 workers, big
        # backlog, FRESH transcript idle=30s, NON-⏳ marker so the #611 WNT
        # escalation never applies) must FIRE the fill nudge -- idle << 15m is
        # EXACTLY the busy-solo state the nudge must reach, and _lane_boundary_ok
        # (already passed above) is the real keystroke gate. RED: the empty-lane
        # 15-min idle floor early-returned skip:idle here.
        now = 100000
        tmtime = now - 30  # fresh: idle << 15min, no WNT escalation (non-⏳)
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertTrue(any("workers=0" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)

    def test_620_giveup_advances_despite_backlog_change(self):
        # #620 THE headline lock: the empty-lane give-up counter (ln) must
        # survive a BACKLOG CHANGE. A busy-solo box churns its backlog inline
        # (33->25); the pre-#620 _lane_idle_reset wiped `ln` on every change, so
        # count_gaveup (n>=MAX_NUDGES) was never reached and the give-up owner
        # ping never fired -- all nudges repeated as (1/2). A box already at the
        # give-up cap must GIVE UP even though the backlog differs from the last
        # nudge's baseline. RED: the idle-floor branch reset ln->0 + skip:idle.
        now = 100000
        tmtime = now - 30  # busy: fresh transcript, idle << 15min
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lnbk": 33}  # cap reached at old backlog
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 25, now,
                                      tmtime, rec=rec)   # backlog now 25 (changed)
        self.assertTrue(owns)
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)

    def test_620_giveup_latch_clears_when_a_lane_appears(self):
        # #620 -- the empty-lane give-up (ln + its lpinged latch) refreshes when
        # the box GETS a lane (workers>0 = "the nudge worked / it dispatched"),
        # NOT on a backlog change. A saturated sweep (no nudge fires, so
        # _lane_record_nudge never runs) with a latched give-up must still clear
        # it so a future give-up can re-escalate. RED: only _lane_clear_
        # effectiveness ran there (lineff/lnw/lnb), leaving ln + lpinged latched.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": True}
        with m.patch.object(wd, "count_live_workers", return_value=(5, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: 32,
                                          now, tmtime, rec=rec)
        self.assertTrue(any("saturated" in ln for ln in logs), logs)
        self.assertFalse(rec.get("lpinged"))
        self.assertFalse(rec.get("ln"))

    def test_620_lane_appearance_resets_giveup_even_with_no_backlog(self):
        # #620 (adversarial-review 🔵): _lane_count_giveup_reset is placed BEFORE
        # the boundary + backlog gates, so a lane appearance (workers>0) refreshes
        # the give-up latch even when the backlog reads None (a cold cache) or the
        # pane is busy. Mutation lock: if the reset moved back after the backlog
        # check, this backlog=None sweep would return first and leave ln/lpinged
        # latched -> a false give-up owner ping once a worker drains.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": True}
        with m.patch.object(wd, "count_live_workers", return_value=(2, [])):
            logs, owns, tmux = self._call(GOAL_ARMED_STRIP_CAP, lambda cwd: None,
                                          now, tmtime, rec=rec)
        self.assertTrue(any("no measurable open backlog" in ln for ln in logs), logs)
        self.assertFalse(rec.get("lpinged"))   # reset fired BEFORE the backlog skip
        self.assertFalse(rec.get("ln"))

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

    def test_busy_empty_lane_stash_abort_still_reaches_giveup(self):
        # #442-review F1 / #726: the stash-abort give-up (a delivery-mechanics
        # bound) was made structurally UNREACHABLE on a busy session by the
        # session-active reset. A busy session always has idle < GOAL_LANE_IDLE_S,
        # so the reset zeroed `lna` every sweep before the streak could reach the
        # cap. A permanently-aborting empty-lane (0 workers -> batch CLOSED ->
        # nudge fires; a parked draft occupies the stash slot) on a busy box must
        # still accumulate the streak across sweeps and fire the ONE give-up ping.
        # (#726 retired the under-saturated nudge, so this now exercises the
        # empty-lane path -- the only branch that reaches stash delivery.)
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
             m.patch.object(wd, "count_live_workers", return_value=(0, [])), \
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

    def test_620_giveup_does_not_rearm_on_active_sweep(self):
        # #620 OVERTURNS the pre-#620 "active empty-lane sweep re-arms the give-up"
        # lock: an active (fresh-transcript) sweep with the give-up already reached
        # must HOLD it and fire GAVE UP, NOT re-arm the counter. The backlog-change
        # re-arm (which a busy-solo box tripped every sweep, wiping `ln`) is gone;
        # the counter now resets only on lane appearance (workers>0). The
        # stash-abort streak is no longer reset here either (it self-heals via the
        # #479 park + successful-delivery reset).
        now = 100000
        tmtime = now - 30  # active
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lna": 3, "lpinged": True}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now,
                                      tmtime, rec=rec)
        self.assertTrue(owns)
        self.assertEqual(rec.get("ln"), goal.GOAL_LANE_MAX_NUDGES)  # HELD, not re-armed
        # already escalated (lpinged) -> holds, never re-arms + re-nudges
        self.assertTrue(any("skip:gave-up" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)
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
             m.patch.object(wd, "count_live_workers", return_value=(0, [])), \
             m.patch.object(wd, "deliver_with_stash", side_effect=fake_stash):
            for i in range(10):                     # 10 sweeps, 60s apart
                now = start + i * 60
                tmtime = now - 30                    # busy empty-lane (#726)
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
        # The gk latch: STASH aborts at the cap, already pinged, park long
        # elapsed, pane now a clean deliverable idle prompt. The nudge must
        # RE-PROBE and DELIVER, never latch on skip:gave-up forever. #726: driven
        # on the empty-lane (0-worker) path -- the only branch that still reaches
        # stash delivery -- with ln BELOW the count give-up cap so ONLY the
        # stash-abort give-up (lna) is exercised, isolating the #511 re-probe.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 0, "lna": goal.GOAL_LANE_MAX_STASH_ABORTS, "lpinged": True,
               "lnpark": now - 10000, "llast": now - 40000}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 37, now, tmtime,
                                      rec=rec)
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
        # reaches the park instead of returning early. #726: empty-lane path,
        # ln below the count give-up cap (see the sibling above).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"ln": 0, "lna": goal.GOAL_LANE_MAX_STASH_ABORTS, "lpinged": True,
               "lnpark": now + 500, "llast": now - 40000}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 37, now, tmtime,
                                      rec=rec)
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
        # backlog 2 is below the floor of 3 for a FRESHLY-idle box (idle ~15min,
        # < GOAL_LANE_INTERVAL_S) -> skip:min-backlog. #804 mode-4 lowers the
        # floor to 1 only once idle > 1h (see test_804_min_backlog_floor... below).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 2, now, tmtime)
        self.assertTrue(any("skip:min-backlog" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_804_min_backlog_floor_drops_to_1_after_an_hour_idle(self):
        # #804 mode-4 RED: a loop that has stood idle > 1h over just 1 workable
        # ticket is a stuck loop, not freshly-idle churn -- the #530 floor of 3 was
        # parking it FOREVER (#791 "stojí navždy by design"). Once idle > 1h the
        # floor drops to 1, so the box IS poked.
        now = 100000
        tmtime = now - goal.GOAL_LANE_INTERVAL_S - 100   # idle > 1h
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 1, now, tmtime)
        self.assertTrue(owns)
        self.assertFalse(any("skip:min-backlog" in ln for ln in logs), logs)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)

    def test_804_freshly_idle_lone_ticket_still_skips(self):
        # The anti-storm floor is UNCHANGED for a freshly-idle box (idle ~15min):
        # a lone ticket there is still not batch-worthy (#530 preserved).
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100   # idle ~15min, < 1h
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 1, now, tmtime)
        self.assertTrue(any("skip:min-backlog" in ln for ln in logs), logs)
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

    def test_620_landed_nudge_advances_giveup_counter_no_lnbk(self):
        # #620 OVERTURNS the pre-#620 "landed nudge records the lnbk baseline"
        # lock: a landed empty-lane nudge advances the give-up counter `ln` and NO
        # LONGER writes the retired `lnbk` baseline (the backlog-change reset it fed
        # is gone). Mutation lock: a fresh seed -> one landed nudge -> ln == 1.
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {}
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 7, now, tmtime,
                                      rec=rec)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(rec.get("ln"), 1, rec)
        self.assertIsNone(rec.get("lnbk"), rec)

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

    def test_620_giveup_holds_and_fires_when_backlog_unchanged(self):
        # #620: an empty-lane sweep with the give-up already reached HOLDS the
        # counter and fires GAVE UP, whatever the backlog (here unchanged from the
        # seed). The pre-#620 idle-branch reset zeroed `ln` on transcript freshness;
        # #619 removed that branch and the give-up now resets only on lane
        # appearance. The stash-abort streak (lna) is NOT reset here anymore -- it
        # self-heals via the #479 park + successful-delivery reset.
        now = 100000
        tmtime = now - 30  # active / fresh transcript
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lna": 3}  # not yet pinged -> fires GAVE UP
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 5, now, tmtime,
                                      rec=rec)
        self.assertEqual(rec.get("ln"), goal.GOAL_LANE_MAX_NUDGES)  # HELD
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
        self.assertFalse(any("skip:idle" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [])

    def test_620_giveup_survives_a_backlog_change(self):
        # #620 OVERTURNS the pre-#620 #530 "backlog change re-arms the give-up": a
        # busy-solo box churns its backlog inline, so a backlog change must NOT
        # reset the give-up (that reset wiped `ln` between every nudge -> all
        # nudges (1/2), give-up never fired). The give-up HOLDS across a backlog
        # change and fires GAVE UP; it resets only on lane appearance (workers>0).
        now = 100000
        tmtime = now - 30
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lna": 0}  # not yet pinged -> fires GAVE UP
        logs, owns, tmux = self._call(GOAL_ARMED_CAP, lambda cwd: 9, now, tmtime,
                                      rec=rec)   # backlog 9 (changed from any baseline)
        self.assertEqual(rec.get("ln"), goal.GOAL_LANE_MAX_NUDGES)  # HELD despite change
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)

    def test_620_giveup_survives_active_quiet_oscillation(self):
        # End-to-end: the give-up SURVIVES an active/quiet oscillation and fires
        # GAVE UP. #620: the give-up now resets only on lane appearance (workers>0),
        # never on transcript freshness or a backlog change -- so a 0-worker box
        # holds `ln` at the cap across both an active and a quiet sweep. (Pre-#620
        # the active sweep's idle-branch reset wiped `ln` on freshness, so the
        # give-up was structurally unreachable on a live supervisor.)
        now = 100000
        rec = {"ln": goal.GOAL_LANE_MAX_NUDGES, "lpinged": False, "lna": 0}
        # active sweep (fresh transcript, idle << 15min): HOLDS the counter
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
    """#442/#726 — the (empty-lane, batch-CLOSED) nudge TEXT must teach the BATCH
    doctrine (skills/autopilot SKILL.md, #723/#724), not the retired continuous
    saturation: parallel worktree worker dispatch, the account-wide cap of 8,
    NO refill while a batch runs, and serialize-only integration. (The
    comprehensive batch-language lock lives in
    tests/test_batch_orchestration.py::TestWatchdogLaneNudgeIsBatch.)"""

    def test_nudge_text_commands_batch_dispatch_doctrine(self):
        rendered = goal.GOAL_LANE_NUDGE_TEXT % (7, 2)
        low = rendered.lower()
        self.assertIn("worktree", low)
        self.assertIn("paraleln", low)
        self.assertIn("sériovo", low)
        # #726: batch doctrine, not the retired "fill lanes" continuous refill.
        self.assertIn("várk", low)
        self.assertIn("refill", low)
        self.assertNotIn("dispatchni teraz ďalšie", low)
        # #726 (review finding 2): the within-batch bound is the canonical
        # post-#723 resource-signal backoff, NOT the retired #442 fixed "cap 8".
        self.assertIn("rate-limit", low)
        self.assertNotIn("8", rendered)

    def test_saturation_workers_is_a_named_constant(self):
        # #481: the batch ceiling is a named constant. #729 removed the
        # memory floor constant (GOAL_LANE_MIN_MEM_AVAIL_MB) that this test
        # used to also assert -- the memory OOM subsystem is gone.
        self.assertEqual(goal.GOAL_LANE_SATURATION_WORKERS, 5)

    def test_lane_live_convo_window_is_minutes_not_the_30min_blanket(self):
        self.assertLessEqual(goal.GOAL_LANE_LIVE_CONVO_S, 5 * 60)
        self.assertLess(goal.GOAL_LANE_LIVE_CONVO_S,
                        wd.GOAL_AUTOARM_RECENT_HUMAN_S)


# --------------------------------------------------------------------------- #
# 7. scan_goal_markers simplification — no `arm_after` key any more, and no
#    crash on a line that would have needed `_entry_asks_to_arm`/
#    `_GOAL_ASK_PROBE` (both deleted).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 8. #731 -- per-request delivery-attempt CAP + prompt-cleanup on drop + the
#    tmux-client-input human signal + arm-confirm-fail diagnostics. The
#    montalu4 retype livelock: verify-failed / stash-abort alternate with NO
#    shared cap, each sweep re-typing ~3.4 kB into the prompt, the human
#    deleting the paste never seen by the transcript-based gate.
# --------------------------------------------------------------------------- #
class _ClientActiveFake(DeliverGoalFakeTmux):
    """#731 D3 -- a DeliverGoalFakeTmux that answers `tmux list-clients -F
    '#{client_activity}'` with the given attached-client epoch(s). Everything
    else delegates to the base fake (no other behaviour changes)."""

    def __init__(self, *a, client_epochs=(), **kw):
        super().__init__(*a, **kw)
        self.client_epochs = list(client_epochs)

    def __call__(self, argv, timeout=8):
        if "list-clients" in " ".join(argv):
            if not self.client_epochs:
                return ""
            return "\n".join(str(int(e)) for e in self.client_epochs) + "\n"
        return super().__call__(argv, timeout)


# #737 -- a long /goal renders only its TAIL rows once the input box scrolls, so
# the VISIBLE box is a mid-payload contiguous SUBSTRING with no `/goal ` prefix
# (the montalu6/montalu3/gk "text zaciname uprostred vety" signature). These
# constants drive the substring-ownership + cleanup tests below.
_GOAL_PAYLOAD_737 = "/goal " + " ".join(
    ("STOP CONDITIONS all issues closed AND CI all-green AND PR mergeable clean "
     "work every issue one at a time until the backlog is empty".split()) * 8)
_SCROLLED_737 = " ".join(_GOAL_PAYLOAD_737.split()[30:80])    # >=80 chars, no prefix
_SHORT_HUMAN_737 = " ".join(_GOAL_PAYLOAD_737.split()[30:34])  # a <80-char substring
_FOREIGN_737 = ("toto je moja vlastna dlha sprava pre kolegu ktoru pisem uz "
                "dvadsat minut a rozhodne nechcem aby mi ju nejaky watchdog "
                "automaticky zmazal lebo to je moj cisto osobny text a nie goal")


class TestGoalDeliveryAttemptCap731(unittest.TestCase):
    CWD = "/home/newlevel/devel/capdrop"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _cap(self):
        # the design constant, read defensively so this RED test runs BEFORE
        # the constant exists and still demonstrates the behaviour difference.
        return getattr(goal, "GOAL_DELIVERY_ATTEMPT_CAP", 3)

    # -------- D1: the retype loop is bounded by the attempt cap ---------- #
    def test_verify_failed_retype_is_bounded_by_attempt_cap(self):
        proj = self._dir()
        sid = "sess-cap-d1"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        # arm_on_submit=False -> every submit is read as a plain prompt, the
        # goal never arms -> skip:verify-failed EVERY sweep (the #720 shape).
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        cap = self._cap()
        last_logs = []
        for t in range(cap + 1):
            last_logs = goal.goal_sweep(2000 + t, run=tmux, projects_dir=proj,
                                        requests_path=self.reqp,
                                        send_fn=lambda m, **k: None,
                                        sleep_fn=lambda *a, **k: None)
        # AFTER the fix: at most `cap` keystroke deliveries, then a terminal
        # drop. BEFORE the fix: cap+1 (types forever), request never cleared.
        self.assertLessEqual(len(tmux.typed_texts()), cap,
                             "retype must be bounded by the attempt cap; typed "
                             "%d times" % len(tmux.typed_texts()))
        self.assertTrue(any("drop:attempt-cap" in ln for ln in last_logs),
                        last_logs)
        self.assertEqual(goal.load_goal_requests(self.reqp), {},
                         "the capped request must be terminally cleared")

    def test_attempt_cap_pings_once_for_normal_origin(self):
        proj = self._dir()
        sid = "sess-cap-ping"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        pings = []
        for t in range(self._cap() + 1):
            goal.goal_sweep(2000 + t, run=tmux, projects_dir=proj,
                            requests_path=self.reqp,
                            send_fn=lambda m, **k: pings.append((m, k)),
                            sleep_fn=lambda *a, **k: None)
        capped = [k for _m, k in pings
                  if str(k.get("dedup_key", "")).startswith("goalarm-attempt-cap")]
        self.assertEqual(len(capped), 1,
                         "a normal-origin attempt-cap drop pings exactly once")

    def test_attempt_cap_silent_for_stale_rearm(self):
        proj = self._dir()
        sid = "sess-cap-stale"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "branch-merge",
                                 now=1000, path=self.reqp, origin="stale-rearm")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        pings = []
        for t in range(self._cap() + 1):
            goal.goal_sweep(2000 + t, run=tmux, projects_dir=proj,
                            requests_path=self.reqp,
                            send_fn=lambda m, **k: pings.append((m, k)),
                            sleep_fn=lambda *a, **k: None)
        capped = [k for _m, k in pings
                  if str(k.get("dedup_key", "")).startswith("goalarm-attempt-cap")]
        self.assertEqual(capped, [],
                         "a stale-rearm attempt-cap drop is SILENT (#675)")
        self.assertEqual(goal.load_goal_requests(self.reqp), {},
                         "silent drop still clears the request")

    # -------- D2: cap-drop cleans up OUR leftover, never a foreign one ---- #
    def _preseed_at_cap(self, sid, box, origin="self-callback"):
        """A request already AT the cap with `box` sitting in the pane and
        janitor provenance present (a prior delivery attempt marked it)."""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin=origin)
        d = goal.load_goal_requests(self.reqp)
        d[sid]["dl_fails"] = self._cap()
        d[sid]["dl_last"] = "skip:verify-failed"
        Path(self.reqp).write_text(json.dumps(d))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   initial_box=box)
        state = {"janitor_watch": {"%9": 2000}}
        return proj, tmux, state

    def test_attempt_cap_clears_own_leftover_in_box(self):
        sid = "sess-cap-own"
        proj, tmux, state = self._preseed_at_cap(sid, "/goal x")
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=self.reqp, state=state,
                                   send_fn=lambda m, **k: None,
                                   sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.box, "",
                         "our own stranded /goal must be CLEARED at the cap drop")
        self.assertTrue(any("leftover=cleared" in ln for ln in logs), logs)

    def test_attempt_cap_leaves_foreign_leftover_untouched(self):
        sid = "sess-cap-foreign"
        foreign = ("moja vlastna dlha rozpisana sprava ktoru vobec nechcem "
                   "aby mi ju hocikto zmazal je to cisto moj text a nie goal")
        proj, tmux, state = self._preseed_at_cap(sid, foreign)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=self.reqp, state=state,
                                   send_fn=lambda m, **k: None,
                                   sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.box, foreign,
                         "a FOREIGN draft must be left completely untouched")
        self.assertTrue(any("leftover=not-ours" in ln for ln in logs), logs)

    def test_attempt_cap_leftover_not_cleared_without_provenance(self):
        # #731-review F1 -- own /goal text in the box but NO janitor provenance
        # (no prior watchdog watch mark): _janitor_recover no-ops, the box stays
        # dirty, so the journal must NOT claim leftover=cleared (the #134/#726
        # honesty class). The leftover is READ BACK from the janitor verdict.
        sid = "sess-cap-noprov"
        proj, tmux, _state = self._preseed_at_cap(sid, "/goal x")
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=self.reqp, state={},  # no watch mark
                                   send_fn=lambda m, **k: None,
                                   sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.box, "/goal x",
                         "no provenance -> the janitor must not touch the box")
        self.assertFalse(any("leftover=cleared" in ln for ln in logs),
                         "must NOT assert cleared when the box was untouched: %s"
                         % logs)

    def test_attempt_cap_does_not_clear_a_busy_pane(self):
        # #731-review F3 -- the cap-drop cleanup must NOT Escape/clear a pane
        # rendering a live turn (a busy / non-input boundary): design B's
        # `_classify_boundary=="input"` gate.
        sid = "sess-cap-busy"
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        d = goal.load_goal_requests(self.reqp)
        d[sid]["dl_fails"] = self._cap()
        Path(self.reqp).write_text(json.dumps(d))
        # a BUSY render (a running turn) — never a clean input boundary.
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_BUSY_CAP)
        state = {"janitor_watch": {"%9": 2000}}
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=self.reqp, state=state,
                                   send_fn=lambda m, **k: None,
                                   sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.sent, [],
                         "a busy pane must get ZERO recovery keystrokes")
        self.assertTrue(any("drop:attempt-cap" in ln for ln in logs), logs)
        self.assertTrue(any("leftover=skipped" in ln for ln in logs), logs)

    # -------- D3: tmux client input defers a WATCHDOG re-arm (#752) ------- #
    def test_client_active_defers_watchdog_rearm(self):
        # #752 -- the client-input human signal now vetoes ONLY a watchdog-
        # initiated re-arm (dark/stale/auth), never the user's own
        # `self-callback` /autopilot arm. A `dark-rearm` into a pane whose
        # attached client typed recently is deferred (via the recent-human
        # gate, which since #731 includes the SAME client-input signal), so no
        # keystroke lands and the request stays pending.
        proj = self._dir()
        sid = "sess-cap-client"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=2000, path=self.reqp, origin="dark-rearm")
        # an attached client with input 10 s ago -> a live human -> defer.
        tmux = _ClientActiveFake([("%9", "claude", self.CWD, "111")],
                                 GOAL_IDLE_CAP, model_type=True,
                                 client_epochs=[1990])
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda m, **k: None,
                               sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.typed_texts(), [],
                         "a live tmux client vetoes a watchdog re-arm keystroke")
        self.assertTrue(any("skip:" in ln for ln in logs), logs)
        self.assertIn(sid, goal.load_goal_requests(self.reqp),
                      "a deferred re-arm leaves the request pending")

    def test_no_clients_does_not_veto(self):
        proj = self._dir()
        sid = "sess-cap-noclient"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        # zero attached clients (headless) -> no veto, delivery proceeds.
        tmux = _ClientActiveFake([("%9", "claude", self.CWD, "111")],
                                 GOAL_IDLE_CAP, model_type=True, client_epochs=[])
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda m, **k: None,
                               sleep_fn=lambda *a, **k: None)
        self.assertFalse(any("skip:client-active" in ln for ln in logs), logs)
        self.assertTrue(any("OK (goal-sweep)" in ln for ln in logs), logs)

    # -------- D4: arm-confirm-fail diagnostics ---------------------------- #
    def test_arm_confirm_fail_writes_diagnostic_line(self):
        proj = self._dir()
        sid = "sess-cap-diag"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                            requests_path=self.reqp,
                            send_fn=lambda m, **k: None,
                            sleep_fn=lambda *a, **k: None)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("ARM-CONFIRM-FAIL", log)
        for field in ("boundary=", "busywait=", "armed=", "box="):
            self.assertIn(field, log, "diagnostic must carry %s" % field)

    # -------- int-guard: a string dl_fails across the JSON boundary ------- #
    def test_dl_fails_string_value_is_int_guarded(self):
        sid = "sess-cap-strint"
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        d = goal.load_goal_requests(self.reqp)
        d[sid]["dl_fails"] = str(self._cap())     # a STRING across the boundary
        Path(self.reqp).write_text(json.dumps(d))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        # must not crash, and must still treat >= cap as a terminal drop.
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda m, **k: None,
                               sleep_fn=lambda *a, **k: None)
        self.assertTrue(any("drop:attempt-cap" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})

    # ================ #737 substring ownership + arm-confirm cleanup ======= #
    def _preseed_payload_at_cap(self, sid, box, payload=_GOAL_PAYLOAD_737):
        """A request AT the cap whose OWN text is the full `payload`, with `box`
        sitting in the pane (a SCROLLED substring / foreign draft / short human
        draft) and janitor provenance present."""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, payload, "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        d = goal.load_goal_requests(self.reqp)
        d[sid]["dl_fails"] = self._cap()
        Path(self.reqp).write_text(json.dumps(d))
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True, initial_box=box)
        return proj, tmux, {"janitor_watch": {"%9": 2000}}

    def _cap_drop(self, proj, tmux, state):
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            return goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=self.reqp, state=state,
                                   send_fn=lambda m, **k: None,
                                   sleep_fn=lambda *a, **k: None)

    def test_cap_drop_clears_scrolled_own_leftover(self):
        # #737 B -- a SCROLLED long /goal is a mid-payload SUBSTRING (no /goal
        # prefix). Pre-fix `_goal_box_kind` reads it "other" -> livelock, box
        # dirty forever. The substring proof recognizes it as ours -> the
        # #731 cap-drop CLEARS it (heals the montalu6/montalu3 leftover).
        sid = "sess-737-scroll"
        proj, tmux, state = self._preseed_payload_at_cap(sid, _SCROLLED_737)
        logs = self._cap_drop(proj, tmux, state)
        self.assertEqual(tmux.box, "",
                         "a SCROLLED own /goal leftover must be recognized + cleared")
        self.assertTrue(any("leftover=cleared" in ln for ln in logs), logs)

    def test_cap_drop_leaves_scrolled_foreign_untouched(self):
        # #737 CONTROL -- a long FOREIGN draft is NOT a substring of our /goal,
        # so it is never recognized as ours -> left completely untouched even
        # with provenance present (fail-safe: foreign draft nikdy).
        sid = "sess-737-foreign"
        proj, tmux, state = self._preseed_payload_at_cap(sid, _FOREIGN_737)
        logs = self._cap_drop(proj, tmux, state)
        self.assertEqual(tmux.box, _FOREIGN_737,
                         "a foreign draft is never a substring of our /goal")
        self.assertTrue(any("leftover=not-ours" in ln for ln in logs), logs)

    def test_cap_drop_leaves_short_human_substring_untouched(self):
        # #737 CONTROL -- a SHORT human draft sharing words with the template is
        # BELOW the >=80-char substring floor -> never a false positive.
        sid = "sess-737-short"
        proj, tmux, state = self._preseed_payload_at_cap(sid, _SHORT_HUMAN_737)
        self.assertLess(len(_SHORT_HUMAN_737), 80, "control must be sub-threshold")
        logs = self._cap_drop(proj, tmux, state)
        self.assertEqual(tmux.box, _SHORT_HUMAN_737,
                         "a sub-threshold human draft must be left untouched")
        self.assertFalse(any("leftover=cleared" in ln for ln in logs), logs)

    def test_arm_confirm_fail_cleans_own_scrolled_leftover(self):
        # #737 A -- after a verify-failed arm the box holds our SCROLLED /goal.
        # `_log_arm_confirm_fail` must CLEAN it (first-person provenance: the box
        # was proven bare just before we typed) and log ARM-CONFIRM-CLEANUP. RED:
        # pre-fix it is forensics-only, the box stays dirty, no cleanup line.
        proj = self._dir()
        sid = "sess-737-armclean"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   initial_box=_SCROLLED_737)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(sid, self.CWD, _GOAL_PAYLOAD_737, "%9", tmux)
        self.assertEqual(tmux.box, "",
                         "arm-confirm-fail must clean our own scrolled /goal leftover")
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("ARM-CONFIRM-CLEANUP", log)

    def test_arm_confirm_fail_declines_foreign_leftover(self):
        # #737 A/D CONTROL -- a foreign draft in the box after an arm-fail is NOT
        # a substring of our /goal, so cleanup DECLINES and the draft survives.
        proj = self._dir()
        sid = "sess-737-armforeign"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   initial_box=_FOREIGN_737)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(sid, self.CWD, _GOAL_PAYLOAD_737, "%9", tmux)
        self.assertEqual(tmux.box, _FOREIGN_737,
                         "a foreign draft must be left untouched after arm-fail")
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("cleanup=declined", log)

    def test_janitor_recognizes_scrolled_leftover_in_occupied_slot(self):
        # #737 B -- the livelock exit: an OCCUPIED stash slot + a SCROLLED own
        # /goal in the box (`stash-abort-slot-occupied` forever) is recognized
        # via `own_payload` -> clear-and-pop (dry-run READY line). Pre-fix the
        # janitor has no `own_payload` param and reads the scrolled head as a
        # foreign occupant -> no recovery ordered.
        cap = ("● Hotovo.\n────\n❯ %s\n────\n  ctx ░░  %s\n"
               % (_SCROLLED_737, wd.STASH_MARKER))
        logs = wd._janitor_recover(
            lambda *a, **k: "", {}, "%9", self.CWD, cap, "loc:0.0",
            None, True, lambda *a, **k: None,
            state={"janitor_watch": {"%9": 2000}}, now=2000,
            own_payload=_GOAL_PAYLOAD_737)
        self.assertTrue(any("would attempt clear-and-pop" in ln for ln in logs),
                        logs)

    def test_janitor_scrolled_leftover_without_provenance_is_untouched(self):
        # #737 CONTROL -- the `own_payload` substring proof only decides WHICH
        # action; the provenance gate still decides WHETHER to act. A scrolled
        # own /goal with NO janitor watch mark + NO park record is left completely
        # untouched (no recovery ordered), even though it IS a substring of the
        # payload. Locks the fail-safe direction of the new own_payload path.
        cap = ("● Hotovo.\n────\n❯ %s\n────\n  ctx ░░  %s\n"
               % (_SCROLLED_737, wd.STASH_MARKER))
        logs = wd._janitor_recover(
            lambda *a, **k: "", {}, "%9", self.CWD, cap, "loc:0.0",
            None, True, lambda *a, **k: None,
            state={}, now=2000,               # NO janitor_watch / stash_parks
            own_payload=_GOAL_PAYLOAD_737)
        self.assertEqual(logs, [],
                         "no provenance -> the janitor must not act, even on a "
                         "recognized own scrolled leftover")

    def test_delivery_attempt_cap_constant_is_a_small_debounce(self):
        self.assertEqual(goal.GOAL_DELIVERY_ATTEMPT_CAP, 3)
        self.assertIn("skip:verify-failed", goal._GOAL_KEYSTROKE_SKIPS)
        self.assertIn("skip:stash-abort", goal._GOAL_KEYSTROKE_SKIPS)
        self.assertNotIn("skip:stash-abort-slot-occupied",
                         goal._GOAL_KEYSTROKE_SKIPS)
        self.assertEqual(wd.GOAL_CLIENT_INPUT_VETO_S, 300)


if __name__ == "__main__":
    unittest.main()
