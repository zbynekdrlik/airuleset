"""#1104 — a SUSPENDED turn ("Waiting for N background agents" / an activity
spinner) is BUSY for every keystroke nudge; verify-failed cleanup deferred to the
first true idle tick; attempt-budget booked only on a REAL send; the enable CLI
warns on a busy pane.

Incident (owner, montalu1, 21.9.2026): the watchdog goal-sweep typed the ~3.8 kB
`/goal STOP CONDITIONS …` template into a pane whose turn was suspended on
`✻ Waiting for 3 background agents to finish` (a bare box, NO `esc to interrupt`),
the Enter was swallowed (`skip:verify-failed`), and the text sat unsent in the
owner's input box. One sweep later the same pane read `skip:busy` — a render race
the busy predicate lost.

Four coupled fixes, RED-first (root cause traced in the #1104 design comment):

1. CLASSIFIER — `_classify_boundary` (watchdog/pane_text.py) marks a pane BUSY
   when a GENERIC ACTIVITY SPINNER (`✻ Frosting… (33s …)` — a spinner glyph +
   the spinner's own ellipsis) renders ABOVE an at-rest box: the Enter is
   swallowed, so every keystroke job's `kind=="busy"` check defers. The sibling
   "Waiting for N background agents" AMBIENT line (no ellipsis) stays the
   `_pane_busy_waiting` gate's job — putting it in the classifier would break the
   #458 lock `test_goal_undeterminable_pane.py` (MONTALU3 carries that line and
   is asserted `("input","")`). Both busy states defer every keystroke path.

2. VERIFY-FAILED CLEANUP — `_log_arm_confirm_fail`'s live-turn decline now journals
   `cleanup=deferred(live-turn)` and sets the #372 janitor watch, so the shared
   janitor recovers the stranded /goal at the FIRST true idle tick (never Escape a
   live turn). `_janitor_undo_if_own_stranded` is the sibling that clears our own
   stranded box with a `janitor-undo` line.

3. ATTEMPT BOOKING — `deliver_goal`'s watchdog-re-arm `mark_pane_attempt` fires
   only when a keystroke was ACTUALLY sent (not OFF-suppressed): an OFF-suppressed
   send leaves the per-pane budget untouched.

4. CLI GUARD — `nudges on --kind <keystroke kind>` reads the box's managed panes
   and WARNS on a busy/draft pane before enabling a keystroke kind.
"""
import io
import json
import os
import sys
import types
import unittest
import unittest.mock as m
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import ops_wait_recheck as owr  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
    _write_goal_marker,
)

# A bare `❯` box with a GENERIC ACTIVITY spinner rendered above it (a turn mid-
# render, NO `esc to interrupt`): the incident's render-race state. The spinner
# carries its own ellipsis `…`.
SPINNER_ABOVE_BARE = (
    "● Predošlá práca hotová.\n"
    "✻ Frosting… (33s · ↓ 1.4k tokens)\n"
    "❯ \n"
    "  ctx ███░  caveman:lite\n")
# A bare `❯` box with the "Waiting for N background agents" AMBIENT line above
# (NO ellipsis) — handled by `_pane_busy_waiting`, NOT the classifier.
WAITING_ABOVE_BARE = (
    "● Predošlá práca hotová.\n"
    "✻ Waiting for 3 background agents to finish\n"
    "❯ \n"
    "  ctx ███░  caveman:lite\n")
# A genuinely idle pane — no spinner, no Waiting line.
CLEAN_IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


