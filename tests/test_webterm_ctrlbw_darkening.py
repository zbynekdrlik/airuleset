"""Live regression lock for #613 REOPEN-3 — `Ctrl+B w` (tmux's own window-
chooser, `choose-tree`) renders a BLACK screen for a client already attached
to a base tmux session the instant a webterm browser client ALSO joins that
base through the pre-fix THROWAWAY GROUPED CLONE shape. Root cause,
reproduction numbers, and the verified fix are recorded on GitHub issue #613
(supervisor comment 5387073996) and in the `_ATTACH_BODY` header comment in
cli_webterm.py.

SAFETY (read before touching this file — #613 incident, 2026-08-23): an
EARLIER version of this module set only `TMUX_TMPDIR` and believed that was
isolation. It was NOT: a tmux CLIENT resolves its socket from `$TMUX`
(inherited from the very pane THIS TEST ITSELF runs inside, since Claude
Code's own session lives in a real tmux pane) BEFORE it ever looks at
`TMUX_TMPDIR` — `TMUX_TMPDIR` only takes effect once `$TMUX` is unset. So
that version's `set-option -g window-size manual` rewrote the OWNER's real
global tmux options, and its teardown `kill-server` (no `-S`) killed his
REAL, live tmux server — with his live Claude Code session, running inside
that same server, dying with it. Twice, before the box was rebooted.

The fix, applied everywhere in this module, with NO alternative path:
  1. EVERY tmux invocation this module actually EXECUTES — every harness
     call, every pty client (including the client spawned to run the REAL
     `cli_webterm.build_connect_argv()` output), and teardown's
     `kill-server` — carries an EXPLICIT `-S <this test's own socket file>`
     on that SAME invocation. `-S` is a CLI argument, so it always wins
     over `$TMUX`/`TMUX_TMPDIR` (tmux's own documented precedence); nothing
     here relies on the environment alone.
  2. The REAL, unmodified production shell text
     (`cli_webterm.build_connect_argv()` / `_remote_command()`) is still
     what gets exercised — never a hand-reconstruction that could drift
     from the actual fix — but it is passed through `_socket_scope()`
     first: a small, auditable regex rewrite that inserts `-S <sock>`
     immediately after every bare `tmux` command word in that text, before
     it is ever executed. This is a TEST-ONLY transform (see
     `TestSocketScopeTransform` below for its own correctness lock) — it
     changes nothing about what cli_webterm.py ships; it only makes every
     invocation the test actually runs carry the explicit socket flag the
     production text (correctly, by design — see the `_ATTACH_BODY`
     header comment) never carries on its own.
  3. Every environment this module hands to a subprocess also has `TMUX`
     and `TMUX_PANE` stripped, and `TMUX_TMPDIR` pointed at this test's own
     tempdir — belt-and-suspenders alongside (1)/(2), never the primary
     guard.
  4. `_IsolatedTmuxServer.__init__` runs a PRE-FLIGHT check, before
     touching anything else: a FRESH `-S` socket path must report "no
     server running" (or otherwise show NO live session name) — never the
     box's real default socket content. It fails loudly if this doesn't
     hold, refusing to proceed.
  5. tests/test_tmux_test_isolation_lock.py is the MECHANICAL,
     repo-wide backstop: it statically fails the whole suite if any real
     `subprocess.run/Popen(...)` call under tests/ or hooks/ executes a
     destructive tmux subcommand with no `-S`/`-L` on the same invocation.

Method mirrors the supervisor's own probe (probe4.py): `pty.openpty()` +
`TIOCSWINSZ` pins each client to an EXACT terminal size before tmux ever
negotiates one, `Ctrl+B w` (`\\x02w`) is written to a client's pty master
and the output is drained and ANSI-stripped, and the window-chooser UI is
judged PRESENT only if BOTH of tmux's own header tokens ("sort:" and
"windows") survive — the exact criterion the supervisor's comment used to
tell "304 chars, status line only" (dead) apart from "4535 chars, full
tree" (alive).
"""
import fcntl
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_tmux_provisioning as tp  # noqa: E402
import cli_webterm as w  # noqa: E402

if shutil.which("tmux") is None:  # pragma: no cover
    raise RuntimeError(
        "tmux not found on PATH -- this module cannot run without it "
        "(every other tmux-driving test in this repo makes the same "
        "assumption, see tests/test_no_session_kill.py)")

