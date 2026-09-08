"""Behaviour test for hooks/pre-push-lint.sh ruff version pin awareness (#951 item 2).

When the local ruff version differs from the CI-pinned version
(`.github/workflows/ci.yml` carries `ruff==<pinned>`), the hook uses a surgical
approach: it re-runs failing files with --select E9,F (the version-stable
pyflakes/syntax core). If the stable core also fails -> BLOCK (real errors,
version-independent). If only non-core rules failed -> WARN (exit 0).

This avoids wholesale fail-open (#951 review RED: pyproject.toml #429 pins
select to E4,E7,E9,F precisely so version drift does NOT change results — an
F401 is F401 in every ruff version).
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


class TestVersionMismatchStableCoreStillBlocks(TestCase):
    """When the CI-pinned ruff version does NOT match the local ruff, a lint
    failure in the stable core (E9,F — e.g. F401 unused import) must still
    BLOCK. The surgical approach re-runs with --select E9,F; if that also
    fails, the error is version-independent and gets blocked."""

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

        # F401 (unused import) is a pyflakes F rule — stable core, version-
        # independent. Must still BLOCK even with version mismatch.
        (self.repo / "bad.py").write_text("import os\nimport sys\n")
        _git(self.repo, "add", "bad.py")
        _git(self.repo, "commit", "-q", "-m", "add bad file")

    def test_version_mismatch_stable_core_F401_still_blocks(self):
        r = _run_hook(self.repo)
        combined = r.stdout + r.stderr
        # F401 is in the E9,F stable core — the hook must BLOCK
        self.assertEqual(
            r.returncode, 2,
            f"Hook should BLOCK on stable-core errors (F401) even with "
            f"version mismatch.\nOutput: {combined}",
        )
        self.assertIn("BLOCKED", combined)
        # Both versions should be named in the output
        self.assertIn("99.0.0", combined, "CI pin version should be in output")


class TestVersionMismatchNonCoreWarnOnly(TestCase):
    """When only NON-CORE rules fail (outside E9,F), and there is a version
    mismatch, the hook should WARN (exit 0) — the failures may be version-
    sensitive. We simulate this with an E401 violation (multiple imports on
    one line) that E9,F does not catch."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-noncore-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        _git(self.tmpdir, "init", "-q", "-b", "main", str(self.tmpdir / "repo"))
        self.repo = self.tmpdir / "repo"

        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n')

        ci_dir = self.repo / ".github" / "workflows"
        ci_dir.mkdir(parents=True)
        (ci_dir / "ci.yml").write_text(
            "jobs:\n  gate:\n    steps:\n"
            "      - run: pip install ruff==99.0.0\n"
        )

        (self.repo / "clean.py").write_text("X = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

        # E401 (multiple imports on one line) — an E4 rule, NOT in E9,F.
        # `ruff check` catches it; `ruff check --select E9,F` does not.
        # Both imports are used, so no F401 (unused import) fires.
        (self.repo / "style_issue.py").write_text(
            "import os, sys\nprint(os.getcwd())\nprint(sys.version)\n"
        )
        _git(self.repo, "add", "style_issue.py")
        _git(self.repo, "commit", "-q", "-m", "add style issue")

    def test_version_mismatch_non_core_warns_not_blocks(self):
        r = _run_hook(self.repo)
        combined = r.stdout + r.stderr
        # Non-core rule failure with version mismatch -> WARN, not BLOCK
        self.assertEqual(
            r.returncode, 0,
            f"Hook should WARN (not BLOCK) on non-core rule failures with "
            f"version mismatch.\nOutput: {combined}",
        )
        # Should mention the version mismatch
        self.assertIn("99.0.0", combined, "CI pin version should be in output")


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


class TestVersionMismatchPinnedRepoStillBlocks(TestCase):
    """When the repo pins its lint rule set via `select =` in pyproject.toml
    (e.g. #429: select = ["E4","E7","E9","F"]), the results are repo-controlled
    and version-independent.  A version mismatch grants NO concession — the
    hook must BLOCK (exit 2) on ANY lint failure, exactly as without mismatch.

    RED-1 from the Fable review: the prior hook re-checked with --select E9,F
    regardless of a select pin, letting E4/E7 violations through (E401 is E4
    family, never caught by E9,F)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-pinned-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        _git(self.tmpdir, "init", "-q", "-b", "main", str(self.tmpdir / "repo"))
        self.repo = self.tmpdir / "repo"

        # pyproject.toml WITH a ruff lint select pin — makes results
        # version-controlled, so no mismatch concession should apply.
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\n\n'
            '[tool.ruff.lint]\nselect = ["E4", "E7", "E9", "F"]\n'
        )

        # CI workflow with a FAKE pinned ruff version that forces a mismatch
        ci_dir = self.repo / ".github" / "workflows"
        ci_dir.mkdir(parents=True)
        (ci_dir / "ci.yml").write_text(
            "jobs:\n  gate:\n    steps:\n"
            "      - run: pip install ruff==99.0.0\n"
        )

        (self.repo / "clean.py").write_text("X = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

        # E401 (multiple imports on one line) — an E4 rule, version-
        # independent when the repo pins select = ["E4",...].
        (self.repo / "bad.py").write_text(
            "import os, sys\nprint(os.getcwd())\nprint(sys.version)\n"
        )
        _git(self.repo, "add", "bad.py")
        _git(self.repo, "commit", "-q", "-m", "add bad file")

    def test_pinned_repo_blocks_on_mismatch(self):
        """A repo with select= pin in pyproject.toml must BLOCK on ANY lint
        failure regardless of version mismatch — no concession."""
        r = _run_hook(self.repo)
        combined = r.stdout + r.stderr
        self.assertEqual(
            r.returncode, 2,
            f"Hook should BLOCK on a pinned repo (select= in pyproject.toml) "
            f"even with version mismatch.\nOutput: {combined}",
        )
        self.assertIn("BLOCKED", combined)


class TestVersionMismatchPinnedRuffTomlBlocks(TestCase):
    """Same as above but with the select pin in ruff.toml instead of
    pyproject.toml — the hook must check both config files."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-rufftoml-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        _git(self.tmpdir, "init", "-q", "-b", "main", str(self.tmpdir / "repo"))
        self.repo = self.tmpdir / "repo"

        # pyproject.toml WITHOUT ruff config — the pin is in ruff.toml
        (self.repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
        # ruff.toml with a select pin
        (self.repo / "ruff.toml").write_text(
            '[lint]\nselect = ["E4", "E7", "E9", "F"]\n'
        )

        ci_dir = self.repo / ".github" / "workflows"
        ci_dir.mkdir(parents=True)
        (ci_dir / "ci.yml").write_text(
            "jobs:\n  gate:\n    steps:\n"
            "      - run: pip install ruff==99.0.0\n"
        )

        (self.repo / "clean.py").write_text("X = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

        (self.repo / "bad.py").write_text(
            "import os, sys\nprint(os.getcwd())\nprint(sys.version)\n"
        )
        _git(self.repo, "add", "bad.py")
        _git(self.repo, "commit", "-q", "-m", "add bad file")

    def test_pinned_ruff_toml_blocks_on_mismatch(self):
        r = _run_hook(self.repo)
        combined = r.stdout + r.stderr
        self.assertEqual(
            r.returncode, 2,
            f"Hook should BLOCK when ruff.toml has select= pin, even with "
            f"version mismatch.\nOutput: {combined}",
        )
        self.assertIn("BLOCKED", combined)


if __name__ == "__main__":
    main()
