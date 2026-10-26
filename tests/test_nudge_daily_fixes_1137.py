"""Bundle B9 fix round — the ONE-machine-nudge-per-pane-per-day cap (owner
26.10.2026: "mali by sa nudges zgrupovať a volať napr. len raz denne") after
its two adversarial reviews. Only a RECOVERY revival is immediate; every work
nudge waits for the pane's one daily batch, and a held send is journalled once
per (pane, kind, day) episode.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_goal_stranded_recovery_566 as stranded  # noqa: E402
import test_infra_priority_arrival_1109 as prio  # noqa: E402
import watchdog as wd  # noqa: E402
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


class PriorityWaveInTheBatch(prio._RiderBase):
    """Review finding 11: a release-blocking infra wave (#1109) that rides the
    daily batch lost its hub receipt (posted only on the direct path) and
    bypassed the owner's `infra-priority` switch (the batch filters on
    `queue-arrival`)."""
    PRIO = {"id": 5771, "kind": "comment", "num": 6883,
            "tag": "GATEKEEPER-ACTION (INFRA)", "permalink": "x"}

    def _collect(self, enabled=lambda kind: True):
        posts, state, bc = [], {}, []
        qrecs = {self.sid: {"base": [], "first_seen": prio.NOW - prio.DAY}}
        tmux = self._tmux()
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "nudges_enabled", enabled):
            logs = prio.qa.goal_queue_arrival_recheck(
                prio.NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath,
                "gk-infra:0", False, set(), queue_fetch=None, state=state,
                sleep_fn=lambda *a, **k: None, captured=None,
                infra_queue_fetch=lambda cwd: [self.PRIO],
                resolve_role_fn=lambda cwd: "infra",
                receipt_post_fn=lambda num, text: posts.append((num, text)),
                batch_collect=bc)
        return logs, bc, posts, state, tmux

    def test_a_delivered_batch_posts_the_hub_receipt(self):
        logs, bc, posts, state, tmux = self._collect()
        self.assertEqual([c for c, _t, _f in bc], ["queue-arrival"], logs)
        out = []
        goal._deliver_batch(bc, "%9", self.sid, "gk-infra:0", self.tpath, tmux,
                            state, prio.NOW, set(), None,
                            lambda *a, **k: None, out, None)
        self.assertEqual([n for n, _ in posts], [6883], out)
        self.assertTrue(any("hub receipt posted" in ln for ln in out), out)

    def test_the_infra_priority_switch_gates_the_batch_too(self):
        logs, bc, posts, _s, _t = self._collect(lambda k: k != "infra-priority")
        self.assertEqual(bc, [], logs)
        self.assertTrue(any("kind-off" in ln and "infra-priority" in ln
                            for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
