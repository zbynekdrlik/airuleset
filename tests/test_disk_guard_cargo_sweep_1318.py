"""#1318 (dev1, 13.11.2026): a Cargo target built daily keeps every old-hash unit in
deps/, build/ and .fingerprint/ forever. At critical disk the guard removes the units
whose fingerprint was not used for 3 days (the cargo-sweep 0.8.0 ``--time`` rule)."""
import fcntl
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from watchdog import disk_guard as dg
from watchdog import disk_guard_cargo_sweep as cs
from watchdog import disk_guard_quota as dq

NOW = 1_800_000_000.0
DAY = 86400
SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55\n"
OLD = "0123456789abcdef"       # a hash last used 10 days ago
NEW = "fedcba9876543210"       # a hash used today
ORPHAN = "00000000aaaaaaaa"    # files with no fingerprint dir at all


def _age(path, seconds):
    t = NOW - seconds
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                os.utime(os.path.join(root, name), (t, t))
    os.utime(path, (t, t))


def _unit(profile, name, h, age_s, size=100):
    """One unit as Cargo writes it: fingerprint dir, deps files, a build-script dir."""
    fp = profile / ".fingerprint" / ("%s-%s" % (name, h))
    fp.mkdir(parents=True)
    (fp / ("lib-" + name)).write_text(h)
    (fp / ("lib-%s.json" % name)).write_text("{}")
    deps = profile / "deps"
    deps.mkdir(exist_ok=True)
    for fname in ("lib%s-%s.rlib" % (name, h), "lib%s-%s.rmeta" % (name, h),
                  "%s-%s.d" % (name, h)):
        (deps / fname).write_bytes(b"x" * size)
        _age(deps / fname, age_s)
    out = profile / "build" / ("%s-%s" % (name, h)) / "out"
    out.mkdir(parents=True)
    (out / "gen.rs").write_bytes(b"y" * size)
    _age(profile / "build" / ("%s-%s" % (name, h)), age_s)
    _age(fp, age_s)


def _target(parent, profiles=("debug",)):
    d = Path(parent) / "target"
    d.mkdir(parents=True)
    (d / "CACHEDIR.TAG").write_text(SIGNATURE)
    (d / ".rustc_info.json").write_text("{}")
    for rel in profiles:
        p = d / rel
        p.mkdir(parents=True)
        (p / ".cargo-lock").write_text("")
        _unit(p, "serde", OLD, 10 * DAY)
        _unit(p, "app", NEW, 3600)
        (p / "deps" / ("libfoo-%s.rlib" % ORPHAN)).write_bytes(b"o" * 50)
        _age(p / "deps" / ("libfoo-%s.rlib" % ORPHAN), 30 * DAY)
        inc = p / "incremental" / ("serde-%s" % OLD)
        inc.mkdir(parents=True)
        (inc / "query-cache.bin").write_bytes(b"i" * 70)
        _age(inc, 30 * DAY)
    return d


