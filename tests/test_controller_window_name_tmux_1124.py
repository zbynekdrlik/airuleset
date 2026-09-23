"""#1124 — REAL tmux proof, on a private `-L` socket, that the controller's
window-name hook and the #660 owner audit coexist in `session-created`.

The unit tests (test_controller_window_name_1124.py) check the rendered text
and argv. This file checks what tmux actually DOES with them, because the bug
was tmux semantics: an UNINDEXED `set-hook -g session-created` clears the whole
hook array, so whichever writer ran last silently deleted the other. After the
fix, a NEW session created from the rendered controller conf, or after the
live-apply in install order, must be named `ar` and must keep both hooks.

Every tmux call goes through `_tmux`, which carries `-L <random socket>`, so the
test never touches the owner's live server (tests/test_tmux_test_isolation_lock.py).
The tmux server runs with HOME pointed at a tmp dir, so the audit logger that
fires on session creation writes there and never into the real ~/.claude.

Box-bound: the hermetic CI container has no tmux, so this file is listed in
.github/box-bound-tests.txt. A missing tmux binary FAILS here and never skips.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import cli_tmux_provisioning as tmuxprov


class TestControllerWindowNameRealTmux(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which("tmux"),
                             "tmux binary required (box-bound test)")
        self.sock = "ar1124-%s" % uuid.uuid4().hex[:12]
        self.home = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, HOME=str(self.home))
        self.env.pop("TMUX", None)

    def tearDown(self):
        self._tmux("kill-server")

    def _tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, *args],
                              capture_output=True, text=True, timeout=10,
                              env=self.env)

    def _runner(self, argv):
        # route the PRODUCTION live-apply argv to the private socket
        self.assertEqual(argv[0], "tmux")
        return self._tmux(*argv[1:])

    def _patches(self):
        return (mock.patch("cli_bashrc_appliers.default_box_class",
                           return_value="controller"),
                mock.patch("cli_tmux_provisioning._hook_marker",
                           return_value=None))

    def _window_names(self):
        res = self._tmux("list-windows", "-a", "-F",
                         "#{session_name} #{window_name} #{automatic-rename}")
        self.assertEqual(res.returncode, 0, res.stderr)
        return sorted(res.stdout.split("\n")[:-1])

    def _hooks(self):
        res = self._tmux("show-hooks", "-g", "session-created")
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def test_rendered_controller_conf_names_new_sessions_ar(self):
        conf = self.home / ".tmux.conf"
        box, marker = self._patches()
        with box, marker:
            tmuxprov.apply_stream_tmux_window_name(
                conf, user="airuleset", host="airuleset",
                run=lambda argv: None)
            tmuxprov.apply_owner_session_created_audit(
                conf, user="airuleset", run=lambda argv: None, home=self.home)
        res = self._tmux("-f", str(conf), "new-session", "-d", "-s", "t",
                         "sleep 60")
        self.assertEqual(res.returncode, 0, res.stderr)
        res = self._tmux("new-session", "-d", "-s", "u", "sleep 60")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._window_names(), ["t ar 0", "u ar 0"])
        hooks = self._hooks()
        self.assertIn("rename-window ar", hooks)
        self.assertIn("log-session-created.sh", hooks)

    def test_live_apply_in_install_order_keeps_both_hooks(self):
        res = self._tmux("-f", "/dev/null", "new-session", "-d", "-s", "t",
                         "sleep 60")
        self.assertEqual(res.returncode, 0, res.stderr)
        conf = self.home / ".tmux.conf"
        box, marker = self._patches()
        with box, marker:
            tmuxprov.apply_stream_tmux_window_name(
                conf, user="airuleset", host="airuleset", run=self._runner)
            tmuxprov.apply_owner_session_created_audit(
                conf, user="airuleset", run=self._runner, home=self.home)
        # the already-running window is renamed by the live-apply
        self.assertEqual(self._window_names(), ["t ar 0"])
        # a session created AFTER install is named by the surviving hook
        res = self._tmux("new-session", "-d", "-s", "u", "sleep 60")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._window_names(), ["t ar 0", "u ar 0"])
        hooks = self._hooks()
        self.assertIn("rename-window ar", hooks)
        self.assertIn("log-session-created.sh", hooks)


if __name__ == "__main__":
    unittest.main()
