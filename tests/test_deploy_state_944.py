"""Tests for the deploy-state PRODUCER (#944 part 2).

Covers: registry lookup, main-version parsing, deploy-window evaluation,
per-version dedup, the multi-instance classifier, and the wiring through
goal_lane_sweep.

All tests use tmpdir/json fixtures -- no live gh, no ssh, no HTTP.
"""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from watchdog import deploy_state


class TestStripOdooPrefix(unittest.TestCase):
    """_strip_odoo_prefix -- strips ``19.0.`` from Odoo version strings."""

    def test_strips_odoo_prefix(self):
        self.assertEqual(deploy_state._strip_odoo_prefix("19.0.2.266.0"),
                         "2.266.0")

    def test_no_prefix(self):
        self.assertEqual(deploy_state._strip_odoo_prefix("2.266.0"),
                         "2.266.0")

    def test_short_version(self):
        self.assertEqual(deploy_state._strip_odoo_prefix("1.2"),
                         "1.2")

    def test_other_prefix(self):
        # "17.0.1.0.0" -> "1.0.0"
        self.assertEqual(deploy_state._strip_odoo_prefix("17.0.1.0.0"),
                         "1.0.0")


class TestReadMainVersion(unittest.TestCase):
    """read_main_version -- reads version from a manifest file via git or
    working tree fallback."""

    def test_reads_manifest_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            vf = os.path.join(td, "manifest.py")
            Path(vf).write_text(
                '{\n    "version": "19.0.2.266.0",\n}')
            # git show will fail (not a git repo), falls back to file read
            v = deploy_state.read_main_version(td, "manifest.py")
            self.assertEqual(v, "2.266.0")

    def test_none_version_file(self):
        self.assertIsNone(deploy_state.read_main_version("/tmp", None))

    def test_missing_file(self):
        self.assertIsNone(
            deploy_state.read_main_version("/tmp", "no-such-file.py"))


class TestDeployWindow(unittest.TestCase):
    """evaluate_deploy_window -- window-open / window-passed classification."""

    def _dt(self, hour, minute=0, weekday=0):
        """Create a datetime for testing.  weekday: 0=Mon, 5=Sat, 6=Sun."""
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Europe/Bratislava")
        # 2026-09-07 is a Monday; add weekday offset
        base = datetime.date(2026, 9, 7)  # Monday
        d = base + datetime.timedelta(days=weekday)
        return datetime.datetime(d.year, d.month, d.day, hour, minute,
                                 tzinfo=tz)

    def test_weeknight_window_open(self):
        spec = {"open_start": "19:00", "open_end": "06:00",
                "timezone": "Europe/Bratislava", "everyday": False}
        # Monday 23:00 -> window open
        w_open, w_passed = deploy_state.evaluate_deploy_window(
            spec, now_dt=self._dt(23, weekday=0))
        self.assertTrue(w_open)
        self.assertFalse(w_passed)

    def test_weeknight_window_passed(self):
        spec = {"open_start": "19:00", "open_end": "06:00",
                "timezone": "Europe/Bratislava", "everyday": False}
        # Monday 10:00 -> window passed (daytime)
        w_open, w_passed = deploy_state.evaluate_deploy_window(
            spec, now_dt=self._dt(10, weekday=0))
        self.assertFalse(w_open)
        self.assertTrue(w_passed)

    def test_weekend_always_open(self):
        spec = {"open_start": "19:00", "open_end": "06:00",
                "timezone": "Europe/Bratislava", "everyday": False}
        # Saturday 10:00 -> weekend, always open
        w_open, w_passed = deploy_state.evaluate_deploy_window(
            spec, now_dt=self._dt(10, weekday=5))
        self.assertTrue(w_open)
        self.assertFalse(w_passed)

    def test_everyday_window_open(self):
        spec = {"open_start": "23:00", "open_end": "05:00",
                "timezone": "Europe/Bratislava", "everyday": True}
        # Saturday 23:30 -> open (everyday window applies on weekend too)
        w_open, w_passed = deploy_state.evaluate_deploy_window(
            spec, now_dt=self._dt(23, 30, weekday=5))
        self.assertTrue(w_open)
        self.assertFalse(w_passed)

    def test_everyday_window_passed(self):
        spec = {"open_start": "23:00", "open_end": "05:00",
                "timezone": "Europe/Bratislava", "everyday": True}
        # Saturday 10:00 -> passed (everyday, no weekend exemption)
        w_open, w_passed = deploy_state.evaluate_deploy_window(
            spec, now_dt=self._dt(10, weekday=5))
        self.assertFalse(w_open)
        self.assertTrue(w_passed)

    def test_none_spec_means_always_open(self):
        """null / None deploy_window = no restriction, always open (#944 Y3)."""
        self.assertEqual(deploy_state.evaluate_deploy_window(None),
                         (True, False))

    def test_empty_times(self):
        self.assertEqual(
            deploy_state.evaluate_deploy_window({"open_start": "",
                                                  "open_end": ""}),
            (False, False))


