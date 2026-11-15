"""#1318 size budget (owner 15.11.2026): every cleanup rule judged a Cargo target by
AGE, so a project that builds every day never got capped (dev1: 494.5 G of 761 G were
``target/`` dirs). A per-target SIZE cap now removes whole stale units, oldest first,
on every disk-guard poll, under Cargo's build lock, without delete-rebuild loops."""
import fcntl
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from watchdog import disk_guard as dg
from watchdog import disk_guard_target_cap as tc

NOW = time.time()
DAY = 86400
SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55\n"
IDLE = (frozenset(), frozenset())
H = ["%016x" % (0xa000 + i) for i in range(20)]


def _age(path, seconds):
    t = NOW - seconds
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            os.utime(os.path.join(root, name), (t, t))
    os.utime(path, (t, t))


def _target(home, name="app"):
    d = Path(home) / "devel" / name / "target"
    p = d / "debug"
    p.mkdir(parents=True)
    (d / "CACHEDIR.TAG").write_text(SIGNATURE)
    (d / ".rustc_info.json").write_text("{}")
    (p / ".cargo-lock").write_text("")
    return d


def _old_unit(profile, pkg, h, age_s, size=1000, kind="lib", src=7):
    """Pre-1.100 layout: .fingerprint/<pkg>-<h>, deps/lib<pkg>-<h>.rlib."""
    fp = profile / ".fingerprint" / ("%s-%s" % (pkg, h))
    fp.mkdir(parents=True)
    (fp / ("%s-%s" % (kind, pkg))).write_text(h)
    (fp / ("%s-%s.json" % (kind, pkg))).write_text(json.dumps({"path": src}))
    (fp / ("dep-%s-%s" % (kind, pkg))).write_text("")
    deps = profile / "deps"
    deps.mkdir(exist_ok=True)
    rlib = deps / ("lib%s-%s.rlib" % (pkg, h))
    rlib.write_bytes(b"x" * size)
    _age(rlib, age_s)
    _age(fp, age_s)


def _new_unit(profile, pkg, h, age_s, size=1000, kind="lib"):
    """Cargo 1.100 layout: build/<pkg>/<h>/{fingerprint,out}."""
    u = profile / "build" / pkg / h
    (u / "fingerprint").mkdir(parents=True)
    (u / "fingerprint" / ("%s-%s" % (kind, pkg))).write_text(h)
    (u / "fingerprint" / "invoked.timestamp").write_text("")
    (u / "out").mkdir()
    (u / "out" / ("lib%s.rlib" % pkg)).write_bytes(b"n" * size)
    _age(u, age_s)


def _inc(profile, crate, ident, age_s, size=1000):
    d = profile / "incremental" / ("%s-%s" % (crate, ident))
    s = d / "s-ho6vk0ecw7-1m39yxn-abc"
    s.mkdir(parents=True)
    (s / "query-cache.bin").write_bytes(b"i" * size)
    _age(d, age_s)
    return d


class _Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.t = _target(self.home)
        self.p = self.t / "debug"
        snap = mock.patch("watchdog.disk_guard_cargo_sweep._snapshot", return_value=IDLE)
        snap.start()
        self.addCleanup(snap.stop)
        self.lines = []

    def note(self, *a):
        self.lines.append(a)

    def run_target(self, cap, rec=None, dry_run=False):
        rec = {} if rec is None else rec
        return tc.enforce_target(str(self.t), cap, NOW, rec, self.note, dry_run=dry_run), rec


