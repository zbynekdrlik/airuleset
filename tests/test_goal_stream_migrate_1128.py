"""#1128 part 3 — the migration-only watcher that re-arms an ACHIEVED
OLD-template stream loop with the new (no-done-state) stream template.

Owner ruling (ROZHODNUTÉ on #1128, option A narrowed): a stream session whose
loop ended on the removed pre-#1128 (B) SLICE-EMPTY stop gets the current
template armed by the watchdog, but ONLY when ALL of these hold (else a
journalled skip):
  1. a reduced-authority box AND the recorded armed goal text carries the
     removed (B) (the old template) — migration-only;
  2. the session is idle: transcript idle >= 10 min and the pane shows the idle
     prompt (not "Waiting for background agents", not a running tool);
  3. no active `◎ /goal` footer (a still-running old loop is left alone);
  4. the pane runs `claude`, never a bare shell;
  5. the existing verified delivery, ONE attempt per session per hour.
Plus the positive achieved record the design asked for: the old template's own
`🏁 BACKLOG EMPTY:` line AFTER the arm (so a `❓`-ended loop is never touched —
the (A)-answer re-arm is a separate, later part).

Every test drives the PRODUCTION sweep shape: `goal_dark_watch` then
`deliver_goal` with the SAME sweep `state` (whose `goal_mark` is "set" — the
#1113 structured-armed refusal the migration origin must pass, and every other
origin must still hit).
"""

import json
import os
import sys
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import goal_registry as gr  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import stream_migrate as sm  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

_FIX = json.loads((Path(__file__).resolve().parent / "fixtures"
                   / "pre_1128_stream_goals.json").read_text(encoding="utf-8"))
OLD_FORK = _FIX["fork-no-merge"]
OLD_BRANCH = _FIX["branch-merge"]
NEW_FORK = gr.render("fork-no-merge")
CWD = "/home/david1/devel/odoo-erp"
_DONE = "Hotovo.\n🏁 BACKLOG EMPTY: 0 open, released\n✅ DONE: slice prázdny"
_Q = "Potrebujem rozhodnutie.\n❓ NEEDS YOU: ktorá možnosť A/B?"


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


class TestOldTemplateRecognition(unittest.TestCase):
    def test_both_pre_1128_stream_templates_are_old(self):
        self.assertTrue(sm.is_old_stream_payload(OLD_FORK))
        self.assertTrue(sm.is_old_stream_payload(OLD_BRANCH))

    def test_no_current_variant_and_no_full_template_is_old(self):
        for line in gr.all_goal_line_variants():
            self.assertFalse(sm.is_old_stream_payload(line), line[:80])
        self.assertFalse(sm.is_old_stream_payload(_FIX["full"]))
        self.assertFalse(sm.is_old_stream_payload(None))
        self.assertFalse(sm.is_old_stream_payload("/goal x"))


