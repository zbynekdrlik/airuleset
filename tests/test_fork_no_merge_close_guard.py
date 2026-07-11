"""Behaviour test for hooks/block-fork-no-merge-issue-close.sh (incident 2026-07-10).

A fork-no-merge worker on odoo-erp drifted from its own hand-off protocol and ran
`gh issue close` directly on ~10 issues — short-circuiting the gatekeeper review this
authority stream exists to enforce and removing the READY-FOR-REVIEW hand-off the
per-ticket Discord card keys off (so the user got only a terse "✅ DONE"). A prose rule
drifted; this hook cannot. It blocks `gh issue close` ONLY for a fork-no-merge stream,
resolved via `airuleset.py authority` (which honours a cwd CLAUDE.md
`airuleset:authority=<profile>` marker), and leaves full / branch-merge streams alone.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"


def _cwd_with_authority(profile):
    d = tempfile.mkdtemp()
    (Path(d) / "CLAUDE.md").write_text(
        f"# proj\n<!-- airuleset:authority={profile} -->\n")
    return d


def run(cmd, cwd):
    payload = json.dumps({"tool_input": {"command": cmd}})
    return subprocess.run(["bash", str(HOOK)], input=payload,
                          capture_output=True, text=True, cwd=cwd)


class TestForkNoMergeCloseGuard(TestCase):
    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.full = _cwd_with_authority("full")
        self.branch = _cwd_with_authority("branch-merge")

    def test_blocks_gh_issue_close_under_fork_no_merge(self):
        r = run("gh issue close 1408 --comment done", self.fork)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)
        self.assertIn("READY-FOR-REVIEW", r.stderr)

    def test_blocks_the_api_patch_close_form(self):
        r = run("gh api -X PATCH repos/o/n/issues/1408 -f state=closed", self.fork)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_api_read_predicate_that_mentions_closed(self):
        # False-positive fix (review 2026-07-11): a READ predicate is NOT a close.
        r = run('gh api repos/o/n/issues/1408 --jq \'.state=="closed"\'', self.fork)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_close_in_a_compound_command(self):
        r = run("cd sub && gh issue close 1400", self.fork)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_gh_issue_comment_under_fork_no_merge(self):
        r = run('gh issue comment 1408 --body "READY-FOR-REVIEW: br — tests green"',
                self.fork)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_gh_issue_close_under_full_authority(self):
        r = run("gh issue close 5 --comment obsolete", self.full)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_gh_issue_close_under_branch_merge(self):
        r = run("gh issue close 5 --comment obsolete", self.branch)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_unrelated_commands_under_fork_no_merge(self):
        for cmd in ("git status", "gh issue list --state open",
                    "gh issue view 5 --json title", "gh pr list"):
            r = run(cmd, self.fork)
            self.assertEqual(r.returncode, 0, f"{cmd}\n{r.stderr}")

    def test_fails_safe_when_authority_unresolvable(self):
        # Review 2026-07-11: if authority can't be resolved (airuleset.py missing/broken)
        # the guard must NOT silently allow the close. Simulate by copying the hook into
        # a temp tree with NO airuleset.py at its REPO_DIR → `python3 .../airuleset.py`
        # errors → AUTH empty → BLOCK (fail-safe), not exit 0.
        import shutil
        d = tempfile.mkdtemp()
        (Path(d) / "hooks").mkdir()
        fake = Path(d) / "hooks" / "block-fork-no-merge-issue-close.sh"
        shutil.copy(str(HOOK), str(fake))          # REPO_DIR=d, but d/airuleset.py absent
        payload = json.dumps({"tool_input": {"command": "gh issue close 1408"}})
        r = subprocess.run(["bash", str(fake)], input=payload,
                           capture_output=True, text=True, cwd=self.full)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fail-safe", r.stderr)


if __name__ == "__main__":
    main()