class TestCapRule(_Home):
    def test_under_the_cap_nothing_is_removed(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        row, _ = self.run_target(cap=10 ** 6)
        self.assertEqual(row["state"], "ok")
        self.assertEqual(len(list((self.p / "deps").iterdir())), 3)

    def test_over_the_cap_oldest_units_go_first_down_to_80_percent(self):
        for i in range(10):              # 10 serde builds, 1000 B each; oldest = H[0]
            _old_unit(self.p, "serde", H[i], (20 - i) * DAY)
        size = tc.target_size(str(self.t))
        cap = size - 1500                # goal 0.8*cap: must drop ~3500 B -> 4 units
        row, rec = self.run_target(cap)
        self.assertEqual(row["state"], "reduced")
        self.assertLessEqual(row["bytes"], cap * tc.TARGET_FRACTION)
        gone = [h for h in H[:10] if not (self.p / "deps" / ("libserde-%s.rlib" % h)).exists()]
        self.assertEqual(gone, H[:len(gone)])             # strictly oldest first
        self.assertGreaterEqual(len(gone), 3)
        self.assertFalse((self.p / ".fingerprint" / ("serde-" + H[0])).exists())
        self.assertIn("debug/.fingerprint/serde-" + H[0], rec["removed"])

    def test_the_newest_unit_of_each_group_and_anything_used_today_stay(self):
        """Owner 15.11.: the cap must never disturb development."""
        _old_unit(self.p, "serde", H[0], 10 * DAY)           # newest of its group: stays
        _old_unit(self.p, "app", H[1], 10 * DAY, kind="test-lib")
        _old_unit(self.p, "app", H[2], 9 * DAY, kind="lib")   # newest lib of app
        _old_unit(self.p, "tokio", H[3], 47 * 3600)          # relatime: 47 h may be 23 h
        _old_unit(self.p, "tokio", H[4], 60)
        _inc(self.p, "tokio", "old1", 40 * 3600)
        _inc(self.p, "tokio", "new2", 60)
        row, _ = self.run_target(cap=10)
        for h in H[:5]:
            self.assertTrue(any(h in f.name for f in (self.p / "deps").iterdir()), h)
        self.assertEqual(len(list((self.p / "incremental").iterdir())), 2)
        self.assertEqual(row["state"], "over")
        self.assertIn("used within 48 h", row["why"])

    def test_cargo_1100_layout_units_go_whole(self):
        for i in range(4):
            _new_unit(self.p, "serde", H[i], (10 - i) * DAY)
        row, _ = self.run_target(cap=1)
        left = sorted(x.name for x in (self.p / "build" / "serde").iterdir())
        self.assertEqual(left, [H[3]])
        self.assertEqual(row["state"], "over")

    def test_incremental_dirs_go_oldest_first_and_the_newest_per_crate_stays(self):
        a = _inc(self.p, "app", "aaaa1", 9 * DAY)
        b = _inc(self.p, "app", "bbbb2", 5 * DAY)
        c = _inc(self.p, "app", "cccc3", 2 * DAY)
        self.run_target(cap=1)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())
        self.assertTrue(c.exists())

    def test_a_unit_a_process_uses_stays_whole(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        rlib = str(self.p / "deps" / ("libserde-%s.rlib" % H[0]))
        with mock.patch("watchdog.disk_guard_cargo_sweep._snapshot",
                        return_value=(frozenset({rlib}), frozenset())):
            self.run_target(cap=1)
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())
        self.assertTrue((self.p / ".fingerprint" / ("serde-" + H[0])).exists())
        self.assertFalse((self.p / "deps" / ("libserde-%s.rlib" % H[1])).exists())

    def test_a_running_build_holding_the_lock_blocks_every_delete(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        fd = os.open(self.p / ".cargo-lock", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH)
        row, _ = self.run_target(cap=1)
        self.assertEqual(len(list((self.p / "deps").iterdir())), 3)
        self.assertEqual((row["state"], row["retry"]), ("reducing", True))
        self.assertEqual(self.lines, [])               # nothing logged for a build in flight

    def test_a_unit_used_between_plan_and_act_stays(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        real = tc._hold_build_locks

        def touch_then_lock(profile):
            os.utime(self.p / ".fingerprint" / ("serde-" + H[0]) / "lib-serde", None)
            return real(profile)
        with mock.patch.object(tc, "_hold_build_locks", side_effect=touch_then_lock):
            self.run_target(cap=1)
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())

    def test_dry_run_removes_nothing(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        row, rec = self.run_target(cap=1, dry_run=True)
        self.assertEqual(len(list((self.p / "deps").iterdir())), 3)
        self.assertGreater(row["plan_units"], 0)
        self.assertNotIn("removed", rec)


class TestReviewFixes(_Home):
    """The two adversarial reviews of 15.11. (build safety, thrash + cost)."""

    def test_the_fingerprint_goes_first_so_a_cut_never_leaves_a_fresh_unit(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        _old_unit(self.p, "serde", H[1], 9 * DAY)
        _new_unit(self.p, "log", H[2], 10 * DAY)
        _new_unit(self.p, "log", H[3], 9 * DAY)
        real = tc.cs._remove

        def only_fingerprints(path):
            if "fingerprint" not in path:
                raise OSError(5, "killed here")
            return real(path)
        with mock.patch.object(tc.cs, "_remove", side_effect=only_fingerprints):
            self.run_target(cap=1)
        self.assertFalse((self.p / ".fingerprint" / ("serde-" + H[0])).exists())
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())
        self.assertFalse((self.p / "build" / "log" / H[2] / "fingerprint").exists())
        self.assertTrue((self.p / "build" / "log" / H[2] / "out").exists())

    def test_two_versions_of_one_crate_keep_their_own_newest_unit(self):
        _old_unit(self.p, "syn", H[0], 10 * DAY, src=1)      # syn 1.x, its only unit
        _old_unit(self.p, "syn", H[1], 9 * DAY, src=2)       # syn 2.x, older build
        _old_unit(self.p, "syn", H[2], 8 * DAY, src=2)       # syn 2.x, newest
        self.run_target(cap=1)
        left = sorted(f.name for f in (self.p / "deps").iterdir())
        self.assertEqual(left, ["libsyn-%s.rlib" % H[0], "libsyn-%s.rlib" % H[2]])

    def test_reading_the_source_id_never_bumps_an_atime(self):
        _old_unit(self.p, "syn", H[0], 10 * DAY)
        js = self.p / ".fingerprint" / ("syn-" + H[0]) / "lib-syn.json"
        before = os.stat(js).st_atime
        tc.collect(str(self.t))
        self.assertEqual(os.stat(js).st_atime, before)

    def test_a_broken_state_file_heals_instead_of_failing_every_poll(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        _old_unit(self.p, "serde", H[1], 9 * DAY)
        sp = Path(tc._state_path(self.home))
        sp.parent.mkdir(parents=True, exist_ok=True)
        for bad in ('{"targets": []}', '{"targets": {"%s": {"removed": {"k": "x"}, '
                    '"measured": "y"}}}' % self.t, "[]", "{not json"):
            sp.write_text(bad)
            with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]):
                rows, _ = tc.enforce(self.home, NOW, cap=1 << 40)
            self.assertEqual([r["state"] for r in rows], ["ok"], bad)
            self.assertIsInstance(json.loads(sp.read_text())["targets"], dict)

    def test_a_refused_mount_is_a_daily_skip_note_never_over_the_cap(self):
        with mock.patch.object(tc.cs, "mount_refusal", return_value="noatime mount"), \
                mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]):
            rows, lines = tc.enforce(self.home, NOW, cap=1)
            _r, again = tc.enforce(self.home, NOW + 2 * 3600, cap=1)
        self.assertEqual([r["state"] for r in rows], ["refused"])
        self.assertEqual([a[0] for a in lines], ["CAP-SKIP"])
        self.assertEqual(again, [])

    def test_a_protected_key_stays_protected_while_it_exists(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        _old_unit(self.p, "serde", H[1], 9 * DAY)
        rec = {"protected": {"debug/.fingerprint/serde-" + H[0]: NOW - 30 * DAY}}
        self.run_target(cap=1, rec=rec)
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())
        self.assertEqual(rec["protected"]["debug/.fingerprint/serde-" + H[0]], NOW)

    def test_a_unit_found_in_use_is_kept_a_day_and_noted_once(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        rlib = str(self.p / "deps" / ("libserde-%s.rlib" % H[0]))
        rec = {}
        with mock.patch("watchdog.disk_guard_cargo_sweep._snapshot",
                        return_value=(frozenset({rlib}), frozenset())):
            self.run_target(cap=1, rec=rec)
            self.run_target(cap=1, rec=rec)
        self.assertEqual([a[0] for a in self.lines].count("CAP-KEEP"), 1)
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())

    def test_the_lock_is_held_in_short_slices(self):
        for i in range(6):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        calls = []
        real = tc._hold_build_locks

        def counting(profile):
            calls.append(profile)
            return real(profile)
        with mock.patch.object(tc, "HOLD_S", -1), \
                mock.patch.object(tc, "_hold_build_locks", side_effect=counting):
            self.run_target(cap=1)
        self.assertEqual(len(calls), 5)                # one unit per hold, 5 removed

    def test_an_unwritable_state_measures_nothing(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]), \
                mock.patch.object(tc, "save_state", side_effect=OSError(28, "full")), \
                mock.patch.object(tc, "target_size") as size:
            rows, lines = tc.enforce(self.home, NOW, cap=1)
        size.assert_not_called()
        self.assertEqual(([a[0] for a in lines], rows), (["CAP-SKIP"], []))

    def test_the_home_is_walked_once_an_hour_not_every_poll(self):
        with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]) as walk:
            tc.enforce(self.home, NOW, cap=1 << 40)
            tc.enforce(self.home, NOW + 60, cap=1 << 40)
            tc.enforce(self.home, NOW + 3600, cap=1 << 40)
        self.assertEqual(walk.call_count, 2)


