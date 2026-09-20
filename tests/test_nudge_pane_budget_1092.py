"""#1092 — the per-pane typing-attempt budget (item c).

A brake INDEPENDENT of the per-kind floor + cross-kind total cap, keyed on the
PANE (pid) not the session (sid): at most `PANE_ATTEMPT_BUDGET` (2) machine
typing attempts per pane per rolling `PANE_ATTEMPT_WINDOW_S` (1 h), across ALL
kinds. Even if a future outcome escapes the per-kind floor stamp (the #1092
swallowed-batch storm was exactly that), the pane can never receive more than two
typing attempts an hour.

RED against the pre-#1092 tree: `nudge_gate` has no `PANE_ATTEMPT_BUDGET` /
`pane_budget_ok` / `mark_pane_attempt` / `pane_budget_hold_reason` at all.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import nudge_gate as ng  # noqa: E402

NOW = 1_000_000
HOUR = 3600


class TestPaneBudgetPrimitives(unittest.TestCase):
    def test_budget_constant_is_two_and_window_one_hour(self):
        self.assertEqual(ng.PANE_ATTEMPT_BUDGET, 2)
        self.assertEqual(ng.PANE_ATTEMPT_WINDOW_S, HOUR)

    def test_empty_state_allows(self):
        self.assertTrue(ng.pane_budget_ok({}, "%1", NOW))
        self.assertTrue(ng.pane_budget_ok(None, "%1", NOW))

    def test_third_attempt_refused_within_the_hour(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        self.assertTrue(ng.pane_budget_ok(state, "%1", NOW + 60))
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        # two attempts in the window -> the third is refused BEFORE any keystroke
        self.assertFalse(ng.pane_budget_ok(state, "%1", NOW + 120))

    def test_a_different_pane_has_its_own_budget(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        self.assertFalse(ng.pane_budget_ok(state, "%1", NOW + 120))
        # a DIFFERENT pane is unaffected
        self.assertTrue(ng.pane_budget_ok(state, "%2", NOW + 120))

    def test_budget_reopens_after_the_rolling_hour(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        self.assertFalse(ng.pane_budget_ok(state, "%1", NOW + 120))
        # past the window from the OLDEST attempt: it ages out -> one slot frees
        self.assertTrue(ng.pane_budget_ok(state, "%1", NOW + HOUR + 1))

    def test_hold_reason_names_count_and_a_clock_time(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        ng.mark_pane_attempt(state, "%1", NOW + 60)
        reason = ng.pane_budget_hold_reason(state, "%1", NOW + 120)
        self.assertIn("hold:pane-budget", reason)
        self.assertIn("2 attempts since", reason)

    def test_mark_prunes_its_own_list_to_the_window_on_write(self):
        state = {}
        ng.mark_pane_attempt(state, "%1", NOW)
        # a later mark far outside the window drops the stale entry, keeps state bounded
        ng.mark_pane_attempt(state, "%1", NOW + 2 * HOUR)
        self.assertEqual(len(state["nudge_pane_attempts"]["%1"]), 1)

    def test_malformed_state_reads_as_no_prior_attempt(self):
        self.assertTrue(ng.pane_budget_ok({"nudge_pane_attempts": "junk"}, "%1", NOW))
        self.assertTrue(ng.pane_budget_ok(
            {"nudge_pane_attempts": {"%1": "junk"}}, "%1", NOW))
        # a future-skewed ts is ignored (fail-safe ALLOW, mirroring _gate_ts)
        self.assertTrue(ng.pane_budget_ok(
            {"nudge_pane_attempts": {"%1": [NOW + 10 * HOUR, NOW + 11 * HOUR]}},
            "%1", NOW))

    def test_prune_reaps_a_gone_pane_with_no_in_window_attempt(self):
        state = {"nudge_pane_attempts": {"%gone": [NOW - 2 * HOUR],
                                         "%live": [NOW]}}
        ng.prune(state, visited_sids=set(), now=NOW)
        self.assertNotIn("%gone", state["nudge_pane_attempts"])
        self.assertIn("%live", state["nudge_pane_attempts"])


class TestSendVerifiedPaneBudget(unittest.TestCase):
    """#1092 (a)+(c) — the single path (`send_verified`) consults the pane budget
    BEFORE any keystroke and stamps a typing attempt + the per-kind floor AFTER
    a swallow. Driven through the SAME stateful fake tmux the send_verified suite
    uses (an accepted submit clears the box + appends a `user` turn; a swallowed
    one keeps the box)."""

    PID = "%9"
    TEXT = "štuchnutie: zaplň lány, backlog=5"

    def _tpath(self):
        import json
        from tempfile import TemporaryDirectory
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "sess.jsonl"
        p.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "prev"}}) + "\n")
        return p

    def _fake(self, enters_swallowed=0, transcript_path=None):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _goal_arm_helpers import DeliverGoalFakeTmux, GOAL_IDLE_CAP
        return DeliverGoalFakeTmux([(self.PID, "claude", "/x", "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   enters_swallowed=enters_swallowed,
                                   transcript_path=transcript_path)

    def test_pane_budget_refuses_the_third_gated_send_before_any_keystroke(self):
        import watchdog as wd
        import time as _t
        p = self._tpath()
        tmux = self._fake(transcript_path=p)
        now = _t.time()
        state = {"nudge_pane_attempts": {self.PID: [now - 10, now - 5]}}
        out, logs = {}, []
        ok = wd.send_verified(self.PID, self.TEXT, tmux, p,
                              sleep_fn=lambda s: None, logs=logs, out=out,
                              nudge="queue-arrival", state=state)
        self.assertFalse(ok)
        self.assertTrue(out.get("pane_budget_held"), logs)
        self.assertEqual(tmux.sent, [], "no keystroke fired on a budget refusal")
        self.assertTrue(any("hold:pane-budget" in ln for ln in logs), logs)

    def test_confirmed_gated_send_marks_a_pane_attempt(self):
        import watchdog as wd
        p = self._tpath()
        tmux = self._fake(transcript_path=p)
        state, out = {}, {}
        ok = wd.send_verified(self.PID, self.TEXT, tmux, p,
                              sleep_fn=lambda s: None, logs=[], out=out,
                              nudge="queue-arrival", state=state)
        self.assertTrue(ok)
        self.assertEqual(len(state.get("nudge_pane_attempts", {}).get(self.PID, [])), 1)
        self.assertTrue(out.get("attempted"))

    def test_genuine_swallow_stamps_floor_and_marks_attempt(self):
        import watchdog as wd
        p = self._tpath()
        tmux = self._fake(enters_swallowed=99, transcript_path=p)
        state, out = {}, {}
        ok = wd.send_verified(self.PID, self.TEXT, tmux, p,
                              sleep_fn=lambda s: None, logs=[], out=out,
                              nudge="queue-arrival", state=state)
        self.assertFalse(ok)
        self.assertTrue(out.get("swallowed"), out)
        # a swallowed attempt IS a delivery attempt: per-kind floor stamped for sid
        self.assertIn("queue-arrival",
                      state.get("nudge_cadence", {}).get("sess", {}))
        # and it counts toward the pane budget
        self.assertEqual(len(state.get("nudge_pane_attempts", {}).get(self.PID, [])), 1)

    def test_recovery_kind_is_not_blocked_by_the_pane_budget(self):
        # a REVIVAL of a dead session must never be stranded by prior nudge
        # attempts into the same pane (#520 / #1092 addendum Approach-2 rejection).
        import watchdog as wd
        import time as _t
        p = self._tpath()
        tmux = self._fake(transcript_path=p)
        now = _t.time()
        state, out = {"nudge_pane_attempts": {self.PID: [now - 10, now - 5]}}, {}
        ok = wd.send_verified(self.PID, self.TEXT, tmux, p,
                              sleep_fn=lambda s: None, logs=[], out=out,
                              nudge="resume", state=state)
        self.assertFalse(out.get("pane_budget_held"))
        self.assertTrue(ok)   # the revival delivered despite an exhausted budget

    def test_owner_reply_is_not_blocked_by_the_pane_budget(self):
        import watchdog as wd
        import time as _t
        p = self._tpath()
        tmux = self._fake(transcript_path=p)
        now = _t.time()
        state, out = {"nudge_pane_attempts": {self.PID: [now - 10, now - 5]}}, {}
        ok = wd.send_verified(self.PID, self.TEXT, tmux, p,
                              sleep_fn=lambda s: None, logs=[], out=out,
                              nudge="queue-arrival", state=state, user_authored=True)
        self.assertFalse(out.get("pane_budget_held"))
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
