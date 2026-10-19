"""#1256 — the watchdog side: which tmux servers are bound to the default
socket path, which of them hosts a Claude, and what happens when there are
two.

Live state that motivated it (subdev 2026-10-19): david1-3 each ran TWO tmux
servers bound to `/tmp/tmux-<uid>/default` (the first lost its socket file to
a weekly /tmp sweep, a reconnect started the second). The #318 recovery only
acted while the socket was MISSING and signalled same-uid servers in `ps`
(pid) order, so the oldest, bash-only server won the socket while the working
Claude sat in an unreachable orphan.

Pure-function tests run on a FAKE proc tree; one real-tmux test proves the
SIGUSR1 re-point end to end on a private `-S` socket.
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
from unittest import mock as m

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
from watchdog import tmux_servers as ts  # noqa: E402
from watchdog import tmux_io  # noqa: E402

HAVE_TMUX = shutil.which("tmux") is not None


class FakeProc:
    """A minimal /proc: net/unix rows, per-pid stat/comm and fd symlinks."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="fakeproc1256-")
        os.mkdir(os.path.join(self.root, "net"))
        self.unix_rows = []
        self._ino = 9000

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def proc(self, pid, comm, ppid=1):
        d = os.path.join(self.root, str(pid))
        os.makedirs(os.path.join(d, "fd"), exist_ok=True)
        with open(os.path.join(d, "comm"), "w") as f:
            f.write(comm + "\n")
        with open(os.path.join(d, "stat"), "w") as f:
            f.write("%d (%s) S %d 0 0 0\n" % (pid, comm, ppid))

    def listen(self, pid, path, flags="00010000"):
        self._ino += 1
        self.unix_rows.append("0000: 00000002 00000000 %s 0001 01 %d %s"
                              % (flags, self._ino, path))
        n = len(os.listdir(os.path.join(self.root, str(pid), "fd")))
        os.symlink("socket:[%d]" % self._ino,
                   os.path.join(self.root, str(pid), "fd", str(n + 3)))
        self._write_unix()

    def _write_unix(self):
        with open(os.path.join(self.root, "net", "unix"), "w") as f:
            f.write("Num RefCount Protocol Flags Type St Inode Path\n")
            f.write("\n".join(self.unix_rows) + "\n")


class _FakeProcCase(unittest.TestCase):
    PATH = "/tmp/tmux-1000/default"

    def setUp(self):
        self.p = FakeProc()
        self.addCleanup(self.p.close)

    def two_servers(self, claude_under=(200,)):
        """Servers 100 and 200 both bound to PATH; a claude under each
        server listed in `claude_under` (server -> bash -> claude)."""
        for srv in (100, 200):
            self.p.proc(srv, "tmux: server")
            self.p.listen(srv, self.PATH)
            self.p.proc(srv + 1, "bash", ppid=srv)
            if srv in claude_under:
                self.p.proc(srv + 2, "claude", ppid=srv + 1)


class ServerDiscovery(_FakeProcCase):
    def test_servers_bound_to_the_path(self):
        self.two_servers()
        self.p.proc(300, "tmux: server")
        self.p.listen(300, "/tmp/tmux-1000/i1157-abc")        # other path
        self.p.proc(400, "tmux: server")
        self.p.listen(400, self.PATH, flags="00000000")       # not listening
        self.assertEqual(ts.servers_bound_to(self.PATH, proc_root=self.p.root),
                         [100, 200])

    def test_claude_hosts_walks_up_the_parent_chain(self):
        self.two_servers(claude_under=(200,))
        self.assertEqual(ts.claude_hosts([100, 200], proc_root=self.p.root), {200})

    def test_recovery_order_puts_the_claude_host_first(self):
        self.two_servers(claude_under=(200,))
        self.assertEqual(ts.recovery_order([100, 200], path=self.PATH,
                                           proc_root=self.p.root), [200, 100])

    def test_recovery_order_keeps_ps_order_without_a_claude(self):
        self.two_servers(claude_under=())
        self.assertEqual(ts.recovery_order([100, 200], path=self.PATH,
                                           proc_root=self.p.root), [100, 200])


class DuplicateCheck(_FakeProcCase):
    def _check(self, owner, dry_run=False, memo=None):
        calls, kills, logs = [], [], []

        def run(argv):
            calls.append(argv)
            return "%d\n" % owner

        res = ts.duplicate_check(run, logs=logs, dry_run=dry_run, path=self.PATH,
                                 proc_root=self.p.root,
                                 kill=lambda pid, sig: kills.append((pid, sig)),
                                 memo_path=memo)
        return res, calls, kills, logs

    def test_single_server_is_a_no_op_without_any_tmux_call(self):
        self.p.proc(100, "tmux: server")
        self.p.listen(100, self.PATH)
        res, calls, kills, logs = self._check(owner=100)
        self.assertIsNone(res)
        self.assertEqual((calls, kills, logs), ([], [], []))

    def test_socket_moves_to_the_server_that_hosts_claude(self):
        self.two_servers(claude_under=(200,))
        res, calls, kills, logs = self._check(owner=100)
        self.assertEqual(res, "repointed")
        self.assertEqual(kills, [(200, signal.SIGUSR1)])
        self.assertIn("-S", calls[0])
        self.assertIn(self.PATH, calls[0])
        self.assertTrue(any("tmux-duplicate-server" in ln and "repointed" in ln
                            for ln in logs), logs)

    def test_dry_run_only_logs(self):
        self.two_servers(claude_under=(200,))
        res, _calls, kills, logs = self._check(owner=100, dry_run=True)
        self.assertEqual(kills, [])
        self.assertTrue(any("would" in ln for ln in logs), logs)

    def test_owner_already_hosts_claude(self):
        self.two_servers(claude_under=(100,))
        res, _calls, kills, _logs = self._check(owner=100)
        self.assertEqual(res, "ok")
        self.assertEqual(kills, [])

    def test_two_claudes_is_ambiguous_and_never_signals(self):
        self.two_servers(claude_under=(100, 200))
        res, _calls, kills, logs = self._check(owner=100)
        self.assertEqual(res, "ambiguous")
        self.assertEqual(kills, [])
        self.assertTrue(any("ambiguous" in ln for ln in logs), logs)

    def test_unchanged_state_is_logged_once(self):
        self.two_servers(claude_under=(100, 200))
        memo = os.path.join(self.p.root, "memo")
        _, _, _, first = self._check(owner=100, memo=memo)
        _, _, _, second = self._check(owner=100, memo=memo)
        self.assertTrue(first)
        self.assertEqual(second, [])


