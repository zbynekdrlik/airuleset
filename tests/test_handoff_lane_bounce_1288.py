"""#1288 / #1291: several lanes per ticket. A gk BOUNCE belongs to the lane whose
READY-FOR-REVIEW it reviewed. Without a `Branch:` line in the bounce, that lane
is read from the RFR the bounce answers (its `HEAD:` matching the bounce
`@sha`, else the single lane of all earlier RFRs; several lanes = unknown).

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

    def test_bounce_lane_falls_back_to_single_prior_lane(self):
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

    def test_two_lanes_and_no_sha_match_is_unknown_not_guessed(self):
        rows = [_rfr(1, "2026-11-03T10:00:00Z", U16, "aaaaaaa1111"),
                _rfr(2, "2026-11-03T11:00:00Z", U18, "ccccccc3333"),
                _bounce(3, "2026-11-03T12:00:00Z", "bbbbbbb2222")]
        st = _state(rows)
        self.assertIsNone(st["gk_latest"]["lane_branch"])
        # unknown lane keeps the ticket-level guard: no new commit -> block
        self.assertIsNotNone(_pf(st, U18))


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


class TestReviewFixes(unittest.TestCase):
    """Review round 1 of the #1288/#1291 lane fix."""

    def test_other_lane_rfr_does_not_hide_an_open_bounce(self):
        # RFR(A) -> BOUNCE@A -> RFR(B): lane A's bounce is still unanswered
        st = _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                     _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6"),
                     _rfr(4, "2026-11-03T13:00:00Z", U18, "1cb428522cbb")])
        self.assertEqual(st["state"], "bounce-unanswered")
        self.assertEqual(st["gk_latest"]["id"], 3)
        self.assertEqual(st["gk_latest"]["lane_branch"], U16)

    def test_same_lane_rfr_answers_its_bounce(self):
        st = _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                     _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6"),
                     _rfr(4, "2026-11-03T13:00:00Z", U16, "9999999aaaa")])
        self.assertNotEqual(st["state"], "bounce-unanswered")

    def test_branchless_rfr_still_answers_any_bounce(self):
        raw = {"id": 4, "login": "stream", "created_at": "2026-11-03T13:00:00Z",
               "body": "READY-FOR-REVIEW\nno branch line\n"}
        st = _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                     _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6"), raw])
        self.assertNotEqual(st["state"], "bounce-unanswered")

    def _two_open(self):
        return _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "aaaaaaa1111"),
                       _rfr(2, "2026-11-03T10:30:00Z", U18, "ccccccc3333"),
                       _bounce(3, "2026-11-03T12:00:00Z", "aaaaaaa1111"),
                       _bounce(5, "2026-11-03T13:00:00Z", "ccccccc3333")])

    def test_preflight_checks_the_readiness_lanes_own_bounce(self):
        blk = _pf(self._two_open(), U16)
        self.assertIsNotNone(blk)
        self.assertIn("BOUNCE 3 ", blk)

    def test_no_clear_while_another_lanes_bounce_stays_open(self):
        ok = subprocess.CompletedProcess([], 0, "", "")
        for lane in (U16, U18):
            with mock.patch("subprocess.run", return_value=ok) as run:
                cleared = airuleset._handoff_clear_bounce(
                    "o/r", 9258, self._two_open(), branch=lane,
                    body="READY-FOR-REVIEW\n")
            self.assertEqual((cleared, run.call_count), (False, 0), lane)

    def test_uppercase_head_matches_the_bounce_sha(self):
        st = _state([_rfr(1, "2026-11-03T10:00:00Z", U16, "43E3AF87D6AA"),
                     _rfr(2, "2026-11-03T11:00:00Z", U18, "1cb428522cbb"),
                     _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6")])
        self.assertEqual(st["gk_latest"]["lane_branch"], U16)

    def test_legacy_f_ids_in_list_table_heading_and_emoji_forms(self):
        for line, fid in (("- 🔴 F1: x", "F1"), ("**🟡 F6** y", "F6"),
                          ("1. F2 broken", "F2"), ("| F3 | bad |", "F3"),
                          ("### F4 heading", "F4"), ("- [ ] F8 todo", "F8")):
            self.assertEqual(airuleset._parse_gk_findings(line), [fid], line)
        self.assertEqual(airuleset._parse_gk_findings("x (#5244 F1) y"), [])
        self.assertEqual(airuleset._parse_gk_findings("| lens | F1 |"), [])


class TestReviewRound2(unittest.TestCase):
    """Review round 2: one meaning of "answered", re-cut lanes, regex edges."""

    def _recut(self, cite):
        body = "READY-FOR-REVIEW\nBranch: david3:%s\nHEAD: 1cb428522cbb\n" % U18
        if cite:
            body += "Root-cause-of-previous-bounce: answers bounce 3\n"
        return [_rfr(1, "2026-11-03T10:00:00Z", U16, "43e3af87d6aa"),
                _bounce(3, "2026-11-03T12:00:00Z", "43e3af87d6"),
                {"id": 4, "login": "stream", "created_at": "2026-11-03T13:00:00Z",
                 "body": body}]

    def test_recut_lane_rfr_naming_the_bounce_answers_it(self):
        self.assertNotEqual(_state(self._recut(True))["state"],
                            "bounce-unanswered")
        self.assertEqual(_state(self._recut(False))["state"], "bounce-unanswered")

    def test_recut_rfr_naming_the_bounce_must_disposition_its_findings(self):
        st = _state(self._recut(False)[:2])
        body = "READY-FOR-REVIEW\nanswers bounce 3\n"
        blk = airuleset._handoff_gk_preflight(
            9258, "o/r", body, branch=U18, watch_result=st,
            commits_since=lambda b, ts, cwd: 2, journal=lambda **kw: None)
        self.assertIsNotNone(blk)
        self.assertIn("needs-disposition F1", blk)

    def test_unknown_lane_open_bounce_is_checked_for_any_lane(self):
        rows = [_rfr(1, "2026-11-03T10:00:00Z", U16, "aaaaaaa1111"),
                _rfr(2, "2026-11-03T10:30:00Z", U18, "ccccccc3333"),
                _bounce(3, "2026-11-03T12:00:00Z", "9999999abcd"),
                _bounce(5, "2026-11-03T13:00:00Z", "ccccccc3333")]
        st = _state(rows)
        self.assertEqual([b["id"] for b in st["open_bounces"]], [5, 3])
        blk = _pf(st, U16)
        self.assertIsNotNone(blk)
        self.assertIn("BOUNCE 3 ", blk)

    def test_regex_round2_edges(self):
        p = airuleset._parse_gk_findings
        self.assertEqual(p("> F2 fixed"), [])
        self.assertEqual(p("2026. F12 was the year"), [])
        self.assertEqual(p("- 🔴 **3** bad"), ["3"])
        self.assertEqual(p("12. F2 broken"), ["F2"])

    def test_regex_is_linear_on_long_blank_runs(self):
        import time
        t0 = time.monotonic()
        airuleset._parse_gk_findings(" " * 20000 + "x\n" + "\t" * 20000 + "y")
        self.assertLess(time.monotonic() - t0, 1.0)


class TestReviewRound3(unittest.TestCase):

    def test_named_bounce_of_a_third_lane_is_the_one_checked(self):
        u20 = "david3/9258-u20-other"
        rows = [_rfr(1, "2026-11-03T10:00:00Z", U16, "aaaaaaa1111"),
                _rfr(2, "2026-11-03T10:30:00Z", u20, "ccccccc3333"),
                _bounce(3, "2026-11-03T12:00:00Z", "aaaaaaa1111"),
                _bounce(5, "2026-11-03T13:00:00Z", "ccccccc3333")]
        body = "READY-FOR-REVIEW\nre-cut, answers bounce 3\n"
        blk = airuleset._handoff_gk_preflight(
            9258, "o/r", body, branch=U18, watch_result=_state(rows),
            commits_since=lambda b, ts, cwd: 2, journal=lambda **kw: None)
        self.assertIsNotNone(blk)
        self.assertIn("needs-disposition F1", blk)
        self.assertIn(" 3 ", blk)

    def test_regex_is_linear_on_long_star_runs(self):
        import time
        t0 = time.monotonic()
        airuleset._parse_gk_findings("*" * 20000 + "x")
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(airuleset._parse_gk_findings("* F9 bullet"), ["F9"])


if __name__ == "__main__":
    unittest.main()
