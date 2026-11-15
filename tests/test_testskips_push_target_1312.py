"""#1312: block-test-skips must judge the repo and the ref the push SENDS.

Live incident (odoo-erp stream david3, lanes 10044/10048/10060, 12.11.2026):
a session sitting in its MAIN checkout ran ``git -C <worktree> push origin
<lane>``. The gate resolved the push range in the session cwd (the main
checkout, on another branch), flagged a skip that lived only there, and forced
a logged ``# airuleset:test-skip-ok`` bypass on a clean lane. The reverse is
the under-block: a lane that really adds a skip passed whenever the cwd
checkout was clean.

Real temp repos (a bare origin, a main checkout, a linked worktree); the REAL
hook runs with cwd = the main checkout, exactly as a session hook does.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "block-test-skips.sh"

# Built from parts so this file never trips its own gate (#1069 self-scan).
SKIP_LINE = "@pytest.mark." + "skip\n"
CLEAN_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"


class _Repo(unittest.TestCase):
    """Bare origin + main checkout + a linked worktree on `lane`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="testskips1312-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env["HOME"] = os.path.join(self.tmp, "home")
        os.makedirs(self.env["HOME"])
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.origin = os.path.join(self.tmp, "origin.git")
        self.main = os.path.join(self.tmp, "main")
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(self.main)
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        self.git(self.main, "init", "-q", "-b", "main")
        self.git(self.main, "remote", "add", "origin", self.origin)
        self.write(self.main, "app.py", "x = 1\n")
        self.git(self.main, "add", "app.py")
        self.git(self.main, "commit", "-qm", "base")
        self.git(self.main, "push", "-q", "origin", "main")
        self.git(self.main, "remote", "set-head", "origin", "main")
        self.git(self.main, "worktree", "add", "-q", "-b", "lane", self.wt, "origin/main")

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                            "-c", "commit.gpgsign=false", *args],
                           cwd=cwd, env=self.env, capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, "git %s: %s" % (args, r.stderr))
        return r.stdout

    def write(self, root, rel, text):
        p = Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, root, rel, text, msg):
        self.write(root, rel, text)
        self.git(root, "add", rel)
        self.git(root, "commit", "-qm", msg)

    def run_hook(self, command, cwd):
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                              capture_output=True, cwd=cwd, env=self.env,
                              timeout=60)

    def dirty_main_checkout(self):
        """The session's own checkout carries a skip that the lane never had."""
        self.git(self.main, "checkout", "-qb", "develop-work")
        self.commit(self.main, "tests/test_main_only.py",
                    "import pytest\n\n\n" + SKIP_LINE + CLEAN_TEST, "main-only skip")


class PushTarget1312(_Repo):

    def test_clean_worktree_push_from_dirty_cwd_is_allowed(self):
        self.dirty_main_checkout()
        self.commit(self.wt, "tests/test_lane.py", CLEAN_TEST, "lane test")
        r = self.run_hook("git -C %s push origin lane" % self.wt, cwd=self.main)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_clean_worktree_push_after_cd_is_allowed(self):
        """``cd <worktree> && git push`` runs in the worktree, not the hook's
        cwd (the session's main checkout)."""
        self.dirty_main_checkout()
        self.commit(self.wt, "tests/test_lane.py", CLEAN_TEST, "lane test")
        r = self.run_hook("cd %s && git push origin lane" % self.wt, cwd=self.main)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_skip_in_worktree_blocks_after_cd(self):
        self.commit(self.wt, "tests/test_lane.py",
                    "import pytest\n\n\n" + SKIP_LINE + CLEAN_TEST, "lane skip")
        r = self.run_hook("cd %s && git push -u origin HEAD" % self.wt, cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_skip_in_worktree_blocks_from_clean_cwd(self):
        self.commit(self.wt, "tests/test_lane.py",
                    "import pytest\n\n\n" + SKIP_LINE + CLEAN_TEST, "lane skip")
        r = self.run_hook("git -C %s push origin lane" % self.wt, cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("tests/test_lane.py", r.stderr)

    def test_refspec_names_a_branch_other_than_head(self):
        """``git push origin lane`` from the main checkout sends ``lane``,
        not the checkout's own HEAD."""
        self.dirty_main_checkout()
        self.commit(self.wt, "tests/test_lane.py", CLEAN_TEST, "lane test")
        r = self.run_hook("git push origin lane:lane", cwd=self.main)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bare_push_keeps_the_cwd_head(self):
        """No refspec: the current branch of the -C/cwd repo, as before."""
        self.dirty_main_checkout()
        r = self.run_hook("git push", cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_bypass_marker_is_read_from_the_pushed_ref(self):
        """The bypass marker belongs to the pushed tip, never the cwd HEAD."""
        self.dirty_main_checkout()
        self.git(self.main, "commit", "-q", "--allow-empty", "-m",
                 "note # airuleset:test-skip-ok cwd-only marker")
        self.commit(self.wt, "tests/test_lane.py",
                    "import pytest\n\n\n" + SKIP_LINE + CLEAN_TEST, "lane skip")
        r = self.run_hook("git -C %s push origin lane" % self.wt, cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)


class ReviewFindings1312(_Repo):
    """Shapes both fresh-context reviews reproduced against the first cut."""

    def lane_with_skip(self):
        self.commit(self.wt, "tests/test_lane.py",
                    "import pytest\n\n\n" + SKIP_LINE + CLEAN_TEST, "lane skip")

    def test_an_earlier_delete_push_never_shadows_a_later_push(self):
        self.lane_with_skip()
        for cmd in ("git push origin --delete foo && git push origin lane",
                    "git push origin :gone; git push origin lane",
                    "git -C %s push origin main && git push origin lane" % self.main):
            self.assertEqual(self.run_hook(cmd, cwd=self.wt).returncode, 2, cmd)

    def test_every_refspec_is_judged(self):
        self.git(self.wt, "branch", "clean", "origin/main")
        self.lane_with_skip()
        r = self.run_hook("git push origin clean lane", cwd=self.wt)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_push_on_a_later_line_is_judged(self):
        self.lane_with_skip()
        r = self.run_hook("echo start\ngit -C %s push origin lane" % self.wt, cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_the_cwd_head_marker_never_bypasses_another_pushed_ref(self):
        """The checkout sits on X whose tip carries the marker; it pushes Y."""
        self.lane_with_skip()
        self.git(self.main, "commit", "-q", "--allow-empty", "-m",
                 "note # airuleset:test-skip-ok cwd-only marker")
        r = self.run_hook("git push origin lane", cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("tests/test_lane.py", r.stderr)

    def test_the_pushed_ref_marker_still_bypasses(self):
        self.lane_with_skip()
        self.git(self.wt, "commit", "-q", "--allow-empty", "-m",
                 "note # airuleset:test-skip-ok reviewed skip")
        r = self.run_hook("git push origin lane", cwd=self.main)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_delete_push_from_a_dirty_checkout_passes(self):
        self.dirty_main_checkout()
        r = self.run_hook("git push origin :lane", cwd=self.main)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_unresolvable_cd_falls_back_to_the_cwd(self):
        self.dirty_main_checkout()
        r = self.run_hook("cd /nonexistent-1312 && git push", cwd=self.main)
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
