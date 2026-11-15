"""#1331: a superseded BOUNCE must not read as current on a follow-up lane.

Live (odoo-erp issue 10100, 15.11.2026): `handoff --body-file` refused with
"no new commit since BOUNCE 6865212484" after gk had ACCEPTed the ticket twice.
Replaying the real thread through `gk_watch_issue` gave `bounce-unanswered`
with `rfr: None`. Two causes, locked here:

1. the REST comment reader gives an App's login as `<slug>[bot]`, while the
   stream's own identity is `app/<slug>`; `_is_own_login` stripped only the
   `app/` prefix, so no own READY-FOR-REVIEW was ever recognised;
2. a later gk ACCEPT never superseded an older BOUNCE.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_gk_watch as gw  # noqa: E402
from cli_quals import _is_own_login  # noqa: E402

GK = "zbynekdrlik"
SELF = "app/odoo-erp-stream-tokens-2"
REST_BOT = "odoo-erp-stream-tokens-2[bot]"

BOUNCE = ("## Gatekeeper review verdict: BOUNCE\ngk-state: BOUNCE @2d3781fd76\n"
          "- 🟡1 — correctness: off-by-one\n")
ACCEPT = "## Gatekeeper review verdict: ACCEPT\ngk-state: ACCEPT @8811ffc059\n"
ACCEPT_WITH_FINDING = ("## Gatekeeper review verdict: ACCEPT\n"
                       "gk-state: ACCEPT @139c052019\n- 🔵2 — open runbook check\n")
RFR = "READY-FOR-REVIEW — #10100 round 2\nCloses-finding: 1 — fixed\n"


def _row(cid, login, body, ts):
    return {"id": cid, "login": login, "body": body, "created_at": ts}


def _watch(rows, matcher=_is_own_login):
    return gw.watch_issue(10100, fetch=lambda _i: rows, gk_login=GK,
                          self_login=SELF, now=2e9, is_own_login=matcher)


class OwnLoginBotSuffix(unittest.TestCase):

    def test_rest_bot_login_is_own(self):
        self.assertTrue(_is_own_login(REST_BOT, SELF))
        self.assertTrue(_is_own_login("odoo-erp-stream-tokens[bot]",
                                      "app/odoo-erp-stream-tokens"))

    def test_other_bot_is_not_own(self):
        self.assertFalse(_is_own_login("odoo-erp-stream-tokens[bot]", SELF))
        self.assertFalse(_is_own_login("github-actions[bot]", SELF))

    def test_existing_forms_still_match(self):
        self.assertTrue(_is_own_login("odoo-erp-stream-tokens-2", SELF))
        self.assertTrue(_is_own_login(SELF, SELF))
        self.assertFalse(_is_own_login("someone", SELF))

    def test_rest_shaped_rfr_answers_the_bounce(self):
        rows = [_row(1, GK, BOUNCE, "2026-11-13T21:21:21Z"),
                _row(2, REST_BOT, RFR, "2026-11-13T23:25:22Z")]
        st = _watch(rows)
        self.assertNotEqual(st["state"], "bounce-unanswered")
        self.assertIsNotNone(st["rfr"])


class AcceptSupersedesBounce(unittest.TestCase):
    """Exact-match matcher: the RFRs are NOT recognised (a different poster),
    so only the ACCEPT supersession can retire the BOUNCE."""

    def _exact(self, rows):
        return _watch(rows, matcher=None)

    def test_later_accept_retires_the_bounce(self):
        rows = [_row(1, GK, BOUNCE, "2026-11-13T21:21:21Z"),
                _row(2, GK, ACCEPT, "2026-11-13T23:37:56Z")]
        st = self._exact(rows)
        self.assertNotEqual(st["state"], "bounce-unanswered")
        self.assertNotIn("1", st["undispositioned_ids"])

    def test_findings_of_the_accept_itself_still_need_a_disposition(self):
        rows = [_row(1, GK, BOUNCE, "2026-11-13T21:21:21Z"),
                _row(2, GK, ACCEPT_WITH_FINDING, "2026-11-15T06:55:55Z")]
        st = self._exact(rows)
        self.assertEqual(st["state"], "needs-disposition")
        self.assertEqual(st["undispositioned_ids"], ["2"])

    def test_a_bounce_newer_than_the_accept_is_current(self):
        rows = [_row(1, GK, ACCEPT, "2026-11-13T23:37:56Z"),
                _row(2, GK, BOUNCE, "2026-11-14T10:00:00Z")]
        st = self._exact(rows)
        self.assertEqual(st["state"], "bounce-unanswered")
        self.assertEqual(st["gk_latest"]["id"], 2)


class ReviewFindings1331(unittest.TestCase):
    """Shapes both fresh-context reviews reproduced against the first cut."""

    def test_a_prose_accept_never_supersedes_a_bounce(self):
        for note in ("Pre ACCEPT treba ešte opraviť 🟡1 (off-by-one).",
                     "I can't ACCEPT this until CI is green."):
            rows = [_row(1, GK, BOUNCE, "2026-11-13T21:21:21Z"),
                    _row(2, GK, note, "2026-11-13T22:00:00Z")]
            st = _watch(rows, matcher=None)
            self.assertEqual(st["state"], "bounce-unanswered", note)
            self.assertIn("1", st["undispositioned_ids"])

    def test_a_shared_identity_stream_mention_never_supersedes(self):
        rows = [_row(1, GK, BOUNCE, "2026-11-13T21:21:21Z"),
                _row(2, GK, "Working on it. Will re-request once gk can ACCEPT.",
                     "2026-11-13T22:00:00Z")]
        st = gw.watch_issue(10100, fetch=lambda _i: rows, gk_login=GK,
                            self_login=GK, now=2e9)
        self.assertEqual(st["state"], "bounce-unanswered")

    def test_the_live_heading_shape_supersedes(self):
        """The real odoo-erp verdict carries the heading, not the marker."""
        rows = [_row(1, GK, BOUNCE, "2026-11-13T21:21:21Z"),
                _row(2, GK, "## Gatekeeper review verdict: ACCEPT\n\nLGTM",
                     "2026-11-13T23:37:56Z")]
        self.assertNotEqual(_watch(rows, matcher=None)["state"], "bounce-unanswered")

    def test_bot_suffix_never_matches_a_bare_login(self):
        self.assertFalse(_is_own_login("my-app", "my-app[bot]"))
        self.assertFalse(_is_own_login("my-app[bot]", "my-app"))
        self.assertFalse(_is_own_login("[bot]", "app/"))
        self.assertTrue(_is_own_login("my-app[bot]", "my-app[bot]"))


if __name__ == "__main__":
    unittest.main()
