"""#1137 (owner 25.10.2026): no Tier-0-style limit on any workstation, dev2
included. With dev2 gone from the session-scope hosts no host remains, so the
whole #1224/#1245 per-session memory-scope machinery is removed: the
`systemd-run --user --scope` launch wrapper, the tool `oom_score_adj`
`BASH_ENV` file, and the install-time adoption of running sessions.
"""
import inspect
import sys
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


if __name__ == "__main__":
    unittest.main()
