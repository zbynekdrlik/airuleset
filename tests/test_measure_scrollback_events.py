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
"""
import importlib.util
import os
import signal
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "measure_scrollback_events.py"


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


class TestTerminatePidBoundedReap(unittest.TestCase):
    """A child that does not honor SIGTERM must still be reaped within a
    bounded time -- never an indefinite hang (the actual failure observed
    running this harness live for #291)."""

    def test_sigterm_ignoring_child_is_reaped_via_sigkill_within_timeout(self):
        mod = _load_module()
        pid = os.fork()
        if pid == 0:
            # child: ignore SIGTERM entirely, loop forever -- exactly the
            # class of process a plain unbounded os.waitpid(pid, 0) can
            # never return from.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.setsid()
            while True:
                time.sleep(0.05)
            os._exit(0)  # unreachable

        try:
            start = time.time()
            mod.terminate_pid(pid, timeout=1.0)
            elapsed = time.time() - start

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


if __name__ == "__main__":
    unittest.main()
