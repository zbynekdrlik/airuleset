"""#689 — tickets-status cache hygiene: a dead-worktree cache entry (its `root`
gone from disk) or an entry older than a conservative window must be SWEPT.

Owner (2026-08-25): `~/.claude/tickets-status/` is a write-only per-cwd cache;
nothing ever deleted a `<cwd-key>.json` whose `root` (worktree) no longer exists
(gk box: 22 files, mostly dead `agent-*` worktree roots 18h–7d old). This is the
HYGIENE side; the collector-side freshness filter (#686) is a separate lane
(cli_webterm.py) — NOT touched here.

RED (`test_refresh_sweeps_dead_root_689`) drives the EXISTING write path
(`cmd_tickets_status --refresh`) so the failure is BEHAVIORAL (the dead entry
SURVIVES a refresh on the pre-#689 tree), never a bare AttributeError (#181).
"""

import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                         # noqa: E402
import statusbar                                         # noqa: E402


class _HomeIsolated(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-cache689-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        self.cache = statusbar.cache_dir()   # resolves under the isolated HOME
        self.cache.mkdir(parents=True, exist_ok=True)
        # a NON-repo cwd so cmd_tickets_status resolves root="" and makes ZERO
        # gh calls (the sweep runs before the repo block, unconditionally).
        self.nonrepo = Path(tempfile.mkdtemp(prefix="airuleset-nonrepo689-"))
        self.addCleanup(shutil.rmtree, self.nonrepo, True)

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    def _seed(self, name, data):
        p = self.cache / name
        p.write_text(json.dumps(data))
        return p


class TestRefreshSweepsDeadRoot(_HomeIsolated):
    def test_refresh_sweeps_dead_root_689(self):
        dead = self._seed("deadworktreekey.json",
                          {"root": str(self.home / "gone-worktree"),
                           "ts": time.time(), "open": 3})
        self.assertTrue(dead.exists())
        args = types.SimpleNamespace(refresh=True, cwd=str(self.nonrepo))
        airuleset.cmd_tickets_status(args)
        self.assertFalse(
            dead.exists(),
            "a cache entry whose root no longer exists must be swept on refresh")


class TestSweepStaleCache(_HomeIsolated):
    """The pure sweep function — dead-root + old-ts removed; live-root, guards,
    non-json, unparseable, symlinks all preserved; best-effort never raises."""

    def test_dead_root_removed(self):
        p = self._seed("dead.json",
                       {"root": str(self.home / "gone"), "ts": time.time()})
        removed = statusbar.sweep_stale_cache()
        self.assertFalse(p.exists())
        self.assertIn("dead.json", removed)

    def test_live_root_kept(self):
        live = self.home / "live-worktree"
        live.mkdir()
        p = self._seed("live.json", {"root": str(live), "ts": time.time()})
        statusbar.sweep_stale_cache()
        self.assertTrue(p.exists(), "a live worktree's entry must survive")

    def test_old_ts_removed_even_with_no_root(self):
        old = time.time() - statusbar.STALE_CACHE_MAX_AGE_S - 1000
        p = self._seed("old.json", {"root": "", "ts": old, "open": 2})
        statusbar.sweep_stale_cache()
        self.assertFalse(p.exists(), "an entry older than the window must be swept")

    def test_fresh_no_root_kept(self):
        # root="" (non-repo cwd) + fresh ts → neither arm fires → kept.
        p = self._seed("fresh.json", {"root": "", "ts": time.time(), "open": 1})
        statusbar.sweep_stale_cache()
        self.assertTrue(p.exists())

    def test_spawn_guard_and_non_json_untouched(self):
        # `.spawn-*` guards have no .json extension → never swept, even old.
        guard = self.cache / ".spawn-deadbeef0000"
        guard.write_text("")
        os.utime(guard, (0, 0))           # ancient
        other = self.cache / "notes.txt"
        other.write_text("x")
        statusbar.sweep_stale_cache()
        self.assertTrue(guard.exists(), "a .spawn-* guard must never be swept")
        self.assertTrue(other.exists(), "a non-.json file must never be swept")

    def test_unparseable_entry_left(self):
        bad = self.cache / "corrupt.json"
        bad.write_text("{ not json")
        statusbar.sweep_stale_cache()
        self.assertTrue(bad.exists(), "an unparseable entry is left (safe direction)")

    def test_symlink_entry_skipped(self):
        target = self.home / "target-worktree"   # exists → its own entry would be kept
        target.mkdir()
        link = self.cache / "link.json"
        try:
            link.symlink_to(target / "does-not-exist.json")
        except OSError:
            self.skipTest("symlinks unsupported")
        statusbar.sweep_stale_cache()
        self.assertTrue(link.is_symlink(), "a symlinked entry must be skipped")

    def test_missing_cache_dir_is_noop(self):
        shutil.rmtree(self.cache, ignore_errors=True)
        self.assertEqual(statusbar.sweep_stale_cache(), [])


if __name__ == "__main__":
    unittest.main()
