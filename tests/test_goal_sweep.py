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


def _rearm_ok(cwd):
    """A resolvable /goal template for a full-authority pane -- the own-template
    reference dark-watch compares against (module fn, not a lambda: E731)."""
    return ("/goal x", "full")


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

# --------------------------------------------------------------------------- #
# 5. goal_dark_watch — job 20 half 1: REPORTS a lost goal once per episode after
#    a #524 CONFIRMED death, never re-arms (#1324), silent on cleared/no-marker,
#    shared janitor recovery runs first.
# --------------------------------------------------------------------------- #

class TestGoalDarkWatch(unittest.TestCase):
    CWD = "/home/newlevel/devel/darkwatch"

    def setUp(self):
        # Isolate the goal-requests/-sync files from the live systemd watchdog
        # (a test process must never race the real ~/.claude/goal-requests.json);
        # the tests assert dark-watch never writes a request (#1324).
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
    # #459/#524/#1324 -- a dark goal is REPORTED (journalled + ONE suppressed
    # ping per episode) only after the #524 confirmation run; it is NEVER
    # re-armed (owner 14.11.2026: "no goal visible" = autopilot off).
    # ----------------------------------------------------------------- #

    def _dark(self, sid):
        """A dark-goal fixture: transcript+`Goal set:` marker, an idle pane
        with NO `◎ /goal` footer (pane_goal_armed -> False)."""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        return proj, tmux

    def _sweep(self, tmux, proj, state, sent, now, rearm=_rearm_ok, dry_run=False):
        return goal.goal_dark_watch(now, run=tmux,
                                    send_fn=lambda m, **k: sent.append((m, k)),
                                    projects_dir=proj, state=state,
                                    sleep_fn=lambda s: None, rearm_fn=rearm,
                                    dry_run=dry_run)

    def _until_lost(self, tmux, proj, state, sent, now, limit=60):
        """Sweep 100 s apart until the `goal-lost:dark` line; returns (now, logs)."""
        for _ in range(limit):
            now += 100
            logs = self._sweep(tmux, proj, state, sent, now)
            if any("goal-lost:dark" in ln for ln in logs):
                return now, logs
        self.fail("the confirmation run never completed")

    def test_reported_only_after_the_confirmation_run(self):
        proj, tmux = self._dark("sess-dark-2")
        sent, state = [], {}
        self._sweep(tmux, proj, state, sent, 1000)        # first observation
        self._sweep(tmux, proj, state, sent, 1100)        # clean_run=1
        self.assertEqual(sent, [], "no report before the #524 run confirms")
        now, logs = self._until_lost(tmux, proj, state, sent, 1100)
        self.assertGreaterEqual(now - 1100, goal.GOAL_DARK_CONFIRM_MIN_SPAN_S)
        self.assertEqual(len(sent), 1, "ONE report once the run confirms")
        self.assertTrue(any("NOT re-armed" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(), {}, "never a re-arm request")
        self.assertEqual(tmux.sent, [], "never a keystroke")

    def test_524_idle_alive_flicker_is_never_reported(self):
        # #524: a 75-min idle-but-ALIVE session whose glyph flickers back armed
        # (montalu 2026-08-16) is never a lost goal: two dark sweeps, then the
        # ARMED read resets the run (VETO-ALIVE).
        proj = self._dir()
        sid = "sess-524-idle-alive"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        idle = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        armed = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP)
        state, sent = {}, []
        self._sweep(idle, proj, state, sent, 100000)
        self._sweep(idle, proj, state, sent, 100071)
        self._sweep(idle, proj, state, sent, 100142)
        logs = self._sweep(armed, proj, state, sent, 100213)
        self.assertTrue(any("VETO-ALIVE:render-armed" in ln for ln in logs), logs)
        self.assertEqual(sent, [], "an idle-alive flicker is never reported")
        self.assertEqual(goal.load_goal_requests(), {})
        self.assertEqual(idle.sent + armed.sent, [], "zero keystrokes")

    def test_mtime_advance_vetoes_the_confirmation_run(self):
        # #524 -- an advancing transcript mtime is a structured LIVENESS proof:
        # the run is VETOED (VETO-ALIVE:mtime-advanced) and reset.
        proj, tmux = self._dark("sess-mtime-veto")
        tpath = next(proj.rglob("sess-mtime-veto.jsonl"))
        sent, state = [], {}

        def sweep(now, mtime):
            os.utime(tpath, (mtime, mtime))
            return self._sweep(tmux, proj, state, sent, now)

        sweep(1000, 500)           # debounce (first observation), mtime frozen
        sweep(1100, 500)           # clean_run=1
        sweep(1200, 500)           # clean_run=2
        logs = sweep(1300, 550)    # mtime ADVANCED -> VETO-ALIVE, run reset
        self.assertTrue(any("VETO-ALIVE:mtime-advanced" in ln for ln in logs), logs)
        self.assertNotIn("sess-mtime-veto", state.get("goal_dark_confirm", {}),
                         "the liveness veto resets the confirmation run")
        self.assertEqual(sent, [])
        self.assertEqual(tmux.sent, [], "zero keystrokes")

    def _flicker_run(self, sid, flicker_cap, n=24):
        # A dark loop whose footer reads `flicker_cap` (armed True / busy None)
        # on every 4th sweep and GOAL_IDLE_CAP (dark) otherwise -- the run never
        # gets MIN_READS consecutive clean-dark reads, so it is never reported.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        idle = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        flick = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], flicker_cap)
        state, sent, now = {}, [], 1000
        for i in range(n):
            now += 100
            self._sweep(flick if (i % 4 == 3) else idle, proj, state, sent, now)
        return idle, flick, sent

    def test_armed_flicker_during_accumulation_is_never_reported(self):
        # #524-review (mutation-locked): each armed read RESETS the run. TEETH:
        # removing `confirm_state.pop` on the armed-True branch reports it.
        idle, armed, sent = self._flicker_run("sess-armed-flicker", GOAL_ARMED_CAP)
        self.assertEqual(sent, [], "a periodically-armed (alive) loop is never reported")
        self.assertEqual(idle.sent + armed.sent, [], "zero keystrokes")

    def test_none_read_breaks_the_consecutive_confirmation_run(self):
        # #524-review (mutation-locked): an undeterminable footer (None) is NOT
        # a clean-dark read. TEETH: removing the clean_run reset reports it.
        idle, busy, sent = self._flicker_run("sess-none-reset", GOAL_BUSY_CAP)
        self.assertEqual(sent, [], "a periodically-undeterminable loop never confirms")
        self.assertEqual(idle.sent, [], "zero keystrokes")

    def test_retired_rearm_state_stores_are_dropped(self):
        # #1324 -- the retired re-arm machinery's state stores leave the state.
        proj, tmux = self._dark("sess-live")
        state = {key: {"x": [1]} for key in goal._GOAL_RETIRED_STATE_KEYS}
        self._sweep(tmux, proj, state, [], 1_000_000)
        for key in goal._GOAL_RETIRED_STATE_KEYS:
            self.assertNotIn(key, state, key)

    def test_first_ping_text_and_dedup_key_are_byte_for_byte_403(self):
        # #459 preserves #403's FIRST ping exactly: the "zomrelo potichu" text and
        # the goal-dark:sid:mark dedup_key WITHOUT a :count suffix, so a legacy
        # dedup marker never yields a duplicate across the deploy boundary.
        proj, tmux = self._dark("sess-dark-firstping")
        sent, state = [], {}
        self._sweep(tmux, proj, state, sent, 1000)
        self._until_lost(tmux, proj, state, sent, 1000)
        self.assertEqual(len(sent), 1)
        first_msg, kw = sent[0]
        self.assertIn("zomrelo potichu", first_msg)
        self.assertNotIn("STÁLE", first_msg)
        self.assertEqual(kw["dedup_key"].count(":"), 2, kw["dedup_key"])

    def test_dark_episode_pings_at_most_once_804(self):
        # #804 item 4 -- a dark episode reports AT MOST ONCE, however long it
        # stays dark (no staged re-ping; the send is notify-suppressed, #704).
        proj, tmux = self._dark("sess-dark-once-804")
        sent, state = [], {}
        now = 1_700_000_000
        self._sweep(tmux, proj, state, sent, now)
        now, _ = self._until_lost(tmux, proj, state, sent, now)
        lines = []
        for _ in range(12):
            now += 100 * 3600
            lines += self._sweep(tmux, proj, state, sent, now)
        self.assertEqual(len(sent), 1, "a dark episode pings AT MOST ONCE (#804)")
        self.assertFalse(any("goal-lost" in ln for ln in lines), lines)

    def test_dry_run_reports_nothing(self):
        proj, tmux = self._dark("sess-dry")
        sent, state, logs = [], {}, []
        for t in range(30):
            logs += self._sweep(tmux, proj, state, sent, 1000 + 100 * t, dry_run=True)
        self.assertEqual(sent, [], "a dry-run sweep sends nothing")
        self.assertEqual(goal.load_goal_requests(), {})

    def test_user_cleared_goal_is_silent(self):
        # A user-CLEARED goal (newest marker `cleared`) is the owner switching
        # the autopilot off: no report, no journal line, no request.
        proj = self._dir()
        sid = "sess-cleared-norearm"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal cleared: /goal x", ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        sent, state, logs = [], {}, []
        for now in (1000, 2000, 2000 + 100 * 3600):
            logs += self._sweep(tmux, proj, state, sent, now)
        self.assertEqual(sent, [])
        self.assertFalse(any("goal-lost" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(), {})

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

    def test_awaiting_user_dark_loop_is_never_reported(self):
        # #737 C -- a session PARKED on a ❓ question is ALIVE-waiting (the
        # montalu6 incident): never reported lost, never a keystroke.
        proj, tmux = self._dark_awaiting("sess-737-await")
        sent, state, logs = [], {}, []
        for t in range(40):
            logs += self._sweep(tmux, proj, state, sent, 1000 + t * 100)
        self.assertEqual(sent, [], "never pinged -- the owner is already there")
        self.assertFalse(any("goal-lost" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], "and never a keystroke")

    def test_non_question_dark_tail_is_still_reported(self):
        # #737 C CONTROL -- the veto is ❓-SPECIFIC: a dead loop whose last turn
        # was a ✅ DONE is still reported lost (and still never re-armed).
        proj, tmux = self._dark_awaiting("sess-737-done", tail="✅ DONE: hotovo")
        sent, state = [], {}
        self._sweep(tmux, proj, state, sent, 1000)
        self._until_lost(tmux, proj, state, sent, 1000)
        self.assertEqual(len(sent), 1)
        self.assertEqual(goal.load_goal_requests(), {})

    def test_default_rearm_fn_fails_toward_none_on_resolve_error(self):
        # #478 review MINOR — an UNEXPECTED resolve_authority failure returns
        # NO template, never escalates authority to "full" (the own-template
        # reference would then match a merge-to-main template on a stream box).
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

    def setUp(self):
        # #804(4)/review -- isolate the roster path for a DIRECT
        # `python3 tests/test_goal_sweep.py` run too (conftest is pytest-only;
        # cmd_push's test_env covers only `unittest discover`), so a census
        # test's roster.save_roster never writes the developer's real
        # ~/.claude/goal-roster.json.
        p = m.patch.dict(os.environ,
                         {"AIRULESET_GOAL_ROSTER_PATH":
                          str(Path(self._dir()) / "goal-roster.json")})
        p.start()
        self.addCleanup(p.stop)

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
        # render path took NO action: no keystroke.
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


class TestGoalLaneNudgeDoctrine(unittest.TestCase):
    """#442 — the goal-guard rider's live-conversation window (the lane
    occupancy nudge it was first written for is gone, #1137)."""

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
        # AFTER the fix: at most `cap` /goal types (+ #1181: at most ONE notice).
        goals = [t for t in tmux.typed_texts() if t.startswith("/goal")]
        self.assertLessEqual(len(goals), cap, "retype bound; typed %d" % len(goals))
        others = [t for t in tmux.typed_texts() if not t.startswith("/goal")]
        self.assertTrue(len(others) <= 1 and all("arm failed" in t for t in others), others)
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

    def test_leftover_stale_rearm_request_dropped_retired_first_sweep(self):
        # #1113 (RETIRES the #675 stale-rearm silent-attempt-cap path): a leftover
        # on-disk stale-rearm request is dropped `drop:stale-rearm-retired` on the
        # FIRST sweep -- before any keystroke or the attempt cap -- so it types
        # nothing, pings nothing, and clears in one sweep.
        proj = self._dir()
        sid = "sess-cap-stale"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "branch-merge",
                                 now=1000, path=self.reqp, origin="stale-rearm")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        pings = []
        goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                        requests_path=self.reqp,
                        send_fn=lambda m, **k: pings.append((m, k)),
                        sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.typed_texts(), [],
                         "a retired stale-rearm never types")
        self.assertEqual(pings, [], "a retired stale-rearm never pings")
        self.assertEqual(goal.load_goal_requests(self.reqp), {},
                         "the retired drop clears the request in ONE sweep")

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
