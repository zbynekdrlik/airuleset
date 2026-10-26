"""Bundle B9 fix round — the ONE-machine-nudge-per-pane-per-day cap (owner
26.10.2026: "mali by sa nudges zgrupovať a volať napr. len raz denne") after
its two adversarial reviews. Only a RECOVERY revival is immediate; every work
nudge waits for the pane's one daily batch, and a held send is journalled once
per (pane, kind, day) episode.
"""
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_goal_stranded_recovery_566 as stranded  # noqa: E402
import test_infra_priority_arrival_1109 as prio  # noqa: E402
import test_nudge_daily_owner_stop_1257 as b1257  # noqa: E402
import test_send_verified_adoption as adopt  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import nudge_gate as ng  # noqa: E402


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


class EveryDueKindFitsTheDailyBatch(prio._RiderBase):
    """Review finding 12: BATCH_MAX_CHARS (1400) trimmed the daily batch to
    two real rider texts, so partition-audit / u-freshness / goal-guard
    starved while release-gap was due every day. Since issue 1157 the batch
    body goes to a nudge FILE and ONE pointer row is typed: no cap."""

    def _real_texts(self):
        from watchdog import ops_wait_recheck, queue_arrival_recheck, release_gap
        from watchdog import u_freshness
        members = [{"number": n, "title": "Ticket %d title words here" % n,
                    "labels": ["ops-wait"]} for n in range(10)]
        return [
            ("release-gap", release_gap._nudge_text(
                7, "develop", "main", deploy_age_h=30.0, inactive_m=200.0)),
            ("partition-audit", ops_wait_recheck._nudge_text(
                12, members, prio.NOW, release_landed=[1, 2, 3],
                stagnation_count=3, deploy_window=[4, 5], deploy_miss=[6],
                spec_missing_n=2, spec_open=[7])),
            ("u-freshness", u_freshness._nudge_text(9)),
            ("queue-arrival", queue_arrival_recheck._nudge_text(
                list(range(5, 13)), 20,
                {n: "Some ticket title %d about something long" % n
                 for n in range(30)})),
            ("goal-guard", goal._GOAL_GUARD_TEXT)]

    def test_all_five_rider_texts_ride_one_batch(self):
        texts = self._real_texts()
        self.assertGreater(sum(len(t) for _c, t in texts), 1400)
        state, logs, ran = {}, [], []
        collect = [(c, t, (lambda c=c: ran.append(c))) for c, t in texts]
        goal._deliver_batch(collect, "%9", self.sid, "gk-infra:0", self.tpath,
                            self._tmux(), state, prio.NOW, set(), None,
                            lambda *a, **k: None, logs, None)
        cats = [c for c, _t in texts]
        self.assertTrue(any("batch-nudge" in ln and "5 section(s)" in ln
                            for ln in logs), logs)
        self.assertEqual(set(ng._session(state, self.sid)), set(cats))
        self.assertEqual(sorted(ran), sorted(cats))


class WorkingStallIsADailyWorkNudge(unittest.TestCase):
    """Review finding 13: job 4's working-stall stuck-check (⏳ WORKING, long
    silence, no subagent) asked a liveness question under the `resume`
    RECOVERY identity, so it skipped the daily cap. It is a work nudge: its own
    capped kind, held (never counted) while the pane's day is spent."""

    def _harness(self, seed_state=None):
        return adopt._RunOnceAdoptHarness(
            self, [adopt._working_row()], mtime_age=2000,
            capture=adopt.NO_BANNER_IDLE, seed_state=seed_state, now=time.time())

    def test_the_stuck_check_types_under_a_capped_kind(self):
        logs, _s, _k, _st, rec = self._harness().run(sv_result=True)
        self.assertEqual([c["nudge"] for c in rec.calls], ["working-stall"], logs)
        self.assertIn("working-stall", wd.MACHINE_NUDGE_KINDS)
        self.assertTrue(ng.pane_budgeted("working-stall"))
        import cli_fleet
        for profile in ("gk", "stream"):     # it keeps firing where loops run
            self.assertIn("working-stall", cli_fleet.NUDGE_PROFILES[profile])

    def test_a_spent_day_holds_it_without_counting_a_nudge(self):
        h = self._harness({"nudge_pane_attempts": {"%9": [time.time() - 60]}})
        logs, sent, _k, state, rec = h.run(sv_result=False)
        self.assertEqual(rec.calls, [], logs)
        self.assertNotIn("working:" + h.SID, state)
        self.assertEqual(sent, [])
        self.assertTrue(any("working-held" in ln and "pane-budget" in ln
                            for ln in logs), logs)


class OwnerActionsArePastTheCap(b1257._Pane):
    """Review finding 14: a ❓-flag prompt and a card-reopen nudge answer a
    VERIFIED owner action (the owner's own Discord reaction / reply), yet they
    typed under the machine `card` identity: the bare route was held by the
    daily cap (and the card switch), and a held card-reopen is fire-and-forget,
    so it was lost. Both are owner-authored on every route, the stash route
    included (which passed no `state` at all)."""
    FLAG = b1257.wd.compose_flag_prompt("Release 0.1.561 je nasadená.")

    def _spent(self):
        return {"nudge_pane_attempts": {self.PID: [time.time() - 60]}}

    def _deliver(self, fn, tmux, text, **kw):
        logs = []
        with m.patch.object(wd, "nudges_enabled", lambda kind: False):
            ok = fn(tmux, text, logs, **kw)
        return ok, logs

    def test_the_flag_prompt_types_past_the_cap_and_the_card_switch(self):
        tmux = self._tmux()
        ok, logs = self._deliver(
            lambda run, text, logs: wd._deliver_flag_prompt_to_exact_session(
                self.PID, run, text, False, logs=logs, tpath=self.tpath,
                state=self._spent(), now=time.time(), sleep_fn=lambda s: None),
            tmux, self.FLAG)
        self.assertTrue(ok, logs)
        self.assertIn("Užívateľ označil", "".join(tmux.typed_texts()))

    def test_a_card_reopen_types_past_the_cap(self):
        tmux = self._tmux()
        ok, logs = self._deliver(
            lambda run, text, logs: wd._nudge_repo_pane(
                self.PID, self.CWD, run, text, False, self.proj, logs=logs,
                tpath=self.tpath, state=self._spent(), now=time.time(),
                sleep_fn=lambda s: None),
            tmux, wd.compose_card_reopen_nudge(7))
        self.assertTrue(ok, logs)
        self.assertIn("ticket #7", "".join(tmux.typed_texts()))

    def test_the_stash_route_is_owner_authored_too(self):
        tmux = self._tmux(b1257.GOAL_DRAFT_CAP)
        _ok, logs = self._deliver(
            lambda run, text, logs: wd._deliver_flag_prompt_to_exact_session(
                self.PID, run, text, False, logs=logs, tpath=self.tpath,
                state=self._spent(), now=time.time(), sleep_fn=lambda s: None),
            tmux, self.FLAG)
        self.assertTrue(any("C-s" in a for a in tmux.sent), (logs, tmux.sent))
        self.assertFalse(any("suppressed" in ln or "pane-budget" in ln
                             for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