class StatusLine(_FakeProcCase):
    """`airuleset.py status` names a duplicate so the owner can see it without
    the journal (silent when <= 1 server shares the path)."""

    def test_silent_for_a_single_server(self):
        self.p.proc(100, "tmux: server")
        self.p.listen(100, self.PATH)
        self.assertIsNone(ts.status_line(path=self.PATH, proc_root=self.p.root))

    def test_names_the_servers_and_the_claude_host(self):
        self.two_servers(claude_under=(200,))
        line = ts.status_line(path=self.PATH, proc_root=self.p.root)
        self.assertIn("2 servers", line)
        self.assertIn("100,200", line)
        self.assertIn("claude in 200", line)

    def test_cmd_status_prints_it(self):
        import airuleset
        self.assertIn("tmux_servers", inspect.getsource(airuleset.cmd_status))


class PaneInventoryWiring(unittest.TestCase):
    """`_pane_inventory_raw` runs the duplicate check after a healthy query and
    re-reads the inventory when the socket moved; run_once hands it `logs`."""

    def _run_factory(self, calls):
        def run(argv):
            calls.append(argv)
            return "%7\tclaude\t/home/x\t42\n" if "list-panes" in argv else ""
        return run

    def test_requery_after_a_repoint(self):
        calls = []
        with m.patch.object(ts, "duplicate_check", return_value="repointed"):
            tmux_io._pane_inventory_raw(self._run_factory(calls), logs=[])
        self.assertEqual(sum(1 for c in calls if "list-panes" in c), 2)

    def test_single_query_when_nothing_moved(self):
        calls = []
        with m.patch.object(ts, "duplicate_check", return_value=None):
            tmux_io._pane_inventory_raw(self._run_factory(calls), logs=[])
        self.assertEqual(sum(1 for c in calls if "list-panes" in c), 1)

    def test_recovery_signals_in_claude_first_order(self):
        src = inspect.getsource(tmux_io._pane_inventory_raw)
        self.assertIn("recovery_order(", src)

    def test_run_once_passes_its_logs(self):
        src = inspect.getsource(wd.run_once)
        self.assertIn("list_claude_panes(run, logs=logs, dry_run=dry_run)", src)


@unittest.skipUnless(HAVE_TMUX, "tmux not installed")
class RepointOnRealTmux(unittest.TestCase):
    """Two real servers bound to ONE private `-S` path; the first runs a
    process named `claude`. The check must hand the socket back to it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="d1256", dir="/tmp")   # socket path <= 108 bytes
        self.sock = os.path.join(self.dir, "sock")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE")}
        self.env.update(HOME=self.dir, TMUX_TMPDIR=self.dir)
        self.addCleanup(self._teardown)
        self.fake_claude = os.path.join(self.dir, "claude")
        shutil.copy("/bin/sleep", self.fake_claude)

    def _teardown(self):
        for pid in ts.servers_bound_to(self.sock):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                if e.errno != errno.ESRCH:
                    sys.stderr.write("dup1256 teardown kill %d: %s\n" % (pid, e))
        shutil.rmtree(self.dir, ignore_errors=True)

    def tmux(self, *args):
        return subprocess.run(["tmux", "-S", self.sock, *args],
                              capture_output=True, text=True, timeout=10,
                              env=self.env)

    def owner(self):
        return int(self.tmux("list-sessions", "-F", "#{pid}").stdout.split()[0])

    def test_socket_returns_to_the_claude_server(self):
        r = self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "a",
                      self.fake_claude, "300")
        self.assertEqual(r.returncode, 0, r.stderr)
        claude_srv = self.owner()
        os.unlink(self.sock)
        r = self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        bash_srv = self.owner()
        self.assertNotEqual(claude_srv, bash_srv)

        def run(argv):
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=10, env=self.env).stdout

        logs = []
        res = ts.duplicate_check(run, logs=logs, path=self.sock, kill=os.kill,
                                 memo_path=os.path.join(self.dir, "memo"))
        self.assertEqual(res, "repointed", logs)
        deadline = time.time() + 5
        while time.time() < deadline and self.owner() != claude_srv:
            time.sleep(0.1)
        self.assertEqual(self.owner(), claude_srv)


if __name__ == "__main__":
    unittest.main()
