"""Characterization test for hooks/pre-push-lint.sh byte-identical file scope (#951 item 3).

Files that are byte-identical to the merge-base target (i.e. not actually
changed by the branch) must not be linted or blocked. The three-dot range
(BASE_REF...HEAD) already excludes them -- no per-file bypass code is needed.

These tests PROVE that the three-dot range handles both:
1. Common case: a file present on both sides of a merge, byte-identical to
   the base -- excluded from lint scope by the three-dot range.
2. Positive control: the branch's own dirty file IS linted and blocked.
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


class TestByteIdenticalFileNotLinted(TestCase):
    """A file that exists on develop with lint errors but is byte-identical
    on the feature branch (came via merge, never touched by the branch) must
    NOT cause the hook to block.

    This is the miva1 incident scenario: upstream_dirty.py is a ruff-violating
    file that develop owns. The feature branch merges develop (bringing it in)
    but never touches it. The three-dot range should already exclude it, and
    the per-file identity check is the safety net.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-ident-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        # Seed repo
        seed = self.tmpdir / "seed"
        _git(self.tmpdir, "init", "-q", "-b", "main", str(seed))
        (seed / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (seed / "clean.py").write_text("X = 1\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "init")

        # Bare origin
        self.origin = self.tmpdir / "origin.git"
        _git(self.tmpdir, "clone", "--bare", "-q", str(seed), str(self.origin))

        # Local clone
        self.local = self.tmpdir / "local"
        _git(self.tmpdir, "clone", "-q", str(self.origin), str(self.local))

        # Create develop, push
        _git(self.local, "checkout", "-q", "-b", "develop")
        _git(self.local, "push", "-q", "origin", "develop")

        # Feature from develop
        _git(self.local, "checkout", "-q", "-b", "feature")
        (self.local / "my_clean.py").write_text("FEAT = 1\n")
        _git(self.local, "add", "my_clean.py")
        _git(self.local, "commit", "-q", "-m", "feature work")
        _git(self.local, "push", "-q", "-u", "origin", "feature")

        # Develop advances with a dirty file
        _git(self.local, "checkout", "-q", "develop")
        (self.local / "upstream_dirty.py").write_text(
            "import os\nimport sys\n"  # unused imports = ruff violation
        )
        _git(self.local, "add", "upstream_dirty.py")
        _git(self.local, "commit", "-q", "-m", "dirty upstream")
        _git(self.local, "push", "-q", "origin", "develop")

        # Feature merges develop (brings in the dirty file, byte-identical)
        _git(self.local, "checkout", "-q", "feature")
        _git(self.local, "fetch", "-q", "origin")
        _git(self.local, "merge", "-q", "--no-edit", "origin/develop")

    def test_byte_identical_upstream_file_not_blocked(self):
        """The merged dirty file should not cause a block -- it is byte-identical
        to develop and was never touched by the feature branch."""
        r = _run_hook(self.local)
        combined = r.stdout + r.stderr
        self.assertEqual(
            r.returncode, 0,
            f"Hook wrongly blocked on a byte-identical upstream file.\n"
            f"Output: {combined}",
        )
        # Ensure the dirty file is NOT mentioned as being linted
        self.assertNotIn(
            "upstream_dirty.py", combined,
            f"upstream_dirty.py should not be in lint scope.\nOutput: {combined}",
        )


class TestBranchOwnDirtyFileStillBlocked(TestCase):
    """A file that the branch itself changes with lint errors must still be
    blocked, even with the identity bypass in place."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="airuleset-pplint-ident2-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        seed = self.tmpdir / "seed"
        _git(self.tmpdir, "init", "-q", "-b", "main", str(seed))
        (seed / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (seed / "clean.py").write_text("X = 1\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "init")

        self.origin = self.tmpdir / "origin.git"
        _git(self.tmpdir, "clone", "--bare", "-q", str(seed), str(self.origin))

        self.local = self.tmpdir / "local"
        _git(self.tmpdir, "clone", "-q", str(self.origin), str(self.local))

        _git(self.local, "checkout", "-q", "-b", "develop")
        _git(self.local, "push", "-q", "origin", "develop")

        _git(self.local, "checkout", "-q", "-b", "feature")

        # Branch's OWN dirty file (not from upstream)
        (self.local / "my_bad.py").write_text("import os\nimport sys\n")
        _git(self.local, "add", "my_bad.py")
        _git(self.local, "commit", "-q", "-m", "my own bad file")
        _git(self.local, "push", "-q", "-u", "origin", "feature")

    def test_branch_own_dirty_file_still_blocks(self):
        r = _run_hook(self.local)
        self.assertEqual(
            r.returncode, 2,
            f"Hook should block on the branch's own lint violation.\n"
            f"Output: {r.stdout + r.stderr}",
        )
        combined = r.stdout + r.stderr
        self.assertIn("BLOCKED", combined)


if __name__ == "__main__":
    main()
