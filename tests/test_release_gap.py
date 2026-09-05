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
FULL-authority box (the INVERSE of #618's lane-nudge authority gate — #618
narrowed that nudge's SKIP to `authority is None`, i.e. widened it to reduced-
authority boxes; this one nudges ONLY `== "full"`) and keystrokes the armed
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

    def test_ahead_equals_min_ahead_is_a_gap(self):
        # `ahead == min_ahead` MUST count as a gap (the documented "any
        # unreleased integration commit qualifies" default). A `<` -> `<=`
        # mutation would drop the commonest 1-commit case (review F7).
        rec = {"first_seen": NOW - CAD - 1, "last_nudge": None}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 3, "in_flight": False}, NOW, CAD, 3)
        self.assertEqual(action, "nudge")

    def test_ahead_just_below_min_ahead_clears(self):
        action, out, reason = rg._release_decision(
            {}, {"ahead": 2, "in_flight": False}, NOW, CAD, 3)
        self.assertEqual(action, "clear")

    def test_exactly_at_cadence_boundary_nudges(self):
        # now - anchor == cadence -> due (a `>=` -> `>` mutation would delay it).
        rec = {"first_seen": NOW - CAD, "last_nudge": None}
        action, out, reason = rg._release_decision(
            rec, {"ahead": 5, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "nudge")

    def test_bool_ahead_is_undetermined(self):
        # bool is an int subclass; True must NOT read as a 1-commit gap (F10).
        action, out, reason = rg._release_decision(
            {}, {"ahead": True, "in_flight": False}, NOW, CAD, MIN)
        self.assertEqual(action, "skip")


# --------------------------------------------------------------------------- #
# 1b. #883 — stalled-release flap-proof nudge (gh-observed inactivity anchor).
# --------------------------------------------------------------------------- #

class TestStalledRelease883(unittest.TestCase):
    """#883 — the 10h release 2.250.0 stall: the inflight action resets
    first_seen/last_nudge on every flap, so a stalled release that briefly
    transitions to cut-in-progress (a CI rerun) then back to shadow-failed
    never accumulates enough wait time to reach the cadence.

    The fix: a `last_stall_nudge` field that survives the inflight reset, plus
    a gh-observed `last_action_ts` inactivity anchor (30 min default)."""

    def _stalled_lane(self):
        """A LaneResult whose stage is in STALLED_STAGES (shadow-failed)."""
        from watchdog.release_lane import LaneResult
        return LaneResult("shadow-failed", "shadow FAILED", "run #42")

    def test_stalled_after_flap_nudges(self):
        """THE INCIDENT REPRO: rec fresh from an inflight reset 10 min ago
        (first_seen=now-600, last_nudge=None), rstate stalled, inactivity
        > 30 min. TODAY: returns wait:grace because anchor is < cadence.
        AFTER FIX: nudge:stalled because the inactivity anchor is met."""
        rec = {"first_seen": NOW - 600, "last_nudge": None}
        rstate = {"ahead": 254, "in_flight": True}
        action, _out, reason = rg._release_decision(
            rec, rstate, NOW, CAD, MIN,
            lane=self._stalled_lane(),
            last_action_ts=NOW - 35 * 60)  # 35 min inactive
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "stalled")

    def test_fresh_activity_suppresses_stall_nudge(self):
        """Inactivity < 30 min -> wait:stall-active, even if the generic
        anchor is past the cadence. This locks the '>30 min' half."""
        rec = {"first_seen": NOW - 2 * CAD, "last_nudge": None}
        rstate = {"ahead": 254, "in_flight": True}
        action, _out, reason = rg._release_decision(
            rec, rstate, NOW, CAD, MIN,
            lane=self._stalled_lane(),
            last_action_ts=NOW - 10 * 60)  # 10 min = fresh
        self.assertEqual(action, "wait")
        self.assertEqual(reason, "stall-active")

    def test_last_stall_nudge_survives_inflight_flap(self):
        """An inflight action must carry last_stall_nudge from the old rec
        so the re-nudge cadence survives the first_seen reset."""
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 5 * DAY,
               "last_stall_nudge": NOW - 600}
        rstate = {"ahead": 99, "in_flight": True}
        action, out, _reason = rg._release_decision(
            rec, rstate, NOW, CAD, MIN)
        self.assertEqual(action, "inflight")
        # last_stall_nudge carried forward, NOT reset
        self.assertEqual(out.get("last_stall_nudge"), NOW - 600)

    def test_stall_cadence_gates_re_nudge(self):
        """A stalled release recently nudged (last_stall_nudge < cadence ago)
        must wait, not re-nudge."""
        rec = {"first_seen": NOW - 600, "last_nudge": None,
               "last_stall_nudge": NOW - 600}
        rstate = {"ahead": 254, "in_flight": True}
        action, _out, reason = rg._release_decision(
            rec, rstate, NOW, CAD, MIN,
            lane=self._stalled_lane(),
            last_action_ts=NOW - 35 * 60)
        self.assertEqual(action, "wait")
        self.assertEqual(reason, "stall-cadence")

    def test_unmeasurable_last_action_falls_back_to_anchor(self):
        """When last_action_ts is None (unmeasurable), fall back to the
        existing anchor-based behavior (#134 anti-silence). An anchor
        past the cadence still nudges."""
        rec = {"first_seen": NOW - CAD - 1, "last_nudge": None}
        rstate = {"ahead": 254, "in_flight": True}
        action, _out, reason = rg._release_decision(
            rec, rstate, NOW, CAD, MIN,
            lane=self._stalled_lane(),
            last_action_ts=None)
        self.assertEqual(action, "nudge")
        # Falls back to the generic anchor path, reason is "due" (today's path)
        self.assertIn(reason, ("stalled", "due"))

    def test_unmeasurable_anchor_within_grace_waits(self):
        """Unmeasurable last_action_ts, anchor NOT past cadence -> wait.
        This anti-silence lock proves the fallback doesn't instant-nudge."""
        rec = {"first_seen": NOW - 1, "last_nudge": None}
        rstate = {"ahead": 254, "in_flight": True}
        action, _out, reason = rg._release_decision(
            rec, rstate, NOW, CAD, MIN,
            lane=self._stalled_lane(),
            last_action_ts=None)
        self.assertEqual(action, "wait")


