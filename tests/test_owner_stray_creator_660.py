"""#660 — end-to-end reproduction of the fleet stray CREATOR path + kill sweep,
against a REAL but fully ISOLATED tmux server.

The creator (proven live across dev1+dev2 via the audit hook): an interactive
ssh session runs `tmux new-session -A -s <name>` (the #651 `t`/`tmux()` wrapper
mechanism); the attach hangs, leaving a STANDALONE idle bare-bash session named
either `<owner>-N` (`zbynek-0`) or a fleet STREAM username (`marek`/`marek-12`).
This test reproduces that resulting stray shape with the SAME command family
(`new-session -A ...`) on a throwaway server, then proves the widened kill sweep
absorbs the stray while leaving the owner session and a grouped work session
untouched.

Isolation (the #613 discipline + `tests/test_tmux_test_isolation_lock.py`):
EVERY tmux call carries an explicit `-S <sock>` on a fresh per-test tempdir
socket, TMUX/TMUX_PANE stripped, a pre-flight emptiness check, and the server
killed + removed unconditionally in tearDown.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import cli_tmux_provisioning as tmuxprov

_LIVE_SIGNATURES = ("attached)", "windows (created")


def _pane_read_override(iso, overrides):
    """#711/#427: wrap `iso.t` so the kill sweep's ONE load-sensitive read --
    `tmux list-panes ... -t =<stray> -F ...#{pane_current_command}...
    #{pane_current_path}` -- returns a DETERMINISTIC line for each stray named
    in `overrides` (a `#{session_attached}\\t#{session_group}\\t
    #{pane_current_command}\\t#{pane_pid}\\t#{pane_current_path}` string),
    instead of reading the freshly-spawned pane's transient state. EVERY other
    tmux call (list-sessions, kill-session, and any non-overridden pane read
    like the grouped `montalu2`) passes straight through to the real isolated
    server, so the session lifecycle stays genuinely end-to-end. This is the
    #427 remedy: route the uncontrolled real-box read through an override-able
    path, keep the rest real."""
    def run(argv):
        if len(argv) >= 5 and argv[1] == "list-panes" and "-t" in argv:
            name = argv[argv.index("-t") + 1].lstrip("=")
            if name in overrides:
                return subprocess.CompletedProcess(
                    argv, 0, stdout=overrides[name] + "\n", stderr="")
        return iso.t(*argv[1:])
    return run


def _settled_pane_line(home, pid="1"):
    """#711/#427: the DETERMINISTIC SETTLED `list-panes` read for a standalone
    idle bare-shell stray sitting in $HOME -- unattached (`0`), ungrouped (``),
    bare `bash`, cwd == home. This is the stray's TRUE post-settle state; the
    freshly-spawned pane merely mis-reads it TRANSIENTLY under load
    (cli_tmux_provisioning.py:1310). `pid` is a don't-care because the tests
    neutralize the shell child-guard with an idle `ps_run`."""
    return "0\t\tbash\t%s\t%s" % (pid, home)


class _Iso:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="stray660-")
        self.sock = os.path.join(self.dir, "sock")
        self.env = dict(os.environ)
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        r = self.t("list-sessions")
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 or any(s in combined for s in _LIVE_SIGNATURES):
            shutil.rmtree(self.dir, ignore_errors=True)
            raise AssertionError("pre-flight: fresh -S socket not empty: %r"
                                 % combined)

    def t(self, *args, timeout=10):
        return subprocess.run(["tmux", "-S", self.sock, *args],
                              capture_output=True, text=True,
                              timeout=timeout, env=self.env)

    def names(self):
        r = self.t("list-sessions", "-F", "#{session_name}")
        return sorted(n for n in (r.stdout or "").split() if n)

    def close(self):
        subprocess.run(["tmux", "-S", self.sock, "kill-server"],
                       capture_output=True, text=True, env=self.env)
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class TestStrayCreatorAndSweep660(unittest.TestCase):
    def setUp(self):
        self.iso = _Iso()
        self.addCleanup(self.iso.close)
        # a runner that wraps every sweep tmux call onto OUR isolated socket.
        self.run = lambda argv: self.iso.t(*argv[1:])

    def test_creator_produces_standalone_stray_absorbed_by_sweep(self):
        # owner base (grouped-or-not, == owner so never a kill candidate)
        self.assertEqual(self.iso.t("-f", "/dev/null", "new-session", "-d",
                                    "-s", "zbynek", "-x", "80", "-y", "24")
                         .returncode, 0)
        # CREATOR PATH: `new-session -A -s marek` (the #651 wrapper shape); `-d`
        # makes the resulting STANDALONE session deterministic in a non-terminal
        # test (live, the interactive attach hangs and leaves the same session).
        # `-c <home>` puts the stray's cwd at the passed HOME so the cwd-guard
        # (a stray sits in $HOME, a work session in a project dir) treats it as
        # a stray.
        self.assertEqual(self.iso.t("new-session", "-A", "-d", "-s", "marek",
                                    "-c", self.iso.dir).returncode, 0)
        # a GROUPED stream work session (marek's real `marek-3` shape) must be
        # PRESERVED: create a base then a grouped sibling matching a stray name.
        self.iso.t("-f", "/dev/null", "new-session", "-d", "-s", "montwork")
        self.iso.t("new-session", "-d", "-t", "montwork", "-s", "montalu2")
        self.assertIn("marek", self.iso.names())

        stray_res = tmuxprov._owner_box_stray_name_res("zbynek",
                                                       single_session=False)
        # a deterministic no-child ps runner keeps the sweep from probing the
        # real freshly-spawned bash panes (whose transient rc-file children
        # would flake the child-guard, review 🔵5); the child-guard itself is
        # locked by a dedicated fake-runner test.
        def idle_ps(argv):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        # #711/#427 HERMETICITY: route marek's ONE load-sensitive pane read
        # (#{pane_current_command}/#{pane_current_path} of the just-spawned pane)
        # through a DETERMINISTIC settled line, so the sweep's absorb decision no
        # longer depends on the transient real-box read that flaked under
        # full-suite load. marek is still really created + really killed; every
        # other read (the grouped `montalu2` skip) hits the real isolated server.
        hermetic_run = _pane_read_override(
            self.iso, {"marek": _settled_pane_line(self.iso.dir)})
        with tempfile.TemporaryDirectory() as td:
            tmuxprov._live_normalize_owner_session(
                "zbynek", run=hermetic_run, stray_name_res=stray_res,
                audit_dir=td, ps_run=idle_ps, home=self.iso.dir)
            log = (Path(td) / "normalize.log").read_text()

        after = self.iso.names()
        self.assertNotIn("marek", after)      # standalone stray absorbed
        self.assertIn("zbynek", after)        # owner never touched
        self.assertIn("montalu2", after)      # grouped work session preserved
        self.assertIn("killed", log)
        self.assertIn("marek", log)

    def test_hermetic_pane_read_absorbs_stray_under_transient_load(self):
        # #711/#427: the ROOT of the full-suite flake -- the sweep reads the
        # freshly-spawned stray pane's #{pane_current_command}/#{pane_current_path}
        # ONCE (cli_tmux_provisioning.py:1310), and under load that read is
        # momentarily NON-bare / unsettled, so the genuinely-idle stray is
        # falsely SKIPPED. Reproduce that transient read DETERMINISTICALLY (the
        # #427 remedy: no timing race) and prove the sweep still absorbs the
        # stray when the read is routed through a settled path.
        self.assertEqual(self.iso.t("-f", "/dev/null", "new-session", "-d",
                                    "-s", "zbynek", "-x", "80", "-y", "24")
                         .returncode, 0)
        # a REAL standalone bare-bash stray in $HOME (the creator-path shape).
        self.assertEqual(self.iso.t("new-session", "-A", "-d", "-s", "marek",
                                    "-c", self.iso.dir).returncode, 0)
        self.assertIn("marek", self.iso.names())

        stray_res = tmuxprov._owner_box_stray_name_res("zbynek",
                                                       single_session=False)

        def idle_ps(argv):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

        # PHASE 1 -- the flake, made deterministic: the #427 load transient
        # (bash still sourcing its rc reads a NON-bare #{pane_current_command})
        # makes the sweep SKIP the genuinely-idle stray. Same real marek; only
        # the one pane read is injected. marek SURVIVES (this is the flake).
        transient_run = _pane_read_override(self.iso, {"marek": "0\t\tcat\t1\t"})
        with tempfile.TemporaryDirectory() as td:
            tmuxprov._live_normalize_owner_session(
                "zbynek", run=transient_run, stray_name_res=stray_res,
                audit_dir=td, ps_run=idle_ps, home=self.iso.dir)
            log1 = (Path(td) / "normalize.log").read_text()
        self.assertIn("marek", self.iso.names())   # transient read -> skipped
        self.assertIn("skip", log1)
        self.assertIn("non-shell", log1)

        # PHASE 2 -- the fix: routing that SAME still-alive stray's read through
        # the DETERMINISTIC settled line (its true post-settle state) absorbs it.
        settled_run = _pane_read_override(
            self.iso, {"marek": _settled_pane_line(self.iso.dir)})
        with tempfile.TemporaryDirectory() as td:
            tmuxprov._live_normalize_owner_session(
                "zbynek", run=settled_run, stray_name_res=stray_res,
                audit_dir=td, ps_run=idle_ps, home=self.iso.dir)
            log2 = (Path(td) / "normalize.log").read_text()
        self.assertNotIn("marek", self.iso.names())   # settled read -> absorbed
        self.assertIn("killed", log2)

    def test_subdev_box_never_sweeps_foreign_names(self):
        # on a single-session (subdev) box the widening is OFF, so a session
        # named after ANOTHER stream is never a candidate -- only `<owner>-N`.
        self.iso.t("-f", "/dev/null", "new-session", "-d", "-s", "marek",
                   "-x", "80", "-y", "24")
        self.iso.t("new-session", "-A", "-d", "-s", "david")
        stray_res = tmuxprov._owner_box_stray_name_res("marek",
                                                       single_session=True)
        tmuxprov._live_normalize_owner_session("marek", run=self.run,
                                               stray_name_res=stray_res)
        self.assertIn("david", self.iso.names())  # foreign stream NOT swept


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
