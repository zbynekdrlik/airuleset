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
    """#942: _bounce_round no longer depends on self_login at all — it counts
    prio:bounce label events, not own RFR comments.  The stray-directory
    identity mismatch (#918) is structurally irrelevant to the bounce round
    now, but the round must still work correctly regardless of self_login."""

    def _fake_runner(self, events, labels=None):
        obj_labels = {"labels": [{"name": lb} for lb in (labels or [])]}
        def runner(*args, **kwargs):
            if args and args[0] == "api":
                return json.dumps(events)
            return json.dumps(obj_labels)
        return runner

    def test_round_independent_of_self_login(self):
        """Bounce round counts prio:bounce events, not own RFR comments,
        so self_login identity mismatch cannot affect the round (#942)."""
        events = [
            {"event": "labeled", "label": {"name": "prio:bounce"}},
            {"event": "unlabeled", "label": {"name": "prio:bounce"}},
            {"event": "labeled", "label": {"name": "prio:bounce"}},
        ]
        r = self._fake_runner(events, labels=["prio:bounce"])
        # Both logins produce the same round — events-based, not RFR.
        rnd_app = cli_quals._bounce_round(1, APP_BOT, runner=r,
                                           repo="o/n")
        rnd_pat = cli_quals._bounce_round(1, PAT_LOGIN, runner=r,
                                           repo="o/n")
        self.assertEqual(rnd_app, rnd_pat,
                         "Round must be identical regardless of self_login")
        self.assertEqual(3, rnd_app,
                         "Two prio:bounce label-adds -> round 3")


if __name__ == "__main__":
    unittest.main()
