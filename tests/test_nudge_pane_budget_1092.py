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


class TestBatchSwallowedFloorAndUndo(unittest.TestCase):
    """#1092 (a)+(b) — the #923 batch delivery block (an inline block in
    `goal.goal_lane_sweep`, so a source-lock, mutation-verified) must, on a
    SWALLOWED-with-attempt outcome, stamp the per-kind floor for ALL included
    kinds + write-through persist + run the janitor UNDO — and must NOT mis-stamp
    a pane-budget refusal (no keystroke) as a swallow."""

    def _src(self):
        import inspect
        from watchdog import goal
        return " ".join(inspect.getsource(goal.goal_lane_sweep).split())

    def test_swallowed_attempt_stamps_floor_for_all_included(self):
        src = self._src()
        # the swallow (attempted) branch stamps ALL included kinds + persists
        self.assertIn('send_out.get("attempted")', src)
        self.assertIn("_nudge_gate.mark_batch_sent(state, sid, _incl, now)", src)

    def test_pane_budget_refusal_is_not_treated_as_a_swallow(self):
        src = self._src()
        self.assertIn('send_out.get("pane_budget_held")', src)

    def test_janitor_undo_is_run_on_swallow_and_unconfirmed(self):
        src = self._src()
        self.assertIn("_janitor_undo_if_own_stranded", src)


class TestJanitorUndoHelper(unittest.TestCase):
    """#1092 (b) — `_janitor_undo_if_own_stranded` clears OUR OWN stranded
    nudge/goal text and leaves a foreign / clean box untouched."""

    PID = "%9"

    def _fake(self, box_text):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _goal_arm_helpers import DeliverGoalFakeTmux, GOAL_IDLE_CAP
        # a fake whose box holds `box_text` on the input line.
        tmux = DeliverGoalFakeTmux([(self.PID, "claude", "/x", "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        tmux.box = box_text
        return tmux

    def test_clears_our_own_nudge_prefix_text(self):
        from watchdog import goal
        tmux = self._fake("nudge: [queue-arrival] 3 new tickets to triage")
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            self.PID, tmux, "nudge: [queue-arrival] 3 new tickets to triage",
            "sess:1", lambda s: None, logs)
        self.assertTrue(cleared, logs)
        self.assertTrue(any("janitor-undo" in ln and "cleared" in ln for ln in logs), logs)

    def test_leaves_a_clean_box_untouched(self):
        from watchdog import goal
        tmux = self._fake("")   # bare box (submit accepted / already backed out)
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            self.PID, tmux, "nudge: [queue-arrival] x", "sess:1",
            lambda s: None, logs)
        self.assertFalse(cleared)
        self.assertTrue(any("box clean" in ln for ln in logs), logs)

    def test_leaves_a_foreign_draft_untouched(self):
        from watchdog import goal
        tmux = self._fake("toto je moja vlastná poznámka nič spoločné s nudge")
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            self.PID, tmux, "nudge: [queue-arrival] x", "sess:1",
            lambda s: None, logs)
        self.assertFalse(cleared)
        self.assertNotIn("BSpace", [x for a in tmux.sent for x in a])