class TestPerVersionDedup(unittest.TestCase):
    """should_fire / clear_stale_dedup -- per-(ticket, version) dedup."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = self._td.name
        self.addCleanup(self._td.cleanup)

    def test_fires_first_time(self):
        self.assertTrue(
            deploy_state.should_fire("test-repo", 42, "2.266.0",
                                     home=self.home))

    def test_does_not_fire_same_version(self):
        deploy_state.should_fire("test-repo", 42, "2.266.0",
                                 home=self.home)
        self.assertFalse(
            deploy_state.should_fire("test-repo", 42, "2.266.0",
                                     home=self.home))

    def test_fires_new_version(self):
        deploy_state.should_fire("test-repo", 42, "2.266.0",
                                 home=self.home)
        self.assertTrue(
            deploy_state.should_fire("test-repo", 42, "2.267.0",
                                     home=self.home))

    def test_clear_stale(self):
        deploy_state.should_fire("test-repo", 42, "2.266.0",
                                 home=self.home)
        # Advance main version past the fired version
        deploy_state.clear_stale_dedup("test-repo", "2.267.0",
                                       home=self.home)
        # The old entry is cleared, so should fire again
        self.assertTrue(
            deploy_state.should_fire("test-repo", 42, "2.266.0",
                                     home=self.home))


class TestRegistryLookup(unittest.TestCase):
    """_find_project -- path match + github_repo fallback."""

    def test_path_match(self):
        with tempfile.TemporaryDirectory() as td:
            reg = [{"path": td, "name": "myproj",
                    "deploy_state": {"instances": []}}]
            entry = deploy_state._find_project(reg, td)
            self.assertIsNotNone(entry)
            self.assertEqual(entry["name"], "myproj")

    def test_github_repo_match(self):
        reg = [{"path": "/nonexistent/path", "name": "odoo-erp",
                "github_repo": "zbynekdrlik/odoo-erp",
                "deploy_state": {"instances": []}}]
        with mock.patch.object(deploy_state, "_repo_slug_from_cwd",
                               return_value="zbynekdrlik/odoo-erp"):
            entry = deploy_state._find_project(reg, "/some/checkout")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["name"], "odoo-erp")

    def test_no_match(self):
        reg = [{"path": "/other", "name": "other"}]
        entry = deploy_state._find_project(reg, "/somewhere")
        self.assertIsNone(entry)


class TestFetchDeployState(unittest.TestCase):
    """fetch_deploy_state -- end-to-end with registry fixture."""

    def test_undeclared_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            reg_path = os.path.join(td, "registry.json")
            Path(reg_path).write_text(json.dumps([
                {"path": td, "name": "simple-project"}
            ]))
            result = deploy_state.fetch_deploy_state(
                td, registry_path=reg_path)
            self.assertIsNone(result)

    def test_declared_returns_instances(self):
        with tempfile.TemporaryDirectory() as td:
            # Write a manifest file
            mf = os.path.join(td, "version.py")
            Path(mf).write_text('{"version": "2.266.0"}')
            reg_path = os.path.join(td, "registry.json")
            Path(reg_path).write_text(json.dumps([{
                "path": td,
                "name": "test-proj",
                "deploy_state": {
                    "main_version_file": "version.py",
                    "instances": [
                        {"name": "inst1",
                         "version_source": None,
                         "deploy_window": None}
                    ]
                }
            }]))
            result = deploy_state.fetch_deploy_state(
                td, registry_path=reg_path)
            self.assertIsNotNone(result)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["instance"], "inst1")
            self.assertEqual(result[0]["main_version"], "2.266.0")
            # prod_version is None (no version_source)
            self.assertIsNone(result[0]["prod_version"])


class TestMultiInstanceClassify(unittest.TestCase):
    """_deploy_watch_classify with per-instance list (F2).

    Each test isolates the dedup state by patching _dedup_path to a tmpdir,
    so should_fire writes to a throwaway dir instead of ~/.claude/.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = self._td.name
        self.addCleanup(self._td.cleanup)
        # Patch dedup + repo_slug so classify's dedup calls are isolated
        self._p1 = mock.patch.object(
            deploy_state, "_dedup_path",
            return_value=os.path.join(self.home, ".claude", "deploy-watch"))
        self._p2 = mock.patch.object(
            deploy_state, "_repo_slug_from_cwd",
            return_value="test/classify-repo")
        self._p1.start()
        self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)

    def test_any_window_open_fires(self):
        """If ANY instance is window-open, fire DEPLOY-WINDOW."""
        from watchdog.ops_wait_recheck import _deploy_watch_classify
        dep_targets = [42, 43]
        state = {}

        def _fetch(_cwd):
            return [
                {"main_version": "2.266.0", "prod_version": "2.265.0",
                 "window_open": True, "window_passed": False},
                {"main_version": "2.266.0", "prod_version": "2.266.0",
                 "window_open": False, "window_passed": True},
            ]

        import time
        now = time.time()
        dw, dm = _deploy_watch_classify(dep_targets, "/tmp", _fetch,
                                        state, now)
        self.assertEqual(dw, [42, 43])
        self.assertEqual(dm, [])

    def test_all_missed_fires_miss(self):
        """If ALL behind instances are window-missed, fire DEPLOY-MISS."""
        from watchdog.ops_wait_recheck import _deploy_watch_classify
        dep_targets = [100]
        state = {}

        def _fetch(_cwd):
            return [
                {"main_version": "2.266.0", "prod_version": "2.265.0",
                 "window_open": False, "window_passed": True},
                {"main_version": "2.266.0", "prod_version": "2.266.0",
                 "window_open": False, "window_passed": False},
            ]

        import time
        now = time.time()
        dw, dm = _deploy_watch_classify(dep_targets, "/tmp", _fetch,
                                        state, now)
        self.assertEqual(dw, [])
        self.assertEqual(dm, [100])

    def test_none_fetch_no_action(self):
        """None fetch -> no action."""
        from watchdog.ops_wait_recheck import _deploy_watch_classify
        dep_targets = [200]
        state = {}

        import time
        now = time.time()
        dw, dm = _deploy_watch_classify(dep_targets, "/tmp", lambda _: None,
                                        state, now)
        self.assertEqual(dw, [])
        self.assertEqual(dm, [])

    def test_single_dict_backward_compat(self):
        """A single dict return (part-1 shape) still works."""
        from watchdog.ops_wait_recheck import _deploy_watch_classify
        dep_targets = [300]
        state = {}

        def _fetch(_cwd):
            return {"main_version": "2.266.0", "prod_version": "2.265.0",
                    "window_open": True, "window_passed": False}

        import time
        now = time.time()
        dw, dm = _deploy_watch_classify(dep_targets, "/tmp", _fetch,
                                        state, now)
        self.assertEqual(dw, [300])
        self.assertEqual(dm, [])