class TestReviewRound2(_Home):
    """The re-review of 15.11.: no 🔴, five 🟡 and the 🔵 fixed here."""

    def test_a_superseded_versions_last_unit_goes_once_unused_for_two_weeks(self):
        _old_unit(self.p, "syn", H[0], 20 * DAY, src=1)      # syn 1.x, gone from Cargo.lock
        _old_unit(self.p, "syn", H[1], 3 * DAY, src=2)
        row, _ = self.run_target(cap=1)
        left = sorted(f.name for f in (self.p / "deps").iterdir())
        self.assertEqual(left, ["libsyn-%s.rlib" % H[1]])

    def test_a_unit_a_build_replaces_under_the_walk_is_skipped_not_fatal(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        a = _inc(self.p, "app", "aaaa1", 9 * DAY)
        _inc(self.p, "app", "bbbb2", 8 * DAY)
        real = tc.cs._crate_last_use

        def vanishing(path):
            if path == str(a):
                raise FileNotFoundError(2, "rotated by rustc", path)
            return real(path)
        with mock.patch.object(tc.cs, "_crate_last_use", side_effect=vanishing):
            row, _ = self.run_target(cap=1)
        self.assertNotEqual(row["state"], "refused")
        self.assertTrue(a.exists())
        self.assertFalse((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())

    def test_the_run_has_a_deadline_and_a_cut_retries_quietly(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        real = tc._check
        calls = []

        def cut_inside_collect(deadline):      # the deadline passes mid-walk
            calls.append(1)
            if len(calls) > 3:
                raise tc.Cut()
            return real(deadline)
        with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]), \
                mock.patch.object(tc, "_check", side_effect=cut_inside_collect):
            rows, lines = tc.enforce(self.home, NOW, cap=1, deadline=time.monotonic() + 60)
        self.assertEqual([(r["state"], r["retry"]) for r in rows], [("reducing", True)])
        self.assertEqual(lines, [])
        self.assertEqual(len(list((self.p / "deps").iterdir())), 3)
        rec = tc.load_state(self.home)["targets"][str(self.t)]
        self.assertEqual(rec["measured"], NOW - tc.MEASURE_INTERVAL_S + tc.RETRY_CUT_S)

    def test_a_config_rebuilt_after_weeks_is_still_recognised_as_a_rebuild(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        _old_unit(self.p, "serde", H[1], 3 * DAY)
        rec = {"removed": {"debug/.fingerprint/serde-" + H[0]: NOW - 30 * DAY}}
        self.run_target(cap=1, rec=rec)
        self.assertIn("CAP-THRASH", [a[0] for a in self.lines])
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())

    def test_a_mass_rebuild_is_one_thrash_note_per_target(self):
        for i in range(4):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        rec = {"removed": {"debug/.fingerprint/serde-" + H[i]: NOW - DAY for i in range(3)}}
        self.run_target(cap=1, rec=rec)
        thrash = [a for a in self.lines if a[0] == "CAP-THRASH"]
        self.assertEqual(len(thrash), 1)
        self.assertIn("3 unit(s)", thrash[0][3])

    def test_a_unit_whose_fingerprint_went_before_a_failure_is_remembered(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        _old_unit(self.p, "serde", H[1], 9 * DAY)
        real = tc.cs._remove

        def only_fingerprints(path):
            if "fingerprint" not in path:
                raise OSError(5, "killed here")
            return real(path)
        rec = {}
        with mock.patch.object(tc.cs, "_remove", side_effect=only_fingerprints):
            self.run_target(cap=1, rec=rec)
        self.assertIn("debug/.fingerprint/serde-" + H[0], rec["removed"])

    def test_an_unwritable_state_walks_nothing_at_all(self):
        with mock.patch.object(tc, "save_state", side_effect=OSError(28, "full")), \
                mock.patch.object(tc, "_cargo_targets") as walk:
            rows, lines = tc.enforce(self.home, NOW, cap=1)
        walk.assert_not_called()
        self.assertEqual((rows, [a[0] for a in lines]), ([], ["CAP-SKIP"]))

    def test_a_fifo_named_like_a_fingerprint_json_never_hangs_the_walk(self):
        import threading
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        os.mkfifo(self.p / ".fingerprint" / ("serde-" + H[0]) / "a-first.json")
        done = []
        th = threading.Thread(target=lambda: done.append(tc.collect(str(self.t))), daemon=True)
        th.start()
        th.join(10)
        self.assertEqual(len(done), 1, "collect hung on a FIFO")

    def test_a_unit_gone_before_the_act_is_not_noted_as_kept(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        real = tc._hold_build_locks

        def vanish_then_lock(profile):
            for f in list((self.p / "deps").glob("*%s*" % H[0])):
                f.unlink()
            import shutil
            shutil.rmtree(self.p / ".fingerprint" / ("serde-" + H[0]), ignore_errors=True)
            return real(profile)
        with mock.patch.object(tc, "_hold_build_locks", side_effect=vanish_then_lock):
            self.run_target(cap=1)
        self.assertNotIn("CAP-KEEP", [a[0] for a in self.lines])


class TestNoThrash(_Home):
    def test_a_unit_the_next_build_recreates_is_protected_not_deleted_again(self):
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        rec = {}
        self.run_target(cap=1, rec=rec)
        self.assertFalse((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())
        _old_unit(self.p, "serde", H[0], 2 * DAY)     # the next build recreated it
        self.lines.clear()
        self.run_target(cap=1, rec=rec)
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())
        self.assertEqual([a[0] for a in self.lines].count("CAP-THRASH"), 1)
        self.lines.clear()
        self.run_target(cap=1, rec=rec)                # third poll: no loop, no repeat log
        self.assertTrue((self.p / "deps" / ("libserde-%s.rlib" % H[0])).exists())
        self.assertNotIn("CAP-THRASH", [a[0] for a in self.lines])

    def _poll(self, now, cap=1):
        with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]):
            return tc.enforce(self.home, now, cap=cap)

    def test_over_the_cap_is_noted_once_a_day_and_measured_once_an_hour(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        rows, lines = self._poll(NOW)
        self.assertEqual([r["state"] for r in rows], ["over"])
        self.assertEqual([a[0] for a in lines], ["CAP-OVER"])
        rows, lines = self._poll(NOW + 60)                 # next poll: not measured again
        self.assertEqual((rows, lines), ([], []))
        rows, lines = self._poll(NOW + 2 * 3600)           # measured, still over: quiet
        self.assertEqual(([r["state"] for r in rows], lines), (["over"], []))
        _rows, lines = self._poll(NOW + 25 * 3600)         # a day later: one more note
        self.assertEqual([a[0] for a in lines], ["CAP-OVER"])

    def test_a_pass_the_budget_cuts_short_comes_back_in_ten_minutes_quietly(self):
        for i in range(4):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]):
            rows, lines = tc.enforce(self.home, NOW, cap=1, act_s=-1)
            self.assertEqual([r["state"] for r in rows], ["reducing"])
            self.assertNotIn("CAP-OVER", [a[0] for a in lines])
            self.assertEqual(tc.enforce(self.home, NOW + 300, cap=1)[0], [])
            rows, _ = tc.enforce(self.home, NOW + tc.RETRY_CUT_S, cap=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(list((self.p / "deps").iterdir())), 1)

    def test_a_profile_without_cargos_lock_file_is_never_touched(self):
        """dev1 15.11.: intercom's migrated target has no .cargo-lock; with no lock
        to hold, a starting build could race the delete."""
        for i in range(3):
            _old_unit(self.p, "serde", H[i], (10 - i) * DAY)
        (self.p / ".cargo-lock").unlink()
        self.run_target(cap=1)
        self.assertEqual(len(list((self.p / "deps").iterdir())), 3)

    def test_a_run_takes_every_due_target_and_stops_between_them_at_its_deadline(self):
        other = _target(self.home, "other")
        with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t), str(other)]):
            first, _ = tc.enforce(self.home, NOW, cap=1 << 40)
            second, _ = tc.enforce(self.home, NOW + 60, cap=1 << 40)
            third, _ = tc.enforce(self.home, NOW + 3600, cap=1 << 40,
                                  deadline=time.monotonic() - 1)
        self.assertEqual([len(first), len(second), len(third)], [2, 0, 0])
        self.assertTrue(tc.due(self.home, NOW + 3600))     # still due for the next run


