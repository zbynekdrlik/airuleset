"""#1139: gates.pushtest must not crash on a diff containing non-UTF-8 bytes
(live: byte 0xf2 in a 47 MB origin/main...HEAD range on a detached HEAD made
the pre-push gate exit 1 and fail-close a legitimate push)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import pushtest  # noqa: E402


class TestPushtestNonUtf8Diff1139(unittest.TestCase):
    def test_diff_with_non_utf8_bytes_reads_without_raising(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                       HOME=d)
            run = lambda *a: subprocess.run(["git", *a], cwd=d, env=env,  # noqa: E731
                                            check=True, capture_output=True)
            run("init", "-q")
            Path(d, "a.txt").write_text("base\n")
            run("add", "a.txt")
            run("commit", "-qm", "base")
            Path(d, "legacy.txt").write_bytes(b"caf\xf2 legacy\n")
            run("add", "legacy.txt")
            run("commit", "-qm", "add legacy bytes")
            old = os.getcwd()
            os.chdir(d)
            self.addCleanup(os.chdir, old)
            out = pushtest._stdout(["diff", "-U0", "HEAD~1...HEAD"])
            self.assertIn("legacy", out)


if __name__ == "__main__":
    unittest.main()
