"""Orphaned tmux socket recovery (#318, 2026-08-08).

Incident: subdev's `/tmp/tmux-1000/` was reaped (recreated empty) while the
tmux SERVER process and david's `claude` session both kept running. Every
watchdog job funnels through `list_claude_panes()`, which reads
`tmux list-panes -a` via the injectable `run` shim — on ANY failure (socket
unreachable, tmux genuinely not running, whatever) that shim degrades to a
bare `""`, indistinguishable from "zero panes exist". Job 8 (`bounce_backstop`)
then built a false "nebeží žiadna Claude session" Discord ping for a repo
whose session was alive the whole time.

`list_claude_panes` must self-heal the ORPHANED-SERVER shape specifically
(server process alive, socket file missing) via `SIGUSR1` — tmux's own
documented recovery for exactly this — while leaving the ordinary "no tmux
server at all" case completely unchanged.

Most tests here drive the REAL filesystem check (`_tmux_socket_missing`)
against a scratch `TMUX_TMPDIR`, rather than mocking that helper — so the
RED proof is a genuine behavioral mismatch (list_claude_panes wrongly
returns `[]` for a session that IS alive), not merely "the new function
doesn't exist yet".
"""

import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

REAL_PANES = "%7\tclaude\t/home/david/devel/odoo/odoo-erp\t8901\n"


class _FakeTmux:
    """Models `tmux list-panes -a` returning empty until a `kill -USR1`
    lands, then returning real pane data — the orphaned-socket-then-recovered
    shape. `server_pid=None` models "no tmux server process at all"."""

    def __init__(self, server_pid=1371, panes_after_recovery=REAL_PANES,
                 recovers=True):
        self.server_pid = server_pid
        self.panes_after_recovery = panes_after_recovery
        self.recovers = recovers
        self.calls = []
        self.recovered = False

    def __call__(self, argv, timeout=8):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "list-panes" in joined:
            if self.recovered:
                return self.panes_after_recovery
            return ""
        if argv[:1] == ["ps"]:
            if self.server_pid is None:
                return "1\tsystemd\n2\tbash\n"
            return "%d\ttmux: server\n" % self.server_pid
        if argv[:2] == ["kill", "-USR1"]:
            if self.recovers:
                self.recovered = True
            return ""
        return ""

    def kill_calls(self):
        return [a for a in self.calls if a[:2] == ["kill", "-USR1"]]

    def ps_calls(self):
        return [a for a in self.calls if a[:1] == ["ps"]]


class _ScratchSocketDir(TemporaryDirectory):
    """A throwaway TMUX_TMPDIR. `present=True` also creates the exact
    tmux-<uid>/default path so `_tmux_socket_missing()` reads False against
    a real filesystem check — no mocking of the helper itself needed."""

    def __init__(self, present=False):
        super().__init__()
        self._present = present

    def __enter__(self):
        base = super().__enter__()
        if self._present:
            d = Path(base) / ("tmux-%d" % os.getuid())
            d.mkdir(parents=True)
            (d / "default").touch()
        return base


class TestOrphanedSocketRecovery(unittest.TestCase):
    def test_orphaned_socket_recovers_and_returns_real_panes(self):
        tmux = _FakeTmux()
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [("%7", "/home/david/devel/odoo/odoo-erp")])
        self.assertEqual(tmux.kill_calls(), [["kill", "-USR1", "1371"]])

    def test_no_tmux_server_at_all_is_unchanged_no_recovery_attempted(self):
        tmux = _FakeTmux(server_pid=None)
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [])
        self.assertEqual(tmux.kill_calls(), [])

    def test_socket_present_pid_found_no_recovery_attempted(self):
        # server process is alive AND the socket file genuinely exists —
        # some OTHER reason list-panes failed (permissions, a transient
        # blip) — must never fire SIGUSR1 at a healthy server.
        tmux = _FakeTmux()
        with _ScratchSocketDir(present=True) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [])
        self.assertEqual(tmux.kill_calls(), [])

    def test_healthy_output_never_probes_ps_at_all(self):
        tmux = _FakeTmux()
        tmux.recovered = True  # list-panes already answers for real
        res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [("%7", "/home/david/devel/odoo/odoo-erp")])
        self.assertEqual(tmux.ps_calls(), [])
        self.assertEqual(tmux.kill_calls(), [])

    def test_logs_capture_recovery_attempt_and_success(self):
        tmux = _FakeTmux()
        logs = []
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            wd.list_claude_panes(tmux, logs=logs)
        self.assertTrue(any("tmux-socket-orphaned" in ln for ln in logs), logs)
        self.assertTrue(any("tmux-socket-recovered" in ln for ln in logs), logs)

    def test_logs_capture_recovery_failure(self):
        tmux = _FakeTmux(recovers=False)
        logs = []
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux, logs=logs)
        self.assertEqual(res, [])
        self.assertTrue(
            any("tmux-socket-recovery-failed" in ln for ln in logs), logs)

    def test_no_logs_param_is_backward_compatible(self):
        # every pre-existing caller passes no `logs=` at all
        tmux = _FakeTmux()
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [("%7", "/home/david/devel/odoo/odoo-erp")])


