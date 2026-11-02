"""#1286: Cargo output outside an agent worktree must age out under disk pressure."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from watchdog import disk_guard as dg
from watchdog import disk_guard_stale_build as sb

DAY = 86400
NOW = 1_800_000_000.0


def _age(path, days):
    t = NOW - days * DAY
    for root, _dirs, files in os.walk(path):
        for n in files:
            os.utime(os.path.join(root, n), (t, t))


def _target(parent, days=30, tag=True):
    d = Path(parent) / "target"
    (d / "debug").mkdir(parents=True)
    (d / "debug" / "app").write_bytes(b"x" * 100)
    if tag:
        (d / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    _age(d, days)
    return d


def _rows(home):
    return sb.discover_stale_build(home=home, now=NOW, live_fn=lambda p: False)


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def candidates(self):
        return sorted(r["path"] for r in _rows(self.home) if r["reason"] is None)

    def test_a_stale_cargo_target_outside_devel_is_a_candidate(self):
        d = _target(Path(self.home) / "restreamer-buildcheck")
        self.assertEqual(self.candidates(), [str(d)])
        self.assertEqual(_rows(self.home)[0]["bytes"], 100 + len(
            "Signature: 8a477f597d28d172789f06886806bc55\n"))

    def test_a_tauri_target_two_levels_down_is_found(self):
        d = _target(Path(self.home) / "copy" / "src-tauri")
        self.assertEqual(self.candidates(), [str(d)])

    def test_a_target_under_devel_is_found(self):
        d = _target(Path(self.home) / "devel" / "app" / "repo")
        self.assertEqual(self.candidates(), [str(d)])

    def test_the_walk_stops_at_its_depth(self):
        _target(Path(self.home) / "a" / "b" / "c" / "d")
        self.assertEqual(self.candidates(), [])

    def test_a_directory_named_target_without_the_cargo_tag_stays(self):
        _target(Path(self.home) / "photos", tag=False)
        self.assertEqual(self.candidates(), [])

    def test_a_recent_target_gives_no_row(self):
        _target(Path(self.home) / "work", days=3)
        self.assertEqual(_rows(self.home), [])

    def test_one_fresh_file_in_an_old_target_keeps_all_of_it(self):
        d = _target(Path(self.home) / "work", days=30)
        fresh = d / "debug" / "new"
        fresh.write_bytes(b"y")
        os.utime(fresh, (NOW - DAY, NOW - DAY))
        self.assertEqual(_rows(self.home), [])

    def test_a_target_in_live_use_is_kept_with_a_reason(self):
        _target(Path(self.home) / "work")
        rows = sb.discover_stale_build(home=self.home, now=NOW, live_fn=lambda p: True)
        self.assertEqual([r["reason"] is None for r in rows], [False])

    def test_a_symlink_is_never_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        _target(outside.name)
        os.symlink(outside.name, os.path.join(self.home, "link"))
        self.assertEqual(_rows(self.home), [])

    def test_hidden_dirs_and_node_modules_are_not_scanned(self):
        _target(Path(self.home) / ".cache" / "x")
        _target(Path(self.home) / "app" / "node_modules" / "y")
        self.assertEqual(_rows(self.home), [])

    def test_gk_scratch_children_stay_unless_they_are_cargo_targets(self):
        wt = Path(self.home) / "gk-scratch" / "apk-1-wt"
        wt.mkdir(parents=True)
        (wt / "uncommitted.txt").write_bytes(b"x")
        _age(wt, 30)
        self.assertEqual(self.candidates(), [])

    def test_the_age_scan_stops_at_the_first_fresh_file(self):
        d = _target(Path(self.home) / "work", days=30)
        for i in range(50):
            sub = d / ("d%d" % i)
            sub.mkdir()
            (sub / "f").write_bytes(b"x")
        fresh = d / "CACHEDIR.TAG"
        os.utime(fresh, (NOW, NOW))
        with mock.patch.object(sb.os, "lstat", wraps=os.lstat) as lstat:
            self.assertEqual(_rows(self.home), [])
        self.assertLess(lstat.call_count, 10)


class TestExecution(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = _target(tmp.name)
        self.action = {"cls": sb.CLS, "kind": "delete", "path": str(self.dir), "bytes": 100}

    def test_the_rung_passes_the_fence(self):
        self.assertIn(sb.CLS, dg.RECLAIMABLE_CLASSES)

    def test_the_executor_deletes_a_candidate(self):
        with mock.patch("cli_target_purge._target_in_live_use", return_value=False):
            self.assertEqual(dg._perform_action(self.action, now=NOW), 100)
        self.assertFalse(self.dir.exists())

    def test_a_build_started_after_the_plan_refuses_the_delete(self):
        with mock.patch("cli_target_purge._target_in_live_use", return_value=True):
            self.assertEqual(dg._perform_action(self.action, now=NOW), -1)
        self.assertTrue(self.dir.exists())


class TestLadder(unittest.TestCase):
    def test_the_rung_is_in_the_pressure_ladder_only(self):
        with tempfile.TemporaryDirectory() as td:
            pressure = [n for n, _ in dg._default_planners(td, time.time())]
            prevention = [n for n, _ in dg._prevention_planners(td, time.time())]
        self.assertIn("stale-build", pressure)
        self.assertLess(pressure.index("stale-build"), pressure.index("journal"))
        self.assertNotIn("stale-build", prevention)

    def test_the_planner_turns_a_candidate_into_a_delete(self):
        with tempfile.TemporaryDirectory() as td:
            d = _target(Path(td) / "copy")
            with mock.patch("cli_live_snapshot.batch_checker", return_value=lambda p: False):
                rows = dg._plan_stale_build(td, NOW)
        self.assertEqual([(r["cls"], r["path"], r["kind"]) for r in rows],
                         [("stale-build", str(d), "delete")])


if __name__ == "__main__":
    unittest.main()
