"""#570 bod 2 — W (`ops-wait`) freshness `stale!` tag + nudge naming.

A parked W ticket the stream has NOT pushed on within 24h is `stale!`-tagged in
`core-quals`/`slice-quals --ops-wait` (`cli_quals._stale_ops_wait_flagged`), and
job 20's re-check nudge NAMES the stale members with the required action. These
tests lock:
  1. the pure stale decider (`_stale_ops_wait_flagged`) — evidence = the
     stream's OWN comment, fresh/stale/fail-safe/degradation branches;
  2. `_print_issue_rows` appends ` stale!` in the reason field (the #539
     `no-question!` mechanism, a sibling flag);
  3. `_watchdog_ops_wait_fetch` parses `stale!` → returns `{number, stale}`
     (back-compatible with legacy `int` members);
  4. the nudge text names the stale members with the doctrine action.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_quals
import cli_quals_cmd
import watchdog.ops_wait_recheck as owr

DAY = 24 * 3600


def _rows(*nums):
    return {n: {"number": n, "title": "t%d" % n, "createdAt": "2026-01-01T00:00:00Z",
                "labels": [{"name": "ops-wait"}]} for n in nums}


class StaleDecider(unittest.TestCase):
    """`_stale_ops_wait_flagged(rows, now, self_login, ages_fn)` — pure, no gh."""

    def setUp(self):
        # #607: the 24h window is now WORKING time (weekend-excluded). The old
        # `now=1_000_000.0` anchor is 1970-01-12 (a Monday) whose `now - 3*DAY`
        # lands on Friday 1970-01-09 — a span that straddles a whole weekend, so
        # its WORKING elapsed time is only ~24h (borderline, no longer strictly
        # `> 24h`). Anchor to a mid-week Wednesday noon so `now - 3*DAY` (Sunday)
        # yields ~60 WORKING hours — unambiguously stale under both the old flat
        # and the new weekend-aware contract, keeping the decider's intent intact.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.now = datetime(2026, 8, 19, 12,
                            tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()

    def _run(self, rows, ages):
        return cli_quals._stale_ops_wait_flagged(
            rows, now=self.now, self_login="me", ages_fn=lambda n: ages.get(n))

    def test_fresh_own_comment_is_not_stale(self):
        got = self._run(_rows(41), {41: (self.now - 3600, self.now - 3600)})
        self.assertEqual(got, set())

    def test_own_comment_older_than_24h_is_stale(self):
        # #753: the freshness anchor is the newest CITED own push; a cited push
        # 3 days old is stale even though a third party commented 1h ago.
        got = self._run(_rows(41), {41: {"own": self.now - 3 * DAY,
                                         "own_cited": self.now - 3 * DAY,
                                         "own_oldest": self.now - 3 * DAY,
                                         "any": self.now - 3600}})
        self.assertEqual(got, {41})

    def test_no_own_but_recent_any_comment_is_not_stale(self):
        # a fresh park (only a supervisor/third-party comment, <24h) must not
        # be falsely accused
        got = self._run(_rows(41), {41: (None, self.now - 3600)})
        self.assertEqual(got, set())

    def test_no_own_and_stale_any_comment_is_stale(self):
        # parked/left untouched >24h with no own push at all
        got = self._run(_rows(41), {41: (None, self.now - 3 * DAY)})
        self.assertEqual(got, {41})

    def test_zero_comments_at_all_is_never_flagged(self):
        got = self._run(_rows(41), {41: (None, None)})
        self.assertEqual(got, set())

    def test_gh_failure_None_is_never_flagged(self):
        # ages_fn returning None (fetch failed/unusable) → fail-safe, no flag
        got = self._run(_rows(41), {41: None})
        self.assertEqual(got, set())

    def test_cap_bounds_the_per_member_fetches(self):
        many = _rows(*range(1, cli_quals.OPS_WAIT_STALE_MAX_FETCHES + 6))
        seen = []

        def ages(n):
            seen.append(n)
            return (self.now - 3 * DAY, self.now - 3 * DAY)   # all stale
        cli_quals._stale_ops_wait_flagged(
            many, now=self.now, self_login="me", ages_fn=ages)
        self.assertLessEqual(len(seen), cli_quals.OPS_WAIT_STALE_MAX_FETCHES)


class CommentAges(unittest.TestCase):
    """`_issue_comment_ages` parses author.login + createdAt; fail-safe → None."""

    def _view(self, comments):
        import json
        payload = json.dumps({"comments": comments})
        return mock.patch.object(
            airuleset, "_gh_out", lambda *a, **k: payload)

    def test_own_and_any_ages_resolved(self):
        with self._view([
            {"author": {"login": "other"}, "createdAt": "2026-08-19T10:00:00Z"},
            {"author": {"login": "me"}, "createdAt": "2026-08-18T10:00:00Z"},
        ]):
            res = cli_quals._issue_comment_ages(41, "me", 0, cwd=None)
        self.assertIsNotNone(res)
        own_ts, any_ts = res["own"], res["any"]   # #753: dict return
        self.assertIsNotNone(own_ts)
        self.assertIsNotNone(any_ts)
        self.assertGreater(any_ts, own_ts)   # the 'other' comment is newer

    def test_gh_empty_output_is_None(self):
        with mock.patch.object(airuleset, "_gh_out", lambda *a, **k: ""):
            self.assertIsNone(cli_quals._issue_comment_ages(41, "me", 0))


class PrintRowsStaleFlag(unittest.TestCase):
    """`_print_issue_rows(..., stale_numbers=...)` appends ` stale!` in reason."""

    def test_stale_appended_to_reason_field(self):
        import io
        import contextlib
        rows = _rows(41, 43)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                stale_numbers={41})
        lines = {ln.split("\t", 1)[0]: ln for ln in buf.getvalue().splitlines()}
        self.assertIn("stale!", lines["41"].split("\t")[3])
        self.assertNotIn("stale!", lines["43"].split("\t")[3])


class WatchdogFetchParsesStale(unittest.TestCase):
    """`_watchdog_ops_wait_fetch` returns [{number, stale}] parsed from the
    `--ops-wait` reason field; legacy `int` members still supported downstream."""

    def test_parses_stale_marker_into_dicts(self):
        out = ("41\t2026-01-01T00:00:00Z\taction-only\tops-wait stale!\ttitle\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tops-wait\ttitle\n")
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch("subprocess.run", return_value=cp), \
                mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                mock.patch.object(airuleset, "resolve_authority", lambda cwd=None: "full"):
            members = airuleset._watchdog_ops_wait_fetch("/r")
        by_num = {m["number"]: m for m in members}
        self.assertTrue(by_num[41]["stale"])
        self.assertFalse(by_num[43]["stale"])


class NudgeNamesStaleMembers(unittest.TestCase):
    """The job-20 nudge text NAMES the stale W members with the doctrine action
    (verify blocker + remind the third party TODAY via a ticket comment)."""

    def test_stale_flag_fires_with_action(self):
        # #714: the stale sub-clause is a compact COUNT flag (the members live
        # in `slice-quals --ops-wait`, tagged `stale!`), keeping STALE + DNES.
        members = [{"number": 41, "stale": True}, {"number": 43, "stale": False}]
        t = owr._nudge_text(None, members, now=1000.0)
        self.assertIn("STALE 1", t)        # the stale flag fires (count=1)
        self.assertIn("#607", t)           # the doctrine pointer
        self.assertIn("DNES", t)           # the required action (remind today)
        self.assertNotIn("#41", t)         # no member enumeration (#714)

    def test_legacy_int_members_still_work(self):
        # back-compat: an int list yields no stale flag; the nudge is a compact
        # W count, never a member enumeration (#714).
        t = owr._nudge_text(None, [41, 43], now=1000.0)
        self.assertIn("W=2", t)
        self.assertNotIn("#41", t)
        self.assertNotIn("STALE", t)


if __name__ == "__main__":
    unittest.main()
