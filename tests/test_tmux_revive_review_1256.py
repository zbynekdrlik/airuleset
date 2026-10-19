"""#1256 review round (2 fresh-context reviews, 2026-10-19) — shell side.

1. The reaper deletes the WHOLE `/tmp/tmux-<uid>/` dir, not just the socket.
   SIGUSR1 makes tmux re-bind its socket but never re-create the directory, so
   the first cut revived nothing in the real incident shape.
2. With several orphans on one path, signalling all of them lets the LAST one
   win in an arbitrary order (`/proc` glob order); signal exactly one: the
   server hosting a `claude`, else the lowest pid.
3. The revive leaves a journal trace (`logger -t airuleset-tmux-revive`).
4. Tests that run the attach body with a fake tmux must never reach the real
   default socket: conftest and the push gate point `AIRULESET_TMUX_SOCK` at a
   per-run path.
5. The test reaper never touches a server bound to the default socket or one
   with a connected client.
"""
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

import cli_tmux_revive as rv  # noqa: E402
import cli_webterm as w  # noqa: E402
import _tmux_reaper as reaper  # noqa: E402
import conftest  # noqa: E402
import test_tmux_revive_1256 as base  # noqa: E402  (module import: no re-collection)


class _SubdirSock(base._RealTmux):
    """The socket lives in its own directory, like `/tmp/tmux-<uid>/default`,
    so a test can delete the directory the way the subdev sweep does."""

    def setUp(self):
        super().setUp()
        self.sockdir = os.path.join(self.short, "s")   # base: short /tmp dir
        os.mkdir(self.sockdir, 0o700)
        self.sock = os.path.join(self.sockdir, "default")
        self.env["AIRULESET_TMUX_SOCK"] = self.sock

    def revive(self):
        return subprocess.run(["sh", "-c", base._scope(rv.TMUX_REVIVE_SH, self.sock)],
                              capture_output=True, text=True, timeout=20,
                              env=self.env)

    def start_with(self, name, *cmd):
        r = self.tmux("-f", "/dev/null", "new-session", "-d", "-s", name, *cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(self.tmux("list-sessions", "-F", "#{pid}").stdout.split()[0])


class WholeSocketDirDeleted(_SubdirSock):
    def test_snippet_recreates_the_dir_and_revives(self):
        pid = self.start_with("alpha")
        shutil.rmtree(self.sockdir)
        self.revive()
        self.assertEqual(self.server_pids_now(), {pid})

    def test_attach_body_joins_the_original_after_rm_rf(self):
        pid = self.start_with("alpha")
        shutil.rmtree(self.sockdir)
        base.AttachBodyNeverForksASecondServer._connect(self, "alpha")
        self.assertEqual(self.server_pids_now(), {pid},
                         "the connect started a NEW tmux server")
        self.assertEqual(base._listening_pids(self.sock), {pid})


class ExactlyOneServerIsSignalled(_SubdirSock):
    def setUp(self):
        super().setUp()
        self.fake_claude = os.path.join(self.dir, "claude")
        shutil.copy("/bin/sleep", self.fake_claude)

    def test_the_claude_host_wins_over_a_newer_bash_only_orphan(self):
        claude_srv = self.start_with("alpha", self.fake_claude, "300")
        os.unlink(self.sock)
        bash_srv = self.start_with("alpha")
        self.assertNotEqual(claude_srv, bash_srv)
        os.unlink(self.sock)                       # both orphaned now
        self.revive()
        self.assertEqual(self.server_pids_now(), {claude_srv})

    def test_without_claude_the_lowest_pid_wins(self):
        first = self.start_with("alpha")
        os.unlink(self.sock)
        second = self.start_with("alpha")
        os.unlink(self.sock)
        self.revive()
        self.assertEqual(self.server_pids_now(), {min(first, second)})


class ReviveLeavesATrace(unittest.TestCase):
    def test_logger_line(self):
        self.assertIn("logger -t airuleset-tmux-revive", rv.TMUX_REVIVE_SH)

    def test_one_kill_per_revive(self):
        self.assertEqual(rv.TMUX_REVIVE_SH.count("kill -USR1"), 1)

    def test_fragment_still_has_no_backslash(self):
        # sshd's authorized_keys dequote keeps `\\` doubled (#1256 design)
        self.assertNotIn("\\", rv.TMUX_REVIVE_SH)
        self.assertTrue(w._ATTACH_BODY.startswith(rv.TMUX_REVIVE_SH))


class TestsNeverReachTheRealSocket(unittest.TestCase):
    def test_conftest_points_the_seam_at_the_run_dir(self):
        src = inspect.getsource(conftest._per_run_tempdir)
        self.assertIn('os.environ["AIRULESET_TMUX_SOCK"]', src)

    def test_push_gate_sets_the_seam_too(self):
        import cli_remote
        self.assertIn('test_env["AIRULESET_TMUX_SOCK"]',
                      inspect.getsource(cli_remote))

    def test_the_seam_is_set_inside_this_run(self):
        sock = os.environ.get("AIRULESET_TMUX_SOCK", "")
        self.assertIn("airuleset-pytest-run-", sock)


class ReaperGuards(unittest.TestCase):
    """A server bound to the default socket, or with a connected client, is
    never reaped even when its TMPDIR names a finished run."""

    def _fake(self, root, pid, tmpdir):
        d = os.path.join(root, str(pid))
        os.makedirs(os.path.join(d, "fd"))
        Path(d, "comm").write_text("tmux: server\n")
        Path(d, "environ").write_bytes(b"TMPDIR=" + tmpdir.encode() + b"\0")
        Path(d, "stat").write_text("%d (tmux: server) S 1 0\n" % pid)

    def test_default_socket_and_connected_servers_are_kept(self):
        root = tempfile.mkdtemp(prefix="reapguard-")
        self.addCleanup(shutil.rmtree, root, True)
        os.mkdir(os.path.join(root, "net"))
        gone = os.path.join(root, "airuleset-pytest-run-gone")
        default = "/tmp/tmux-%d/default" % os.getuid()
        rows = []
        for pid, path, state, ino in ((10, "/x/run/sock", "01", 501),
                                      (11, default, "01", 502),
                                      (12, "/x/run/sock2", "01", 503),
                                      (12, "/x/run/sock2", "03", 504)):
            if not os.path.isdir(os.path.join(root, str(pid))):
                self._fake(root, pid, gone)
            flags = "00010000" if state == "01" else "00000000"
            rows.append("0000: 00000002 00000000 %s 0001 %s %d %s"
                        % (flags, state, ino, path))
            fd_dir = os.path.join(root, str(pid), "fd")
            os.symlink("socket:[%d]" % ino,
                       os.path.join(fd_dir, str(len(os.listdir(fd_dir)) + 3)))
        Path(root, "net", "unix").write_text(
            "Num RefCount Protocol Flags Type St Inode Path\n" + "\n".join(rows) + "\n")
        self.assertEqual(reaper.stale_servers(proc_root=root, default_path=default), [10])


if __name__ == "__main__":
    unittest.main()
