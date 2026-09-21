"""Tests for the #970 resource-aware lane cap (lane_resource_caps +
per-resource counting + integration with goal_lane_occupancy_nudge).

These tests fail without the #970 changes (lane_resource_cap does not exist,
the floor is always min(5, backlog)).
"""

import json
import os
import tempfile
import unittest

from watchdog import goal
from watchdog import lane_resources


class TestLaneResourceCap(unittest.TestCase):
    """Unit tests for lane_resource_cap() backward compat — (cap, reason)."""

    def test_absent_file_returns_flat_5_no_reason(self):
        """No .claude/lane-resources.json -> (5, None)."""
        with tempfile.TemporaryDirectory() as d:
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIsNone(reason)

    def test_valid_max_lanes_1(self):
        """A project declaring max_lanes=1 returns (1, None)."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 1}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, 1)
            self.assertIsNone(reason)

    def test_valid_max_lanes_3(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 3}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, 3)
            self.assertIsNone(reason)

    def test_max_lanes_5_returns_5(self):
        """max_lanes == GOAL_LANE_SATURATION_WORKERS -> (5, None)."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, 5)
            self.assertIsNone(reason)

    def test_max_lanes_0_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 0}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("out of range", reason)

    def test_max_lanes_above_5_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 10}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("out of range", reason)

    def test_malformed_json_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                f.write("not json")
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("malformed JSON", reason)

    def test_none_cwd_returns_default_no_reason(self):
        cap, reason = lane_resources.lane_resource_cap(None)
        self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
        self.assertIsNone(reason)

    def test_missing_max_lanes_key_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"box": 1}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("not int", reason)

    def test_max_lanes_string_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": "1"}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("not int", reason)

    def test_json_list_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump([1], f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("not a JSON object", reason)

    def test_max_lanes_bool_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": True}, f)
            cap, reason = lane_resources.lane_resource_cap(d)
            self.assertEqual(cap, lane_resources.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("bool", reason)


class TestLaneResourceCaps(unittest.TestCase):
    """Tests for lane_resource_caps() — per-resource caps (#970 fix-forward)."""

    def test_absent_file_returns_total_only(self):
        with tempfile.TemporaryDirectory() as d:
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertEqual(caps, {"total": 5})
            self.assertIsNone(reason)

    def test_max_lanes_only_returns_total(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 3}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertEqual(caps, {"total": 3})
            self.assertIsNone(reason)

    def test_resources_box_1(self):
        """max_lanes=5, resources={"box": 1} -> {"total": 5, "box": 1}."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5, "resources": {"box": 1}}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertEqual(caps, {"total": 5, "box": 1})
            self.assertIsNone(reason)

    def test_resources_box_2(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5, "resources": {"box": 2}}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertEqual(caps, {"total": 5, "box": 2})
            self.assertIsNone(reason)

    def test_resources_not_dict_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5, "resources": "box"}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertEqual(caps["total"], 5)
            self.assertIn("not dict", reason)

    def test_resource_bool_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5, "resources": {"box": True}}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertIn("bool", reason)

    def test_resource_over_max_lanes_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 3, "resources": {"box": 4}}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertIn("out of range", reason)

    def test_resource_zero_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5, "resources": {"box": 0}}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertIn("out of range", reason)

    def test_resource_total_reserved(self):
        """L-2: resources.total is a reserved key -> default + reason."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5, "resources": {"total": 1}}, f)
            caps, reason = lane_resources.lane_resource_caps(d)
            self.assertIn("reserved", reason)

    def test_backward_compat_via_goal(self):
        """goal.lane_resource_caps is re-exported from lane_resources."""
        self.assertIs(goal.lane_resource_caps, lane_resources.lane_resource_caps)


class TestLaneResourceGuardConstants(unittest.TestCase):
    """Guard tests for the constants and file path."""

    def test_saturation_constant_is_5(self):
        """Guard: the flat default is still 5."""
        self.assertEqual(lane_resources.GOAL_LANE_SATURATION_WORKERS, 5)

    def test_file_path(self):
        self.assertEqual(lane_resources._LANE_RESOURCE_FILE,
                         os.path.join(".claude", "lane-resources.json"))

    def test_lane_needs_file(self):
        self.assertEqual(lane_resources._LANE_NEEDS_FILE, "lane-needs")

    def test_goal_reexports_constant(self):
        """GOAL_LANE_SATURATION_WORKERS is available from goal module."""
        self.assertEqual(goal.GOAL_LANE_SATURATION_WORKERS, 5)


class TestIntegrationNudgeWithResources(unittest.TestCase):
    """MEDIUM finding fix: integration test that goal_lane_occupancy_nudge
    respects lane-resources.json caps end-to-end.

    Calls goal_lane_occupancy_nudge (the real function) with a tmpdir
    containing a lane-resources.json, verifying:
    - saturated skip at cap=1 with 1 live worker
    - nudge text with effective cap when under-saturated
    - INVALID log when the file is malformed
    """

    def _make_project(self, lane_resources_data=None):
        """Create a tmpdir project with optional lane-resources.json."""
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        if lane_resources_data is not None:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump(lane_resources_data, f)
        return d

    def _call_nudge(self, cwd, live_workers=0, backlog_n=5):
        """Call goal_lane_occupancy_nudge with minimal wiring."""
        import time
        now = time.time()
        rec = {}
        state = {}

        def fake_backlog_fetch():
            return backlog_n

        # Stub out all the gates that would skip before the floor check
        import unittest.mock as m

        with m.patch.object(goal, "watchdog") as mock_wd:
            mock_wd.transcript_last_marker.return_value = "⏳"
            mock_wd.pane_waiting_on_user.return_value = False
            mock_wd._pane_compacting.return_value = False
            mock_wd._pane_live_task_count.return_value = 0
            mock_wd.count_live_workers.return_value = (live_workers, [])
            mock_wd._cached_backlog_count.return_value = backlog_n
            mock_wd.lane_has_live_evidence.return_value = live_workers > 0
            mock_wd.project_label.return_value = "test"
            mock_wd.pane_owner.return_value = None
            mock_wd.pane_goal_armed.return_value = True
            mock_wd._goal_autoarm_recent_human_activity.return_value = (
                False, "")
            mock_wd.transcript_last_error.return_value = None

            with m.patch.object(goal, "_compact") as mock_compact:
                mock_compact.pending_compact_hold.return_value = False
                with m.patch.object(goal, "_one_glance") as mock_og:
                    mock_og.lane_working_no_tasks_decision.return_value = (
                        m.Mock(defer=False, streak=0, log=None))
                    with m.patch.object(goal, "_lane_boundary_ok",
                                        return_value=(True, "idle", "")):
                        with m.patch.object(goal, "_account_limit_decision",
                                            return_value=(False, None, None)):
                            with m.patch.object(
                                goal, "_lane_effective_min_backlog",
                                return_value=1,
                            ):
                                logs, owns = (
                                    goal.goal_lane_occupancy_nudge(
                                        now=now, run={}, rec=rec,
                                        sid="test-sid",
                                        cwd=cwd, pid=1, captured="",
                                        tpath="/dev/null",
                                        tmtime=now - 10,
                                        loc="test:0.0",
                                        send_fn=None, dry_run=True,
                                        handled=None,
                                        projects_dir="/tmp",
                                        backlog_fetch=(
                                            fake_backlog_fetch),
                                        state=state,
                                        batch_collect=[]))
        return logs

    def test_saturated_at_cap_1(self):
        """With max_lanes=1, 1 live worker -> saturated skip."""
        cwd = self._make_project({"max_lanes": 1})
        logs = self._call_nudge(cwd, live_workers=1, backlog_n=5)
        log_text = " ".join(logs)
        self.assertIn("saturated", log_text)
        self.assertIn("effective_cap=1", log_text)

    def test_nudge_at_cap_1_zero_workers(self):
        """With max_lanes=1, 0 workers -> refill nudge (READY in dry_run)."""
        cwd = self._make_project({"max_lanes": 1})
        logs = self._call_nudge(cwd, live_workers=0, backlog_n=5)
        log_text = " ".join(logs)
        # dry_run=True -> READY path, not batch-collected
        self.assertIn("READY", log_text)
        self.assertNotIn("saturated", log_text)

    def test_invalid_file_logs_reason(self):
        """Malformed lane-resources.json -> INVALID log line."""
        cwd = self._make_project({"max_lanes": "bad"})
        logs = self._call_nudge(cwd, live_workers=0, backlog_n=5)
        log_text = " ".join(logs)
        self.assertIn("INVALID", log_text)
        self.assertIn("not int", log_text)

    def test_resource_aware_cap(self):
        """With max_lanes=5 + resources.box=1, the total cap is 5."""
        cwd = self._make_project({"max_lanes": 5, "resources": {"box": 1}})
        logs = self._call_nudge(cwd, live_workers=4, backlog_n=10)
        log_text = " ".join(logs)
        # 4 < 5 = total cap -> not saturated -> READY (dry_run)
        self.assertIn("READY", log_text)
        self.assertNotIn("saturated", log_text)

    def test_resource_aware_saturated(self):
        """With max_lanes=5, 5 workers -> saturated."""
        cwd = self._make_project({"max_lanes": 5, "resources": {"box": 1}})
        logs = self._call_nudge(cwd, live_workers=5, backlog_n=10)
        log_text = " ".join(logs)
        self.assertIn("saturated", log_text)



if __name__ == "__main__":
    unittest.main()
