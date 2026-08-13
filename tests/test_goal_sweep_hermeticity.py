"""#437 regression proof: `tests/test_goal_sweep.py`'s own `goal.goal_sweep()`
calls must never resolve `goal.goal_sync_log_path()` to the REAL
`~/.claude/goal-sync.log` -- live-reproduced on dev1 (2026-08-13): running
this file's own `TestGoalSweep` class with an unmodified `$HOME` appended
real `sess-sweep-*`/`cwd=/home/.../goalsweep` fixture lines to the
production log (the incident #437 was filed from), displacing genuine
forensic history out of the log's own bounded `GOAL_SYNC_LOG_LINES_MAX`
window.

Deliberately a SEPARATE file from `test_goal_sweep.py` itself: this test
spawns a REAL subprocess re-discovering the WHOLE `test_goal_sweep.py` file
(every class, not just `TestGoalSweep` -- a future class reintroducing the
same unisolated `goal.goal_sweep()`/`goal.deliver_goal()` call must be
caught too), and a hermeticity check living INSIDE the file it re-discovers
would recursively re-spawn itself forever.

Must hold under BOTH `pytest` and bare `python3 -m unittest discover`
(#427 class -- conftest.py-only isolation is NOT enough, since bare
`unittest discover` never reads conftest.py at all): the fix this test
proves is a plain `unittest.mock.patch.object` call made from inside
`TestCase.setUp()`, which both runners invoke identically -- so THIS test
itself needs no special runner-awareness either.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class GoalSweepFileNeverWritesOutsideItsIsolatedHome(unittest.TestCase):
    def test_the_whole_file_stays_inside_an_isolated_home(self):
        scratch = tempfile.mkdtemp(prefix="goalsweep-hermeticity-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        env = dict(os.environ)
        env["HOME"] = scratch
        env["AIRULESET_TEST_IGNORE_DISABLE"] = "1"
        r = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests",
             "-p", "test_goal_sweep.py"],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
            env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        synclog = Path(scratch) / ".claude" / "goal-sync.log"
        self.assertFalse(
            synclog.exists(),
            "tests/test_goal_sweep.py wrote a REAL-shaped goal-sync.log "
            "outside its isolated HOME -- some test's call into "
            "goal.deliver_goal()/goal.goal_sweep() resolved "
            "goal.goal_sync_log_path() to a real path instead of an "
            "isolated one (#437):\n%s"
            % (synclog.read_text(encoding="utf-8") if synclog.exists() else ""))

        # goal.goal_requests_path() has the SAME "resolved at call time, no
        # test-only override" shape -- assert it too while we're here (every
        # TestGoalSweep call already threads an explicit `requests_path=`,
        # so this one is not expected to fail even before the #437 fix, but
        # it guards the identical bug one file over for any future test that
        # forgets to pass it).
        reqlog = Path(scratch) / ".claude" / "goal-requests.json"
        self.assertFalse(
            reqlog.exists(),
            "tests/test_goal_sweep.py wrote a REAL-shaped goal-requests.json "
            "outside its isolated HOME:\n%s"
            % (reqlog.read_text(encoding="utf-8") if reqlog.exists() else ""))


if __name__ == "__main__":
    unittest.main()