def _old_entries(profile):
    return sorted(str(p.relative_to(profile)) for p in profile.rglob("*")
                  if OLD in p.name and "incremental" not in p.parts)


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def rows(self, live_fn=lambda p: False):
        return cs.discover_stale_units(home=self.home, now=NOW, live_fn=live_fn)

    def test_a_daily_built_target_gives_one_row_with_only_the_stale_units_bytes(self):
        d = _target(Path(self.home) / "devel" / "dstack")
        rows = self.rows()
        # three deps files + one build file of 100 B each; the fingerprint files are tiny
        self.assertEqual([(r["cls"], r["path"], r["reason"]) for r in rows],
                         [("stale-target-artifacts", str(d / "debug"), None)])
        self.assertGreaterEqual(rows[0]["bytes"], 400)
        self.assertLess(rows[0]["bytes"], 500)
        self.assertIn("1 unit", rows[0]["why"])

    def test_a_target_with_nothing_stale_gives_no_row(self):
        d = _target(Path(self.home) / "devel" / "app")
        _age(d / "debug" / ".fingerprint" / ("serde-" + OLD), 3600)
        self.assertEqual(self.rows(), [])

    def test_cross_target_profiles_are_found(self):
        d = _target(Path(self.home) / "devel" / "app",
                    profiles=("debug", "x86_64-pc-windows-gnu/release"))
        self.assertEqual(sorted(r["path"] for r in self.rows()),
                         sorted([str(d / "debug"), str(d / "x86_64-pc-windows-gnu" / "release")]))

    def test_a_target_in_use_is_kept_and_not_walked(self):
        d = _target(Path(self.home) / "devel" / "app")
        asked = []

        def live_fn(path):
            asked.append(path)
            return True

        with mock.patch.object(cs, "_unit_ages", side_effect=AssertionError("walked")):
            rows = self.rows(live_fn)
        self.assertEqual(asked, [str(d)])
        self.assertEqual([(r["path"], r["reason"]) for r in rows], [(str(d), "in live use")])

    def test_a_tree_without_cargos_own_marker_is_never_a_candidate(self):
        d = _target(Path(self.home) / "devel" / "app")
        (d / ".rustc_info.json").unlink()
        self.assertEqual(self.rows(), [])

    def test_a_profile_without_a_cargo_lock_is_never_a_candidate(self):
        d = _target(Path(self.home) / "devel" / "app")
        (d / "debug" / ".cargo-lock").unlink()
        self.assertEqual(self.rows(), [])

    def test_largest_profile_first(self):
        small = _target(Path(self.home) / "devel" / "small")
        big = _target(Path(self.home) / "devel" / "big")
        (big / "debug" / "deps" / ("serde-%s.big" % OLD)).write_bytes(b"z" * 5000)
        self.assertEqual([r["path"] for r in self.rows()],
                         [str(big / "debug"), str(small / "debug")])


