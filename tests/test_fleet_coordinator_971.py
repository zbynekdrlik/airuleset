"""#971 — fleet-burn coordinator cutover: role-based gating + shared feed.

RED tests — these MUST FAIL before the fix and PASS after.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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

        logs = wd.fleet_burn_job(
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

        logs = wd.fleet_burn_job(
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

        logs = wd.fleet_burn_job(
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

        logs = wd.fleet_burn_job(
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


if __name__ == "__main__":
    unittest.main()