class TestDeclaration(unittest.TestCase):
    def test_default_cap_and_declared_cap(self):
        self.assertEqual(tc.cap_bytes("x", {}), 40 * tc.GIB)
        self.assertEqual(tc.cap_bytes("x", {"x": {"target_cap_gb": 80}}), 80 * tc.GIB)

    def test_the_policy_validates_the_keys(self):
        import cli_account_policy as policy
        self.assertEqual(policy.validate_build_budget({}), [])
        self.assertEqual(policy.validate_build_budget(
            {"target_cap_gb": 60, "cargo_full_debug": True}), [])
        for bad in ({"target_cap_gb": "60"}, {"target_cap_gb": 2}, {"target_cap_gb": True},
                    {"target_cap_gb": 5000}, {"cargo_full_debug": "yes"}):
            self.assertTrue(policy.validate_build_budget(bad), bad)

    def test_the_bootstrap_accepts_the_keys_and_rejects_a_bad_value(self):
        import cli_account_bootstrap as b
        raw = dict(b.SERVICE_ACCOUNTS["fohmixer"], target_cap_gb=60, cargo_full_debug=False)
        self.assertEqual([e for e in b.validate_account("fohmixer", raw)
                          if "target_cap" in e or "cargo_full" in e], [])
        raw["target_cap_gb"] = "lots"
        self.assertTrue(any("target_cap_gb" in e for e in b.validate_account("fohmixer", raw)))


