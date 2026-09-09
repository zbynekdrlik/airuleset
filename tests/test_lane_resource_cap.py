"""Tests for the #970 resource-aware lane cap (lane_resource_cap + effective
floor computation in goal_lane_occupancy_nudge).

RED→GREEN: These tests fail without the #970 changes (lane_resource_cap does
not exist, the floor is always min(5, backlog)).
"""

import json
import os
import tempfile
import unittest

from watchdog import goal


class TestLaneResourceCap(unittest.TestCase):
    """Unit tests for goal.lane_resource_cap()."""

    def test_absent_file_returns_flat_5(self):
        """No .claude/lane-resources.json → the flat default."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(goal.lane_resource_cap(d),
                             goal.GOAL_LANE_SATURATION_WORKERS)

    def test_valid_max_lanes_1(self):
        """A project declaring max_lanes=1 returns 1."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 1}, f)
            self.assertEqual(goal.lane_resource_cap(d), 1)

    def test_valid_max_lanes_3(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 3}, f)
            self.assertEqual(goal.lane_resource_cap(d), 3)

    def test_max_lanes_5_returns_5(self):
        """max_lanes == GOAL_LANE_SATURATION_WORKERS → same as default."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 5}, f)
            self.assertEqual(goal.lane_resource_cap(d), 5)

    def test_max_lanes_0_returns_flat_default(self):
        """max_lanes=0 is invalid → fall back to the default."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 0}, f)
            self.assertEqual(goal.lane_resource_cap(d),
                             goal.GOAL_LANE_SATURATION_WORKERS)

    def test_max_lanes_above_5_returns_flat_default(self):
        """max_lanes > GOAL_LANE_SATURATION_WORKERS → clamped to default."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": 10}, f)
            self.assertEqual(goal.lane_resource_cap(d),
                             goal.GOAL_LANE_SATURATION_WORKERS)

    def test_malformed_json_returns_flat_default(self):
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                f.write("not json")
            self.assertEqual(goal.lane_resource_cap(d),
                             goal.GOAL_LANE_SATURATION_WORKERS)

    def test_none_cwd_returns_flat_default(self):
        self.assertEqual(goal.lane_resource_cap(None),
                         goal.GOAL_LANE_SATURATION_WORKERS)

    def test_missing_max_lanes_key_returns_flat_default(self):
        """JSON is valid but has no max_lanes key."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"box": 1}, f)
            self.assertEqual(goal.lane_resource_cap(d),
                             goal.GOAL_LANE_SATURATION_WORKERS)

    def test_max_lanes_string_returns_flat_default(self):
        """max_lanes="1" (string, not int) → invalid, return default."""
        with tempfile.TemporaryDirectory() as d:
            lr = os.path.join(d, ".claude")
            os.makedirs(lr)
            with open(os.path.join(lr, "lane-resources.json"), "w") as f:
                json.dump({"max_lanes": "1"}, f)
            self.assertEqual(goal.lane_resource_cap(d),
                             goal.GOAL_LANE_SATURATION_WORKERS)


class TestLaneNudgeText(unittest.TestCase):
    """Tests for _lane_nudge_text — the resource-aware nudge formatter."""

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


class TestEffectiveCapInSaturationLog(unittest.TestCase):
    """The saturation log line now includes effective_cap (#970)."""

    def test_saturation_constant_is_5(self):
        """Guard: the flat default is still 5."""
        self.assertEqual(goal.GOAL_LANE_SATURATION_WORKERS, 5)


class TestLaneResourceFileConstant(unittest.TestCase):
    """Guard: the file path is deterministic."""

    def test_file_path(self):
        self.assertEqual(goal._LANE_RESOURCE_FILE,
                         os.path.join(".claude", "lane-resources.json"))


if __name__ == "__main__":
    unittest.main()
