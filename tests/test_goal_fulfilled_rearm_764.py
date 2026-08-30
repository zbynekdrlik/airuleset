"""#764 — a FULFILLED `/goal` loop (stop-(B) completion: footer dark,
`mark=="set"`, a `🏁 BACKLOG EMPTY:` proof in its last turn) whose obligation
cache has REFILLED (open>0, fresh) must be RE-ARMED FAST via the structured
`record_goal_request`/`deliver_goal` channel — the cross-stream ping-pong
re-entry the slow dead-loop confirmation (2/day, 8 clean-dark reads) was never
built for.

Root cause (traced in `watchdog/goal.py`): CC never persists a "fulfilled"
marker, so a completed loop is transcript-identical to a silently-dead one
(`mark=="set"`, footer dark) EXCEPT for the `🏁 BACKLOG EMPTY:` completion line.
The only auto re-arm today is `goal_dark_watch`'s dead-loop self-heal (8 clean
reads over >=600 s, cap 2/day) — built for "confirmed dead", far too slow/capped
for a backlog that REFILLED after a genuine completion (gk↔subdev hand-off,
`prio:bounce` return). This suite locks the new `fulfilled-rearm` lane: the
`🏁` proof (`transcript_last_backlog_empty_ts`) replaces the dark-duration
confirmation, rate-limited (min gap + daily cap), the recent-human/pane-safety
gates preserved (new origin joins `_GOAL_WATCHDOG_REARM_ORIGINS`), and a
stop-(A) ❓-blocked completion — which prints NO `🏁` line — structurally never
fires it.
"""

import json
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

_BACKLOG_DONE = ("Všetko hotové.\n"
                 "🏁 BACKLOG EMPTY: 0 open, main green\n"
                 "✅ DONE: backlog prázdny")
_Q_BLOCKED = ("Potrebujem rozhodnutie.\n"
              "❓ NEEDS YOU: ktorá možnosť A/B?")


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _append_assistant(path, text, ts):
    """Append a real assistant turn WITH a timestamp — the completion turn a
    fulfilled loop writes (its last real assistant message carries the `🏁`)."""
    entry = {"type": "assistant", "timestamp": _iso(ts),
             "message": {"id": "msg_done", "content": text}}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- #
# 1. The pure `🏁 BACKLOG EMPTY:` proof reader.
# --------------------------------------------------------------------------- #
class TestBacklogEmptyProofReader(unittest.TestCase):
    def _tmp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_returns_ts_when_last_turn_carries_the_marker(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, "warmup", 100)
        _append_assistant(p, _BACKLOG_DONE, 600)
        self.assertEqual(wd.transcript_last_backlog_empty_ts(p), 600.0)

    def test_none_when_last_turn_is_a_question_block(self):
        # stop-(A) ❓-blocked completion prints NO 🏁 -> None (never re-armed).
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, _BACKLOG_DONE, 600)     # an OLD completion...
        _append_assistant(p, _Q_BLOCKED, 700)        # ...then a later ❓ block
        self.assertIsNone(wd.transcript_last_backlog_empty_ts(p))

    def test_none_when_no_marker_anywhere(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, "ordinary ✅ DONE turn", 600)
        self.assertIsNone(wd.transcript_last_backlog_empty_ts(p))

    def test_none_on_missing_file(self):
        self.assertIsNone(
            wd.transcript_last_backlog_empty_ts(self._tmp() / "nope.jsonl"))


