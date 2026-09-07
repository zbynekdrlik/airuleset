"""#918: _stream_self_login() returns wrong identity on a PAT box with a
stray App-token directory.

Root cause: _stream_self_login() checks _is_gh_app_token_box() (directory
existence only), and when True returns STREAM_APP_BOT_LOGIN unconditionally
— even when the active gh auth is a PAT (kvaskodev), not an App token.
This makes _bounce_round() unable to match own prior RFR comments
(authored by the PAT login), producing round=1 instead of the true round.

The fix validates the App-token-box detection: if _gh_login() returns a
real login (not None), the box is operating as a PAT box and the stray
directory should be ignored.
"""

import json
import unittest
from unittest import mock

import airuleset
import cli_quals


PAT_LOGIN = "kvaskodev"
APP_BOT = "app/odoo-erp-stream-tokens"


class TestStrayAppTokenDir(unittest.TestCase):
    """_stream_self_login must return the PAT login, not the App bot login,
    when the App-token directory exists but the active auth is a PAT."""

    def test_pat_box_with_stray_dir_returns_pat_login(self):
        """On a PAT box where _is_gh_app_token_box() is True (stray dir),
        _stream_self_login() must return the real PAT login, not
        STREAM_APP_BOT_LOGIN."""
        with mock.patch.object(cli_quals, "_is_gh_app_token_box",
                               return_value=True), \
             mock.patch.object(airuleset, "_gh_login",
                               return_value=PAT_LOGIN):
            result = cli_quals._stream_self_login()
        self.assertEqual(PAT_LOGIN, result,
                         "_stream_self_login must prefer the real PAT login "
                         "over the App bot login when the active auth is a PAT")

    def test_genuine_app_token_box_returns_bot_login(self):
        """On a genuine App-token box (_gh_login returns None because
        gh api user 403s), _stream_self_login must still return
        STREAM_APP_BOT_LOGIN."""
        with mock.patch.object(cli_quals, "_is_gh_app_token_box",
                               return_value=True), \
             mock.patch.object(airuleset, "_gh_login",
                               return_value=None):
            result = cli_quals._stream_self_login()
        self.assertEqual(APP_BOT, result,
                         "Genuine App-token box must still return "
                         "STREAM_APP_BOT_LOGIN")

    def test_no_app_dir_returns_gh_login(self):
        """When _is_gh_app_token_box() is False, _stream_self_login must
        return _gh_login() as before (the unchanged baseline)."""
        with mock.patch.object(cli_quals, "_is_gh_app_token_box",
                               return_value=False), \
             mock.patch.object(airuleset, "_gh_login",
                               return_value=PAT_LOGIN):
            result = cli_quals._stream_self_login()
        self.assertEqual(PAT_LOGIN, result)


class TestBounceRoundStrayDir(unittest.TestCase):
    """_bounce_round with the wrong self_login produces round=1 instead of
    the correct round — the gate-visible symptom of #918."""

    def _fake_runner(self, comments, labels=None):
        obj = {"comments": comments,
               "labels": [{"name": lb} for lb in (labels or [])]}
        def runner(*args, **kwargs):
            return json.dumps(obj)
        return runner

    def test_pat_comments_invisible_with_app_bot_login(self):
        """When self_login is STREAM_APP_BOT_LOGIN but comments were
        authored by the PAT login, _bounce_round returns 1 (wrong).
        This is the RED test — it documents the bug, not the fix."""
        r = self._fake_runner([
            {"author": {"login": PAT_LOGIN},
             "body": "READY-FOR-REVIEW: branch a head abc"},
            {"author": {"login": PAT_LOGIN},
             "body": "READY-FOR-REVIEW: branch b head def"},
            {"author": {"login": PAT_LOGIN},
             "body": "READY-FOR-REVIEW: branch c head ghi"},
        ], labels=["prio:bounce"])
        # With the WRONG self_login (App bot), none match -> round 2
        # (floored from 1 by prio:bounce).
        rnd_wrong = cli_quals._bounce_round(1, APP_BOT, runner=r)
        self.assertEqual(2, rnd_wrong,
                         "Bug confirmation: App bot login misses PAT comments")
        # With the CORRECT self_login (PAT), all 3 match -> round 4.
        rnd_correct = cli_quals._bounce_round(1, PAT_LOGIN, runner=r)
        self.assertEqual(4, rnd_correct,
                         "Correct PAT login must find all 3 RFR comments")


if __name__ == "__main__":
    unittest.main()
