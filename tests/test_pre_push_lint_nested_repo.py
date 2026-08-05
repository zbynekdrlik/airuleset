"""Behaviour test for hooks/pre-push-lint.sh (#218).

`git diff --name-only` always returns paths ROOT-relative, regardless of the
invoking process's own cwd. The hook piped those straight into `ruff check`
with no adjustment -- and `ruff check` resolves a relative path against ITS
OWN process cwd, which for a nested-repo dispatch (git root at the repo top,
the actual Python project one level down, e.g. `email-extractor/`) is the
SUBDIRECTORY the worker declared as its project, not the git root. A changed
file reported as `email-extractor/app/__init__.py` (root-relative) then gets
looked up as `<subdir>/email-extractor/app/__init__.py`, which does not
exist -- ruff's own "file not found" error is treated as a lint failure and
falsely blocks an otherwise-clean push.
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


def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


class _NestedRepoBase(TestCase):
    """Git root at `self.repo`; the actual Python project (with its own
    `pyproject.toml`) one level down at `self.sub` -- the reported layout."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-pplint-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        self.sub = self.repo / "email-extractor"
        (self.sub / "app").mkdir(parents=True)
        (self.sub / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (self.sub / "app" / "__init__.py").write_text("VALUE = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

    def run_hook(self, cwd):
        payload = {"tool_input": {"command": "git push origin main"}}
        env = dict(os.environ)
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              cwd=str(cwd), capture_output=True, text=True, env=env)


class TestNestedRepoCleanPushIsNotFalselyBlocked(_NestedRepoBase):

    def setUp(self):
        super().setUp()
        # a clean, lint-passing change to the file the next push introduces
        (self.sub / "app" / "__init__.py").write_text("VALUE = 2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "change")

    def test_a_clean_python_change_in_the_nested_subdir_is_not_blocked(self):
        r = self.run_hook(self.sub)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("No such file or directory", r.stdout + r.stderr)

    def test_a_clean_push_from_the_git_root_itself_is_unaffected(self):
        # the non-nested case (hook cwd == git root) must behave exactly the
        # same as before the fix -- no false block, no false pass.
        r = self.run_hook(self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestNestedRepoGenuineLintErrorStillBlocks(_NestedRepoBase):

    def setUp(self):
        super().setUp()
        # a genuine lint error (unused import) in the changed file
        (self.sub / "app" / "__init__.py").write_text("import os\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "bad")

    def test_a_real_lint_error_in_the_nested_subdir_still_blocks(self):
        r = self.run_hook(self.sub)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)


if __name__ == "__main__":
    main()
