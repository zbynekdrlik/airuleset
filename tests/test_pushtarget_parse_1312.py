"""#1312: the pure ``gates.pushtarget.parse`` shapes (no git, no repo)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gates import pushscope, pushtarget  # noqa: E402

P = pushtarget.PushTarget


class Parse1312(unittest.TestCase):

    def check(self, cmd, want):
        self.assertEqual(pushtarget.parse(cmd), want, cmd)

    def test_bare_push_keeps_cwd_and_head(self):
        self.check("git push", P(None, "HEAD", False))
        self.check("git push -u origin HEAD", P(None, "HEAD", False))

    def test_git_dash_c(self):
        self.check("git -C /w/lane push origin lane", P("/w/lane", "lane", False))
        self.check("git -C /w -C sub push", P("/w/sub", "HEAD", False))
        self.check("git -c core.x=1 -C /w push origin HEAD:dev", P("/w", "HEAD", False))

    def test_cd_chain(self):
        self.check("cd /w/lane && git push origin lane", P("/w/lane", "lane", False))
        self.check("cd /w; cd lane && git push", P("/w/lane", "HEAD", False))
        self.check("cd /w && git -C sub push", P("/w/sub", "HEAD", False))

    def test_unfollowable_cd_falls_back_to_cwd(self):
        self.check("cd $WT && git push origin lane", P(None, "lane", False))
        self.check("cd /w | git push", P(None, "HEAD", False))
        self.check("(cd /w) && git push", P(None, "HEAD", False))

    def test_refspec_forms(self):
        self.check("git push origin +lane:main", P(None, "lane", False))
        self.check("git push origin refs/heads/lane", P(None, "refs/heads/lane", False))
        self.check("git push -o ci.skip origin lane", P(None, "lane", False))
        self.check("git push origin @", P(None, "HEAD", False))

    def test_delete_is_skipped(self):
        self.assertTrue(pushtarget.parse("git push origin :old").skip)
        self.assertTrue(pushtarget.parse("git push --delete origin old").skip)

    def test_env_prefix_and_absolute_git(self):
        self.check("GIT_TRACE=0 /usr/bin/git -C /w push origin x", P("/w", "x", False))

    def test_first_push_segment_wins(self):
        self.check("git status && git -C /a push origin a && git -C /b push",
                   P("/a", "a", False))

    def test_unbalanced_quote_is_the_old_behaviour(self):
        self.check("git push origin 'lane", P(None, "HEAD", False))

    def test_push_regex_accepts_dash_c(self):
        self.assertTrue(pushscope.is_push_command("git -C /tmp/wt push origin x"))
        self.assertTrue(pushscope.is_push_command("git -c a=b push"))
        self.assertFalse(pushscope.is_push_command("git -C /tmp/wt status"))


if __name__ == "__main__":
    unittest.main()
