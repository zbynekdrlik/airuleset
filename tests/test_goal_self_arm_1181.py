"""#1181 -- the owner's `/autopilot` callback never armed a NON-declared pane.

Incident (dev1 iemmixer + fohmixer, 29.9.2026; the controller's own supervisor
pane since 22.9.): every job-9 delivery of a `self-callback` arm request ended
`skip:verify-failed` + `ARM-CONFIRM-FAIL boundary=input box=empty`, the
transcript gained no user turn, and after 3 attempts the request was dropped.

Proven cause (live repro in a private tmux server, matrix on the ticket):
`deliver_goal` gave the owner's OWN arm the staged PRIORITY nudge identity
`goal-sweep` on any pane that is not a declared window. #1023 made every
PRIORITY kind default OFF, and it is OFF on every box, so the one `keys`
primitive withheld every keystroke: nothing was typed, and the zero-keystroke
refusal was reported as a failed verify. gk never showed it because its
declared windows ride the always-on `goal-arm` kind (#1038). Payload length,
the CC version (2.1.283 / 2.1.284) and Remote Control made no difference.

Locked here:
  1. a `self-callback` arm on a non-declared pane with every staged kind OFF
     TYPES the /goal and arms (the root fix: the owner's own arm rides the
     always-on `goal-arm` recovery kind, like the #1128 stream watcher);
  2. a watchdog re-arm the owner's switch withholds is dropped as
     `drop:nudge-off` (nothing typed, no pane read, one attempt booked), never
     reported as `skip:verify-failed`;
  3. a `self-callback` arm dropped at the strict attempt cap leaves ONE pointer
     line in the pane and a `goal: arm failed (...)` status row.
"""
import json
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
)

CWD = "/home/newlevel/devel/iemmixer1181"     # NOT a declared window anywhere
PAYLOAD = "/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds x"


def _real_kill_switch(testcase):
    """The REAL per-kind predicate against an empty HOME = every staged kind OFF,
    exactly the box state (#1063 lesson: pop the conftest bypass, else the
    suite exercises the all-on shortcut, not the switch)."""
    home = TemporaryDirectory()
    testcase.addCleanup(home.cleanup)
    statep = Path(home.name) / ".claude" / "api-watchdog-state.json"
    for p in (m.patch.dict(os.environ, {"HOME": home.name}),
              m.patch.object(wd, "STATE_PATH", statep)):   # never the box's file
        p.start()
        testcase.addCleanup(p.stop)
    saved = os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
    if saved is not None:
        testcase.addCleanup(os.environ.__setitem__,
                            "AIRULESET_TEST_IGNORE_DISABLE", saved)
    return Path(home.name)


def _pane(testcase, sid):
    proj = TemporaryDirectory()
    testcase.addCleanup(proj.cleanup)
    tp = _write_marker_transcript(proj.name, CWD, sid)
    tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP,
                               model_type=True, transcript_path=tp)
    return Path(proj.name), tmux


class TestSelfCallbackArmTypesAtAllOff(unittest.TestCase):
    """1. The root: the owner's own arm is never withheld by the staged switch."""

    def setUp(self):
        _isolate_goal_state(self)
        _real_kill_switch(self)

    def test_the_box_state_really_has_goal_sweep_off(self):
        # the precondition the incident ran under, read from the real predicate
        self.assertFalse(wd.nudges_enabled("goal-sweep"))

    def test_self_callback_arm_on_a_non_declared_pane_types_and_arms(self):
        sid = "sess-1181-self"
        proj, tmux = _pane(self, sid)
        logs = []
        now = 100000
        word = goal.deliver_goal(sid, CWD, PAYLOAD, "full", run=tmux,
                                 projects_dir=proj, now=now, request_ts=now,
                                 state={}, sleep_fn=lambda s: None, logs=logs,
                                 origin="self-callback")
        self.assertIn(PAYLOAD, tmux.typed_texts(),
                      "the /goal was never typed (withheld by the staged "
                      "switch): %r" % logs)
        self.assertEqual(word, "sent", logs)
        self.assertFalse(any("nudges OFF: suppressed" in ln for ln in logs), logs)

    def test_the_nudge_identity_of_the_owners_arm_is_the_recovery_kind(self):
        self.assertEqual(goal._declared_window_nudge(CWD, "self-callback"),
                         "goal-arm")
        self.assertIn("goal-arm", wd.RECOVERY_NUDGE_KINDS)

    def test_a_watchdog_rearm_on_the_same_pane_keeps_the_staged_kind(self):
        # scope lock: only the owner's own arm changes identity
        self.assertEqual(goal._declared_window_nudge(CWD, "dark-rearm"),
                         "goal-sweep")


