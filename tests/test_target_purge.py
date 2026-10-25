"""`_target_in_live_use` — the shared mechanical live-use guard (#315).

The Tier-0 target/ purge this file used to lock was retired with the dev1
local-work doctrine (#1137); the live-use guard it introduced stays, shared by
the merged-lane target reclaim, the scratch sweeps and the disk guard.
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402


def _mkfakeproc(root, entries):
    """A fake `/proc`-shaped tree: `entries` is a list of dicts, each
    `{"pid": "1234", "exe": <target str or None>, "cwd": <target str or
    None>, "fds": [<target str>, ...]}`. Symlinks are DANGLING on purpose —
    `os.readlink` never needs the target to actually exist."""
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    for e in entries:
        pdir = proc / e["pid"]
        pdir.mkdir()
        if e.get("exe") is not None:
            os.symlink(e["exe"], pdir / "exe")
        if e.get("cwd") is not None:
            os.symlink(e["cwd"], pdir / "cwd")
        fdd = pdir / "fd"
        fdd.mkdir()
        for i, target in enumerate(e.get("fds", [])):
            os.symlink(target, fdd / str(i))
    return proc


# ---------------------------------------------------------------------------
# _target_in_live_use — the mechanical hot-swap/event guard
# ---------------------------------------------------------------------------

class TestTargetInLiveUse(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-live-use-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.target = self.root / "repo" / "target"
        self.target.mkdir(parents=True)

    def test_no_process_using_it_is_not_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "111", "exe": "/usr/bin/bash", "fds": ["/dev/null"]},
        ])
        self.assertFalse(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_exe_inside_target_is_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "222", "exe": str(self.target / "release" / "app")},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_open_fd_inside_target_is_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "333", "exe": "/usr/bin/bash",
             "fds": [str(self.target / "release" / "app.log")]},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_cwd_inside_target_is_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "444", "exe": "/usr/bin/bash", "cwd": str(self.target / "debug")},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_undeterminable_no_proc_dir_defaults_to_in_use(self):
        """No /proc-shaped dir at all -> can't determine -> SKIP (never
        purge on an unmeasurable answer) — approval-scope.md's mechanical
        substitute for asking the user "is there an event right now"."""
        self.assertTrue(airuleset._target_in_live_use(
            self.target, proc_dir=self.root / "no-such-proc"))

    def test_unrelated_target_dir_paths_are_not_confused(self):
        """A process using a DIFFERENT, similarly-named target dir (e.g.
        target-old/) must not false-positive via a bare string prefix
        match on the un-separated path."""
        sibling = self.root / "repo" / "target-old"
        sibling.mkdir()
        proc = _mkfakeproc(self.root, [
            {"pid": "555", "exe": str(sibling / "release" / "app")},
        ])
        self.assertFalse(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_cwd_exactly_equal_to_target_dir_is_in_use(self):
        """#315 adversarial-review finding 8: a process whose cwd/fd/exe
        link is EXACTLY `target_dir` (no trailing component) was missed —
        `"/repo/target".startswith("/repo/target/")` is False. A shell
        parked in target/, or a backup/file-manager process holding the
        bare directory open, must still count as in-use."""
        proc = _mkfakeproc(self.root, [
            {"pid": "666", "exe": "/usr/bin/bash", "cwd": str(self.target)},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))


if __name__ == "__main__":
    unittest.main()