class TestStreamMigrate(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        self.now = float(int(time.time()))

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, payload=OLD_FORK, last=_DONE, idle_s=1200,
                 mark="Goal set: "):
        proj = self._dir()
        tpath = _write_marker_transcript(proj, CWD, sid, "warmup")
        _write_goal_marker(proj, CWD, sid, mark + payload, ts_epoch=500)
        entry = {"type": "assistant", "timestamp": _iso(600),
                 "message": {"id": "msg_done", "content": last}}
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        aged = self.now - idle_s
        os.utime(tpath, (aged, aged))
        return proj

    def _sweep(self, proj, cap=GOAL_IDLE_CAP, authority="fork-no-merge",
               state=None, cmd="claude", now=None, obl=(0, None)):
        state = {} if state is None else state
        tmux = DeliverGoalFakeTmux([("%9", cmd, CWD, "111")], cap)
        now = self.now if now is None else now
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state=state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (obl[0], obl[1] if obl[1] else now),
            rearm_fn=lambda cwd: (NEW_FORK, authority),
            requests_path=self.reqp)
        return goal.load_goal_requests(self.reqp), logs, state, tmux

    def _deliver(self, proj, sid, state, cap=GOAL_IDLE_CAP, cmd="claude",
                 req=None):
        req = req or goal.load_goal_requests(self.reqp)[sid]
        live = DeliverGoalFakeTmux([("%9", cmd, CWD, "111")], cap,
                                   model_type=True)
        with unittest.mock.patch.object(
                wd, "_goal_autoarm_recent_human_activity",
                return_value=(False, "")):
            word = goal.deliver_goal(
                sid, CWD, req["text"], req["authority"], run=live,
                projects_dir=proj, now=self.now + 30, origin=req["origin"],
                request_ts=req["ts"], sleep_fn=lambda s: None, state=state)
        return word, live

    def _forced(self, authority="fork-no-merge"):
        return {"text": NEW_FORK, "authority": authority,
                "origin": sm.ORIGIN, "ts": self.now}

    # --- fires -------------------------------------------------------------- #
    def test_fires_on_an_idle_old_template_dark_stream_pane(self):
        proj = self._fixture("m-ok")
        reqs, logs, state, tmux = self._sweep(proj)
        req = reqs.get("m-ok")
        self.assertIsInstance(req, dict, logs)
        self.assertEqual(req["origin"], sm.ORIGIN)
        self.assertEqual(req["text"], NEW_FORK)
        self.assertEqual(tmux.sent, [], "dark-watch only records")
        self.assertTrue(any("STREAM-MIGRATE" in ln for ln in logs), logs)
        self.assertEqual(state["goal_mark"]["m-ok"]["mark"]["state"], "set")
        word, live = self._deliver(proj, "m-ok", state)
        self.assertEqual(word, "sent", Path(self.syncp).read_text())
        self.assertTrue(any("-l" in a for a in live.sent), live.sent)

    def test_branch_merge_old_template_fires_too(self):
        proj = self._fixture("m-bm", payload=OLD_BRANCH)
        reqs, _l, state, _t = self._sweep(proj, authority="branch-merge")
        self.assertEqual(reqs["m-bm"]["origin"], sm.ORIGIN)
        self.assertEqual(self._deliver(proj, "m-bm", state)[0], "sent")

    def test_fires_whatever_the_obligation_cache_says(self):
        for sid, obl in (("m-open", (5, None)), ("m-none", (None, None))):
            proj = self._fixture(sid)
            reqs, _l, _s, _t = self._sweep(proj, obl=obl)
            self.assertEqual((reqs.get(sid) or {}).get("origin"), sm.ORIGIN, sid)

    # --- never fires -------------------------------------------------------- #
    def test_never_on_an_armed_pane(self):
        proj = self._fixture("m-armed")
        reqs, _l, state, _t = self._sweep(proj, cap=GOAL_ARMED_CAP)
        self.assertEqual(reqs, {})
        word, live = self._deliver(proj, "m-armed", state, cap=GOAL_ARMED_CAP,
                                   req=self._forced())
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_never_on_a_busy_pane(self):
        proj = self._fixture("m-busy")
        reqs, _l, state, _t = self._sweep(proj, cap=GOAL_BUSY_CAP)
        self.assertEqual(reqs, {})
        _r, _l, state, _t = self._sweep(proj)          # recorded while idle...
        word, live = self._deliver(proj, "m-busy", state, cap=GOAL_BUSY_CAP)
        self.assertTrue(word.startswith("skip:"), word)  # ...never typed busy
        self.assertEqual(live.sent, [])

    def test_never_on_a_recently_written_transcript(self):
        proj = self._fixture("m-fresh", idle_s=300)
        reqs, logs, _s, _t = self._sweep(proj)
        self.assertEqual(reqs, {})
        self.assertTrue(any("stream-migrate SKIP" in ln and "idle" in ln
                            for ln in logs), logs)

    def test_delivery_rechecks_the_idle_window(self):
        proj = self._fixture("m-toctou")
        _r, _l, state, _t = self._sweep(proj)
        tpath = next(proj.rglob("m-toctou.jsonl"))
        os.utime(tpath, (self.now - 60, self.now - 60))  # a turn ran since
        word, live = self._deliver(proj, "m-toctou", state)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_never_on_a_bare_shell(self):
        proj = self._fixture("m-shell")
        reqs, _l, state, _t = self._sweep(proj, cmd="bash")
        self.assertEqual(reqs, {})
        _r, _l, state, _t = self._sweep(proj)
        word, live = self._deliver(proj, "m-shell", state, cmd="bash")
        self.assertNotEqual(word, "sent")
        self.assertEqual(live.sent, [])

    def test_never_on_a_new_template_goal(self):
        proj = self._fixture("m-new", payload=NEW_FORK)
        reqs, _l, state, _t = self._sweep(proj)
        self.assertNotEqual((reqs.get("m-new") or {}).get("origin"), sm.ORIGIN)
        word, live = self._deliver(proj, "m-new", state, req=self._forced())
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_never_on_a_full_box(self):
        proj = self._fixture("m-full")
        reqs, _l, state, _t = self._sweep(proj, authority="full")
        self.assertNotEqual((reqs.get("m-full") or {}).get("origin"), sm.ORIGIN)
        word, live = self._deliver(proj, "m-full", state,
                                   req=self._forced("full"))
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_never_on_an_owner_cleared_goal(self):
        proj = self._fixture("m-clr", mark="Goal cleared: ")
        reqs, _l, _s, _t = self._sweep(proj)
        self.assertEqual(reqs, {})

    def test_never_on_a_question_ended_loop(self):
        proj = self._fixture("m-q", last=_Q)
        reqs, _l, _s, _t = self._sweep(proj)
        self.assertEqual(reqs, {})

    def test_other_origins_are_still_refused_on_an_old_template(self):
        proj = self._fixture("m-other")
        _r, _l, state, _t = self._sweep(proj)
        for origin in ("fulfilled-rearm", "dark-rearm", "answer-rearm"):
            req = dict(self._forced(), origin=origin)
            word, live = self._deliver(proj, "m-other", state, req=req)
            self.assertEqual(word, "drop:already-armed", origin)
            self.assertEqual(live.sent, [], origin)

    # --- one attempt per session per hour; journalled once ------------------ #
    def test_one_attempt_per_session_per_hour(self):
        proj = self._fixture("m-rate")
        _r, _l, state, _t = self._sweep(proj)
        goal.clear_goal_request("m-rate", path=self.reqp)  # delivered/dropped
        reqs, logs, state, _t = self._sweep(proj, state=state,
                                            now=self.now + 1800)
        self.assertEqual(reqs, {}, "a second attempt inside the hour")
        self.assertTrue(any("1/h" in ln for ln in logs), logs)
        reqs, _l, _s, _t = self._sweep(proj, state=state, now=self.now + 3700)
        self.assertEqual(reqs["m-rate"]["origin"], sm.ORIGIN)

    def test_a_skip_is_journalled_once_not_every_sweep(self):
        proj = self._fixture("m-log", idle_s=120)
        state, lines = {}, []
        for k in range(3):
            _r, logs, state, _t = self._sweep(proj, state=state,
                                              now=self.now + k * 60)
            lines += [ln for ln in logs if "stream-migrate SKIP" in ln]
        self.assertEqual(len(lines), 1, lines)

    # --- adversarial-review findings (part 3) -------------------------------- #
    def test_delivers_even_with_the_staged_goal_sweep_kind_off(self):
        # 🔴: a stream box with no DECLARED window (montalu1-8) resolves the
        # staged, default-OFF `goal-sweep` kind -> every keystroke suppressed.
        # The owner-authorized migration rides the always-on `goal-arm` kind.
        proj = self._fixture("m-kind")
        _r, _l, state, _t = self._sweep(proj)
        home = self._dir()
        env = {k: v for k, v in os.environ.items()
               if k != "AIRULESET_TEST_IGNORE_DISABLE"}
        env["HOME"] = str(home)
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            word, live = self._deliver(proj, "m-kind", state)
        self.assertEqual(word, "sent", Path(self.syncp).read_text())
        self.assertTrue(any("-l" in a for a in live.sent), live.sent)

    def test_never_re_arms_with_a_template_that_is_itself_old(self):
        proj = self._fixture("m-oldtpl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_dark_watch(
            self.now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state={}, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (0, self.now),
            rearm_fn=lambda cwd: (OLD_FORK, "fork-no-merge"),
            requests_path=self.reqp)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})
        self.assertTrue(any("still the old" in ln for ln in logs), logs)

    def test_migrates_when_a_human_turn_followed_the_achievement(self):
        # live david1, 2026-09-23: the old loop ACHIEVED, then a human prompt
        # produced a later plain ✅ turn; the footer is dark (no ◎), so the loop
        # is not running. A rejected 🏁 keeps ◎ lit and never reaches dark-watch.
        proj = self._fixture("m-later")
        tpath = next(proj.rglob("m-later.jsonl"))
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": _iso(700),
                                "message": {"id": "m2", "content":
                                            "ďalšia práca ✅ DONE: x"}}) + "\n")
        os.utime(tpath, (self.now - 1200, self.now - 1200))
        reqs, logs, _s, _t = self._sweep(proj)
        self.assertEqual(reqs["m-later"]["origin"], sm.ORIGIN, logs)
        self.assertFalse(any("newest turn" in ln for ln in logs), logs)

    def test_an_unresolved_template_falls_through_to_the_normal_path(self):
        proj = self._fixture("m-notpl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_dark_watch(
            self.now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state={}, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (4, self.now),
            rearm_fn=lambda cwd: (None, "fork-no-merge"),
            requests_path=self.reqp)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})
        self.assertTrue(any("fulfilled-rearm SKIP:no-template" in ln
                            for ln in logs), logs)

    def test_a_pending_request_blocks_a_new_record(self):
        proj = self._fixture("m-pend")
        goal.record_goal_request("m-pend", CWD, "/goal y", "fork-no-merge",
                                 now=self.now, path=self.reqp,
                                 origin="self-callback")
        reqs, logs, _s, _t = self._sweep(proj)
        self.assertEqual(reqs["m-pend"]["origin"], "self-callback")
        self.assertTrue(any("already pending" in ln for ln in logs), logs)

    def test_decide_on_an_unreadable_transcript_falls_through(self):
        line, handled = sm.decide(
            "s", CWD, None, OLD_FORK, self.now, "loc", False, {},
            lambda cwd: (NEW_FORK, "fork-no-merge"), lambda s: False,
            lambda t, a: None, lambda: None, latest_is_done=True)
        self.assertFalse(handled)
        self.assertIn("transcript unreadable", line)

    def test_dry_run_mutates_no_state(self):
        proj = self._fixture("m-dry")
        state = {}
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP)
        goal.goal_dark_watch(
            self.now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state=state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (0, self.now),
            rearm_fn=lambda cwd: (NEW_FORK, "fork-no-merge"),
            requests_path=self.reqp, dry_run=True)
        self.assertNotIn("goal_stream_migrate", state)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})

    def test_recent_human_defers_the_delivery(self):
        proj = self._fixture("m-human")
        _r, _l, state, _t = self._sweep(proj)
        req = goal.load_goal_requests(self.reqp)["m-human"]
        live = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with unittest.mock.patch.object(
                wd, "_goal_autoarm_recent_human_activity",
                return_value=(True, "presence marker 3s")):
            word = goal.deliver_goal(
                "m-human", CWD, req["text"], req["authority"], run=live,
                projects_dir=proj, now=self.now + 30, origin=req["origin"],
                request_ts=req["ts"], sleep_fn=lambda s: None, state=state)
        self.assertEqual(word, "skip:recent-human")
        self.assertEqual(live.sent, [])

    def test_delivery_journals_the_exemption_and_its_refusal_reason(self):
        proj = self._fixture("m-jr")
        _r, _l, state, _t = self._sweep(proj)
        self._deliver(proj, "m-jr", state)
        self.assertIn("PASS structured-armed", Path(self.syncp).read_text())
        tpath = next(proj.rglob("m-jr.jsonl"))
        os.utime(tpath, (self.now - 60, self.now - 60))
        self._deliver(proj, "m-jr", state, req=self._forced())
        self.assertIn("stream-migrate: transcript not idle",
                      Path(self.syncp).read_text())

    def test_one_typed_attempt_per_request(self):
        # guard 5: a keystroke-bearing skip ends a stream-migrate request (the
        # next attempt is the next hour's decision), never 3-6 retypes.
        goal.record_goal_request("m-one", CWD, NEW_FORK, "fork-no-merge",
                                 now=self.now, path=self.reqp,
                                 origin=sm.ORIGIN)
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP)
        for word in ("skip:verify-failed", "skip:verify-failed-live"):
            goal.record_goal_request("m-one", CWD, NEW_FORK, "fork-no-merge",
                                     now=self.now, path=self.reqp,
                                     origin=sm.ORIGIN)
            with unittest.mock.patch.object(goal, "deliver_goal",
                                            return_value=word):
                logs = goal.goal_sweep(self.now + 30, run=tmux,
                                       projects_dir=self._dir(),
                                       requests_path=self.reqp,
                                       sleep_fn=lambda s: None, state={})
            self.assertEqual(goal.load_goal_requests(self.reqp), {}, word)
            self.assertTrue(any("one typed attempt" in ln for ln in logs), logs)

    def test_the_origin_is_a_silent_watchdog_rearm(self):
        self.assertIn(sm.ORIGIN, goal._GOAL_WATCHDOG_REARM_ORIGINS)
        self.assertNotIn(sm.ORIGIN, goal._GOAL_USER_CALLBACK_ORIGINS)


if __name__ == "__main__":
    unittest.main()
