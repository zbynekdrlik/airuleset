"""#1286 (dev1, 4.11.2026): a project that builds daily never ages out, so at critical
disk the guard must reclaim the regenerable incremental output of an idle Cargo target."""
import fcntl
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from watchdog import disk_guard as dg
from watchdog import disk_guard_quota as dq
from watchdog import disk_guard_stale_build as sb

NOW = 1_800_000_000.0
SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55\n"


def _set_age(path, seconds):
    t = NOW - seconds
    for root, _dirs, files in os.walk(path):
        for name in files:
            os.utime(os.path.join(root, name), (t, t))


def _target(parent, idle_s=2 * 3600, incremental=("debug/incremental",)):
    """A live project's target: deps written just now, incremental idle for ``idle_s``."""
    d = Path(parent) / "target"
    (d / "debug" / "deps").mkdir(parents=True)
    (d / "debug" / "deps" / "app").write_bytes(b"x" * 500)
    (d / "CACHEDIR.TAG").write_text(SIGNATURE)
    (d / ".rustc_info.json").write_text("{}")
    for rel in incremental:
        (d / rel / "app-1").mkdir(parents=True)
        (d / rel / "app-1" / "query-cache.bin").write_bytes(b"y" * 100)
        (d / rel).parent.joinpath(".cargo-lock").write_text("")
        (d / rel).parent.joinpath(".cargo-build-lock").write_text("")
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
        _set_age(d, 2 * 3600)
        self.assertEqual(self.rows(), [])

    def test_a_dir_named_incremental_without_a_cargo_lock_beside_it_stays(self):
        d = _target(Path(self.home) / "devel" / "app", incremental=())
        other = d / "test-output" / "incremental"
        other.mkdir(parents=True)
        (other / "keep.txt").write_text("x")
        _set_age(other, 2 * 3600)
        self.assertEqual(self.rows(), [])

    def test_a_symlink_inside_target_never_reaches_outside_it(self):
        d = _target(Path(self.home) / "devel" / "app", incremental=())
        outside = Path(self.home) / "data"
        (outside / "incremental").mkdir(parents=True)
        (outside / ".cargo-lock").write_text("")
        (outside / "incremental" / "user.txt").write_text("x")
        _set_age(outside, 2 * 3600)
        (d / "linked").symlink_to(outside)
        self.assertEqual(self.rows(), [])

    def test_one_pass_takes_only_the_longest_idle_target(self):
        """The ladder rechecks the disk between rungs, never between a rung's rows."""
        young = _target(Path(self.home) / "devel" / "young", idle_s=2 * 3600)
        old = _target(Path(self.home) / "devel" / "old", idle_s=9 * 3600)
        self.assertEqual([r["path"] for r in self.rows()], [str(old / "debug" / "incremental")])
        self.assertNotEqual(old, young)

    def test_a_target_in_use_gives_one_row_and_no_walk(self):
        d = _target(Path(self.home) / "devel" / "app", incremental=(
            "debug/incremental", "release/incremental"))
        with mock.patch.object(sb, "_old_size", side_effect=AssertionError("walked")):
            rows = self.rows(lambda p: True)
        self.assertEqual([(r["path"], r["reason"]) for r in rows], [(str(d), "in live use")])

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
            with sb.reclaim_guard("build-incremental", str(self.incremental), NOW) as ok:
                pass
        return ok, use

    def test_a_target_that_lost_its_cargo_marker_refuses(self):
        (self.target / ".rustc_info.json").unlink()
        self.assertFalse(self._still()[0])

    def test_a_path_that_became_a_symlink_route_refuses(self):
        real = self.target.parent / "real"
        (self.target / "debug").rename(real)
        (self.target / "debug").symlink_to(real)
        self.assertFalse(self._still()[0])

    def test_a_build_holding_a_cargo_lock_refuses(self):
        fd = os.open(self.target / "debug" / ".cargo-build-lock", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertFalse(self._still()[0])

    def test_a_build_cannot_start_while_the_delete_runs(self):
        lock = self.target / "debug" / ".cargo-lock"
        with mock.patch("cli_target_purge._target_in_live_use", return_value=False):
            with sb.reclaim_guard("build-incremental", str(self.incremental), NOW) as ok:
                self.assertTrue(ok)
                fd = os.open(lock, os.O_RDONLY)
                self.addCleanup(os.close, fd)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # released afterwards

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
    def test_the_quota_pass_keeps_the_rung(self):
        self.assertNotIn("build-incremental", dq.QUOTA_PASS_EXCLUDED_RUNGS)

    def test_a_small_root_at_its_lower_critical_reaches_the_rung(self):
        seen = {}

        def _planners(home, now, **kw):
            seen.update(kw)
            return [("noop", lambda: [])]

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(dg, "_default_planners", side_effect=_planners):
            dg.run_disk_guard(
                now=1000.0, home=td, dry_run=True,
                statvfs_fn=lambda _m: types.SimpleNamespace(
                    f_blocks=1000, f_bfree=130, f_bavail=130, f_frsize=4096,
                    f_files=100000, f_ffree=50000),
                dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000,
                box_class_fn=lambda: "workstation",
                scratch_discover_fn=lambda *_a: [],
                top_consumers_fn=lambda *a, **kw: [])
        self.assertEqual(seen.get("level"), "critical")

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


class TestRefusalLog(unittest.TestCase):
    def test_a_refused_build_row_names_the_build_not_a_session(self):
        act = {"cls": "build-incremental", "path": "/x/target/debug/incremental",
               "bytes": 2 << 20, "kind": "delete"}
        status = {"worst_pct": 91, "level": "critical"}
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log"
            dg.execute_drain(status, td, [("build-incremental", lambda: [act])],
                             lambda: 91, lambda a: -1, geteuid_fn=lambda: 1000,
                             log_path=str(log), now=NOW, target_pct=75)
            text = log.read_text()
        self.assertIn("build started or target in use since plan (#1286)", text)
        self.assertNotIn("session live", text)


class TestTmpTestLiveness(unittest.TestCase):
    def test_a_live_dir_is_kept_at_plan_time(self):
        row = {"cls": "tmp-test", "path": "/tmp/cargo-mutants-a.tmp", "bytes": 5, "reason": None}
        with mock.patch.object(dg, "discover_stale_tmp_test_dirs", return_value=[row]), \
                mock.patch("cli_live_snapshot.batch_checker", return_value=lambda p: True):
            rows = dg._plan_tmp_test("/h", NOW)
        self.assertEqual([r["kind"] for r in rows], ["skip"])

    def test_an_idle_dir_is_planned(self):
        row = {"cls": "tmp-test", "path": "/tmp/cargo-mutants-a.tmp", "bytes": 5, "reason": None}
        with mock.patch.object(dg, "discover_stale_tmp_test_dirs", return_value=[row]), \
                mock.patch("cli_live_snapshot.batch_checker", return_value=lambda p: False):
            rows = dg._plan_tmp_test("/h", NOW)
        self.assertEqual([r["kind"] for r in rows], ["delete"])

    def test_the_executor_refuses_a_dir_that_became_live(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "cargo-mutants-a.tmp"
            d.mkdir()
            action = {"cls": "tmp-test", "path": str(d), "bytes": 5, "kind": "delete"}
            with mock.patch("cli_target_purge._target_in_live_use", return_value=True):
                self.assertEqual(dg._perform_action(action, now=NOW), -1)
            self.assertTrue(d.is_dir())


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
