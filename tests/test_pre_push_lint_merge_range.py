"""Behaviour test for hooks/pre-push-lint.sh merge-range scoping (#951).

After merging the integration branch (upstream) into a work branch, the lint
hook must scope to files changed by the BRANCH, not files that came from
upstream. The bug: `git diff --name-only UPSTREAM..HEAD` (two-dot) diffs the
two tips and includes upstream-only files after a merge; the fix: use
`BASE_REF...HEAD` (three-dot = diff from merge-base) with PR-target base
resolution, consistent with the sibling push-gate hooks.

Fixture layout (reproduces the odoo-erp #6602 incident):
  - A bare "origin" repo with main and develop branches
  - A feature branch, pushed to origin (tracking origin/feature)
  - Upstream (develop) advances with an unrelated ruff-violating Python file
  - The feature branch merges origin/develop, then adds a clean file
  - On the RE-PUSH, @{u}=origin/feature (old tip) → two-dot includes the
    merged develop files → false lint block
  - The hook must NOT block (upstream files are not the branch's responsibility)
  - A variant where the branch itself introduces a ruff violation MUST block
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
    """Run pre-push-lint.sh with a git-push payload on stdin."""
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


class _MergeRangeBase(TestCase):
    """Set up a bare origin + local clone reproducing the incident scenario.

    1. origin has main + develop branches
    2. feature branch created from develop, pushed to origin (tracking set)
    3. develop ADVANCES with a ruff-violating file
    4. feature branch merges origin/develop (bringing in the dirty file)
    5. On the next push, @{u}=origin/feature (old tip) → the two-dot range
       UPSTREAM..HEAD includes develop's dirty file (the bug)
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-merge-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        # 1. Seed repo (non-bare) with initial commit, then bare-clone as origin
        seed = self.tmpdir / "seed"
        _git(self.tmpdir, "init", "-q", "-b", "main", str(seed))
        (seed / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (seed / "clean.py").write_text("X = 1\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "init")

        self.origin = self.tmpdir / "origin.git"
        _git(self.tmpdir, "clone", "--bare", "-q", str(seed), str(self.origin))

        # 2. Clone origin as local working copy
        self.local = self.tmpdir / "local"
        _git(self.tmpdir, "clone", "-q", str(self.origin), str(self.local))

        # 3. Create develop from main, push it
        _git(self.local, "checkout", "-q", "-b", "develop")
        _git(self.local, "push", "-q", "origin", "develop")

        # 4. Create feature branch from develop and push it (sets tracking)
        _git(self.local, "checkout", "-q", "-b", "feature")
        # Add a small initial feature commit so the branch is distinct
        (self.local / "initial_feature.py").write_text("INIT = 1\n")
        _git(self.local, "add", "initial_feature.py")
        _git(self.local, "commit", "-q", "-m", "initial feature commit")
        _git(self.local, "push", "-q", "-u", "origin", "feature")

        # 5. Switch to develop and advance it with a ruff-violating file
        _git(self.local, "checkout", "-q", "develop")
        (self.local / "upstream_dirty.py").write_text(
            "import os\nimport sys\n"  # unused imports = ruff violation
        )
        _git(self.local, "add", "upstream_dirty.py")
        _git(self.local, "commit", "-q", "-m", "add dirty file on develop")
        _git(self.local, "push", "-q", "origin", "develop")

        # 6. Switch back to feature and merge origin/develop
        _git(self.local, "checkout", "-q", "feature")
        _git(self.local, "fetch", "-q", "origin")
        _git(self.local, "merge", "-q", "--no-edit", "origin/develop")
        # Now @{u} = origin/feature (the OLD push point, before the merge)
        # The two-dot range origin/feature..HEAD includes upstream_dirty.py


class TestMergeRangeCleanBranchNotBlocked(_MergeRangeBase):
    """A branch that merges upstream and adds ONLY clean files must NOT be
    blocked by upstream's ruff violations."""

    def setUp(self):
        super().setUp()
        # The branch adds a clean file (no ruff violations)
        (self.local / "my_feature.py").write_text("FEATURE = True\n")
        _git(self.local, "add", "my_feature.py")
        _git(self.local, "commit", "-q", "-m", "add clean feature file")

    def test_upstream_dirty_file_does_not_block_clean_branch(self):
        r = _run_hook(self.local)
        self.assertEqual(
            r.returncode, 0,
            f"Hook wrongly blocked a clean branch after merging upstream.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}",
        )


class TestMergeRangeLocalViolationStillBlocks(_MergeRangeBase):
    """A branch that introduces its OWN ruff violation must still be blocked,
    even after the range fix."""

    def setUp(self):
        super().setUp()
        # The branch adds a file with a genuine ruff violation
        (self.local / "my_bad_feature.py").write_text(
            "import json\nimport collections\n"  # unused imports
        )
        _git(self.local, "add", "my_bad_feature.py")
        _git(self.local, "commit", "-q", "-m", "add bad feature file")

    def test_local_lint_violation_still_blocks(self):
        r = _run_hook(self.local)
        self.assertEqual(
            r.returncode, 2,
            f"Hook failed to block a branch with its own ruff violation.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}",
        )
        combined = r.stdout + r.stderr
        self.assertIn("BLOCKED", combined)


if __name__ == "__main__":
    main()
