"""Tests for the #970 resource-aware lane cap (lane_resource_cap + effective
floor computation in goal_lane_occupancy_nudge).

These tests fail without the #970 changes (lane_resource_cap does not exist,
the floor is always min(5, backlog)).
"""

import json
import os
import tempfile
import unittest

from watchdog import goal


class TestLaneResourceCap(unittest.TestCase):
    """Unit tests for goal.lane_resource_cap() — returns (cap, reason)."""

    def test_absent_file_returns_flat_5_no_reason(self):
        """No .claude/lane-resources.json -> (5, None)."""
        with tempfile.TemporaryDirectory() as d:
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIsNone(reason)

    def test_valid_max_lanes_1(self):
        """A project declaring max_lanes=1 returns (1, None)."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 1}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, 1)
            self.assertIsNone(reason)

    def test_valid_max_lanes_3(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 3}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, 3)
            self.assertIsNone(reason)

    def test_max_lanes_5_returns_5(self):
        """max_lanes == GOAL_LANE_SATURATION_WORKERS -> (5, None)."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, 5)
            self.assertIsNone(reason)

    def test_max_lanes_0_returns_default_with_reason(self):
        """max_lanes=0 is out of range -> default + reason."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 0}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("out of range", reason)

    def test_max_lanes_above_5_returns_default_with_reason(self):
        """max_lanes > GOAL_LANE_SATURATION_WORKERS -> default + reason."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 10}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("out of range", reason)

    def test_malformed_json_returns_default_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                f.write("not json")
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("malformed JSON", reason)

    def test_none_cwd_returns_default_no_reason(self):
        cap, reason = goal.lane_resource_cap(None)
        self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
        self.assertIsNone(reason)

    def test_missing_max_lanes_key_returns_default_with_reason(self):
        """JSON is valid but has no max_lanes key -> default + reason."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"box": 1}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("not int", reason)

    def test_max_lanes_string_returns_default_with_reason(self):
        """max_lanes="1" (string, not int) -> default + reason."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": "1"}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("not int", reason)

    def test_json_list_returns_default_with_reason(self):
        """JSON is a list, not an object (M1 review finding)."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump([1], f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("not a JSON object", reason)

    def test_max_lanes_bool_returns_default_with_reason(self):
        """max_lanes=true is bool, not int (M1 review finding)."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": True}, f)
            cap, reason = goal.lane_resource_cap(d)
            self.assertEqual(cap, goal.GOAL_LANE_SATURATION_WORKERS)
            self.assertIn("bool", reason)


class TestLaneNudgeText(unittest.TestCase):
    """Tests for _lane_nudge_text -- the resource-aware nudge formatter."""

    def test_default_cap_produces_text_with_5(self):
        text = goal._lane_nudge_text(10, 2, 5)
        self.assertIn("menej než 5", text)
        self.assertIn("až 5 PARALELNÝCH", text)

    def test_cap_1_produces_text_with_1(self):
        text = goal._lane_nudge_text(10, 2, 1)
        self.assertIn("menej než 1", text)
        self.assertIn("až 1 PARALELNÝCH", text)
        self.assertNotIn("menej než 5", text)

    def test_cap_2_produces_text_with_2(self):
        text = goal._lane_nudge_text(10, 2, 2)
        self.assertIn("menej než 2", text)
        self.assertIn("až 2 PARALELNÝCH", text)

    def test_backlog_and_waiters_are_formatted(self):
        text = goal._lane_nudge_text(7, 3, 5)
        self.assertIn("backlog=7", text)
        self.assertIn("waiterov beží: 3", text)


class TestLaneResourceGuardConstants(unittest.TestCase):
    """Guard tests for the constants and file path."""

    def test_saturation_constant_is_5(self):
        """Guard: the flat default is still 5."""
        self.assertEqual(goal.GOAL_LANE_SATURATION_WORKERS, 5)

    def test_file_path(self):
        self.assertEqual(goal._LANE_RESOURCE_FILE,
                         os.path.join(".claude", "lane-resources.json"))


if __name__ == "__main__":
    unittest.main()
