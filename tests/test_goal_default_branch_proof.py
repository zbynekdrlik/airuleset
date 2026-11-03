"""The full /goal (B) proof reads CI on the repo's DEFAULT branch.

dsync (2026-11-03): its default branch is `master`, so the hard-coded
`gh run list -b main` printed nothing and (B) could never hold on a truly
empty backlog. The proof must resolve the branch from the repo itself.
"""
import os
import sys
from unittest import TestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import goal_registry  # noqa: E402

RESOLVE = "gh repo view --json defaultBranchRef --jq .defaultBranchRef.name"


class TestDefaultBranchProof(TestCase):
    def test_full_proof_never_hard_codes_main(self):
        line = goal_registry.render_goal_line("full")
        self.assertNotIn("gh run list -b main", line)

    def test_full_proof_resolves_the_default_branch(self):
        line = goal_registry.render_goal_line("full")
        self.assertIn('gh run list -b "$(%s)" -L 1' % RESOLVE, line)

    def test_every_full_role_variant_carries_it(self):
        for role in ("infra", "quality", "review"):
            with self.subTest(role=role):
                line = goal_registry.render_goal_line("full", role)
                self.assertNotIn("gh run list -b main", line)