# The owner's real fixed geometry (never a hand-picked literal — see
# _webterm_term_grid's own "never a duplicated literal" convention).
_COLS, _ROWS = (int(x) for x in tp.TMUX_DEFAULT_SIZE.lower().split("x"))
_SSH_CLIENT_ROWS = 49   # the owner's real Windows-Terminal client height
                        # (measured live, issue #613 comment 5387073996) --
                        # deliberately 1 row off the window's own 50, which
                        # comment 5387073996's round 1 proved is NOT the
                        # cause of the blackout (a control, not an oversight).

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][B0]|"
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)
# tmux's own choose-tree header renders both these tokens whenever the
# session/window tree is actually painted -- the supervisor's own criterion
# for "alive" ("windows (sort: ...)" per pane) vs "304 chars, status line
# only" (dead).
_CHOOSER_TOKENS = ("sort:", "windows")

# Any of these appearing where a FRESH, never-before-used socket should
# report emptiness is the #613 incident signature -- the pre-flight check
# refuses to proceed if it ever sees one.
_LIVE_SIGNATURES = ("/tmp/tmux-1000/default", "zbynek", "marek", "gatekeeper")


def _socket_scope(cmd, sock):
    """Rewrite every bare `tmux` COMMAND WORD in `cmd` to carry an explicit
    `-S <sock>` immediately after it -- see the module docstring's safety
    section, point 2. Word-boundary AND not-preceded-by-`/`-or-word-char,
    so this can never match part of a longer token (a path ending in
    `/tmux`, a variable named `...tmux...`); `_ATTACH_BODY` only ever
    invokes the bare command name `tmux`, verified by
    TestSocketScopeTransform below on the REAL current production text."""
    return re.sub(r'(?<![\w/])tmux\b', 'tmux -S ' + shlex.quote(sock), cmd)


def _scoped_connect_argv(entry, sock):
    """The REAL `cli_webterm.build_connect_argv(entry)` argv, with its
    embedded shell text passed through `_socket_scope()` before use. Only
    the `local` (`["sh", "-c", cmd]`) shape is needed here -- webterm's
    dashboard always resolves a browser tab through THIS shape for a local
    (dev1) or the connect script's own bare-tmux logic; a remote
    (ssh-prefixed) entry would need the SAME scoping applied on the far
    side of an ssh hop, out of reach for a local isolated-server test, and
    is not what #613 REOPEN-3's fix touches (the fix is inside
    `_ATTACH_BODY`, shared by both shapes)."""
    argv = list(w.build_connect_argv(entry))
    assert argv[:2] == ["sh", "-c"], (
        "build_connect_argv() shape changed -- update this scoping helper: %r"
        % (argv,))
    argv[2] = _socket_scope(argv[2], sock)
    return argv


def _visible(raw):
    return _ANSI_RE.sub("", raw.decode("utf-8", "replace"))


def _chooser_rendered(raw):
    text = _visible(raw)
    return all(tok in text for tok in _CHOOSER_TOKENS), text


def _drain(fd, seconds, poll=0.05):
    """Every byte available on `fd` for up to `seconds` wall-clock,
    non-blocking, coalescing however many separate writes tmux painted in.
    A WALL-CLOCK budget, not one fixed sleep -- a slow/busy box just gets
    more polls of the same budget, never a shorter read window (the ticket's
    own robustness requirement)."""
    buf = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            chunk = b""
        except OSError:
            break
        if chunk:
            buf += chunk
            continue
        time.sleep(poll)
    return buf