class TestWithheldRearmIsNamedNotVerifyFailed(unittest.TestCase):
    """2. A delivery the owner's switch withholds types NOTHING and says so."""

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        _real_kill_switch(self)

    def test_off_rearm_is_drop_nudge_off_with_zero_keystrokes(self):
        sid = "sess-1181-off"
        proj, tmux = _pane(self, sid)
        calls, logs = [], []
        now = 100000

        def run(argv, timeout=8):
            calls.append(argv)
            return tmux(argv, timeout)
        word = goal.deliver_goal(sid, CWD, PAYLOAD, "full", run=run,
                                 projects_dir=proj, now=now, request_ts=now,
                                 state={}, sleep_fn=lambda s: None,
                                 logs=logs, origin="dark-rearm")
        self.assertEqual(word, "drop:nudge-off", logs)
        self.assertEqual(tmux.keys(), [], "nothing may be typed at OFF")
        self.assertFalse(any("capture-pane" in a for a in calls), calls)
        self.assertTrue(any("nudges OFF: suppressed goal-sweep" in ln
                            for ln in logs), logs)
        sync = self.syncp.read_text() if self.syncp.exists() else ""
        self.assertNotIn("ARM-CONFIRM-FAIL", sync,
                         "a withheld keystroke is not a failed arm confirm")
        self.assertIn("DROP nudge-off(goal-sweep)", sync)

    def test_nudge_off_is_terminal_and_bounded_by_the_origin_attempt_cap(self):
        # one sweep: cleared, never re-typed, no misleading attempt-cap ping, and
        # ONE booked dark-rearm attempt so dark-watch stays under its 24 h cap
        sid = "sess-1181-off-sweep"
        proj, tmux = _pane(self, sid)
        goal.record_goal_request(sid, CWD, PAYLOAD, "full", now=100000,
                                 path=self.reqp, origin="dark-rearm")
        state, pings = {}, []
        logs = goal.goal_sweep(100010, run=tmux, projects_dir=proj,
                               requests_path=self.reqp, state=state,
                               send_fn=lambda msg, **k: pings.append(msg),
                               sleep_fn=lambda s: None)
        self.assertEqual(tmux.keys(), [])
        self.assertNotIn(sid, goal.load_goal_requests(self.reqp))
        self.assertTrue(any("drop:nudge-off" in ln for ln in logs), logs)
        self.assertEqual(pings, [])
        key = goal._GOAL_ATTEMPTS_STATE_KEYS["dark-rearm"]
        self.assertEqual(len(state.get(key, {}).get(sid, [])), 1, state)


QUIET = (False, "")
WAITING = ("● Predošlá práca hotová.\n✻ Waiting for 3 background agents to "
           "finish\n❯ \n  ctx ███░  caveman:lite\n")


