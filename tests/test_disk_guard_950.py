"""Tests for #950 — per-user quota, shared Playwright.

The home-snapshot backstop was REMOVED by #1380 (owner 20.11.2026): a
top-level home dir is owner data, never a drain."""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQuotaConstants(unittest.TestCase):
    """#950-A: quota constants and rendering."""

    def test_quota_constants_exist(self):
        from cli_resource_guards import QUOTA_SOFT_KIB, QUOTA_HARD_KIB, QUOTA_GRACE_S
        self.assertEqual(QUOTA_SOFT_KIB, 8 * 1024 * 1024)
        self.assertEqual(QUOTA_HARD_KIB, 10 * 1024 * 1024)
        self.assertEqual(QUOTA_GRACE_S, 86400)

    def test_quota_unit_rendered(self):
        from cli_resource_guards import render_quota_unit
        body = render_quota_unit()
        self.assertIn("[Unit]", body)
        self.assertIn("usrjquota=aquota.user", body)
        self.assertIn("quotaon", body)
        self.assertIn("ConditionPathExists=/aquota.user", body)

    def test_guard_files_includes_quota_unit(self):
        from cli_resource_guards import guard_files, QUOTA_UNIT_PATH
        paths = [p for p, _ in guard_files()]
        self.assertIn(QUOTA_UNIT_PATH, paths)

    def test_apply_script_contains_quota_section(self):
        from cli_resource_guards import build_apply_script
        script = build_apply_script()
        self.assertIn("ext4 disk quota", script)
        self.assertIn("setquota", script)
        self.assertIn("quotaon", script)
        self.assertIn("repquota", script)
        # Must NOT contain tune2fs -O quota (refused on mounted root)
        self.assertNotIn("tune2fs -O quota", script)

    def test_apply_script_contains_playwright_section(self):
        from cli_resource_guards import build_apply_script, PLAYWRIGHT_SHARED_PATH
        script = build_apply_script()
        self.assertIn("shared Playwright", script)
        self.assertIn(PLAYWRIGHT_SHARED_PATH, script)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", script)


class TestQuotaSegment(unittest.TestCase):
    """#950: the quota NN% footer segment."""

    def test_quota_segment_hidden_below_90(self):
        from statusbar import quota_segment
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as td:
            dg = Path(td) / ".claude" / "disk-guard"
            dg.mkdir(parents=True)
            (dg / "status.json").write_text(json.dumps({
                "quota_pct": 70, "ts": time.time(), "worst_pct": 50}))
            result = quota_segment(home=td, now=time.time())
            self.assertEqual(result, "")

    def test_quota_segment_shown_at_90(self):
        from statusbar import quota_segment
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as td:
            dg = Path(td) / ".claude" / "disk-guard"
            dg.mkdir(parents=True)
            (dg / "status.json").write_text(json.dumps({
                "quota_pct": 92, "ts": time.time(), "worst_pct": 50}))
            result = quota_segment(home=td, now=time.time())
            self.assertIn("quota 92%", result)

    def test_quota_segment_hidden_when_missing(self):
        from statusbar import quota_segment
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as td:
            dg = Path(td) / ".claude" / "disk-guard"
            dg.mkdir(parents=True)
            (dg / "status.json").write_text(json.dumps({
                "worst_pct": 50, "ts": time.time()}))
            result = quota_segment(home=td, now=time.time())
            self.assertEqual(result, "")


class TestStreamEnvBashrcBlock(unittest.TestCase):
    """#950-B: shared-stream env marker block."""

    def test_stream_env_block_exports_the_per_user_playwright_cache(self):
        # #1048 REVERSES #950's /opt export: a no-sudo shared-stream box exports
        # the per-user cache (the location airuleset installs the pinned chromium
        # into), NEVER the root-owned /opt copy with its mismatched build. Assert
        # on the ACTIVE `export` lines (a /opt mention in a comment is fine).
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", STREAM_ENV_BASHRC_BLOCK)
        exports = [ln for ln in STREAM_ENV_BASHRC_BLOCK.splitlines()
                   if ln.strip().startswith("export")
                   and "PLAYWRIGHT_BROWSERS_PATH" in ln]
        self.assertTrue(exports)
        for ln in exports:
            self.assertIn(".cache/ms-playwright", ln)
            self.assertNotIn("/opt/ms-playwright", ln)

    def test_stream_env_markers(self):
        from cli_bashrc_appliers import (STREAM_ENV_MARK_START,
                                          STREAM_ENV_MARK_END,
                                          STREAM_ENV_BASHRC_BLOCK)
        self.assertTrue(STREAM_ENV_BASHRC_BLOCK.startswith(STREAM_ENV_MARK_START))
        self.assertTrue(STREAM_ENV_BASHRC_BLOCK.endswith(STREAM_ENV_MARK_END))


class TestReadQuotaPct(unittest.TestCase):
    """#950: _read_quota_pct function."""

    def test_returns_none_when_quota_not_available(self):
        from watchdog.disk_guard import _read_quota_pct
        result = _read_quota_pct()
        self.assertTrue(result is None or isinstance(result, int))


if __name__ == "__main__":
    unittest.main()
