"""#971 — fleet-burn coordinator cutover: role-based gating + shared feed.

RED tests — these MUST FAIL before the fix and PASS after.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import watchdog as wd


def tempfile_mkdtemp_cleanup(tc):
    d = tempfile.mkdtemp()
    tc.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
    return d


def _snap_row(ts, host, usd, msgs, avg_ctx):
    return {"ts": ts, "host": host, "usd": usd, "msgs": msgs, "avg_ctx": avg_ctx,
            "by_model": {}}


def _fleet_now():
    """2026-07-25T20:30:00+00:00 — past the 5-min delay boundary."""
    import datetime
    return datetime.datetime(2026, 7, 25, 20, 30, 0,
                             tzinfo=datetime.timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Deliverable 1 — coordinator gating by ROLE (box-class `controller`),
# not hostname `dev1`.
# ---------------------------------------------------------------------------

class TestCoordinatorGatingByRole(unittest.TestCase):
    """cmd_watchdog must gate fleet jobs (16/19/35) on box-class `controller`,
    not `os.uname().nodename == 'dev1'`."""

    def test_cmd_watchdog_source_no_hostname_dev1(self):
        """The three `os.uname().nodename == 'dev1'` checks in cmd_watchdog
        must be replaced with a role-based check."""
        import inspect
        import airuleset
        source = inspect.getsource(airuleset.cmd_watchdog)
        # After the fix, no "dev1" hostname check should remain in cmd_watchdog
        self.assertNotIn('nodename == "dev1"', source,
                         "cmd_watchdog still hardcodes hostname 'dev1' — "
                         "must use box-class 'controller' role gate (#971)")
        self.assertNotIn("nodename == 'dev1'", source,
                         "cmd_watchdog still hardcodes hostname 'dev1'")

    def test_cmd_watchdog_source_uses_controller_box_class(self):
        """cmd_watchdog must reference 'controller' box-class for the
        coordinator gate."""
        import inspect
        import airuleset
        source = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("controller", source,
                       "cmd_watchdog must reference 'controller' box-class (#971)")


# ---------------------------------------------------------------------------
# M2 (#971 follow-on) — BEHAVIOURAL gate test: the source-text checks above
# prove the "dev1" literal is gone, but not that cmd_watchdog's WIRING
# actually differs between box-classes. Drive cmd_watchdog for real, with
# default_box_class() patched to each value, and assert what it wires into
# run_once() — the fleet_fetch callable, the two enable flags, and
# shared_fleet_path — is gated correctly on BOTH branches.
# ---------------------------------------------------------------------------

class TestCmdWatchdogCoordinatorWiring(unittest.TestCase):
    """Patch `default_box_class` to 'controller' vs 'workstation' and drive
    `airuleset.cmd_watchdog`, capturing the kwargs it passes to `run_once`."""

    class _Args:
        dry_run = False
        verbose = False

    def _drive(self, box_class, shared_dir_exists):
        import airuleset
        from watchdog import reaper

        captured = {}

        def fake_run_once(*a, **kw):
            captured.update(kw)
            return []

        tmp = tempfile_mkdtemp_cleanup(self)
        shared_dir = Path(tmp) / "shared"
        if shared_dir_exists:
            shared_dir.mkdir()

        with mock.patch.object(reaper, "default_box_class",
                               return_value=box_class), \
             mock.patch.object(wd, "run_once", side_effect=fake_run_once), \
             mock.patch.object(airuleset, "SHARED_FLEET_DIR", shared_dir):
            airuleset.cmd_watchdog(self._Args())
        return captured

    def test_controller_wires_fleet_fetch_and_shared_path(self):
        captured = self._drive("controller", shared_dir_exists=True)
        self.assertIsNotNone(captured.get("fleet_fetch"),
                             "controller box-class must wire fleet_fetch (#971)")
        self.assertTrue(captured.get("burn_alert_enabled"),
                        "controller box-class must enable burn_alert (#971)")
        self.assertTrue(captured.get("conformance_hb_enabled"),
                        "controller box-class must enable conformance_hb (#971)")
        self.assertIsNotNone(
            captured.get("shared_fleet_path"),
            "controller with an existing shared dir must wire shared_fleet_path (#971)")

    def test_workstation_does_not_wire_fleet_fetch_or_shared_path(self):
        captured = self._drive("workstation", shared_dir_exists=True)
        self.assertIsNone(captured.get("fleet_fetch"),
                          "non-controller box-class must NOT wire fleet_fetch (#971)")
        self.assertFalse(captured.get("burn_alert_enabled"),
                         "non-controller box-class must NOT enable burn_alert (#971)")
        self.assertFalse(captured.get("conformance_hb_enabled"),
                         "non-controller box-class must NOT enable conformance_hb (#971)")
        self.assertIsNone(
            captured.get("shared_fleet_path"),
            "non-controller box-class must never wire shared_fleet_path, even if "
            "the shared dir happens to exist (#971)")

    def test_controller_without_shared_dir_skips_shared_path(self):
        captured = self._drive("controller", shared_dir_exists=False)
        self.assertIsNotNone(captured.get("fleet_fetch"),
                             "controller box-class must still wire fleet_fetch")
        self.assertIsNone(
            captured.get("shared_fleet_path"),
            "shared_fleet_path must stay None when the shared dir does not exist yet (#971)")


# ---------------------------------------------------------------------------
# Deliverable 2 — shared fleet.jsonl feed at /var/lib/airuleset/fleet.jsonl.
# The coordinator writes the same row to both the primary home-dir path
# AND the shared path when `shared_fleet_path` is given.
# ---------------------------------------------------------------------------

class TestSharedFleetFeed(unittest.TestCase):
    """fleet_burn_job must write to shared_fleet_path when given."""

    def test_shared_fleet_path_receives_same_row(self):
        """When shared_fleet_path is given, fleet_burn_job writes the same
        JSON row to BOTH the primary fleet_path AND the shared path."""
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        shared_path = Path(tmp) / "shared" / "fleet.jsonl"
        shared_path.parent.mkdir(parents=True)
        local_snap = Path(tmp) / "snapshots.jsonl"
        with open(local_snap, "w") as f:
            f.write(json.dumps(_snap_row("2026-07-25T19:00:00+00:00", "ctrl", 1.0, 5, 1000)) + "\n")

        wd.fleet_burn_job(
            _fleet_now(), {}, [], lambda *a, **k: None,
            fetch=lambda hs, hb: {},
            local_snapshot_path=local_snap,
            fleet_path=fleet_path,
            shared_fleet_path=shared_path,
        )
        self.assertTrue(fleet_path.exists(), "primary fleet.jsonl must be written")
        self.assertTrue(shared_path.exists(), "shared fleet.jsonl must be written")
        primary_row = json.loads(fleet_path.read_text().strip())
        shared_row = json.loads(shared_path.read_text().strip())
        self.assertEqual(primary_row, shared_row,
                         "shared path must receive the SAME row as primary")

    def test_shared_fleet_path_not_given_skips_shared_write(self):
        """Without shared_fleet_path, fleet_burn_job writes ONLY the primary
        path — no crash, no error."""
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"

        wd.fleet_burn_job(
            _fleet_now(), {}, [], lambda *a, **k: None,
            fetch=lambda hs, hb: {},
            fleet_path=fleet_path,
        )
        self.assertTrue(fleet_path.exists())
        # no crash is the assertion

    def test_shared_fleet_path_dry_run_writes_nothing(self):
        """In dry_run mode, neither the primary NOR the shared path is written."""
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        shared_path = Path(tmp) / "shared" / "fleet.jsonl"
        shared_path.parent.mkdir(parents=True)

        wd.fleet_burn_job(
            _fleet_now(), {}, [], lambda *a, **k: None,
            fetch=lambda hs, hb: {},
            fleet_path=fleet_path,
            shared_fleet_path=shared_path,
            dry_run=True,
        )
        self.assertFalse(fleet_path.exists())
        self.assertFalse(shared_path.exists())

    def test_shared_fleet_path_failure_does_not_break_primary(self):
        """If writing to the shared path fails (e.g. permissions), the primary
        path must still be written and no exception raised."""
        tmp = tempfile_mkdtemp_cleanup(self)
        fleet_path = Path(tmp) / "fleet.jsonl"
        # Point shared_path to a non-existent dir without auto-create
        shared_path = Path(tmp) / "no-such-dir" / "deep" / "fleet.jsonl"

        wd.fleet_burn_job(
            _fleet_now(), {}, [], lambda *a, **k: None,
            fetch=lambda hs, hb: {},
            fleet_path=fleet_path,
            shared_fleet_path=shared_path,
        )
        self.assertTrue(fleet_path.exists(), "primary must succeed even if shared fails")
        self.assertFalse(shared_path.exists(), "shared should fail on missing parent dir")


# ---------------------------------------------------------------------------
# Docstring / comment checks — the coordinator must be documented as
# "controller" not "dev1".
# ---------------------------------------------------------------------------

class TestDocstringUpdates(unittest.TestCase):
    """run_once's docstring and the fleet_burn_job section comment must
    reference 'controller' instead of 'dev1' for the coordinator role."""

    def test_run_once_docstring_no_dev1_coordinator_reference(self):
        """run_once's docstring for jobs 16/19/35 must say 'controller',
        not 'dev1', for the coordinator role."""
        doc = wd.run_once.__doc__ or ""
        # The old text: "coordinator-only (cmd_watchdog wires this ONLY on dev1)"
        # and "only on dev1 — conformance_hb_enabled"
        self.assertNotIn("ONLY on dev1", doc,
                         "run_once docstring still references 'dev1' as coordinator")
        self.assertNotIn("only on dev1", doc,
                         "run_once docstring still references 'dev1' as coordinator")

    def test_fleet_burn_job_docstring_no_dev1(self):
        """fleet_burn_job's docstring must not reference 'dev1'."""
        doc = wd.fleet_burn_job.__doc__ or ""
        self.assertNotIn("dev1", doc,
                         "fleet_burn_job docstring still references 'dev1'")

    def test_cli_burn_coordinator_comment_no_dev1(self):
        """cli_burn.py's coordinator comment must say 'controller', not 'dev1'."""
        import cli_burn
        import inspect
        source = inspect.getsource(cli_burn.cmd_burn)
        self.assertNotIn("coordinator-only (dev1)", source,
                         "cli_burn.py still documents coordinator as 'dev1'")


