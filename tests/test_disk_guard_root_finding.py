"""Behaviour tests for the #841 leg C owner-daily root-level finding surface
(`watchdog/disk_guard_root.py`) + its wiring into Job 40.

The per-USER watchdog reads the root reporter's world-readable JSON, and at
CRITICAL pressure records a `disk-guard: root-level candidates` finding a
SESSION raises the owner-daily ❓ from — NEVER a ping (notify stays out of the
guard). Covers: the staleness guard (a dead root timer never paints a finding),
the threshold crossing, the once-per-fresh-report log line (not per poll), the
`asked_on` dedup preservation, the resolved-episode cache clear, the once-a-day
stale WARN, and the run_disk_guard wiring (critical → records; notice → not).

All reads are injected (`read_fn`) or use a temp HOME — no real /run report, no
real ~/.claude, no ping.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from watchdog import disk_guard_root as r          # noqa: E402
from watchdog import disk_guard as dg              # noqa: E402


def _report(now, est, gen_at="2026-09-02T00:00:00Z", candidates=None):
    return {"generated_at": gen_at, "generated_ts": now, "estimate_bytes": est,
            "candidates": candidates if candidates is not None else
            [{"cls": "apt-cache", "path": "/var/cache/apt", "bytes": est}]}


CRIT = {"level": "critical", "worst_pct": 92, "dim": "bytes"}


class TestReadRootReport(unittest.TestCase):

    def test_fresh_report_returned(self):
        now = 1_000_000
        rep = _report(now, 800 * 1024 * 1024)
        self.assertEqual(r.read_root_report(now, read_fn=lambda p: rep), rep)

    def test_stale_report_is_absent(self):
        now = 1_000_000
        old = _report(now - r.ROOT_REPORT_STALE_S - 100, 800 * 1024 * 1024)
        self.assertIsNone(r.read_root_report(now, read_fn=lambda p: old))

    def test_future_ts_is_absent(self):
        now = 1_000_000
        fut = _report(now + 10_000, 800 * 1024 * 1024)
        self.assertIsNone(r.read_root_report(now, read_fn=lambda p: fut))

    def test_unparseable_report_is_absent(self):
        self.assertIsNone(r.read_root_report(1_000_000, read_fn=lambda p: None))
        self.assertIsNone(r.read_root_report(1_000_000, read_fn=lambda p: "junk"))


class TestRecordFinding(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.td, ignore_errors=True))
        self.home = self.td

    def _cache(self):
        p = Path(self.home) / ".claude" / "disk-guard" / r.ROOT_CANDIDATES_NAME
        return json.load(open(p)) if p.exists() else None

    def test_crossing_threshold_writes_finding_and_logs(self):
        now = 1_000_000
        est = 800 * 1024 * 1024
        logs = r.maybe_record_root_finding(
            CRIT, self.home, now, read_fn=lambda p: _report(now, est))
        # the decision line is logged (owner-daily ❓ pending)
        self.assertTrue(any("root-level candidates" in ln for ln in logs))
        c = self._cache()
        self.assertIsNotNone(c)
        self.assertEqual(c["estimate_bytes"], est)
        self.assertIsNone(c["asked_on"])
        self.assertEqual(c["report_generated_at"], "2026-09-02T00:00:00Z")

    def test_below_threshold_records_nothing(self):
        now = 1_000_000
        small = 10 * 1024 * 1024
        logs = r.maybe_record_root_finding(
            CRIT, self.home, now, read_fn=lambda p: _report(now, small))
        self.assertEqual(logs, [])
        self.assertIsNone(self._cache())

    def test_resolved_episode_clears_the_finding(self):
        now = 1_000_000
        big, small = 800 * 1024 * 1024, 10 * 1024 * 1024
        r.maybe_record_root_finding(CRIT, self.home, now, read_fn=lambda p: _report(now, big))
        self.assertIsNotNone(self._cache())
        # root side now under threshold → the finding is cleared (no more asking)
        r.maybe_record_root_finding(CRIT, self.home, now + 60, read_fn=lambda p: _report(now + 60, small))
        self.assertIsNone(self._cache())

    def test_log_line_fires_once_per_fresh_report_not_per_poll(self):
        now = 1_000_000
        est = 800 * 1024 * 1024
        rep = _report(now, est)  # SAME generated_at across polls
        l1 = r.maybe_record_root_finding(CRIT, self.home, now, read_fn=lambda p: rep)
        l2 = r.maybe_record_root_finding(CRIT, self.home, now + 60, read_fn=lambda p: rep)
        self.assertTrue(any("root-level candidates" in x for x in l1))
        # same daily report on the next poll → NO new log line (no spam)
        self.assertEqual(l2, [])
        # but a NEW daily report re-logs
        rep2 = _report(now + 90000, est, gen_at="2026-09-03T00:00:00Z")
        l3 = r.maybe_record_root_finding(CRIT, self.home, now + 90000, read_fn=lambda p: rep2)
        self.assertTrue(any("root-level candidates" in x for x in l3))

    def test_asked_on_is_preserved_across_refreshes(self):
        now = 1_000_000
        est = 800 * 1024 * 1024
        rep = _report(now, est)
        r.maybe_record_root_finding(CRIT, self.home, now, read_fn=lambda p: rep)
        r.mark_asked(self.home, now=now, today="20260902")
        self.assertEqual(self._cache()["asked_on"], "20260902")
        # a later poll (same report) must NOT wipe the once/day dedup
        r.maybe_record_root_finding(CRIT, self.home, now + 60, read_fn=lambda p: rep)
        self.assertEqual(self._cache()["asked_on"], "20260902")

    def test_stale_report_warns_once_per_day(self):
        now = 1_000_000
        stale = _report(now - r.ROOT_REPORT_STALE_S - 100, 800 * 1024 * 1024)
        l1 = r.maybe_record_root_finding(CRIT, self.home, now, read_fn=lambda p: stale)
        self.assertTrue(any("root disk-guard report absent/stale" in x for x in l1))
        # second poll SAME day → no repeat WARN
        l2 = r.maybe_record_root_finding(CRIT, self.home, now + 60, read_fn=lambda p: stale)
        self.assertEqual(l2, [])

    def test_dry_run_writes_nothing(self):
        now = 1_000_000
        est = 800 * 1024 * 1024
        r.maybe_record_root_finding(CRIT, self.home, now, read_fn=lambda p: _report(now, est),
                                    dry_run=True)
        self.assertIsNone(self._cache())


class TestReadFinding(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.td, ignore_errors=True))
        self.home = self.td

    def test_fresh_finding_returned_for_session(self):
        now = 1_000_000
        r.maybe_record_root_finding(CRIT, self.home, now,
                                    read_fn=lambda p: _report(now, 800 * 1024 * 1024))
        f = r.read_finding(self.home, now + 60)
        self.assertIsNotNone(f)
        self.assertGreaterEqual(f["estimate_bytes"], r.FINDING_THRESHOLD_BYTES)

    def test_stale_finding_is_not_offered(self):
        now = 1_000_000
        r.maybe_record_root_finding(CRIT, self.home, now,
                                    read_fn=lambda p: _report(now, 800 * 1024 * 1024))
        # a dead Job 40 leaves the finding stale → the session must not ask
        self.assertIsNone(r.read_finding(self.home, now + r.ROOT_REPORT_STALE_S + 100))

    def test_no_finding_returns_none(self):
        self.assertIsNone(r.read_finding(self.home, 1_000_000))


class TestRunDiskGuardWiring(unittest.TestCase):
    """The Job-40 wiring: critical → the finding recorder runs; notice → not."""

    def _statvfs(self, used_pct):
        class S:
            f_blocks = 1000
            f_bfree = int(1000 * (100 - used_pct) / 100)
            f_bavail = int(1000 * (100 - used_pct) / 100)
            f_files = 1000
            f_ffree = 900
        return lambda _p: S()

    def test_critical_invokes_the_root_finding_recorder(self):
        calls = []
        orig = r.maybe_record_root_finding
        r.maybe_record_root_finding = lambda *a, **k: calls.append(a) or []
        try:
            with tempfile.TemporaryDirectory() as td:
                dg.run_disk_guard(
                    now=1_000_000, home=td, dry_run=True,
                    statvfs_fn=self._statvfs(95), dev_fn=lambda p: 1,
                    geteuid_fn=lambda: 1000, mounts=("/",))
        finally:
            r.maybe_record_root_finding = orig
        self.assertEqual(len(calls), 1, "root-finding recorder not called at critical")

    def test_notice_does_not_invoke_the_recorder(self):
        calls = []
        orig = r.maybe_record_root_finding
        r.maybe_record_root_finding = lambda *a, **k: calls.append(a) or []
        try:
            with tempfile.TemporaryDirectory() as td:
                dg.run_disk_guard(
                    now=1_000_000, home=td, dry_run=True,
                    statvfs_fn=self._statvfs(78), dev_fn=lambda p: 1,
                    geteuid_fn=lambda: 1000, mounts=("/",))
        finally:
            r.maybe_record_root_finding = orig
        self.assertEqual(calls, [], "recorder must not run below critical")

    def test_drain_level_does_not_invoke_the_recorder(self):
        # 85% = DRAIN (80-89), NOT critical — gives the `== "critical"` gate
        # teeth against a mutant widening it to include drain (85% is past the
        # ok/notice early return, so this exercises the critical gate itself).
        calls = []
        orig = r.maybe_record_root_finding
        r.maybe_record_root_finding = lambda *a, **k: calls.append(a) or []
        try:
            with tempfile.TemporaryDirectory() as td:
                dg.run_disk_guard(
                    now=1_000_000, home=td, dry_run=True,
                    statvfs_fn=self._statvfs(85), dev_fn=lambda p: 1,
                    geteuid_fn=lambda: 1000, mounts=("/",))
        finally:
            r.maybe_record_root_finding = orig
        self.assertEqual(calls, [], "recorder must run ONLY at critical, not drain")


class TestSessionCLI(unittest.TestCase):
    """`airuleset.py disk-guard-root` — the session-facing reader/mark-asked."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.td, ignore_errors=True))
        # HOME isolation (saved + restored per the #385 HOME-leak discipline).
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = self.td
        self.addCleanup(self._restore_home)
        import cli_disk_guard_root as cdgr
        self.cdgr = cdgr

    def _restore_home(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home

    def _args(self, **kw):
        class A:
            mark_asked = kw.get("mark_asked", False)
            json = kw.get("json", False)
        return A()

    def _capture(self, args):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cdgr.cmd_disk_guard_root(args)
        return rc, buf.getvalue()

    def test_none_when_no_finding(self):
        rc, out = self._capture(self._args())
        self.assertEqual(rc, 0)
        self.assertIn("none", out)

    def test_prints_finding_and_marks_asked(self):
        now = time.time()
        r.maybe_record_root_finding(CRIT, self.td, now,
                                    read_fn=lambda p: _report(now, 800 * 1024 * 1024))
        rc, out = self._capture(self._args())
        self.assertEqual(rc, 0)
        self.assertIn("FINDING", out)
        self.assertIn("owner-daily", out)      # the session-prompt hint
        # mark asked, then the hint disappears
        _rc, mout = self._capture(self._args(mark_asked=True))
        self.assertIn("marked asked_on", mout)
        _rc2, out2 = self._capture(self._args())
        self.assertNotIn("should raise ONE owner-daily", out2)


class TestCliRegistration(unittest.TestCase):
    def test_subcommand_is_registered(self):
        import airuleset
        self.assertIn("disk-guard-root", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["disk-guard-root"],
                      airuleset.cmd_disk_guard_root)


if __name__ == "__main__":
    unittest.main()
