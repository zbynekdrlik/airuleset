"""Behaviour test for hooks/pre-push-lint.sh ruff version pin awareness (#951 item 2).

When the local ruff version differs from the CI-pinned version
(`.github/workflows/ci.yml` carries `ruff==<pinned>`), the hook must NOT
hard-block on lint failures that may be version-specific. Instead it prints a
WARN naming both versions and demotes any ruff failure from BLOCK (exit 2) to
WARN (exit 0 with diagnostic message).

Fixture: a repo with a CI workflow pinning ruff==99.0.0 (a version that does not
match any real local ruff), a pyproject.toml, and a file with a genuine lint
error. The hook should detect the version mismatch and exit 0 (WARN) instead of
exit 2 (BLOCK).
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
    payload = {"tool_input": {"command": "git push origin main"}}
    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


class TestVersionMismatchDemotesBlockToWarn(TestCase):
    """When the CI-pinned ruff version does NOT match the local ruff, a lint
    failure should be demoted from BLOCK (exit 2) to WARN (exit 0)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-vpin-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        _git(self.tmpdir, "init", "-q", "-b", "main", str(self.tmpdir / "repo"))
        self.repo = self.tmpdir / "repo"

        # pyproject.toml so the hook detects a Python project
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n')

        # CI workflow with a FAKE pinned ruff version that will never match local
        ci_dir = self.repo / ".github" / "workflows"
        ci_dir.mkdir(parents=True)
        (ci_dir / "ci.yml").write_text(
            "jobs:\n  gate:\n    steps:\n"
            "      - run: pip install ruff==99.0.0\n"
        )

        (self.repo / "clean.py").write_text("X = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

        # Add a file with a genuine lint violation (unused import)
        (self.repo / "bad.py").write_text("import os\nimport sys\n")
        _git(self.repo, "add", "bad.py")
        _git(self.repo, "commit", "-q", "-m", "add bad file")

    def test_version_mismatch_demotes_lint_failure_to_warn(self):
        r = _run_hook(self.repo)
        combined = r.stdout + r.stderr
        # The hook should exit 0 (WARN), NOT exit 2 (BLOCK)
        self.assertEqual(
            r.returncode, 0,
            f"Hook should demote lint failure to WARN on version mismatch, "
            f"but exited {r.returncode}.\nOutput: {combined}",
        )
        # The output should mention the version mismatch
        self.assertRegex(
            combined.lower(),
            r"warn|version|mismatch|differs",
            f"Expected version-mismatch warning in output.\nOutput: {combined}",
        )


class TestVersionMatchStillBlocks(TestCase):
    """When the CI-pinned ruff version MATCHES the local ruff, a lint failure
    should still BLOCK (exit 2) -- normal behavior."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-vmatch-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        _git(self.tmpdir, "init", "-q", "-b", "main", str(self.tmpdir / "repo"))
        self.repo = self.tmpdir / "repo"

        # Get the actual local ruff version
        r = subprocess.run(["ruff", "--version"], capture_output=True, text=True)
        local_ver = r.stdout.strip().split()[-1]  # "ruff X.Y.Z" -> "X.Y.Z"

        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n')

        # CI workflow with the MATCHING ruff version
        ci_dir = self.repo / ".github" / "workflows"
        ci_dir.mkdir(parents=True)
        (ci_dir / "ci.yml").write_text(
            f"jobs:\n  gate:\n    steps:\n"
            f"      - run: pip install ruff=={local_ver}\n"
        )

        (self.repo / "clean.py").write_text("X = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

        # Add a file with a genuine lint violation
        (self.repo / "bad.py").write_text("import os\nimport sys\n")
        _git(self.repo, "add", "bad.py")
        _git(self.repo, "commit", "-q", "-m", "add bad file")

    def test_version_match_still_blocks(self):
        r = _run_hook(self.repo)
        self.assertEqual(
            r.returncode, 2,
            f"Hook should BLOCK (exit 2) when versions match and lint fails.\n"
            f"Output: {r.stdout + r.stderr}",
        )
        combined = r.stdout + r.stderr
        self.assertIn("BLOCKED", combined)


class TestNoWorkflowFileStillBlocks(TestCase):
    """When no CI workflow is found (can't detect pinned version), the hook
    should behave normally -- BLOCK on lint failure."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-noci-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        _git(self.tmpdir, "init", "-q", "-b", "main", str(self.tmpdir / "repo"))
        self.repo = self.tmpdir / "repo"

        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
        # NO .github/workflows/ directory
        (self.repo / "clean.py").write_text("X = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

        (self.repo / "bad.py").write_text("import os\nimport sys\n")
        _git(self.repo, "add", "bad.py")
        _git(self.repo, "commit", "-q", "-m", "add bad file")

    def test_no_workflow_file_still_blocks(self):
        r = _run_hook(self.repo)
        self.assertEqual(
            r.returncode, 2,
            f"Hook should BLOCK when no CI workflow found.\n"
            f"Output: {r.stdout + r.stderr}",
        )


if __name__ == "__main__":
    main()