# ---------------------------------------------------------------------------
# run_once shared_fleet_path parameter wiring
# ---------------------------------------------------------------------------

class TestRunOnceSharedFleetParam(unittest.TestCase):
    """run_once must accept shared_fleet_path and pass it to fleet_burn_job."""

    def test_run_once_accepts_shared_fleet_path(self):
        """run_once's signature must include shared_fleet_path."""
        import inspect
        sig = inspect.signature(wd.run_once)
        self.assertIn("shared_fleet_path", sig.parameters,
                       "run_once must accept shared_fleet_path (#971)")



# ---------------------------------------------------------------------------
# M3 (#971 follow-on) — sudo-probe dedup: `airuleset._probe_sudo()` must
# delegate to `watchdog.disk_guard._sudo_available()` (the disk-pressure
# guard's own `sudo -n true` probe) instead of a second, independent
# implementation of the identical check.
# ---------------------------------------------------------------------------

class TestProbeSudoDedup(unittest.TestCase):
    """airuleset._probe_sudo must reuse the shared disk_guard probe."""

    def test_probe_sudo_delegates_to_disk_guard(self):
        import airuleset
        from watchdog import disk_guard

        with mock.patch.object(disk_guard, "_sudo_available",
                               return_value=True) as fake:
            self.assertTrue(airuleset._probe_sudo())
            fake.assert_called_once()

        with mock.patch.object(disk_guard, "_sudo_available",
                               return_value=False) as fake:
            self.assertFalse(airuleset._probe_sudo())
            fake.assert_called_once()

    def test_probe_sudo_source_has_no_second_subprocess_run(self):
        """airuleset._probe_sudo's OWN body must not shell out to
        `sudo -n true` itself — that would be the duplicate implementation
        this dedup removes."""
        import inspect
        import airuleset
        source = inspect.getsource(airuleset._probe_sudo)
        self.assertNotIn('"sudo", "-n", "true"', source,
                         "_probe_sudo re-implements the sudo -n true probe "
                         "instead of delegating to disk_guard._sudo_available (#971 M3)")

if __name__ == "__main__":
    unittest.main()