class TestDedupWiredInClassify(unittest.TestCase):
    """F5: should_fire is called inside _deploy_watch_classify (R2 fix)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = self._td.name
        self.addCleanup(self._td.cleanup)

    def test_dedup_prevents_second_fire(self):
        """Same (ticket, version) classified twice -> second returns empty."""
        from watchdog.ops_wait_recheck import _deploy_watch_classify
        dep_targets = [500]
        import time
        now = time.time()

        def _fetch(_cwd):
            return [{"main_version": "2.266.0", "prod_version": "2.265.0",
                     "window_open": True, "window_passed": False}]

        with mock.patch.object(deploy_state, "_dedup_path",
                               return_value=os.path.join(
                                   self.home, ".claude", "deploy-watch")):
            with mock.patch.object(deploy_state, "_repo_slug_from_cwd",
                                   return_value="test/dedup-repo"):
                # First call: should fire
                state1 = {}
                dw1, _ = _deploy_watch_classify(
                    dep_targets, "/tmp", _fetch, state1, now)
                self.assertEqual(dw1, [500])
                # Second call with fresh state (simulates next sweep):
                state2 = {}
                dw2, _ = _deploy_watch_classify(
                    dep_targets, "/tmp", _fetch, state2, now + 100)
                self.assertEqual(dw2, [],
                                 "dedup should prevent second fire")


class TestGoalLaneSweepWiring(unittest.TestCase):
    """goal_lane_sweep accepts and forwards deploy_state_fetch (#944 F1)."""

    def test_signature_accepts_deploy_state_fetch(self):
        """goal_lane_sweep accepts deploy_state_fetch kwarg."""
        import inspect
        from watchdog.goal import goal_lane_sweep
        sig = inspect.signature(goal_lane_sweep)
        self.assertIn("deploy_state_fetch", sig.parameters)

    def test_run_once_accepts_deploy_state_fetch(self):
        """run_once accepts deploy_state_fetch kwarg."""
        import inspect
        from watchdog import run_once
        sig = inspect.signature(run_once)
        self.assertIn("deploy_state_fetch", sig.parameters)

    def test_factory_returns_callable(self):
        """_watchdog_deploy_state_fetch returns a callable."""
        import airuleset
        fn = airuleset._watchdog_deploy_state_fetch()
        self.assertTrue(callable(fn))


class TestRegistryOdooErp(unittest.TestCase):
    """The projects-registry.json carries the odoo-erp deploy_state entry."""

    def test_odoo_erp_has_deploy_state(self):
        reg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects-registry.json")
        with open(reg_path) as f:
            data = json.load(f)
        odoo = None
        for entry in data:
            if entry.get("name") == "odoo-erp":
                odoo = entry
                break
        self.assertIsNotNone(odoo, "odoo-erp entry missing from registry")
        ds = odoo.get("deploy_state")
        self.assertIsInstance(ds, dict)
        instances = ds.get("instances")
        self.assertIsInstance(instances, list)
        self.assertGreaterEqual(len(instances), 3,
                                "expected montalu + slovnormal + miva")
        names = [i["name"] for i in instances]
        self.assertIn("montalu", names)
        self.assertIn("slovnormal", names)
        self.assertIn("miva", names)

    def test_odoo_erp_has_github_repo(self):
        reg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects-registry.json")
        with open(reg_path) as f:
            data = json.load(f)
        odoo = next(e for e in data if e.get("name") == "odoo-erp")
        self.assertEqual(odoo["github_repo"], "zbynekdrlik/odoo-erp")

    def test_miva_deploy_window_everyday(self):
        """miva's deploy window is everyday=True (kiosk runs daily)."""
        reg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects-registry.json")
        with open(reg_path) as f:
            data = json.load(f)
        odoo = next(e for e in data if e.get("name") == "odoo-erp")
        miva = next(i for i in odoo["deploy_state"]["instances"]
                    if i["name"] == "miva")
        self.assertTrue(miva["deploy_window"]["everyday"])

    def test_slovnormal_deploy_window_weeknight(self):
        """slovnormal's deploy window is weeknight-only (everyday=False)."""
        reg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects-registry.json")
        with open(reg_path) as f:
            data = json.load(f)
        odoo = next(e for e in data if e.get("name") == "odoo-erp")
        sn = next(i for i in odoo["deploy_state"]["instances"]
                  if i["name"] == "slovnormal")
        self.assertFalse(sn["deploy_window"]["everyday"])


if __name__ == "__main__":
    unittest.main()
