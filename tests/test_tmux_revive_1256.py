"""#1256 — a lost tmux socket must never fork a SECOND tmux server, and a
per-human webterm tab must never land in ANOTHER human's session.

Incident (owner report 2026-10-19): subdev's weekly hygiene `rm -rf`s
`/tmp/tmux-<uid>/` (the socket mtime never moves) while the server keeps
running. The next webterm connect found "no server", fell through to
`exec tmux new-session -A -s "$P"` and started a second server with a
same-named session — two Claudes in one account, each visible only to whoever
was attached to its server. On claudy, the attach fallback "exactly one
session exists -> join it" put the owner into marek's session after his own
ended.

Real-tmux tests run on a private `-S` socket (the #613 isolation lock) with a
throwaway HOME; the production socket path is redirected through the
`AIRULESET_TMUX_SOCK` seam the revive snippet reads.
"""
import errno
import fcntl
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as prof  # noqa: E402
from test_webterm import _FAKE_TMUX  # noqa: E402

HAVE_TMUX = shutil.which("tmux") is not None
_GONE = (errno.ESRCH, errno.EBADF, errno.ECHILD)


def _best_effort(fn, *args):
    """Teardown of a process/fd that may already be gone: an ESRCH/EBADF is
    the expected 'already cleaned' answer; anything else is reported."""
    try:
        fn(*args)
    except OSError as e:
        if e.errno not in _GONE:
            sys.stderr.write("revive1256 teardown %s%r: %s\n" % (fn.__name__, args, e))


def _scope(cmd, sock):
    """Every bare `tmux` command word gets `-S <sock>` (the
    test_webterm_ctrlbw_darkening `_socket_scope` transform)."""
    return re.sub(r'(?<![\w/])tmux\b', 'tmux -S ' + shlex.quote(sock), cmd)


def _listening_pids(path):
    """Pids holding a LISTENING unix socket bound to `path` (an orphaned
    server still lists its bound path in /proc/net/unix)."""
    inodes = set()
    with open("/proc/net/unix") as f:
        for line in f:
            p = line.split()
            if len(p) >= 8 and p[7] == path and p[3] == "00010000":
                inodes.add(p[6])
    pids = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            fds = os.listdir("/proc/%s/fd" % pid)
        except OSError:
            continue   # another uid's process, or it exited: not ours to read
        for fd in fds:
            try:
                link = os.readlink("/proc/%s/fd/%s" % (pid, fd))
            except OSError:
                continue   # the fd closed between listdir and readlink
            if link.startswith("socket:[") and link[8:-1] in inodes:
                pids.add(int(pid))
    return pids


@unittest.skipUnless(HAVE_TMUX, "tmux not installed")
class _RealTmux(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="revive1256-")
        self.home = os.path.join(self.dir, "home")
        os.mkdir(self.home)
        self.sock = os.path.join(self.dir, "sock")
        self.sock2 = None
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE")}
        self.env.update(HOME=self.home, TMUX_TMPDIR=self.dir,
                        AIRULESET_TMUX_SOCK=self.sock)
        if self.env.get("TERM", "dumb") in ("", "dumb"):
            self.env["TERM"] = "xterm-256color"
        self._clients = []
        self.addCleanup(self._teardown)

    def _teardown(self):
        for proc, master in self._clients:
            _best_effort(os.close, master)
            _best_effort(os.killpg, proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        for path in (self.sock, self.sock2):
            for pid in (_listening_pids(path) if path else ()):
                _best_effort(os.kill, pid, signal.SIGTERM)
        shutil.rmtree(self.dir, ignore_errors=True)

    def tmux(self, *args, sock=None):
        return subprocess.run(["tmux", "-S", sock or self.sock, *args],
                              capture_output=True, text=True, timeout=10,
                              env=self.env)

    def start_server(self, name, sock=None):
        r = self.tmux("-f", "/dev/null", "new-session", "-d", "-s", name,
                      sock=sock)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(self.tmux("list-sessions", "-F", "#{pid}",
                             sock=sock).stdout.split()[0])

    def server_pids_now(self):
        r = self.tmux("list-sessions", "-F", "#{pid}")
        return {int(x) for x in r.stdout.split()} if r.returncode == 0 else set()


class ReviveSnippetOnRealTmux(_RealTmux):
    """`cli_webterm.TMUX_REVIVE_SH`: when no server answers on the socket path
    but a server is still bound to that path (socket file deleted), SIGUSR1 it
    so it re-creates its socket — never start a new server."""

    def _revive(self, tail=""):
        return subprocess.run(["sh", "-c", _scope(w.TMUX_REVIVE_SH + tail, self.sock)],
                              capture_output=True, text=True, timeout=15,
                              env=self.env)

    def test_revives_the_orphan_instead_of_losing_it(self):
        pid = self.start_server("alpha")
        os.unlink(self.sock)
        self.assertEqual(self.server_pids_now(), set())   # unreachable now
        r = self._revive("tmux list-sessions -F '#{pid}'")
        self.assertEqual(r.stdout.split(), [str(pid)], r.stderr)
        self.assertTrue(os.path.exists(self.sock))

    def test_only_the_server_bound_to_the_seam_path_is_signalled(self):
        self.start_server("alpha")
        self.sock2 = os.path.join(self.dir, "other")
        self.start_server("beta", sock=self.sock2)
        os.unlink(self.sock)
        os.unlink(self.sock2)
        self._revive()
        self.assertTrue(os.path.exists(self.sock))
        self.assertFalse(os.path.exists(self.sock2),
                         "a server on a DIFFERENT socket path was revived")

    def test_healthy_server_is_left_alone(self):
        pid = self.start_server("alpha")
        before = os.stat(self.sock).st_ino
        self._revive()
        self.assertEqual(os.stat(self.sock).st_ino, before)
        self.assertEqual(self.server_pids_now(), {pid})


class AttachBodyNeverForksASecondServer(_RealTmux):
    """The REAL webterm attach body, run in a real pty: after the socket file
    is deleted under a live server, the connect must re-attach THAT server's
    session — one server bound to the path, same pid, no fresh session."""

    def _connect(self, preferred):
        body = _scope(w._remote_command(preferred), self.sock)
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 176, 0, 0))
        proc = subprocess.Popen(["sh", "-c", body], stdin=slave, stdout=slave,
                                stderr=slave, env=self.env, close_fds=True,
                                start_new_session=True)
        os.close(slave)
        self._clients.append((proc, master))
        deadline = time.time() + 8
        while time.time() < deadline:
            r = self.tmux("list-clients")
            if r.returncode == 0 and r.stdout.strip():
                return
            time.sleep(0.1)
        self.fail("the webterm client never attached")

    def test_reconnect_after_socket_loss_joins_the_original_server(self):
        pid = self.start_server("alpha")
        os.unlink(self.sock)
        self._connect("alpha")
        self.assertEqual(self.server_pids_now(), {pid},
                         "the connect started a NEW tmux server")
        self.assertEqual(_listening_pids(self.sock), {pid},
                         "two servers are bound to one socket path")
        names = self.tmux("list-sessions", "-F", "#{session_name}").stdout.split()
        self.assertEqual(names, ["alpha"])