# --------------------------------------------------------------------------- #
# 2. The fulfilled-rearm lane inside goal_dark_watch.
# --------------------------------------------------------------------------- #
class TestFulfilledRearmLane(unittest.TestCase):
    CWD = "/home/newlevel/devel/fulfilledrearm"
    ORIGIN = "fulfilled-rearm"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, last_text=_BACKLOG_DONE, mark_ts=500, done_ts=600,
                 cap=GOAL_IDLE_CAP):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid, "warmup")
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x",
                           ts_epoch=mark_ts)
        tpath = next(proj.rglob(sid + ".jsonl"))
        _append_assistant(tpath, last_text, done_ts)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        return proj, tmux

    def _sweep(self, proj, tmux, now, obl, state=None, reqs=None, dry_run=False,
               rearm=None):
        reqs = reqs if reqs is not None else self._dir() / "goal-requests.json"
        rearm = rearm or (lambda cwd: ("/goal DONE or stop after 50", "full"))
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state={} if state is None else state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: obl, rearm_fn=rearm,
            requests_path=reqs, dry_run=dry_run)
        return goal.load_goal_requests(reqs), logs, reqs

    # --- the core: fulfilled + refilled backlog -> a re-arm request --------- #
    def test_fulfilled_and_workable_records_a_rearm(self):
        proj, tmux = self._fixture("sess-f-1")
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        req = reqs.get("sess-f-1")
        self.assertIsInstance(req, dict,
                              "a fulfilled+workable loop must record a re-arm")
        self.assertEqual(req.get("origin"), self.ORIGIN)
        self.assertTrue(any("FULFILLED-REARM" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], "the lane NEVER types (record-only)")

    def test_new_origin_is_a_watchdog_rearm_origin(self):
        # so deliver_goal applies the recent-human + pane-safety gates to it.
        self.assertIn(self.ORIGIN, goal._GOAL_WATCHDOG_REARM_ORIGINS)

    # --- the ❓-blocked completion structurally never fires ----------------- #
    def test_question_blocked_completion_never_rearms(self):
        proj, tmux = self._fixture("sess-q", last_text=_Q_BLOCKED)
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "a ❓-blocked completion prints no 🏁 -> never re-armed")

    # --- user-cleared mark never fires (permanent #170 guard) --------------- #
    def test_user_cleared_mark_never_rearms(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-cl", "warmup")
        _write_goal_marker(proj, self.CWD, "sess-cl", "Goal cleared: /goal x",
                           ts_epoch=500)
        tpath = next(proj.rglob("sess-cl.jsonl"))
        _append_assistant(tpath, _BACKLOG_DONE, 600)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "a cleared marker is NEVER re-armed (#170)")

    # --- stale obligation cache never fires --------------------------------- #
    def test_stale_obligation_cache_never_rearms(self):
        proj, tmux = self._fixture("sess-stale")
        old = 100000 - (goal.GOAL_DARK_CACHE_MAX_AGE_S + 10)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, old))
        self.assertEqual(reqs, {},
                         "a STALE obligation cache can never prove work remains")

    # --- empty backlog never fires (achieved final state) ------------------- #
    def test_empty_backlog_never_rearms(self):
        proj, tmux = self._fixture("sess-empty")
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (0, 100000))
        self.assertNotIn("sess-empty", reqs,
                         "open==0 is the correct achieved final state")

    # --- 🏁 that PREDATES the current arm never fires ----------------------- #
    def test_backlog_proof_before_the_mark_never_rearms(self):
        # a re-armed-then-died loop whose LAST turn is a PRE-rearm 🏁: the mark
        # is newer than the 🏁, so this is NOT the current episode's completion.
        proj, tmux = self._fixture("sess-oldflag", mark_ts=900, done_ts=600)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "🏁 older than the current arm is not a fresh completion")

    # --- armed footer never fires (already re-armed) ------------------------ #
    def test_armed_footer_never_rearms(self):
        proj, tmux = self._fixture("sess-armed", cap=GOAL_ARMED_CAP)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "an ALREADY-armed loop is never fulfilled-re-armed")

    # --- rate-limit: min gap between fulfilled-rearms per sid --------------- #
    def test_min_gap_blocks_a_second_rearm_within_the_window(self):
        proj, tmux = self._fixture("sess-gap")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        r1, _l1, _ = self._sweep(proj, tmux, 100000, (7, 100000), state, reqs)
        self.assertEqual(r1.get("sess-gap", {}).get("origin"), self.ORIGIN)
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-gap")
        self.assertEqual(len(recs or []), 1, "exactly ONE record in the window")
        # a 2nd sweep within the min-gap must NOT record a fresh slot.
        _r2, l2, _ = self._sweep(
            proj, tmux, 100000 + goal.GOAL_FULFILLED_REARM_MIN_GAP_S - 5,
            (7, 100000), state, reqs)
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-gap")
        self.assertEqual(len(recs or []), 1,
                         "the min-gap holds a second re-arm inside the window")
        self.assertTrue(any("SKIP:gap" in ln for ln in l2), l2)

    def test_gap_frees_after_the_window(self):
        proj, tmux = self._fixture("sess-gap2")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        self._sweep(proj, tmux, 100000, (7, 100000), state, reqs)
        self._sweep(proj, tmux, 100000 + goal.GOAL_FULFILLED_REARM_MIN_GAP_S + 5,
                    (7, 100000), state, reqs)
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-gap2")
        self.assertEqual(len(recs or []), 2,
                         "once the min-gap passes a fresh re-arm records again")

    # --- rate-limit: daily cap --------------------------------------------- #
    def test_daily_cap_honored(self):
        proj, tmux = self._fixture("sess-cap")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        now = 100000
        # space each sweep past the min gap so only the DAILY cap can bind.
        step = goal.GOAL_FULFILLED_REARM_MIN_GAP_S + 1
        for _i in range(goal.GOAL_FULFILLED_REARM_MAX_PER_DAY + 3):
            self._sweep(proj, tmux, now, (7, now), state, reqs)
            now += step
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-cap") or []
        self.assertEqual(len(recs), goal.GOAL_FULFILLED_REARM_MAX_PER_DAY,
                         "no more than the daily cap of fulfilled-rearms per sid")

    # --- recent-human veto applies to the new origin (delivered path) ------- #
    def test_recent_human_veto_applies_to_fulfilled_rearm(self):
        # the request is RECORDED by dark-watch, but deliver_goal must SKIP it
        # when a human is present -> the new origin honours the recent-human gate
        # exactly like every other watchdog re-arm.
        proj, tmux = self._fixture("sess-human")
        reqs = self._dir() / "goal-requests.json"
        self._sweep(proj, tmux, 100000, (7, 100000), reqs=reqs)
        req = goal.load_goal_requests(reqs).get("sess-human")
        self.assertEqual(req.get("origin"), self.ORIGIN)
        # now deliver with a human just-present -> skip:recent-human, no type.
        with unittest.mock.patch.object(
                wd, "_goal_autoarm_recent_human_activity",
                return_value=(True, "presence marker 3s")):
            verdict = goal.deliver_goal(
                "sess-human", self.CWD, req["text"], req["authority"],
                run=tmux, projects_dir=proj, now=100050,
                origin=self.ORIGIN, request_ts=req["ts"], sleep_fn=lambda s: None)
        self.assertEqual(verdict, "skip:recent-human")
        self.assertEqual(tmux.sent, [], "no keystroke while a human is present")

    # --- dry-run records nothing ------------------------------------------- #
    def test_dry_run_never_records(self):
        proj, tmux = self._fixture("sess-dry")
        state = {}
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000), state,
                                    dry_run=True)
        self.assertEqual(reqs, {}, "dry-run records no request")
        self.assertNotIn("sess-dry", state.get("goal_fulfilled_rearm", {}),
                         "dry-run consumes no rate-limit slot")
        self.assertTrue(any("would record" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
