"""#1110 — the goal-arm keystroke is gated on the session's TRANSCRIPT liveness
(structured state, #486 direction), a mis-timed keystroke never counts toward the
attempt cap, and the arm-confirm diagnostic records the transcript age.

Incident (owner, dev1 songplayer 22.9.2026, sid 4d877a2a): `/autopilot` recorded a
goal-arm request; job 9 (`goal_sweep`) delivered a keystroke at 12:32/12:33/12:34
into a pane whose render was a bare `❯` box between tool rounds — byte-identical to a
genuinely idle prompt (#1104's spinner belt cannot see it: no spinner row between
tool calls). The turn was STILL RUNNING (transcript entries every few seconds through
12:36, turn ended 12:39). Each keystroke `skip:verify-failed`; three exhausted
`GOAL_DELIVERY_ATTEMPT_CAP = 3`; the request was DROPPED at 12:36. The pane went idle
at 12:39 with nothing pending — never armed. The owner read it as "nudges OFF".

Root cause + fix (design comment 5775245682, Approach 1):
  1. The pre-keystroke gate was render-only. The session TRANSCRIPT is the structured
     truth — a running turn appends at every tool round. `deliver_goal` now defers with
     ZERO keystrokes (`skip:busy-transcript`) when the transcript was written within
     `GOAL_TURN_LIVE_WINDOW_S` (45 s), BEFORE the render gate.
  2. A `verify-failed` whose transcript advanced DURING the confirm window is a
     MIS-timed keystroke, not a failed one: `skip:verify-failed-live` — NOT in
     `_GOAL_KEYSTROKE_SKIPS`, so `goal_sweep` never counts it toward the cap and the
     request stays pending. A genuinely quiet `verify-failed` still counts (the #731
     swallowed-submit class the cap was designed for).
  3. `_log_arm_confirm_fail` carries `tage=<seconds since the transcript's last write>`
     so any future shape is classifiable from the log alone.

FIXTURE REALISM (see `_goal_arm_helpers._write_marker_transcript`): an idle session's
transcript is minutes old; these tests set an explicit age per case against an explicit
`now`, so the liveness verdict is deterministic.
"""
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import goal_turn_liveness as gtl  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
)

CWD = "/home/newlevel/devel/turnlive1110"
SID = "sess-turnlive-1110"
TEXT = "/goal work the whole backlog one ticket at a time until every issue is closed"
PANE = [("%9", "claude", CWD, "111")]


