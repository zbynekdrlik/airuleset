"""#733 — gk queue-arrival watcher: wake an armed `/goal` supervisor session
that is PARKED ON A LONG BACKGROUND WAITER (a `run_in_background` shadow-CI /
write-lock task) the moment a NEW hand-off lands in the gk queue.

INCIDENT (odoo-erp gk box, 2026-08-26 evening): the gk autopilot session waited
on a release tail while THREE new items arrived in the gk queue
(READY-FOR-REVIEW #5177 20:51, GATEKEEPER-ACTION #5310 21:05, READY-FOR-REVIEW
#3073 22:00) — the session was blind to all three until the owner asked by hand
TWICE. Jobs 8/11 are IDLE-pane / PRESENCE-based / ~30-min cadence; job 11's
stale-handoff alarm is 6h+ and Discord-only; job 20's three riders (lane /
ops-wait / release-gap) never read the gk queue union. So no mechanism gave a
FAST, arrival-triggered, in-session wake.

This 4th rider on `goal_lane_sweep`'s armed-pane loop (the faithful sibling of
#547/#578/#616) snapshots the union `ready-for-review ∪ needs-gatekeeper ∪
prio:bounce` per repo and, on a SET DELTA (a new member appears vs the recorded
baseline), keystrokes ONE verified nudge into the armed FULL-authority session.

RED against the pre-implementation tree: `from watchdog import
queue_arrival_recheck` ImportErrors, and `goal_lane_sweep` rejects the
`queue_fetch` kwarg. GREEN once the module + wiring land.
"""

import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import goal
from watchdog import queue_arrival_recheck as qa
from watchdog import session_status as ss

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600


# --------------------------------------------------------------------------- #
# 1. Pure decider — the safety-critical set-delta verdict.
# --------------------------------------------------------------------------- #

class TestQueueDecision(unittest.TestCase):
    def test_none_cur_skip_and_never_mutates(self):
        rec = {"base": [1, 2], "first_seen": NOW - DAY}
        action, out, reason, arr = qa._queue_decision(rec, None, NOW)
        self.assertEqual(action, "skip")
        self.assertEqual(reason, "undetermined")
        self.assertIs(out, rec)
        self.assertEqual(arr, [])

    def test_non_list_cur_skip(self):
        action, out, reason, arr = qa._queue_decision({}, "boom", NOW)
        self.assertEqual(action, "skip")

    def test_first_observation_seeds_no_nudge(self):
        action, out, reason, arr = qa._queue_decision({}, [5, 3, 9], NOW)
        self.assertEqual(action, "seed")
        self.assertEqual(reason, "first-seen")
        self.assertEqual(out["base"], [3, 5, 9])
        self.assertEqual(out["first_seen"], NOW)
        self.assertEqual(arr, [])

    def test_malformed_rec_is_seeded_fresh(self):
        action, out, reason, arr = qa._queue_decision("garbage", [1], NOW)
        self.assertEqual(action, "seed")
        self.assertEqual(out["base"], [1])

    def test_unchanged_tracks_no_nudge(self):
        rec = {"base": [1, 2, 3], "first_seen": NOW - DAY}
        action, out, reason, arr = qa._queue_decision(rec, [3, 2, 1], NOW)
        self.assertEqual(action, "track")
        self.assertEqual(reason, "no-arrival")
        self.assertEqual(out["base"], [1, 2, 3])
        self.assertEqual(arr, [])

    def test_member_removed_tracks_advances_base_no_nudge(self):
        rec = {"base": [1, 2, 3], "first_seen": NOW - DAY}
        action, out, reason, arr = qa._queue_decision(rec, [1, 3], NOW)
        self.assertEqual(action, "track")
        self.assertEqual(reason, "resolved")
        self.assertEqual(out["base"], [1, 3])   # baseline dropped the resolved one
        self.assertEqual(arr, [])

    def test_new_member_arrival_nudges_keeps_old_base(self):
        rec = {"base": [1, 2], "first_seen": NOW - DAY}
        action, out, reason, arr = qa._queue_decision(rec, [1, 2, 7], NOW)
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "arrival")
        self.assertEqual(arr, [7])
        # base is NOT advanced by the decider — the orchestrator promotes it only
        # on a CONFIRMED delivery, so a swallowed nudge re-detects the arrival.
        self.assertEqual(out["base"], [1, 2])
        self.assertEqual(out["first_seen"], NOW - DAY)   # preserved

    def test_arrival_and_resolution_together_still_nudges(self):
        # #5 leaves, #7 arrives in the same window -> the ARRIVAL wins (nudge),
        # base stays OLD so the delivery-time promotion captures both changes.
        rec = {"base": [1, 5], "first_seen": NOW - DAY}
        action, out, reason, arr = qa._queue_decision(rec, [1, 7], NOW)
        self.assertEqual(action, "nudge")
        self.assertEqual(arr, [7])


