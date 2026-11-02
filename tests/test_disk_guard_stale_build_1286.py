"""#1286: build output outside an agent worktree must age out under disk pressure."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from watchdog import disk_guard as dg
from watchdog import disk_guard_stale_build as sb

DAY = 86400
NOW = 1_800_000_000.0


def _age(path, days):
    t = NOW - days * DAY
    for root, dirs, files in os.walk(path):
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

    def test_a_tauri_target_two_levels_down_is_found(self):
        d = _target(Path(self.home) / "copy" / "src-tauri")
        self.assertEqual(self.candidates(), [str(d)])

    def test_a_target_under_devel_is_found(self):
        d = _target(Path(self.home) / "devel" / "app" / "repo")
        self.assertEqual(self.candidates(), [str(d)])

    def test_a_directory_named_target_without_the_cargo_tag_stays(self):
        _target(Path(self.home) / "photos", tag=False)
        self.assertEqual(self.candidates(), [])

    def test_a_recent_target_is_kept_with_a_reason(self):
        d = _target(Path(self.home) / "work", days=3)
        rows = _rows(self.home)
        self.assertEqual([r["path"] for r in rows], [str(d)])
        self.assertIsNotNone(rows[0]["reason"])

    def test_one_fresh_file_in_an_old_target_keeps_all_of_it(self):
        d = _target(Path(self.home) / "work", days=30)
        fresh = d / "debug" / "new"
        fresh.write_bytes(b"y")
        os.utime(fresh, (NOW - DAY, NOW - DAY))
        self.assertEqual(self.candidates(), [])

    def test_a_target_in_live_use_is_kept(self):
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

    def test_gk_scratch_children_age_out_one_by_one(self):
        root = Path(self.home) / "gk-scratch"
        old = root / "apk-1-wt"
        new = root / "apk-2-wt"
        old.mkdir(parents=True)
        new.mkdir()
        (old / "a").write_bytes(b"x")
        (new / "b").write_bytes(b"x")
        _age(old, 30)
        _age(new, 2)
        self.assertEqual(self.candidates(), [str(old)])

    def test_a_home_with_nothing_gives_no_rows(self):
        self.assertEqual(_rows(self.home), [])


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
            os.utime(d, (NOW, NOW))
            rows = dg._plan_stale_build(td, NOW)
        self.assertEqual([(r["cls"], r["path"], r["kind"]) for r in rows],
                         [("stale-build", str(d), "delete")])


if __name__ == "__main__":
    unittest.main()