def _proj_aged(testcase, age_s, now):
    """A temp projects dir with a transcript whose mtime is EXACTLY `now - age_s`
    (deterministic, independent of wall-clock jitter). Returns (proj, tpath)."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    proj = Path(d.name)
    tpath = _write_marker_transcript(proj, CWD, SID, transcript_age_s=None)
    os.utime(tpath, (now - age_s, now - age_s))
    return proj, tpath


# --------------------------------------------------------------------------- #
# The leaf's pure classifiers (no I/O beyond a stat) — the logic the gate reads.
# --------------------------------------------------------------------------- #
class TestLeafClassifiers(unittest.TestCase):
    def test_transcript_age_none_for_missing_or_falsy(self):
        self.assertIsNone(gtl.transcript_age_s(None, 100.0))
        self.assertIsNone(gtl.transcript_age_s("/no/such/transcript.jsonl", 100.0))

    def test_window_default_env_override_and_floor(self):
        self.assertEqual(gtl.goal_turn_live_window_s(env={}), 45)
        self.assertEqual(gtl.goal_turn_live_window_s(
            env={"AIRULESET_GOAL_TURN_LIVE_WINDOW_S": "90"}), 90)
        self.assertEqual(gtl.goal_turn_live_window_s(
            env={"AIRULESET_GOAL_TURN_LIVE_WINDOW_S": "5"}), 15)   # floored
        self.assertEqual(gtl.goal_turn_live_window_s(
            env={"AIRULESET_GOAL_TURN_LIVE_WINDOW_S": "nope"}), 45)  # fail-safe

    def test_turn_live_window(self):
        self.assertTrue(gtl.turn_live(5, env={}))
        self.assertFalse(gtl.turn_live(120, env={}))
        self.assertFalse(gtl.turn_live(None))
        self.assertTrue(gtl.turn_live(-1.0, env={}))       # tiny jitter -> live
        self.assertFalse(gtl.turn_live(-1.0e9, env={}))    # future-dated -> not a signal

    def test_classify_confirm_fail(self):
        self.assertEqual(gtl.classify_confirm_fail(120, 2), "live")     # advanced
        self.assertEqual(gtl.classify_confirm_fail(120, 127), "quiet")  # aged
        self.assertEqual(gtl.classify_confirm_fail(120, 120), "quiet")  # unchanged
        self.assertEqual(gtl.classify_confirm_fail(None, 2), "quiet")   # counted default
        self.assertEqual(gtl.classify_confirm_fail(120, None), "quiet")


# --------------------------------------------------------------------------- #
# (a)/(b) the PRE-keystroke busy-transcript gate.
# --------------------------------------------------------------------------- #
class TestBusyTranscriptGate(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _deliver(self, age_s, arm=True, transcript_path=None, sleep_fn=None):
        now = time.time()
        proj, tpath = _proj_aged(self, age_s, now)
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=arm, transcript_path=transcript_path)
        word = goal.deliver_goal(SID, CWD, TEXT, "full", run=tmux,
                                 projects_dir=proj, now=now,
                                 sleep_fn=sleep_fn or (lambda s: None))
        return word, tmux, tpath, now

    def test_a_fresh_transcript_defers_zero_keystrokes(self):
        # (a) clean idle box, transcript 5 s old -> the turn is live -> defer.
        word, tmux, _, _ = self._deliver(age_s=5)
        self.assertEqual(word, "skip:busy-transcript")
        self.assertEqual(tmux.sent, [], "no keystroke into a live turn")

    def test_a_busy_transcript_line_names_the_age(self):
        self._deliver(age_s=5)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("busy-transcript", log)
        self.assertIn("tage=", log)

    def test_b_old_transcript_runs_the_keystroke_path(self):
        # (b) same clean idle box, transcript 120 s old -> not live -> arms.
        word, tmux, _, _ = self._deliver(age_s=120)
        self.assertEqual(word, "sent")
        self.assertNotEqual(tmux.sent, [])

    def test_gate_is_before_the_render_gate(self):
        # a fresh transcript defers even when the render would classify busy:
        # the transcript gate is evaluated FIRST (its own word, not skip:busy).
        now = time.time()
        proj, _ = _proj_aged(self, 5, now)
        spinner = ("● Hotovo.\n✻ Frosting… (33s · ↓ 1.4k tokens)\n❯ \n"
                   "  ctx ███░  caveman:lite\n")
        tmux = DeliverGoalFakeTmux(PANE, spinner, model_type=True)
        word = goal.deliver_goal(SID, CWD, TEXT, "full", run=tmux,
                                 projects_dir=proj, now=now, sleep_fn=lambda s: None)
        self.assertEqual(word, "skip:busy-transcript")
        self.assertEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
# (g) a missing/unreadable transcript is NOT a liveness signal -> no defer.
# --------------------------------------------------------------------------- #
class TestMissingTranscriptFallsThrough(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def test_g_missing_transcript_no_busy_transcript_defer(self):
        from watchdog import compact as _compact
        proj = self._dir()
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(_compact, "_find_pane_for_session", return_value="%9"), \
             m.patch.object(wd, "find_active_transcript", return_value=None):
            word = goal.deliver_goal(SID, CWD, TEXT, "full", run=tmux,
                                     projects_dir=proj, now=time.time(),
                                     sleep_fn=lambda s: None)
        self.assertNotEqual(word, "skip:busy-transcript",
                            "a missing transcript must never defer via the liveness gate")

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)


# --------------------------------------------------------------------------- #
# (c)/(d) the POST-keystroke confirm split + the sweep accounting.
# --------------------------------------------------------------------------- #
class TestConfirmSplitAccounting(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _advancing_sleep(self, tpath, now):
        # a live turn writes an entry during the confirm window: move the mtime
        # forward (to 2 s old) so age_after < age_before -> "live".
        def _s(_dummy):
            os.utime(tpath, (now - 2, now - 2))
        return _s

    def test_c_live_confirm_is_verify_failed_live_and_uncounted(self):
        # (c) transcript quiet at the gate (120 s), a keystroke is typed, the
        # arm never confirms, and the transcript ADVANCED during the confirm
        # window -> skip:verify-failed-live (a mis-timed keystroke).
        now = time.time()
        proj, tpath = _proj_aged(self, 120, now)
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)   # typed, never arms
        word = goal.deliver_goal(SID, CWD, TEXT, "full", run=tmux,
                                 projects_dir=proj, now=now, state={},
                                 sleep_fn=self._advancing_sleep(tpath, now))
        self.assertEqual(word, "skip:verify-failed-live")
        self.assertNotEqual(tmux.sent, [], "the keystroke WAS typed (mis-timed)")

    def test_c_verify_failed_live_never_bumps_dl_fails_over_5_sweeps(self):
        # (c) five sweeps that each land skip:verify-failed-live must leave
        # dl_fails at 0 and the request PENDING (never the cap drop).
        now = time.time()
        proj, tpath = _proj_aged(self, 120, now)
        goal.record_goal_request(SID, CWD, TEXT, "full", now=now,
                                 path=self.reqp, origin="self-callback")
        for _i in range(5):
            os.utime(tpath, (now - 120, now - 120))   # quiet at the gate again
            tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                       arm_on_submit=False)
            with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
                goal.goal_sweep(now, run=tmux, projects_dir=proj,
                                requests_path=self.reqp,
                                send_fn=lambda msg, **kw: None,
                                sleep_fn=self._advancing_sleep(tpath, now))
        reqs = goal.load_goal_requests(self.reqp)
        self.assertIn(SID, reqs, "the request must stay PENDING")
        self.assertEqual(int(reqs[SID].get("dl_fails", 0)), 0,
                         "a mis-timed (live) keystroke never counts toward the cap")

    def test_d_quiet_confirm_is_verify_failed_and_counted_to_cap(self):
        # (d) transcript quiet the WHOLE time (no advance) -> skip:verify-failed
        # (the #731 swallowed-submit class); three of them -> drop:attempt-cap.
        now = time.time()
        proj, tpath = _proj_aged(self, 120, now)
        # single call returns the counted word:
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            word = goal.deliver_goal(SID, CWD, TEXT, "full", run=tmux,
                                     projects_dir=proj, now=now, state={},
                                     sleep_fn=lambda s: None)   # no advance -> quiet
        self.assertEqual(word, "skip:verify-failed")

        # three quiet verify-faileds via the sweep -> the #731 terminal cap drop.
        goal.record_goal_request(SID, CWD, TEXT, "full", now=now,
                                 path=self.reqp, origin="self-callback")
        drop_seen = False
        for _i in range(goal.GOAL_DELIVERY_ATTEMPT_CAP + 1):
            os.utime(tpath, (now - 120, now - 120))
            tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                       arm_on_submit=False)
            with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
                logs = goal.goal_sweep(now, run=tmux, projects_dir=proj,
                                       requests_path=self.reqp,
                                       send_fn=lambda msg, **kw: None,
                                       sleep_fn=lambda s: None)
            if any("drop:attempt-cap" in ln for ln in logs):
                drop_seen = True
                break
        self.assertTrue(drop_seen, "three quiet verify-faileds must hit the #731 cap")
        self.assertNotIn(SID, goal.load_goal_requests(self.reqp),
                         "the cap drop clears the request (the #731 lock stays)")


# --------------------------------------------------------------------------- #
# (e) the dev1 sequence: three live-turn attempts (uncounted) then idle -> sent.
# --------------------------------------------------------------------------- #
class TestDev1SequenceArmsOnceIdle(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def test_e_three_live_attempts_then_idle_arms(self):
        now = time.time()
        proj, tpath = _proj_aged(self, 120, now)
        goal.record_goal_request(SID, CWD, TEXT, "full", now=now,
                                 path=self.reqp, origin="self-callback")

        def _adv(_dummy):
            os.utime(tpath, (now - 2, now - 2))   # live: advanced during confirm

        # three live-turn attempts (keystroke typed, arm fails, transcript
        # advanced) -> skip:verify-failed-live, uncounted, request stays pending.
        for _i in range(3):
            os.utime(tpath, (now - 120, now - 120))          # quiet at the gate
            tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                       arm_on_submit=False)
            with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
                goal.goal_sweep(now, run=tmux, projects_dir=proj,
                                requests_path=self.reqp,
                                send_fn=lambda msg, **kw: None, sleep_fn=_adv)
            reqs = goal.load_goal_requests(self.reqp)
            self.assertIn(SID, reqs, "still pending after a live-turn attempt")
            self.assertEqual(int(reqs[SID].get("dl_fails", 0)), 0)

        # the turn ends: transcript quiet, pane idle -> the arm lands.
        os.utime(tpath, (now - 120, now - 120))
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=True)
        logs = goal.goal_sweep(now, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda msg, **kw: None, sleep_fn=lambda s: None)
        self.assertTrue(any("-> sent" in ln for ln in logs),
                        "the arm must land once the turn ends: %r" % logs)
        self.assertNotIn(SID, goal.load_goal_requests(self.reqp),
                         "a sent arm clears the request")


# --------------------------------------------------------------------------- #
# (f) the ARM-CONFIRM-FAIL diagnostic carries tage=.
# --------------------------------------------------------------------------- #
class TestArmConfirmFailCarriesTage(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def test_f_arm_confirm_fail_line_has_tage(self):
        # driven through deliver_goal's own verify-failed path (quiet transcript
        # -> skip:verify-failed -> _log_arm_confirm_fail): the diagnostic line
        # must carry tage=<seconds>.
        now = time.time()
        proj, tpath = _proj_aged(self, 120, now)
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=False)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal.deliver_goal(SID, CWD, TEXT, "full", run=tmux,
                              projects_dir=proj, now=now, state={},
                              sleep_fn=lambda s: None)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("ARM-CONFIRM-FAIL", log)
        self.assertRegex(log, r"tage=\d+", "the diagnostic must record the transcript age")


if __name__ == "__main__":
    unittest.main()
