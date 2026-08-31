"""Behaviour test for watchdog Job 37 — the runaway shadow-ugrep OS-process
reaper (#776, Layer 2).

The reaper uses INJECTED ps_fetch/kill_fn/verify_fn fakes throughout — it NEVER
touches a real process, so it is safe under xdist (the internals-tests.md
isolation lesson: never chmod/kill anything shared across workers).
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
    REAPER_MIN_CPU_RATIO,
    SHADOW_UGREP_SIGNATURE,
    _is_shadow_ugrep_runaway,
    _matches_signature,
)

RUNAWAY_CMD = ("ugrep -G --ignore-files --hidden -I --exclude-dir=.git "
               "--exclude-dir=.svn -rn foo /")
YOUNG = 5
OLD = REAPER_MIN_AGE_S + 60
# a busy-loop burns ~1 CPU-sec per wall-sec, so cputimes ~= etimes
BUSY = OLD
# a pipe-blocked grep runs long but burns ~0 CPU
IDLE_CPU = 1


class _Recorder:
    def __init__(self):
        self.killed = []

    def __call__(self, pid):
        self.killed.append(int(pid))


def _procs(rows):
    """Return a ps_fetch that yields the given (pid, etimes, cputimes, args)."""
    return lambda: rows


def _verify_ok(pid):
    """A verify_fn that always confirms the runaway (matches signature)."""
    return RUNAWAY_CMD


class TestSignatureMatcher(unittest.TestCase):
    def test_anchored_signature_matches_runaway(self):
        self.assertTrue(_matches_signature(RUNAWAY_CMD))
        self.assertTrue(_matches_signature("/usr/bin/ugrep -G --ignore-files x"))

    def test_signature_is_anchored_not_substring(self):
        # a process merely QUOTING the signature (argv0 != ugrep) never matches
        self.assertFalse(_matches_signature(
            "watch pgrep -af 'ugrep -G --ignore-files'"))
        self.assertFalse(_matches_signature(
            "grep -F 'ugrep -G --ignore-files' log"))

    def test_wrong_flag_order_does_not_match(self):
        self.assertFalse(_matches_signature("ugrep --ignore-files -G x"))

    def test_old_busy_runaway_matches(self):
        self.assertTrue(_is_shadow_ugrep_runaway(RUNAWAY_CMD, OLD, BUSY))

    def test_young_runaway_does_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(RUNAWAY_CMD, YOUNG, YOUNG))

    def test_old_but_LOW_CPU_does_not_match(self):
        # #776 review 🟡: a legitimate `tail -f log | grep pat` waiter is
        # shadowed to ugrep, carries the signature, runs > 30 min blocked on
        # the pipe (~0 CPU). It must NOT be killed — the CPU gate is what
        # separates a busy-loop from a legitimate long-runner.
        self.assertFalse(_is_shadow_ugrep_runaway(RUNAWAY_CMD, OLD, IDLE_CPU))

    def test_old_but_different_process_does_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(
            "python3 some_long_running_job.py", OLD, BUSY))

    def test_exactly_at_age_threshold_does_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(
            RUNAWAY_CMD, REAPER_MIN_AGE_S, REAPER_MIN_AGE_S))

    def test_non_int_fields_do_not_match(self):
        self.assertFalse(_is_shadow_ugrep_runaway(RUNAWAY_CMD, "nope", BUSY))
        self.assertFalse(_is_shadow_ugrep_runaway(RUNAWAY_CMD, OLD, "nope"))

    def test_constants(self):
        self.assertEqual(SHADOW_UGREP_SIGNATURE, "ugrep -G --ignore-files")
        self.assertEqual(REAPER_MIN_CPU_RATIO, 0.5)


class TestReaperKills(unittest.TestCase):
    def test_kills_old_busy_runaway_and_logs(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(99999, OLD, BUSY, RUNAWAY_CMD)]),
            kill_fn=kill, verify_fn=_verify_ok)
        self.assertEqual(kill.killed, [99999])
        self.assertEqual(len(logs), 1)
        self.assertIn("SIGKILL", logs[0])
        self.assertIn("99999", logs[0])
        self.assertIn(str(OLD), logs[0])
        self.assertIn("ugrep -G --ignore-files", logs[0])

    def test_leaves_young_runaway_untouched(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(123, YOUNG, YOUNG, RUNAWAY_CMD)]),
            kill_fn=kill, verify_fn=_verify_ok)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_leaves_pipe_blocked_long_runner_untouched(self):
        # the whole 🟡 fix, end-to-end
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(555, OLD, IDLE_CPU, RUNAWAY_CMD)]),
            kill_fn=kill, verify_fn=_verify_ok)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_leaves_old_but_different_untouched(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(1, OLD, BUSY, "python3 slow.py"),
                             (2, OLD, BUSY, "sleep 100000")]),
            kill_fn=kill, verify_fn=_verify_ok)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_kills_only_the_qualifying_ones(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([
                (1, OLD, BUSY, RUNAWAY_CMD),     # kill
                (2, YOUNG, YOUNG, RUNAWAY_CMD),  # too young
                (3, OLD, IDLE_CPU, RUNAWAY_CMD), # too little CPU (pipe-blocked)
                (4, OLD, BUSY, "python3 x.py"),  # not a runaway
                (5, OLD, BUSY, RUNAWAY_CMD),     # kill
            ]),
            kill_fn=kill, verify_fn=_verify_ok)
        self.assertEqual(sorted(kill.killed), [1, 5])
        self.assertEqual(len(logs), 2)

    def test_dry_run_kills_nothing_but_logs(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(7, OLD, BUSY, RUNAWAY_CMD)]),
            kill_fn=kill, verify_fn=_verify_ok, dry_run=True)
        self.assertEqual(kill.killed, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("DRY-RUN", logs[0])


class TestReaperFailSafe(unittest.TestCase):
    def test_ps_error_kills_nothing(self):
        kill = _Recorder()

        def boom():
            raise OSError("ps blew up")

        logs = shadow_ugrep_reaper(ps_fetch=boom, kill_fn=kill, verify_fn=_verify_ok)
        self.assertEqual(kill.killed, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("ps error", logs[0])

    def test_ps_none_kills_nothing(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(ps_fetch=lambda: None, kill_fn=kill,
                                   verify_fn=_verify_ok)
        self.assertEqual(kill.killed, [])
        self.assertEqual(logs, [])

    def test_malformed_row_is_skipped(self):
        kill = _Recorder()
        rows = [("not", "a", "quad"), (5, OLD, BUSY, RUNAWAY_CMD)]
        shadow_ugrep_reaper(ps_fetch=_procs(rows), kill_fn=kill, verify_fn=_verify_ok)
        # the good row still reaps; the malformed one is skipped, not guessed
        self.assertEqual(kill.killed, [5])

    def test_unwired_kill_fn_kills_nothing(self):
        # #776 review 🟡: a mis-wired run_once seam (ps wired, kill None) must
        # NOT fall through to a real SIGKILL — it logs "would kill" and stops.
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(8, OLD, BUSY, RUNAWAY_CMD)]),
            kill_fn=None, verify_fn=_verify_ok)
        self.assertEqual(len(logs), 1)
        self.assertIn("kill_fn not wired", logs[0])

    def test_toctou_pid_reuse_is_not_killed(self):
        # #776 review 🔵: if /proc/<pid>/cmdline no longer matches (pid reused)
        # between the ps read and the kill, do NOT kill.
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(9, OLD, BUSY, RUNAWAY_CMD)]),
            kill_fn=kill, verify_fn=lambda pid: "python3 innocent.py")
        self.assertEqual(kill.killed, [])
        self.assertIn("reused", logs[0])

    def test_toctou_pid_vanished_is_not_killed(self):
        kill = _Recorder()
        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(10, OLD, BUSY, RUNAWAY_CMD)]),
            kill_fn=kill, verify_fn=lambda pid: None)
        self.assertEqual(kill.killed, [])
        self.assertIn("vanished", logs[0])

    def test_kill_failure_is_logged_not_raised(self):
        def bad_kill(pid):
            raise ProcessLookupError("gone")

        logs = shadow_ugrep_reaper(
            ps_fetch=_procs([(8, OLD, BUSY, RUNAWAY_CMD)]),
            kill_fn=bad_kill, verify_fn=_verify_ok)
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
        logs = self._run()
        self.assertEqual([ln for ln in logs if "shadow-ugrep-reaper" in ln], [])

    def test_wired_reaper_runs_and_decides_within_one_cycle(self):
        kill = _Recorder()
        logs = self._run(
            reaper_ps_fetch=_procs([(4242, OLD, BUSY, RUNAWAY_CMD)]),
            reaper_kill_fn=kill)
        # run_once wires the real verify_fn (/proc read); pid 4242 does not
        # exist, so the TOCTOU re-verify correctly declines to kill it — which
        # is itself the fail-safe. Assert the reaper RAN and made a decision.
        reaped = [ln for ln in logs if "shadow-ugrep-reaper" in ln]
        self.assertEqual(len(reaped), 1)
        self.assertEqual(kill.killed, [])
        self.assertIn("vanished", reaped[0])


if __name__ == "__main__":
    unittest.main()
