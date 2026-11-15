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
    for root, _dirs, files in os.walk(path):
        for name in files:
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


def _old_unit(profile, pkg, h, age_s, size=1000, kind="lib"):
    """Pre-1.100 layout: .fingerprint/<pkg>-<h>, deps/lib<pkg>-<h>.rlib."""
    fp = profile / ".fingerprint" / ("%s-%s" % (pkg, h))
    fp.mkdir(parents=True)
    (fp / ("%s-%s" % (kind, pkg))).write_text(h)
    (fp / ("%s-%s.json" % (kind, pkg))).write_text("{}")
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

    def test_the_newest_unit_of_each_group_and_a_unit_used_this_hour_stay(self):
        _old_unit(self.p, "serde", H[0], 30 * DAY)           # newest of its group: stays
        _old_unit(self.p, "app", H[1], 30 * DAY, kind="test-lib")
        _old_unit(self.p, "app", H[2], 20 * DAY, kind="lib")  # newest lib of app
        _old_unit(self.p, "tokio", H[3], 600)                # used 10 min ago
        _old_unit(self.p, "tokio", H[4], 60)
        row, _ = self.run_target(cap=10)
        for h in H[:5]:
            self.assertTrue(any(h in f.name for f in (self.p / "deps").iterdir()), h)
        self.assertEqual(row["state"], "unreachable")

    def test_cargo_1100_layout_units_go_whole(self):
        for i in range(4):
            _new_unit(self.p, "serde", H[i], (10 - i) * DAY)
        row, _ = self.run_target(cap=1)
        left = sorted(x.name for x in (self.p / "build" / "serde").iterdir())
        self.assertEqual(left, [H[3]])
        self.assertEqual(row["state"], "unreachable")

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
        self.assertIn(row["state"], ("locked", "unreachable"))
        self.assertTrue(any(a[0] == "CAP-LOCKED" for a in self.lines))

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

    def test_unreachable_is_logged_once_per_state_change_not_every_poll(self):
        _old_unit(self.p, "serde", H[0], 10 * DAY)
        lines = []
        for _ in range(3):
            with mock.patch.object(tc, "_cargo_targets", return_value=[str(self.t)]):
                _rows, got = tc.enforce(self.home, NOW, cap=1)
            lines += got
        self.assertEqual([a[0] for a in lines].count("CAP-STATE"), 1)
        state = tc.load_state(self.home)["targets"][str(self.t)]
        self.assertEqual((state["state"], state["over_streak"]), ("unreachable", 3))


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
    """Measured 15.11. on dev1 (scratch copies): wall-bridge 26.4 % and shading
    smaller targets with line-tables-only debug info."""

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


class TestVisibility(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name

    def _cache(self, rows, ts=None):
        d = Path(self.home) / ".claude" / "disk-guard"
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps(
            {"worst_pct": 70, "ts": time.time() if ts is None else ts, "target_caps": rows}))

    def _row(self, state, streak=0):
        return {"path": "/home/dstack/devel/dstack/target", "bytes": 52 * tc.GIB,
                "cap": 40 * tc.GIB, "state": state, "over_streak": streak}

    def test_the_footer_shows_only_an_actionable_target(self):
        import statusbar
        self._cache([self._row("locked", 3)])
        self.assertEqual(statusbar.disk_segment(home=self.home), "")
        self._cache([self._row("unreachable", 1)])
        self.assertIn("build dstack 52G/40G", statusbar.disk_segment(home=self.home))
        self._cache([self._row("over", tc.ACTIONABLE_STREAK)])
        self.assertIn("build dstack 52G/40G", statusbar.disk_segment(home=self.home))
        self._cache([self._row("unreachable", 1)], ts=time.time() - 3600)
        self.assertEqual(statusbar.disk_segment(home=self.home), "")

    def test_status_lists_the_targets(self):
        self._cache([self._row("unreachable", 1)])
        lines = tc.status_lines(self.home)
        self.assertIn("dstack 52G/40G unreachable", "\n".join(lines))
        self.assertIn("ACTION", "\n".join(lines))
        self._cache([])
        self.assertIn("none above half the cap", tc.status_lines(self.home)[0])


class TestPollWiring(unittest.TestCase):
    def test_the_cap_pass_runs_on_a_poll_with_no_disk_pressure(self):
        with tempfile.TemporaryDirectory() as home:
            t = _target(home, "dstack")
            for i in range(3):
                _old_unit(t / "debug", "serde", H[i], (10 - i) * DAY)
            with mock.patch("watchdog.disk_guard_cargo_sweep._snapshot", return_value=IDLE), \
                    mock.patch.object(tc, "cap_bytes", return_value=1):
                logs = dg.run_disk_guard(
                    now=NOW, home=home, dry_run=False,
                    statvfs_fn=lambda _m: types.SimpleNamespace(
                        f_blocks=1000, f_bfree=600, f_bavail=600, f_frsize=4096,
                        f_files=100000, f_ffree=50000),
                    dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000,
                    box_class_fn=lambda: "workstation",
                    planners_fn=lambda _h, _n: [], top_consumers_fn=lambda *a, **k: [])
            cache = json.loads((Path(home) / ".claude/disk-guard/status.json").read_text())
            self.assertEqual([r["state"] for r in cache["target_caps"]], ["unreachable"])
            self.assertEqual(len(list((t / "debug" / "deps").iterdir())), 1)
            self.assertTrue(any("target cap removed 2 unit" in ln for ln in logs), logs)

    def test_never_as_root(self):
        status, logs = {}, []
        with mock.patch.object(tc, "enforce") as enf:
            tc.run_pass(status, "/root", NOW, False, logs, is_root=True)
        enf.assert_not_called()
        self.assertNotIn("target_caps", status)


if __name__ == "__main__":
    unittest.main()
