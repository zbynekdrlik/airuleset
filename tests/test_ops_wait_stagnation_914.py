"""#914 — W-stagnation tracking, tighter cadence, and escalation flag.

RED/GREEN pairs for:
  1. `_recheck_decision` uses tight cadence when W is non-shrinking;
  2. stagnation_count increments on non-shrinking W, resets on shrink;
  3. `_flag_items` renders W-STAGNATION flag at threshold;
  4. `_flag_items` renders owner-escalation at higher threshold;
  5. `_nudge_text` passes stagnation_count through to the flag;
  6. The orchestrator records w_count_at_nudge and stagnation_count.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog.ops_wait_recheck import (
    OPS_WAIT_RECHECK_CADENCE_S,
    OPS_WAIT_RECHECK_TIGHT_CADENCE_S,
    W_STAGNATION_FLAG_THRESHOLD,
    W_STAGNATION_OWNER_ESCALATE_THRESHOLD,
    _flag_items,
    _nudge_text,
    _recheck_decision,
)

NOW = 1_700_000_000
DAY = 86400
HOUR = 3600


class TestTightCadenceOnStagnation(unittest.TestCase):
    """_recheck_decision uses the tight cadence when W is stagnating."""

    def test_first_nudge_uses_normal_cadence(self):
        """A fresh rec (no w_count_at_nudge) uses the normal cadence, not tight."""
        rec = {"first_seen": NOW - 5 * HOUR, "last_nudge": None}
        # 5h < 22h normal cadence -> wait (NOT nudge even though > 4h tight)
        action, _, reason = _recheck_decision(rec, 0, [41, 43], NOW,
                                              OPS_WAIT_RECHECK_CADENCE_S)
        self.assertEqual(action, "wait",
                         "first nudge without prior w_count_at_nudge should use "
                         "normal cadence, not tight")

    def test_stagnating_w_uses_tight_cadence(self):
        """A stagnating W (count >= prior, stagnation_count >= 1) tightens."""
        rec = {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 5 * HOUR,  # 5h ago > 4h tight cadence
            "w_count_at_nudge": 30,
            "stagnation_count": 2,
        }
        # W=30 >= prior 30, stagnation >= 1 -> tight cadence (4h)
        # 5h > 4h -> due
        action, _, reason = _recheck_decision(rec, 0, list(range(30)), NOW,
                                              OPS_WAIT_RECHECK_CADENCE_S)
        self.assertEqual(action, "nudge",
                         "stagnating W (count >= prior) should use tight 4h cadence")

    def test_shrinking_w_resets_to_normal_cadence(self):
        """A shrinking W (count < prior) stays on normal cadence."""
        rec = {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 5 * HOUR,  # 5h ago
            "w_count_at_nudge": 30,
            "stagnation_count": 5,  # was stagnating, but W shrunk now
        }
        # W now = 10 < prior 30 -> NOT stagnating, use normal cadence
        # 5h < 22h normal -> wait
        action, _, reason = _recheck_decision(rec, 0, list(range(10)), NOW,
                                              OPS_WAIT_RECHECK_CADENCE_S)
        self.assertEqual(action, "wait",
                         "shrinking W should reset to normal cadence")


class TestStagnationCountTracking(unittest.TestCase):
    """_recheck_decision increments/resets stagnation_count in the rec."""

    def test_increments_on_nudge_with_non_shrinking_w(self):
        """stagnation_count increments when W count >= prior at nudge time."""
        rec = {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 25 * HOUR,
            "w_count_at_nudge": 20,
            "stagnation_count": 2,
        }
        action, new_rec, _ = _recheck_decision(
            rec, 0, list(range(20)), NOW, OPS_WAIT_RECHECK_CADENCE_S)
        self.assertEqual(action, "nudge")
        # stagnation_count is updated by the ORCHESTRATOR, not the decider
        # The decider preserves the prior value; the orchestrator bumps it
        self.assertEqual(new_rec["stagnation_count"], 2,
                         "decider preserves stagnation_count from rec")
        self.assertEqual(new_rec["w_count_at_nudge"], 20,
                         "decider preserves w_count_at_nudge from rec")

    def test_preserves_stagnation_for_wait(self):
        """In a wait verdict, stagnation fields are preserved from the rec."""
        rec = {
            "first_seen": NOW - 1 * HOUR,
            "last_nudge": NOW - 1 * HOUR,
            "w_count_at_nudge": 10,
            "stagnation_count": 3,
        }
        action, new_rec, _ = _recheck_decision(
            rec, 0, list(range(10)), NOW, OPS_WAIT_RECHECK_CADENCE_S)
        self.assertEqual(action, "wait")
        self.assertEqual(new_rec["stagnation_count"], 3)
        self.assertEqual(new_rec["w_count_at_nudge"], 10)


class TestStagnationFlag(unittest.TestCase):
    """_flag_items renders the W-STAGNATION flag at threshold."""

    def test_no_flag_below_threshold(self):
        flags = _flag_items([{"number": 41, "stale": False}], None,
                            stagnation_count=W_STAGNATION_FLAG_THRESHOLD - 1)
        joined = " ".join(flags)
        self.assertNotIn("W-STAGNATION", joined)

    def test_flag_at_threshold(self):
        flags = _flag_items([{"number": 41, "stale": False}], None,
                            stagnation_count=W_STAGNATION_FLAG_THRESHOLD)
        joined = " ".join(flags)
        self.assertIn("W-STAGNATION", joined)
        self.assertIn("#914", joined)

    def test_owner_escalation_above_high_threshold(self):
        flags = _flag_items([{"number": 41, "stale": False}], None,
                            stagnation_count=W_STAGNATION_OWNER_ESCALATE_THRESHOLD)
        joined = " ".join(flags)
        self.assertIn("W-STAGNATION", joined)
        self.assertIn("ownerovi", joined)

    def test_no_owner_escalation_below_high_threshold(self):
        flags = _flag_items([{"number": 41, "stale": False}], None,
                            stagnation_count=W_STAGNATION_OWNER_ESCALATE_THRESHOLD - 1)
        joined = " ".join(flags)
        if "W-STAGNATION" in joined:
            self.assertNotIn("ownerovi", joined)

    def test_default_stagnation_count_zero(self):
        """stagnation_count defaults to 0 — no flag without explicit count."""
        flags = _flag_items([{"number": 41, "stale": False}], None)
        joined = " ".join(flags)
        self.assertNotIn("W-STAGNATION", joined)


class TestNudgeTextStagnation(unittest.TestCase):
    """_nudge_text passes stagnation_count through to the flag."""

    def test_stagnation_in_nudge_text(self):
        text = _nudge_text(
            0, [{"number": 41, "stale": False}], NOW,
            stagnation_count=W_STAGNATION_FLAG_THRESHOLD)
        self.assertIn("W-STAGNATION", text)

    def test_no_stagnation_at_zero(self):
        text = _nudge_text(
            0, [{"number": 41, "stale": False}], NOW,
            stagnation_count=0)
        self.assertNotIn("W-STAGNATION", text)


class TestConstantValues(unittest.TestCase):
    """Lock the constant values for the stagnation mechanism."""

    def test_tight_cadence_shorter_than_normal(self):
        self.assertLess(OPS_WAIT_RECHECK_TIGHT_CADENCE_S,
                        OPS_WAIT_RECHECK_CADENCE_S)

    def test_tight_cadence_is_4h(self):
        self.assertEqual(OPS_WAIT_RECHECK_TIGHT_CADENCE_S, 4 * 3600)

    def test_flag_threshold_is_3(self):
        self.assertEqual(W_STAGNATION_FLAG_THRESHOLD, 3)

    def test_owner_escalation_threshold_is_5(self):
        self.assertEqual(W_STAGNATION_OWNER_ESCALATE_THRESHOLD, 5)

    def test_owner_threshold_above_flag_threshold(self):
        self.assertGreater(W_STAGNATION_OWNER_ESCALATE_THRESHOLD,
                           W_STAGNATION_FLAG_THRESHOLD)


class _OrchestratorBase(unittest.TestCase):
    """Harness for orchestrator-level stagnation tests, mirroring _OrchBase
    in test_ops_wait_recheck.py."""
    SID = "sess-914-stag"
    CWD = "/fake/odoo"

    def setUp(self):
        from tests.test_ops_wait_recheck import (  # noqa: F811
            CAD, DeliverGoalFakeTmux, GOAL_ARMED_CAP,
        )
        self.CAD = CAD
        self.FakeTmux = DeliverGoalFakeTmux
        self.GOAL_ARMED_CAP = GOAL_ARMED_CAP
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tpath = Path(self._tmpdir.name) / "transcript.jsonl"
        self.tpath.write_text("")
        self.addCleanup(self._tmpdir.cleanup)

    def _tmux(self, **kw):
        return self.FakeTmux([("%9", "claude", self.CWD, "111")],
                             self.GOAL_ARMED_CAP, model_type=True,
                             transcript_path=self.tpath, **kw)

    def _run(self, wrecs, fetch, tmux, *, state=None, i_count=0):
        from watchdog.ops_wait_recheck import goal_ops_wait_recheck
        return goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.SID, self.CWD, "%9", self.tpath, "sess:0",
            False, set(), ops_wait_fetch=fetch,
            state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, cadence=self.CAD, i_count=i_count)


class TestOrchestratorStagnation(_OrchestratorBase):
    """The orchestrator writes stagnation state ONLY on confirmed delivery."""

    def test_delivered_nudge_records_stagnation(self):
        """A delivered nudge writes w_count_at_nudge + stagnation_count."""
        wrecs = {self.SID: {"first_seen": NOW - 10 * DAY, "last_nudge": None}}
        logs = self._run(wrecs, lambda cwd: [41, 43], self._tmux())
        self.assertTrue(any("nudge" in ln for ln in logs))
        rec = wrecs[self.SID]
        self.assertEqual(rec["w_count_at_nudge"], 2)
        self.assertEqual(rec["stagnation_count"], 0)  # first nudge

    def test_stagnation_increments_across_delivered_nudges(self):
        """stagnation_count increments when W count >= prior at delivery."""
        wrecs = {self.SID: {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 25 * HOUR,
            "w_count_at_nudge": 2,
            "stagnation_count": 1,
        }}
        logs = self._run(wrecs, lambda cwd: [41, 43], self._tmux())
        self.assertTrue(any("nudge" in ln for ln in logs))
        rec = wrecs[self.SID]
        self.assertEqual(rec["stagnation_count"], 2)
        self.assertEqual(rec["w_count_at_nudge"], 2)

    def test_stagnation_resets_on_shrink(self):
        """stagnation_count resets to 0 when W count < prior at delivery."""
        wrecs = {self.SID: {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 25 * HOUR,
            "w_count_at_nudge": 5,
            "stagnation_count": 3,
        }}
        logs = self._run(wrecs, lambda cwd: [41, 43], self._tmux())
        self.assertTrue(any("nudge" in ln for ln in logs))
        rec = wrecs[self.SID]
        self.assertEqual(rec["stagnation_count"], 0)
        self.assertEqual(rec["w_count_at_nudge"], 2)

    def test_swallowed_send_does_not_inflate_stagnation(self):
        """F1 fix: a swallowed send must NOT increment stagnation_count."""
        wrecs = {self.SID: {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 25 * HOUR,
            "w_count_at_nudge": 2,
            "stagnation_count": 1,
        }}
        # enters_swallowed=99 makes send_verified always return False (swallow)
        tmux = self._tmux(enters_swallowed=99)
        self._run(wrecs, lambda cwd: [41, 43], tmux)
        rec = wrecs[self.SID]
        # stagnation_count MUST stay at 1 (the prior value), not increment
        self.assertEqual(rec.get("stagnation_count", 1), 1,
                         "F1: a swallowed send must NOT increment stagnation_count")
        # w_count_at_nudge MUST stay at 2 (the prior value), not update
        self.assertEqual(rec.get("w_count_at_nudge", 2), 2,
                         "F1: a swallowed send must NOT update w_count_at_nudge")

    def test_i_only_nudges_do_not_inflate_stagnation(self):
        """F2 fix: I-only nudges (W=0) must not inflate stagnation_count."""
        wrecs = {self.SID: {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 25 * HOUR,
            "w_count_at_nudge": 0,
            "stagnation_count": 2,
        }}
        state = {"goal_lane": {self.SID: {"llast": NOW - 1}}}
        self._run(wrecs, lambda cwd: [], self._tmux(),
                  i_count=3, state=state)
        rec = wrecs[self.SID]
        # After an I-only nudge, stagnation must NOT increment beyond prior
        # The W is empty, so stagnation_count should reset to 0
        self.assertEqual(rec.get("stagnation_count", 0), 0,
                         "F2: I-only nudges must not inflate stagnation_count")

    def test_log_carries_stagnation_token(self):
        """F5: the nudge log line must carry stag= and cadence= tokens."""
        wrecs = {self.SID: {
            "first_seen": NOW - 10 * DAY,
            "last_nudge": NOW - 25 * HOUR,
            "w_count_at_nudge": 2,
            "stagnation_count": 1,
        }}
        logs = self._run(wrecs, lambda cwd: [41, 43], self._tmux())
        nudge_logs = [ln for ln in logs if "nudge" in ln]
        self.assertTrue(nudge_logs, "expected a nudge log line")
        self.assertIn("stag=", nudge_logs[0])
        self.assertIn("cadence=", nudge_logs[0])


if __name__ == "__main__":
    unittest.main()
