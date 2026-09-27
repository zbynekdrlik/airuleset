"""#1162: on a two-branch repo's ``dev``, the PR-scoped push gates must measure
the PR range ``origin/main..HEAD`` -- never the tracking ref ``origin/dev``.

Live repro (iemmixer): the RED commit ``c696a0a`` was pushed first (its CI
failed as intended); the GREEN ``efbd184`` push was then blocked as
GREEN-without-RED, because ``gates.pushscope.resolve()`` fell through to the
#909 tracking fallback on ``dev`` and the range became ``origin/dev..HEAD`` --
only the not-yet-pushed GREEN commit, with the already-pushed RED invisible.

Real temp git repo + a local BARE origin (main + dev); no network, no gh.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gates import pushscope  # noqa: E402

HOOK = REPO / "hooks" / "pre-push-test-check.sh"


def _clean_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["HOME"] = home
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class _TwoBranchRepo(unittest.TestCase):
    """A work repo on ``dev`` whose origin is a local bare repo holding main +
    dev, with ``dev`` tracking ``origin/dev`` (the real two-branch shape)."""

    DEFAULT = "main"
    SET_HEAD = True

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pushscope1162-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = _clean_env(self.home)
        self.origin = os.path.join(self.tmp, "origin.git")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self._git(self.tmp, "init", "-q", "--bare", "-b", self.DEFAULT, self.origin)
        self.g("init", "-q", "-b", self.DEFAULT)
        self.g("remote", "add", "origin", self.origin)
        self._write("app.py", "def f():\n    return 1\n")
        self.g("add", "app.py")
        self.g("commit", "-qm", "base")
        self.g("push", "-q", "origin", self.DEFAULT)
        if self.SET_HEAD:
            self.g("remote", "set-head", "origin", self.DEFAULT)
        self.g("checkout", "-qb", "dev")
        self.g("push", "-q", "-u", "origin", "dev")

    def _git(self, cwd, *args):
        r = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t",
             "-c", "commit.gpgsign=false", *args],
            cwd=cwd, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, "git %s failed: %s" % (args, r.stderr))
        return r.stdout

    def g(self, *args):
        return self._git(self.work, *args)

    def _write(self, rel, text):
        p = Path(self.work, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def red_commit(self):
        self._write("tests/test_app.py",
                    "from app import f\n\n\ndef test_f():\n"
                    "    assert f() == 2\n    assert f() != 1\n")
        self.g("add", "tests/test_app.py")
        self.g("commit", "-qm", "test: [red] f must return 2")

    def green_commit(self):
        self._write("app.py", "def f():\n    return 2\n")
        self.g("add", "app.py")
        self.g("commit", "-qm", "fix: f returns 2\n\nCloses #7")

    def resolve(self, **kw):
        # Hermetic in-process call: no inherited GIT_* / real HOME gitconfig.
        with mock.patch.dict(os.environ, self.env, clear=True):
            return pushscope.resolve(self.work, **kw)

    def run_hook(self, command="git push origin dev"):
        import json
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                              capture_output=True, cwd=self.work, env=self.env,
                              timeout=60)


class TestResolveTwoBranchDev1162(_TwoBranchRepo):
    def test_dev_with_tracking_ref_resolves_to_pr_target(self):
        self.red_commit()
        self.g("push", "-q", "origin", "dev")
        self.green_commit()
        base, dest, cur, default = self.resolve()
        self.assertEqual((cur, default), ("dev", "main"))
        self.assertEqual(base, "origin/main",
                         "two-branch dev must measure the dev->main PR range")
        self.assertEqual(dest, "origin/main")

    def test_branch_override_still_narrows_dev(self):
        # block-test-skips' per-added-line semantics (#909 F1) keep the
        # origin/<branch> range; only the destination follows the fix.
        base, dest, _cur, _default = self.resolve(apply_branch_override=True)
        self.assertEqual(base, "origin/dev")
        self.assertEqual(dest, "origin/main")


class TestPrePushHookTwoBranchDev1162(_TwoBranchRepo):
    def test_green_after_pushed_red_passes(self):
        self.red_commit()
        self.g("push", "-q", "origin", "dev")
        self.green_commit()
        r = self.run_hook()
        self.assertEqual(r.returncode, 0,
                         "GREEN after an already-pushed RED must pass: %s"
                         % (r.stdout + r.stderr))

    def test_green_without_red_on_dev_still_blocks(self):
        self.green_commit()
        r = self.run_hook()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_green_before_red_on_dev_still_blocks(self):
        # Order still matters across the whole PR range: a fix pushed BEFORE
        # its test is a GREEN-without-RED, even when a later test exists.
        self.green_commit()
        self.g("push", "-q", "origin", "dev")
        self.red_commit()
        r = self.run_hook()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Bug-fix commit appears BEFORE", r.stdout + r.stderr)


class TestDevAlongsideDevelop1162(_TwoBranchRepo):
    def test_three_branch_repo_with_a_dev_branch_keeps_develop(self):
        # A develop-based repo that also has a `dev` branch is NOT the
        # two-branch shape: its PR target stays origin/develop (pre-#1162).
        self.g("push", "-q", "origin", "dev:develop")
        self.g("fetch", "-q", "origin")
        base, dest, _cur, _default = self.resolve()
        self.assertEqual((base, dest), ("origin/develop", "origin/develop"))


class TestUnresolvedMasterDefault1162(_TwoBranchRepo):
    """origin/HEAD unset and the default is `master`: default_branch() falls
    back to "main", whose origin ref does not exist. The dev arm must not pin
    that phantom range (Gate 2 would silently see no commits)."""

    DEFAULT = "master"
    SET_HEAD = False

    def test_phantom_default_falls_back_to_tracking_ref(self):
        base, _dest, cur, default = self.resolve()
        self.assertEqual((cur, default), ("dev", "main"))
        self.assertEqual(base, "origin/dev")

    def test_fix_before_test_still_blocks(self):
        self.green_commit()
        self.red_commit()
        r = self.run_hook()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Bug-fix commit appears BEFORE", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
