"""#797 — the hourly U-freshness reconcile rider (`watchdog/u_freshness.py`): a
5th rider on `goal_lane_sweep`'s armed-pane loop that keeps the footer `U` count
TRUTHFUL. Since #795 retired the daily re-ask, the footer `U` is the owner's ONLY
question surface, so a phantom `U` (a needs-answer/decision label / question-map
entry the session forgot to clear after an answer/obsoletion) lies to him.

This rider reads the SAME tickets-status cache the footer renders
(`statusbar.obligation_partition`, ZERO gh calls) and, for an armed pane whose
`user_waiting > 0`, keystrokes ONE compact `stuck-check: U-reconcile` into the
SESSION telling it to re-audit each U member (live owner question → keep;
answered/obsolete → drop the label + clean the question-map entry WITH evidence +
refresh). Hard per-session floor ≥1h (the owner's strop) via the shared
`nudge_gate`. It NEVER pings the owner (BY CONSTRUCTION — the module imports no
notify path); the session fixes the state, the footer becomes truthful.

RED against the pre-implementation tree: `from watchdog import u_freshness`
ImportErrors, and `goal_lane_sweep` rejects the `u_fetch` kwarg. GREEN once the
module + wiring land.
"""

import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: F401
import watchdog as wd
from watchdog import goal
from watchdog import u_freshness as uf
from watchdog import nudge_gate as ng
from watchdog import session_status as ss

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
HOUR = 3600
DAY = 24 * 3600
FRESH_TS = NOW - 60          # a fresh cache ts (within the 15-min window)
CADENCE = HOUR
CACHE_AGE = uf.U_STATUS_CACHE_MAX_AGE_S if hasattr(uf, "U_STATUS_CACHE_MAX_AGE_S") \
    else 15 * 60


# --------------------------------------------------------------------------- #
# 1. Pure decider — the safety-critical verdict (never nudge on uncertainty).
# --------------------------------------------------------------------------- #

class TestUDecision(unittest.TestCase):
    def test_absent_cache_no_ts_refreshes_no_nudge(self):
        action, out, reason = uf._u_decision({}, None, None, NOW, CADENCE,
                                              CACHE_AGE)
        self.assertEqual(action, "refresh")

    def test_stale_ts_refreshes_no_nudge(self):
        stale = NOW - CACHE_AGE - 60
        action, out, reason = uf._u_decision({}, 3, stale, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "refresh")
        self.assertEqual(reason, "stale-cache")

    def test_fresh_cache_but_user_waiting_none_skips(self):
        action, out, reason = uf._u_decision({}, None, FRESH_TS, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "skip")

    def test_user_waiting_zero_clears(self):
        rec = {"first_seen": NOW - DAY, "last_nudge": NOW - HOUR}
        action, out, reason = uf._u_decision(rec, 0, FRESH_TS, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "clear")
        self.assertIsNone(out)

    def test_fresh_u_positive_seeds_grace_no_nudge(self):
        # a freshly-seen U>0 pane sits in grace (first_seen == now) → no nudge
        action, out, reason = uf._u_decision({}, 2, FRESH_TS, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "wait")
        self.assertEqual(out["first_seen"], NOW)

    def test_u_positive_past_cadence_nudges(self):
        rec = {"first_seen": NOW - 2 * HOUR, "last_nudge": None}
        action, out, reason = uf._u_decision(rec, 4, FRESH_TS, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "due")

    def test_u_positive_inside_reping_window_waits(self):
        rec = {"first_seen": NOW - DAY, "last_nudge": NOW - 100}
        action, out, reason = uf._u_decision(rec, 4, FRESH_TS, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "wait")

    def test_last_nudge_preserved_on_nudge_intent(self):
        # a "nudge" verdict is an INTENT; last_nudge advances only on a confirmed
        # send (the caller does it), so the decider preserves it here.
        rec = {"first_seen": NOW - DAY, "last_nudge": NOW - 2 * HOUR}
        action, out, reason = uf._u_decision(rec, 1, FRESH_TS, NOW, CADENCE,
                                             CACHE_AGE)
        self.assertEqual(action, "nudge")
        self.assertEqual(out["last_nudge"], NOW - 2 * HOUR)