# --------------------------------------------------------------------------- #
# 1. CLASSIFIER — a spinner above a bare box classifies BUSY; the Waiting line
#    stays "input" (its gate is `_pane_busy_waiting`).
# --------------------------------------------------------------------------- #
class TestClassifierSpinnerAboveBox(unittest.TestCase):
    def test_generic_spinner_above_bare_box_is_busy(self):
        # RED: pre-fix `_classify_boundary` sees only the bare box and returns
        # ("input", ""), so a keystroke job types into a swallowing pane.
        self.assertEqual(wd._classify_boundary(SPINNER_ABOVE_BARE), ("busy", None))

    def test_all_cc_spinner_frames_are_busy_not_just_one_glyph(self):
        # #1104-review F1: CC cycles the leading spinner GLYPH per animation frame
        # (`✻`/`✳`/`✽`/`✢`/`·`, watchdog/long_turn.py). The detection MUST be
        # frame-agnostic — a race-moment capture landing on `✳`/`·` was the
        # incident's own trigger. RED against a glyph-hardcoded regex.
        def cap(spin):
            return "● Hotovo.\n" + spin + "\n❯ \n  ctx ███░  caveman:lite\n"
        for spin in ("✳ Baking… (4m 2s · esc to interrupt)",
                     "· Germinating… (2h 40m 36s · ↓ 69.3k tokens)",
                     "✽ Simmering… (7s · ↓ 2k tokens)",
                     "✢ Whisking… (12s)"):
            self.assertEqual(wd._classify_boundary(cap(spin)), ("busy", None),
                             "frame %r must classify busy" % spin[:12])

    def test_finished_turn_summary_is_not_busy(self):
        # A FINISHED-turn summary reuses the spinner glyph but has no `…(duration)`
        # readout (`✻ Brewed for 24s`) — it must NOT read busy (else an idle pane
        # after a turn ends is starved).
        cap = "● Hotovo.\n✻ Brewed for 24s\n❯ \n  ctx ███░  caveman:lite\n"
        self.assertEqual(wd._classify_boundary(cap), ("input", ""))

    def test_waiting_line_stays_input_handled_by_pane_busy_waiting(self):
        # The #458 lock: a Waiting line (no ellipsis) is NOT a classifier "busy"
        # (that would break test_goal_undeterminable_pane.py); it is caught by
        # the already-working `_pane_busy_waiting`.
        self.assertEqual(wd._classify_boundary(WAITING_ABOVE_BARE), ("input", ""))
        self.assertTrue(owr._pane_busy_waiting(WAITING_ABOVE_BARE))

    def test_clean_idle_pane_stays_input(self):
        self.assertEqual(wd._classify_boundary(CLEAN_IDLE), ("input", ""))
        self.assertFalse(owr._pane_busy_waiting(CLEAN_IDLE))

    def test_a_bare_box_with_no_activity_line_is_still_input(self):
        self.assertEqual(wd._classify_boundary("hello\n❯ "), ("input", ""))


# --------------------------------------------------------------------------- #
# 2. GOAL DELIVERY defers (no keystroke) on both busy states.
# --------------------------------------------------------------------------- #
class TestGoalDeliveryDefersOnBusy(unittest.TestCase):
    CWD = "/home/newlevel/devel/busy1104"
    SID = "sess-busy-1104"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, captured):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   captured, model_type=True)
        word = goal.deliver_goal(self.SID, self.CWD, "/goal STOP CONDITIONS x",
                                 "full", run=tmux, projects_dir=proj,
                                 sleep_fn=lambda s: None)
        return word, tmux

    def test_spinner_above_box_defers_zero_keystrokes(self):
        # RED: pre-fix the classifier reads "input" and deliver_goal types the
        # /goal into a swallowing pane.
        word, tmux = self._deliver(SPINNER_ABOVE_BARE)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [], "no keystroke into a spinner-busy pane")

    def test_waiting_above_box_defers_zero_keystrokes(self):
        # Regression lock: the Waiting line already defers via `_pane_busy_waiting`.
        word, tmux = self._deliver(WAITING_ABOVE_BARE)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])

    def test_clean_idle_pane_still_delivers(self):
        # Control: the gate is narrow — a genuinely idle pane still arms.
        word, tmux = self._deliver(CLEAN_IDLE)
        self.assertEqual(word, "sent")
        self.assertNotEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
