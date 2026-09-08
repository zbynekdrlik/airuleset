"""Edge-case test: a renamed dirty file IS a branch change and blocks (#951 item 3).

A file renamed on the branch (same content, different name) appears as "added"
(new name) in the three-dot range.  The rename is the branch's own action, so
the file under its new name is the branch's responsibility and must be linted.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "pre-push-lint.sh"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(repo, *args, check=True):
    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        check=check, capture_output=True, text=True, env=env,
    )


def _run_hook(cwd):
    payload = {"tool_input": {"command": "git push origin feature"}}
    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


class TestRenamedFileWithDirtyContentBlocks(TestCase):
    """A file RENAMED by the branch (new name, content from base) that has lint
    errors IS a branch change -- it should be linted. The rename is the branch's
    own action; the content under the new name is the branch's responsibility."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-rename-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        seed = self.tmpdir / "seed"
        _git(self.tmpdir, "init", "-q", "-b", "main", str(seed))
        (seed / "pyproject.toml").write_text('[project]\nname = "x"\n')
        # A file with lint errors that exists under the original name
        (seed / "old_name.py").write_text("import os\nimport sys\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "init")

        self.origin = self.tmpdir / "origin.git"
        _git(self.tmpdir, "clone", "--bare", "-q", str(seed), str(self.origin))

        self.local = self.tmpdir / "local"
        _git(self.tmpdir, "clone", "-q", str(self.origin), str(self.local))

        _git(self.local, "checkout", "-q", "-b", "develop")
        _git(self.local, "push", "-q", "origin", "develop")

        _git(self.local, "checkout", "-q", "-b", "feature")
        # Rename the file (same content, new name) -- this IS a branch change
        _git(self.local, "mv", "old_name.py", "new_name.py")
        _git(self.local, "commit", "-q", "-m", "rename file")
        _git(self.local, "push", "-q", "-u", "origin", "feature")

    def test_renamed_dirty_file_is_linted(self):
        """A renamed file IS a branch change and should be linted."""
        r = _run_hook(self.local)
        # The renamed file has lint errors -- the hook SHOULD block
        self.assertEqual(
            r.returncode, 2,
            f"Hook should block on renamed file with lint errors.\n"
            f"Output: {r.stdout + r.stderr}",
        )


if __name__ == "__main__":
    main()
