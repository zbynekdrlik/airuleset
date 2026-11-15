"""#1312: the pure ``gates.pushtarget.parse`` shapes (no git, no repo)."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gates import pushscope, pushtarget  # noqa: E402

P = pushtarget.PushTarget
H = ("HEAD",)


class Parse1312(unittest.TestCase):

    def check(self, cmd, *want):
        self.assertEqual(pushtarget.parse(cmd), list(want), cmd)

    def test_bare_push_keeps_cwd_and_head(self):
        self.check("git push", P(None, H, False))
        self.check("git push -u origin HEAD", P(None, H, False))

    def test_git_dash_c(self):
        self.check("git -C /w/lane push origin lane", P("/w/lane", ("lane",), False))
        self.check("git -C /w -C sub push", P("/w/sub", H, False))
        self.check("git -c core.x=1 -C /w push origin HEAD:dev", P("/w", H, False))

    def test_cd_chain(self):
        self.check("cd /w/lane && git push origin lane", P("/w/lane", ("lane",), False))
        self.check("cd /w; cd lane && git push", P("/w/lane", H, False))
        self.check("cd /w && git -C sub push", P("/w/sub", H, False))
        self.check("cd -P /w && git push", P("/w", H, False))

    def test_newline_separates_commands(self):
        """Review A: a push on line 2 was invisible to the parser."""
        self.check("echo hi\ngit -C /wt push origin lane", P("/wt", ("lane",), False))
        self.check("cd /wt\ngit push origin lane", P("/wt", ("lane",), False))
        self.check("cd /wt \\\n && git push", P("/wt", H, False))

    def test_unfollowable_cd_falls_back_to_cwd(self):
        self.check("cd $WT && git push origin lane", P(None, ("lane",), False))
        self.check("cd /w | git push", P(None, H, False))
        self.check("(cd /w) && git push", P(None, H, False))
        self.check("cd - && git push", P(None, H, False))

    def test_refspec_forms(self):
        self.check("git push origin +lane:main", P(None, ("lane",), False))
        self.check("git push origin refs/heads/lane", P(None, ("refs/heads/lane",), False))
        self.check("git push -o ci.skip origin lane", P(None, ("lane",), False))
        self.check("git push origin @", P(None, H, False))
        self.check("git push --repo=origin lane", P(None, ("lane",), False))

    def test_every_refspec_is_a_source(self):
        """Review B: only the first refspec was judged."""
        self.check("git push origin clean lane", P(None, ("clean", "lane"), False))
        self.check("git push origin a:x :gone b:y", P(None, ("a", "b"), False))

    def test_delete_is_skipped(self):
        self.assertTrue(pushtarget.parse("git push origin :old")[0].skip)
        self.assertTrue(pushtarget.parse("git push --delete origin old")[0].skip)

    def test_env_prefix_and_absolute_git(self):
        self.check("GIT_TRACE=0 /usr/bin/git -C /w push origin x", P("/w", ("x",), False))

    def test_every_push_segment_is_returned(self):
        """Review B 🔴: an earlier clean or delete push shadowed a later one."""
        self.check("git status && git -C /a push origin a && git -C /b push",
                   P("/a", ("a",), False), P("/b", H, False))
        self.check("git push origin --delete foo && git push",
                   P(None, (), True), P(None, H, False))

    def test_wrapped_pushes_and_unknown_relative_cd(self):
        self.check("git push -d o x && sudo -u x git push origin lane",
                   P(None, (), True), P(None, ("lane",), False))
        self.check("cd $X && cd ../wt && git push", P(None, H, False))
        self.check("(cd /w && git push)", P("/w", H, False))
        self.check("cd $X && git -C /abs push", P("/abs", H, False))

    def test_a_push_the_parser_cannot_see_adds_the_old_target(self):
        self.check("git -C /a push origin a && bash -c 'git push'",
                   P("/a", ("a",), False), P(None, H, False))

    def test_unbalanced_quote_is_the_old_behaviour(self):
        self.check("git push origin 'lane", P(None, H, False))

    def test_push_regexes_are_linear(self):
        """`-C <dir>` and `-\\S+` overlapped on a `-C -C -C …` run: exponential."""
        import time
        cmd = "gi" + "t " + "-C " * 2000 + "x"
        t = time.monotonic()
        pushtarget._ANY_PUSH_RE.findall(cmd)
        pushscope.is_push_command(cmd)
        pushtarget.parse(cmd)
        self.assertLess(time.monotonic() - t, 1.0)

    def test_push_regex_accepts_dash_c(self):
        self.assertTrue(pushscope.is_push_command("git -C /tmp/wt push origin x"))
        self.assertTrue(pushscope.is_push_command("git -c a=b push"))
        self.assertFalse(pushscope.is_push_command("git -C /tmp/wt status"))


class Resolved1312(unittest.TestCase):

    def ok(self, _args, _cwd):
        return subprocess.CompletedProcess([], 0)

    def test_a_dir_that_is_no_work_tree_falls_back_to_cwd(self):
        """Review A: `cd -P /x` used to become dir `-P`, then a silent allow."""
        got = pushtarget.resolved("cd /nowhere && git push origin lane", self.ok,
                                  lambda d: d is None)
        self.assertEqual(got, [(None, "lane", "lane")])

    def test_deletes_add_nothing_and_unknown_sources_are_head(self):
        def git(args, _cwd):
            return subprocess.CompletedProcess([], 0 if "lane^{commit}" in args else 1)
        got = pushtarget.resolved("git push origin :old && git push origin lane gone",
                                  git, lambda d: True)
        self.assertEqual(got, [(None, "lane", "lane"), (None, "HEAD", None)])


if __name__ == "__main__":
    unittest.main()
