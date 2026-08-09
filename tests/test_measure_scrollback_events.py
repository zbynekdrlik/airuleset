"""#291: scripts/measure_scrollback_events.py's kill_pty_client() had no
bounded reap. A live diagnostic run for #291 hung >500s: the Python driver
blocked forever inside a plain `os.waitpid(pid, 0)`, while the underlying
isolated tmux server + attached pty client it was supposed to tear down were
later found alive and idle -- the driver process was the one stuck, not the
tmux/claude processes it spawned. External `timeout` had to SIGTERM the
driver directly, which skipped its own `finally: teardown(...)` entirely
(Python installs no SIGTERM handler by default, so `finally` blocks never
run on a raw SIGTERM), leaving two orphaned `-L`-socket tmux servers under
/tmp -- each holding a scratch-profile COPY of the real
~/.claude/.credentials.json (see bootstrap_profile() in the sibling
measure_scrollback_holes.py, reused by this script).

terminate_pid() is the fix: SIGTERM, a bounded poll for exit, then an
unconditional SIGKILL escalation + a final blocking waitpid. SIGKILL cannot
be ignored or blocked by the target, so this call is guaranteed to return --
the harness must never again hang indefinitely on a child that doesn't honor
SIGTERM, regardless of the exact reason a given child fails to exit on its
own.

This test needs no tmux/claude dependency -- it drives terminate_pid()
against a real forked child directly, which is the same waitpid semantics
the production code path uses (kill_pty_client() manages a pid obtained
from pty.fork(), not from subprocess.Popen).

Adversarial-review hardening (#291, round 2): the first cut of
`test_sigterm_ignoring_child_is_reaped_via_sigkill_within_timeout` sent
SIGTERM to the freshly-forked child with NO synchronization at all -- the
kernel routinely delivers a signal queued before the child's first
scheduled instruction using the DEFAULT (terminate) disposition, before
`signal.signal(SIGTERM, signal.SIG_IGN)` ever runs. Measured live: the
"SIGTERM-ignoring" child died to plain SIGTERM 20/20 real runs, meaning
the test never actually reached terminate_pid's SIGKILL escalation branch
at all -- a mutant that DELETES that branch entirely still passed. Fixed
with a readiness pipe (parent blocks until the child confirms SIG_IGN is
installed, THEN sends SIGTERM) plus a bounded observer thread around
terminate_pid itself, so a still-broken mutant produces a clean assertion
failure here rather than hanging this test -- and, since this file runs
inside the fail-closed pre-push test suite, hanging the whole push.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "measure_scrollback_events.py"

_REAL_RMTREE = shutil.rmtree  # captured before any test patches shutil.rmtree


def _load_module():
    spec = importlib.util.spec_from_file_location("measure_scrollback_events", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _force_reap(pid, label):
    """Test-cleanup safety net: if terminate_pid() left `pid` alive (RED
    phase, or a regression), never leak a runaway process out of the test
    run. Logged, not silently swallowed -- a hit here during GREEN means
    the fix itself is incomplete and is worth seeing in test output."""
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError) as e:
        # Expected in the common case: terminate_pid() already reaped it,
        # so there is nothing left here to kill/wait on.
        print(f"_force_reap[{label}]: pid {pid} already reaped by "
              f"terminate_pid (expected): {e}", file=sys.stderr)


def _run_terminate_pid_bounded(mod, pid, timeout, observer_timeout=8.0):
    """Run terminate_pid(pid, timeout=timeout) inside a joined DAEMON
    thread with its own outer bound, so a MUTANT terminate_pid (e.g. one
    that drops the SIGKILL escalation and keeps only the poll loop + a
    final unconditional blocking os.waitpid) reports as a clean, fast
    test FAILURE here -- `thread.is_alive()` after the join -- instead of
    hanging this test, and with it the whole fail-closed pre-push suite,
    indefinitely. Returns (thread, result) where result['elapsed'] is set
    only if the call genuinely completed inside the observer window."""
    result = {}

    def _target():
        start = time.time()
        mod.terminate_pid(pid, timeout=timeout)
        result["elapsed"] = time.time() - start

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(observer_timeout)
    return thread, result


class TestTerminatePidBoundedReap(unittest.TestCase):
    """A child that does not honor SIGTERM must still be reaped within a
    bounded time -- never an indefinite hang (the actual failure observed
    running this harness live for #291)."""

    def test_sigterm_ignoring_child_is_reaped_via_sigkill_within_timeout(self):
        mod = _load_module()
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            # child: ignore SIGTERM entirely, loop forever -- exactly the
            # class of process a plain unbounded os.waitpid(pid, 0) can
            # never return from. Confirm SIG_IGN is genuinely installed
            # BEFORE the parent is allowed to send SIGTERM -- otherwise a
            # SIGTERM queued before the child's first scheduled
            # instruction is delivered with the DEFAULT (terminate)
            # disposition, and this test would silently never exercise
            # the SIGKILL-escalation branch it exists to lock.
            os.close(read_fd)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.setsid()
            os.write(write_fd, b"x")
            os.close(write_fd)
            while True:
                time.sleep(0.05)
            os._exit(0)  # unreachable

        os.close(write_fd)
        try:
            ready = os.read(read_fd, 1)
            os.close(read_fd)
            self.assertEqual(
                ready, b"x",
                "child never confirmed SIG_IGN was installed -- the test "
                "would otherwise race terminate_pid's own SIGTERM against "
                "signal.signal() and could pass without ever reaching the "
                "SIGKILL escalation")

            thread, result = _run_terminate_pid_bounded(mod, pid, timeout=1.0)
            self.assertFalse(
                thread.is_alive(),
                "terminate_pid did not return within the bounded observer "
                "window -- this IS the #291 hang class (e.g. a mutant that "
                "drops the SIGKILL escalation): drive it inside a joined "
                "thread, never call it directly, so a still-hanging "
                "terminate_pid fails this assertion instead of hanging the "
                "whole pre-push test suite")
            elapsed = result.get("elapsed")
            self.assertIsNotNone(elapsed, "terminate_pid thread never completed")
            self.assertLess(
                elapsed, 4.0,
                "terminate_pid must escalate to SIGKILL and return -- never "
                "hang indefinitely on a SIGTERM-ignoring child (this is the "
                "exact bug that hung the #291 live harness run for >500s)")
            self.assertFalse(
                _pid_alive(pid),
                "the SIGTERM-ignoring child must actually be gone (reaped "
                "via the SIGKILL escalation), not merely timed out on")
        finally:
            _force_reap(pid, "sigterm-ignoring")

    def test_cooperative_child_is_reaped_promptly_without_waiting_out_timeout(self):
        mod = _load_module()
        pid = os.fork()
        if pid == 0:
            os._exit(0)  # exits immediately -- SIGKILL should never be needed

        try:
            start = time.time()
            mod.terminate_pid(pid, timeout=3.0)
            elapsed = time.time() - start

            self.assertLess(
                elapsed, 1.0,
                "a child that already exited must be reaped almost "
                "instantly, not wait out the full SIGTERM-grace budget")
        finally:
            _force_reap(pid, "cooperative")


class TestTeardownRetriesALeftoverScratchDir(unittest.TestCase):
    """#291 adversarial review, M2: on real runs, teardown()'s own
    `shutil.rmtree(root, ignore_errors=True)` sometimes silently leaves
    the scratch directory behind even though the run otherwise completed
    cleanly -- observed twice, live, on genuinely successful #291 runs.
    Each leftover still held a scratch-profile COPY of the real
    ~/.claude/.credentials.json (mode 0600 -- no cross-uid exposure risk
    on these boxes, but a credential copy that should not linger).
    teardown() must retry the removal once, and print a loud LEFTOVER
    warning if the directory still exists after both attempts, rather
    than silently leaking it forever."""

    def test_a_first_swallowed_rmtree_failure_is_retried_and_the_dir_removed(self):
        mod = _load_module()
        tmpdir = tempfile.mkdtemp(prefix="airuleset-teardown-retry-test-")
        (Path(tmpdir) / "cred.json").write_text("scratch-copy")
        calls = []

        def fake_rmtree(path, ignore_errors=False):
            # Uses the pre-patch REAL rmtree, never the module-global
            # `shutil.rmtree` name -- that name IS this mock while the
            # patch is active, so calling it here would recurse forever.
            calls.append(path)
            if len(calls) == 1:
                return  # simulate the swallowed-failure leftover
            _REAL_RMTREE(path, ignore_errors=ignore_errors)

        with unittest.mock.patch("shutil.rmtree", side_effect=fake_rmtree):
            mod.teardown("airuleset-nonexistent-sock-291", tmpdir, False)

        self.assertEqual(
            len(calls), 2,
            "teardown must retry rmtree exactly once when the first attempt "
            "leaves the directory behind")
        self.assertFalse(
            Path(tmpdir).exists(),
            "the scratch directory (and its credential copy) must be gone "
            "after the retry succeeds")

    def test_a_directory_still_present_after_retry_is_warned_about_loudly(self):
        mod = _load_module()
        tmpdir = tempfile.mkdtemp(prefix="airuleset-teardown-retry-test-")
        try:
            with unittest.mock.patch("shutil.rmtree"):  # no-op every call
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    mod.teardown("airuleset-nonexistent-sock-291", tmpdir, False)
            self.assertIn(
                "LEFTOVER", stderr.getvalue(),
                "a scratch dir (holding a credential copy) still present "
                "after two rmtree attempts must be surfaced loudly, never "
                "silently swallowed")
            self.assertIn(tmpdir, stderr.getvalue(), "the warning must name the path")
        finally:
            _REAL_RMTREE(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
