"""The #1224 tool-OOM BASH_ENV file must never source itself.

Found while gating issue 1256 (2026-10-19): a Claude session started by the
launcher exports `BASH_ENV=~/.claude/airuleset-tool-oom.sh`. When a launcher
runs under a DIFFERENT `$HOME` (a test HOME, or a sudo/su-hosted account), it
saw an inherited BASH_ENV that differs from its own oom file and chained it as
`AIRULESET_PREV_BASH_ENV`. The inner oom file then sources the outer one, whose
PREV is itself: every non-interactive bash recursed until it crashed
(`Segmentation fault`), so every Bash tool call of such a session died and 12
launcher tests failed in the push gate.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

import cli_session_scope as scope  # noqa: E402
from test_session_scope_1224 import _Launch, _exe  # noqa: E402


class OomEnvNeverSourcesItself(unittest.TestCase):
    def test_prev_pointing_at_the_file_itself_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, scope.TOOL_OOM_ENV_NAME)
            f.write_text(scope.render_tool_oom_env())
            r = subprocess.run(["/bin/bash", "-c", "echo ok"], capture_output=True,
                               text=True, timeout=30,
                               env={"PATH": "/usr/bin:/bin", "BASH_ENV": str(f),
                                    "AIRULESET_PREV_BASH_ENV": str(f)})
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr)

    def test_a_launch_under_another_home_does_not_recurse(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d, "outer", ".claude")
            outer.mkdir(parents=True)
            scope.write_tool_oom_env(outer)
            launch = _Launch(Path(d, "inner"), host="controller")
            _exe(launch.home / ".local" / "bin" / "claude",
                 "#!/bin/bash\necho ok\n")
            env = {"PATH": "/usr/bin:/bin", "HOME": str(launch.home),
                   "BASH_ENV": str(outer / scope.TOOL_OOM_ENV_NAME),
                   "AIRULESET_MEMINFO": str(launch.home / "meminfo")}
            r = subprocess.run(["bash", str(launch.script), "plain"], env=env,
                               capture_output=True, text=True, timeout=30,
                               cwd=str(launch.home))
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr)

    def test_a_foreign_bash_env_is_still_chained(self):
        with tempfile.TemporaryDirectory() as d:
            prev = Path(d, "user-env.sh")
            prev.write_text("echo prev-ran\n")
            launch = _Launch(Path(d, "inner"), host="controller")
            _exe(launch.home / ".local" / "bin" / "claude",
                 "#!/bin/bash\necho ok\n")
            env = {"PATH": "/usr/bin:/bin", "HOME": str(launch.home),
                   "BASH_ENV": str(prev),
                   "AIRULESET_MEMINFO": str(launch.home / "meminfo")}
            r = subprocess.run(["bash", str(launch.script), "plain"], env=env,
                               capture_output=True, text=True, timeout=30,
                               cwd=str(launch.home))
            self.assertIn("prev-ran", r.stdout)
            self.assertIn("ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
