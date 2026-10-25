"""#1137 (owner 25.10.2026): no Tier-0-style limit on any workstation, dev2
included. With dev2 gone from the session-scope hosts no host remains, so the
whole #1224/#1245 per-session memory-scope machinery is removed: the
`systemd-run --user --scope` launch wrapper, the tool `oom_score_adj`
`BASH_ENV` file, and the install-time adoption of running sessions.
"""
import inspect
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_bashrc_appliers  # noqa: E402
import cli_claude_scripts  # noqa: E402

_GONE = ("systemd-run", "_airuleset_exec_claude", "BASH_ENV",
         "airuleset-tool-oom", "oom_score_adj", "MemoryMax")


class TestScopeModuleGone(unittest.TestCase):
    def test_module_file_is_gone(self):
        self.assertFalse((REPO / "cli_session_scope.py").exists())

    def test_launchers_exec_claude_directly(self):
        for render in (cli_claude_scripts.render_claude_launch_script,
                       cli_claude_scripts.render_claude_impl_launch_script):
            text = render()
            for token in _GONE:
                self.assertNotIn(token, text, (render.__name__, token))
            self.assertIn("exec claude", text, render.__name__)

    def test_install_neither_writes_nor_adopts(self):
        self.assertNotIn("cli_session_scope", inspect.getsource(airuleset.cmd_install))
        self.assertNotIn("cli_session_scope", inspect.getsource(cli_bashrc_appliers))
        self.assertNotIn("cli_session_scope", inspect.getsource(cli_claude_scripts))



class TestInstallDropsScopeFiles(unittest.TestCase):
    """Review A1: an upgraded box must not keep the retired managed files. The
    launcher install already removes retired files it once deployed next to
    its scripts (the #649 window-menu helper); the two scope files join it."""

    def test_launcher_install_removes_the_retired_files(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        claude = d / ".claude"
        claude.mkdir()
        stale = [claude / "airuleset-tool-oom.sh",
                 claude / "airuleset-session-scope.log",
                 claude / "airuleset-tmux-window-menu.sh"]
        for path in stale:
            path.write_text("stale\n")
        airuleset.apply_ultracode_launcher(
            d / ".bashrc", claude / "l.sh", claude / "h.py", claude / "pp.sh",
            claude / "impl.sh", claude / "impl.md")
        self.assertEqual([p.name for p in stale if p.exists()], [])


if __name__ == "__main__":
    unittest.main()
