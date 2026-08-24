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
        self.assertEqual(self.iso.t("new-session", "-A", "-d", "-s", "marek")
                         .returncode, 0)
        # a GROUPED stream work session (marek's real `marek-3` shape) must be
        # PRESERVED: create a base then a grouped sibling matching a stray name.
        self.iso.t("-f", "/dev/null", "new-session", "-d", "-s", "montwork")
        self.iso.t("new-session", "-d", "-t", "montwork", "-s", "montalu2")
        self.assertIn("marek", self.iso.names())

        stray_res = tmuxprov._owner_box_stray_name_res("zbynek",
                                                       single_session=False)
        with tempfile.TemporaryDirectory() as td:
            tmuxprov._live_normalize_owner_session(
                "zbynek", run=self.run, stray_name_res=stray_res, audit_dir=td)
            log = (Path(td) / "normalize.log").read_text()

        after = self.iso.names()
        self.assertNotIn("marek", after)      # standalone stray absorbed
        self.assertIn("zbynek", after)        # owner never touched
        self.assertIn("montalu2", after)      # grouped work session preserved
        self.assertIn("killed", log)
        self.assertIn("marek", log)

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