class TestLastActionTs883(unittest.TestCase):
    """#883 — _last_action_ts pure helper unit tests."""

    def test_shadow_updated_at(self):
        rstate = {"shadow_run": {"updatedAt": "2026-09-05T10:00:00Z"}}
        ts = rg._last_action_ts(rstate)
        self.assertIsNotNone(ts)
        self.assertIsInstance(ts, float)

    def test_cut_pr_updated_at(self):
        rstate = {"cut_pr": {"updatedAt": "2026-09-05T10:00:00Z"}}
        ts = rg._last_action_ts(rstate)
        self.assertIsNotNone(ts)

    def test_max_of_sources(self):
        rstate = {
            "shadow_run": {"updatedAt": "2026-09-05T08:00:00Z"},
            "cut_pr": {"updatedAt": "2026-09-05T10:00:00Z"},
        }
        ts = rg._last_action_ts(rstate)
        # cut_pr.updatedAt is newer, so that should be the max
        shadow_ts = rg._parse_iso_ts("2026-09-05T08:00:00Z")
        cutpr_ts = rg._parse_iso_ts("2026-09-05T10:00:00Z")
        self.assertEqual(ts, cutpr_ts)
        self.assertGreater(ts, shadow_ts)

    def test_none_rstate_returns_none(self):
        self.assertIsNone(rg._last_action_ts(None))

    def test_empty_rstate_returns_none(self):
        self.assertIsNone(rg._last_action_ts({}))

    def test_malformed_timestamps_returns_none(self):
        rstate = {"shadow_run": {"updatedAt": "not-a-date"},
                  "cut_pr": {"updatedAt": 12345}}
        self.assertIsNone(rg._last_action_ts(rstate))

    def test_bool_timestamp_ignored(self):
        """Bool is an int subclass — must not read as a valid epoch."""
        rstate = {"shadow_run": {"updatedAt": True}}
        self.assertIsNone(rg._last_action_ts(rstate))

    def test_statuscheckrollup_completed_at(self):
        rstate = {"cut_pr": {
            "statusCheckRollup": [
                {"completedAt": "2026-09-05T12:00:00Z"},
                {"completedAt": "2026-09-05T11:00:00Z"},
            ],
        }}
        ts = rg._last_action_ts(rstate)
        expected = rg._parse_iso_ts("2026-09-05T12:00:00Z")
        self.assertEqual(ts, expected)


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
        self.assertTrue("release-gap" in typed or "release-idle" in typed)
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

    def test_persistent_swallow_backs_off_after_max_fails(self):
        # #749 — a pane whose submit is persistently swallowed (verify fails
        # every sweep) MUST back off after a bounded number of consecutive failed
        # sends instead of re-typing every ~60s sweep forever (the user-visible
        # "dokolecka promptuje"). Mirrors ops_wait_recheck's #714 bound.
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}

        def fetch(cwd):
            return {"ahead": 5, "in_flight": False}
        state = {}
        max_fails = getattr(rg, "MAX_SEND_FAILS", 3)
        for _ in range(max_fails):
            logs = self._run(rrecs, fetch, self._tmux(enters_swallowed=5),
                             handled=set(), state=state)
            self.assertTrue(any("submit-unverified" in ln for ln in logs))
        # After MAX_SEND_FAILS consecutive failures the anchor is advanced one
        # full cadence (back off), so the NEXT sweep produces a WAIT verdict with
        # NO keystroke — the storm is bounded, not per-sweep-forever.
        self.assertEqual(rrecs[self.sid]["last_nudge"], NOW)
        tmux = self._tmux(enters_swallowed=5)
        logs = self._run(rrecs, fetch, tmux, handled=set(), state=state)
        self.assertTrue(any("-> wait" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])

    def test_corrupt_send_fails_counter_tolerated(self):
        # #749 — the persisted send_fails counter crosses the JSON boundary, so a
        # corrupt/legacy non-int (or a bool) must read as 0 and never raise; the
        # first failure then counts as attempt 1, not a crash.
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None,
                            "send_fails": "not-an-int"}}
        logs = self._run(rrecs, lambda cwd: {"ahead": 5, "in_flight": False},
                         self._tmux(enters_swallowed=5), handled=set(), state={})
        self.assertTrue(any("attempt 1/" in ln for ln in logs))
        self.assertEqual(rrecs[self.sid]["send_fails"], 1)

    def test_busy_pane_defers_without_keystroke(self):
        # #749/#714 — a pane showing CC's "Waiting for N background agents to
        # finish" must NOT be typed into (the submit is swallowed and parks
        # orphaned). Defer without a keystroke; last_nudge unadvanced.
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        captured = "some ui rows\nWaiting for 2 background agents to finish\n❯ "
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs = rg.goal_release_gap_recheck(
                NOW, tmux, rrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, set(),
                release_state_fetch=lambda cwd: {"ahead": 5, "in_flight": False},
                state={}, sleep_fn=lambda *a, **k: None, cadence=CAD,
                min_ahead=MIN, captured=captured)
        self.assertTrue(any("busy" in ln.lower() for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(rrecs[self.sid]["last_nudge"])


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
                     handled=None, authority="full", captured=GOAL_ARMED_CAP):
        proj = Path(self._proj.name)
        tpath = _write_marker_transcript(proj, self.CWD, "sess-616-lane")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat(sid)
        gmarks = state.setdefault("goal_mark", {})
        gmarks[sid] = {"off": 0, "mark": {"state": "set", "ts": NOW}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   captured, model_type=True,
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
        self.assertTrue("release-gap" in typed or "release-idle" in typed)
        self.assertEqual(state["release_gap"][sid]["last_nudge"], NOW)

    def test_busy_bg_agent_pane_not_nudged_wiring(self):
        # #749 — locks the `captured=captured` wiring at goal.py's release-gap
        # call site: an armed pane whose capture carries CC's "Waiting for N
        # background agents to finish" line must reach the rider's busy-pane gate
        # via goal_lane_sweep and produce NO keystroke. Reverting `captured=
        # captured` makes the rider read None (fail-open) and this test types.
        busy_cap = "Waiting for 2 background agents to finish\n" + GOAL_ARMED_CAP
        state = {"release_gap": {
            "sess-616-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(
            state, captured=busy_cap,
            release_state_fetch=lambda cwd: {"ahead": 99, "in_flight": False})
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(state["release_gap"][sid]["last_nudge"])

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


# --------------------------------------------------------------------------- #
# 9. #594 busy-pane re-fire lock (review F2) — a delivered-but-unconfirmed nudge
#    into an actively-cycling armed loop must NOT re-deliver every sweep.
# --------------------------------------------------------------------------- #

class TestBusyPaneRefire594(_OrchBase):
    def _tmux_unconfirmed(self):
        # transcript_path=None: the Enter SUBMITS (clears the box —
        # delivered/queued) but writes NO `user` turn in the window, so
        # `send_verified` returns False yet sets out["delivered_unconfirmed"].
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=None)

    def test_delivered_unconfirmed_holds_dedup_across_sweeps(self):
        # A `delivered = ok` mutation (dropping the delivered_unconfirmed OR-half)
        # re-fires the ~600-char release nudge every 60s into the live gk loop —
        # the #594 incident. GREEN: ONE delivery, last_nudge advanced.
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        state = {}
        tmux = self._tmux_unconfirmed()
        for _ in range(3):
            with m.patch("airuleset.resolve_authority", return_value="full"):
                rg.goal_release_gap_recheck(
                    NOW, tmux, rrecs, self.sid, self.CWD, "%9", self.tpath,
                    "sess:0", False, set(),
                    release_state_fetch=lambda cwd: {"ahead": 7,
                                                     "in_flight": False},
                    state=state, sleep_fn=lambda *a, **k: None, cadence=CAD,
                    min_ahead=MIN)
        deliveries = [t for t in tmux.typed_texts() if "stuck-check" in t]
        self.assertEqual(len(deliveries), 1,
                         "a delivered-but-unconfirmed release nudge must NOT "
                         "re-deliver across sweeps (busy-pane spam, #594): got %d"
                         % len(deliveries))
        self.assertEqual(rrecs[self.sid]["last_nudge"], NOW)


# --------------------------------------------------------------------------- #
# 10. The raw fetch classifier (review F1/F3) — subprocess-mocked, per branch.
# --------------------------------------------------------------------------- #

class TestReleaseTrainRunInFlight(unittest.TestCase):
    def test_deploy_push_run_is_in_flight(self):
        runs = [{"status": "in_progress", "event": "push",
                 "headBranch": "main", "name": "Deploy to PROD"}]
        self.assertTrue(
            airuleset._release_train_run_in_flight(runs, "staging", "main"))

    def test_issue_comment_utility_run_on_main_is_not(self):
        # THE F1 fix: a constant issue_comment utility workflow on main must NOT
        # read as a release in flight (else it starves the nudge forever).
        runs = [{"status": "in_progress", "event": "issue_comment",
                 "headBranch": "main", "name": "Sub-dev Handoff Gate"}]
        self.assertFalse(
            airuleset._release_train_run_in_flight(runs, "staging", "main"))

    def test_release_named_workflow_dispatch_is_in_flight(self):
        runs = [{"status": "queued", "event": "workflow_dispatch",
                 "headBranch": "wip", "name": "Release Pipeline"}]
        self.assertTrue(
            airuleset._release_train_run_in_flight(runs, "staging", "main"))

    def test_completed_run_is_not(self):
        runs = [{"status": "completed", "event": "push",
                 "headBranch": "main", "name": "Deploy to PROD"}]
        self.assertFalse(
            airuleset._release_train_run_in_flight(runs, "staging", "main"))

    def test_feature_branch_non_deploy_push_is_not(self):
        runs = [{"status": "in_progress", "event": "push",
                 "headBranch": "feature-x", "name": "CI"}]
        self.assertFalse(
            airuleset._release_train_run_in_flight(runs, "staging", "main"))

    def test_empty_and_malformed(self):
        self.assertFalse(
            airuleset._release_train_run_in_flight([], "staging", "main"))
        self.assertFalse(
            airuleset._release_train_run_in_flight([None, "x", 5], "staging",
                                                  "main"))

    def test_gh_not_found(self):
        self.assertTrue(airuleset._gh_not_found("gh: Not Found (HTTP 404)"))
        self.assertTrue(airuleset._gh_not_found("HTTP 404"))
        self.assertFalse(airuleset._gh_not_found("error connecting to github.com"))
        self.assertFalse(airuleset._gh_not_found(""))


def _fake_run_factory(*, origin="https://github.com/o/n.git", origin_rc=0,
                      compare=None, staging=None, prs=None, runs=None,
                      deploy_runs=None, shadow_runs=None):
    """A subprocess.run replacement dispatching on argv (order-independent).
    compare/staging: (rc, stdout, stderr). prs: {base: [rows]}. runs: {status:
    [rows]}. deploy_runs/shadow_runs: [rows] for workflow-scoped run list calls.
    #846: compare stdout is now JSON {ahead, oldest} — callers pass the raw int
    and this factory wraps it."""
    import json as _json
    from subprocess import CompletedProcess

    def fake(argv, **kw):
        if argv[:2] == ["git", "-C"]:
            return CompletedProcess(argv, origin_rc, stdout=origin, stderr="")
        if argv[:2] == ["gh", "api"] and "compare" in argv[2]:
            rc, out, err = compare
            if rc == 0 and out and not out.strip().startswith("{"):
                out = _json.dumps({"ahead": int(out.strip()), "oldest": None})
            return CompletedProcess(argv, rc, stdout=out, stderr=err)
        if argv[:2] == ["gh", "api"] and "/branches/" in argv[2]:
            rc, out, err = staging
            return CompletedProcess(argv, rc, stdout=out, stderr=err)
        if argv[:3] == ["gh", "pr", "list"]:
            base = argv[argv.index("--base") + 1]
            return CompletedProcess(argv, 0,
                                    stdout=_json.dumps((prs or {}).get(base, [])),
                                    stderr="")
        if argv[:3] == ["gh", "run", "list"]:
            if "-w" in argv:
                wf = argv[argv.index("-w") + 1]
                if "deploy" in wf.lower() or "prod" in wf.lower():
                    return CompletedProcess(argv, 0,
                                            stdout=_json.dumps(deploy_runs or []),
                                            stderr="")
                return CompletedProcess(argv, 0,
                                        stdout=_json.dumps(shadow_runs or []),
                                        stderr="")
            st = argv[argv.index("--status") + 1]
            return CompletedProcess(argv, 0,
                                    stdout=_json.dumps((runs or {}).get(st, [])),
                                    stderr="")
        raise AssertionError("unexpected argv: %r" % argv)

    return fake


class TestWatchdogReleaseStateFetch(unittest.TestCase):
    def _fetch(self, **kw):
        with m.patch("subprocess.run", side_effect=_fake_run_factory(**kw)):
            return airuleset._watchdog_release_state_fetch("/r")

    def _assert_core(self, result, expected):
        """Assert the core 3 keys match, ignoring #846 widened keys."""
        self.assertIsInstance(result, dict)
        for k in ("ahead", "in_flight", "train"):
            self.assertEqual(result[k], expected[k], "key %r" % k)

    def test_non_github_origin_is_none(self):
        self.assertIsNone(self._fetch(origin="https://gitlab.com/o/n.git"))

    def test_origin_read_failure_is_none(self):
        self.assertIsNone(self._fetch(origin_rc=1))

    def test_no_develop_branch_compare_404_is_clean_no_gap(self):
        self._assert_core(
            self._fetch(compare=(1, "", "gh: Not Found (HTTP 404)")),
            {"ahead": 0, "in_flight": False, "train": False})

    def test_compare_transient_error_is_none(self):
        self.assertIsNone(self._fetch(compare=(1, "", "error connecting")))

    def test_ahead_zero_with_staging_is_clean_proven_train(self):
        # #698 contract extension: the drained (ahead 0) verdict no longer
        # short-circuits blind — the staging branch is verified so the result
        # can honestly carry `train` True for the release-landed escalation.
        # Same ahead/in_flight semantics as the pre-#698 short-circuit.
        self._assert_core(self._fetch(compare=(0, "0", ""),
                                      staging=(0, "staging", "")),
                          {"ahead": 0, "in_flight": False, "train": True})

    def test_unparsable_ahead_is_none(self):
        self.assertIsNone(self._fetch(compare=(0, "nope", "")))

    def test_gap_but_no_staging_is_clean_no_gap(self):
        # review F6: a gap on a 2-branch repo with a stray develop but no staging
        # is NOT a release train -> clean no-gap, never a spurious nudge.
        self._assert_core(
            self._fetch(compare=(0, "9", ""),
                        staging=(1, "", "Not Found (HTTP 404)")),
            {"ahead": 0, "in_flight": False, "train": False})

    def test_staging_transient_error_is_none(self):
        self.assertIsNone(self._fetch(compare=(0, "9", ""),
                                      staging=(1, "", "error connecting")))

    def test_gap_staging_release_pr_is_in_flight(self):
        self._assert_core(
            self._fetch(compare=(0, "9", ""), staging=(0, "staging", ""),
                        prs={"staging": [{"number": 1}]}),
            {"ahead": 9, "in_flight": True, "train": True})

    def test_gap_prod_release_pr_is_in_flight(self):
        self._assert_core(
            self._fetch(compare=(0, "9", ""), staging=(0, "staging", ""),
                        prs={"main": [{"number": 2}]}),
            {"ahead": 9, "in_flight": True, "train": True})

    def test_gap_no_pr_deploy_run_is_in_flight(self):
        self._assert_core(
            self._fetch(compare=(0, "9", ""), staging=(0, "staging", ""),
                        prs={}, runs={"in_progress": [
                            {"status": "in_progress", "event": "push",
                             "headBranch": "main", "name": "Deploy"}]}),
            {"ahead": 9, "in_flight": True, "train": True})

    def test_gap_no_pr_only_utility_run_is_stalled(self):
        # THE NUDGE CASE (review F1 both directions): real gap, no release PR,
        # and only a utility issue_comment workflow running on main -> NOT in
        # flight, so the loop is genuinely stalled and will be nudged.
        self._assert_core(
            self._fetch(compare=(0, "9", ""), staging=(0, "staging", ""),
                        prs={}, runs={"in_progress": [
                            {"status": "in_progress", "event": "issue_comment",
                             "headBranch": "main", "name": "Bounce Label Hygiene"}]}),
            {"ahead": 9, "in_flight": False, "train": True})

    def test_gap_no_pr_no_run_is_stalled(self):
        self._assert_core(
            self._fetch(compare=(0, "9", ""), staging=(0, "staging", ""),
                        prs={}, runs={}),
            {"ahead": 9, "in_flight": False, "train": True})


# --------------------------------------------------------------------------- #
# 9. #812 — hourly cadence defaults (owner "release train bez prestojov").
#
# LIVE FORENSICS (gk box, 2026-09-01): the rider ran every 60s all day (1103
# decisions) on a REAL 210->254-commit gap, but produced ZERO nudges. The
# release-in-flight signal flapped True ~7x across the day; every flap runs the
# `inflight` action, which resets the stall anchor (first_seen=now), so the
# `wait` age never reached the 6h cadence (longest continuous wait window
# 06:16->09:50 ~= 3h34m < 6h). Threshold was never the blocker (gap 210-254 >>
# min_ahead=1). Fix: cadence + floor -> 1h; keep min_ahead=1.
# --------------------------------------------------------------------------- #

class TestHourlyCadenceDefaults812(unittest.TestCase):
    @staticmethod
    def _no_env():
        # A patch.dict context that RESTORES os.environ after the test; the
        # caller pops AIRULESET_RELEASE_GAP_CADENCE_S inside it so the default
        # constant governs (the ONLY cadence override — the floor RELEASE_GAP_
        # MIN_S is a plain constant, never env-read; the gk box had no overrides).
        ctx = m.patch.dict(os.environ, {}, clear=False)
        return ctx

    def test_default_cadence_is_hourly(self):
        with self._no_env():
            os.environ.pop("AIRULESET_RELEASE_GAP_CADENCE_S", None)
            self.assertEqual(rg.RELEASE_GAP_CADENCE_S, 3600)
            self.assertEqual(rg._cadence(), 3600)

    def test_default_floor_is_hourly(self):
        # The floor drops to 1h so the owner's intended hourly cadence is
        # REACHABLE (a 2h floor would have clamped a 1h override back to 2h).
        self.assertEqual(rg.RELEASE_GAP_MIN_S, 3600)
        with self._no_env():
            os.environ["AIRULESET_RELEASE_GAP_CADENCE_S"] = "1800"  # 30 min
            self.assertEqual(rg._cadence(), 3600)                    # floored to 1h

    def test_min_ahead_default_kept_at_one(self):
        # DECISION LOCK (#812 rejected raising min_ahead to 15): the gap was
        # 210-254, so the threshold was never what stopped the nudge. Hourly
        # cadence + min_ahead=1 IS the "nonstop release train" intent.
        self.assertEqual(rg.RELEASE_GAP_MIN_AHEAD, 1)

    def test_incident_gap_nudges_after_one_hour(self):
        # The incident, encoded on the PURE decider: a real gap (ahead=254, no
        # release in flight) whose anchor is just over 1h old must NUDGE under
        # the DEFAULT cadence. Under the old 6h default this was "wait" (the
        # zero-nudges-all-day bug); under the new 1h default it nudges.
        with self._no_env():
            os.environ.pop("AIRULESET_RELEASE_GAP_CADENCE_S", None)
            cad = rg._cadence()
            rec = {"first_seen": NOW - 3601, "last_nudge": None}
            rstate = {"ahead": 254, "in_flight": False, "train": True}
            action, _out, reason = rg._release_decision(rec, rstate, NOW, cad, 1)
            self.assertEqual(action, "nudge")
            self.assertEqual(reason, "due")

    def test_incident_gap_would_have_waited_under_old_6h(self):
        # Companion proof the fix is causal: the SAME 1h-old gap, evaluated with
        # the OLD 6h cadence, is "wait" — that is exactly the all-day stall.
        rec = {"first_seen": NOW - 3601, "last_nudge": None}
        rstate = {"ahead": 254, "in_flight": False, "train": True}
        action, _out, _reason = rg._release_decision(rec, rstate, NOW, 6 * 3600, 1)
        self.assertEqual(action, "wait")


# --------------------------------------------------------------------------- #
# 10. #812 (d) — decision-log invariant: EVERY decider action reaches an
# explicit journalled line (#486 "no silent skip"). Confirmed live by the 1103
# journalled lines; locked here so no future branch goes silent.
# --------------------------------------------------------------------------- #

class TestDecisionLogInvariant812(_OrchBase):
    def _run_logs(self, fetch, rrecs):
        return self._run(rrecs, fetch, self._tmux())

    def test_every_action_emits_a_decision_line(self):
        cases = [
            # (label, fetch, seed rec)         -> expected decider action
            ("undetermined", lambda cwd: None, {"first_seen": NOW - 5 * DAY}),
            ("clear", lambda cwd: {"ahead": 0, "in_flight": False}, {}),
            ("inflight", lambda cwd: {"ahead": 99, "in_flight": True},
             {"first_seen": NOW - 5 * DAY}),
            ("wait", lambda cwd: {"ahead": 5, "in_flight": False}, {}),
            ("nudge", lambda cwd: {"ahead": 254, "in_flight": False},
             {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - CAD - 1}),
        ]
        for label, fetch, seed in cases:
            with self.subTest(action=label):
                logs = self._run_logs(fetch, {self.sid: dict(seed)})
                self.assertTrue(logs, "%s produced NO decision-log line" % label)
                self.assertTrue(
                    any("release-gap" in ln for ln in logs),
                    "%s log line not journalled as release-gap: %r"
                    % (label, logs))

    def test_inflight_log_carries_pre_reset_age(self):
        # #812 review F6: a flap that resets the stall anchor logs the CUMULATIVE
        # age the gap was tracked BEFORE the reset, so a flap-vs-cadence
        # starvation (the incident) is one journal grep, not a day of forensics.
        rrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        logs = self._run_logs(lambda cwd: {"ahead": 99, "in_flight": True}, rrecs)
        self.assertTrue(
            any("skip:release-in-flight" in ln and "pre-reset" in ln
                for ln in logs),
            "inflight log missing the pre-reset age: %r" % logs)


if __name__ == "__main__":
    unittest.main()