# --------------------------------------------------------------------------- #
# 2. Nudge text — compact, prefixed, names the commands + the #795 no-re-ask.
# --------------------------------------------------------------------------- #

class TestNudgeText(unittest.TestCase):
    def test_shape(self):
        t = uf._nudge_text(7)
        self.assertTrue(t.startswith("stuck-check: "))
        self.assertLessEqual(len(t), uf.NUDGE_MAX_CHARS)
        self.assertIn("U=7", t)
        self.assertIn("--waiting", t)
        self.assertIn("tickets-status --refresh", t)
        self.assertIn("#795", t)  # the "otázku NEOPAKUJ" no-re-ask line


class TestNoOwnerPingByConstruction(unittest.TestCase):
    """The #795 invariant: this rider's ONLY output is a keystroke into the
    session — it must never import a notify/Discord send path (no owner ping)."""

    def test_module_source_never_imports_notify(self):
        src = Path(uf.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import notify", src)
        self.assertNotIn("from notify", src)


# --------------------------------------------------------------------------- #
# 3. Orchestrator — I/O around the pure decider.
# --------------------------------------------------------------------------- #

class _OrchBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/ufrepo"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-797-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, urecs, u_fetch, tmux, *, dry_run=False, handled=None,
             state=None, captured=None, refresh_fn=None):
        return uf.goal_u_freshness_recheck(
            NOW, tmux, urecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            dry_run, handled, u_fetch=u_fetch,
            state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, captured=captured,
            refresh_fn=refresh_fn or (lambda cwd: None))


class TestOrchestrator(_OrchBase):
    def test_absent_cache_spawns_refresh_no_nudge(self):
        spawned = []
        urecs = {}
        self._run(urecs, lambda cwd: (None, None), self._tmux(),
                  refresh_fn=lambda cwd: spawned.append(cwd))
        self.assertEqual(spawned, [self.CWD])
        self.assertEqual(self._tmux().typed_texts(), [])

    def test_user_waiting_zero_clears_rec(self):
        urecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": NOW - DAY}}
        logs = self._run(urecs, lambda cwd: (0, FRESH_TS), self._tmux())
        self.assertNotIn(self.sid, urecs)
        self.assertTrue(any("clear" in ln for ln in logs))

    def test_fresh_u_is_seeded_not_nudged(self):
        urecs = {}
        tmux = self._tmux()
        self._run(urecs, lambda cwd: (3, FRESH_TS), tmux)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIn(self.sid, urecs)

    def test_u_past_cadence_is_nudged_and_marks_gate(self):
        urecs = {self.sid: {"first_seen": NOW - 2 * HOUR, "last_nudge": None}}
        tmux = self._tmux()
        state = {}
        self._run(urecs, lambda cwd: (5, FRESH_TS), tmux, handled=set(),
                  state=state)
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("U=5", typed)
        self.assertEqual(urecs[self.sid]["last_nudge"], NOW)
        # the shared gate clock was stamped so a sibling rider defers this sweep-run
        self.assertEqual(state["nudge_cadence"][self.sid]["u-freshness"], NOW)

    def test_cadence_gate_blocks_a_second_nudge_within_the_hour(self):
        # the owner's hard strop: even with the rider's own last_nudge cleared, a
        # gate ts < 1h ago DEFERS (no keystroke).
        urecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}}
        state = {}
        ng.mark_sent(state, self.sid, "u-freshness", NOW - 1800)  # 30 min ago
        tmux = self._tmux()
        logs = self._run(urecs, lambda cwd: (5, FRESH_TS), tmux, handled=set(),
                         state=state)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("cadence-gate" in ln for ln in logs))

    def test_busy_pane_defers_no_keystroke(self):
        urecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}}
        tmux = self._tmux()
        logs = self._run(urecs, lambda cwd: (5, FRESH_TS), tmux, handled=set(),
                         captured="Waiting for 2 background agents to finish…")
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("busy-bg-agent" in ln for ln in logs))

    def test_already_handled_defers(self):
        urecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}}
        tmux = self._tmux()
        logs = self._run(urecs, lambda cwd: (5, FRESH_TS), tmux,
                         handled={self.sid})
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("already-handled" in ln for ln in logs))

    def test_dry_run_would_nudge_no_mutation_no_send(self):
        urecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}}
        tmux = self._tmux()
        state = {}
        logs = self._run(urecs, lambda cwd: (5, FRESH_TS), tmux, dry_run=True,
                         state=state)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("WOULD-NUDGE" in ln for ln in logs))
        self.assertNotIn("nudge_cadence", state)

    def test_fetch_error_skips(self):
        def boom(cwd):
            raise RuntimeError("cache blew up")
        urecs = {}
        logs = self._run(urecs, boom, self._tmux())
        self.assertTrue(any("fetch-error" in ln for ln in logs))

    def test_swallowed_send_bounded_retry(self):
        urecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}}
        tmux = self._tmux(enters_swallowed=5)
        logs = self._run(urecs, lambda cwd: (5, FRESH_TS), tmux, handled=set(),
                         state={})
        # a genuine swallow does NOT advance last_nudge (retries next sweep) and
        # books a failure toward the bounded retry cap.
        self.assertIsNone(urecs[self.sid]["last_nudge"])
        self.assertTrue(any("submit-unverified" in ln for ln in logs))


