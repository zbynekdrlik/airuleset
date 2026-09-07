"""RED tests for #925 — owner-ruling phase: eliminate Claude-generated waste
on shared-stream boxes.

Four behavioural points:
1. Extended TMP_TEST_PREFIXES (npmcache-*, tmp*) + shared-stream 3h age
2. Shared-stream scratch age-out = 3h (not 7d)
3. Doctrine update citing owner ruling
4. Transcript gzip-at-rest = 2d on shared-stream (not 7d)
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watchdog.disk_guard as dg  # noqa: E402
import cli_scratch_sweep as css  # noqa: E402


DAY = 86400.0


# --------------------------------------------------------------------------- #
# Point 1: Extended TMP_TEST_PREFIXES + box-class-aware age
# --------------------------------------------------------------------------- #
class TestExtendedTmpTestPrefixes(unittest.TestCase):
    """#925 point 1: TMP_TEST_PREFIXES must include npmcache-* and tmp*."""

    def test_npmcache_prefix_present(self):
        self.assertIn("npmcache-", dg.TMP_TEST_PREFIXES)

    def test_tmp_prefix_present(self):
        self.assertIn("tmp", dg.TMP_TEST_PREFIXES)

    def test_jest_prefix_still_present(self):
        self.assertIn("jest_", dg.TMP_TEST_PREFIXES)

    def test_pytest_prefix_still_present(self):
        self.assertIn("pytest-of-", dg.TMP_TEST_PREFIXES)


class TestTmpTestDiscoveryNpmcache(unittest.TestCase):
    """#925: discover_stale_tmp_test_dirs must find npmcache-* dirs."""

    def test_finds_old_npmcache_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            npm = os.path.join(tmp, "npmcache-abc")
            os.makedirs(npm)
            os.utime(npm, (0, time.time() - 2 * DAY))
            rows = dg.discover_stale_tmp_test_dirs(
                tmp_dir=tmp, now=time.time(), min_age_days=1,
                uid=os.getuid())
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertTrue(
                any("npmcache-abc" in r["path"] for r in candidates),
                "npmcache-abc should be a genuine candidate")

    def test_finds_old_tmp_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            td = os.path.join(tmp, "tmpXXXlonger")
            os.makedirs(td)
            os.utime(td, (0, time.time() - 2 * DAY))
            rows = dg.discover_stale_tmp_test_dirs(
                tmp_dir=tmp, now=time.time(), min_age_days=1,
                uid=os.getuid())
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertTrue(
                any("tmpXXXlonger" in r["path"] for r in candidates),
                "tmpXXXlonger should be a genuine candidate")


class TestSharedStreamTmpTestAge(unittest.TestCase):
    """#925: on a shared-stream box, the tmp-test age floor is 3h, not 1d."""

    def test_shared_stream_age_3h(self):
        age = dg._effective_tmp_test_age_days(
            box_class_fn=lambda: "shared-stream")
        self.assertAlmostEqual(age, 3.0 / 24.0, places=4,
                               msg="shared-stream age floor should be 3h")

    def test_workstation_age_1d(self):
        age = dg._effective_tmp_test_age_days(
            box_class_fn=lambda: "workstation")
        self.assertEqual(age, 1,
                         msg="workstation age floor should be 1d")

    def test_explicit_min_age_wins(self):
        age = dg._effective_tmp_test_age_days(
            min_age_days=5, box_class_fn=lambda: "shared-stream")
        self.assertEqual(age, 5,
                         msg="explicit min_age_days should override box class")

    def test_shared_stream_discovers_4h_old_dir(self):
        """A dir 4h old is past the 3h floor on a shared-stream box."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            jest = os.path.join(tmp, "jest_4hdir")
            os.makedirs(jest)
            now = time.time()
            os.utime(jest, (0, now - 4 * 3600))  # 4h old
            rows = dg.discover_stale_tmp_test_dirs(
                tmp_dir=tmp, now=now, uid=os.getuid(),
                box_class_fn=lambda: "shared-stream")
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertTrue(
                any("jest_4hdir" in r["path"] for r in candidates),
                "4h-old dir should be a candidate on shared-stream (3h floor)")

    def test_workstation_skips_4h_old_dir(self):
        """A dir 4h old is too recent on a workstation (1d floor)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            jest = os.path.join(tmp, "jest_4hdir")
            os.makedirs(jest)
            now = time.time()
            os.utime(jest, (0, now - 4 * 3600))  # 4h old
            rows = dg.discover_stale_tmp_test_dirs(
                tmp_dir=tmp, now=now, uid=os.getuid(),
                box_class_fn=lambda: "workstation")
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertFalse(
                any("jest_4hdir" in r["path"] for r in candidates),
                "4h-old dir should NOT be a candidate on workstation (1d floor)")