class TestToolchainEnv(unittest.TestCase):
    """Measured 15.11. on dev1 (scratch copies, build + test --no-run, both green):
    wall-bridge 893.0 -> 657.6 MB (-26.4 %), shading 3580.7 -> 2079.5 MB (-41.9 %)."""

    def _sourced(self, text):
        import getpass
        import subprocess
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(text)
        self.addCleanup(os.unlink, f.name)
        out = subprocess.run(
            ["sh", "-c", '. "$1"; printf "%s|%s" "$CARGO_PROFILE_DEV_DEBUG" '
             '"$CARGO_PROFILE_TEST_DEBUG"', "sh", f.name],
            capture_output=True, text=True, check=True,
            env={"PATH": os.environ["PATH"], "HOME": "/nonexistent"})
        return out.stdout, getpass.getuser()

    def test_project_accounts_get_line_tables_only(self):
        import cli_project_toolchain as tch
        got, _me = self._sourced(tch.render_env_file())
        self.assertEqual(got, "line-tables-only|line-tables-only")

    def test_an_opted_out_account_keeps_cargos_default(self):
        import getpass
        import cli_project_toolchain as tch
        got, _ = self._sourced(tch.render_env_file(full_debug=("other", getpass.getuser())))
        self.assertEqual(got, "|")
        got, _ = self._sourced(tch.render_env_file(full_debug=("other",)))
        self.assertEqual(got, "line-tables-only|line-tables-only")

    def test_the_bootstrap_renders_the_boxs_opt_outs(self):
        import cli_account_bootstrap as b
        import cli_project_toolchain as tch
        decl = {"fohmixer": dict(b.SERVICE_ACCOUNTS["fohmixer"], cargo_full_debug=True),
                "x": {"host": "dev2", "cargo_full_debug": True}}
        with mock.patch.dict(b.SERVICE_ACCOUNTS, decl):
            self.assertEqual(tch.full_debug_accounts("dev1"), ("fohmixer",))
            step = tch.render_account_env_step(b.account_spec("fohmixer"))
        self.assertIn(tch.render_env_file(full_debug=("fohmixer",)), step)


