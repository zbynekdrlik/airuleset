"""#1324 -- the watchdog NEVER arms a `/goal` on its own (owner ROZHODNUTÉ
14.11.2026: "no goal visible" = autopilot off).

Retirement locks (the #707/#1306 checklist): every automatic arm origin
(dark-rearm, auth-rearm, fulfilled-rearm, answer-rearm, stream-migrate) is a
RETIRED origin -- a leftover on disk is purged / dropped, never typed, never
recorded again -- its producers and rate-limit state are gone, `goal_dark_watch`
only JOURNALS a lost goal (once per episode), and the ONE surviving arm is the
owner's own `self-callback` on the always-on `goal-arm` kind.
"""

import importlib.util
import inspect
import json
import os
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import watchdog as wd
from watchdog import goal, goal_arm_failure

from _goal_arm_helpers import (
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

_SIG = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"
_TEMPLATE = "/goal " + _SIG + ": (A) BLOCKED ON MY ANSWER; (B) backlog empty."
_COND = _TEMPLATE[len("/goal "):]
AUTH_CLEAR = ('Goal cleared after an unrecoverable error (authentication '
              'failed): "%s, (A) BLOCKED…". Run /goal again to continue.' % _SIG)
RETIRED = ("dark-rearm", "auth-rearm", "fulfilled-rearm", "answer-rearm",
           "stream-migrate")
CWD = "/home/newlevel/devel/noautoarm"


def _tmp(tc):
    d = TemporaryDirectory()
    tc.addCleanup(d.cleanup)
    return Path(d.name)


class RetiredOrigins(unittest.TestCase):
    def setUp(self):
        self.reqp, _ = _isolate_goal_state(self)

    def test_every_automatic_origin_is_retired(self):
        for origin in RETIRED:
            self.assertIn(origin, goal._GOAL_RETIRED_ORIGINS, origin)
        self.assertEqual(goal._GOAL_USER_CALLBACK_ORIGINS, ("self-callback",))
        self.assertNotIn("self-callback", goal._GOAL_RETIRED_ORIGINS)

    def test_leftover_is_not_live(self):
        for origin in RETIRED:
            self.assertFalse(goal.request_is_live(
                {"origin": origin, "text": _TEMPLATE, "cwd": CWD}), origin)
        self.assertTrue(goal.request_is_live(
            {"origin": "self-callback", "text": _TEMPLATE, "cwd": CWD}))

    def test_record_refuses_a_retired_origin(self):
        for origin in RETIRED:
            ok = goal.record_goal_request("sid-" + origin, CWD, _TEMPLATE,
                                          "full", now=1000, origin=origin)
            self.assertFalse(ok, origin)
        self.assertEqual(goal.load_goal_requests(), {})

    def test_deliver_drops_a_retired_origin_before_any_pane_read(self):
        def run(argv, timeout=None):
            raise AssertionError("pane touched for a retired origin: %r" % argv)
        for origin in RETIRED:
            word = goal.deliver_goal("sid-x", CWD, _TEMPLATE, "full", run=run,
                                     projects_dir=_tmp(self), now=1000,
                                     state={}, request_ts=990, origin=origin)
            self.assertEqual(word, "drop:%s-retired" % origin)
            self.assertIn(word, goal._GOAL_TERMINAL_WORDS)

    def test_sweep_purges_a_leftover_without_delivering(self):
        self.reqp.write_text(json.dumps({"sid-auth": {
            "cwd": CWD, "ts": int(time.time()), "origin": "auth-rearm",
            "authority": "full", "text": _TEMPLATE}}))
        with m.patch.object(goal, "deliver_goal",
                            side_effect=AssertionError("typed a retired arm")):
            logs = goal.goal_sweep(time.time(), run=lambda *a, **k: "",
                                   projects_dir=_tmp(self), state={})
        self.assertEqual(goal.load_goal_requests(), {})
        self.assertIn("DROP (goal-sweep) sid=sid-auth -> auth-rearm-retired", logs)


class MachineryGone(unittest.TestCase):
    def test_producers_and_rate_state_deleted(self):
        for name in ("_auth_rearm_decide", "_dark_record_rearm",
                     "_fulfilled_rearm_decide", "_fulfilled_silent_veto",
                     "_answer_rearm_decide", "_answer_rearm_check_transcript",
                     "_stream_rearm", "_stream_seams", "_recovery_rearm_ok",
                     "_fulfilled_rearm_ok", "_dark_rearm_attempt_ok",
                     "_dark_rearm_backoff", "_record_delivered_attempt",
                     "_GOAL_ATTEMPTS_STATE_KEYS", "_GOAL_WATCHDOG_REARM_ORIGINS",
                     "_GOAL_RECOVERY_ORIGINS", "_default_obligation_fn",
                     "_goal_client_active_skip", "_structured_goal_mark_state",
                     "GOAL_DARK_REARM_STALE_S", "GOAL_DARK_REARM_MAX_PER_DAY",
                     "GOAL_DARK_REARM_BACKOFF_S",
                     "GOAL_DARK_REARM_HARD_CAP_PER_DAY",
                     "GOAL_FULFILLED_REARM_MIN_GAP_S",
                     "GOAL_FULFILLED_REARM_MAX_PER_DAY",
                     "GOAL_AUTH_REARM_MIN_GAP_S", "GOAL_AUTH_REARM_MAX_PER_DAY",
                     "GOAL_ANSWER_REARM_MIN_GAP_S",
                     "GOAL_ANSWER_REARM_MAX_PER_DAY", "_FULFILLED_SILENT"):
            self.assertFalse(hasattr(goal, name), name)
        self.assertNotIn("drop:stale-rearm", goal._GOAL_TERMINAL_WORDS)

    def test_rearm_modules_deleted(self):
        for mod in ("watchdog.stream_migrate", "watchdog.auth_rearm_gate"):
            self.assertIsNone(importlib.util.find_spec(mod), mod)

    def test_signatures_lost_the_rearm_params(self):
        params = inspect.signature(goal.goal_dark_watch).parameters
        self.assertNotIn("obligation_fn", params)
        self.assertNotIn("requests_path", params)
        self.assertNotIn("record_fn",
                         inspect.signature(goal_arm_failure.nudge_off).parameters)


class OwnerArmSurvives(unittest.TestCase):
    def test_self_callback_rides_the_always_on_kind(self):
        self.assertEqual(goal._declared_window_nudge(CWD, "self-callback"),
                         "goal-arm")
        self.assertIn("goal-arm", wd.RECOVERY_NUDGE_KINDS)

    def test_self_callback_records(self):
        _isolate_goal_state(self)
        self.assertTrue(goal.record_goal_request(
            "sid-own", CWD, _TEMPLATE, "full", now=1000, origin="self-callback"))
        self.assertEqual(goal.load_goal_requests()["sid-own"]["origin"],
                         "self-callback")


class DarkWatchJournals(unittest.TestCase):
    """A lost goal is journalled once per episode, never recorded or typed."""

    def setUp(self):
        _isolate_goal_state(self)
        rec = m.patch.object(goal, "record_goal_request",
                             side_effect=AssertionError("dark-watch recorded an arm"))
        rec.start()
        self.addCleanup(rec.stop)
        # a WORKABLE backlog: the strongest pre-#1324 re-arm trigger
        obl = m.patch("statusbar.obligation_count", return_value=(5, 99000))
        obl.start()
        self.addCleanup(obl.stop)

    def _watch(self, proj, now, state, sends):
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_dark_watch(
            now, run=tmux, state=state, projects_dir=proj,
            send_fn=lambda msg, **k: sends.append((msg, k)),
            sleep_fn=lambda s: None, rearm_fn=lambda cwd: (_TEMPLATE, "full"))
        self.assertEqual(tmux.sent, [])
        return logs

    def _auth_fixture(self, sid):
        proj = _tmp(self)
        _write_marker_transcript(proj, CWD, sid)
        _write_goal_marker(proj, CWD, sid, "Goal set: %s" % _COND, ts_epoch=500)
        from datetime import datetime, timezone
        p = proj / wd.encode_project_dir(CWD) / (sid + ".jsonl")
        iso = datetime.fromtimestamp(600, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "system", "subtype": "goal",
                                "timestamp": iso, "content": AUTH_CLEAR}) + "\n")
        return proj

    def test_auth_clear(self):
        sid = "sid-auth-lost"
        proj = self._auth_fixture(sid)
        state, sends, lines = {}, [], []
        for i in range(3):
            lines += self._watch(proj, 100000 + 60 * i, state, sends)
        lost = [ln for ln in lines if "goal-lost:auth-clear" in ln]
        self.assertEqual(len(lost), 1, lines)
        self.assertIn("NOT re-armed", lost[0])
        self.assertEqual(sends, [])

    def test_confirmed_dark_loop(self):
        sid = "sid-dark-lost"
        proj = _tmp(self)
        _write_marker_transcript(proj, CWD, sid)
        _write_goal_marker(proj, CWD, sid, "Goal set: %s" % _COND, ts_epoch=500)
        tp = proj / wd.encode_project_dir(CWD) / (sid + ".jsonl")
        aged = time.time() - 3600
        os.utime(tp, (aged, aged))
        state, sends, lines = {}, [], []
        for i in range(14):           # > 8 clean reads over > 10 min
            lines += self._watch(proj, 100000 + 90 * i, state, sends)
        lost = [ln for ln in lines if "goal-lost:dark" in ln]
        self.assertEqual(len(lost), 1, lines)
        self.assertIn("NOT re-armed", lost[0])
        self.assertEqual(len(sends), 1, "one goal-dark report per episode")
        self.assertTrue(sends[0][1]["dedup_key"].startswith("goal-dark:"))

    def test_a_loop_that_ended_on_the_flag_is_finished_not_lost(self):
        # review 🟡: a 🏁 BACKLOG EMPTY completion leaves the mark "set" and the
        # footer dark; it is journalled once as fulfilled, never reported lost.
        sid = "sid-fulfilled"
        proj = _tmp(self)
        _write_marker_transcript(proj, CWD, sid)
        _write_goal_marker(proj, CWD, sid, "Goal set: %s" % _COND, ts_epoch=500)
        tp = proj / wd.encode_project_dir(CWD) / (sid + ".jsonl")
        with open(tp, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "timestamp":
                                "1970-01-01T00:10:00.000Z", "message": {
                                    "id": "m2", "content":
                                    "🏁 BACKLOG EMPTY: 0 open, main green"}})
                    + "\n")
        aged = time.time() - 3600
        os.utime(tp, (aged, aged))
        state, sends, lines = {}, [], []
        for i in range(14):
            lines += self._watch(proj, 100000 + 90 * i, state, sends)
        self.assertFalse(any("goal-lost" in ln for ln in lines), lines)
        self.assertEqual(sum("goal-ended:fulfilled" in ln for ln in lines), 1, lines)
        self.assertEqual(sends, [])

    def test_auth_latch_survives_a_transient_skip(self):
        # review 🔵: a pane skipped one sweep (copy-mode) keeps its latch, so the
        # episode is never journalled twice.
        sid = "sid-auth-skip"
        proj = self._auth_fixture(sid)
        state, sends, lines = {}, [], []
        lines += self._watch(proj, 100000, state, sends)
        with m.patch.object(wd, "pane_in_mode", return_value=True):
            lines += self._watch(proj, 100060, state, sends)
        lines += self._watch(proj, 100120, state, sends)
        self.assertEqual(sum("goal-lost:auth-clear" in ln for ln in lines), 1, lines)

    def test_retired_stores_dropped_even_with_the_kill_switch(self):
        state = {"goal_auth_rearm_attempts": {"s": [1]}, "auth_rearm_hold": {"s": 1}}
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            goal.goal_dark_watch(1000, run=lambda *a, **k: "", state=state)
        self.assertEqual(state, {})