class TestRearmThroughPaneBudget(unittest.TestCase):
    """#1092 (e) — a watchdog RE-ARM (`deliver_goal` for a watchdog re-arm origin)
    types into an ACTIVE stream pane, so it goes through the SAME per-pane budget +
    empty-box-at-idle precondition as a nudge (deliver_goal delivers via
    _send_goal_verified, not send_verified, so it is enforced there)."""

    CWD = "/home/newlevel/devel/rearmbudget"

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _goal_arm_helpers import _isolate_goal_state
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        from tempfile import TemporaryDirectory
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, sid, armed_cond, template, state, now=100000,
                 initial_box="", origin="stale-rearm"):
        import unittest.mock as m
        import watchdog as wd
        from watchdog import goal
        from _goal_arm_helpers import (
            DeliverGoalFakeTmux, GOAL_ARMED_CAP,
            _write_marker_transcript, _write_goal_marker)
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + armed_cond,
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   initial_box=initial_box)
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            word = goal.deliver_goal(
                sid, self.CWD, template, "branch-merge", run=tmux, projects_dir=proj,
                now=now, request_ts=now, sleep_fn=lambda s: None, state=state,
                origin=origin)
        return word, tmux

    _OLD = ("STOP CONDITIONS — the loop is DONE the moment EITHER holds, both "
            "checkable from the transcript: (A) an OLDER wording of the stop "
            "conditions, from before the shipped template changed.")
    _TMPL = ("/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds, "
             "both checkable from the transcript: (A) the NEW wording carrying "
             "the saturation clause: SATURATE parallel isolation:worktree lanes.")

    def test_rearm_deferred_when_pane_budget_exhausted(self):
        state = {"nudge_pane_attempts": {"%9": [100000 - 10, 100000 - 5]}}
        word, tmux = self._deliver("sess-rb-1", self._OLD, self._TMPL, state)
        self.assertEqual(word, "skip:pane-budget")
        self.assertEqual(tmux.sent, [], "no keystroke on a pane-budget defer")

    def test_rearm_into_bare_box_marks_a_pane_attempt(self):
        state = {}
        word, tmux = self._deliver("sess-rb-2", self._OLD, self._TMPL, state)
        self.assertEqual(word, "sent", "a stale-rearm REPLACE into a bare box types")
        self.assertEqual(len(state.get("nudge_pane_attempts", {}).get("%9", [])), 1)

    def test_rearm_deferred_on_a_foreign_draft_never_stashes(self):
        state = {}
        word, tmux = self._deliver("sess-rb-3", self._OLD, self._TMPL, state,
                                   initial_box="moja vlastná poznámka k tiketu")
        self.assertEqual(word, "skip:pane-busy-draft")
        # never typed / stashed around the owner's own draft
        self.assertNotIn("-l", [x for a in tmux.sent for x in a])


class TestStaleRearmOncePerTemplateVersion(unittest.TestCase):
    """#1092 (e) — a template change is NOT an emergency: `stale-rearm` records at
    most ONE re-arm per session per TEMPLATE VERSION (keyed on the shipped
    template hash), so a template deploy cannot storm every armed session's prompt
    sweep after sweep."""

    CWD = "/home/newlevel/devel/stalever"

    _SIG = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"
    _OLD = (_SIG + ", both checkable from the transcript: (A) an OLDER wording of "
            "the stop conditions, from before the shipped template changed.")
    _TMPL = ("/goal " + _SIG + ", both checkable from the transcript: (A) the NEW "
             "wording carrying the saturation clause: SATURATE parallel lanes.")
    _TMPL2 = _TMPL + " EXTRA v2 clause."

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _goal_arm_helpers import _isolate_goal_state
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        from tempfile import TemporaryDirectory
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _sweep(self, sid, tmpl, state, reqs, now=100000):
        from watchdog import goal
        from _goal_arm_helpers import (
            DeliverGoalFakeTmux, GOAL_ARMED_CAP, _write_marker_transcript,
            _write_goal_marker)
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + self._OLD,
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP)
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state=state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (7, now), rearm_fn=lambda cwd: (tmpl, "branch-merge"),
            requests_path=reqs, dry_run=False)
        return goal.load_goal_requests(reqs), logs

    def test_second_sweep_same_version_does_not_re_record(self):
        from watchdog import goal
        st = {}
        reqs = self._dir() / "goal-requests.json"
        r1, _ = self._sweep("sess-v1", self._TMPL, st, reqs)
        self.assertEqual(r1.get("sess-v1", {}).get("origin"), "stale-rearm")
        goal.clear_goal_request("sess-v1", path=reqs)
        r2, logs2 = self._sweep("sess-v1", self._TMPL, st, reqs)
        self.assertEqual(r2, {}, "same template version -> no second re-arm record")
        self.assertTrue(any("template version" in ln for ln in logs2), logs2)

    def test_a_new_template_version_re_records(self):
        from watchdog import goal
        st = {}
        reqs = self._dir() / "goal-requests.json"
        self._sweep("sess-v2", self._TMPL, st, reqs)
        goal.clear_goal_request("sess-v2", path=reqs)
        r2, _ = self._sweep("sess-v2", self._TMPL2, st, reqs)
        self.assertEqual(r2.get("sess-v2", {}).get("origin"), "stale-rearm",
                         "a changed template version re-arms again")


if __name__ == "__main__":
    unittest.main()
