"""#921 RESIDUAL — *_attempts counters must record ONLY after verified
keystroke delivery, not at decision/request time.

(#1324 retired the auth-rearm / goal-sweep re-arm attempt caps together with
every automatic arm origin; the qdisarm cap, the idle-only riders and the
stash-abort livelock counter below remain.)

#1063 addendum EXCEPTION (the QDISARM cap only): a GENUINE `skip:verify-failed`
disarm (a keystroke typed but not verified) ALSO consumes a slot, so a
persistently unverifiable pane stops after GOAL_QDISARM_MAX_PER_DAY instead of a
~60 s re-type storm — see TestQdisarmAttemptOnSentOrVerifyFail (a kill-switch
SUPPRESSION, which types nothing, is never counted).
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


class TestQdisarmAttemptOnSentOrVerifyFail(unittest.TestCase):
    """#1063 addendum: the QDISARM attempt-cap counts EVERY attempted keystroke
    -- a landed `sent` AND a GENUINE `skip:verify-failed` (a keystroke typed but
    not verified) -- so a persistently unverifiable pane stops after
    GOAL_QDISARM_MAX_PER_DAY instead of re-typing `/goal clear` every ~60 s.
    A kill-switch SUPPRESSION types NOTHING and is NOT counted (defensive:
    goal-disarm is a RECOVERY kind, never staged off). This REVERSES the #921
    'record only on sent' narrowing FOR THE QDISARM CAP."""

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