# --------------------------------------------------------------------------- #
# Point 2: Shared-stream scratch age-out = 3h
# --------------------------------------------------------------------------- #
class TestSharedStreamScratchAge(unittest.TestCase):
    """#925 point 2: scratch age-out constant exists and is 3h."""

    def test_constant_exists(self):
        self.assertTrue(
            hasattr(css, "CLAUDE_SCRATCH_MIN_AGE_HOURS_SHARED_STREAM"),
            "CLAUDE_SCRATCH_MIN_AGE_HOURS_SHARED_STREAM must exist")

    def test_constant_value_3h(self):
        self.assertEqual(css.CLAUDE_SCRATCH_MIN_AGE_HOURS_SHARED_STREAM, 3)

    def test_effective_scratch_age_shared_stream(self):
        age = dg._effective_scratch_age_days(
            box_class_fn=lambda: "shared-stream")
        self.assertAlmostEqual(age, 3.0 / 24.0, places=4,
                               msg="shared-stream scratch age should be 3h")

    def test_effective_scratch_age_workstation(self):
        age = dg._effective_scratch_age_days(
            box_class_fn=lambda: "workstation")
        self.assertEqual(age, css.CLAUDE_SCRATCH_MIN_AGE_DAYS_DEFAULT,
                         msg="workstation scratch age should be 7d default")


# --------------------------------------------------------------------------- #
# Point 3: Doctrine update
# --------------------------------------------------------------------------- #
class TestDoctrineUpdate(unittest.TestCase):
    """#925 point 3: the #778 module carries the owner ruling citation."""

    def test_module_mentions_925(self):
        mod_path = os.path.join(
            os.path.dirname(__file__), "..",
            "modules", "quality", "no-local-builds.md")
        text = open(mod_path).read()
        self.assertIn("#925", text)
        self.assertIn("MUST clean", text,
                      "doctrine must state runs MUST clean tmp")


# --------------------------------------------------------------------------- #
# Point 4: Transcript gzip-at-rest = 2d on shared-stream
# --------------------------------------------------------------------------- #
class TestSharedStreamTranscriptAge(unittest.TestCase):
    """#925 point 4: transcript gzip age = 2d on shared-stream, 7d default."""

    def test_constant_exists(self):
        self.assertTrue(
            hasattr(dg, "TRANSCRIPT_PRESSURE_MIN_AGE_DAYS_SHARED_STREAM"),
            "TRANSCRIPT_PRESSURE_MIN_AGE_DAYS_SHARED_STREAM must exist")

    def test_constant_value_2d(self):
        self.assertEqual(dg.TRANSCRIPT_PRESSURE_MIN_AGE_DAYS_SHARED_STREAM, 2)

    def test_effective_transcript_age_shared_stream(self):
        age = dg._effective_transcript_age_days(
            box_class_fn=lambda: "shared-stream")
        self.assertEqual(age, 2,
                         msg="shared-stream transcript age should be 2d")

    def test_effective_transcript_age_workstation(self):
        age = dg._effective_transcript_age_days(
            box_class_fn=lambda: "workstation")
        self.assertEqual(age, 7,
                         msg="workstation transcript age should be 7d")

    def test_shared_stream_3h_constant_in_disk_guard(self):
        """The TMP_TEST_MIN_AGE_HOURS_SHARED_STREAM constant must exist."""
        self.assertTrue(
            hasattr(dg, "TMP_TEST_MIN_AGE_HOURS_SHARED_STREAM"))
        self.assertEqual(dg.TMP_TEST_MIN_AGE_HOURS_SHARED_STREAM, 3)


if __name__ == "__main__":
    unittest.main()
