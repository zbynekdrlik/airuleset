"""#1128 — the watchdog re-arms an ACHIEVED reduced-authority (stream) loop.

Before #1128, `_fulfilled_rearm_decide` (#764/#766) classified a 🏁-proven
achieved loop with a FRESH open==0 obligation cache as FULFILLED-SILENT — the
correct final state — and never re-armed it, on ANY authority. For a sub-dev
stream that is exactly the regression: its loop ended at I=0 while gk/U/W work
was still coming (david1-4 idle overnight), and a later gk bounce / client reply
/ new stream ticket waited for the owner.

Owner ruling: a stream loop has no backlog-empty end. So for a REDUCED
authority the achieved loop is re-armed through the SAME verified path — a
`fulfilled-rearm` request (record-only; goal_sweep/deliver_goal type it under
the unchanged #1113/#1104 guards) — whatever the obligation cache says. The
positive "achieved" record is the `🏁 BACKLOG EMPTY:` line written AFTER the arm
(CC persists no achieved marker); an owner `/goal clear` writes a `cleared`
marker and is never re-armed (#170); a ❓-blocked end prints no 🏁. A FULL-
authority achieved loop stays FULFILLED-SILENT, byte-for-byte as before.
"""

import json
import sys
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

_STREAM_DONE = ("Hotovo.\n"
                "🏁 BACKLOG EMPTY: 0 open, released\n"
                "✅ DONE: slice prázdny")
