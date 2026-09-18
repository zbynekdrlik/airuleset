"""#1070 item 1 -- gates.ghread: a REST-first GitHub issue/comment reader with a
GraphQL fallback, budget-aware.

The owner identity's GraphQL 5000/h budget is exhausted hourly; a gate that reads
design presence through `gh issue view` (GraphQL) then wrongly sees "no design"
and hard-blocks a worker whose main HAD posted the design. REST draws on a
SEPARATE budget and keeps working. This module reads REST-first, falls back to
GraphQL, and -- only when BOTH paths fail (quota/transport) -- reports an
explicit `gate-unavailable:<reason>` (never "missing design").

Hermetic: a fake gh runner returns (rc, stdout, stderr) per argv, so no network
and no real gh.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import ghread


def _rl_body():
    # a realistic gh GraphQL rate-limit error body
    return ("GraphQL: API rate limit exceeded for user ID 123. "
            "(rateLimit) [RATE_LIMITED]")


def _comments_ndjson(*bodies):
    return "\n".join(json.dumps({"id": i, "body": b})
                     for i, b in enumerate(bodies, 1))


class ReadComments(unittest.TestCase):
    def test_rest_success_parses_bodies(self):
        def runner(argv):
            if argv[:2] == ["gh", "api"] and "comments" in argv[2]:
                return 0, _comments_ndjson("first", "second"), ""
            raise AssertionError("GraphQL fallback should not run on REST success")
        bodies, err = ghread.read_comment_bodies(5, "o/r", runner=runner)
        self.assertIsNone(err)
        self.assertEqual(bodies, ["first", "second"])

    def test_rest_quota_then_graphql_success_falls_back(self):
        def runner(argv):
            if argv[:2] == ["gh", "api"] and "comments" in argv[2]:
                return 1, "", _rl_body()  # REST hit the (shared) limit
            if argv[:3] == ["gh", "issue", "view"]:
                return 0, json.dumps({"comments": [{"body": "gql body"}]}), ""
            raise AssertionError("unexpected argv %r" % argv)
        bodies, err = ghread.read_comment_bodies(5, "o/r", runner=runner)
        self.assertIsNone(err)
        self.assertEqual(bodies, ["gql body"])

    def test_both_paths_quota_yields_gate_unavailable_never_missing_design(self):
        def runner(argv):
            return 1, "", _rl_body()  # both REST and GraphQL exhausted
        bodies, err = ghread.read_comment_bodies(5, "o/r", runner=runner)
        self.assertIsNone(bodies)
        self.assertIsNotNone(err)
        self.assertTrue(err.startswith("gate-unavailable:"), err)
        self.assertIn("rate limit", err.lower())
        # the design's hard invariant: never the words "missing design"
        self.assertNotIn("missing design", err.lower())

    def test_both_paths_transport_error_yields_gate_unavailable(self):
        def runner(argv):
            return 127, "", "gh: command not found"
        bodies, err = ghread.read_comment_bodies(5, "o/r", runner=runner)
        self.assertIsNone(bodies)
        self.assertTrue(err.startswith("gate-unavailable:"), err)

    def test_graphql_rc0_but_unparseable_is_gate_unavailable(self):
        # a stub / broken gh that returns rc0 garbage on the GraphQL path must
        # not be trusted as "no comments" -- both paths effectively failed.
        def runner(argv):
            if argv[:2] == ["gh", "api"]:
                return 1, "", "boom"
            return 0, "OPEN", ""  # not JSON
        bodies, err = ghread.read_comment_bodies(5, "o/r", runner=runner)
        self.assertIsNone(bodies)
        self.assertTrue(err.startswith("gate-unavailable:"), err)


class ReadIssuePrVsIssue(unittest.TestCase):
    def test_pull_request_key_marks_a_pr(self):
        def runner(argv):
            return 0, json.dumps({"number": 201,
                                  "pull_request": {"url": "x"}}), ""
        is_pr, err = ghread.is_pull_request(201, "o/r", runner=runner)
        self.assertIsNone(err)
        self.assertTrue(is_pr)

    def test_plain_issue_is_not_a_pr(self):
        def runner(argv):
            return 0, json.dumps({"number": 203, "title": "a real issue"}), ""
        is_pr, err = ghread.is_pull_request(203, "o/r", runner=runner)
        self.assertIsNone(err)
        self.assertFalse(is_pr)

    def test_issue_read_quota_is_gate_unavailable_unknown(self):
        def runner(argv):
            return 1, "", _rl_body()
        is_pr, err = ghread.is_pull_request(203, "o/r", runner=runner)
        self.assertIsNone(is_pr)  # unknown
        self.assertTrue(err.startswith("gate-unavailable:"), err)


class ResolveSlug(unittest.TestCase):
    def test_ssh_remote(self):
        def runner(argv):
            return 0, "git@github.com:owner/name.git", ""
        self.assertEqual(ghread.resolve_slug("/repo", runner=runner), "owner/name")

    def test_https_remote(self):
        def runner(argv):
            return 0, "https://github.com/owner/name", ""
        self.assertEqual(ghread.resolve_slug("/repo", runner=runner), "owner/name")

    def test_no_origin_is_none(self):
        def runner(argv):
            return 1, "", "no such remote 'origin'"
        self.assertIsNone(ghread.resolve_slug("/repo", runner=runner))


if __name__ == "__main__":
    unittest.main()
