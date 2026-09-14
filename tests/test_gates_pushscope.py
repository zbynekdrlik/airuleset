"""#1020 -- fast, subprocess-free unit tests for gates.pushscope's pure
push-shape helpers. The git-dependent resolver (resolve/changed_files) is
covered end-to-end by TestBlockTestSkips* / TestPrePush* in test_airuleset.py
(the #847/#909/#1003 oracle); this file locks the two shape predicates that used
to be duplicated grep patterns.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import pushscope                                # noqa: E402


class TestIsPushCommand(unittest.TestCase):
    def test_plain_push(self):
        self.assertTrue(pushscope.is_push_command("git push origin main"))

    def test_push_with_single_token_flag(self):
        # The precise pattern matches a chain of `-flag` tokens before push
        # (verbatim from block-test-skips). A `-c key=val` value token breaks
        # the chain (a pre-existing limitation, faithfully preserved).
        self.assertTrue(pushscope.is_push_command("git --no-verify push origin dev"))

    def test_not_a_push(self):
        self.assertFalse(pushscope.is_push_command("git status"))

    def test_strict_ignores_push_inside_quotes(self):
        # block-test-skips' quote-stripped shape: "git push" inside a quoted
        # commit message is NOT a push command.
        self.assertFalse(pushscope.is_push_command(
            "git commit -m 'mention git push in the message'", quote_strip=True))

    def test_strict_rejects_pushall_substring(self):
        # precise pattern requires push at a word boundary.
        self.assertFalse(pushscope.is_push_command("git pushall", quote_strip=True))

    def test_loose_matches_inside_quotes(self):
        # pre-push-test-check's historical loose shape (raw substring).
        self.assertTrue(pushscope.is_push_command(
            "git commit -m 'git push later'", quote_strip=False))


class TestIsWipBackupPush(unittest.TestCase):
    def test_backup_refspec(self):
        self.assertTrue(pushscope.is_wip_backup_push(
            "git push origin HEAD:refs/autopilot-wip/worktree-agent-x"))

    def test_backup_delete(self):
        self.assertTrue(pushscope.is_wip_backup_push(
            "git push origin --delete refs/autopilot-wip/worktree-agent-x"))

    def test_normal_push_is_not_backup(self):
        self.assertFalse(pushscope.is_wip_backup_push("git push origin main"))


if __name__ == "__main__":
    unittest.main()
