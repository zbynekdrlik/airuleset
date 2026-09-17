"""#921 RESIDUAL — *_attempts counters must record ONLY after verified
keystroke delivery, not at decision/request time.

RED reproduces the m1 shape: N auth-rearm decisions are made (each calling
record_goal_request), but deliver_goal never returns "sent" (blocked by
busy-waiting). The *_attempts state accumulates N entries, the 12/day cap
fires, and auth-rearm is permanently skipped even after the blocker clears.

The invariant: an *_attempts entry is written ONLY when deliver_goal returns
"sent" — never at decision time when the request is merely RECORDED.

#1063 addendum EXCEPTION (the QDISARM cap only): a GENUINE `skip:verify-failed`
disarm (a keystroke typed but not verified) ALSO consumes a slot, so a
persistently unverifiable pane stops after GOAL_QDISARM_MAX_PER_DAY instead of a
~60 s re-type storm — see TestQdisarmAttemptOnSentOrVerifyFail. The
"record only on sent" invariant above still holds for the auth-rearm / goal-sweep
caps (a kill-switch SUPPRESSION, which types nothing, is never counted).
"""

import json
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                    # noqa: E402
from watchdog import goal                                # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    _isolate_goal_state,
    _write_marker_transcript,
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _encode,
)

_QREPOKE_Q = "❓ NEEDS YOU: schváliš prístup A alebo B?"


def _write_repoke_transcript(pd, cwd, sid, n=6):
    """`n` assistant `❓ NEEDS YOU` turns with a machine `continue` re-poke
    between each (the real stuck-`/goal` loop shape)."""
    d = Path(pd) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    entries = []
    for _ in range(n):
        entries.append({"type": "user", "message": {"content": "continue"}})
        entries.append({"type": "assistant",
                        "message": {"content": "Work.\n\n" + _QREPOKE_Q}})
    p = d / (sid + ".jsonl")
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                 encoding="utf-8")
    return p

# The autopilot goal condition must start with this signature to pass the
# foreign-goal guard in _auth_rearm_decide / _classify_armed_condition.
_AUTOPILOT_GOAL_TEXT = (
    "STOP CONDITIONS — the loop is DONE the moment EITHER holds: "
    "(A) test condition for #921"
)


class TestAuthRearmAttemptOnlyOnDelivery(unittest.TestCase):
    """auth_rearm_attempts must NOT be recorded when deliver_goal never
    returns "sent" (the m1 12-undelivered-attempts bug)."""

    CWD = "/home/newlevel/devel/attempts921"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        self.state = {}

    def test_undelivered_auth_rearm_does_not_fill_cap(self):
        """12 auth-rearm decisions where delivery is blocked must NOT fill
        the 12/day cap — the 13th decision must still be allowed."""
        proj_td = TemporaryDirectory()
        self.addCleanup(proj_td.cleanup)
        proj = Path(proj_td.name)
        sid = "sess-auth-rearm-cap"
        _write_marker_transcript(proj, self.CWD, sid)

        auth_attempts = self.state.setdefault("goal_auth_rearm_attempts", {})

        # Simulate 12 auth-rearm decisions by directly recording attempts
        # the way _auth_rearm_decide does today (recording at decision time).
        # Each also writes a goal request, simulating the full decision flow.
        base_t = 100000.0
        for i in range(12):
            t = base_t + i * 400  # 400s apart (> 300s min gap)
            # _auth_rearm_decide records the attempt AND creates a request.
            # We simulate the effect: attempts accumulated, request created
            # but never delivered (busy-waiting blocks deliver_goal).
            # First clear any prior request so _auth_rearm_decide proceeds.
            goal.clear_goal_request(sid, path=self.reqp)
            # Call _auth_rearm_decide — it will check the rate limit,
            # record the attempt, and write a request.
            result = goal._auth_rearm_decide(
                sid, self.CWD,
                {"state": "cleared", "clear_kind": "auth",
                 "payload": _AUTOPILOT_GOAL_TEXT},
                False,  # armed=False
                t, "test:0.0", False,  # dry_run=False
                lambda cwd: (_AUTOPILOT_GOAL_TEXT, "full"),  # rearm_fn
                lambda cwd: (5, t - 10),  # obligation_fn: 5 open, fresh cache
                self.reqp,
                auth_attempts,
            )
            self.assertIsNotNone(result, "decision %d should produce a log" % i)
            self.assertIn("recording re-arm", result,
                          "decision %d should record a re-arm" % i)

        # After the fix: NO attempts recorded at decision time (recording
        # moved to delivery). The state dict should be empty.
        self.assertEqual(len(auth_attempts.get(sid, [])), 0,
                         "post-fix: 0 attempts recorded at decision time "
                         "(recording moved to delivery)")

        # The 13th decision — after clearing the pending request — must be
        # ALLOWED (no delivery happened, so no attempts recorded).
        t13 = base_t + 12 * 400
        goal.clear_goal_request(sid, path=self.reqp)
        result13 = goal._auth_rearm_decide(
            sid, self.CWD,
            {"state": "cleared", "clear_kind": "auth",
             "payload": _AUTOPILOT_GOAL_TEXT},
            False, t13, "test:0.0", False,
            lambda cwd: (_AUTOPILOT_GOAL_TEXT, "full"),
            lambda cwd: (5, t13 - 10),
            self.reqp,
            auth_attempts,
        )
        # THE FIX: after the fix, attempts are recorded only on delivery,
        # so auth_attempts should be empty (no delivery happened) and the
        # 13th decision should be allowed (recording re-arm, not RATE-LIMIT).
        self.assertIn("recording re-arm", result13,
                      "The 13th auth-rearm decision must be ALLOWED when "
                      "none of the prior 12 were ever delivered (the m1 bug: "
                      "undelivered attempts filled the cap permanently)")