class _IsolatedTmuxServer:
    """One throwaway tmux SERVER, reached EXCLUSIVELY via an explicit
    `-S <sock>` on every single invocation this class makes (see the
    module docstring's safety section) — never `TMUX_TMPDIR`/`-L`/bare
    `tmux` reliance. `sock` is a path inside a fresh per-instance tempdir,
    guaranteed never previously bound to any server. A PRE-FLIGHT check
    runs in `__init__`, before anything else, refusing to proceed if that
    fresh socket does not report emptiness. Killed and removed
    unconditionally in `close()`."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="webterm613-")
        self.sock = os.path.join(self.dir, "sock")
        self.env = dict(os.environ)
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.env["TMUX_TMPDIR"] = self.dir   # defensive extra layer only; -S is primary
        self._clients = []   # [(Popen, master_fd), ...]
        self._preflight_check()

    def _preflight_check(self):
        # Requirement: before touching ANYTHING else, prove a FRESH -S
        # socket path is genuinely empty -- never the box's real default
        # socket content. tmux reports "no server running on <sock>" (or
        # similar) for a socket file that doesn't exist yet; a server-
        # existence-implying success here, or any live session name
        # leaking through, means -S isolation is somehow NOT holding, and
        # this refuses to proceed rather than risk repeating the incident.
        r = subprocess.run(["tmux", "-S", self.sock, "list-sessions"],
                           capture_output=True, text=True, timeout=10,
                           env=self.env)
        combined = r.stdout + r.stderr
        for sig in _LIVE_SIGNATURES:
            if sig in combined:
                shutil.rmtree(self.dir, ignore_errors=True)
                raise AssertionError(
                    "PRE-FLIGHT REFUSAL: a brand-new -S socket path %r "
                    "already reports live content (%r found) -- isolation "
                    "is NOT holding (the exact #613 incident signature). "
                    "Output: %r" % (self.sock, sig, combined))
        if r.returncode == 0:
            shutil.rmtree(self.dir, ignore_errors=True)
            raise AssertionError(
                "PRE-FLIGHT REFUSAL: 'tmux -S %r list-sessions' on a "
                "never-before-used socket path unexpectedly SUCCEEDED "
                "(rc=0) -- expected 'no server running'. Output: %r"
                % (self.sock, combined))

    def tmux(self, *args, timeout=10):
        return subprocess.run(["tmux", "-S", self.sock, *args],
                              capture_output=True, text=True, timeout=timeout,
                              env=self.env)

    def start_base(self, name, cols=_COLS, rows=_ROWS):
        r = self.tmux("-f", "/dev/null", "new-session", "-d", "-s", name,
                      "-x", str(cols), "-y", str(rows))
        assert r.returncode == 0, "new-session failed: %s" % (r.stderr,)
        # Confirm this session genuinely lives on OUR socket, never the
        # default one -- the module docstring's requirement 4, checked
        # again right after the first session-creating command.
        chk = self.tmux("display-message", "-t", name, "-p", "#{socket_path}")
        assert chk.returncode == 0, chk.stderr
        got = chk.stdout.strip()
        assert os.path.realpath(got) == os.path.realpath(self.sock), (
            "new-session landed on an unexpected socket: got %r, expected "
            "%r -- REFUSING to continue (the #613 incident)"
            % (got, self.sock))
        self.tmux("set-option", "-g", "window-size", tp.TMUX_WINDOW_SIZE)
        self.tmux("set-option", "-g", "default-size", "%dx%d" % (cols, rows))
        for extra in ("beta", "gamma", "delta"):   # a real tree needs >1 window
            self.tmux("new-window", "-t", name, "-n", extra)

    def has_session(self, name):
        return self.tmux("has-session", "-t", name).returncode == 0

    def client_count(self, name):
        r = self.tmux("list-clients", "-t", name)
        if r.returncode != 0:
            return 0
        return len([ln for ln in r.stdout.splitlines() if ln.strip()])

    def wait_for_clients(self, name, expected, timeout=6.0):
        """Poll `list-clients` until `expected` clients are attached, or
        `timeout` -- robust to a busy box where tmux's own accept lags a
        fixed sleep."""
        deadline = time.time() + timeout
        n = self.client_count(name)
        while time.time() < deadline and n < expected:
            time.sleep(0.1)
            n = self.client_count(name)
        return n

    def attach_client(self, argv, rows, cols=_COLS):
        """A REAL pty client running `argv` (already `-S`-scoped by the
        caller — either built directly with `["tmux", "-S", self.sock,
        ...]`, or via `_scoped_connect_argv()`), pinned to an exact
        (rows, cols) via TIOCSWINSZ before tmux ever negotiates a size --
        mirrors the supervisor's own probe technique. Returns the master
        fd."""
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
        proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                preexec_fn=os.setsid, close_fds=True,
                                env=self.env)
        os.close(slave)
        fcntl.fcntl(master, fcntl.F_SETFL, os.O_NONBLOCK)
        self._clients.append((proc, master))
        return master

    def kill_client(self, master_fd):
        """Terminate the client owning `master_fd` (SIGTERM, then SIGKILL
        if it hasn't exited within 3s -- verified live during this
        ticket's own dev-time probing that a tmux attach client does NOT
        reliably honour plain SIGTERM, unlike most processes)."""
        for i, (proc, fd) in enumerate(self._clients):
            if fd != master_fd:
                continue
            self._kill_proc(proc)
            try:
                os.close(fd)
            except OSError as e:
                print("_IsolatedTmuxServer.kill_client: close(fd=%r) "
                      "failed (already closed?): %r" % (fd, e),
                      file=sys.stderr)
            del self._clients[i]
            return

    def _kill_proc(self, proc):
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            print("_IsolatedTmuxServer._kill_proc: pid %d did not exit on "
                  "SIGTERM within 3s (expected for a tmux attach client, "
                  "see module docstring); sending SIGKILL"
                  % proc.pid, file=sys.stderr)
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("_IsolatedTmuxServer._kill_proc: pid %d still alive "
                  "after SIGKILL+5s; leaving it to kill-server/teardown "
                  "to reclaim (best-effort test cleanup)" % proc.pid,
                  file=sys.stderr)

    def close(self):
        for proc, _fd in list(self._clients):
            self._kill_proc(proc)
        for _proc, fd in self._clients:
            try:
                os.close(fd)
            except OSError as e:
                print("_IsolatedTmuxServer.close: close(fd=%r) failed "
                      "(already closed?): %r" % (fd, e), file=sys.stderr)
        self._clients = []
        self.tmux("kill-server")   # -S self.sock, via the wrapper -- NEVER the live default
        shutil.rmtree(self.dir, ignore_errors=True)


def _wait_for_chooser(fd, attempts=8, per_try=1.0):
    """Retry the drain-and-check several times instead of one fixed sleep
    -- robust to a busy box where tmux's own render lags (the ticket's own
    robustness requirement: 'retries on the assertion rather than one
    fixed sleep')."""
    text = ""
    for _ in range(attempts):
        raw = _drain(fd, per_try)
        ok, text = _chooser_rendered(raw)
        if ok:
            return True, text
    return False, text


