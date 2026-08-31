"""Behaviour test for watchdog Job 37 — the runaway shadow-ugrep OS-process
reaper (#776, Layer 2).

The reaper uses INJECTED ps_fetch/kill_fn fakes throughout — it NEVER touches a
real process, so it is safe under xdist (the internals-tests.md isolation
lesson: never chmod/kill anything shared across workers).
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd                       # noqa: E402
from watchdog.reaper import (               # noqa: E402
    shadow_ugrep_reaper,
    REAPER_MIN_AGE_S,
    SHADOW_UGREP_SIGNATURE,
    _is_shadow_ugrep_runaway,
)

RUNAWAY_CMD = ("ugrep -G --ignore-files --hidden -I --exclude-dir=.git "
               "--exclude-dir=.svn -rn foo /")
YOUNG = 5
OLD = REAPER_MIN_AGE_S + 60


class _Recorder:
    def __init__(self):
        self.killed = []

    def __call__(self, pid):
        self.killed.append(int(pid))


class TestSignatureMatcher(unittest.TestCase):
    def test_old_runaway_matches(self):
        self.assertTrue(_is_shadow_ugrep_runaway(RUNAWAY_CMD, OLD, REAPER_MIN_AGE_S))

    def test_young_runaway_does_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(RUNAWAY_CMD, YOUNG, REAPER_MIN_AGE_S))

    def test_old_but_different_process_does_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(
            "python3 some_long_running_job.py", OLD, REAPER_MIN_AGE_S))

    def test_exactly_at_threshold_does_not_match(self):
        # strictly GREATER than the age gate
        self.assertFalse(_is_shadow_ugrep_runaway(
            RUNAWAY_CMD, REAPER_MIN_AGE_S, REAPER_MIN_AGE_S))

    def test_non_int_etimes_does_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(RUNAWAY_CMD, "nope", REAPER_MIN_AGE_S))

    def test_signature_constant(self):
        self.assertEqual(SHADOW_UGREP_SIGNATURE, "ugrep -G --ignore-files")


class TestReaperKills(unittest.TestCase):
    def test_kills_old_runaway_and_logs(self):
        kill = _Recorder()
        procs = [(99999, OLD, RUNAWAY_CMD)]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=kill)
        self.assertEqual(kill.killed, [99999])
        self.assertEqual(len(logs), 1)
        # log carries reason + cmdline + age
        self.assertIn("SIGKILL", logs[0])
        self.assertIn("99999", logs[0])
        self.assertIn(str(OLD), logs[0])
        self.assertIn("ugrep -G --ignore-files", logs[0])

    def test_leaves_young_runaway_untouched(self):
        kill = _Recorder()
        procs = [(123, YOUNG, RUNAWAY_CMD)]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=kill)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_leaves_old_but_different_untouched(self):
        kill = _Recorder()
        procs = [(123, OLD, "python3 slow.py"), (124, OLD, "sleep 100000")]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=kill)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_kills_only_the_qualifying_ones(self):
        kill = _Recorder()
        procs = [
            (1, OLD, RUNAWAY_CMD),          # kill
            (2, YOUNG, RUNAWAY_CMD),        # too young
            (3, OLD, "python3 x.py"),       # not a runaway
            (4, OLD, RUNAWAY_CMD),          # kill
        ]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=kill)
        self.assertEqual(sorted(kill.killed), [1, 4])
        self.assertEqual(len(logs), 2)

    def test_dry_run_kills_nothing_but_logs(self):
        kill = _Recorder()
        procs = [(7, OLD, RUNAWAY_CMD)]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=kill, dry_run=True)
        self.assertEqual(kill.killed, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("DRY-RUN", logs[0])


class TestReaperFailSafe(unittest.TestCase):
    def test_ps_error_kills_nothing(self):
        kill = _Recorder()

        def boom():
            raise OSError("ps blew up")

        logs = shadow_ugrep_reaper(ps_fetch=boom, kill_fn=kill)
        self.assertEqual(kill.killed, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("ps error", logs[0])

    def test_ps_none_kills_nothing(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(ps_fetch=lambda: None, kill_fn=kill)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_malformed_row_is_skipped(self):
        kill = _Recorder()
        procs = [("not", "a", "triple", "extra"), (5, OLD, RUNAWAY_CMD)]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=kill)
        # the good row still reaps; the malformed one is skipped, not guessed
        self.assertEqual(kill.killed, [5])

    def test_kill_failure_is_logged_not_raised(self):
        def bad_kill(pid):
            raise ProcessLookupError("gone")

        procs = [(8, OLD, RUNAWAY_CMD)]
        logs = shadow_ugrep_reaper(ps_fetch=lambda: procs, kill_fn=bad_kill)
        self.assertEqual(len(logs), 1)
        self.assertIn("FAILED", logs[0])


class TestJob37Wiring(unittest.TestCase):
    """End-to-end: run_once with the reaper seams wired reaps within ONE cycle."""

    def _run(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        proj.mkdir()
        return wd.run_once(
            now=1_000_000.0, run=lambda argv, timeout=8: "",
            send_fn=lambda *a, **k: None, projects_dir=proj,
            state_path=str(Path(tmp.name) / "state.json"),
            pending_prefix=str(Path(tmp.name) / "pending-"), **kw)

    def test_unwired_caller_runs_no_reaper(self):
        # no reaper_ps_fetch -> job 37 is gated OFF (network-free for other tests)
        logs = self._run()
        self.assertEqual([ln for ln in logs if "shadow-ugrep-reaper" in ln], [])

    def test_wired_reaper_kills_within_one_cycle(self):
        kill = _Recorder()
        procs = [(4242, OLD, RUNAWAY_CMD)]
        logs = self._run(reaper_ps_fetch=lambda: procs, reaper_kill_fn=kill)
        self.assertEqual(kill.killed, [4242])
        reaped = [ln for ln in logs if "shadow-ugrep-reaper" in ln]
        self.assertEqual(len(reaped), 1)
        self.assertIn("SIGKILL", reaped[0])


if __name__ == "__main__":
    unittest.main()
