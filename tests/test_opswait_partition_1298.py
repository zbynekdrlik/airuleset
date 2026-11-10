"""W membership is a derived fact (#1298): a plain `ops-wait` ticket whose
newest valid `Waiting-on:` line is missing, or older than 7 days with no newer
cited own push, counts in I, not W.

Read side: `gates.opswait.unbacked_reason` (pure) over the comment ages
(`cli_quals._ages_from_comments`, new `waiting_on` key) → the fact
`wait_unbacked` read by `cli_ticket_facts.refresh` from the W comment prefetch
→ `cli_ticket_state.classify`. Unknown (comments not read) keeps W.
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import cli_quals  # noqa: E402
import cli_ticket_facts  # noqa: E402
import cli_ticket_route  # noqa: E402
import cli_ticket_state as ts  # noqa: E402
from gates import opswait  # noqa: E402
from ticket_facts_testlib import _NOW, _graphql, _row  # noqa: E402

DAY = 86400
GOOD = "Waiting-on: Hetzner support — reply on ticket 4711 — read the ticket"


def _comment(ts_iso, body, login="someone"):
    return {"createdAt": ts_iso, "author": {"login": login}, "body": body}


class Reason(unittest.TestCase):
    def test_unknown_is_none(self):
        self.assertIsNone(opswait.unbacked_reason(None, _NOW))

    def test_no_line_is_unbacked(self):
        r = opswait.unbacked_reason({"waiting_on": None}, _NOW)
        self.assertIn("no valid `Waiting-on:", r)

    def test_fresh_line_is_backed(self):
        self.assertEqual(opswait.unbacked_reason(
            {"waiting_on": _NOW - 6 * DAY}, _NOW), "")

    def test_the_seven_day_boundary(self):
        self.assertEqual(opswait.unbacked_reason(
            {"waiting_on": _NOW - 7 * DAY}, _NOW), "")
        self.assertTrue(opswait.unbacked_reason(
            {"waiting_on": _NOW - 7 * DAY - 1}, _NOW))

    def test_an_older_cited_push_does_not_age_a_fresh_line(self):
        ages = {"waiting_on": _NOW - 2 * DAY, "own_cited": _NOW - 9 * DAY}
        self.assertEqual(opswait.unbacked_reason(ages, _NOW), "")

    def test_old_line_is_unbacked_unless_a_newer_cited_push(self):
        old = {"waiting_on": _NOW - 8 * DAY, "own_cited": None}
        self.assertIn("8 days old", opswait.unbacked_reason(old, _NOW))
        pushed = dict(old, own_cited=_NOW - 2 * DAY)
        self.assertEqual(opswait.unbacked_reason(pushed, _NOW), "")
        stale_push = dict(old, own_cited=_NOW - 9 * DAY)
        self.assertTrue(opswait.unbacked_reason(stale_push, _NOW))


class Ages(unittest.TestCase):
    def test_newest_valid_line_of_any_author(self):
        ages = cli_quals._ages_from_comments([
            _comment("2026-09-20T10:00:00Z", GOOD, login="gk"),
            _comment("2026-09-22T10:00:00Z",
                     "Waiting-on: owner — power-cycle — ask him"),
            _comment("2026-09-23T10:00:00Z", "just a note"),
        ], "me")
        self.assertEqual(ages["waiting_on"],
                         cli_quals._parse_iso_ts("2026-09-20T10:00:00Z"))

    def test_the_newest_of_two_valid_lines(self):
        ages = cli_quals._ages_from_comments([
            _comment("2026-09-20T10:00:00Z", GOOD),
            _comment("2026-09-22T10:00:00Z", GOOD),
        ], "me")
        self.assertEqual(ages["waiting_on"],
                         cli_quals._parse_iso_ts("2026-09-22T10:00:00Z"))

    def test_legacy_shapes_carry_the_key(self):
        self.assertIsNone(cli_quals._norm_ages((1.0, 2.0))["waiting_on"])
        self.assertIsNone(cli_quals._norm_ages({"own": 1.0})["waiting_on"])


class Classify(unittest.TestCase):
    PLAIN = {"labels": [{"name": "ops-wait"}]}

    def test_unbacked_plain_ops_wait_is_I(self):
        bucket, reason = ts.classify(self.PLAIN, ts.Facts(wait_unbacked="x"))
        self.assertEqual(bucket, "I")
        self.assertIn("#1298", reason)

    def test_backed_or_unknown_stays_W(self):
        self.assertEqual(ts.classify(self.PLAIN, ts.Facts())[0], "W")
        self.assertEqual(ts.classify(self.PLAIN)[0], "W")   # label partition

    def test_only_the_plain_ops_wait_verdict_moves(self):
        sent = {"labels": [{"name": "ops-wait"}, {"name": "needs-acceptance"}]}
        self.assertEqual(ts.classify(sent, ts.Facts(wait_unbacked="x"))[0], "W")
        q = {"labels": [{"name": "ops-wait"}, {"name": "needs-answer"}]}
        self.assertEqual(ts.classify(q, ts.Facts(wait_unbacked="x"))[0], "U")

    def test_plain_ops_wait_predicate(self):
        self.assertTrue(ts.plain_ops_wait(self.PLAIN))
        for extra in ("needs-acceptance", "prio:bounce", "needs-answer",
                      "needs-gatekeeper"):
            row = {"labels": [{"name": "ops-wait"}, {"name": extra}]}
            self.assertFalse(ts.plain_ops_wait(row), extra)
        self.assertFalse(ts.plain_ops_wait({"labels": []}))

    def test_a_handed_unbacked_row_is_gk_on_the_slice_box(self):
        box = ts.Box(own_stream="david1")
        self.assertEqual(ts.classify(
            self.PLAIN, ts.Facts(wait_unbacked="x", handed=True), box)[0], "gk")
        self.assertEqual(ts.classify(
            self.PLAIN, ts.Facts(handed=True), box)[0], "W")

    def test_bucketize_reads_the_fact_per_row(self):
        rows = {5: _row(5, "ops-wait"), 6: _row(6, "ops-wait")}
        out = ts.bucketize(rows, ts.TicketFacts(wait_unbacked={5: "none"}))
        self.assertEqual((sorted(out["I"]), sorted(out["W"])), ([5], [6]))


class Facts(unittest.TestCase):
    def _refresh(self, home, wait, wait_fn, now=_NOW):
        return cli_ticket_facts.refresh(
            "/repo", "o/r", {5, 6}, home=home, now=now,
            gh_fn=lambda _a: __import__("json").dumps(_graphql()),
            released_fn=lambda *_a: {}, wait=wait, own_stream=None,
            wait_fn=wait_fn)

    def test_refresh_reads_caches_and_load_serves_it(self):
        calls = []

        def wait_fn(_root, _own, numbers):
            calls.append(sorted(numbers))
            return {5: {"waiting_on": None}, 6: {"waiting_on": _NOW}}.get

        with tempfile.TemporaryDirectory() as home:
            facts = self._refresh(home, [5, 6], wait_fn)
            self.assertEqual(list(facts.wait_unbacked), [5])
            loaded = cli_ticket_facts.load("/repo", home=home, now=_NOW + 30)
            self.assertEqual(list(loaded.wait_unbacked), [5])
            self._refresh(home, [5], wait_fn, now=_NOW + 60)     # reused
            self.assertEqual(calls, [[5, 6]])
            self._refresh(home, [5, 7], wait_fn, now=_NOW + 90)  # new member
            self.assertEqual(calls, [[5, 6], [5, 7]])
            later = _NOW + cli_ticket_facts.WAIT_REUSE_S + 120   # expired
            self._refresh(home, [5], wait_fn, now=later)
            self.assertEqual(len(calls), 3)

    def test_a_failed_read_is_unknown(self):
        def boom(*_a):
            raise RuntimeError("gh down")
        with tempfile.TemporaryDirectory() as home:
            facts = self._refresh(home, [5], boom)
            self.assertEqual(facts.wait_unbacked, {})
            self.assertEqual(cli_ticket_facts.load(
                "/repo", home=home, now=_NOW).wait_unbacked, {})
            calls = []

            def wait_fn(_root, _own, numbers):
                calls.append(sorted(numbers))
                return {5: {"waiting_on": None}}.get
            self._refresh(home, [5], wait_fn, now=_NOW + 30)   # not cached
            self.assertEqual(calls, [[5]])

    def test_default_reader_never_reads_per_row(self):
        with mock.patch.object(cli_quals, "_obligation_quals",
                               return_value=["q"]), \
                mock.patch.object(cli_quals, "_ops_wait_prefetch_comments",
                                  return_value={5: []}) as pre, \
                mock.patch.object(cli_quals, "_stream_self_login",
                                  return_value="me"), \
                mock.patch.object(cli_quals, "_issue_comment_ages",
                                  side_effect=AssertionError("per-row read")):
            ages_of = cli_ticket_facts._default_wait_ages("/r", None, {5, 6})
            self.assertIsNone(ages_of(5)["waiting_on"])
            self.assertIsNone(ages_of(6))                # not prefetched
        pre.assert_called_once_with(["q"], "/r")

    def test_the_slice_box_reads_its_own_slice(self):
        with mock.patch.object(cli_quals, "_slice_quals",
                               return_value=["s"]) as sq, \
                mock.patch.object(cli_quals, "_obligation_quals",
                                  side_effect=AssertionError("core quals")), \
                mock.patch.object(cli_quals, "_ops_wait_prefetch_comments",
                                  return_value={}) as pre, \
                mock.patch.object(cli_quals, "_stream_self_login",
                                  return_value="me"):
            cli_ticket_facts._default_wait_ages("/r", "david1", {5})
        sq.assert_called_once_with("david1", "/r")
        pre.assert_called_once_with(["s"], "/r")

    def test_footer_hands_its_own_stream_to_the_read(self):
        seen = {}

        def fake_refresh(*_a, **kw):
            seen.update(kw)
            return ts.TicketFacts()
        with mock.patch.object(cli_ticket_facts, "refresh", fake_refresh), \
                mock.patch.object(cli_ticket_route, "asked_refs",
                                  return_value=frozenset()):
            cli_ticket_route.footer({5: _row(5, "ops-wait")}, "/r", "o/r",
                                    set(), own_stream="david1")
        self.assertEqual(seen["own_stream"], "david1")

    def test_footer_passes_only_plain_ops_wait_rows(self):
        rows = {5: _row(5, "ops-wait"), 6: _row(6, "ops-wait", "needs-acceptance"),
                7: _row(7), 8: _row(8, "ops-wait", "needs-answer")}
        seen = {}

        def fake_refresh(*_a, **kw):
            seen.update(kw)
            return ts.TicketFacts(wait_unbacked={5: "none"})
        with mock.patch.object(cli_ticket_facts, "refresh", fake_refresh), \
                mock.patch.object(cli_ticket_route, "asked_refs",
                                  return_value=frozenset()):
            buckets, _f = cli_ticket_route.footer(rows, "/r", "o/r", set(),
                                                  own_stream=None)
        self.assertEqual(seen["wait"], [5])
        self.assertIn(5, buckets["I"])
        self.assertIn(6, buckets["W"])


class Doctrine(unittest.TestCase):
    def test_w_bullet_points_at_the_rule(self):
        text = (REPO_ROOT / "modules" / "core" /
                "statusline-vocabulary.md").read_text(encoding="utf-8")
        bullet = re.search(r"(?m)^- `· W N`.*$", text).group(0)
        self.assertIn("`Waiting-on: <3rd party> — <event> — <check>`", bullet)
        self.assertIn("#1298", bullet)
        self.assertIn(">7 d old → `I`", bullet)
        self.assertNotIn("own tracking", bullet)   # our own wait is no W


if __name__ == "__main__":
    unittest.main()