class TestSocketScopeTransform(unittest.TestCase):
    """`_socket_scope()`'s own correctness lock, run against the REAL,
    current `cli_webterm._remote_command()` text (never a hand-written
    sample) -- if `_ATTACH_BODY` ever grows a NEW bare `tmux` invocation
    shape this transform doesn't catch, this fails FIRST and loudly,
    before the live tests below could ever silently under-scope one."""

    def test_every_tmux_word_in_the_real_attach_body_gets_scoped(self):
        cmd = w._remote_command("some-preferred-name")
        scoped = _socket_scope(cmd, "/fake/sock/for/this/check")
        unscoped = re.findall(r'(?<![\w/])tmux\b(?!\s+-S\s)', scoped)
        self.assertEqual(
            unscoped, [],
            "found a bare 'tmux' word in the REAL _ATTACH_BODY text that "
            "_socket_scope() did not guard with -S: %r\nscoped text:\n%s"
            % (unscoped, scoped))
        # sanity: it did rewrite something (never a silent no-op)
        self.assertGreater(scoped.count("-S /fake/sock/for/this/check"), 0)


class TestCtrlBWindowChooserRegression613(unittest.TestCase):
    """#613 REOPEN-3: attach the SSH-stand-in client to a fresh isolated
    base, then attach a SECOND (webterm) client, press Ctrl+B w on the
    FIRST client, and check whether tmux painted the chooser tree. RED on
    the removed clone shape (rebuilt by hand in
    test_grouped_clone_shape_reproduces_the_pre_fix_blackout, since the
    fixed code can no longer produce it), GREEN via the REAL, unmodified
    `cli_webterm.build_connect_argv()` production path (socket-scoped by
    `_scoped_connect_argv()`, see module docstring)."""

    def setUp(self):
        self.cluster = _IsolatedTmuxServer()
        self.addCleanup(self.cluster.close)
        self.cluster.start_base("base")
        self.ssh_fd = self.cluster.attach_client(
            ["tmux", "-S", self.cluster.sock, "attach", "-t", "base"],
            rows=_SSH_CLIENT_ROWS)
        self.cluster.wait_for_clients("base", 1)
        _drain(self.ssh_fd, 1.0)

    def _press_ctrl_b_w_and_check(self):
        os.write(self.ssh_fd, b"\x02w")
        rendered, text = _wait_for_chooser(self.ssh_fd)
        os.write(self.ssh_fd, b"\x1b")   # back out of the chooser, tidy
        _drain(self.ssh_fd, 0.5)
        return rendered, text

    def test_direct_attach_keeps_the_window_chooser_alive(self):
        # THE FIX (GREEN): the EXACT command `cli_webterm` builds for a
        # webterm client today -- build_connect_argv() on a `local`
        # inventory entry, unmodified production LOGIC, only socket-scoped
        # for this isolated test run (see _scoped_connect_argv()).
        entry = {"local": True, "preferred": "base"}
        argv = _scoped_connect_argv(entry, self.cluster.sock)
        self.cluster.attach_client(argv, rows=_SSH_CLIENT_ROWS)
        self.cluster.wait_for_clients("base", 2)
        _drain(self.ssh_fd, 1.0)

        rendered, text = self._press_ctrl_b_w_and_check()
        self.assertTrue(
            rendered,
            "Ctrl+B w must still render the window tree once a webterm "
            "client is attached via the real production connect path "
            "(#613); painted %d visible chars, chooser tokens %r not "
            "found:\n%s" % (len(text), _CHOOSER_TOKENS, text[:500]))

    def test_grouped_clone_shape_reproduces_the_pre_fix_blackout(self):
        # THE BUG, locked so it can never silently come back unnoticed.
        # Built BY HAND here (never via cli_webterm, which IS the fix
        # under test and can no longer produce this shape) -- the exact
        # pre-fix topology: a second, same-group session created per
        # connect, attached with `-f ignore-size`. Matches issue #613
        # comment 5387073996's own round-2/round-4 measurement ("304
        # chars, status line only" / "176 chars" -- dead either way).
        clone = "base-web-clonecheck"
        self.cluster.tmux("new-session", "-d", "-t", "base", "-s", clone)
        self.cluster.attach_client(
            ["tmux", "-S", self.cluster.sock, "attach-session", "-t", clone,
             "-f", "ignore-size"],
            rows=_SSH_CLIENT_ROWS)
        self.cluster.wait_for_clients("base", 2)
        _drain(self.ssh_fd, 1.0)

        rendered, text = self._press_ctrl_b_w_and_check()
        self.assertFalse(
            rendered,
            "the grouped-clone shape was expected to reproduce the #613 "
            "blackout (this LOCKS the bug's mechanism, not the fix) -- got "
            "%d visible chars instead, which means the clone shape no "
            "longer reproduces the bug on this tmux build:\n%s"
            % (len(text), text[:500]))


