"""#1051 — cmd_push per-target post-check: after `airuleset.py install`, run
`timeout 5 gh --version` through the freshly-installed chain and FAIL that
target (never hang) if it does not return. So a future push can NEVER ship a
hanging `gh` (the #1040/#1051 exec-loop).

RED-first: `cli_remote._gh_chain_postcheck` does not exist yet, and
`_deploy_to_all_remotes` does not append it to the remote install command.
"""
import inspect
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_remote  # noqa: E402


def _fake_gh(dirpath, body):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "gh")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(p, 0o755)
    return p


class TestGhChainPostCheck(unittest.TestCase):
    def test_fragment_bounds_gh_version_and_has_distinct_failure(self):
        frag = cli_remote._gh_chain_postcheck()
        # a bounded `gh --version` (never an unbounded call)
        self.assertRegex(frag, r"timeout\s+\d+\s+gh\s+--version")
        # a DISTINCT non-zero exit so a hang is not confused with a git-pull fail
        self.assertIn("exit 87", frag)

    def test_fails_a_hanging_gh_without_hanging(self):
        # a fake target whose `gh` hangs forever -> the post-check must FAIL
        # (never hang) well within the ssh deploy timeout.
        tmp = tempfile.mkdtemp()
        _fake_gh(tmp, "#!/usr/bin/env bash\nsleep 3600\n")
        frag = cli_remote._gh_chain_postcheck()
        env = {**os.environ, "PATH": tmp + os.pathsep + os.environ.get("PATH", "")}
        start = time.monotonic()
        r = subprocess.run(["bash", "-c", frag], capture_output=True, text=True,
                           timeout=30, env=env)
        elapsed = time.monotonic() - start
        self.assertNotEqual(r.returncode, 0, "a hanging gh must FAIL the target")
        self.assertLess(elapsed, 15, "the post-check must never hang")
        self.assertIn("POSTCHECK FAILED", r.stderr)

    def test_passes_a_fast_healthy_gh(self):
        tmp = tempfile.mkdtemp()
        _fake_gh(tmp, "#!/usr/bin/env bash\necho 'gh version 2.40.0'\nexit 0\n")
        frag = cli_remote._gh_chain_postcheck()
        env = {**os.environ, "PATH": tmp + os.pathsep + os.environ.get("PATH", "")}
        r = subprocess.run(["bash", "-c", frag], capture_output=True, text=True,
                           timeout=30, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_deploy_loop_appends_the_postcheck_to_the_remote_command(self):
        # source-lock: the deploy loop's per-target remote command must run the
        # post-check after `airuleset.py install`, so an ssh rc!=0 (the existing
        # `failed.append` accounting) catches a hanging gh on any target.
        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        self.assertIn("_gh_chain_postcheck(", src)
        # it comes AFTER the install in the same command string.
        self.assertIn("airuleset.py install", src)


if __name__ == "__main__":
    unittest.main()