#    The shared delivery primitive send_verified (every ops-wait / u-freshness /
#    queue-arrival / release-gap / lane-reconcile rider + the batch send routes
#    through it) DEFERS on a spinner-above-bare-box (#1104-review F2 shared-
#    benefit: the riders gate only on _pane_busy_waiting, which catches the
#    ellipsis-free Waiting line but NOT a mid-render spinner).
# --------------------------------------------------------------------------- #
class TestRiderDefersOnSpinnerViaSendVerified(unittest.TestCase):
    PID = "%9"

    def _tpath(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "sess.jsonl"
        p.write_text(json.dumps({"type": "assistant",
                                 "message": {"content": "x"}}) + "\n")
        return p

    def test_send_verified_defers_on_spinner_above_bare_box(self):
        # A rider's keystroke into a spinner-above-BARE-box would be SWALLOWED:
        # the box reads bare so the pre-fix bare-check passed and it TYPED. RED
        # against the pre-F2 tree; GREEN once send_verified aborts on the spinner.
        p = self._tpath()
        tmux = DeliverGoalFakeTmux([(self.PID, "claude", "/x", "111")],
                                   SPINNER_ABOVE_BARE, model_type=True,
                                   transcript_path=p)
        logs = []
        ok = wd.send_verified(self.PID, "rider-nudge: fill the lane, backlog=5",
                              tmux, p, sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok, "a rider must not deliver into a spinner-busy pane")
        self.assertEqual(tmux.sent, [], "zero keystrokes into a suspended turn")
        self.assertTrue(any("spinner above box" in ln for ln in logs), logs)

    def test_send_verified_still_delivers_into_a_clean_idle_pane(self):
        # Control: the gate is narrow — a genuinely idle pane still delivers.
        p = self._tpath()
        tmux = DeliverGoalFakeTmux([(self.PID, "claude", "/x", "111")],
                                   CLEAN_IDLE, model_type=True,
                                   transcript_path=p)
        ok = wd.send_verified(self.PID, "rider-nudge: fill the lane",
                              tmux, p, sleep_fn=lambda s: None, logs=[])
        self.assertTrue(ok)
        self.assertNotEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
# 3. VERIFY-FAILED cleanup deferred to the first true idle tick + janitor-undo.
# --------------------------------------------------------------------------- #
_GOAL_1104 = (
    "/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, both "
    "checkable from the transcript: work the whole backlog one ticket at a time "
    "until every workable issue is closed and CI is all-green. END.")


class TestVerifyFailedDefersCleanupOnLiveTurn(unittest.TestCase):
    CWD = "/home/newlevel/devel/vfdefer1104"
    SID = "sess-vfdefer-1104"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _cap_with_stranded_goal_and_waiting(self):
        # Our own /goal stranded in a bare-modelled box, with a Waiting line
        # above it (the live incident: text in the box, session busy).
        head = _GOAL_1104
        return (
            "● Predošlá práca hotová.\n"
            "✻ Waiting for 3 background agents to finish\n"
            "❯ " + head + "\n"
            "  ctx ███░  caveman:lite\n")

    def test_live_turn_defers_cleanup_and_sets_janitor_watch(self):
        # RED: pre-fix `_log_arm_confirm_fail` logs `cleanup=declined (non-input
        # boundary)` and sets NO watch, so the stranded /goal waits for the hourly
        # floor. GREEN: `cleanup=deferred(live-turn)` + the #372 watch set, so the
        # shared janitor recovers it at the first true idle tick.
        cap = self._cap_with_stranded_goal_and_waiting()
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        state = {}
        now = 100000
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(self.SID, self.CWD, _GOAL_1104, "%9", tmux,
                                       state=state, now=now)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("cleanup=deferred(live-turn)", log)
        self.assertTrue(wd._janitor_watch_seen(state, "%9", now),
                        "the deferred cleanup must set the #372 janitor watch")
        # never Escape a live turn: the box keeps our text for now.
        self.assertEqual(tmux.sent, [], "no keystroke into a live/busy turn")

    def test_shared_janitor_clears_the_stranded_goal_at_next_idle(self):
        # The next TRUE idle tick: with the watch set, the shared janitor
        # (`_janitor_recover`) recognizes our own leftover (own_payload=template)
        # and clears the box. This is the deferred cleanup firing. The fake models
        # a box only on a BARE `❯` seed line + `initial_box`.
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], CLEAN_IDLE,
                                   model_type=True, initial_box=_GOAL_1104)
        state = {"janitor_watch": {"%9": 100000}}
        rec = {}
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            jlogs = wd._janitor_recover(tmux, rec, "%9", self.CWD, tmux._render(),
                                        "loc", lambda *a, **k: None, False,
                                        lambda s: None, state=state, now=100001,
                                        own_payload=_GOAL_1104)
        self.assertEqual(tmux.box, "", "the stranded /goal must be cleared at idle")
        self.assertTrue(any("RECOVERED (janitor)" in ln for ln in jlogs), jlogs)

    def test_janitor_undo_clears_our_stranded_box_with_a_janitor_undo_line(self):
        # The #1092 sibling verb the design names: `_janitor_undo_if_own_stranded`
        # clears our own stranded text and emits a `janitor-undo` line.
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], CLEAN_IDLE,
                                   model_type=True, initial_box=_GOAL_1104)
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            "%9", tmux, _GOAL_1104, "loc", lambda s: None, logs)
        self.assertTrue(cleared)
        self.assertEqual(tmux.box, "")
        self.assertTrue(any("janitor-undo" in ln for ln in logs), logs)

    def test_foreign_leftover_still_declines_not_deferred(self):
        # CONTROL: a foreign draft (not our own) still DECLINES (untouched),
        # never deferred — the #737 fail-safe. `cleanup=declined` stays.
        cap = ("● Hotovo.\n"
               "✻ Waiting for 3 background agents to finish\n"
               "❯ moja vlastná dlhá poznámka k tiketu ktorú som napísal\n"
               "  ctx ███░  caveman:lite\n")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        state = {}
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(self.SID, self.CWD, _GOAL_1104, "%9", tmux,
                                       state=state, now=100000)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("cleanup=declined", log)
        self.assertFalse(wd._janitor_watch_seen(state, "%9", 100000),
                         "a foreign draft never arms the deferred cleanup")


# --------------------------------------------------------------------------- #
# 4. ATTEMPT BOOKING — only a REAL (not OFF-suppressed) keystroke consumes the
#    per-pane budget.
# --------------------------------------------------------------------------- #
# A template DRIFT (OLD armed wording vs NEW shipped template) so the stale-rearm
# actually PROCEEDS to the send instead of `drop:already-current`.
_ARMED_OLD = ("STOP CONDITIONS — the loop is DONE the moment EITHER holds, both "
              "checkable from the transcript: (A) an OLDER wording of the stop "
              "conditions, from before the shipped template changed.")
