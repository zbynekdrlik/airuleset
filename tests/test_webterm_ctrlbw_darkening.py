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
import signal
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
        fixed sleep. ASSERTS the expected count was actually reached
        (adversarial review 🟡: an unchecked return value here let a
        SILENT attach failure masquerade as a false GREEN on the fix-proof
        test -- Ctrl+B w naturally renders fine on a single client, so a
        webterm client that silently never attached would still pass)."""
        deadline = time.time() + timeout
        n = self.client_count(name)
        while time.time() < deadline and n < expected:
            time.sleep(0.1)
            n = self.client_count(name)
        assert n == expected, (
            "expected %d client(s) attached to %r within %.1fs, got %d -- "
            "an attach silently failed or is still pending" % (
                expected, name, timeout, n))
        return n

    def attach_client(self, argv, rows, cols=_COLS):
        """A REAL pty client running `argv` (already `-S`-scoped by the
        caller — either built directly with `["tmux", "-S", self.sock,
        ...]`, or via `_scoped_connect_argv()`), pinned to an exact
        (rows, cols) via TIOCSWINSZ before tmux ever negotiates a size --
        mirrors the supervisor's own probe technique. Returns the master
        fd.

        RUNTIME safety guard (adversarial review 🟡: unlike `self.tmux()`,
        whose own literal `-S` is structurally visible to
        tests/test_tmux_test_isolation_lock.py's static AST scan, this
        method takes an arbitrary caller-supplied `argv` -- a Name node,
        invisible to that same static scan, so a future call site with no
        `-S` anywhere would be caught by NEITHER the static lock nor a
        docstring convention alone). Refuses at RUNTIME, before spawning
        anything, unless `-S` appears as a standalone token somewhere in
        `argv` -- covering both the direct `["tmux", "-S", sock, ...]`
        shape and the `["sh", "-c", "<scoped shell text>"]` shape (the
        `-S` lives inside the joined text either way)."""
        joined = " ".join(str(a) for a in argv)
        assert re.search(r"(?<!\S)-S(?!\S)", joined), (
            "attach_client() refuses an argv with no explicit -S guard "
            "anywhere -- exactly the #613 incident's failure class. "
            "argv=%r" % (argv,))
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
        reliably honour plain SIGTERM, unlike most processes). Forceful,
        guaranteed cleanup -- for a GRACEFUL disconnect that gives a
        disconnect TRAP its fair chance to run first, use
        `disconnect_client()` instead."""
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

    def disconnect_client(self, master_fd, timeout=5.0):
        """A GRACEFUL disconnect, distinct from `kill_client()`'s forceful
        one: closes the pty MASTER fd first (the kernel's own "controlling
        terminal hung up" mechanism -- the exact way a real ttyd child
        learns its browser/websocket disconnected), which delivers SIGHUP
        to the WHOLE foreground process group attached to that pty (both
        the wrapper shell AND its `tmux attach-session` child, since the
        child inherits the shell's `setsid`-created group and never calls
        setsid itself). This gives `_ATTACH_BODY`'s disconnect trap (armed
        on EXIT/HUP/INT/TERM) its natural, unforced chance to run BEFORE
        any signal is sent directly -- needed to prove the trap's SIDE
        EFFECT (mouse reverting off) actually happens, not just that the
        process eventually dies. Falls back to the forceful `_kill_proc`
        (process-group SIGTERM/SIGKILL) only if the process does not exit
        within `timeout` on its own."""
        for i, (proc, fd) in enumerate(self._clients):
            if fd != master_fd:
                continue
            try:
                os.close(fd)
            except OSError as e:
                print("_IsolatedTmuxServer.disconnect_client: close(fd=%r) "
                      "failed (already closed?): %r" % (fd, e),
                      file=sys.stderr)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                print("_IsolatedTmuxServer.disconnect_client: pid %d did "
                      "not exit within %.1fs of its pty master closing; "
                      "falling back to a forceful kill" % (proc.pid, timeout),
                      file=sys.stderr)
                self._kill_proc(proc)
            del self._clients[i]
            return

    def _kill_proc(self, proc):
        """Forceful teardown: SIGTERM then SIGKILL, delivered to the
        WHOLE PROCESS GROUP (`os.killpg`), not just `proc.pid` alone.
        Every client here is spawned with `preexec_fn=os.setsid`, so
        `proc.pid` doubles as the group id for its whole subtree -- this
        matters because the mouse-revert fix (#613 REOPEN-3 review round)
        removed the join branch's `exec`, so `proc.pid` is now the WRAPPER
        SHELL, with `tmux attach-session` running as its own separate
        CHILD process; signaling only the shell's single pid would leave
        that child tmux process orphaned and still attached, which would
        hang every `wait_for_clients(..., 0)` assertion in this file."""
        if proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            print("_IsolatedTmuxServer._kill_proc: pgid %d did not exit on "
                  "SIGTERM within 3s (expected for a tmux attach client, "
                  "see module docstring); sending SIGKILL"
                  % pgid, file=sys.stderr)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("_IsolatedTmuxServer._kill_proc: pgid %d still alive "
                  "after SIGKILL+5s; leaving it to kill-server/teardown "
                  "to reclaim (best-effort test cleanup)" % pgid,
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
        # `list-clients -t <target>` counts clients by their CURRENT
        # SESSION NAME, not by group membership -- a client attached to
        # the CLONE is counted under the clone's own name, never under
        # "base", even though they share a window group. Wait on each
        # target separately (an earlier version of this test waited on
        # "base" for both clients and silently proceeded with only 1
        # actually attached -- caught by wait_for_clients()'s own
        # adversarial-review-added assertion).
        self.cluster.wait_for_clients("base", 1)
        self.cluster.wait_for_clients(clone, 1)
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


class TestMouseRevertsOnDisconnect(unittest.TestCase):
    """#648 (FIX LANDED, Option 2), live proof against a REAL tmux server.
    Background: #613 REOPEN-3 armed a disconnect trap that FORCED `mouse
    off` to revert the connect-set `mouse on`. #646 then made `-g mouse on`
    the managed fleet-wide default -- and a session-LOCAL `mouse off`
    OVERRIDES `-g mouse on`, so after a webterm connect+disconnect the
    owner's OWN ssh session (attached to the SAME session) was left
    mouse-off until it restarted. The fix: the disconnect trap now UNSETS
    the session-local override (`set-option -u -t "$T" mouse`) instead of
    forcing a value, so the effective value falls back to inheritance. This
    proves the SIDE EFFECT end-to-end against the REAL, unmodified
    production connect path -- not just that the trap TEXT is right (that is
    tests/test_webterm.py's job), but that a real tmux server's session
    option genuinely stops overriding the global once the client
    disconnects."""

    def setUp(self):
        self.cluster = _IsolatedTmuxServer()
        self.addCleanup(self.cluster.close)
        self.cluster.start_base("base")

    def _mouse_local(self):
        # The SESSION-LOCAL mouse override only. `show-options` WITHOUT `-A`
        # reports a value ONLY when set at the session scope; an unset
        # session option (inheriting the global) reads as "" -- exactly how
        # we prove the trap UNSET the local override (vs the old forced-off,
        # which left "mouse off" set here).
        r = self.cluster.tmux("show-options", "-t", "base", "mouse")
        return r.stdout.strip()

    def _mouse_effective(self):
        # The EFFECTIVE computed value INCLUDING inheritance, as "on"/"off".
        # `#{mouse}` renders 1/0 whether the value is set locally OR
        # inherited from the global -- version-robust (no `mouse* on`
        # asterisk parsing needed).
        v = self.cluster.tmux("display-message", "-t", "base", "-p",
                              "#{mouse}").stdout.strip()
        return {"1": "on", "0": "off"}.get(v, v)

    def test_mouse_reverts_to_the_fleet_global_after_disconnect(self):
        # The real post-#646 fleet scenario: `-g mouse on` is set globally
        # (cli_tmux_provisioning's managed default). A webterm connect sets a
        # redundant session-local `mouse on`; on disconnect the trap must
        # UNSET it (not force `mouse off`), so the session falls back to the
        # global `on` -- NOT off. Before the fix the forced `mouse off`
        # session-local override won over `-g mouse on` and left the owner's
        # ssh session mouse-off (the bug this ticket fixes).
        self.cluster.tmux("set-option", "-g", "mouse", "on")
        self.assertEqual(
            self._mouse_local(), "",
            "sanity: no session-LOCAL override before any webterm connect")
        self.assertEqual(
            self._mouse_effective(), "on",
            "sanity: effective mouse inherits the -g mouse on global")

        entry = {"local": True, "preferred": "base"}
        argv = _scoped_connect_argv(entry, self.cluster.sock)
        web_fd = self.cluster.attach_client(argv, rows=_SSH_CLIENT_ROWS)
        self.cluster.wait_for_clients("base", 1)
        _drain(web_fd, 1.0)

        self.assertEqual(
            self._mouse_local(), "mouse on",
            "a webterm connect sets the session-local mouse on (#615)")

        self.cluster.disconnect_client(web_fd)
        self.cluster.wait_for_clients("base", 0)

        # THE FIX: the trap UNSET the local override, so it is GONE ...
        self.assertEqual(
            self._mouse_local(), "",
            "the disconnect trap must UNSET the session-local mouse "
            "override (restore inheritance), never force a value -- a "
            "leftover 'mouse off' here is exactly the #648 bug")
        # ... and the effective value inherits the fleet global `on` again.
        self.assertEqual(
            self._mouse_effective(), "on",
            "effective mouse must inherit the -g mouse on global after "
            "disconnect -- a forced session-local 'mouse off' overriding the "
            "fleet default is the regression #648 removed")

    def test_disconnect_trap_never_forces_a_value_with_no_global(self):
        # With NO global set (a box not provisioned by #646), the trap must
        # STILL only UNSET the local override -- leaving it GONE (inheriting
        # tmux's factory default off), never a forced session-local
        # `mouse off`. Both old and new code read effective "off" here, so
        # the DISCRIMINATOR is the LOCAL override: the old forced-off left
        # "mouse off" set locally, the fix leaves it unset ("").
        self.assertEqual(
            self._mouse_local(), "",
            "sanity: mouse unset (factory default) before any connect")

        entry = {"local": True, "preferred": "base"}
        argv = _scoped_connect_argv(entry, self.cluster.sock)
        web_fd = self.cluster.attach_client(argv, rows=_SSH_CLIENT_ROWS)
        self.cluster.wait_for_clients("base", 1)
        _drain(web_fd, 1.0)

        self.assertEqual(
            self._mouse_local(), "mouse on",
            "a webterm connect sets the session-local mouse on (#615)")

        self.cluster.disconnect_client(web_fd)
        self.cluster.wait_for_clients("base", 0)

        self.assertEqual(
            self._mouse_local(), "",
            "the trap must UNSET the local override (leaving it inherit the "
            "factory default), never force a session-local 'mouse off' -- a "
            "leftover 'mouse off' is the old, buggy forced-off shape")
        self.assertEqual(
            self._mouse_effective(), "off",
            "with no global, the effective value is tmux's factory default "
            "off once the local override is unset")


class TestWindowMenuRebind613Round2(unittest.TestCase):
    """#613 REOPEN-2 round-2: the owner's Ctrl+B w blackout is an UPSTREAM
    tmux 3.7b bug -- `choose-tree` renders ONLY to clients of the NEWEST
    grouped session, so the client of an OLDER grouped session (the owner's
    webterm, attached to the base while `tmux new -t zbynek` keeps forming a
    newer grouped sibling on every terminal attach) gets a black screen. The
    fix rebinds `prefix+w` fleet-wide to a `run-shell` window picker built
    from `display-menu`, which renders on EVERY client (measured 17952 chars
    on the exact dead base client, vs 176 for choose-tree). See the design +
    validation comments on issue #613 (2026-08-24).

    Three locks, mirroring #646's mouse-line shape (render + live-apply) plus
    a REAL isolated-tmux behavioral regression using this module's own
    `_IsolatedTmuxServer` harness -- RED before the rebind exists (default
    choose-tree on a base-with-sibling paints no window list), GREEN once the
    managed conf carries the rebind."""

    # (a) the managed conf marker block carries the rebind (render lock,
    # mirrors #646's mouse-line conf-block test).
    def test_managed_block_rebinds_prefix_w_to_the_window_menu(self):
        block = tp.render_tmux_history_block(window_size_manual=True)
        self.assertIn(
            "bind-key w run-shell", block,
            "the managed tmux block must rebind prefix+w to a run-shell "
            "window picker (dodging the upstream grouped-session choose-tree "
            "bug), so choose-tree never blackens an older grouped client:\n%s"
            % block)
        self.assertIn(
            "airuleset-tmux-window-menu.sh", block,
            "the rebind must invoke the deployed window-menu helper script "
            "by absolute path (the #289 popup-script precedent -- inline "
            "shell is impossible, _tmux_conf_quote refuses the literal $ the "
            "menu generator needs):\n%s" % block)
        self.assertIn(
            "#{client_name}", block,
            "the rebind must hand run-shell the pressing client's name so "
            "the helper's `display-menu -c` targets the exact (dead) "
            "client:\n%s" % block)

    # (b) the rebind is live-applied to a running server (a running tmux
    # never re-reads the conf -- same live-apply class as the popup bind).
    def test_window_menu_rebind_is_live_applied(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "tmux.conf")
            calls = []
            tp.apply_tmux_history_limit(Path(conf), run=calls.append)
        menu = [c for c in calls
                if list(c[:3]) == ["tmux", "bind-key", "w"]
                and any("run-shell" in str(x) for x in c)]
        self.assertEqual(
            len(menu), 1,
            "apply_tmux_history_limit must live-apply the prefix+w window-menu "
            "rebind exactly once (a running server never re-reads the conf); "
            "found %d such calls in:\n%s" % (len(menu), calls))
        self.assertTrue(
            any("airuleset-tmux-window-menu.sh" in str(x) for x in menu[0]),
            "the live-applied rebind must point at the deployed helper "
            "script: %r" % (menu[0],))

    # (c) THE behavioral regression: the exact dead-client shape (a client
    # on the OLDER grouped base, a newer grouped sibling present) -- press
    # Ctrl+B w and require the window picker to actually paint the window
    # list. RED with the default binding (choose-tree paints nothing here),
    # GREEN with the production rebind applied.
    def test_window_menu_renders_on_the_dead_grouped_base_client(self):
        cluster = _IsolatedTmuxServer()
        self.addCleanup(cluster.close)
        cluster.start_base("base")   # base + windows beta/gamma/delta

        # Derive the EXACT rebind argv production would live-apply (never a
        # hand-written one) -- absent in RED, so nothing is applied and the
        # default choose-tree stays bound.
        applied = self._apply_production_rebind(cluster)

        # A NEWER grouped sibling with its own client -- the owner's real
        # topology (webterm on the older base, WT on the newer sibling). This
        # is the shape the round-2 matrix measured as dead for choose-tree.
        cluster.tmux("new-session", "-d", "-t", "base", "-s", "sib")
        base_fd = cluster.attach_client(
            ["tmux", "-S", cluster.sock, "attach", "-t", "base"],
            rows=_SSH_CLIENT_ROWS)
        cluster.attach_client(
            ["tmux", "-S", cluster.sock, "attach", "-t", "sib"],
            rows=_SSH_CLIENT_ROWS)
        cluster.wait_for_clients("base", 1)
        cluster.wait_for_clients("sib", 1)
        _drain(base_fd, 1.0)

        os.write(base_fd, b"\x02w")
        # The MENU-SPECIFIC discriminator is the picker's own title "Okná"
        # (adversarial review #613r2, B1): window NAMES leak into tmux's default
        # status line, so they render even in the choose-tree blackout ("status
        # line only") -- name-presence has no RED/GREEN teeth. The menu title
        # appears ONLY when display-menu actually paints, never in the status
        # line or a choose-tree render.
        text = ""
        for _ in range(8):
            text += _visible(_drain(base_fd, 1.0))
            if "Okná" in text:
                break
        # Press the digit shown on window index 1's row (base-index defaults to
        # 0, so index 1 == the "beta" window). With the picker painted and the
        # hot-key == the shown index (A1), this SELECTS that window -- proving
        # the menu is live AND the key mapping is correct, not just that pixels
        # appeared. This is the real behavioral teeth.
        os.write(base_fd, b"1")
        _drain(base_fd, 0.8)
        cur = cluster.tmux(
            "display-message", "-p", "-t", "base", "#{window_index}").stdout.strip()

        self.assertTrue(
            applied,
            "the production window-menu rebind was not found in "
            "apply_tmux_history_limit's live-apply calls -- the fix is not "
            "wired in (RED)")
        self.assertIn(
            "Okná", text,
            "prefix+w on the OLDER grouped base client must PAINT the window "
            "picker (its title 'Okná'); got %d visible chars, this is the exact "
            "upstream choose-tree blackout the rebind fixes:\n%s"
            % (len(text), text[:600]))
        self.assertEqual(
            cur, "1",
            "pressing the digit shown on a menu row must select that window "
            "index (base-index 0 -> row '1:' selects window 1); current window "
            "index is %r -- the number hot-key is wrong (A1)" % cur)

    def _apply_production_rebind(self, cluster):
        """Capture the rebind argv `apply_tmux_history_limit` would live-apply,
        deploy the REAL helper script to a throwaway path, repoint the argv at
        it, and apply it to `cluster`'s isolated server. Returns True iff a
        rebind argv existed (False in RED -> nothing applied)."""
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "tmux.conf")
            calls = []
            tp.apply_tmux_history_limit(Path(conf), run=calls.append)
        menu = [c for c in calls
                if list(c[:3]) == ["tmux", "bind-key", "w"]
                and any("run-shell" in str(x) for x in c)]
        dest = getattr(tp, "WINDOW_MENU_SCRIPT_DEST", None)
        render = getattr(tp, "render_window_menu_script", None)
        if not menu or dest is None or render is None:
            return False
        script = os.path.join(cluster.dir, "window-menu.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(render())
        os.chmod(script, 0o755)
        # repoint the (baked-in absolute) helper path at the throwaway copy
        argv = [str(x).replace(str(dest), script) for x in menu[0]]
        r = cluster.tmux(*argv[1:])   # argv[0] == "tmux"; cluster.tmux adds -S
        self.assertEqual(r.returncode, 0, "live-applying the rebind failed: %s" % r.stderr)
        return True


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
