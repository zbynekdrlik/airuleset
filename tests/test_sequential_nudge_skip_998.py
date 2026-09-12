"""#998 item 2 — a SEQUENTIAL-mode pane never gets a refill/queue-arrival nudge.

Both the lane-occupancy refill nudge (`goal.goal_lane_occupancy_nudge`) and the
queue-arrival recheck (`queue_arrival_recheck.goal_queue_arrival_recheck`) skip
with the decision word `skip:sequential-mode` when the pane's resolved mode is
sequential — ONE unit at a time, no refill. Parallel is byte-identical.
"""
import json
import sys
import tempfile
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from watchdog import goal  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402


def _seq_cwd():
    d = tempfile.mkdtemp()
    claude = Path(d) / ".claude"
    claude.mkdir()
    (claude / "lane-resources.json").write_text(json.dumps({"mode": "sequential"}))
    return d


class TestLaneOccupancySequentialSkip(TestCase):
    def test_sequential_cwd_skips_refill(self):
        cwd = _seq_cwd()
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs, owns = goal.goal_lane_occupancy_nudge(
                100000, lambda *a, **k: None, {}, "sid", cwd, "111",
                "captured", "/tmp/t.jsonl", 100000, "loc", None, False, set(),
                "/tmp/proj", backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        self.assertFalse(owns)
        self.assertTrue(any("skip:sequential-mode" in ln for ln in logs), logs)

    def test_parallel_cwd_does_not_skip_for_sequential(self):
        with tempfile.TemporaryDirectory() as cwd:
            with m.patch("airuleset.resolve_authority", return_value="full"):
                logs, _owns = goal.goal_lane_occupancy_nudge(
                    100000, lambda *a, **k: None, {}, "sid", cwd, "111",
                    "captured", "/tmp/t.jsonl", 100000, "loc", None, False,
                    set(), "/tmp/proj", backlog_fetch=lambda cwd: 5, state={},
                    sleep_fn=lambda s: None)
            self.assertFalse(any("skip:sequential-mode" in ln for ln in logs),
                             logs)


class TestQueueArrivalSequentialSkip(TestCase):
    def test_sequential_cwd_skips_queue_arrival(self):
        cwd = _seq_cwd()
        called = []
        with m.patch("airuleset.resolve_authority", return_value="full"):
            logs = qa.goal_queue_arrival_recheck(
                100000, lambda *a, **k: None, {}, "sid", cwd, "%9",
                "/tmp/t.jsonl", "sess:0", False, set(),
                queue_fetch=lambda c: called.append(c) or [1], state={},
                sleep_fn=lambda *a, **k: None)
        self.assertTrue(any("skip:sequential-mode" in ln for ln in logs), logs)
        self.assertEqual(called, [])  # never even fetched the queue

    def test_parallel_cwd_reaches_fetch(self):
        with tempfile.TemporaryDirectory() as cwd:
            called = []
            with m.patch("airuleset.resolve_authority", return_value="full"):
                qa.goal_queue_arrival_recheck(
                    100000, lambda *a, **k: None, {}, "sid", cwd, "%9",
                    "/tmp/t.jsonl", "sess:0", False, set(),
                    queue_fetch=lambda c: called.append(c) or [1], state={},
                    sleep_fn=lambda *a, **k: None)
            self.assertEqual(called, [cwd])  # parallel: fetch reached


if __name__ == "__main__":
    main()