class TestPollWiring(unittest.TestCase):
    """The poll only decides and launches: a dstack-size pass is longer than a poll
    (re-review 3, 15.11.), so it runs as a detached user unit."""

    def _poll(self, home, launches, now=NOW, ok=True):
        def launch(h):
            launches.append(h)
            if ok:
                tc.run_detached(h, now)
            return (ok, "started" if ok else "systemd-run absent")
        with mock.patch("watchdog.disk_guard_cargo_sweep._snapshot", return_value=IDLE), \
                mock.patch.object(tc, "cap_bytes", return_value=1), \
                mock.patch.object(tc, "_launch", side_effect=launch):
            return dg.run_disk_guard(
                now=now, home=home, dry_run=False,
                statvfs_fn=lambda _m: types.SimpleNamespace(
                    f_blocks=1000, f_bfree=600, f_bavail=600, f_frsize=4096,
                    f_files=100000, f_ffree=50000),
                dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000,
                box_class_fn=lambda: "workstation",
                planners_fn=lambda _h, _n: [], top_consumers_fn=lambda *a, **k: [])

    def test_a_poll_with_no_disk_pressure_launches_the_detached_run(self):
        with tempfile.TemporaryDirectory() as home:
            t = _target(home, "dstack")
            for i in range(3):
                _old_unit(t / "debug", "serde", H[i], (10 - i) * DAY)
            launches = []
            logs = self._poll(home, launches)
            self.assertEqual(launches, [home])
            cache = json.loads((Path(home) / ".claude/disk-guard/status.json").read_text())
            self.assertNotIn("target_caps", cache)         # owner 15.11.: no new surface
            self.assertEqual(len(list((t / "debug" / "deps").iterdir())), 1)
            journal = (Path(home) / ".claude/disk-guard").glob("*.log")
            self.assertIn("CAP-REMOVE", "".join(f.read_text() for f in journal))
            self.assertFalse(any("target-cap" in ln for ln in logs), logs)
            self._poll(home, launches, now=NOW + 60)        # nothing due: no launch
            self.assertEqual(len(launches), 1)

    def test_a_failed_launch_is_logged_and_retried_in_ten_minutes(self):
        with tempfile.TemporaryDirectory() as home:
            _target(home, "dstack")
            launches = []
            logs = self._poll(home, launches, ok=False)
            self.assertTrue(any("target-cap launch failed" in ln for ln in logs), logs)
            self._poll(home, launches, now=NOW + 60, ok=False)
            self._poll(home, launches, now=NOW + tc.RETRY_CUT_S, ok=False)
            self.assertEqual(len(launches), 2)

    def test_the_poll_itself_never_walks_a_target(self):
        logs = []
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(tc, "_cargo_targets") as walk, \
                mock.patch.object(tc, "_launch", return_value=(True, "started")) as launch:
            tc.run_pass(home, NOW, False, logs, is_root=False)
        walk.assert_not_called()
        launch.assert_called_once()

    def test_the_launch_is_a_niced_single_flight_user_unit(self):
        seen = []

        def run(argv, **kw):
            seen.append(argv)
            return types.SimpleNamespace(returncode=0, stderr="")
        from watchdog import user_unit
        with mock.patch("subprocess.run", side_effect=run):
            self.assertEqual(tc._launch("/home/x"), (False, "systemd-run not used under test"))
        self.assertEqual(seen, [])          # a test never starts a real pass on a real home
        with mock.patch.object(user_unit, "launch", wraps=user_unit.launch) as lw, \
                mock.patch("subprocess.run", side_effect=run), \
                mock.patch("watchdog.disk_guard_escalation.running_under_pytest",
                           return_value=False):
            self.assertEqual(tc._launch("/home/x"), (True, "started"))
        argv = seen[0]
        self.assertEqual(argv[argv.index("--unit") + 1], tc.UNIT_NAME)
        self.assertIn("Nice=10", argv)
        self.assertIn("IOSchedulingClass=idle", argv)
        self.assertEqual(argv[-2:], ["-c", tc.CHILD_CODE])
        self.assertEqual(lw.call_args.kwargs["env_prefixes"], ())

    def test_one_detached_run_at_a_time_and_main_needs_run(self):
        import fcntl
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / ".claude" / "disk-guard"
            d.mkdir(parents=True)
            fd = os.open(d / tc.LOCK_NAME, os.O_RDWR | os.O_CREAT)
            self.addCleanup(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
            with mock.patch.object(tc, "enforce") as enf:
                self.assertEqual(tc.run_detached(home, NOW), [])
            enf.assert_not_called()
        self.assertEqual(tc.main([]), 2)

    def test_never_as_root_and_never_in_a_dry_run(self):
        logs = []
        with mock.patch.object(tc, "_launch") as launch:
            tc.run_pass("/root", NOW, False, logs, is_root=True)
            with tempfile.TemporaryDirectory() as home:
                tc.run_pass(home, NOW, True, logs, is_root=False)
        launch.assert_not_called()

    def test_nothing_new_is_surfaced(self):
        """Owner 15.11.: "ja nechcem stale nieco vidiet" — journal only."""
        import statusbar
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / ".claude" / "disk-guard"
            d.mkdir(parents=True)
            (d / "status.json").write_text(json.dumps({
                "worst_pct": 70, "ts": time.time(),
                "target_caps": [{"path": "/x/target", "state": "over"}]}))
            self.assertEqual(statusbar.disk_segment(home=home), "")
        self.assertFalse(hasattr(tc, "status_lines"))


if __name__ == "__main__":
    unittest.main()
