"""Bundle B9 fix round, fourth adversarial review of the issue 1257 owner-stop
latch: the shared Claude-process predicate under a SHELL parent (the usual
npm launch shape), a process name that is not UTF-8, and the review-4 edges.
Real processes; /proc is read for real, never mocked (an OS fault is the
only seam patched).
"""
import os
import subprocess
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import owner_stop  # noqa: E402

CLI = "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"


def _spawn(testcase, argv):
    proc = subprocess.Popen(argv)
    testcase.addCleanup(proc.wait)
    testcase.addCleanup(proc.kill)
    return proc


def _children(pid, deadline=10.0):
    """The child pids of `pid` once it has forked one (a shell `cmd; :`)."""
    end = time.time() + deadline
    while time.time() < end:
        kids = [p for p in os.listdir("/proc") if p.isdigit() and wd._proc_read(
            "/proc/%s/stat" % p).rsplit(") ", 1)[-1].split()[1:2] == [str(pid)]]
        if kids:
            return kids
        time.sleep(0.05)
    raise AssertionError("no child of %s" % pid)


class _Procs(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.node = Path(d.name) / "node"
        self.node.symlink_to(sys.executable)

    def _shell(self, *argv):
        """`sh -c '<argv>; :'` — the shell stays the parent (no exec), the way a
        tmux pane's shell forks the Claude it runs."""
        sh = _spawn(self, ["sh", "-c", " ".join("'%s'" % a for a in argv) + "; :"])
        _children(sh.pid)
        return str(sh.pid)

    def _npm_under_shell(self):
        return self._shell(str(self.node), "-c", "import time; time.sleep(60)", CLI)


class TheNpmShapeUnderAShell(_Procs):
    """Review 5, finding 1: every npm test used the node process itself as the
    pane_pid, so the walk under a shell parent — the usual launch, which
    `_hosts_claude` and `list_claude_panes` depend on — had no test."""

    def test_the_npm_claude_under_a_shell_is_found(self):
        sh = self._npm_under_shell()
        child = wd._pane_claude_pid(sh)
        self.assertIsNotNone(child)
        self.assertNotEqual(child, sh)
        self.assertTrue(owner_stop._hosts_claude(sh))

    def test_a_plain_node_under_a_shell_is_no_claude(self):
        sh = self._shell(str(self.node), "-c", "import time; time.sleep(60)",
                         "/srv/app/server.js")
        self.assertIsNone(wd._pane_claude_pid(sh))
        self.assertFalse(owner_stop._hosts_claude(sh))

    def test_list_claude_panes_sees_an_npm_claude_under_a_shell(self):
        npm, plain = self._npm_under_shell(), self._shell(
            str(self.node), "-c", "import time; time.sleep(60)", "/srv/app/x.js")
        inv = "%%S\tsh\t/x\t%s\n%%T\tsh\t/y\t%s\n" % (npm, plain)
        panes = wd.list_claude_panes(lambda argv, timeout=8: inv
                                     if "list-panes" in argv else "")
        self.assertEqual([p for p, _c in panes], ["%S"])


class ABadlyNamedProcessAnywhere(_Procs):
    """Review 5, finding 2: the decode fix had no test that fails without it;
    and the rename was awaited with a fixed sleep (a race under load)."""

    def test_the_hosted_walk_survives_a_non_utf8_process_name(self):
        bad = _spawn(self, [sys.executable, "-c",
                            "import ctypes, time; ctypes.CDLL(None).prctl("
                            "15, b'bad\\xff\\xfe', 0, 0, 0); time.sleep(60)"])
        end = time.time() + 10
        while b"\xff" not in Path("/proc/%d/comm" % bad.pid).read_bytes():
            self.assertLess(time.time(), end, "the child never renamed itself")
            time.sleep(0.05)
        self.assertIsNotNone(wd._pane_claude_pid(self._npm_under_shell()))


class AnUnreadableProcFailsClosed(_Procs):
    """Review 5, finding 3: `_hosts_claude` promised "an unreadable /proc reads
    as a Claude", but the shared walk turns a listing fault (e.g. EMFILE) into
    "no Claude", so an npm Claude next to a bare shell read as a dev server
    and its working session could be latched as an exit."""

    def test_a_proc_listing_fault_reads_as_a_claude(self):
        sh = self._shell("sleep", "60")
        real = os.listdir

        def listdir(path="."):
            if str(path) == "/proc":
                raise OSError(24, "Too many open files")
            return real(path)
        with m.patch("os.listdir", side_effect=listdir):
            self.assertTrue(owner_stop._hosts_claude(sh))


if __name__ == "__main__":
    unittest.main()
