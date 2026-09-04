"""Tests for the #846 release-lane classifier, the semantic flip in
_release_decision, the widened _nudge_text, and the footer segment."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watchdog.release_lane import (
    STAGES,
    STALLED_STAGES,
    LaneResult,
    classify_release_lane,
)
from watchdog.release_gap import (
    _release_decision,
    _nudge_text,
    _deploy_age_hours,
    _parse_iso_ts,
)
import statusbar


# ---------------------------------------------------------------------------
# Classifier — per stage
# ---------------------------------------------------------------------------


class TestClassifierStages(unittest.TestCase):
    def test_promote_ready(self):
        lstate = {"promote_pr": {"number": 42, "statusCheckRollup": [], "mergeable": "MERGEABLE"}}
        r = classify_release_lane(lstate)
        self.assertEqual(r.stage, "promote-ready")
        self.assertIn("42", r.evidence)
        self.assertIn("zmerguj", r.action)

    def test_cut_ci_red(self):
        lstate = {
            "cut_pr": {
                "number": 10,
                "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}],
            },
        }
        r = classify_release_lane(lstate)
        self.assertEqual(r.stage, "cut-ci-red")
        self.assertIn("10", r.evidence)
        self.assertIn("NIKDY re-cut", r.action)

    def test_shadow_failed(self):
        lstate = {
            "cut_pr": {"number": 10, "statusCheckRollup": []},
            "shadow_run": {"conclusion": "failure", "databaseId": 999},
        }
        r = classify_release_lane(lstate)
        self.assertEqual(r.stage, "shadow-failed")
        self.assertIn("999", r.evidence)
        self.assertIn("NIKDY re-cut", r.action)

    def test_cut_in_progress(self):
        lstate = {
            "cut_pr": {"number": 10, "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}]},
        }
        r = classify_release_lane(lstate)
        self.assertEqual(r.stage, "cut-in-progress")
        self.assertIn("10", r.evidence)

    def test_deploying(self):
        lstate = {"in_flight": True, "ahead": 5}
        r = classify_release_lane(lstate)
        self.assertEqual(r.stage, "deploying")

    def test_no_cut(self):
        lstate = {"ahead": 5}
        r = classify_release_lane(lstate)
        self.assertEqual(r.stage, "no-cut")
        self.assertIn("develop", r.action)

    def test_unknown_empty(self):
        r = classify_release_lane({})
        self.assertEqual(r.stage, "unknown")

    def test_unknown_none(self):
        r = classify_release_lane(None)
        self.assertEqual(r.stage, "unknown")


class TestClassifierMissingFields(unittest.TestCase):
    def test_missing_cut_pr_degrades(self):
        lstate = {"ahead": 5, "shadow_run": {"conclusion": "failure", "databaseId": 1}}
        r = classify_release_lane(lstate)
        self.assertIn(r.stage, ("no-cut", "deploying", "unknown"))

    def test_missing_promote_pr_degrades(self):
        lstate = {"ahead": 5}
        r = classify_release_lane(lstate)
        self.assertNotEqual(r.stage, "promote-ready")

    def test_cut_pr_no_number_degrades(self):
        lstate = {"cut_pr": {"statusCheckRollup": []}}
        r = classify_release_lane(lstate)
        self.assertNotIn(r.stage, ("cut-ci-red", "cut-in-progress"))


class TestStages(unittest.TestCase):
    def test_stalled_stages_are_subset(self):
        for s in STALLED_STAGES:
            self.assertIn(s, STAGES)


# ---------------------------------------------------------------------------
# Semantic flip: in_flight + STALLED lane → nudge (RED test)
# ---------------------------------------------------------------------------


class TestSemanticFlip(unittest.TestCase):
    """The KEY behavioural change: in_flight + cut-ci-red today returns
    'inflight' (suppress) → new 'nudge' (the semantic flip)."""

    def _rstate(self, ahead=10, in_flight=True):
        return {"ahead": ahead, "in_flight": in_flight, "train": True}

    def test_legacy_in_flight_suppresses_without_lane(self):
        """lane=None (legacy) → in_flight=True → 'inflight' (today's behaviour)."""
        action, _, reason = _release_decision(
            {}, self._rstate(), now=10000, cadence=3600, min_ahead=1, lane=None)
        self.assertEqual(action, "inflight")

    def test_in_flight_plus_stalled_lane_nudges(self):
        """lane with cut-ci-red + in_flight=True → 'nudge' after cadence."""
        lane = LaneResult("cut-ci-red", "fix it", "PR #10")
        action, _, reason = _release_decision(
            {"first_seen": 0, "last_nudge": None},
            self._rstate(), now=10000, cadence=3600, min_ahead=1, lane=lane)
        self.assertEqual(action, "nudge")

    def test_in_flight_plus_shadow_failed_nudges(self):
        lane = LaneResult("shadow-failed", "fix shadow", "run #1")
        action, _, reason = _release_decision(
            {"first_seen": 0, "last_nudge": None},
            self._rstate(), now=10000, cadence=3600, min_ahead=1, lane=lane)
        self.assertEqual(action, "nudge")

    def test_in_flight_plus_moving_lane_suppresses(self):
        """A genuinely moving lane (cut-in-progress) still suppresses."""
        lane = LaneResult("cut-in-progress", "running", "PR #10")
        action, _, reason = _release_decision(
            {}, self._rstate(), now=10000, cadence=3600, min_ahead=1, lane=lane)
        self.assertEqual(action, "inflight")

    def test_in_flight_plus_deploying_suppresses(self):
        lane = LaneResult("deploying", "deploy running", "")
        action, _, reason = _release_decision(
            {}, self._rstate(), now=10000, cadence=3600, min_ahead=1, lane=lane)
        self.assertEqual(action, "inflight")


# ---------------------------------------------------------------------------
# Threshold boundary-exact fixtures (2h/3h)
# ---------------------------------------------------------------------------


class TestThresholds(unittest.TestCase):
    def test_2h_boundary_wait(self):
        """At exactly 2h - 1s → wait (not yet due)."""
        rstate = {"ahead": 10, "in_flight": False, "train": True}
        action, _, _ = _release_decision(
            {"first_seen": 0}, rstate, now=7199, cadence=7200, min_ahead=1)
        self.assertEqual(action, "wait")

    def test_2h_boundary_nudge(self):
        """At exactly 2h → nudge."""
        rstate = {"ahead": 10, "in_flight": False, "train": True}
        action, _, _ = _release_decision(
            {"first_seen": 0}, rstate, now=7200, cadence=7200, min_ahead=1)
        self.assertEqual(action, "nudge")


# ---------------------------------------------------------------------------
# Deploy age helpers
# ---------------------------------------------------------------------------


class TestDeployAge(unittest.TestCase):
    def test_parse_iso_ts(self):
        ts = _parse_iso_ts("2026-09-05T10:00:00Z")
        self.assertIsInstance(ts, float)
        self.assertGreater(ts, 0)

    def test_parse_iso_ts_none(self):
        self.assertIsNone(_parse_iso_ts(None))
        self.assertIsNone(_parse_iso_ts(""))
        self.assertIsNone(_parse_iso_ts(42))

    def test_deploy_age_hours(self):
        now = _parse_iso_ts("2026-09-05T13:00:00Z")
        rstate = {"last_deploy_ts": "2026-09-05T10:00:00Z"}
        age = _deploy_age_hours(rstate, now)
        self.assertAlmostEqual(age, 3.0, places=1)

    def test_deploy_age_none_missing(self):
        self.assertIsNone(_deploy_age_hours({}, 10000))
        self.assertIsNone(_deploy_age_hours(None, 10000))


# ---------------------------------------------------------------------------
# Nudge text — stage-derived + ≤700 cap
# ---------------------------------------------------------------------------


class TestNudgeText(unittest.TestCase):
    def test_legacy_no_lane(self):
        text = _nudge_text(10, "develop", "main")
        self.assertTrue(text.startswith("stuck-check:"))
        self.assertIn("develop", text)
        self.assertIn("10", text)

    def test_stage_derived_with_lane(self):
        lane = LaneResult("cut-ci-red", "oprav CI", "PR #10")
        text = _nudge_text(10, "develop", "main", lane=lane, deploy_age_h=7)
        self.assertTrue(text.startswith("stuck-check:"))
        self.assertIn("oprav CI", text)
        self.assertIn("PR #10", text)
        self.assertIn("~7h", text)

    def test_nudge_text_cap_700(self):
        lane = LaneResult("shadow-failed", "x" * 600, "run #1")
        text = _nudge_text(10, "develop", "main", lane=lane, deploy_age_h=5)
        self.assertLessEqual(len(text), 700)

    def test_no_cut_stage_text(self):
        lane = LaneResult("no-cut", "spusti release pipeline: otvor develop→staging PR (cut)", "")
        text = _nudge_text(44, "develop", "main", lane=lane, deploy_age_h=7)
        self.assertIn("spusti release", text)
        self.assertIn("FROZEN", text)


# ---------------------------------------------------------------------------
# Footer segment — show/hide/stale
# ---------------------------------------------------------------------------


class TestReleaseIdleSegment(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.home = self.tmpdir
        self.cwd = "/some/repo"
        self.cache_dir = os.path.join(self.tmpdir, ".claude", "release-idle")
        os.makedirs(self.cache_dir, exist_ok=True)
        key = statusbar.cwd_key(self.cwd)
        self.cache_path = os.path.join(self.cache_dir, "%s.json" % key)

    def _write_cache(self, deploy_age_h, ts=None):
        ts = ts if ts is not None else time.time()
        with open(self.cache_path, "w") as fh:
            json.dump({"ts": ts, "deploy_age_h": deploy_age_h}, fh)

    def test_shown_at_breach(self):
        now = time.time()
        self._write_cache(5.0, ts=now)
        seg = statusbar.release_idle_segment(cwd=self.cwd, home=self.home, now=now)
        self.assertIn("rel 5h", seg)

    def test_hidden_below_breach(self):
        now = time.time()
        self._write_cache(2.0, ts=now)
        seg = statusbar.release_idle_segment(cwd=self.cwd, home=self.home, now=now)
        self.assertEqual(seg, "")

    def test_hidden_when_stale(self):
        now = time.time()
        self._write_cache(5.0, ts=now - 7200)  # 2h old > 1h stale threshold
        seg = statusbar.release_idle_segment(cwd=self.cwd, home=self.home, now=now)
        self.assertEqual(seg, "")

    def test_hidden_no_cwd(self):
        seg = statusbar.release_idle_segment(cwd=None, home=self.home)
        self.assertEqual(seg, "")

    def test_hidden_no_cache(self):
        seg = statusbar.release_idle_segment(cwd=self.cwd, home=self.home)
        self.assertEqual(seg, "")


# ---------------------------------------------------------------------------
# Legacy backward compat — _release_decision without lane
# ---------------------------------------------------------------------------


class TestLegacyCompat(unittest.TestCase):
    """Verify _release_decision with lane=None is byte-identical to pre-#846."""

    def test_skip_undetermined(self):
        a, r, reason = _release_decision(None, None, 0, 3600, 1)
        self.assertEqual(a, "skip")

    def test_clear_no_gap(self):
        a, r, reason = _release_decision({}, {"ahead": 0, "in_flight": False, "train": True}, 0, 3600, 1)
        self.assertEqual(a, "clear")

    def test_inflight_legacy(self):
        a, r, reason = _release_decision({}, {"ahead": 5, "in_flight": True, "train": True}, 0, 3600, 1)
        self.assertEqual(a, "inflight")

    def test_wait_inside_cadence(self):
        a, r, reason = _release_decision(
            {"first_seen": 0}, {"ahead": 5, "in_flight": False, "train": True}, 100, 3600, 1)
        self.assertEqual(a, "wait")

    def test_nudge_past_cadence(self):
        a, r, reason = _release_decision(
            {"first_seen": 0}, {"ahead": 5, "in_flight": False, "train": True}, 3601, 3600, 1)
        self.assertEqual(a, "nudge")


if __name__ == "__main__":
    unittest.main()
