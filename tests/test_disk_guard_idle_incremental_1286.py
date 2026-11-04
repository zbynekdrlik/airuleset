"""#1286 (dev1, 4.11.2026): a project that builds daily never ages out, so at critical
disk the guard must reclaim the regenerable incremental output of an idle Cargo target."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from watchdog import disk_guard as dg
from watchdog import disk_guard_stale_build as sb

NOW = 1_800_000_000.0
SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55\n"


def _set_age(path, seconds):
    t = NOW - seconds
    for root, _dirs, files in os.walk(path):
        for name in files:
            os.utime(os.path.join(root, name), (t, t))


def _target(parent, idle_s=3600, incremental=("debug/incremental",)):
    """A live project's target: deps written just now, incremental idle for ``idle_s``."""
    d = Path(parent) / "target"
    (d / "debug" / "deps").mkdir(parents=True)
    (d / "debug" / "deps" / "app").write_bytes(b"x" * 500)
    (d / "CACHEDIR.TAG").write_text(SIGNATURE)
    (d / ".rustc_info.json").write_text("{}")
    for rel in incremental:
        (d / rel / "app-1").mkdir(parents=True)
        (d / rel / "app-1" / "query-cache.bin").write_bytes(b"y" * 100)
        _set_age(d / rel, idle_s)
    return d


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def rows(self, live_fn=lambda p: False):
        return sb.discover_idle_incremental(home=self.home, now=NOW, live_fn=live_fn)

    def test_a_live_project_gives_only_its_incremental_dir(self):
        d = _target(Path(self.home) / "devel" / "dstack")
        rows = self.rows()
        self.assertEqual([(r["cls"], r["path"], r["bytes"], r["reason"]) for r in rows],
                         [("build-incremental", str(d / "debug" / "incremental"), 100, None)])

    def test_every_profile_and_cross_target_incremental_is_found(self):
        d = _target(Path(self.home) / "devel" / "app", incremental=(
            "debug/incremental", "covlike/debug/incremental",
            "x86_64-pc-windows-gnu/debug/incremental"))
        self.assertEqual(sorted(r["path"] for r in self.rows()), sorted([
            str(d / "debug" / "incremental"),
            str(d / "covlike" / "debug" / "incremental"),
            str(d / "x86_64-pc-windows-gnu" / "debug" / "incremental")]))

    def test_an_incremental_dir_written_in_the_last_minutes_is_left_alone(self):
        _target(Path(self.home) / "devel" / "app", idle_s=sb.INCREMENTAL_MIN_IDLE_S - 60)
        self.assertEqual(self.rows(), [])

    def test_a_target_in_use_is_kept_and_the_liveness_asks_about_the_whole_target(self):
        """Cargo holds target/debug/.cargo-lock open, never a file under incremental/."""
        d = _target(Path(self.home) / "devel" / "app")
        asked = []

        def live_fn(path):
            asked.append(path)
            return path == str(d)

        rows = self.rows(live_fn)
        self.assertEqual(asked, [str(d)])
        self.assertEqual([r["reason"] for r in rows], ["in live use"])

    def test_a_directory_without_cargos_own_marker_is_never_a_candidate(self):
        d = Path(self.home) / "devel" / "app" / "target" / "debug" / "incremental"
        d.mkdir(parents=True)
        (d / "mine.txt").write_text("x")
        _set_age(d, 3600)
        self.assertEqual(self.rows(), [])

    def test_a_symlinked_incremental_dir_is_never_followed(self):
        d = _target(Path(self.home) / "devel" / "app", incremental=())
        outside = Path(self.home) / "outside"
        outside.mkdir()
        (d / "debug" / "incremental").symlink_to(outside)
        self.assertEqual(self.rows(), [])


class TestActTimeRecheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = _target(Path(tmp.name) / "app")
        self.incremental = self.target / "debug" / "incremental"

    def _still(self, live=False):
        with mock.patch("cli_target_purge._target_in_live_use", return_value=live) as use:
            result = sb.still_idle_incremental(str(self.incremental), NOW)
        return result, use

    def test_an_idle_dir_passes_and_the_check_covers_the_whole_target(self):
        result, use = self._still()
        self.assertTrue(result)
        use.assert_called_once_with(str(self.target))

    def test_a_build_started_since_the_plan_refuses(self):
        self.assertFalse(self._still(live=True)[0])

    def test_a_write_since_the_plan_refuses(self):
        _set_age(self.incremental, 10)
        self.assertFalse(self._still()[0])

    def test_the_executor_refuses_without_deleting(self):
        action = {"cls": "build-incremental", "path": str(self.incremental),
                  "bytes": 100, "kind": "delete"}
        with mock.patch("cli_target_purge._target_in_live_use", return_value=True):
            self.assertEqual(dg._perform_action(action, now=NOW), -1)
        self.assertTrue(self.incremental.is_dir())

    def test_the_executor_deletes_only_the_incremental_dir(self):
        action = {"cls": "build-incremental", "path": str(self.incremental),
                  "bytes": 100, "kind": "delete"}
        with mock.patch("cli_target_purge._target_in_live_use", return_value=False):
            self.assertEqual(dg._perform_action(action, now=NOW), 100)
        self.assertFalse(self.incremental.exists())
        self.assertTrue((self.target / "debug" / "deps" / "app").is_file())


class TestLadder(unittest.TestCase):
    def _plan(self, level):
        with tempfile.TemporaryDirectory() as td:
            d = _target(Path(td) / "app")
            with mock.patch("cli_live_snapshot.batch_checker", return_value=lambda p: False):
                return d, dg._plan_idle_incremental(td, NOW, level=level)

    def test_the_rung_runs_after_the_stale_build_rung_and_not_in_prevention(self):
        with tempfile.TemporaryDirectory() as td:
            pressure = [n for n, _ in dg._default_planners(td, time.time())]
            prevention = [n for n, _ in dg._prevention_planners(td, time.time())]
        self.assertLess(pressure.index("stale-build"), pressure.index("build-incremental"))
        self.assertLess(pressure.index("build-incremental"), pressure.index("journal"))
        self.assertNotIn("build-incremental", prevention)

    def test_the_class_is_reclaimable(self):
        self.assertIn("build-incremental", dg.RECLAIMABLE_CLASSES)

    def test_critical_level_plans_a_delete(self):
        d, rows = self._plan("critical")
        self.assertEqual([(r["path"], r["kind"]) for r in rows],
                         [(str(d / "debug" / "incremental"), "delete")])

    def test_below_critical_plans_nothing(self):
        for level in ("drain", "notice", None):
            self.assertEqual(self._plan(level)[1], [])

    def test_default_planners_hand_the_level_to_the_rung(self):
        with tempfile.TemporaryDirectory() as td:
            d = _target(Path(td) / "app")
            planners = dict(dg._default_planners(td, NOW, level="critical"))
            with mock.patch("cli_live_snapshot.batch_checker", return_value=lambda p: False):
                rows = planners["build-incremental"]()
        self.assertEqual([r["path"] for r in rows], [str(d / "debug" / "incremental")])


class TestCargoMutantsLeftover(unittest.TestCase):
    def test_a_killed_cargo_mutants_run_ages_out_of_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "cargo-mutants-app-abc.tmp"
            d.mkdir()
            (d / "f").write_bytes(b"x" * 10)
            old = time.time() - 2 * 86400
            os.utime(d / "f", (old, old))
            rows = dg.discover_stale_tmp_test_dirs(tmp_dir=td, min_age_days=1)
        self.assertEqual([(r["path"], r["reason"]) for r in rows], [(str(d), None)])


if __name__ == "__main__":
    unittest.main()
