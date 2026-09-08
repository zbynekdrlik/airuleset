"""#904: App-token streams' own comments are not recognized as "own".

Root cause: `_issue_comment_ages` and `_bounce_round` compare
comment `author.login` with `self_login` via strict equality.
On App-token boxes, `_stream_self_login()` returns
`"app/odoo-erp-stream-tokens"` (STREAM_APP_BOT_LOGIN), but
GitHub's `gh issue view --json comments` renders the author.login
as the bare slug `"odoo-erp-stream-tokens"`.

The strict `==` always fails -> own comments are invisible ->
W members falsely tagged stale!, own_cited/own_target/own_rfr
never set.
"""

import json
import unittest
from unittest import mock

import airuleset
import cli_quals


# The constant from airuleset.py -- the "app/" prefixed form.
APP_PREFIXED = "app/odoo-erp-stream-tokens"
# What GitHub actually returns as author.login for App comments.
APP_BARE = "odoo-erp-stream-tokens"


class TestCommentAgesAppToken(unittest.TestCase):
    """_issue_comment_ages must recognize bare-slug App comments as own."""

    def _make_gh_out(self, comments):
        """Return a mock _gh_out that returns canned comments JSON."""
        obj = {"comments": comments}
        return lambda *a, **kw: json.dumps(obj)

    def test_bare_slug_comment_recognized_as_own(self):
        """A comment by the bare App slug must count as 'own' when
        self_login is the app/-prefixed form."""
        ts = "2025-06-01T12:00:00Z"
        comments = [{"author": {"login": APP_BARE},
                      "createdAt": ts, "body": "push evidence"}]
        with mock.patch.object(airuleset, "_gh_out",
                               self._make_gh_out(comments)):
            res = cli_quals._issue_comment_ages(
                41, APP_PREFIXED, 0, cwd=None)
        self.assertIsNotNone(res)
        self.assertIsNotNone(res.get("own"),
                             "Bare-slug App comment must be recognized as own")

    def test_prefixed_comment_recognized_when_self_login_bare(self):
        """The reverse: self_login is bare, comment author is app/-prefixed."""
        ts = "2025-06-01T12:00:00Z"
        comments = [{"author": {"login": APP_PREFIXED},
                      "createdAt": ts, "body": "push evidence"}]
        with mock.patch.object(airuleset, "_gh_out",
                               self._make_gh_out(comments)):
            res = cli_quals._issue_comment_ages(
                41, APP_BARE, 0, cwd=None)
        self.assertIsNotNone(res)
        self.assertIsNotNone(res.get("own"),
                             "app/-prefixed comment must be recognized as own"
                             " when self_login is the bare slug")

    def test_unrelated_login_not_matched(self):
        """A comment by a truly different login must NOT be treated as own."""
        ts = "2025-06-01T12:00:00Z"
        comments = [{"author": {"login": "some-other-user"},
                      "createdAt": ts, "body": "foreign comment"}]
        with mock.patch.object(airuleset, "_gh_out",
                               self._make_gh_out(comments)):
            res = cli_quals._issue_comment_ages(
                41, APP_PREFIXED, 0, cwd=None)
        self.assertIsNotNone(res)
        self.assertIsNone(res.get("own"),
                          "A foreign login must not be treated as own")

    def test_exact_match_still_works(self):
        """When both sides are identical, the match must still work."""
        ts = "2025-06-01T12:00:00Z"
        comments = [{"author": {"login": APP_PREFIXED},
                      "createdAt": ts, "body": "exact match"}]
        with mock.patch.object(airuleset, "_gh_out",
                               self._make_gh_out(comments)):
            res = cli_quals._issue_comment_ages(
                41, APP_PREFIXED, 0, cwd=None)
        self.assertIsNotNone(res)
        self.assertIsNotNone(res.get("own"),
                             "Exact login match must still work")


class TestBounceRoundAppToken(unittest.TestCase):
    """_bounce_round derives from prio:bounce events, not RFR comments (#942).

    The App-token login normalization (#904) is no longer relevant to
    _bounce_round (it counts label events, not own comments), but the
    round must still work correctly on an App-token box."""

    def _fake_runner(self, events, labels=None):
        obj_labels = {"labels": [{"name": lb} for lb in (labels or [])]}
        def runner(*args, **kwargs):
            if args and args[0] == "api":
                return json.dumps(events)
            return json.dumps(obj_labels)
        return runner

    def test_bounce_round_works_regardless_of_login(self):
        """Bounce round counts prio:bounce events, not RFR comments,
        so App-token login shape is irrelevant (#942)."""
        r = self._fake_runner([
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ], labels=["prio:bounce"])
        rnd = cli_quals._bounce_round(1, APP_PREFIXED, runner=r,
                                       repo="o/n")
        self.assertEqual(2, rnd,
                         "One bounce event -> round 2")


if __name__ == "__main__":
    unittest.main()