# --------------------------------------------------------------------------- #
# 2. Per-repo TTL cache over the fetch.
# --------------------------------------------------------------------------- #

class TestCachedQueue(unittest.TestCase):
    def test_second_read_within_ttl_hits_cache(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return [1, 2]
        st = {}
        qa._cached_queue("/r", f, st, NOW, ttl=100)
        qa._cached_queue("/r", f, st, NOW + 50, ttl=100)
        self.assertEqual(calls, ["/r"])

    def test_read_past_ttl_refetches(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return [1]
        st = {}
        qa._cached_queue("/r", f, st, NOW, ttl=100)
        qa._cached_queue("/r", f, st, NOW + 200, ttl=100)
        self.assertEqual(len(calls), 2)

    def test_none_failure_cached_only_for_fail_ttl(self):
        calls = []

        def f(cwd):
            calls.append(1)
            return None
        st = {}
        qa._cached_queue("/r", f, st, NOW, ttl=10000, fail_ttl=30)
        qa._cached_queue("/r", f, st, NOW + 10, ttl=10000, fail_ttl=30)
        self.assertEqual(len(calls), 1)   # still inside fail_ttl
        qa._cached_queue("/r", f, st, NOW + 40, ttl=10000, fail_ttl=30)
        self.assertEqual(len(calls), 2)   # fail_ttl expired -> refetch

    def test_wired_none_returns_none_no_cache_write(self):
        st = {}
        self.assertIsNone(qa._cached_queue("/r", None, st, NOW))
        self.assertNotIn("/r", st.get("queue_arrival_cache", {}))

    def test_fetch_exception_is_none(self):
        def boom(cwd):
            raise RuntimeError("x")
        self.assertIsNone(qa._cached_queue("/r", boom, {}, NOW))

    def test_per_cwd_keyed(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return [1]
        st = {}
        qa._cached_queue("/a", f, st, NOW)
        qa._cached_queue("/b", f, st, NOW)
        self.assertEqual(sorted(calls), ["/a", "/b"])

    def test_non_list_return_is_none(self):
        self.assertIsNone(qa._cached_queue("/r", lambda c: "boom", {}, NOW))


# --------------------------------------------------------------------------- #
# 3. Helpers — cadence/ttl floor, nudge text.
# --------------------------------------------------------------------------- #

class TestHelpers(unittest.TestCase):
    def test_fetch_ttl_floored(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_QUEUE_ARRIVAL_FETCH_TTL_S": "1"}):
            self.assertEqual(qa._fetch_ttl(), qa.QUEUE_ARRIVAL_FETCH_TTL_MIN_S)

    def test_fetch_ttl_env_override(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_QUEUE_ARRIVAL_FETCH_TTL_S": "900"}):
            self.assertEqual(qa._fetch_ttl(), 900)

    def test_nudge_text_names_arrivals_and_prefix(self):
        txt = qa._nudge_text([5177, 5310, 3073], 8)
        self.assertIn("stuck-check:", txt)
        self.assertIn("#5177", txt)
        self.assertIn("#5310", txt)
        self.assertIn("#3073", txt)
        self.assertLessEqual(len(txt), qa.NUDGE_MAX_CHARS)

    def test_nudge_text_capped_and_truncates_arrival_list(self):
        many = list(range(1, 60))
        txt = qa._nudge_text(many, 60)
        self.assertLessEqual(len(txt), qa.NUDGE_MAX_CHARS)
        # only the first MAX_NAMED_ARRIVALS are named, the rest summarized
        self.assertIn("#1", txt)
        self.assertIn("ďalš", txt)   # "+K ďalších"


# --------------------------------------------------------------------------- #
# 4. Orphan reaper.
# --------------------------------------------------------------------------- #

class TestPruneOrphans(unittest.TestCase):
    def test_aged_not_visited_reaped(self):
        qrecs = {"gone": {"base": [1], "lts": NOW - 3 * DAY}}
        qa._prune_queue_arrival_orphans(qrecs, set(), NOW)
        self.assertNotIn("gone", qrecs)

    def test_visited_never_reaped_even_stale(self):
        qrecs = {"live": {"base": [1], "lts": NOW - 9 * DAY}}
        qa._prune_queue_arrival_orphans(qrecs, {"live"}, NOW)
        self.assertIn("live", qrecs)

    def test_recent_not_visited_kept(self):
        qrecs = {"fresh": {"base": [1], "lts": NOW - 60}}
        qa._prune_queue_arrival_orphans(qrecs, set(), NOW)
        self.assertIn("fresh", qrecs)

    def test_future_lts_kept(self):
        qrecs = {"skew": {"base": [1], "lts": NOW + 10_000}}
        qa._prune_queue_arrival_orphans(qrecs, set(), NOW)
        self.assertIn("skew", qrecs)

    def test_non_dict_never_raises(self):
        qa._prune_queue_arrival_orphans("nope", set(), NOW)   # no raise


# --------------------------------------------------------------------------- #
# 5. Orchestrator — authority / fetch / decide / deliver.
# --------------------------------------------------------------------------- #

class _OrchBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/qarepo"

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
                                              "sess-733-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, qrecs, fetch, tmux, *, dry_run=False, handled=None,
             state=None, authority="full", captured=None):
        with m.patch("airuleset.resolve_authority", return_value=authority):
            return qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                dry_run, handled, queue_fetch=fetch,
                state=state if state is not None else {},
                sleep_fn=lambda *a, **k: None, captured=captured)


class TestOrchestrator(_OrchBase):
    def test_reduced_authority_skips_without_fetch(self):
        called = []
        qrecs = {}
        logs = self._run(qrecs, lambda cwd: called.append(cwd),
                         self._tmux(), authority="fork-no-merge")
        self.assertTrue(any("skip:not-full-authority" in ln for ln in logs))
        self.assertEqual(called, [])
        self.assertEqual(qrecs, {})

    def test_authority_unresolved_skips(self):
        qrecs = {}
        with m.patch("airuleset.resolve_authority",
                     side_effect=RuntimeError("boom")):
            logs = qa.goal_queue_arrival_recheck(
                NOW, self._tmux(), qrecs, self.sid, self.CWD, "%9", self.tpath,
                "sess:0", False, set(), queue_fetch=lambda cwd: [1],
                state={}, sleep_fn=lambda *a, **k: None)
        self.assertTrue(any("skip:authority-unresolved" in ln for ln in logs))

    def test_none_fetch_undetermined_no_mutation(self):
        qrecs = {self.sid: {"base": [1, 2]}}
        logs = self._run(qrecs, lambda cwd: None, self._tmux())
        self.assertTrue(any("skip:undetermined" in ln for ln in logs))
        self.assertEqual(qrecs[self.sid], {"base": [1, 2]})

    def test_first_observation_seeds_no_keystroke(self):
        qrecs = {}
        tmux = self._tmux()
        logs = self._run(qrecs, lambda cwd: [4, 5], tmux)
        self.assertTrue(any("seed" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [4, 5])
        self.assertEqual(qrecs[self.sid]["lts"], NOW)

    def test_unchanged_tracks_no_keystroke(self):
        qrecs = {self.sid: {"base": [1, 2], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(qrecs, lambda cwd: [1, 2], tmux)
        self.assertTrue(any("track" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])

    def test_resolved_member_advances_base_no_keystroke(self):
        qrecs = {self.sid: {"base": [1, 2, 3], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        self._run(qrecs, lambda cwd: [1, 2], tmux)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])

    def test_arrival_types_and_advances_base(self):
        qrecs = {self.sid: {"base": [1, 2], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        handled = set()
        logs = self._run(qrecs, lambda cwd: [1, 2, 9], tmux, handled=handled,
                         state={})
        self.assertTrue(any("queue-arrival nudge" in ln for ln in logs))
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("#9", typed)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2, 9])   # promoted on send
        self.assertIn(self.sid, handled)

    def test_arrival_dry_run_would_nudge_no_mutation(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(qrecs, lambda cwd: [1, 2], tmux, dry_run=True)
        self.assertTrue(any("WOULD-NUDGE" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1])   # base NOT advanced

    def test_arrival_already_handled_defers_no_keystroke(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        handled = {self.sid}
        logs = self._run(qrecs, lambda cwd: [1, 2], tmux, handled=handled)
        self.assertTrue(any("skip:already-handled" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1])   # not advanced -> retry

    def test_arrival_busy_pane_defers(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        cap = "Waiting for 2 background agents to finish\n❯ "
        logs = self._run(qrecs, lambda cwd: [1, 2], tmux, handled=set(),
                         captured=cap)
        self.assertTrue(any("busy-bg-agent" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1])   # not advanced -> retry

    def test_swallowed_submit_does_not_advance_base(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux(enters_swallowed=5)
        handled = set()
        logs = self._run(qrecs, lambda cwd: [1, 2], tmux, handled=handled,
                         state={})
        self.assertTrue(any("submit-unverified" in ln for ln in logs))
        self.assertEqual(qrecs[self.sid]["base"], [1])   # unadvanced -> retry
        self.assertNotIn(self.sid, handled)

    def test_bounded_retry_backs_off_after_max_fails(self):
        # after MAX_SEND_FAILS consecutive swallows, accept the wave (advance
        # base) + reset the counter so the pane is not typed into every sweep.
        base_rec = {"base": [1], "first_seen": NOW - DAY,
                    "send_fails": qa.MAX_SEND_FAILS - 1}
        qrecs = {self.sid: dict(base_rec)}
        tmux = self._tmux(enters_swallowed=5)
        logs = self._run(qrecs, lambda cwd: [1, 2], tmux, handled=set(),
                         state={})
        self.assertTrue(any("backing off" in ln for ln in logs))
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])   # accepted the wave
        self.assertEqual(qrecs[self.sid]["send_fails"], 0)


# --------------------------------------------------------------------------- #
# 6. Integration — the wiring into goal_lane_sweep.
# --------------------------------------------------------------------------- #

class TestLaneSweepWiring(unittest.TestCase):
    """RED against the pre-wiring tree: `goal_lane_sweep` produces NO
    queue-arrival nudge for an armed FULL-authority pane whose gk queue GREW.
    GREEN once the sweep calls `goal_queue_arrival_recheck`."""

    CWD = "/home/newlevel/devel/qalane"

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

    def _armed_sweep(self, state, *, queue_fetch, dry_run=False,
                     handled=None, authority="full"):
        proj = Path(self._proj.name)
        tpath = _write_marker_transcript(proj, self.CWD, "sess-733-lane")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat(sid)
        gmarks = state.setdefault("goal_mark", {})
        gmarks[sid] = {"off": 0, "mark": {"state": "set", "ts": NOW}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value=authority), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=proj, state=state, dry_run=dry_run,
                handled=handled, backlog_fetch=lambda cwd: 0,
                queue_fetch=queue_fetch,
                sleep_fn=lambda *a, **k: None)
        return sid, tmux

    def test_queue_arrival_is_nudged(self):
        state = {"queue_arrival": {
            "sess-733-lane": {"base": [1, 2], "first_seen": NOW - DAY}}}
        sid, tmux = self._armed_sweep(
            state, queue_fetch=lambda cwd: [1, 2, 5177])
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed,
                      "an armed full-authority pane whose gk queue grew must be "
                      "nudged (RED before goal_lane_sweep wires "
                      "goal_queue_arrival_recheck)")
        self.assertIn("#5177", typed)
        self.assertEqual(state["queue_arrival"][sid]["base"], [1, 2, 5177])

    def test_first_observation_only_seeds(self):
        state = {}
        sid, tmux = self._armed_sweep(state, queue_fetch=lambda cwd: [1, 2])
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(state["queue_arrival"][sid]["base"], [1, 2])

    def test_reduced_authority_box_not_nudged(self):
        state = {"queue_arrival": {
            "sess-733-lane": {"base": [1], "first_seen": NOW - DAY}}}
        sid, tmux = self._armed_sweep(
            state, authority="fork-no-merge", queue_fetch=lambda cwd: [1, 9])
        self.assertEqual(tmux.typed_texts(), [])

    def test_no_queue_fetch_is_a_noop(self):
        state = {}
        sid, tmux = self._armed_sweep(state, queue_fetch=None)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertNotIn("queue_arrival", state)

    def test_gone_session_orphan_is_reaped(self):
        state = {"queue_arrival": {
            "orphan-733": {"base": [1], "lts": NOW - 3 * DAY},
            "sess-733-lane": {"base": [1], "first_seen": NOW - DAY}}}
        sid, tmux = self._armed_sweep(
            state, queue_fetch=lambda cwd: [1, 2])
        self.assertNotIn("orphan-733", state["queue_arrival"])
        self.assertIn(sid, state["queue_arrival"])


# --------------------------------------------------------------------------- #
# 7. run_once + cmd_watchdog wiring (signature + threading + real fetch).
# --------------------------------------------------------------------------- #

class TestRunOnceWiring(unittest.TestCase):
    def test_run_once_accepts_queue_fetch(self):
        import inspect
        self.assertIn("queue_fetch",
                      inspect.signature(wd.run_once).parameters)

    def test_goal_lane_sweep_accepts_queue_fetch(self):
        import inspect
        self.assertIn("queue_fetch",
                      inspect.signature(goal.goal_lane_sweep).parameters)

    def test_run_once_threads_it_into_the_sweep(self):
        import inspect
        src = inspect.getsource(wd.run_once)
        self.assertIn("queue_fetch=queue_fetch", src)

    def test_cmd_watchdog_wires_the_real_fetch(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("queue_fetch=_watchdog_queue_fetch", src)

    def test_real_fetch_unions_the_three_labels(self):
        # `_watchdog_queue_fetch` runs 3 label queries and unions the numbers.
        seen = {}

        class R:
            def __init__(self, out):
                self.returncode = 0
                self.stdout = out
                self.stderr = ""

        def fake_run(cmd, **kw):
            # find the --label value
            lbl = cmd[cmd.index("--label") + 1]
            seen[lbl] = seen.get(lbl, 0) + 1
            data = {"ready-for-review": '[{"number": 5177}]',
                    "needs-gatekeeper": '[{"number": 5310}]',
                    "prio:bounce": '[{"number": 3073}, {"number": 5177}]'}
            return R(data.get(lbl, "[]"))

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=fake_run):
            out = airuleset._watchdog_queue_fetch("/r")
        self.assertEqual(out, [3073, 5177, 5310])   # sorted union, deduped
        self.assertEqual(set(seen), {"ready-for-review", "needs-gatekeeper",
                                     "prio:bounce"})

    def test_real_fetch_non_full_authority_returns_none(self):
        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset.resolve_authority",
                        return_value="fork-no-merge"):
            self.assertIsNone(airuleset._watchdog_queue_fetch("/r"))

    def test_real_fetch_query_error_is_none(self):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", return_value=R()):
            self.assertIsNone(airuleset._watchdog_queue_fetch("/r"))


if __name__ == "__main__":
    unittest.main()
