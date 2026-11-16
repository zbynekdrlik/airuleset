"""#1288 / #1291: several lanes per ticket. A gk BOUNCE belongs to the lane whose
READY-FOR-REVIEW it reviewed. Without a `Branch:` line in the bounce, that lane
is read from the RFR the bounce answers (its `HEAD:` matching the bounce
`@sha`, else the newest RFR before the bounce).

#1288: another lane's bounce must not block a fresh lane's RFR.
#1291: a lane's RFR must not clear `prio:bounce` of another lane's open bounce.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_gk_watch  # noqa: E402

U16 = "david3/9258-u16-multi-tab-stores"
U18 = "david3/9258-u18-fa-invoice-setup"


def _rfr(cid, ts, branch, head):
    return {"id": cid, "login": "stream", "created_at": ts,
            "body": "READY-FOR-REVIEW\nBranch: david3:%s\nHEAD: %s\n" % (
                branch, head)}


def _bounce(cid, ts, sha, extra=""):
    return {"id": cid, "login": "gk", "created_at": ts,
            "body": "## Gatekeeper review verdict: BOUNCE @%s\n%s"
                    "- F1: broken\n" % (sha, extra)}


def _state(rows):
    return cli_gk_watch.watch_issue(9258, fetch=lambda i: rows, gk_login="gk",
                                    self_login="stream", now=1.9e9)


def _pf(st, branch, body="READY-FOR-REVIEW\n"):
    return airuleset._handoff_gk_preflight(
        9258, "o/r", body, branch=branch, watch_result=st,
        commits_since=lambda b, ts, cwd: 0, journal=lambda **kw: None)


class TestLaneInference(unittest.TestCase):

    def test_bounce_lane_from_rfr_head_sha(self):
        rows = [_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                _rfr(2, "2026-11-03T11:00:00Z", U18, "1cb428522cbb"),
                _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6")]
        st = _state(rows)
        self.assertEqual(st["state"], "bounce-unanswered")
        self.assertEqual(st["gk_latest"]["lane_branch"], U16)

    def test_bounce_lane_falls_back_to_newest_prior_rfr(self):
        rows = [_rfr(1, "2026-11-03T10:00:00Z", U16, "aaaaaaa1111"),
                _bounce(3, "2026-11-03T12:00:00Z", "bbbbbbb2222")]
        self.assertEqual(_state(rows)["gk_latest"]["lane_branch"], U16)

    def test_bounce_branch_line_wins_and_is_normalised(self):
        rows = [_rfr(1, "2026-11-03T10:00:00Z", U16, "aaaaaaa1111"),
                _bounce(3, "2026-11-03T12:00:00Z", "aaaaaaa1111",
                        extra="Branch: `david3:%s`\n" % U18)]
        self.assertEqual(_state(rows)["gk_latest"]["lane_branch"], U18)

    def test_no_rfr_no_branch_lane_unknown(self):
        rows = [_bounce(3, "2026-11-03T12:00:00Z", "aaaaaaa1111")]
        self.assertIsNone(_state(rows)["gk_latest"]["lane_branch"])


class TestPreflightLaneScope1288(unittest.TestCase):

    def setUp(self):
        self.st = _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                          _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6")])

    def test_other_lane_bounce_does_not_block_fresh_lane(self):
        self.assertIsNone(_pf(self.st, U18))

    def test_bounced_lane_itself_still_blocks(self):
        blk = _pf(self.st, U16)
        self.assertIsNotNone(blk)
        self.assertIn("no new commit since BOUNCE", blk)

    def test_fork_shorthand_readiness_branch_still_matches(self):
        self.assertIsNotNone(_pf(self.st, "david3:" + U16))


class TestClearBounce1291(unittest.TestCase):

    def setUp(self):
        self.st = _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                          _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6")])

    def _clear(self, branch, body):
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("subprocess.run", return_value=ok) as run:
            cleared = airuleset._handoff_clear_bounce(
                "o/r", 9258, self.st, branch=branch, body=body)
        return cleared, run.call_count

    def test_other_lane_rfr_keeps_the_label(self):
        self.assertEqual(self._clear(U18, "READY-FOR-REVIEW\n"), (False, 0))

    def test_bounced_lane_rfr_clears(self):
        self.assertEqual(self._clear(U16, "READY-FOR-REVIEW\n"), (True, 1))

    def test_rfr_naming_the_bounce_id_clears(self):
        unknown = _state([_bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6")])
        self.st = unknown
        self.assertEqual(self._clear(U18, "READY-FOR-REVIEW\n"), (False, 0))
        body = "READY-FOR-REVIEW\nRoot-cause-of-previous-bounce: 3 lens\n"
        self.assertEqual(self._clear(U18, body), (True, 1))

    def test_non_bounce_state_never_clears(self):
        self.st = {"state": "rfr-current", "gk_latest": {}}
        self.assertEqual(self._clear(U16, "x"), (False, 0))


if __name__ == "__main__":
    unittest.main()
