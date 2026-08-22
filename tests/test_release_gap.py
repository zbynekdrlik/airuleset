"""#616 — release-gap nudge for an armed FULL-authority `/goal` loop.

The gatekeeper's own `/goal` loop merges sub-dev work into the integration
branch (`develop`) but has NO trigger to ever START the release train
(develop -> staging -> main + deploy), so merged work sits unreleased for days
(the owner's recurring complaint). Jobs 24/28 (`delivery_stall_watch` /
`stuck_main_sweep`) are DETECTION-ONLY (a Discord ping, never a keystroke),
measure the CHECKED-OUT branch (a detached HEAD on the gk box, not develop),
go silent past `DELIVERY_STALL_MAX_S`, and never check "a release is in
flight" — so they structurally cannot serve this.

This rider on `goal_lane_sweep`'s armed-pane loop (the mirror of #547/#578)
detects "integration branch is ahead of prod AND no release is in flight" on a
FULL-authority box (the #618 MIRROR — #618 narrowed the lane nudge to
`authority is None`; this nudges ONLY `== "full"`) and keystrokes the armed
session to run its release pipeline.

RED (this file, against the pre-implementation tree): `from watchdog import
release_gap` ImportErrors and `goal_lane_sweep` rejects the `release_state_fetch`
kwarg. GREEN once the module + wiring land.
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
from watchdog import release_gap as rg
from watchdog import session_status as ss

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
CAD = 1000   # a small test cadence
MIN = 1      # test min-ahead


# --------------------------------------------------------------------------- #
# 1. Pure decider — the safety-critical verdict logic.
# --------------------------------------------------------------------------- #

class TestReleaseDecision(unittest.TestCase):
    def test_none_rstate_skip_and_never_mutates(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 5 * DAY}
        action, out, reason = rg._release_decision(rec, None, NOW, CAD, MIN)
        self.assertEqual(action, "skip")
        self.assertEqual(reason, "undetermined")
        self.assertIs(out, rec)

    def test_ahead_not_int_skip(self):
        action, out, reason = rg._release_decision(
            {}, {"ahead": None, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "skip")

    def test_in_flight_non_bool_skip(self):
        action, out, reason = rg._release_decision(
            {}, {"ahead": 5, "in_flight": None}, NOW, CAD, MIN)
        self.assertEqual(action, "skip")

    def test_no_gap_clears(self):
        action, out, reason = rg._release_decision(
            {"first_seen": NOW - 5 * DAY}, {"ahead": 0, "in_flight": False},
            NOW, CAD, MIN)
        self.assertEqual(action, "clear")
        self.assertIsNone(out)

    def test_below_min_ahead_clears(self):
        action, out, reason = rg._release_decision(
            {}, {"ahead": 2, "in_flight": False}, NOW, CAD, 3)
        self.assertEqual(action, "clear")

    def test_gap_in_flight_resets_anchor_no_nudge(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 5 * DAY}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 99, "in_flight": True}, NOW, CAD, MIN)
        self.assertEqual(action, "inflight")
        self.assertEqual(out["first_seen"], NOW)   # anchor reset — train moving
        self.assertIsNone(out["last_nudge"])

    def test_gap_first_sight_waits_and_seeds(self):
        action, out, reason = rg._release_decision(
            {}, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "wait")
        self.assertEqual(reason, "grace")
        self.assertEqual(out["first_seen"], NOW)
        self.assertIsNone(out["last_nudge"])
        self.assertEqual(out["sig"], "ahead:5|inflight:False")

    def test_gap_past_cadence_from_first_seen_nudges(self):
        rec = {"first_seen": NOW - CAD - 1, "last_nudge": None}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "due")

    def test_gap_within_grace_waits(self):
        rec = {"first_seen": NOW - 1, "last_nudge": None}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "wait")

    def test_reping_past_window_from_last_nudge_nudges(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - CAD - 1}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "nudge")

    def test_reping_within_window_waits(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 1}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "wait")

    def test_nudge_verdict_preserves_last_nudge(self):
        # A "nudge" verdict is an INTENT; the caller advances last_nudge only on
        # a CONFIRMED submit, so the decider leaves it untouched.
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - CAD - 1}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(out["last_nudge"], NOW - CAD - 1)

    def test_malformed_rec_is_seeded_fresh(self):
        action, out, reason = rg._release_decision(
            "junk", {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "wait")
        self.assertEqual(out["first_seen"], NOW)


# --------------------------------------------------------------------------- #
# 2. Origin-slug parse (pure, in airuleset.py).
# --------------------------------------------------------------------------- #

class TestParseOriginSlug(unittest.TestCase):
    def test_https_git(self):
        self.assertEqual(
            airuleset._parse_origin_slug(
                "https://github.com/zbynekdrlik/odoo-erp.git"),
            "zbynekdrlik/odoo-erp")

    def test_https_no_git(self):
        self.assertEqual(
            airuleset._parse_origin_slug("https://github.com/owner/name"),
            "owner/name")

    def test_ssh_scp_form(self):
        self.assertEqual(
            airuleset._parse_origin_slug("git@github.com:owner/name.git"),
            "owner/name")

    def test_ssh_url_form(self):
        self.assertEqual(
            airuleset._parse_origin_slug("ssh://git@github.com/owner/name.git"),
            "owner/name")

    def test_garbage_is_none(self):
        self.assertIsNone(airuleset._parse_origin_slug("not a url"))

    def test_empty_is_none(self):
        self.assertIsNone(airuleset._parse_origin_slug(""))

    def test_none_is_none(self):
        self.assertIsNone(airuleset._parse_origin_slug(None))


# --------------------------------------------------------------------------- #
# 3. Per-repo TTL cache.
# --------------------------------------------------------------------------- #

class TestCachedReleaseState(unittest.TestCase):
    def test_second_read_within_ttl_hits_cache(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return {"ahead": 3, "in_flight": False}

        st = {}
        rg._cached_release_state("/r", f, st, NOW)
        rg._cached_release_state("/r", f, st, NOW + 60)
        self.assertEqual(len(calls), 1)

    def test_read_past_ttl_refetches(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return {"ahead": 3, "in_flight": False}

        st = {}
        rg._cached_release_state("/r", f, st, NOW, ttl=100)
        rg._cached_release_state("/r", f, st, NOW + 200, ttl=100)
        self.assertEqual(len(calls), 2)

    def test_none_failure_cached_only_for_fail_ttl(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return None

        st = {}
        rg._cached_release_state("/r", f, st, NOW, ttl=10000, fail_ttl=30)
        # inside fail_ttl -> cached
        rg._cached_release_state("/r", f, st, NOW + 10, ttl=10000, fail_ttl=30)
        self.assertEqual(len(calls), 1)
        # past fail_ttl -> refetch
        rg._cached_release_state("/r", f, st, NOW + 40, ttl=10000, fail_ttl=30)
        self.assertEqual(len(calls), 2)

    def test_wired_none_returns_none_no_cache_write(self):
        st = {}
        self.assertIsNone(rg._cached_release_state("/r", None, st, NOW))
        self.assertNotIn("release_state_cache", st)

    def test_fetch_exception_is_none(self):
        def boom(cwd):
            raise RuntimeError("x")

        st = {}
        self.assertIsNone(rg._cached_release_state("/r", boom, st, NOW))

    def test_per_cwd_keyed(self):
        calls = []

        def f(cwd):
            calls.append(cwd)
            return {"ahead": 1, "in_flight": False}

        st = {}
        rg._cached_release_state("/a", f, st, NOW)
        rg._cached_release_state("/b", f, st, NOW)
        self.assertEqual(len(calls), 2)


# --------------------------------------------------------------------------- #
# 4. Helpers.
# --------------------------------------------------------------------------- #

class TestHelpers(unittest.TestCase):
    def test_cadence_floored(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_RELEASE_GAP_CADENCE_S": "60"}):
            self.assertEqual(rg._cadence(), rg.RELEASE_GAP_MIN_S)
        with m.patch.dict(os.environ,
                          {"AIRULESET_RELEASE_GAP_CADENCE_S": "99999999"}):
            self.assertEqual(rg._cadence(), 99999999)

    def test_min_ahead_floored(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_RELEASE_GAP_MIN_AHEAD": "0"}):
            self.assertEqual(rg._min_ahead(), 1)
        with m.patch.dict(os.environ,
                          {"AIRULESET_RELEASE_GAP_MIN_AHEAD": "5"}):
            self.assertEqual(rg._min_ahead(), 5)

    def test_branch_env_overrides(self):
        with m.patch.dict(os.environ, {
                "AIRULESET_RELEASE_INTEGRATION_BRANCH": "dev",
                "AIRULESET_RELEASE_PROD_BRANCH": "prod"}):
            self.assertEqual(rg._integration_branch(), "dev")
            self.assertEqual(rg._prod_branch(), "prod")

    def test_nudge_text_names_branches_and_gap(self):
        t = rg._nudge_text(99, "develop", "main")
        self.assertTrue(t.startswith("stuck-check:"))
        self.assertIn("release-gap", t)
        self.assertIn("develop", t)
        self.assertIn("main", t)
        self.assertIn("99", t)


# --------------------------------------------------------------------------- #
# 5. Orphan reaper.
# --------------------------------------------------------------------------- #

class TestPruneReleaseGapOrphans(unittest.TestCase):
    def test_aged_not_visited_reaped(self):
        recs = {"gone": {"first_seen": NOW - 5 * DAY, "lts": NOW - 3 * DAY}}
        rg._prune_release_gap_orphans(recs, set(), NOW)
        self.assertNotIn("gone", recs)

    def test_visited_never_reaped_even_stale(self):
        recs = {"live": {"first_seen": NOW - 5 * DAY, "lts": NOW - 5 * DAY}}
        rg._prune_release_gap_orphans(recs, {"live"}, NOW)
        self.assertIn("live", recs)

    def test_recent_not_visited_kept(self):
        recs = {"recent": {"first_seen": NOW, "lts": NOW - 60}}
        rg._prune_release_gap_orphans(recs, set(), NOW)
        self.assertIn("recent", recs)

    def test_future_lts_kept(self):
        recs = {"future": {"first_seen": NOW, "lts": NOW + 5 * DAY}}
        rg._prune_release_gap_orphans(recs, set(), NOW)
        self.assertIn("future", recs)

    def test_non_dict_never_raises(self):
        rg._prune_release_gap_orphans(None, set(), NOW)  # no raise


# --------------------------------------------------------------------------- #
# 6. Orchestrator — the authority/fetch/decide/deliver path, direct.
# --------------------------------------------------------------------------- #

class _OrchBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/relgap"

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
                                              "sess-616-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, rrecs, fetch, tmux, *, dry_run=False, handled=None,
             state=None, authority="full"):
        with m.patch("airuleset.resolve_authority", return_value=authority):
            return rg.goal_release_gap_recheck(
                NOW, tmux, rrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                dry_run, handled, release_state_fetch=fetch,
                state=state if state is not None else {},
                sleep_fn=lambda *a, **k: None, cadence=CAD, min_ahead=MIN)


class TestOrchestrator(_OrchBase):
    def test_reduced_authority_skips_without_fetch(self):
        called = []
        rrecs = {}
        logs = self._run(rrecs, lambda cwd: called.append(cwd),
                         self._tmux(), authority="branch-merge")
        self.assertTrue(any("skip:not-full-authority" in ln for ln in logs))
        self.assertEqual(called, [])   # never fetched on a reduced box
        self.assertEqual(rrecs, {})

    def test_authority_unresolved_skips(self):
        rrecs = {}
        with m.patch("airuleset.resolve_authority",
                     side_effect=RuntimeError("boom")):
            logs = rg.goal_release_gap_recheck(
                NOW, self._tmux(), rrecs, self.sid, self.CWD, "%9", self.tpath,
                "sess:0", False, set(), release_state_fetch=lambda cwd: None,
                state={}, sleep_fn=lambda *a, **k: None, cadence=CAD)
        self.assertTrue(any("skip:authority-unresolved" in ln for ln in logs))

    def test_none_state_undetermined(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY}}
        logs = self._run(rrecs, lambda cwd: None, self._tmux())
        self.assertTrue(any("skip:undetermined" in ln for ln in logs))
        self.assertEqual(rrecs[self.sid], {"first_seen": NOW - 5 * DAY})

    def test_no_gap_clears_rec(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY}}
        logs = self._run(rrecs, lambda cwd: {"ahead": 0, "in_flight": False},
                         self._tmux())
        self.assertTrue(any("clear" in ln for ln in logs))
        self.assertNotIn(self.sid, rrecs)

    def test_release_in_flight_no_keystroke_resets_anchor(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        logs = self._run(rrecs, lambda cwd: {"ahead": 99, "in_flight": True},
                         tmux)
        self.assertTrue(any("skip:release-in-flight" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(rrecs[self.sid]["first_seen"], NOW)   # anchor reset

    def test_first_sight_waits_and_seeds(self):
        rrecs = {}
        logs = self._run(rrecs, lambda cwd: {"ahead": 5, "in_flight": False},
                         self._tmux())
        self.assertTrue(any("-> wait" in ln for ln in logs))
        self.assertEqual(rrecs[self.sid]["first_seen"], NOW)
        self.assertEqual(rrecs[self.sid]["lts"], NOW)

    def test_due_dry_run_would_nudge_no_mutation(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        logs = self._run(rrecs, lambda cwd: {"ahead": 5, "in_flight": False},
                         tmux, dry_run=True)
        self.assertTrue(any("WOULD-NUDGE" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(rrecs[self.sid]["last_nudge"])
        self.assertNotIn("lts", rrecs[self.sid])

    def test_due_real_delivery_types_and_advances(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        handled = set()
        state = {}
        logs = self._run(rrecs, lambda cwd: {"ahead": 42, "in_flight": False},
                         tmux, handled=handled, state=state)
        self.assertTrue(any("release-gap nudge" in ln for ln in logs))
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("release-gap", typed)
        self.assertIn("42", typed)
        self.assertEqual(rrecs[self.sid]["last_nudge"], NOW)
        self.assertIn(self.sid, handled)

    def test_due_but_already_handled_defers(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        handled = {self.sid}
        logs = self._run(rrecs, lambda cwd: {"ahead": 5, "in_flight": False},
                         tmux, handled=handled)
        self.assertTrue(any("skip:already-handled" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(rrecs[self.sid]["last_nudge"])

    def test_swallowed_submit_does_not_advance(self):
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux(enters_swallowed=5)
        handled = set()
        logs = self._run(rrecs, lambda cwd: {"ahead": 5, "in_flight": False},
                         tmux, handled=handled, state={})
        self.assertTrue(any("submit-unverified" in ln for ln in logs))
        self.assertIsNone(rrecs[self.sid]["last_nudge"])
        self.assertNotIn(self.sid, handled)


# --------------------------------------------------------------------------- #
# 7. Integration regression — the wiring into goal_lane_sweep.
# --------------------------------------------------------------------------- #

class TestLaneSweepWiring(unittest.TestCase):
    """RED against the pre-wiring tree: `goal_lane_sweep` produces NO release-gap
    nudge for an armed FULL-authority pane with a stalled gap past the cadence.
    GREEN once the sweep calls `goal_release_gap_recheck`."""

    CWD = "/home/newlevel/devel/relgaplane"

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

    def _armed_sweep(self, state, *, release_state_fetch, dry_run=False,
                     handled=None, authority="full"):
        proj = Path(self._proj.name)
        tpath = _write_marker_transcript(proj, self.CWD, "sess-616-lane")
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
            # backlog 0 -> the lane-occupancy nudge skips (no backlog); no
            # ops_wait_fetch -> that recheck is a no-op; so the ONLY possible
            # keystroke is the release-gap nudge under test.
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=proj, state=state, dry_run=dry_run,
                handled=handled, backlog_fetch=lambda cwd: 0,
                release_state_fetch=release_state_fetch,
                sleep_fn=lambda *a, **k: None)
        return sid, tmux

    def test_stalled_gap_past_cadence_is_nudged(self):
        state = {"release_gap": {
            "sess-616-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, release_state_fetch=lambda cwd: {"ahead": 99,
                                                    "in_flight": False})
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed,
                      "an armed full-authority pane with a stalled release gap "
                      "past cadence must be nudged (RED before goal_lane_sweep "
                      "wires goal_release_gap_recheck)")
        self.assertIn("release-gap", typed)
        self.assertEqual(state["release_gap"][sid]["last_nudge"], NOW)

    def test_release_in_flight_not_nudged(self):
        state = {"release_gap": {
            "sess-616-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, release_state_fetch=lambda cwd: {"ahead": 99,
                                                    "in_flight": True})
        self.assertEqual(tmux.typed_texts(), [])

    def test_reduced_authority_box_not_nudged(self):
        state = {"release_gap": {
            "sess-616-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, authority="branch-merge",
            release_state_fetch=lambda cwd: {"ahead": 99, "in_flight": False})
        self.assertEqual(tmux.typed_texts(), [])

    def test_no_gap_clears_state(self):
        state = {"release_gap": {
            "sess-616-lane": {"first_seen": NOW - 5 * DAY}}}
        sid, tmux = self._armed_sweep(
            state, release_state_fetch=lambda cwd: {"ahead": 0,
                                                    "in_flight": False})
        self.assertNotIn(sid, state["release_gap"])

    def test_no_release_state_fetch_is_a_noop(self):
        state = {}
        sid, tmux = self._armed_sweep(state, release_state_fetch=None)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertNotIn("release_gap", state)

    def test_gone_session_orphan_is_reaped(self):
        state = {"release_gap": {
            "orphan-616": {"first_seen": NOW - 5 * DAY, "lts": NOW - 3 * DAY},
            "sess-616-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, release_state_fetch=lambda cwd: {"ahead": 5,
                                                    "in_flight": False})
        self.assertNotIn("orphan-616", state["release_gap"])
        self.assertIn(sid, state["release_gap"])

    def test_dry_run_mutates_no_state(self):
        state = {"release_gap": {
            "sess-616-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, dry_run=True,
            release_state_fetch=lambda cwd: {"ahead": 9, "in_flight": False})
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(state["release_gap"][sid]["last_nudge"])


# --------------------------------------------------------------------------- #
# 8. run_once + cmd_watchdog wiring (signature + threading).
# --------------------------------------------------------------------------- #

class TestRunOnceWiring(unittest.TestCase):
    def test_run_once_accepts_release_state_fetch(self):
        import inspect
        self.assertIn("release_state_fetch",
                      inspect.signature(wd.run_once).parameters)

    def test_goal_lane_sweep_accepts_release_state_fetch(self):
        import inspect
        self.assertIn("release_state_fetch",
                      inspect.signature(goal.goal_lane_sweep).parameters)

    def test_run_once_threads_it_into_the_sweep(self):
        import inspect
        src = inspect.getsource(wd.run_once)
        self.assertIn("release_state_fetch=release_state_fetch", src)

    def test_cmd_watchdog_wires_the_real_fetch(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("release_state_fetch=_watchdog_release_state_fetch", src)

    def test_job20_docstring_mentions_release_gap(self):
        self.assertIn("release-gap", (wd.run_once.__doc__ or ""))


if __name__ == "__main__":
    unittest.main()
