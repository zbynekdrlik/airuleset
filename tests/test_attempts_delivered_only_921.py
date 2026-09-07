"""#921 RESIDUAL — *_attempts counters must record ONLY after verified
keystroke delivery, not at decision/request time.

RED reproduces the m1 shape: N auth-rearm decisions are made (each calling
record_goal_request), but deliver_goal never returns "sent" (blocked by
busy-waiting). The *_attempts state accumulates N entries, the 12/day cap
fires, and auth-rearm is permanently skipped even after the blocker clears.

The invariant: an *_attempts entry is written ONLY when deliver_goal returns
"sent" — never at decision time when the request is merely RECORDED.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import goal                                # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    _isolate_goal_state,
    _write_marker_transcript,
)

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


class TestQdisarmAttemptOnlyOnSent(unittest.TestCase):
    """goal_qdisarm_attempts must be recorded only when word == "sent",
    not on a failed delivery."""

    def test_failed_delivery_does_not_consume_slot(self):
        """A qdisarm delivery returning a non-sent, non-transient word
        must NOT consume an attempt slot. Content-lock: assert the
        recording is inside the 'if word == "sent":' branch."""
        import inspect
        src = inspect.getsource(goal.goal_question_repoke_watch)
        # Find the qdisarm attempt recording line
        lines = src.split("\n")
        recording_line_idx = None
        sent_block_idx = None
        for i, ln in enumerate(lines):
            stripped = ln.lstrip()
            if "attempts[sid] = pruned + [now]" in stripped:
                recording_line_idx = i
            if stripped.startswith("if word == \"sent\""):
                sent_block_idx = i

        self.assertIsNotNone(recording_line_idx,
                             "attempts[sid] = pruned + [now] must exist")
        self.assertIsNotNone(sent_block_idx,
                             'if word == "sent": block must exist')
        # The recording line must be AFTER the sent check (inside its block),
        # i.e., at a higher line index. On the current code it's BEFORE.
        self.assertGreater(recording_line_idx, sent_block_idx,
                           "attempts recording must be INSIDE the "
                           "'if word == \"sent\":' block, not before it "
                           "(only record on verified delivery)")


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


class TestRiderBusyWaitingAge(unittest.TestCase):
    """The 4 secondary rider sites must use _busy_waiting_with_age,
    not the unbounded _pane_busy_waiting."""

    def test_lane_reconcile_uses_age_bounded(self):
        """lane_reconcile.py must call _busy_waiting_with_age, not
        _pane_busy_waiting (the unbounded version)."""
        import inspect
        from watchdog import lane_reconcile
        src = inspect.getsource(lane_reconcile.goal_lane_reconcile_recheck)
        self.assertIn("_busy_waiting_with_age", src,
                      "lane_reconcile must use age-bounded busy-waiting")
        self.assertNotIn("._pane_busy_waiting(", src,
                         "lane_reconcile must NOT use unbounded "
                         "_pane_busy_waiting")

    def test_queue_arrival_uses_age_bounded(self):
        import inspect
        from watchdog import queue_arrival_recheck
        src = inspect.getsource(
            queue_arrival_recheck.goal_queue_arrival_recheck)
        self.assertIn("_busy_waiting_with_age", src,
                      "queue_arrival must use age-bounded busy-waiting")
        self.assertNotIn("._pane_busy_waiting(", src,
                         "queue_arrival must NOT use unbounded "
                         "_pane_busy_waiting")

    def test_u_freshness_uses_age_bounded(self):
        import inspect
        from watchdog import u_freshness
        src = inspect.getsource(u_freshness.goal_u_freshness_recheck)
        self.assertIn("_busy_waiting_with_age", src,
                      "u_freshness must use age-bounded busy-waiting")
        self.assertNotIn("._pane_busy_waiting(", src,
                         "u_freshness must NOT use unbounded "
                         "_pane_busy_waiting")

    def test_release_gap_uses_age_bounded(self):
        import inspect
        from watchdog import release_gap
        src = inspect.getsource(release_gap.goal_release_gap_recheck)
        self.assertIn("_busy_waiting_with_age", src,
                      "release_gap must use age-bounded busy-waiting")
        # release_gap inlines the regex — check it doesn't use the raw
        # _BG_AGENTS_WAIT_RX.search pattern for the busy gate
        self.assertNotIn("_BG_AGENTS_WAIT_RX.search", src,
                         "release_gap must NOT use raw regex for "
                         "busy-waiting (use _busy_waiting_with_age)")


if __name__ == "__main__":
    unittest.main()