class TestCappedSelfArmIsVisible(unittest.TestCase):
    """3. A capped owner arm leaves one pane line + a status row."""

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        _real_kill_switch(self)
        p = m.patch.object(wd, "_tmux_client_recent_input", return_value=QUIET)
        p.start()
        self.addCleanup(p.stop)

    def _sweep(self, sid, origin, captured=GOAL_IDLE_CAP, in_mode=False,
               now=100000, tage=120, state=None):
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        tp = _write_marker_transcript(proj.name, CWD, sid, transcript_age_s=tage)
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], captured,
                                   model_type=True, transcript_path=tp,
                                   in_mode=in_mode)
        goal.record_goal_request(sid, CWD, PAYLOAD, "full", now=now - 200,
                                 path=self.reqp, origin=origin)
        d = json.loads(self.reqp.read_text())
        d[sid].update(dl_fails=goal.GOAL_DELIVERY_ATTEMPT_CAP,
                      dl_last="skip:verify-failed")
        self.reqp.write_text(json.dumps(d))
        state = {} if state is None else state
        logs = goal.goal_sweep(now, run=tmux, projects_dir=Path(proj.name),
                               requests_path=self.reqp, state=state,
                               sleep_fn=lambda s: None)
        return tmux, state, logs

    @staticmethod
    def _notices(tmux):
        return [t for t in tmux.typed_texts() if "arm failed" in t]

    def test_self_callback_cap_types_one_pointer_line_and_records_it(self):
        sid = "sess-1181-cap"
        tmux, state, logs = self._sweep(sid, "self-callback")
        self.assertTrue(any("drop:attempt-cap" in ln for ln in logs), logs)
        notices = self._notices(tmux)
        self.assertEqual(len(notices), 1, "exactly one pointer line: %r"
                         % tmux.typed_texts())
        self.assertIn("/autopilot", notices[0])
        self.assertIn("Claude: no action", notices[0])
        self.assertNotIn(PAYLOAD, tmux.typed_texts(), "never a new /goal type")
        rec = state.get("goal_arm_failed", {}).get(sid)
        self.assertIsNotNone(rec, state)
        self.assertEqual(rec["reason"], "skip:verify-failed")

    def test_the_notice_fits_one_machine_line_and_is_no_slash_command(self):
        from watchdog import goal_arm_failure as gaf, nudge_file
        line = gaf.NOTICE.format(attempts=goal.GOAL_DELIVERY_LIVE_ATTEMPT_CAP)
        self.assertLessEqual(nudge_file.cells(line), nudge_file.LINE_MAX_CELLS)
        self.assertFalse(line.lstrip().startswith("/"))

    def test_the_notice_is_machine_text_never_an_owner_answer(self):
        # #1133: an answer / human-presence detector must reject our own line
        from watchdog import goal_arm_failure as gaf, questions, stream_migrate
        line = gaf.NOTICE.format(attempts=3)
        entry = {"type": "user", "message": {"content": line}}
        self.assertFalse(questions._is_genuine_human_prompt(entry))
        self.assertTrue(stream_migrate._own_keystroke(line))

    def test_no_notice_into_a_box_holding_a_draft(self):
        draft = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
        tmux, _state, logs = self._sweep("sess-1181-draft", "self-callback",
                                         captured=draft)
        self.assertEqual(self._notices(tmux), [])
        self.assertTrue(any("not an empty idle input" in ln for ln in logs), logs)

    def test_no_notice_into_a_copy_mode_pane(self):
        tmux, state, logs = self._sweep("sess-1181-mode", "self-callback",
                                        in_mode=True)
        self.assertEqual(self._notices(tmux), [])
        self.assertTrue(any("notice not typed (in-mode)" in ln for ln in logs),
                        logs)
        self.assertIn("sess-1181-mode", state.get("goal_arm_failed", {}))

    def test_no_notice_into_a_waiting_for_agents_pane(self):
        tmux, _state, logs = self._sweep("sess-1181-wait", "self-callback",
                                         captured=WAITING)
        self.assertEqual(self._notices(tmux), [])
        self.assertTrue(any("notice not typed (busy-waiting)" in ln
                            for ln in logs), logs)

    def test_no_notice_into_a_live_turn(self):
        import time as _t
        tmux, _state, logs = self._sweep("sess-1181-live", "self-callback",
                                         now=_t.time(), tage=3)
        self.assertEqual(self._notices(tmux), [])
        self.assertTrue(any("notice not typed (live-turn)" in ln
                            for ln in logs), logs)

    def test_the_notice_is_typed_once_per_failure_window(self):
        sid = "sess-1181-twice"
        state = {}
        first, _s, _l = self._sweep(sid, "self-callback", state=state)
        second, _s, logs = self._sweep(sid, "self-callback", now=100500,
                                       state=state)
        self.assertEqual(len(self._notices(first)), 1)
        self.assertEqual(self._notices(second), [], "a re-arm loop is bounded")
        self.assertTrue(any("notice already typed" in ln for ln in logs), logs)
        self.assertEqual(state["goal_arm_failed"][sid]["ts"], 100500,
                         "the status row is refreshed by the later cap")

    def test_a_watchdog_rearm_at_the_cap_gets_no_pane_line(self):
        sid = "sess-1181-rearm-cap"
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            tmux, state, logs = self._sweep(sid, "dark-rearm")
        self.assertEqual(self._notices(tmux), [])
        self.assertFalse(state.get("goal_arm_failed"))

    def _status(self, sid, state_doc, captured=GOAL_IDLE_CAP):
        import airuleset
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        _write_marker_transcript(proj.name, CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], captured)
        statep = Path(proj.name) / "api-watchdog-state.json"
        statep.write_text(json.dumps(state_doc))
        with m.patch.object(wd, "STATE_PATH", statep), \
                m.patch.object(wd, "PROJECTS_DIR", Path(proj.name)), \
                m.patch("time.time", return_value=10 ** 10 + 60):
            return airuleset.goal_status_probe(CWD, run=tmux, pane_env="%9")

    @staticmethod
    def _failed(sid, **extra):
        rec = {"ts": 10 ** 10, "reason": "skip:verify-failed", "attempts": 3}
        return dict({"goal_arm_failed": {sid: rec}}, **extra)

    def test_status_row_names_the_failed_arm(self):
        sid = "sess-1181-status"
        self.assertEqual(
            self._status(sid, self._failed(sid)),
            "goal: arm failed (skip:verify-failed x3) — paste the /goal line "
            "/autopilot printed, or re-run /autopilot")

    def test_an_armed_pane_never_shows_a_stale_failure(self):
        from _goal_arm_helpers import GOAL_ARMED_CAP
        sid = "sess-1181-armed"
        row = self._status(sid, self._failed(sid), captured=GOAL_ARMED_CAP)
        self.assertTrue(row.startswith("goal: armed"), row)

    def test_an_arm_after_the_failure_retires_the_row(self):
        # the owner pasted the line; the loop later ended: no stale "failed"
        sid = "sess-1181-pasted"
        mark = {"mark": {"state": "set", "ts": 10 ** 10 + 30}}
        row = self._status(sid, self._failed(sid, goal_mark={sid: mark}))
        self.assertNotIn("arm failed", row)

    def test_a_corrupt_record_never_breaks_the_sweep(self):
        from watchdog import goal_arm_failure as gaf
        state = {"goal_arm_failed": {"old": {"ts": "garbage"}}}
        gaf.record(state, "new", CWD, "skip:verify-failed", 3, 100)
        self.assertEqual(list(state["goal_arm_failed"]), ["new"])


