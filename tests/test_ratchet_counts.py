"""#842 net-drain ratchet counter leaf (ratchet_counts.py)."""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import ratchet_counts as rc  # noqa: E402


class TestThresholds(TestCase):
    def test_ratchet_blocks_at_and_above_parity(self):
        # #842 req 2: allowed ONLY while created < closed.
        self.assertTrue(rc.ratchet_blocks(0, 0))     # first filing of the day
        self.assertTrue(rc.ratchet_blocks(5, 5))     # parity blocks
        self.assertTrue(rc.ratchet_blocks(6, 5))     # inflating blocks
        self.assertFalse(rc.ratchet_blocks(4, 5))    # draining allows

    def test_footer_drift_only_strictly_inflating(self):
        # #842 req 6: ▲ only when created > closed (strictly), NOT at parity.
        self.assertTrue(rc.footer_drift(6, 5))
        self.assertFalse(rc.footer_drift(5, 5))
        self.assertFalse(rc.footer_drift(4, 5))


class TestDayBoundary(TestCase):
    def test_day_start_carries_local_offset(self):
        # A bare date is UTC in GitHub search; the offset MUST be present or the
        # day boundary shifts. astimezone() gives the box-local offset.
        now = datetime(2026, 9, 2, 15, 30, tzinfo=timezone(timedelta(hours=2)))
        s = rc._day_start_iso(now)
        self.assertTrue(s.startswith("2026-09-02T00:00:00"))
        # ends with a +HHMM / -HHMM offset (here +0200)
        self.assertRegex(s, r"[+-]\d{4}$")
        self.assertIn("+0200", s)


class _FakeGh:
    """Fake `gh` on PATH that answers `issue list … -q length` with a count.
    `created`/`closed` chosen by which `--search created:`/`closed:` appears.
    `fail=True` exits 1 for everything (a gh error)."""

    def __init__(self, tmp, created, closed, fail=False):
        bin_dir = Path(tmp) / "fakebin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "argv = sys.argv[1:]\n"
            "if %r:\n"
            "    sys.exit(1)\n"
            "joined = ' '.join(argv)\n"
            "if 'created:' in joined:\n"
            "    print(%d); sys.exit(0)\n"
            "if 'closed:' in joined:\n"
            "    print(%d); sys.exit(0)\n"
            "sys.exit(1)\n" % (fail, created, closed))
        (bin_dir / "gh").chmod(0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env.get("PATH", "")


class TestComputeCounts(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-ratchet-compute-")

    def test_parses_created_and_closed(self):
        gh = _FakeGh(self.tmp, created=3, closed=7)
        self.assertEqual(rc.compute_counts("o/r", self.tmp, gh_env=gh.env), (3, 7))

    def test_gh_error_is_none(self):
        gh = _FakeGh(self.tmp, created=3, closed=7, fail=True)
        self.assertIsNone(rc.compute_counts("o/r", self.tmp, gh_env=gh.env))


class TestCachedCounts(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-ratchet-cache-")
        self.home = tempfile.mkdtemp(prefix="airuleset-ratchet-home-")

    def _cache_file(self, repo="o/r"):
        return Path(rc._cache_path(repo, self.home))

    def test_refreshes_and_writes_cache(self):
        gh = _FakeGh(self.tmp, created=2, closed=9)
        got = rc.cached_counts("o/r", self.tmp, gh_env=gh.env, home=self.home)
        self.assertEqual(got[:2], (2, 9))
        data = json.loads(self._cache_file().read_text())
        self.assertEqual((data["created_today"], data["closed_today"]), (2, 9))

    def test_fresh_cache_served_without_gh(self):
        # Seed a fresh cache, then run with a FAILING gh — must serve the cache,
        # never call gh.
        today = rc._today_str()
        rc._write_cache("o/r", 4, 10, today, home=self.home)
        gh = _FakeGh(self.tmp, created=0, closed=0, fail=True)
        got = rc.cached_counts("o/r", self.tmp, gh_env=gh.env, home=self.home)
        self.assertEqual(got[:2], (4, 10))

    def test_stale_cache_triggers_refresh(self):
        # A cache older than TTL_S must refresh (new gh values win).
        today = rc._today_str()
        path = self._cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_today": 1, "closed_today": 1,
                                    "day": today, "ts": time.time() - rc.TTL_S - 5}))
        gh = _FakeGh(self.tmp, created=3, closed=8)
        got = rc.cached_counts("o/r", self.tmp, gh_env=gh.env, home=self.home)
        self.assertEqual(got[:2], (3, 8))

    def test_day_rolled_cache_triggers_refresh(self):
        # Yesterday's cache is stale regardless of ts freshness.
        path = self._cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_today": 99, "closed_today": 0,
                                    "day": "2000-01-01", "ts": time.time()}))
        gh = _FakeGh(self.tmp, created=1, closed=5)
        got = rc.cached_counts("o/r", self.tmp, gh_env=gh.env, home=self.home)
        self.assertEqual(got[:2], (1, 5))

    def test_gh_error_on_needed_refresh_is_none(self):
        # No cache + gh error -> None (the hook BLOCKS, fail-safe).
        gh = _FakeGh(self.tmp, created=0, closed=0, fail=True)
        got = rc.cached_counts("o/r", self.tmp, gh_env=gh.env, home=self.home)
        self.assertIsNone(got)

    def test_bump_created_increments_fresh_cache(self):
        today = rc._today_str()
        rc._write_cache("o/r", 3, 9, today, home=self.home)
        rc.bump_created("o/r", home=self.home)
        data = json.loads(self._cache_file().read_text())
        self.assertEqual(data["created_today"], 4)
        self.assertEqual(data["closed_today"], 9)

    def test_bump_created_noop_on_day_rolled_cache(self):
        path = self._cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_today": 3, "closed_today": 9,
                                    "day": "2000-01-01", "ts": time.time()}))
        rc.bump_created("o/r", home=self.home)
        data = json.loads(path.read_text())
        self.assertEqual(data["created_today"], 3)  # unchanged

    def test_bump_created_noop_without_cache(self):
        # No cache file -> silent no-op, never raises.
        rc.bump_created("o/r", home=self.home)
        self.assertFalse(self._cache_file().exists())


if __name__ == "__main__":
    main()
