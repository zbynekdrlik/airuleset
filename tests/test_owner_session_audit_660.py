"""#660 — native `session-created` audit hook on the OWNER box.

The pane process tree of a stray `<owner>-N` / foreign owner-session ends at
the tmux SERVER, so the creating CLIENT is unrecoverable post-hoc. This installs
a native `set-hook -g session-created` (the #649 native-beats-custom doctrine)
that logs the new session name + creating-client pid/tty/name + a ps chain of
the creator to `~/.claude/tmux-audit/session-created.log`, so the NEXT stray's
creator is captured deterministically.

Owner boxes only: single-session boxes (gk + subdev streams) already own
`session-created` for #593 window-rename, so this must never render there.

Dependency-injected fake `run` + tmp conf/home; no real tmux, invisible to the
`tests/test_tmux_test_isolation_lock.py` AST scan (no real subprocess tmux
argv literal here).
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli_tmux_provisioning as tmuxprov


class _Rec:
    """Records argv, returns success for everything (no real tmux)."""
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def set_hook_calls(self):
        return [c for c in self.calls
                if "set-hook" in c and "session-created" in c]


class TestRenderPieces(unittest.TestCase):
    def test_block_sets_session_created_hook_via_runshell(self):
        block = tmuxprov.render_owner_session_audit_block(
            "/home/x/.claude/tmux-audit/log-session-created.sh")
        self.assertIn("set-hook -g session-created", block)
        self.assertIn("run-shell -b", block)
        self.assertIn("/home/x/.claude/tmux-audit/log-session-created.sh", block)

    def test_logger_script_shape(self):
        script = tmuxprov.render_owner_audit_logger()
        self.assertIn("set -euo pipefail", script)
        self.assertIn("session-created.log", script)
        self.assertIn("client_pid", script)


class TestApplier(unittest.TestCase):
    def _apply(self, *, owner_box, conf_seed=None):
        rec = _Rec()
        self._td = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        conf = Path(self._td.name) / ".tmux.conf"
        if conf_seed is not None:
            conf.write_text(conf_seed)
        with mock.patch("cli_bashrc_appliers.is_single_session_box_user",
                        return_value=not owner_box):
            tmuxprov.apply_owner_session_created_audit(
                tmux_conf_path=conf, user="tester", run=rec,
                home=self._home.name)
        return rec, conf

    def tearDown(self):
        for a in ("_td", "_home"):
            d = getattr(self, a, None)
            if d is not None:
                d.cleanup()

    def _logger_path(self):
        return Path(self._home.name) / ".claude" / "tmux-audit" / \
            "log-session-created.sh"

    def test_owner_box_writes_block_logger_and_live_applies(self):
        rec, conf = self._apply(owner_box=True)
        text = conf.read_text()
        self.assertIn(tmuxprov.OWNER_AUDIT_MARK_START, text)
        self.assertIn("set-hook -g session-created", text)
        logger = self._logger_path()
        self.assertTrue(logger.is_file())
        self.assertTrue(os.access(logger, os.X_OK), "logger must be executable")
        self.assertEqual(len(rec.set_hook_calls()), 1)
        # the live-applied hook names session-created and the logger path
        argv = rec.set_hook_calls()[0]
        self.assertIn("session-created", argv)
        self.assertTrue(any(str(logger) in a for a in argv))

    def test_non_owner_box_no_block_no_live_apply(self):
        rec, conf = self._apply(owner_box=False)
        text = conf.read_text() if conf.exists() else ""
        self.assertNotIn(tmuxprov.OWNER_AUDIT_MARK_START, text)
        self.assertEqual(rec.set_hook_calls(), [])
        self.assertFalse(self._logger_path().exists())

    def test_idempotent_block_appears_once(self):
        rec, conf = self._apply(owner_box=True)
        first = conf.read_text()
        # re-apply against the SAME conf, owner box
        with mock.patch("cli_bashrc_appliers.is_single_session_box_user",
                        return_value=False):
            tmuxprov.apply_owner_session_created_audit(
                tmux_conf_path=conf, user="tester", run=_Rec(),
                home=self._home.name)
        second = conf.read_text()
        self.assertEqual(second.count(tmuxprov.OWNER_AUDIT_MARK_START), 1)
        self.assertEqual(second, first)

    def test_strips_block_on_non_owner_box(self):
        seed = ("# unrelated\n" + tmuxprov.render_owner_session_audit_block(
            "/home/x/.claude/tmux-audit/log-session-created.sh") + "\n# tail\n")
        rec, conf = self._apply(owner_box=False, conf_seed=seed)
        text = conf.read_text()
        self.assertNotIn(tmuxprov.OWNER_AUDIT_MARK_START, text)
        self.assertIn("# unrelated", text)
        self.assertIn("# tail", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