_Q_BLOCKED = "Potrebujem rozhodnutie.\n❓ NEEDS YOU: ktorá možnosť A/B?"
STREAM_GOAL = "/goal STOP CONDITIONS — stream template"
ORIGIN = "fulfilled-rearm"


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _append_assistant(path, text, ts):
    entry = {"type": "assistant", "timestamp": _iso(ts),
             "message": {"id": "msg_done", "content": text}}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class TestStreamAchievedLoopIsReArmed(unittest.TestCase):
    CWD = "/home/david1/devel/odoo-erp"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, last_text=_STREAM_DONE, mark_text="Goal set: /goal x",
                 cap=GOAL_IDLE_CAP):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid, "warmup")
        _write_goal_marker(proj, self.CWD, sid, mark_text, ts_epoch=500)
        tpath = next(proj.rglob(sid + ".jsonl"))
        _append_assistant(tpath, last_text, 600)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        return proj, tmux

    def _sweep(self, proj, tmux, obl, authority="fork-no-merge", now=100000,
               state=None, pings=None):
        reqs = self._dir() / "goal-requests.json"
        logs = goal.goal_dark_watch(
            now, run=tmux,
            send_fn=lambda mm, **k: (pings if pings is not None else []).append(mm),
            projects_dir=proj, state={} if state is None else state,
            sleep_fn=lambda s: None, obligation_fn=lambda cwd: obl,
            rearm_fn=lambda cwd: (STREAM_GOAL, authority),
            requests_path=reqs)
        return goal.load_goal_requests(reqs), logs

    # --- the fix: an achieved stream loop is re-armed ------------------------ #
    def test_fork_no_merge_achieved_with_fresh_open_zero_is_rearmed(self):
        proj, tmux = self._fixture("s-fork")
        reqs, logs = self._sweep(proj, tmux, (0, 100000))
        req = reqs.get("s-fork")
        self.assertIsInstance(req, dict, logs)
        self.assertEqual(req.get("origin"), ORIGIN)
        self.assertEqual(req.get("text"), STREAM_GOAL)
        self.assertEqual(req.get("authority"), "fork-no-merge")
        self.assertFalse(any("FULFILLED-SILENT" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], "dark-watch only RECORDS, never types")

    def test_branch_merge_achieved_is_rearmed_too(self):
        proj, tmux = self._fixture("s-bm")
        reqs, _logs = self._sweep(proj, tmux, (0, 100000),
                                  authority="branch-merge")
        self.assertEqual((reqs.get("s-bm") or {}).get("origin"), ORIGIN)

    def test_stale_obligation_cache_still_rearms_a_stream(self):
        # the count is irrelevant for a stream: it has no done-state at all.
        proj, tmux = self._fixture("s-stale")
        old = 100000 - (goal.GOAL_DARK_CACHE_MAX_AGE_S + 10)
        reqs, _logs = self._sweep(proj, tmux, (0, old))
        self.assertEqual((reqs.get("s-stale") or {}).get("origin"), ORIGIN)

    def test_unreadable_obligation_cache_still_rearms_a_stream(self):
        proj, tmux = self._fixture("s-none")
        reqs, _logs = self._sweep(proj, tmux, (None, None))
        self.assertEqual((reqs.get("s-none") or {}).get("origin"), ORIGIN)

    def test_stream_rearm_logs_its_reason(self):
        proj, tmux = self._fixture("s-log")
        _reqs, logs = self._sweep(proj, tmux, (0, 100000))
        self.assertTrue(any("FULFILLED-REARM" in ln and "stream" in ln
                            for ln in logs), logs)

    def test_stream_achieved_loop_is_never_pinged_as_dead(self):
        proj, tmux = self._fixture("s-ping")
        pings, state = [], {}
        self._sweep(proj, tmux, (0, 100000), now=100000, state=state,
                    pings=pings)
        self._sweep(proj, tmux, (0, 100100), now=100100, state=state,
                    pings=pings)
        self.assertEqual(pings, [], "an achieved stream loop is re-armed, "
                         "never the #459 dead-loop ping")

    # --- never re-armed ----------------------------------------------------- #
    def test_owner_cleared_stream_goal_is_never_rearmed(self):
        proj, tmux = self._fixture("s-cleared",
                                   mark_text="Goal cleared: /goal x")
        reqs, _logs = self._sweep(proj, tmux, (0, 100000))
        self.assertEqual(reqs, {}, "an owner /goal clear is NEVER re-armed")

    def test_question_blocked_stream_loop_is_never_rearmed_here(self):
        # stop (A) prints no 🏁 -> not an achieved record (the #890 answer-rearm
        # lane owns it once the owner answers).
        proj, tmux = self._fixture("s-q", last_text=_Q_BLOCKED)
        reqs, _logs = self._sweep(proj, tmux, (0, 100000))
        self.assertNotEqual((reqs.get("s-q") or {}).get("origin"), ORIGIN)

    def test_armed_stream_pane_is_never_rearmed(self):
        proj, tmux = self._fixture("s-armed", cap=GOAL_ARMED_CAP)
        reqs, _logs = self._sweep(proj, tmux, (0, 100000))
        self.assertEqual(reqs, {})

    def test_the_rate_limit_still_binds_a_stream(self):
        proj, tmux = self._fixture("s-gap")
        state = {"goal_fulfilled_rearm": {"s-gap": [100000 - 10]}}
        reqs, logs = self._sweep(proj, tmux, (0, 100000), state=state)
        self.assertEqual(reqs, {})
        self.assertTrue(any("SKIP:gap" in ln for ln in logs), logs)

    # --- full authority is unchanged ---------------------------------------- #
    def test_full_authority_achieved_loop_stays_fulfilled_silent(self):
        proj, tmux = self._fixture("s-full")
        reqs, logs = self._sweep(proj, tmux, (0, 100000), authority="full")
        self.assertEqual(reqs, {})
        self.assertTrue(any("FULFILLED-SILENT" in ln for ln in logs), logs)

    def test_full_authority_stale_cache_still_falls_through(self):
        proj, tmux = self._fixture("s-full-stale")
        old = 100000 - (goal.GOAL_DARK_CACHE_MAX_AGE_S + 10)
        reqs, logs = self._sweep(proj, tmux, (0, old), authority="full")
        self.assertEqual(reqs, {})
        self.assertFalse(any("FULFILLED" in ln for ln in logs), logs)

    # --- delivery: the unchanged #1113/#1104 guards -------------------------- #
    def _record_then_deliver(self, sid, cap):
        proj, tmux = self._fixture(sid)
        reqs_path = self._dir() / "goal-requests.json"
        goal.goal_dark_watch(
            100000, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state={}, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (0, 100000),
            rearm_fn=lambda cwd: (STREAM_GOAL, "fork-no-merge"),
            requests_path=reqs_path)
        req = goal.load_goal_requests(reqs_path)[sid]
        live = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        with unittest.mock.patch.object(
                wd, "_goal_autoarm_recent_human_activity",
                return_value=(False, "")):
            verdict = goal.deliver_goal(
                sid, self.CWD, req["text"], req["authority"], run=live,
                projects_dir=proj, now=100050, origin=req["origin"],
                request_ts=req["ts"], sleep_fn=lambda s: None)
        return verdict, live

    def test_delivery_never_types_into_an_armed_pane(self):
        verdict, live = self._record_then_deliver("s-d-armed", GOAL_ARMED_CAP)
        self.assertEqual(verdict, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_delivery_never_types_into_a_busy_pane(self):
        verdict, live = self._record_then_deliver("s-d-busy", GOAL_BUSY_CAP)
        self.assertTrue(verdict.startswith("skip:"), verdict)
        self.assertEqual(live.sent, [])


if __name__ == "__main__":
    unittest.main()