class TestFreshArmSecondsAgeReadsArmed(unittest.TestCase):
    """4. Found by the post-fix live repro (CC 2.1.284): for its first minute a
    freshly armed goal renders a SECONDS age in the box header, and the
    header regex accepted only h/m/d, so `_await_goal_armed` read a real arm as
    dark (`not-armed-after-submit` -> `skip:verify-failed-live`)."""

    # the pane frame captured live right after the arm (pane width 200)
    FRAME = ("· Whatchamacalliting… (8s · ↓ 355 tokens · thinking)\n"
             "  ⎿  Tip: Use /voice to enable push-to-talk dictation\n"
             + " " * 179 + "◎ /goal active (8s)\n"
             + "─" * 200 + "\n❯ \n" + "─" * 200 + "\n"
             "  ⏵⏵ auto mode on (shift+tab to cycle) · install gh for PR "
             "status · esc to interrupt · ← for agents\n")

    def test_the_live_fresh_arm_frame_reads_armed(self):
        self.assertIs(wd.pane_goal_armed(self.FRAME), True)

    def test_the_closed_form_still_rejects_prose(self):
        for line in ("◎ /goal active (8 s)", "◎ /goal active (8sec)",
                     "◎ /goal active (8s) and more"):
            self.assertIsNone(wd._GOAL_HEADER_INDICATOR_RX.match(line), line)


if __name__ == "__main__":
    unittest.main()