_REARM_TMPL = ("/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, "
               "both checkable from the transcript: (A) the NEW wording carrying "
               "the saturation clause: SATURATE parallel isolation:worktree lanes.")


class TestAttemptBookingOnlyOnRealSend(unittest.TestCase):
    CWD = "/home/newlevel/devel/bookrb1104"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, sid, state, nudges_on):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + _ARMED_OLD,
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True)
        ctx = []
        # a NON-declared window keeps the staged `goal-sweep` (suppressible) nudge.
        ctx.append(m.patch.object(goal, "_declared_window_nudge",
                                  return_value="goal-sweep"))
        ctx.append(m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                                  return_value=(False, "test")))
        # nudges_on=False → goal-sweep OFF (keys suppressed, nothing typed).
        ctx.append(m.patch.object(wd, "nudges_enabled",
                                  side_effect=lambda k=None, **kw: bool(nudges_on)))
        for p in ctx:
            p.start()
            self.addCleanup(p.stop)
        now = 100000
        word = goal.deliver_goal(
            sid, self.CWD, _REARM_TMPL, "branch-merge", run=tmux, projects_dir=proj,
            now=now, request_ts=now, sleep_fn=lambda s: None, state=state,
            origin="stale-rearm")
        return word, tmux

    def test_off_suppressed_send_leaves_the_budget_untouched(self):
        # RED: pre-fix `mark_pane_attempt` fires regardless of the send outcome,
        # so an OFF-suppressed send (nothing typed) still burns a budget slot.
        state = {}
        word, tmux = self._deliver("sess-off-rb", state, nudges_on=False)
        self.assertEqual(tmux.sent, [], "OFF suppresses the keystroke entirely")
        self.assertEqual(state.get("nudge_pane_attempts", {}).get("%9", []), [],
                         "an OFF-suppressed send must not consume the pane budget")
        from watchdog import nudge_gate as ng
        self.assertTrue(ng.pane_budget_ok(state, "%9", 100000))

    def test_real_send_books_one_attempt(self):
        # Regression lock: a genuinely delivered re-arm keystroke IS booked.
        state = {}
        word, tmux = self._deliver("sess-on-rb", state, nudges_on=True)
        self.assertEqual(word, "sent")
        self.assertEqual(
            len(state.get("nudge_pane_attempts", {}).get("%9", [])), 1,
            "a really sent keystroke books exactly one attempt")


# --------------------------------------------------------------------------- #
# 5. CLI GUARD — `nudges on --kind <keystroke kind>` warns on a busy pane.
# --------------------------------------------------------------------------- #
class TestNudgesEnableWarnsOnBusyPane(unittest.TestCase):
    CWD = "/home/newlevel/devel/clibusy1104"

    def _fake(self, captured):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   captured, model_type=True)

    def test_pane_warning_helper_flags_a_busy_pane(self):
        import airuleset
        warns = airuleset._nudges_keystroke_pane_warnings(
            run=self._fake(WAITING_ABOVE_BARE))
        self.assertTrue(any("BUSY" in w and "%9" in w for w in warns), warns)

    def test_pane_warning_helper_flags_a_spinner_busy_pane(self):
        import airuleset
        warns = airuleset._nudges_keystroke_pane_warnings(
            run=self._fake(SPINNER_ABOVE_BARE))
        self.assertTrue(any("BUSY" in w for w in warns), warns)

    def test_pane_warning_helper_silent_on_idle_pane(self):
        import airuleset
        warns = airuleset._nudges_keystroke_pane_warnings(
            run=self._fake(CLEAN_IDLE))
        self.assertEqual(warns, [], warns)

    def test_nudges_on_keystroke_kind_prints_the_busy_warning(self):
        import airuleset
        with TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                args = types.SimpleNamespace(
                    nudges_action="on", kind="goal-sweep", all=False,
                    fleet=False)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = airuleset.cmd_nudges(args, run=self._fake(WAITING_ABOVE_BARE))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("BUSY", out)

    def test_nudges_on_non_keystroke_kind_prints_no_busy_warning(self):
        # #1104-review F4: `card` (a Discord notification) never types into a
        # pane, so enabling it must NOT print the busy-pane warning even when a
        # managed pane is busy.
        import airuleset
        with TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                args = types.SimpleNamespace(
                    nudges_action="on", kind="card", all=False, fleet=False)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = airuleset.cmd_nudges(args, run=self._fake(WAITING_ABOVE_BARE))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertNotIn("BUSY", out)


if __name__ == "__main__":
    unittest.main()