class TestQdisarmAttemptOnSentOrVerifyFail(unittest.TestCase):
    """#1063 addendum: the QDISARM attempt-cap counts EVERY attempted keystroke
    -- a landed `sent` AND a GENUINE `skip:verify-failed` (a keystroke typed but
    not verified) -- so a persistently unverifiable pane stops after
    GOAL_QDISARM_MAX_PER_DAY instead of re-typing `/goal clear` every ~60 s.
    A kill-switch SUPPRESSION types NOTHING and is NOT counted (defensive:
    goal-disarm is a RECOVERY kind, never staged off). This REVERSES the #921
    'record only on sent' narrowing FOR THE QDISARM CAP; the auth-rearm /
    goal-sweep caps keep the sent-only invariant (the sibling classes above)."""

    CWD = "/home/newlevel/devel/qdisarm921"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _armed_tmux(self, tpath):
        return DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP,
            model_type=True, transcript_path=str(tpath))

    def _run(self, proj, tmux, state, now=100000.0):
        return goal.goal_question_repoke_watch(
            now, run=tmux, state=state, projects_dir=proj,
            sleep_fn=lambda s: None, human_ts_fn=lambda tp: None)

    def test_genuine_verify_fail_consumes_a_slot(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        sid = "sess-vf"
        tpath = _write_repoke_transcript(proj, self.CWD, sid)
        tmux = self._armed_tmux(tpath)
        state = {}
        # nudges_enabled(goal-disarm) is True (recovery kind) -> the genuine
        # verify-fail branch, which records a slot but writes NO veto.
        with m.patch.object(goal, "_deliver_goal_clear",
                            return_value="skip:verify-failed"):
            logs = self._run(proj, tmux, state)
        self.assertEqual(
            len(state["goal_qdisarm_attempts"][sid]), 1,
            "a genuine verify-failed disarm must consume an attempt-cap slot")
        self.assertNotIn(
            sid, state.get("goal_disarmed_q", {}),
            "a verify-failed disarm writes no re-entry veto (goal not cleared)")
        self.assertTrue(any("disarm delivery FAILED" in ln for ln in logs))

    def test_kill_switch_suppression_consumes_no_slot(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        sid = "sess-suppressed"
        tpath = _write_repoke_transcript(proj, self.CWD, sid)
        tmux = self._armed_tmux(tpath)
        state = {}
        # A hypothetical future re-staging turns goal-disarm OFF: keys() types
        # nothing, so no attempt slot is consumed and the suppression is flagged.
        with m.patch.object(wd, "nudges_enabled",
                            side_effect=lambda kind=None, home=None: False):
            logs = self._run(proj, tmux, state)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertNotIn(
            sid, state.get("goal_qdisarm_attempts", {}),
            "a kill-switch-suppressed disarm consumes no attempt-cap slot")
        self.assertTrue(
            any("disarm suppressed: nudges OFF" in ln for ln in logs))


class TestGoalSweepRecordsAttemptOnDelivery(unittest.TestCase):
    """goal_sweep must record the *_attempts entry when deliver_goal
    returns "sent" for a tracked origin."""

    def test_sent_auth_rearm_records_attempt_in_state(self):
        """After goal_sweep delivers a "sent" for an auth-rearm origin,
        goal_auth_rearm_attempts[sid] must contain the delivery timestamp."""
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        proj = Path(td.name)
        reqp, syncp = _isolate_goal_state(self)
        state = {}
        CWD = "/home/newlevel/devel/attempts-sweep-921"
        sid = "sess-auth-sweep"
        _write_marker_transcript(proj, CWD, sid)

        now = 200000.0
        # Record a goal request with auth-rearm origin
        goal.record_goal_request(sid, CWD, "/goal autotest", "full",
                                 now=now - 10, path=reqp,
                                 origin="auth-rearm")

        from _goal_arm_helpers import DeliverGoalFakeTmux, GOAL_IDLE_CAP
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", CWD, sid)],
            GOAL_IDLE_CAP, model_type=True)

        logs = goal.goal_sweep(now, run=tmux, projects_dir=proj,
                               requests_path=reqp, sleep_fn=lambda s: None,
                               state=state)
        combined = "\n".join(logs)
        self.assertIn("sent", combined)

        # After the fix, goal_sweep must record the attempt on delivery
        auth_attempts = state.get("goal_auth_rearm_attempts", {})
        self.assertIn(sid, auth_attempts,
                      "After a 'sent' delivery of an auth-rearm origin, "
                      "goal_auth_rearm_attempts must record the sid "
                      "(attempt recorded on delivery, not at decision time)")
        self.assertEqual(len(auth_attempts[sid]), 1)


class TestRidersIdleOnly1023(unittest.TestCase):
    """#1023: the #921 aged busy-waiting OVERRIDE is REMOVED — every rider gates
    on the plain `_pane_busy_waiting` (busy ⇒ defer), NEVER the age-bounded
    `_busy_waiting_with_age` (which typed into a long-busy pane)."""

    def _src(self, module_name, fn_name):
        import importlib
        import inspect
        mod = importlib.import_module("watchdog." + module_name)
        return inspect.getsource(getattr(mod, fn_name))

    def test_riders_use_plain_pane_busy_waiting(self):
        for module_name, fn_name in (
                ("lane_reconcile", "goal_lane_reconcile_recheck"),
                ("queue_arrival_recheck", "goal_queue_arrival_recheck"),
                ("u_freshness", "goal_u_freshness_recheck"),
                ("release_gap", "goal_release_gap_recheck"),
                ("ops_wait_recheck", "goal_ops_wait_recheck")):
            src = self._src(module_name, fn_name)
            self.assertIn("_pane_busy_waiting(", src,
                          "%s must gate on the plain _pane_busy_waiting" % module_name)
            self.assertNotIn("_busy_waiting_with_age", src,
                             "%s must NOT use the removed aged override" % module_name)

    def test_aged_override_fully_removed(self):
        from watchdog import ops_wait_recheck
        self.assertFalse(hasattr(ops_wait_recheck, "_busy_waiting_with_age"))
        self.assertFalse(hasattr(ops_wait_recheck, "BUSY_WAITING_AGE_BOUND_S"))


class TestStashAbortLivelock(unittest.TestCase):
    """#921 residual item 3: the foreign-slot stash-abort livelock —
    counter exceeds cap, drop+re-create ping-pong, no escalation."""

    def test_abort_counter_preserved_across_request_expiry(self):
        """When a goal request expires (terminal) after N slot-occupied
        aborts, the abort counter must NOT be reset to 0 — the next
        request must inherit the accumulated count so the escalation
        threshold is reachable across request lifetimes."""
        import inspect
        src = inspect.getsource(goal.goal_sweep)
        # Find the terminal-word pop: `aborts.pop(sid, None)` inside the
        # `if word in _GOAL_TERMINAL_WORDS:` block.
        # After the fix, the pop should be conditioned — NOT popping
        # when the sid has accumulated slot-occupied aborts.
        lines = src.split("\n")
        # Find the TERMINAL_WORDS block
        terminal_idx = None
        for i, ln in enumerate(lines):
            if "_GOAL_TERMINAL_WORDS" in ln and "if word in" in ln:
                terminal_idx = i
                break
        self.assertIsNotNone(terminal_idx)
        # The pop should be conditioned, not unconditional.
        # After the fix: only pop aborts on "sent", not on all terminal words.
        # We check that aborts.pop is NOT in the generic terminal block,
        # but only in the "sent" branch.
        terminal_block = "\n".join(lines[terminal_idx:terminal_idx + 5])
        self.assertNotIn("aborts.pop(sid",
                         terminal_block,
                         "aborts.pop must NOT be in the generic terminal-word "
                         "block — only in the 'sent' branch (preserve abort "
                         "history across request lifetimes)")

    def test_escalation_threshold_exists(self):
        """A GOAL_STASH_ABORT_ESCALATION constant must exist, larger than
        GOAL_STASH_ABORT_LIVELOCK, to bound the foreign-slot livelock."""
        self.assertTrue(hasattr(goal, "GOAL_STASH_ABORT_ESCALATION"),
                        "GOAL_STASH_ABORT_ESCALATION constant must exist")
        self.assertGreater(goal.GOAL_STASH_ABORT_ESCALATION,
                           goal.GOAL_STASH_ABORT_LIVELOCK,
                           "escalation must be > the livelock threshold")


if __name__ == "__main__":
    unittest.main()
