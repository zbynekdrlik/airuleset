"""#1310 — a new project account's tmux pane must come up with the launcher.

The root bootstrap (step 10) starts the account's tmux session BEFORE the
account's first ``airuleset.py install`` writes the ``claude()`` launcher into
``~/.bashrc``. Bash parses ``~/.bashrc`` once, so the pane's ``claude`` stayed
the bare binary (no bypass mode, no ``-c``) until a hand ``respawn-pane``
(mikrotik-pz, 12.11.2026). Install now respawns such a pane itself, but only an
idle login shell with no child, older than the launcher stamp, in a session
nobody is attached to.

Every test runs against a FAKE ``/proc`` tree and a FAKE tmux runner — never a
real tmux server, never a real process.
"""
import ast
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import airuleset  # noqa: E402
import cli_pane_respawn as pr  # noqa: E402

BTIME = 1_700_000_000
CLK = 100
STAMP = BTIME + 5_000          # the launcher landed 5000 s after boot


class _World:
    """A fake /proc, a fake tmux and a home with the launcher stamp."""

    def __init__(self, td):
        self.proc = os.path.join(td, "proc")
        os.makedirs(self.proc)
        with open(os.path.join(self.proc, "stat"), "w") as f:
            f.write("cpu  1 2 3\nbtime %d\nprocesses 9\n" % BTIME)
        self.home = os.path.join(td, "home", "acct")
        os.makedirs(os.path.join(self.home, ".claude"))
        os.makedirs(os.path.join(self.home, "devel", "proj"))
        self.since = os.path.join(self.home, ".claude", "airuleset-launcher-since")
        open(self.since, "w").close()
        os.utime(self.since, (STAMP, STAMP))
        self.panes = []            # [(pane_id, pid)]
        self.attached = "0"
        self.calls = []
        self.pid_after_check = {}  # pane_id -> pid reported on the re-check
        self.child_on_recheck = {}  # pid -> child that appears at the re-check
        self.rechecks = 0

    def add_proc(self, pid, cmdline, start, children=()):
        d = os.path.join(self.proc, str(pid))
        os.makedirs(os.path.join(d, "task", str(pid)))
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in cmdline) + b"\0")
        ticks = (start - BTIME) * CLK
        rest = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10
        with open(os.path.join(d, "stat"), "w") as f:
            f.write("%d (%s) %s\n" % (pid, cmdline[0].lstrip("-"), " ".join(rest)))
        with open(os.path.join(d, "task", str(pid), "children"), "w") as f:
            f.write(" ".join(str(c) for c in children) + (" " if children else ""))

    def add_pane(self, pane_id, pid, cmdline=("-bash",), start=STAMP - 60,
                 children=()):
        self.add_proc(pid, list(cmdline), start, children)
        self.panes.append((pane_id, pid))

    def run(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["tmux", "has-session"]:
            return 0, ""
        if "-t" in argv and argv[1] in ("display-message", "respawn-pane"):
            target = argv[argv.index("-t") + 1]
            if target.startswith("=") and ":" not in target:
                return 0, "\n"     # tmux 3.7b: a bare =name is no pane target
        if argv[:2] == ["tmux", "list-sessions"]:
            return 0, "%s proj\n1 projx\n" % self.attached
        if argv[:2] == ["tmux", "display-message"]:
            fmt = argv[-1]
            if fmt == "#{pane_pid}":
                self.rechecks += 1
                for pid, kid in self.child_on_recheck.items():
                    path = os.path.join(self.proc, str(pid), "task", str(pid),
                                        "children")
                    with open(path, "w") as f:
                        f.write("%d " % kid)
                pane = argv[argv.index("-t") + 1]
                pid = self.pid_after_check.get(pane, dict(self.panes).get(pane))
                return 0, "%s\n" % pid
        if argv[:2] == ["tmux", "list-panes"]:
            return 0, "".join("%s %d\n" % p for p in self.panes)
        if argv[:2] == ["tmux", "respawn-pane"]:
            return 0, ""
        return 1, ""

    def respawned(self):
        return [c[c.index("-t") + 1] for c in self.calls
                if c[:2] == ["tmux", "respawn-pane"]]

    def go(self, session="proj"):
        return pr.respawn_stale_panes(
            session, os.path.join(self.home, "devel", "proj"), self.since,
            run=self.run, proc=self.proc, clk_tck=CLK)


class TestRespawnDecision(unittest.TestCase):

    def test_idle_login_pane_older_than_the_launcher_is_respawned(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            lines = w.go()
            self.assertEqual(w.respawned(), ["%0"], lines)
            call = [c for c in w.calls if c[:2] == ["tmux", "respawn-pane"]][0]
            self.assertIn("-k", call)
            self.assertEqual(call[call.index("-c") + 1],
                             os.path.join(w.home, "devel", "proj"))
            self.assertEqual(call[-1], "exec bash -l")
            self.assertTrue(any("respawned" in ln for ln in lines), lines)

    def test_bash_dash_l_counts_as_a_login_shell(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001, cmdline=("bash", "-l"))
            w.go()
            self.assertEqual(w.respawned(), ["%0"])

    def test_pane_with_a_child_is_never_touched(self):
        """A running claude (or vim, a dev server) lives as the pane's child."""
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001, children=(4002,))
            w.add_proc(4002, ["claude"], STAMP - 30)
            lines = w.go()
            self.assertEqual(w.respawned(), [])
            self.assertTrue(any("child" in ln for ln in lines), lines)

    def test_pane_younger_than_the_launcher_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001, start=STAMP + 60)
            w.go()
            self.assertEqual(w.respawned(), [])

    def test_non_login_pane_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001, cmdline=("python3", "server.py"))
            w.go()
            self.assertEqual(w.respawned(), [])

    def test_attached_session_is_left_alone(self):
        """A human at the prompt may hold half-typed text."""
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.attached = "1"
            w.add_pane("%0", 4001)
            lines = w.go()
            self.assertEqual(w.respawned(), [])
            self.assertTrue(any("attached" in ln for ln in lines), lines)

    def test_pane_that_changed_since_the_check_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            w.pid_after_check["%0"] = 4999
            w.go()
            self.assertEqual(w.respawned(), [])

    def test_child_that_appears_before_the_respawn_keeps_the_pane(self):
        """The owner types `claude` between the check and the respawn."""
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            w.child_on_recheck[4001] = 4002
            w.go()
            self.assertEqual(w.rechecks, 1)
            self.assertEqual(w.respawned(), [])

    def test_only_the_stale_pane_of_several_is_respawned(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            w.add_pane("%1", 4011, children=(4012,))
            w.add_proc(4012, ["claude"], STAMP - 30)
            w.add_pane("%2", 4021, start=STAMP + 10)
            w.go()
            self.assertEqual(w.respawned(), ["%0"])

    def test_no_stamp_means_no_respawn(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            os.unlink(w.since)
            w.go()
            self.assertEqual(w.respawned(), [])

    def test_missing_session_is_a_quiet_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            w.run = lambda argv, _r=w.run: (1, "") if argv[1] == "has-session" else _r(argv)
            self.assertEqual(w.go(), [])
            self.assertEqual(w.respawned(), [])


    def test_vanished_pane_is_skipped_and_the_rest_still_run(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.panes.append(("%9", 4999))       # listed, but no /proc entry
            w.add_pane("%0", 4001)
            lines = w.go()
            self.assertEqual(w.respawned(), ["%0"])
            self.assertTrue(any("%9" in ln and "gone" in ln for ln in lines), lines)


class TestRealTmux(unittest.TestCase):
    """The same decision on a REAL tmux server of its own (`-S` private
    socket, `-f /dev/null`, TMUX unset) and the real /proc: the fake runner
    once hid a target syntax real tmux 3.7b does not resolve (review 🔴)."""

    def setUp(self):
        if not shutil.which("tmux"):
            self.fail("tmux is required for this test")
        self.dir = tempfile.mkdtemp(prefix="respawn1310-")
        self.sock = os.path.join(self.dir, "sock")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("TMUX", "TMUX_PANE")}
        self.env.update(HOME=self.dir, TMUX_TMPDIR=self.dir)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(self.tmux, "kill-server")

    def tmux(self, *args):
        r = subprocess.run(["tmux", "-S", self.sock, "-f", "/dev/null", *args],
                           capture_output=True, text=True, timeout=10,
                           env=self.env)
        return r.returncode, r.stdout

    def run_tmux(self, argv):
        self.assertEqual(argv[0], "tmux")
        return self.tmux(*argv[1:])

    def pids(self):
        return dict(ln.split() for ln in self.tmux(
            "list-panes", "-s", "-t", "=proj", "-F",
            "#{pane_id} #{pane_pid}")[1].splitlines())

    def test_idle_pane_respawned_busy_pane_kept(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", "proj", "-c",
                                   self.dir, "exec bash -l")[0], 0)
        self.assertEqual(self.tmux("split-window", "-t", "=proj:", "-c", self.dir,
                                   "bash -l -c 'sleep 60; :'")[0], 0)
        before = self.pids()
        busy = []
        for _ in range(50):                 # the sleep child forks after exec
            busy = [p for p, pid in before.items() if pr._children(pid, "/proc")]
            if busy:
                break
            time.sleep(0.1)
        self.assertEqual(len(busy), 1, before)
        since = os.path.join(self.dir, "since")
        open(since, "w").close()
        later = time.time() + 30
        os.utime(since, (later, later))
        lines = pr.respawn_stale_panes("proj", self.dir, since, run=self.run_tmux)
        after = self.pids()
        idle = [p for p in before if p not in busy][0]
        self.assertNotEqual(after[idle], before[idle], lines)
        self.assertEqual(after[busy[0]], before[busy[0]], lines)
        self.assertTrue(any("respawned" in ln for ln in lines), lines)

    def test_attached_count_is_read_by_exact_session_name(self):
        self.assertEqual(self.tmux("new-session", "-d", "-s", "proj", "-c",
                                   self.dir, "exec bash -l")[0], 0)
        self.assertEqual(pr._attached("proj", self.run_tmux), "0")
        self.assertIsNone(pr._attached("pro", self.run_tmux))


class TestInstallEntry(unittest.TestCase):

    def test_non_project_account_does_nothing(self):
        calls = []
        out = pr.maybe_respawn_stale_project_panes(
            user="nobody", run=lambda a: calls.append(a) or (0, ""),
            is_project=lambda u: False)
        self.assertEqual(out, [])
        self.assertEqual(calls, [])

    def test_real_runner_is_inert_under_pytest(self):
        """The default runner would drive the REAL tmux of whoever runs tests."""
        self.assertEqual(pr.maybe_respawn_stale_project_panes(
            user="mikrotik-pz", is_project=lambda u: True), [])

    def test_project_account_uses_its_declared_session_and_dir(self):
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            w.add_pane("%0", 4001)
            pr.maybe_respawn_stale_project_panes(
                user="mikrotik-pz", home=w.home, run=w.run, proc=w.proc,
                clk_tck=CLK, is_project=lambda u: True,
                spec={"tmux_session": "proj", "project_dir": "devel/proj"})
            self.assertEqual(w.respawned(), ["%0"])
            has = [c for c in w.calls if c[:2] == ["tmux", "has-session"]][0]
            self.assertEqual(has[-1], "=proj")

    def test_never_raises(self):
        def boom(argv):
            raise OSError("tmux gone")
        with tempfile.TemporaryDirectory() as td:
            w = _World(td)
            out = pr.maybe_respawn_stale_project_panes(
                user="mikrotik-pz", home=w.home, run=boom, proc=w.proc,
                is_project=lambda u: True,
                spec={"tmux_session": "proj", "project_dir": "devel/proj"})
        self.assertTrue(any("error" in ln for ln in out), out)


class TestInstallWiring(unittest.TestCase):

    def test_cmd_install_calls_the_respawn_step(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(airuleset.cmd_install)))
        names = {n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("maybe_respawn_stale_project_panes", names)
        self.assertIs(airuleset.maybe_respawn_stale_project_panes,
                      pr.maybe_respawn_stale_project_panes)


if __name__ == "__main__":
    unittest.main()
