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

A fresh-context adversarial review of the first cut (see the #318 GitHub
issue) found real, executed bugs the original tests could not catch: the
first version picked the FIRST `tmux: server` line from `ps -e` with no
uid filter, which live-measured is wrong on a shared box (a foreign uid's
own server) and even on a single-uid box (this repo's own scripts/tests
run a second `-L` tmux server alongside the default one, and dev2 runs a
real `-L t2` server right now) — signaling the wrong same-uid server never
recreates the DEFAULT socket. `_FakeTmux` here therefore models `ps -eo
pid,uid,comm` (three columns, matching real `ps` exactly) and supports
MULTIPLE candidate rows so a test can prove the fix tries every same-uid
candidate in turn, not just the first.
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
    lands on the pid that actually owns the DEFAULT socket, then returns
    real pane data — the orphaned-socket-then-recovered shape.

    `server_rows` is a list of `(pid, uid, comm)` triples mirroring real
    `ps -eo pid,uid,comm` output (three columns) — lets a test model
    several candidate tmux servers (same-uid siblings, foreign-uid decoys)
    and pick exactly which ONE pid actually owns the default socket
    (`recovers_on`). Defaults to a single same-uid `tmux: server` row so
    the common-case tests stay short."""

    def __init__(self, server_rows=None, recovers_on="__first__",
                 panes_after_recovery=REAL_PANES):
        if server_rows is None:
            server_rows = [(1371, os.getuid(), "tmux: server")]
        self.server_rows = server_rows
        if recovers_on == "__first__":
            recovers_on = server_rows[0][0] if server_rows else None
        self.recovers_on = recovers_on
        self.panes_after_recovery = panes_after_recovery
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
            lines = ["    PID   UID COMMAND"]
            for pid, uid, comm in self.server_rows:
                lines.append("%d\t%d\t%s" % (pid, uid, comm))
            return "\n".join(lines) + "\n"
        if argv[:2] == ["kill", "-USR1"]:
            signaled = int(argv[2])
            if self.recovers_on is not None and signaled == self.recovers_on:
                self.recovered = True
            return ""
        return ""

    def kill_calls(self):
        return [a for a in self.calls if a[:2] == ["kill", "-USR1"]]

    def killed_pids(self):
        return [int(a[2]) for a in self.kill_calls()]

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
        self.assertEqual(tmux.killed_pids(), [1371])

    def test_no_tmux_server_at_all_is_unchanged_no_recovery_attempted(self):
        tmux = _FakeTmux(server_rows=[])
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [])
        self.assertEqual(tmux.kill_calls(), [])

    def test_socket_present_pid_found_no_recovery_attempted(self):
        # server process is alive AND the socket file genuinely exists —
        # some OTHER reason list-panes failed (permissions, a transient
        # blip) — must never fire SIGUSR1 at a healthy server, and (the
        # #318 review's MINOR-1) must never even pay for a `ps -e` scan.
        tmux = _FakeTmux()
        with _ScratchSocketDir(present=True) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [])
        self.assertEqual(tmux.kill_calls(), [])
        self.assertEqual(tmux.ps_calls(), [])

    def test_healthy_output_never_probes_ps_at_all(self):
        tmux = _FakeTmux()
        tmux.recovered = True  # list-panes already answers for real
        res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [("%7", "/home/david/devel/odoo/odoo-erp")])
        self.assertEqual(tmux.ps_calls(), [])
        self.assertEqual(tmux.kill_calls(), [])

    def test_foreign_uid_server_is_never_signaled(self):
        # a decoy row for a DIFFERENT uid (the subdev shared-box shape —
        # montalu/marek/david all show up in a bare `ps -e`) must be
        # filtered out entirely, never signaled.
        foreign = os.getuid() + 9999
        tmux = _FakeTmux(
            server_rows=[(999, foreign, "tmux: server"),
                         (1371, os.getuid(), "tmux: server")],
            recovers_on=1371)
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux)
        self.assertEqual(res, [("%7", "/home/david/devel/odoo/odoo-erp")])
        self.assertNotIn(999, tmux.killed_pids())
        self.assertIn(1371, tmux.killed_pids())

    def test_tries_every_same_uid_candidate_until_one_recovers(self):
        # two SAME-uid servers (e.g. the default socket + this repo's own
        # `-L` test sockets) — the FIRST one in `ps` order does not own the
        # default socket at all, so signaling it alone must not be trusted;
        # the fix must retry the real query and try the NEXT candidate.
        tmux = _FakeTmux(
            server_rows=[(2001, os.getuid(), "tmux: server"),
                         (1371, os.getuid(), "tmux: server")],
            recovers_on=1371)
        logs = []
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux, logs=logs)
        self.assertEqual(res, [("%7", "/home/david/devel/odoo/odoo-erp")])
        self.assertEqual(tmux.killed_pids(), [2001, 1371])
        self.assertTrue(any("server-pid=2001" in ln for ln in logs), logs)
        self.assertTrue(any("server-pid=1371" in ln for ln in logs), logs)
        self.assertTrue(any("tmux-socket-recovered" in ln for ln in logs), logs)

    def test_logs_capture_recovery_attempt_and_success(self):
        tmux = _FakeTmux()
        logs = []
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            wd.list_claude_panes(tmux, logs=logs)
        self.assertTrue(any("tmux-socket-orphaned" in ln for ln in logs), logs)
        self.assertTrue(any("tmux-socket-recovered" in ln for ln in logs), logs)

    def test_logs_capture_recovery_failure(self):
        tmux = _FakeTmux(recovers_on=None)
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


class TestDryRun(unittest.TestCase):
    """#318 adversarial-review MAJOR-2: `list_claude_panes` had no `dry_run`
    of its own, so `bounce_backstop(..., dry_run=True)` still sent a REAL
    SIGUSR1 — `watchdog --once --dry-run` must stay genuinely side-effect
    free."""

    def test_dry_run_never_signals_and_logs_would_recover(self):
        tmux = _FakeTmux()
        logs = []
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux, logs=logs, dry_run=True)
        self.assertEqual(res, [])
        self.assertEqual(tmux.kill_calls(), [])
        self.assertTrue(any("would recover" in ln for ln in logs), logs)

    def test_dry_run_with_no_logs_param_is_still_a_no_op(self):
        tmux = _FakeTmux()
        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            res = wd.list_claude_panes(tmux, dry_run=True)
        self.assertEqual(res, [])
        self.assertEqual(tmux.kill_calls(), [])


class TestTmuxServerPidsHelper(unittest.TestCase):
    def test_finds_real_server_pid(self):
        run = _FakeTmux(server_rows=[(4242, os.getuid(), "tmux: server")])
        self.assertEqual(wd._tmux_server_pids(run), [4242])

    def test_returns_empty_when_absent(self):
        run = _FakeTmux(server_rows=[])
        self.assertEqual(wd._tmux_server_pids(run), [])

    def test_filters_out_foreign_uid_and_keeps_order(self):
        foreign = os.getuid() + 9999
        run = _FakeTmux(server_rows=[
            (999, foreign, "tmux: server"),
            (2001, os.getuid(), "tmux: server"),
            (1371, os.getuid(), "tmux: server"),
        ])
        self.assertEqual(wd._tmux_server_pids(run), [2001, 1371])

    def test_malformed_ps_line_is_ignored(self):
        def run(argv, timeout=8):
            if argv[:1] == ["ps"]:
                return "not-a-pid\tnot-a-uid\ttmux: server\n"
            return ""
        self.assertEqual(wd._tmux_server_pids(run), [])


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
            my_uid = os.getuid()

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
                        return "1371\t%d\ttmux: server\n" % my_uid
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

    def test_dry_run_reaches_list_claude_panes_and_never_signals(self):
        import time

        with _ScratchSocketDir(present=False) as tdir, \
                m.patch.dict(os.environ, {"TMUX_TMPDIR": tdir}):
            tmux = _FakeTmux()
            wd.bounce_backstop(
                time.time(), tmux, {}, lambda body, **kw: "sent",
                home=str(Path(tdir) / "home"), dry_run=True,
                gh_fetch=lambda root: [1705],
                cross_stream_repos={"odoo-erp"})
        self.assertEqual(tmux.kill_calls(), [])


if __name__ == "__main__":
    unittest.main()
