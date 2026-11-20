"""Tests for #935 — disk_guard: /tmp age-out gap fix.

Part A DROPPED per Fable review H1: the existing `worktree` rung already covers
project-local worktrees via `_checkout_roots` walking all of $HOME to depth 6.

Part B (work-products snapshot backstop) was REMOVED by #1380 (owner
20.11.2026): ~/.claude/work-products is the durable store, never a drain.
Part C: /tmp age-out: recursive newest-mtime instead of top-level st_mtime
"""

import os
import time
import unittest

import watchdog.disk_guard as dg


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