class TestTmuxServerPidHelper(unittest.TestCase):
    def test_finds_real_server_pid(self):
        run = _FakeTmux(server_pid=4242)
        self.assertEqual(wd._tmux_server_pid(run), 4242)

    def test_returns_none_when_absent(self):
        run = _FakeTmux(server_pid=None)
        self.assertIsNone(wd._tmux_server_pid(run))

    def test_malformed_ps_line_is_ignored(self):
        def run(argv, timeout=8):
            if argv[:1] == ["ps"]:
                return "not-a-pid\ttmux: server\n"
            return ""
        self.assertIsNone(wd._tmux_server_pid(run))


class TestTmuxSocketMissingHelper(unittest.TestCase):
    def test_missing_path_is_true(self):
        self.assertTrue(wd._tmux_socket_missing("/nonexistent/path/xyz-318"))

    def test_existing_path_is_false(self):
        self.assertFalse(wd._tmux_socket_missing(__file__))

    def test_default_path_honors_tmux_tmpdir_env(self):
        with _ScratchSocketDir(present=True) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            self.assertFalse(wd._tmux_socket_missing())
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            self.assertTrue(wd._tmux_socket_missing())


class TestBounceBackstopSurfacesSocketRecovery(unittest.TestCase):
    """Job 8's own `logs=` wiring — the incident's own reporting path shows
    the recovery attempt in the journal instead of just a bare unexplained
    'no panes' the moment before the false Discord ping fires."""

    def test_recovery_is_logged_and_the_pane_is_used_not_a_false_ping(self):
        import json
        import time
        import statusbar

        with TemporaryDirectory() as home, \
                _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            root = str(Path(home) / "devel" / "odoo-erp")
            Path(root).mkdir(parents=True)
            d = statusbar.cache_dir(home)
            d.mkdir(parents=True, exist_ok=True)
            (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(
                {"open": 1, "name": "odoo-erp", "root": root,
                 "ts": int(time.time())}))

            idle = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"

            class Tmux:
                def __init__(self):
                    self.recovered = False
                    self.calls = []

                def __call__(self, argv, timeout=8):
                    self.calls.append(list(argv))
                    j = " ".join(argv)
                    if "list-panes" in j:
                        if self.recovered:
                            return "%%1\tclaude\t%s\t9001" % root
                        return ""
                    if argv[:1] == ["ps"]:
                        return "1371\ttmux: server\n"
                    if argv[:2] == ["kill", "-USR1"]:
                        self.recovered = True
                        return ""
                    if "capture-pane" in j:
                        return idle
                    if "display" in j:
                        return "0"
                    return ""

            tmux = Tmux()
            pings = []

            def send(body, **kw):
                pings.append(body)
                return "sent"

            logs = wd.bounce_backstop(
                time.time(), tmux, {}, send, home=home,
                gh_fetch=lambda root: [1705],
                cross_stream_repos={"odoo-erp"})

            self.assertTrue(
                any("tmux-socket-recovered" in ln for ln in logs), logs)
            # the pane was found (post-recovery) — nudged directly, never
            # the false "no session" Discord ping
            self.assertFalse(pings, pings)


if __name__ == "__main__":
    unittest.main()