class FallbackNeverJoinsAnotherHuman(unittest.TestCase):
    """The single-session fallback (#555, for gk's unnamed `0`) must never put
    a per-human tab into ANOTHER human's session (claudy, 2026-10-19)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        fake = Path(self.d) / "tmux"
        fake.write_text(_FAKE_TMUX, encoding="utf-8")
        os.chmod(fake, 0o755)
        self.log = Path(self.d) / "log"

    def _run(self, preferred, sessions):
        env = dict(os.environ, PATH=self.d + ":" + os.environ["PATH"],
                   FAKETMUX_SESSIONS=sessions, FAKETMUX_LOG=str(self.log))
        subprocess.run(["sh", "-c", w._remote_command(preferred)],
                       capture_output=True, text=True, env=env, timeout=10)
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_owner_tab_does_not_join_mareks_lone_session(self):
        log = self._run("zbynek", "::marek")
        self.assertNotRegex(log, r"attach-session -t marek\b")
        self.assertRegex(log, r"new-session -A -s zbynek\b")

    def test_marek_tab_does_not_join_the_owners_lone_session(self):
        log = self._run("marek", "::zbynek")
        self.assertNotRegex(log, r"attach-session -t zbynek\b")
        self.assertRegex(log, r"new-session -A -s marek\b")

    def test_unnamed_lone_session_is_still_joined(self):
        log = self._run("zbynek", "::0")
        self.assertRegex(log, r"attach-session -t 0 ")


class HumanSessionRegistry(unittest.TestCase):
    """`HUMAN_SESSION_NAMES` is the ONE list of per-human session names; every
    inventory `preferred` is either the target account's own name or a member."""

    def test_registry_members(self):
        self.assertEqual(w.HUMAN_SESSION_NAMES,
                         frozenset({w.OWNER_GROUP, prof.MAREK_GATEWAY_USER,
                                    prof.CODEX_PREFERRED}))

    def test_every_preferred_is_an_account_or_a_registered_human(self):
        profiles = [prof.OWNER, prof.DAVID, prof.MAREK, prof.DOMINIKA,
                    prof.TIMO, prof.PALO]
        for profile in profiles:
            for e in prof.profile_inventory(profile, []):
                pref = e.get("preferred")
                self.assertTrue(
                    pref == e.get("user") or pref in w.HUMAN_SESSION_NAMES,
                    "%s/%s: preferred=%r is neither the account nor a "
                    "registered human" % (profile, e.get("id"), pref))

    def test_project_account_sessions_are_registered_humans(self):
        import cli_account_bootstrap as b
        for acct, decl in b.SERVICE_ACCOUNTS.items():
            for name in decl.get("webterm_sessions") or ():
                self.assertTrue(name == acct or name in w.HUMAN_SESSION_NAMES,
                                "%s: session %r unregistered" % (acct, name))

    def test_rendered_command_carries_the_registry(self):
        cmd = w._remote_command("zbynek")
        self.assertIn("H=", cmd)
        for name in w.HUMAN_SESSION_NAMES:
            self.assertIn(name, cmd.split("H=", 1)[1].split(";", 1)[0])


if __name__ == "__main__":
    unittest.main()
