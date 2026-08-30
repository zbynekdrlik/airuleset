"""#753 — a W-push resets `stale!` ONLY with a source citation.

Root cause #1: `cli_quals._issue_comment_ages` inspected only comment
`createdAt`/`author`, never the BODY, so ANY own comment (a bare "čakáme")
reset the 24h `stale!` clock — the montalu3 W-34 degeneration.

These tests lock the citation-narrowed reset:
  1. `_comment_has_citation` recognizes a version / Discuss thread / msg-id /
     `#N` ref and rejects a content-free "still waiting" comment;
  2. `_issue_comment_ages` returns the extensible DICT
     `{own, any, own_cited, own_oldest}` computed from comment bodies;
  3. `_stale_ops_wait_flagged` anchors freshness on the newest CITED own push
     (montalu3 sustained-bare-push case → stale; fresh cited push → not stale;
     fail-safe on a freshly-parked single bare push), and still accepts a legacy
     2-tuple for the untouched #699/#607 fakes.
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_quals

DAY = 24 * 3600


def _rows(*nums):
    return {n: {"number": n, "title": "t%d" % n,
                "labels": [{"name": "ops-wait"}]} for n in nums}


def _ages(own=None, any_ts=None, own_cited=None, own_oldest=None):
    return {"own": own, "any": any_ts,
            "own_cited": own_cited, "own_oldest": own_oldest}


class Citation(unittest.TestCase):
    def test_recognizes_citation_forms(self):
        for body in (
            "blocker re-overený vs PROD 2.226.0, stále nenasadené",
            "re-overené proti verzii v0.1.99",
            "klient neodpovedal vo vlákne discuss.channel_275",
            "pripomienka poslaná, msg 1723308",
            "blocker stále platí — pozri #4980",
        ):
            self.assertTrue(cli_quals._comment_has_citation(body),
                            "should recognize a citation in %r" % body)

    def test_rejects_uncited_comment(self):
        for body in ("blocker re-overený, čakáme", "still waiting on the client",
                     "", None):
            self.assertFalse(cli_quals._comment_has_citation(body),
                             "a content-free push carries no citation: %r" % body)


class CommentAgesDict(unittest.TestCase):
    def _view(self, comments):
        import json
        payload = json.dumps({"comments": comments})
        return mock.patch.object(airuleset, "_gh_out", lambda *a, **k: payload)

    def test_returns_dict_with_citation_fields(self):
        with self._view([
            {"author": {"login": "me"}, "createdAt": "2026-08-15T10:00:00Z",
             "body": "re-overené vs PROD 2.226.0"},           # own, CITED, old
            {"author": {"login": "me"}, "createdAt": "2026-08-19T10:00:00Z",
             "body": "čakáme"},                                # own, bare, new
            {"author": {"login": "other"}, "createdAt": "2026-08-19T11:00:00Z",
             "body": "reply"},                                 # third party newest
        ]):
            res = cli_quals._issue_comment_ages(41, "me", 0, cwd=None)
        self.assertIsInstance(res, dict)
        for k in ("own", "any", "own_cited", "own_oldest"):
            self.assertIn(k, res)
        self.assertEqual(res["own"], res["own_oldest"] + 4 * DAY)  # bare newer
        self.assertLess(res["own_cited"], res["own"])              # cited is older
        self.assertGreater(res["any"], res["own"])                # 'other' newest


class StaleCitationDecider(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 19, 12,
                            tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()

    def _run(self, rows, ages):
        return cli_quals._stale_ops_wait_flagged(
            rows, now=self.now, self_login="me", ages_fn=lambda n: ages.get(n))

    def test_sustained_bare_pushes_over_24h_are_stale(self):
        # montalu3: only bare own comments, oldest >24h working, no cited push.
        got = self._run(_rows(41), {41: _ages(
            own=self.now - 3600, own_cited=None, own_oldest=self.now - 3 * DAY,
            any_ts=self.now - 3600)})
        self.assertEqual(got, {41})

    def test_fresh_cited_push_is_not_stale(self):
        got = self._run(_rows(41), {41: _ages(
            own=self.now - 3600, own_cited=self.now - 3600,
            own_oldest=self.now - 3 * DAY, any_ts=self.now - 1800)})
        self.assertEqual(got, set())

    def test_old_cited_push_is_stale(self):
        got = self._run(_rows(41), {41: _ages(
            own=self.now - 3 * DAY, own_cited=self.now - 3 * DAY,
            own_oldest=self.now - 5 * DAY, any_ts=self.now - 3 * DAY)})
        self.assertEqual(got, {41})

    def test_fresh_single_bare_push_is_not_stale_failsafe(self):
        # freshly parked, one bare comment <24h, no citation -> never accuse.
        got = self._run(_rows(41), {41: _ages(
            own=self.now - 3600, own_cited=None, own_oldest=self.now - 3600,
            any_ts=self.now - 3600)})
        self.assertEqual(got, set())

    def test_legacy_two_tuple_still_accepted(self):
        # #699/#607 fakes inject a legacy (own, any) tuple -> normalized -> the
        # any fallback still fires (no own_cited/own_oldest available).
        got = self._run(_rows(41), {41: (None, self.now - 3 * DAY)})
        self.assertEqual(got, {41})


if __name__ == "__main__":
    unittest.main()