# --------------------------------------------------------------------------- #
# 4. Integration — wiring into goal_lane_sweep.
# --------------------------------------------------------------------------- #

class TestLaneSweepWiring(unittest.TestCase):
    """RED against the pre-wiring tree: `goal_lane_sweep` produces NO
    u-freshness nudge for an armed pane whose footer cache shows U>0. GREEN once
    the sweep calls `goal_u_freshness_recheck`."""

    CWD = "/home/newlevel/devel/uflane"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)

    def _heartbeat(self, sid):
        pth = ss.status_path(sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text('{"schema": 1, "sid": "%s", "kind": "main", '
                       '"last_turn": "stop", "ts": %d, "cwd": "%s", '
                       '"marker": "working", "goal_armed": true}'
                       % (sid, NOW, self.CWD), encoding="utf-8")

    def _armed_sweep(self, state, *, u_fetch, dry_run=False, handled=None):
        proj = Path(self._proj.name)
        tpath = _write_marker_transcript(proj, self.CWD, "sess-797-lane")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat(sid)
        gmarks = state.setdefault("goal_mark", {})
        gmarks[sid] = {"off": 0, "mark": {"state": "set", "ts": NOW}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=proj, state=state, dry_run=dry_run,
                handled=handled, backlog_fetch=lambda cwd: 0,
                u_fetch=u_fetch,
                sleep_fn=lambda *a, **k: None)
        return sid, tmux

    def test_u_positive_pane_is_nudged(self):
        state = {"u_freshness": {
            "sess-797-lane": {"first_seen": NOW - DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, u_fetch=lambda cwd: (3, FRESH_TS))
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed,
                      "an armed pane whose footer cache shows U>0 must be nudged "
                      "(RED before goal_lane_sweep wires goal_u_freshness_recheck)")
        self.assertIn("U-reconcile", typed)

    def test_no_u_fetch_is_a_noop(self):
        state = {}
        sid, tmux = self._armed_sweep(state, u_fetch=None)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertNotIn("u_freshness", state)

    def test_u_zero_is_not_nudged(self):
        state = {}
        sid, tmux = self._armed_sweep(state, u_fetch=lambda cwd: (0, FRESH_TS))
        self.assertEqual(tmux.typed_texts(), [])


if __name__ == "__main__":
    unittest.main()
