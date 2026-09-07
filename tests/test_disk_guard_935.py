"""Tests for #935 — disk_guard: work-products snapshot backstop + /tmp age-out
gap fix.

Part A DROPPED per Fable review H1: the existing `worktree` rung already covers
project-local worktrees via `_checkout_roots` walking all of $HOME to depth 6.

Part B: work-products snapshot backstop
Part C: /tmp age-out: recursive newest-mtime instead of top-level st_mtime
"""

import os
import time
import unittest

import watchdog.disk_guard as dg


# --------------------------------------------------------------------------- #
# Part B — work-products snapshot backstop
# --------------------------------------------------------------------------- #
class TestWorkProductsSnapshotClass(unittest.TestCase):
    """The new rung class must be in RECLAIMABLE_CLASSES."""

    def test_work_products_snapshot_in_fence(self):
        self.assertIn("work-products-snapshot", dg.RECLAIMABLE_CLASSES)


class TestDiscoverSnapshotWorkProducts(unittest.TestCase):
    """discover_snapshot_work_products must find large subtrees (>10k files
    OR >1G) and age-out after 7 days. Small content is never touched."""

    def _make_big_subtree(self, wp_dir, name, file_count, age_days):
        """Create a subtree under work-products/ with N files."""
        sub = wp_dir / name
        sub.mkdir(parents=True, exist_ok=True)
        old_time = time.time() - age_days * 86400
        for i in range(file_count):
            f = sub / ("file_%05d.txt" % i)
            f.write_text("x" * 100)
            os.utime(str(f), (old_time, old_time))
        os.utime(str(sub), (old_time, old_time))
        return sub

    def test_finds_old_large_file_count_snapshot(self):
        """A subtree with >threshold files, older than 7d -> candidate."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wp = tmp_path / ".claude" / "work-products"
            wp.mkdir(parents=True)
            self._make_big_subtree(wp, "big-snapshot", 15, age_days=10)
            rows = dg.discover_snapshot_work_products(
                home=str(tmp_path), now=time.time(),
                min_age_days=7, file_threshold=10, size_threshold=10**12)
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertTrue(
                any("big-snapshot" in r["path"] for r in candidates),
                "Large old snapshot should be a candidate; rows=%r" % rows)

    def test_keeps_small_subtree(self):
        """A subtree with few files (doc-like) -> never touched."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wp = tmp_path / ".claude" / "work-products"
            wp.mkdir(parents=True)
            self._make_big_subtree(wp, "small-doc", 3, age_days=10)
            rows = dg.discover_snapshot_work_products(
                home=str(tmp_path), now=time.time(),
                min_age_days=7, file_threshold=10, size_threshold=10**12)
            self.assertEqual(len(rows), 0,
                             "Small doc-like subtree should be ignored")

    def test_keeps_recent_snapshot(self):
        """A large subtree younger than 7d -> kept."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wp = tmp_path / ".claude" / "work-products"
            wp.mkdir(parents=True)
            self._make_big_subtree(wp, "fresh-snapshot", 15, age_days=1)
            rows = dg.discover_snapshot_work_products(
                home=str(tmp_path), now=time.time(),
                min_age_days=7, file_threshold=10, size_threshold=10**12)
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertEqual(len(candidates), 0,
                             "Recent snapshot should be kept")

    def test_finds_large_size_snapshot(self):
        """A subtree exceeding the SIZE threshold -> candidate."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wp = tmp_path / ".claude" / "work-products"
            wp.mkdir(parents=True)
            sub = wp / "huge-sub"
            sub.mkdir()
            bigfile = sub / "bigdata.bin"
            bigfile.write_bytes(b"x" * 2000)
            old = time.time() - 10 * 86400
            os.utime(str(bigfile), (old, old))
            rows = dg.discover_snapshot_work_products(
                home=str(tmp_path), now=time.time(),
                min_age_days=7, file_threshold=10**6,
                size_threshold=1000)  # 1000 bytes threshold
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertTrue(
                any("huge-sub" in r["path"] for r in candidates),
                "Large-size subtree should be a candidate")

    def test_keeps_with_airuleset_keep_marker(self):
        """A large old snapshot with .airuleset-keep marker -> kept (#935 H3)."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wp = tmp_path / ".claude" / "work-products"
            wp.mkdir(parents=True)
            sub = self._make_big_subtree(wp, "kept-snapshot", 15, age_days=10)
            (sub / ".airuleset-keep").write_text("keep")
            rows = dg.discover_snapshot_work_products(
                home=str(tmp_path), now=time.time(),
                min_age_days=7, file_threshold=10, size_threshold=10**12)
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertEqual(len(candidates), 0,
                             ".airuleset-keep should prevent sweep")
            # Verify a skip row with keep reason exists
            skips = [r for r in rows if r.get("reason") and "keep marker" in r["reason"]]
            self.assertTrue(len(skips) > 0,
                            "Should have a skip row with keep marker reason")

    def test_keeps_with_no_sweep_marker(self):
        """A large old snapshot with .no-sweep marker -> kept (#861 parity)."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wp = tmp_path / ".claude" / "work-products"
            wp.mkdir(parents=True)
            sub = self._make_big_subtree(wp, "nosweep-snap", 15, age_days=10)
            (sub / ".no-sweep").write_text("keep")
            rows = dg.discover_snapshot_work_products(
                home=str(tmp_path), now=time.time(),
                min_age_days=7, file_threshold=10, size_threshold=10**12)
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertEqual(len(candidates), 0,
                             ".no-sweep should prevent sweep")


# --------------------------------------------------------------------------- #
# Part C — /tmp age-out: recursive newest-mtime
# --------------------------------------------------------------------------- #
class TestTmpTestDirRecursiveMtime(unittest.TestCase):
    """#935-C: discover_stale_tmp_test_dirs must use the NEWEST file mtime
    inside the dir (recursive walk), NOT the dir entry's own st_mtime.

    The RED test: a dir whose own mtime is recent (touched by adding a file)
    but whose CONTENT is old. Before the fix, the old content-age would be
    masked by the fresh dir mtime and the dir would be kept. After the fix,
    the content-age is what matters.
    """

    def test_dir_fresh_mtime_but_stale_content_is_swept(self):
        """A dir whose entry-mtime is fresh but inner files are old -> candidate.
        This is the #935-C regression test: before the fix, st.st_mtime
        (the dir entry's mtime) was used, making this dir appear fresh.
        After the fix, _safe_dir_newest_mtime (recursive) finds the old file.
        """
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jest = tmp_path / "jest_staletest"
            jest.mkdir()
            old_file = jest / "test_result.json"
            old_file.write_text('{"pass": true}')
            old_time = time.time() - 5 * 86400  # 5 days old
            os.utime(str(old_file), (old_time, old_time))
            now = time.time()
            os.utime(str(jest), (now, now))
            rows = dg.discover_stale_tmp_test_dirs(
                tmp_dir=str(tmp_path), now=now, min_age_days=2,
                uid=os.getuid())
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertTrue(
                any("jest_staletest" in r["path"] for r in candidates),
                "Dir with stale content (but fresh dir-mtime) should be swept; "
                "rows=%r" % rows)

    def test_dir_with_genuinely_fresh_content_is_kept(self):
        """A dir with a genuinely fresh file inside -> kept."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jest = tmp_path / "jest_freshtest"
            jest.mkdir()
            fresh_file = jest / "output.log"
            fresh_file.write_text("recent output")
            rows = dg.discover_stale_tmp_test_dirs(
                tmp_dir=str(tmp_path), now=time.time(), min_age_days=2,
                uid=os.getuid())
            candidates = [r for r in rows if r.get("reason") is None]
            self.assertFalse(
                any("jest_freshtest" in r["path"] for r in candidates),
                "Dir with fresh content should be kept")


class TestSafeDirNewestMtime(unittest.TestCase):
    """Unit tests for _safe_dir_newest_mtime helper (#935-C)."""

    def test_returns_newest_file_mtime(self):
        """Returns the mtime of the newest regular file in the tree."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sub = tmp_path / "sub"
            sub.mkdir()
            old = tmp_path / "old.txt"
            old.write_text("old")
            old_time = time.time() - 10 * 86400
            os.utime(str(old), (old_time, old_time))
            new = sub / "new.txt"
            new.write_text("new")
            new_time = time.time() - 1 * 86400
            os.utime(str(new), (new_time, new_time))
            result = dg._safe_dir_newest_mtime(str(tmp_path))
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result, new_time, delta=1.0)

    def test_empty_dir_returns_dir_mtime(self):
        """An empty dir returns its own mtime (#355 fallback)."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            empty = tmp_path / "empty"
            empty.mkdir()
            result = dg._safe_dir_newest_mtime(str(empty))
            self.assertIsNotNone(result)
            dir_mtime = os.lstat(str(empty)).st_mtime
            self.assertAlmostEqual(result, dir_mtime, delta=1.0)

    def test_nonexistent_returns_none(self):
        """A nonexistent path returns None (fail-safe keep)."""
        result = dg._safe_dir_newest_mtime("/nonexistent/path/abc123")
        self.assertIsNone(result)


# --------------------------------------------------------------------------- #
# Ladder wiring
# --------------------------------------------------------------------------- #
class TestLadderContainsNewRungs(unittest.TestCase):
    """The new rungs must appear in _default_planners."""

    def test_work_products_snapshot_in_ladder(self):
        """work-products-snapshot rung must be in the default ladder."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            planners = dg._default_planners(str(tmp), time.time())
            labels = [label for label, _fn in planners]
            self.assertIn("work-products-snapshot", labels)

    def test_project_worktree_not_in_ladder(self):
        """project-worktree should NOT be in the ladder (dropped per review H1:
        the existing worktree rung already covers project-local worktrees)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            planners = dg._default_planners(str(tmp), time.time())
            labels = [label for label, _fn in planners]
            self.assertNotIn("project-worktree", labels)


if __name__ == "__main__":
    unittest.main()