class TestBaseSessionSurvivesBrowserDisconnect(unittest.TestCase):
    """#613 REOPEN-3 explicit requirement: the base session must NEVER be
    destroyed by a browser disconnect. Proven two ways: (1) no
    `destroy-unattached` policy is armed anywhere, global OR session-level
    on the base -- tmux keeps its factory default `off`; (2) a LONE
    webterm client (no ssh client at all) attaching and then disconnecting
    leaves the base alive."""

    def setUp(self):
        self.cluster = _IsolatedTmuxServer()
        self.addCleanup(self.cluster.close)
        self.cluster.start_base("base")

    def test_no_destroy_unattached_armed_globally_or_on_the_base(self):
        g = self.cluster.tmux("show-options", "-g", "destroy-unattached")
        self.assertEqual(g.returncode, 0, g.stderr)
        self.assertIn("off", g.stdout,
                      "global destroy-unattached must stay tmux's factory "
                      "default (off): %r" % (g.stdout,))
        # A session-level OVERRIDE would show as its OWN line here; an
        # unset session option prints nothing (inherits the global).
        s = self.cluster.tmux("show-options", "-t", "base", "destroy-unattached")
        self.assertNotIn("destroy-unattached on", s.stdout)
        self.assertNotIn("destroy-unattached", s.stdout,
                         "the base must carry no session-level "
                         "destroy-unattached override at all: %r" % (s.stdout,))

    def test_lone_browser_disconnect_leaves_base_alive(self):
        # The REAL production connect path (socket-scoped), no ssh/owner
        # client attached at all -- the scenario the ticket explicitly
        # called out ("a browser disconnect with no ssh client attached
        # leaves the session alive").
        entry = {"local": True, "preferred": "base"}
        argv = _scoped_connect_argv(entry, self.cluster.sock)
        web_fd = self.cluster.attach_client(argv, rows=_SSH_CLIENT_ROWS)
        self.cluster.wait_for_clients("base", 1)
        _drain(web_fd, 1.0)

        self.assertTrue(self.cluster.has_session("base"),
                        "sanity: base must exist while the browser is attached")
        self.cluster.kill_client(web_fd)
        self.cluster.wait_for_clients("base", 0)

        self.assertTrue(
            self.cluster.has_session("base"),
            "the base session must survive a lone browser disconnect -- "
            "#613 REOPEN-3 explicit requirement (no clone-only sweep hook "
            "left armed against it)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
