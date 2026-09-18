"""#1061 item 1 -- gates.designbypost: a lane WORKER's raw `gh issue comment`
carrying `Design-by: main` is BLOCKED (the anti-spoof for the dispatch gate).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import designbypost as dbp  # noqa: E402

WT = "/home/airuleset/devel/airuleset/.claude/worktrees/agent-x"
MAIN = "/home/airuleset/devel/airuleset"


class TestEvaluate(unittest.TestCase):
    def test_worker_design_by_main_inline_blocks(self):
        v, r = dbp.evaluate(
            'gh issue comment 5 --body "some text\nDesign-by: main claude-fable-5-1"',
            WT)
        self.assertEqual(v, "block", r)

    def test_worker_design_by_worker_allowed(self):
        v, _ = dbp.evaluate(
            'gh issue comment 5 --body "Design-by: worker claude-opus-4-8"', WT)
        self.assertEqual(v, "allow")

    def test_main_cwd_design_by_main_allowed(self):
        v, _ = dbp.evaluate(
            'gh issue comment 5 --body "Design-by: main claude-fable-5-1"', MAIN)
        self.assertEqual(v, "allow")

    def test_non_comment_command_allowed(self):
        v, _ = dbp.evaluate('echo "Design-by: main x"', WT)
        self.assertEqual(v, "allow")

    def test_anchors_confirmed_comment_allowed(self):
        v, _ = dbp.evaluate(
            'gh issue comment 5 --body "Anchors-confirmed: all present"', WT)
        self.assertEqual(v, "allow")

    def test_bold_design_by_main_blocks(self):
        v, _ = dbp.evaluate(
            'gh issue comment 5 --body "**Design-by:** main claude-fable-5-1"', WT)
        self.assertEqual(v, "block")

    def test_bypass_token_allows(self):
        v, r = dbp.evaluate(
            'gh issue comment 5 --body "Design-by: main x"  '
            '# airuleset:design-by-ok test', WT)
        self.assertEqual(v, "allow")
        self.assertIn("bypass", r)

    def test_body_file_design_by_main_blocks(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        f = Path(d) / "body.md"
        f.write_text("Design-by: main claude-fable-5-1\n")
        v, _ = dbp.evaluate("gh issue comment 5 -F %s" % f, WT)
        self.assertEqual(v, "block")

    def test_repo_flag_between_gh_and_issue(self):
        v, _ = dbp.evaluate(
            'gh -R owner/repo issue comment 5 --body "Design-by: main x"', WT)
        self.assertEqual(v, "block")

    def test_gh_api_comments_post_blocks(self):
        # the EXACT API the dispatch gate reads from -- a spoof here is trusted.
        v, _ = dbp.evaluate(
            "gh api repos/o/r/issues/5/comments -f "
            "body='some text Design-by: main claude-fable-5-1'", WT)
        self.assertEqual(v, "block")

    def test_gh_api_comments_read_allowed(self):
        # a bare GET carries no Design-by: main body -> not blocked.
        v, _ = dbp.evaluate("gh api repos/o/r/issues/5/comments --paginate", WT)
        self.assertEqual(v, "allow")

    def test_glued_body_file_blocks(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        f = Path(d) / "body.md"
        f.write_text("Design-by: main claude-fable-5-1\n")
        v, _ = dbp.evaluate("gh issue comment 5 -F%s" % f, WT)
        self.assertEqual(v, "block")

    # #1060 L3b review 🔴: the dual-agent IMPLEMENTER session runs with
    # AIRULESET_ROLE=implementer (set by the claude-impl launcher) in its own
    # worktree. It is a NON-main author that must NOT pass off a design as the
    # Fable main's — the anti-spoof gate must block a hand-typed `Design-by:
    # main` from it exactly as it blocks a worker. (Before the fix,
    # `_is_lane_worker` keyed on `== "worker"` only, so an implementer session
    # spoofed straight through — the receiving-side dispatch gate then trusted
    # the spoofed comment.)
    def test_implementer_env_design_by_main_blocks_worktree(self):
        with mock.patch.dict(os.environ, {"AIRULESET_ROLE": "implementer"}):
            v, r = dbp.evaluate(
                'gh issue comment 5 --body "Design-by: main claude-fable-5-1"', WT)
        self.assertEqual(v, "block", r)

    def test_implementer_env_design_by_main_blocks_even_main_cwd(self):
        # the env role identifies the implementer regardless of cwd — so even a
        # non-worktree cwd must be blocked (the env is set only by the managed
        # launcher, never a self-declared string).
        with mock.patch.dict(os.environ, {"AIRULESET_ROLE": "implementer"}):
            v, r = dbp.evaluate(
                'gh issue comment 5 --body "Design-by: main claude-fable-5-1"',
                MAIN)
        self.assertEqual(v, "block", r)

    def test_implementer_env_design_by_worker_allowed(self):
        # an implementer stamping its OWN honest `Implemented-by:`/worker line is
        # never the spoof — only `Design-by: main` is.
        with mock.patch.dict(os.environ, {"AIRULESET_ROLE": "implementer"}):
            v, _ = dbp.evaluate(
                'gh issue comment 5 --body "Anchors-confirmed: all present"', WT)
        self.assertEqual(v, "allow")


class TestAdapterEndToEnd(unittest.TestCase):
    HOOK = REPO / "hooks" / "block-design-by-spoof.sh"

    def _run(self, command, cwd):
        env = dict(os.environ, HOME=tempfile.mkdtemp())
        payload = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": command}, "cwd": cwd})
        return subprocess.run(["bash", str(self.HOOK)], input=payload,
                              capture_output=True, text=True, env=env)

    def test_worker_spoof_blocked_via_adapter(self):
        r = self._run('gh issue comment 5 --body "Design-by: main claude-fable-5-1"',
                      WT)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Design-by: main", r.stderr)

    def test_worker_worker_stamp_allowed_via_adapter(self):
        r = self._run('gh issue comment 5 --body "Design-by: worker claude-opus-4-8"',
                      WT)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
