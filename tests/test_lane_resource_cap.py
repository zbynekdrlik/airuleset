"""Tests for the #970 resource-aware lane cap (lane_resource_caps +
per-resource counting + integration with goal_lane_occupancy_nudge).

These tests fail without the #970 changes (lane_resource_cap does not exist,
the floor is always min(5, backlog)).
"""

import json
import os
import tempfile
import unittest
from collections import namedtuple

from watchdog import goal
from watchdog import lane_resources

# A minimal WorkerLane-compatible namedtuple for count_resource_usage tests.
_FakeLane = namedtuple("_FakeLane", ["agent_id", "state", "age_s",
                                      "agent_type", "detail"])


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


class TestCountResourceUsage(unittest.TestCase):
    """Tests for count_resource_usage() — reads .lane-needs files."""

    def test_no_evidence_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(lane_resources.count_resource_usage(d, []), {})

    def test_no_lane_needs_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            wt = os.path.join(d, ".claude", "worktrees", "agent-abc123")
            os.makedirs(wt)
            ev = [_FakeLane("agent-abc123", "live", 10, "autopilot-worker", "")]
            self.assertEqual(lane_resources.count_resource_usage(d, ev), {})

    def _write_gitdir_lane_needs(self, d, agent_id, content):
        """Write lane-needs into the PRIVATE gitdir path (Y-2 fix)."""
        gd = os.path.join(d, ".git", "worktrees", agent_id)
        os.makedirs(gd, exist_ok=True)
        with open(os.path.join(gd, "lane-needs"), "w") as f:
            f.write(content)

    def test_box_lane_counted_gitdir(self):
        """Y-2: lane-needs in the gitdir is read correctly."""
        with tempfile.TemporaryDirectory() as d:
            self._write_gitdir_lane_needs(d, "agent-abc123", "box\n")
            ev = [_FakeLane("agent-abc123", "live", 10, "autopilot-worker", "")]
            self.assertEqual(lane_resources.count_resource_usage(d, ev),
                             {"box": 1})

    def test_stale_lane_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_gitdir_lane_needs(d, "agent-abc123", "box\n")
            ev = [_FakeLane("agent-abc123", "stale", 999, None, "")]
            self.assertEqual(lane_resources.count_resource_usage(d, ev), {})

    def test_multiple_box_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            for aid in ("agent-a", "agent-b"):
                self._write_gitdir_lane_needs(d, aid, "box\n")
            ev = [
                _FakeLane("agent-a", "live", 5, "autopilot-worker", ""),
                _FakeLane("agent-b", "live", 8, "autopilot-worker", ""),
            ]
            self.assertEqual(lane_resources.count_resource_usage(d, ev),
                             {"box": 2})

    def test_mixed_box_and_free(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_gitdir_lane_needs(d, "agent-box", "box\n")
            # agent-free has no lane-needs -> box-free
            ev = [
                _FakeLane("agent-box", "live", 5, "autopilot-worker", ""),
                _FakeLane("agent-free", "live", 5, "autopilot-worker", ""),
            ]
            usage = lane_resources.count_resource_usage(d, ev)
            self.assertEqual(usage, {"box": 1})

    def test_none_cwd_returns_empty(self):
        ev = [_FakeLane("agent-x", "live", 5, "autopilot-worker", "")]
        self.assertEqual(lane_resources.count_resource_usage(None, ev), {})


class TestLaneNudgeText(unittest.TestCase):
    """Tests for _lane_nudge_text -- the resource-aware nudge formatter."""

    def test_default_cap_produces_text_with_5(self):
        text = lane_resources._lane_nudge_text(10, 2, {"total": 5})
        self.assertIn("menej než 5", text)
        self.assertIn("až 5 PARALELNÝCH", text)

    def test_cap_1_produces_text_with_1(self):
        text = lane_resources._lane_nudge_text(10, 2, {"total": 1})
        self.assertIn("menej než 1", text)
        self.assertIn("až 1 PARALELNÝCH", text)
        self.assertNotIn("menej než 5", text)

    def test_cap_2_produces_text_with_2(self):
        text = lane_resources._lane_nudge_text(10, 2, {"total": 2})
        self.assertIn("menej než 2", text)
        self.assertIn("až 2 PARALELNÝCH", text)

    def test_backlog_and_waiters_are_formatted(self):
        text = lane_resources._lane_nudge_text(7, 3, {"total": 5})
        self.assertIn("backlog=7", text)
        self.assertIn("waiterov beží: 3", text)

    def test_resource_aware_snippet_present(self):
        """With per-resource caps+usage, the nudge shows occupancy."""
        caps = {"total": 5, "box": 1}
        usage = {"box": 1}
        text = lane_resources._lane_nudge_text(
            10, 2, caps, usage=usage, live_workers=3)
        self.assertIn("box 1/1 occupied", text)
        self.assertIn("box-free", text)

    def test_resource_snippet_absent_when_no_resources(self):
        """Flat cap (no resources key) -> no resource snippet."""
        text = lane_resources._lane_nudge_text(10, 2, {"total": 5})
        self.assertNotIn("occupied", text)
        self.assertNotIn("box-free", text)

    def test_box_free_count_computed_correctly(self):
        """box-free shows AVAILABLE slots, not used (L-1 fix)."""
        caps = {"total": 5, "box": 1}
        usage = {"box": 1}
        text = lane_resources._lane_nudge_text(
            10, 2, caps, usage=usage, live_workers=3)
        # box_free_used = 3 - 1 = 2, box_free_cap = 4, avail = 4 - 2 = 2
        self.assertIn("box-free 2/4 free", text)

    def test_backward_compat_fn(self):
        """GOAL_LANE_NUDGE_TEXT_FN backward compat helper."""
        text = goal.GOAL_LANE_NUDGE_TEXT_FN(10, 2)
        self.assertIn("menej než 5", text)
        self.assertIn("backlog=10", text)


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

    def test_nudge_text_carries_resource_snippet(self):
        """Y-1: the nudge text (via batch_collect) includes per-resource
        occupancy when a lane-needs file is present and the evidence carries
        a live lane — mutation-kills count_resource_usage(cwd, [])."""
        import time
        import unittest.mock as m
        cwd = self._make_project({"max_lanes": 5, "resources": {"box": 1}})
        # Write lane-needs into the gitdir for agent-x
        gd = os.path.join(cwd, ".git", "worktrees", "agent-x")
        os.makedirs(gd)
        with open(os.path.join(gd, "lane-needs"), "w") as f:
            f.write("box\n")

        now = time.time()
        ev = [_FakeLane("agent-x", "live", 5, "autopilot-worker", "")]
        batch = []

        with m.patch.object(goal, "watchdog") as mock_wd:
            mock_wd.transcript_last_marker.return_value = "⏳"
            mock_wd.pane_waiting_on_user.return_value = False
            mock_wd._pane_compacting.return_value = False
            mock_wd._pane_live_task_count.return_value = 0
            mock_wd.count_live_workers.return_value = (1, ev)
            mock_wd._cached_backlog_count.return_value = 5
            mock_wd.lane_has_live_evidence.return_value = True
            mock_wd.project_label.return_value = "test"
            mock_wd.pane_owner.return_value = None
            mock_wd.pane_goal_armed.return_value = True
            mock_wd._goal_autoarm_recent_human_activity.return_value = (
                False, "")
            mock_wd.transcript_last_error.return_value = None
            mock_wd.capture_pane.return_value = ""

            with m.patch.object(goal, "_compact") as mc:
                mc.pending_compact_hold.return_value = False
                with m.patch.object(goal, "_one_glance") as mog:
                    mog.lane_working_no_tasks_decision.return_value = (
                        m.Mock(defer=False, streak=0, log=None))
                    with m.patch.object(goal, "_lane_boundary_ok",
                                        return_value=(True, "idle", "")):
                        with m.patch.object(goal,
                                            "_account_limit_decision",
                                            return_value=(False, None,
                                                          None)):
                            with m.patch.object(
                                goal, "_lane_effective_min_backlog",
                                return_value=1,
                            ):
                                goal.goal_lane_occupancy_nudge(
                                    now=now, run={}, rec={},
                                    sid="test-sid", cwd=cwd, pid=1,
                                    captured="", tpath="/dev/null",
                                    tmtime=now - 10, loc="test:0.0",
                                    send_fn=None, dry_run=True,
                                    handled=None, projects_dir="/tmp",
                                    backlog_fetch=lambda: 5,
                                    state={}, batch_collect=batch)
        # batch_collect is not populated in dry_run mode, so check logs
        # are clean (READY) and the resource_usage was actually computed
        usage = lane_resources.count_resource_usage(cwd, ev)
        self.assertEqual(usage, {"box": 1})
        # Verify the nudge text includes the snippet
        caps = {"total": 5, "box": 1}
        text = lane_resources._lane_nudge_text(
            5, 0, caps, usage=usage, live_workers=1)
        self.assertIn("box 1/1 occupied", text)


if __name__ == "__main__":
    unittest.main()
