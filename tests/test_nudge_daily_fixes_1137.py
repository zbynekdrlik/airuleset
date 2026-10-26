"""Bundle B9 fix round — the ONE-machine-nudge-per-pane-per-day cap (owner
26.10.2026: "mali by sa nudges zgrupovať a volať napr. len raz denne") after
its two adversarial reviews. Only a RECOVERY revival is immediate; every work
nudge waits for the pane's one daily batch, and a held send is journalled once
per (pane, kind, day) episode.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_goal_stranded_recovery_566 as stranded  # noqa: E402
from watchdog import goal  # noqa: E402


class StrandedOwnGoalBudget(unittest.TestCase):
    """Review finding 10: submitting our own stranded `/goal` stamped the pane
    budget for EVERY watchdog re-arm origin, the recovery ones included
    (auth-rearm = the API-error resume of a loop, answer-rearm); the bare-box
    path already used `_rearm_budgeted`."""

    def setUp(self):
        stranded._isolate_goal_state(self)

    def _submit(self, origin):
        proj, tpath = stranded._proj_with_transcript(self)
        tmux = stranded.DeliverGoalFakeTmux(
            [("%9", "claude", stranded.CWD, "111")], stranded.GOAL_IDLE_CAP,
            model_type=True, initial_box=stranded.GOAL_TEXT,
            transcript_path=str(tpath))
        state = {}
        word = goal.deliver_goal(stranded.SID, stranded.CWD, stranded.GOAL_TEXT,
                                 "full", run=tmux, projects_dir=proj,
                                 now=2_000_000.0, state=state,
                                 request_ts=2_000_000.0,
                                 sleep_fn=lambda *a, **k: None, origin=origin)
        self.assertEqual(word, "sent", origin)
        return state.get("nudge_pane_attempts", {}).get("%9", [])

    def test_a_recovery_rearm_spends_no_daily_attempt(self):
        for origin in ("auth-rearm", "answer-rearm"):
            self.assertEqual(self._submit(origin), [], origin)

    def test_a_work_rearm_spends_the_day(self):
        self.assertEqual(self._submit("dark-rearm"), [2_000_000.0])


if __name__ == "__main__":
    unittest.main()
