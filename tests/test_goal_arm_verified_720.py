"""#720 — VERIFIED, CHUNK-SAFE, BUSY-AWARE goal-arm delivery.

The owner's `/autopilot` self-callback armed a ~3900-char full-authority /goal
template that arrived ODSEKNUTÝ — the HEAD was swallowed, so CC read the text as
an ordinary prompt and the goal never armed (empty footer), yet `goal-arm`
returned "sent" and cleared the request (no auto-recovery). Root cause: the
bare-box send `_send_goal_verified` verified the type with the HEAD-BLIND
tail-only `_await_typed`/`_typed_landed` (a swallowed-head payload whose TAIL
rendered PASSES), then pressed Enter and returned True on box-cleared ALONE —
never confirming the goal actually armed.

This ticket routes the bare-box send through the #670 head-inclusive verified
typing primitive (`_type_literal_verified`) so a head-swallowed /goal is
undone+retyped byte-exact and NEVER submitted; adds the #714 `_pane_busy_waiting`
gate to `deliver_goal` so a "Waiting for N background agents" pane defers instead
of parking an orphaned /goal; and confirms `pane_goal_armed` after the submit
before returning "sent".

These drive the SAME stateful fake tmux the goal tests use (now modelling CC
arming a goal on a /goal submit), extended for the first-byte swallow.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import watchdog.goal as goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, GOAL_ARMED_CAP, GOAL_DRAFT_CAP,
    _SwallowFirstCharFake, _write_marker_transcript, _isolate_goal_state,
)

PID = "%9"
WRAP = 60

# A realistic ~3900-char full-authority /goal template (just under CC's 4000
# cap) — long enough that `_type_literal` CHUNKS it and it WRAPS at the fake
# width, so a swallowed FIRST char is invisible to a tail-only verify.
GOAL_ARM_3900 = ("/goal STOP CONDITIONS: " + (
    "all named issues closed AND CI all-green AND the PR mergeable and clean; "
    "work the backlog one ticket at a time until every workable issue is done, "
    "never stop early, surface per-ticket questions the moment they arise. " * 18
).strip() + " END.")   # ~3933 chars — just under CC's 4000 cap, the incident
#                        scale. NO trailing whitespace (a real template never has one;
#                        `_input_line_text` strips the boundary row, so a trailing
#                        space would break the tail-suffix match for a clean type)

# A NON-armed pane blocked on background workers: the box is a free bare `❯`
# (so `_classify_boundary` reads kind="input"), but a "Waiting for N background
# agents to finish" spinner sits ABOVE it (the #714 swallowed-submit state).
GOAL_WAITING_CAP = ("● Predošlá práca hotová.\n"
                    "✻ Waiting for 2 background agents to finish\n"
                    "❯ \n"
                    "  ctx ███░  caveman:lite\n")


def _tpath(testcase):
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    p = Path(d.name) / "sess.jsonl"
    p.write_text(json.dumps(
        {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
    return p


def _submitted_turns(p):
    """Every `user` turn the fake recorded — i.e. every prompt SUBMITTED."""
    out = []
    for ln in p.read_text().splitlines():
        e = json.loads(ln)
        if e.get("type") == "user":
            out.append(e["message"]["content"])
    return out


class HeadSwallowedGoalArmNeverSubmitted(unittest.TestCase):
    def _fake(self, p, swallow_budget):
        return _SwallowFirstCharFake(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, wrap_width=WRAP, swallow_budget=swallow_budget)

    def test_head_swallowed_goal_arm_is_never_submitted(self):
        # The load-bearing #720 invariant: a head-swallowed /goal must NEVER be
        # submitted. Before the fix, the tail-only `_await_typed` passes the
        # swallowed-head payload and Enter submits it as a plain prompt (the
        # goal never arms). After the fix, the head-inclusive verify aborts.
        p = _tpath(self)
        tmux = self._fake(p, swallow_budget=99)          # every fresh type swallows
        ok = goal._send_goal_verified(PID, GOAL_ARM_3900, tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertFalse(ok)
        self.assertEqual(_submitted_turns(p), [],
                         "a head-swallowed /goal was SUBMITTED")

    def test_recovered_goal_arm_submits_byte_exact_3900(self):
        # A single-swallow race is RECOVERED: the retry re-types the full
        # ~3900-char template byte-exact and THAT is what gets submitted +
        # (with the pane arming) returns "sent".
        self.assertGreater(len(GOAL_ARM_3900), 3800)     # the incident scale
        self.assertLess(len(GOAL_ARM_3900), 4000)        # under CC's cap (#720-review)
        p = _tpath(self)
        tmux = self._fake(p, swallow_budget=1)
        ok = goal._send_goal_verified(PID, GOAL_ARM_3900, tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertTrue(ok, "a recoverable first-byte race must still arm")
        self.assertIn(GOAL_ARM_3900, _submitted_turns(p))


class ArmConfirmedBeforeSent(unittest.TestCase):
    def _fake(self, p, arm_on_submit):
        return DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, arm_on_submit=arm_on_submit)

    def test_submitted_but_unarmed_goal_returns_false(self):
        # #720 tail: a byte-perfect /goal that submits but does NOT arm (CC read
        # it as a plain prompt -> no `◎ /goal` footer) must NOT return "sent" —
        # else the goal is silently lost with the request cleared. Box-cleared
        # alone is not proof of arming.
        p = _tpath(self)
        tmux = self._fake(p, arm_on_submit=False)
        ok = goal._send_goal_verified(PID, "/goal STOP CONDITIONS ok", tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertFalse(ok)

    def test_submitted_and_armed_goal_returns_true(self):
        # Control: a /goal that DOES arm (footer shows `◎ /goal`) still returns
        # True — the happy path is not broken by the arm-confirm.
        p = _tpath(self)
        tmux = self._fake(p, arm_on_submit=True)
        ok = goal._send_goal_verified(PID, "/goal STOP CONDITIONS ok", tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertTrue(ok)


class ClearIsNotArmConfirmed(unittest.TestCase):
    def test_goal_clear_disarm_returns_true_without_an_arm_check(self):
        # `_send_goal_verified` is ALSO the `/goal clear` disarm path
        # (`_deliver_goal_clear`, verify_armed=False). A clear DISARMS, so the
        # #720 arm-confirm must be SKIPPED for it — else a successful disarm
        # (pane_goal_armed -> False afterwards) would be mis-read as a failure.
        p = _tpath(self)
        tmux = DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p)
        ok = goal._send_goal_verified(PID, "/goal clear", tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[],
                                      verify_armed=False)
        self.assertTrue(ok)
        self.assertIn("/goal clear", _submitted_turns(p))


class BusyWaitingPaneDefers(unittest.TestCase):
    SID = "sess-720-busy"
    CWD = "/home/newlevel/devel/goal720"

    def setUp(self):
        _isolate_goal_state(self)

    def _deliver(self, captured):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, model_type=True)
        word = goal.deliver_goal(self.SID, self.CWD, "/goal STOP CONDITIONS x",
                                 "full", run=tmux, projects_dir=proj,
                                 sleep_fn=lambda s: None)
        return word, tmux

    def test_waiting_for_bg_agents_pane_defers_without_keystroke(self):
        # #720/#714: `deliver_goal` must NOT type into a pane showing "Waiting
        # for N background agents to finish" — the box shows a bare `❯` so
        # `_classify_boundary` passes, but a submit is swallowed and the /goal
        # parks orphaned. Defer (skip:busy), ZERO keystrokes.
        word, tmux = self._deliver(GOAL_WAITING_CAP)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])

    def test_idle_pane_without_the_waiting_line_still_sends(self):
        # Control: the SAME path on a genuinely idle pane (no Waiting line)
        # still delivers — the gate is narrow, it does not block a real arm.
        word, tmux = self._deliver(GOAL_IDLE_CAP)
        self.assertEqual(word, "sent")


class ArmConfirmPollIterates(unittest.TestCase):
    # #720-review 🔵5 — the arm-confirm poll must ITERATE for a LATE arm (CC
    # arms a moment after Enter), and give up False on a never-arming submit.
    def test_a_late_arm_is_confirmed(self):
        tmux = DeliverGoalFakeTmux(
            [(PID, "s", "1", "1")], GOAL_IDLE_CAP,
            cap_seq=[GOAL_IDLE_CAP, GOAL_IDLE_CAP, GOAL_ARMED_CAP])
        self.assertTrue(goal._await_goal_armed(PID, tmux, lambda s: None))

    def test_a_never_arming_submit_gives_up_false(self):
        tmux = DeliverGoalFakeTmux([(PID, "s", "1", "1")], GOAL_IDLE_CAP,
                                   cap_seq=[GOAL_IDLE_CAP])
        self.assertFalse(goal._await_goal_armed(PID, tmux, lambda s: None))


class ClearPathViaDeliverGoalClear(unittest.TestCase):
    # #720-review 🟡1 — teeth for the CLEAR path's two #720 fix lines.
    def setUp(self):
        _isolate_goal_state(self)

    def test_disarm_returns_sent_without_an_arm_confirm(self):
        # A `/goal clear` DISARMS (footer goes dark). `_deliver_goal_clear` passes
        # verify_armed=False, so the #720 arm-confirm is SKIPPED — else a
        # successful disarm (fake strips ◎ on the clear submit) would read as
        # skip:verify-failed. Teeth for the verify_armed=False call-site wiring.
        p = _tpath(self)
        tmux = DeliverGoalFakeTmux([(PID, "s", "1", "1")], GOAL_ARMED_CAP,
                                   model_type=True, transcript_path=p)
        word = goal._deliver_goal_clear(PID, "/goal clear", tmux, GOAL_ARMED_CAP,
                                        {}, 1_000_000, lambda s: None, [])
        self.assertEqual(word, "sent")
        self.assertIn("/goal clear", _submitted_turns(p))

    def test_disarm_defers_on_a_waiting_pane_without_keystroke(self):
        # Teeth for the _deliver_goal_clear busy-Waiting gate: never type
        # `/goal clear` into a "Waiting for N background agents" pane.
        tmux = DeliverGoalFakeTmux([(PID, "s", "1", "1")], GOAL_WAITING_CAP,
                                   model_type=True)
        word = goal._deliver_goal_clear(PID, "/goal clear", tmux,
                                        GOAL_WAITING_CAP, {}, 1_000_000,
                                        lambda s: None, [])
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])


class StashArmRouteConfirmsArmed(unittest.TestCase):
    # #720-review 🟡2 (A-2) — the stash arm route also confirms the goal armed,
    # closing the silent-"sent" hole on the draft route (not just the bare box).
    SID = "sess-720-stash"
    CWD = "/home/newlevel/devel/goal720stash"

    def setUp(self):
        _isolate_goal_state(self)

    def test_stash_submit_that_never_arms_is_not_reported_sent(self):
        import unittest.mock as m
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_DRAFT_CAP, model_type=True)
        # deliver_with_stash "succeeds" but the pane never arms (CC read the
        # submitted /goal as a plain prompt) -> must be skip:verify-failed.
        with m.patch.object(wd, "deliver_with_stash", return_value=True):
            word = goal.deliver_goal(self.SID, self.CWD, "/goal STOP CONDITIONS x",
                                     "full", run=tmux, projects_dir=proj,
                                     sleep_fn=lambda s: None)
        self.assertEqual(word, "skip:verify-failed")


if __name__ == "__main__":
    unittest.main()
