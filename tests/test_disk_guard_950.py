"""Tests for #950 — per-user quota, shared Playwright, home-snapshot backstop."""
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


class TestHomeSnapshotDiscovery(unittest.TestCase):
    """#950-C: generalized home-directory snapshot backstop."""

    def test_home_snapshot_in_reclaimable_classes(self):
        from watchdog.disk_guard import RECLAIMABLE_CLASSES
        self.assertIn("home-snapshot", RECLAIMABLE_CLASSES)

    def test_workstation_returns_empty(self):
        """A workstation home is the owner's data — never swept."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        result = discover_snapshot_home_dirs(
            home="/tmp/fake", box_class_fn=lambda: "workstation")
        self.assertEqual(result, [])

    def test_dot_entries_excluded(self):
        """Dot-entries are structurally excluded."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dot_dir = Path(td) / ".cache"
            dot_dir.mkdir()
            for i in range(20):
                (dot_dir / ("f%d" % i)).write_text("x" * 100000)
            result = discover_snapshot_home_dirs(
                home=td, now=time.time(),
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100)
            paths = [r["path"] for r in result]
            self.assertNotIn(str(dot_dir), paths)

    def test_safe_names_excluded(self):
        """Named safe dirs (devel, uploads, snap, bin) are excluded."""
        from watchdog.disk_guard import discover_snapshot_home_dirs, HOME_SNAPSHOT_SAFE_NAMES
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for name in HOME_SNAPSHOT_SAFE_NAMES:
                d = Path(td) / name
                d.mkdir()
                for i in range(20):
                    (d / ("f%d" % i)).write_text("x" * 100000)
            result = discover_snapshot_home_dirs(
                home=td, now=time.time(),
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100)
            found_paths = [r["path"] for r in result]
            for name in HOME_SNAPSHOT_SAFE_NAMES:
                self.assertNotIn(str(Path(td) / name), found_paths)

    def test_git_shaped_deferred(self):
        """A directory with .git at its root is deferred to the worktree rung."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "cycles-wt"
            d.mkdir()
            (d / ".git").write_text("gitdir: /some/repo/.git/worktrees/x")
            for i in range(20):
                (d / ("f%d" % i)).write_text("x" * 100000)
            result = discover_snapshot_home_dirs(
                home=td, now=time.time(),
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100)
            git_results = [r for r in result if r["path"] == str(d)]
            self.assertEqual(len(git_results), 1)
            self.assertEqual(git_results[0]["kind"], "skip")
            self.assertIn("git-shaped", git_results[0]["reason"])

    def test_snapshot_old_enough_deleted(self):
        """A large non-git dir older than min_age_days is a delete candidate."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        import tempfile
        now = time.time()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "wt-5564"
            d.mkdir()
            for i in range(20):
                f = d / ("f%d" % i)
                f.write_text("x" * 100000)
                os.utime(str(f), (now - 86400 * 10, now - 86400 * 10))
            result = discover_snapshot_home_dirs(
                home=td, now=now,
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100,
                min_age_days=7)
            delete_results = [r for r in result
                              if r.get("kind") == "delete" and "wt-5564" in r["path"]]
            self.assertEqual(len(delete_results), 1)

    def test_recent_snapshot_kept(self):
        """A snapshot-shaped dir younger than min_age_days is kept."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        import tempfile
        now = time.time()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "recent-big"
            d.mkdir()
            for i in range(20):
                (d / ("f%d" % i)).write_text("x" * 100000)
            result = discover_snapshot_home_dirs(
                home=td, now=now,
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100,
                min_age_days=7)
            skip_results = [r for r in result
                            if r.get("kind") == "skip" and "recent-big" in r["path"]]
            self.assertEqual(len(skip_results), 1)
            self.assertIn("too recent", skip_results[0]["reason"])

    def test_keep_marker_respected(self):
        """An .airuleset-keep marker opts out of the sweep."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        import tempfile
        now = time.time()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "protected"
            d.mkdir()
            (d / ".airuleset-keep").write_text("")
            for i in range(20):
                f = d / ("f%d" % i)
                f.write_text("x" * 100000)
                os.utime(str(f), (now - 86400 * 10, now - 86400 * 10))
            result = discover_snapshot_home_dirs(
                home=td, now=now,
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100)
            keep_results = [r for r in result if "protected" in r["path"]]
            self.assertEqual(len(keep_results), 1)
            self.assertEqual(keep_results[0]["kind"], "skip")
            self.assertIn("keep marker", keep_results[0]["reason"])

    def test_symlink_not_followed(self):
        """A symlink entry is never followed or deleted."""
        from watchdog.disk_guard import discover_snapshot_home_dirs
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real"
            real.mkdir()
            link = Path(td) / "link-to-real"
            link.symlink_to(real)
            result = discover_snapshot_home_dirs(
                home=td, now=time.time(),
                box_class_fn=lambda: "shared-stream",
                file_threshold=10, size_threshold=100)
            link_results = [r for r in result if "link-to-real" in str(r.get("path", ""))]
            self.assertEqual(len(link_results), 0)


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

    def test_stream_env_block_has_playwright_path(self):
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH", STREAM_ENV_BASHRC_BLOCK)
        self.assertIn("/opt/ms-playwright", STREAM_ENV_BASHRC_BLOCK)

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


class TestHomePlannerInLadder(unittest.TestCase):
    """#950: home-snapshot planner is wired into the default drain ladder."""

    def test_home_snapshot_in_default_planners(self):
        from watchdog.disk_guard import _default_planners
        names = [n for n, _ in _default_planners("/tmp/fake", time.time())]
        self.assertIn("home-snapshot", names)


if __name__ == "__main__":
    unittest.main()
