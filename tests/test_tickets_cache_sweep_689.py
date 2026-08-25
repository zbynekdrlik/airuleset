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


if __name__ == "__main__":
    unittest.main()