class TestSweep(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = _target(Path(tmp.name) / "app")
        self.profile = self.target / "debug"

    def sweep(self, live=False):
        with mock.patch("cli_target_purge._target_in_live_use", return_value=live) as use:
            freed = cs.sweep_profile(str(self.profile), NOW)
        return freed, use

    def test_the_stale_unit_goes_from_deps_build_and_fingerprint_together(self):
        self.assertNotEqual(_old_entries(self.profile), [])
        freed, use = self.sweep()
        self.assertEqual(_old_entries(self.profile), [])
        self.assertGreaterEqual(freed, 400)
        use.assert_called_once_with(str(self.target))

    def test_fresh_units_orphans_and_incremental_stay(self):
        self.sweep()
        self.assertTrue((self.profile / "deps" / ("libapp-%s.rlib" % NEW)).is_file())
        self.assertTrue((self.profile / ".fingerprint" / ("app-" + NEW)).is_dir())
        self.assertTrue((self.profile / "build" / ("app-" + NEW)).is_dir())
        self.assertTrue((self.profile / "deps" / ("libfoo-%s.rlib" % ORPHAN)).is_file())
        self.assertTrue((self.profile / "incremental" / ("serde-" + OLD)).is_dir())

    def test_a_unit_used_since_the_plan_stays(self):
        """Cargo read the fingerprint since the plan: its atime is fresh now."""
        fp = self.profile / ".fingerprint" / ("serde-" + OLD) / "lib-serde"
        old = NOW - 10 * DAY
        os.utime(fp, (NOW - 60, old))
        self.sweep()
        self.assertTrue((self.profile / "deps" / ("libserde-%s.rlib" % OLD)).is_file())

    def test_a_hash_with_one_fresh_fingerprint_dir_is_kept(self):
        twin = self.profile / ".fingerprint" / ("serde-build-" + OLD)
        twin.mkdir()
        (twin / "run").write_text("x")
        _age(twin, 60)
        self.sweep()
        self.assertTrue((self.profile / "deps" / ("libserde-%s.rlib" % OLD)).is_file())

    def test_a_hash_with_a_fresh_main_dir_and_a_stale_twin_is_kept(self):
        """The mirror order of the twin test: a fresh dir must win whichever is read last."""
        _age(self.profile / ".fingerprint" / ("serde-" + OLD), 60)
        twin = self.profile / ".fingerprint" / ("serde-build-" + OLD)
        twin.mkdir()
        (twin / "run").write_text("x")
        _age(twin, 10 * DAY)
        self.sweep()
        self.assertTrue((self.profile / "deps" / ("libserde-%s.rlib" % OLD)).is_file())
        self.assertTrue(twin.is_dir())

    def test_a_symlinked_deps_dir_is_never_entered(self):
        outside = self.target.parent / "outside-deps"
        (self.profile / "deps").rename(outside)
        (self.profile / "deps").symlink_to(outside)
        self.sweep()
        self.assertTrue((outside / ("libserde-%s.rlib" % OLD)).is_file())

    def test_a_profile_reached_through_a_symlinked_triple_refuses(self):
        real = self.target.parent / "real-triple"
        (real / "debug").mkdir(parents=True)
        (real / "debug" / ".cargo-lock").write_text("")
        _unit(real / "debug", "serde", OLD, 10 * DAY)
        (self.target / "x86_64-pc-windows-gnu").symlink_to(real)
        with mock.patch("cli_target_purge._target_in_live_use", return_value=False):
            freed = cs.sweep_profile(str(self.target / "x86_64-pc-windows-gnu" / "debug"), NOW)
        self.assertEqual(freed, -1)
        self.assertTrue((real / "debug" / "deps" / ("libserde-%s.rlib" % OLD)).is_file())

    def test_a_unit_whose_files_cannot_go_keeps_its_fingerprint_for_the_next_pass(self):
        deps = self.profile / "deps"
        deps.chmod(0o555)
        self.addCleanup(deps.chmod, 0o755)
        self.sweep()
        self.assertTrue((deps / ("libserde-%s.rlib" % OLD)).is_file())
        self.assertTrue((self.profile / ".fingerprint" / ("serde-" + OLD)).is_dir())

    def test_an_empty_fingerprint_dir_is_aged_by_its_mtime(self):
        """Listing the dir bumps its own atime, so the atime must not keep it alive."""
        h = "1111111111111111"
        fp = self.profile / ".fingerprint" / ("empty-" + h)
        fp.mkdir()
        os.utime(fp, (NOW - 60, NOW - 10 * DAY))
        (self.profile / "deps" / ("libempty-%s.rlib" % h)).write_bytes(b"e" * 10)
        self.sweep()
        self.assertFalse(os.path.lexists(self.profile / "deps" / ("libempty-%s.rlib" % h)))
        self.assertFalse(os.path.lexists(fp))

    def test_a_target_that_became_live_refuses_without_deleting(self):
        freed, _use = self.sweep(live=True)
        self.assertEqual(freed, -1)
        self.assertNotEqual(_old_entries(self.profile), [])

    def test_a_build_holding_the_lock_refuses(self):
        fd = os.open(self.profile / ".cargo-lock", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertEqual(self.sweep()[0], -1)
        self.assertNotEqual(_old_entries(self.profile), [])

    def test_the_lock_is_held_during_the_delete_and_released_after(self):
        lock = self.profile / ".cargo-lock"
        seen = []
        real_unlink = os.unlink

        def probe(path, *a, **kw):
            if not seen:
                fd = os.open(lock, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen.append("free")
                except BlockingIOError:
                    seen.append("held")
                finally:
                    os.close(fd)
            return real_unlink(path, *a, **kw)

        with mock.patch.object(cs.os, "unlink", side_effect=probe):
            self.sweep()
        self.assertEqual(seen, ["held"])
        fd = os.open(lock, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_a_symlink_entry_is_removed_but_never_followed(self):
        outside = self.target.parent / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("x")
        link = self.profile / "build" / ("serde-link-" + OLD)
        link.symlink_to(outside)
        self.sweep()
        self.assertTrue((outside / "keep.txt").is_file())
        self.assertFalse(os.path.lexists(link))

    def test_a_hard_linked_binary_is_not_counted_as_freed(self):
        uplift = self.profile / "serde-bin"
        os.link(self.profile / "deps" / ("libserde-%s.rlib" % OLD), uplift)
        freed, _use = self.sweep()
        self.assertTrue(uplift.is_file())
        self.assertLess(freed, 400)

    def test_the_executor_routes_the_class_to_the_sweep(self):
        action = {"cls": "stale-target-artifacts", "path": str(self.profile),
                  "bytes": 999, "kind": "delete"}
        with mock.patch("cli_target_purge._target_in_live_use", return_value=False):
            freed = dg._perform_action(action, now=NOW)
        self.assertEqual(_old_entries(self.profile), [])
        self.assertTrue(self.profile.is_dir())
        self.assertGreaterEqual(freed, 400)

    def test_the_executor_refuses_a_live_target(self):
        action = {"cls": "stale-target-artifacts", "path": str(self.profile),
                  "bytes": 999, "kind": "delete"}
        with mock.patch("cli_target_purge._target_in_live_use", return_value=True):
            self.assertEqual(dg._perform_action(action, now=NOW), -1)
        self.assertNotEqual(_old_entries(self.profile), [])


class TestLadder(unittest.TestCase):
    def test_the_rung_sits_between_stale_build_and_build_incremental(self):
        with tempfile.TemporaryDirectory() as td:
            pressure = [n for n, _ in dg._default_planners(td, time.time())]
            prevention = [n for n, _ in dg._prevention_planners(td, time.time())]
        self.assertLess(pressure.index("stale-build"), pressure.index("stale-target-artifacts"))
        self.assertLess(pressure.index("stale-target-artifacts"), pressure.index("build-incremental"))
        self.assertNotIn("stale-target-artifacts", prevention)
        self.assertNotIn("stale-target-artifacts", dq.QUOTA_PASS_EXCLUDED_RUNGS)

    def test_the_class_is_reclaimable(self):
        self.assertIn("stale-target-artifacts", dg.RECLAIMABLE_CLASSES)

    def _plan(self, level):
        with tempfile.TemporaryDirectory() as td:
            d = _target(Path(td) / "app")
            planners = dict(dg._default_planners(td, NOW, level=level))
            with mock.patch("cli_live_snapshot.batch_checker", return_value=lambda p: False):
                return d, planners["stale-target-artifacts"]()

    def test_drain_and_critical_levels_plan_a_delete(self):
        # owner 13.11.: the disk must never reach the edge again, so unused units
        # (no rebuild cost) go from the 80 % drain level, not only at 90 %.
        for level in ("drain", "critical"):
            d, rows = self._plan(level)
            self.assertEqual([(r["path"], r["kind"]) for r in rows],
                             [(str(d / "debug"), "delete")], level)

    def test_below_drain_plans_nothing(self):
        for level in ("notice", "ok", None):
            self.assertEqual(self._plan(level)[1], [])

    def test_an_acted_sweep_is_journalled_and_a_refusal_names_the_build(self):
        acts = [{"cls": "stale-target-artifacts", "path": "/x/target/debug",
                 "bytes": 2 << 20, "kind": "delete"},
                {"cls": "stale-target-artifacts", "path": "/y/target/debug",
                 "bytes": 5 << 20, "kind": "delete"}]
        status = {"worst_pct": 91, "level": "critical"}
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log"
            dg.execute_drain(status, td, [("stale-target-artifacts", lambda: acts)],
                             lambda: 91, lambda a: -1 if a["path"].startswith("/x") else 3 << 20,
                             geteuid_fn=lambda: 1000, log_path=str(log), now=NOW,
                             target_pct=75)
            text = log.read_text()
            journal = [json.loads(line) for f in Path(td).rglob("deletions-*.jsonl")
                       for line in f.read_text().splitlines()]
        self.assertIn("build started or target in use since plan (#1286)", text)
        # the journal records what the sweep really freed, not the plan's estimate
        self.assertEqual([(j.get("rung"), j.get("path"), j.get("bytes")) for j in journal],
                         [("stale-target-artifacts", "/y/target/debug", 3 << 20)])


if __name__ == "__main__":
    unittest.main()
