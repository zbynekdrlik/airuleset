"""#1256 (f) — tmux servers started by tests must not outlive their run.

Live 2026-10-19: the controller `airuleset` account carried ~120 `tmux: server`
processes left by pytest runs (`-L i1157s3-*`, `-S <run>/…/sock`). A tmux
server daemonizes, so a run that dies or whose cleanup does not reach it
leaves it behind. Every one of them carried `TMPDIR=<tmp>/airuleset-pytest-run-*`
(the #548 per-run dir) and every one of those dirs was already gone.

The reaper kills own-uid tmux servers whose TMPDIR is a per-run dir that no
longer exists (start of a run), and the current run's own servers (end of a
run, before the dir is removed). A server without such a TMPDIR is never
touched.
"""
import errno
import inspect
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import _tmux_reaper as reaper  # noqa: E402
import conftest  # noqa: E402

HAVE_TMUX = shutil.which("tmux") is not None


class FakeProc:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="fakeproc-reap-")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def proc(self, pid, comm, env):
        d = os.path.join(self.root, str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "comm"), "w") as f:
            f.write(comm + "\n")
        with open(os.path.join(d, "environ"), "wb") as f:
            f.write(b"".join(("%s=%s" % kv).encode() + b"\0" for kv in env.items()))


class StaleAndRunServers(unittest.TestCase):
    def setUp(self):
        self.p = FakeProc()
        self.addCleanup(self.p.close)
        self.base = tempfile.mkdtemp(prefix="reapbase-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.live = os.path.join(self.base, "airuleset-pytest-run-live")
        os.mkdir(self.live)
        self.gone = os.path.join(self.base, "airuleset-pytest-run-gone")
        self.p.proc(10, "tmux: server", {"TMPDIR": self.gone})
        self.p.proc(11, "tmux: server", {"TMPDIR": self.live})
        self.p.proc(12, "tmux: server", {"HOME": "/home/x"})          # a real one
        self.p.proc(13, "tmux: server", {"TMPDIR": "/tmp/claude-1000"})  # not a run dir
        self.p.proc(14, "bash", {"TMPDIR": self.gone})                 # not tmux

    def test_stale_servers_are_those_whose_run_dir_is_gone(self):
        self.assertEqual(reaper.stale_servers(proc_root=self.p.root), [10])

    def test_run_servers_are_this_runs_own(self):
        self.assertEqual(reaper.run_servers(self.live, proc_root=self.p.root), [11])

    def test_reap_sends_sigterm(self):
        sent = []
        reaper.reap([10, 11], kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [(10, signal.SIGTERM), (11, signal.SIGTERM)])


class ConftestWiring(unittest.TestCase):
    def test_per_run_tempdir_reaps_at_start_and_before_removal(self):
        src = inspect.getsource(conftest._per_run_tempdir)
        self.assertIn("reaper.stale_servers()", src)
        self.assertIn("reaper.run_servers(str(run_dir))", src)
        self.assertLess(src.index("reaper.run_servers"), src.index("shutil.rmtree(run_dir"))


@unittest.skipUnless(HAVE_TMUX, "tmux not installed")
class ReapsARealLeakedServer(unittest.TestCase):
    def test_leaked_server_of_a_finished_run_is_killed(self):
        scratch = tempfile.mkdtemp(prefix="reap1256-")
        self.addCleanup(shutil.rmtree, scratch, True)
        run_dir = os.path.join(scratch, "airuleset-pytest-run-finished")
        os.mkdir(run_dir)
        short = tempfile.mkdtemp(prefix="r1256", dir="/tmp")   # socket path <= 108 bytes
        self.addCleanup(shutil.rmtree, short, True)
        sock = os.path.join(short, "sock")
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        env.update(TMPDIR=run_dir, HOME=scratch)
        r = subprocess.run(["tmux", "-S", sock, "-f", "/dev/null", "new-session",
                            "-d", "-s", "x"], capture_output=True, text=True,
                           timeout=10, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        pid = int(subprocess.run(["tmux", "-S", sock, "list-sessions", "-F", "#{pid}"],
                                 capture_output=True, text=True, timeout=10,
                                 env=env).stdout.split()[0])
        self.addCleanup(self._kill_quiet, pid)
        os.rmdir(run_dir)                       # the run finished, its dir is gone
        self.assertIn(pid, reaper.stale_servers())
        reaper.reap(reaper.stale_servers())
        deadline = time.time() + 5
        while time.time() < deadline and os.path.exists("/proc/%d" % pid):
            time.sleep(0.05)
        self.assertFalse(os.path.exists("/proc/%d" % pid))

    @staticmethod
    def _kill_quiet(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as e:
            if e.errno != errno.ESRCH:
                sys.stderr.write("reap1256 cleanup kill %d: %s\n" % (pid, e))


if __name__ == "__main__":
    unittest.main()