class OwnerOnlyAllowlist(unittest.TestCase):
    def setUp(self):
        _isolate_goal_state(self)

    def test_an_unknown_named_origin_is_refused_and_dropped(self):
        self.assertFalse(goal.record_goal_request(
            "sid-new", CWD, _TEMPLATE, "full", now=1000, origin="future-rearm"))
        self.assertEqual(goal.load_goal_requests(), {})

        def run(argv, timeout=None):
            raise AssertionError("pane touched for a non-owner origin")
        word = goal.deliver_goal("sid-new", CWD, _TEMPLATE, "full", run=run,
                                 projects_dir=_tmp(self), now=1000, state={},
                                 request_ts=990, origin="future-rearm")
        self.assertEqual(word, "drop:non-owner-origin")
        self.assertIn(word, goal._GOAL_TERMINAL_WORDS)

    def test_only_the_owner_paths_call_the_arm_store_and_delivery(self):
        # structural lock: in non-test code `record_goal_request` is called only
        # by `_goal_sync_attempt`, `deliver_goal` only by `_goal_sync_attempt`
        # and `goal_sweep` (the owner's `goal-arm --self` and its re-evaluation).
        import ast
        import glob
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        allowed = {"record_goal_request": {"_goal_sync_attempt"},
                   "deliver_goal": {"_goal_sync_attempt", "goal_sweep"}}
        seen = {k: set() for k in allowed}
        rels = [os.path.relpath(f, root)
                for f in glob.glob(root + "/**/*.py", recursive=True)]
        paths = [os.path.join(root, r) for r in rels
                 if not r.startswith(("tests/", ".claude/", "scripts/repro_"))]
        for path in paths:
            tree = ast.parse(open(path, encoding="utf-8").read())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call):
                        f = call.func
                        name = getattr(f, "attr", None) or getattr(f, "id", None)
                        if name in allowed:
                            seen[name].add(fn.name)
        for name, callers in seen.items():
            self.assertLessEqual(callers, allowed[name], name)
            self.assertTrue(callers, name)


if __name__ == "__main__":
    unittest.main()
