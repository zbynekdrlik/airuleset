"""#818 — the W staleness / job-20 nudge must be aware of the #799 N=3
tacit-acceptance window, so on days 2–3 it stops fighting the tacit-close
doctrine (a `stale!` tag + a "remind DNES" nudge when a second reminder is
forbidden).

Doctrine (#799): a delivered+verified client-acceptance W ticket gets ONE
substantive reminder in the #607 working-day window; then N=3 WORKING days of
silence → TACIT CLOSE (never reminded again). The single reminder is a #753
cited own push, so `_stale_ops_wait_flagged` re-fires `stale!` 24 working-hours
after it and `ops_wait_recheck._flag_items` nudges "posli pripomienku DNES".

The falsifiable signal (design Prístup 2): a dedicated LINE-ANCHORED ticket
marker `Acceptance-reminder: msg <id>` on the final-reminder comment (immune to
inline/quoted mentions, doubles as the #753 cited push). These tests lock:
  1. `_issue_comment_ages` extracts `own_final_reminder` from the marker;
     `_norm_ages` carries it (legacy tuple → None).
  2. `cli_quals._tacit_window_flagged` — acceptance-scoped, working-day N=3 gate,
     newest-cited guard, fail-safe UNTAGGED.
  3. `_ops_wait_flag_sets` computes the tacit sets AND subtracts them from
     `stale!` (and `recheck!`), returning them so the caller can render them.
  4. `_print_issue_rows` renders `tacit-wait` / `tacit-close?`.
  5. `_ops_wait_summary_line` counts them (#754 bucket picture).
  6. `_watchdog_ops_wait_fetch` parses `tacit-close?` → `member["tacit_close"]`
     while the base `acceptance` reason survives (UNPARK-AUDIT still counts it).
  7. `ops_wait_recheck._flag_items` emits a `TACIT-CLOSE` clause with the #799
     action (verify sent + close), NEVER "remind".
  8. the doctrine (statusline W bullet) carries the marker + both tags + the
     operative NEpripomínaj negation.
"""
import contextlib
import io
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_quals
import cli_quals_cmd
import working_time
from watchdog import ops_wait_recheck as owr

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "modules" / "core" / "statusline-vocabulary.md"

# A deterministic weekday timeline: now = Friday noon UTC (2026-09-04). Working
# deltas are then unambiguous regardless of the calendar the suite runs on.
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc).timestamp()          # Fri
REMINDER_IN_WINDOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc).timestamp()   # Wed → 48 working h
REMINDER_PAST_WINDOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc).timestamp()  # Mon → 96 working h


def _acc_rows(*nums):
    """Acceptance-reason W rows (needs-acceptance + ops-wait)."""
    return {n: {"number": n, "title": "SMS notifikacie %d" % n,
                "labels": [{"name": "ops-wait"}, {"name": "needs-acceptance"}],
                "createdAt": "2026-08-01T00:00:00Z"} for n in nums}


def _ow_rows(*nums):
    """Pure ops-wait (release/event) W rows — NOT client-acceptance."""
    return {n: {"number": n, "title": "release v2.181 nasadenie %d" % n,
                "labels": [{"name": "ops-wait"}],
                "createdAt": "2026-08-01T00:00:00Z"} for n in nums}


def _ages(*, cited=None, final_reminder=None, any_ts=None, oldest=None):
    return {"own": cited, "any": any_ts if any_ts is not None else cited,
            "own_cited": cited, "own_oldest": oldest if oldest is not None else cited,
            "own_final_reminder": final_reminder}


class WorkingDeltaFixturesAreSane(unittest.TestCase):
    """The deterministic timeline must give the intended working-day deltas so
    the N=3 boundary tests below are not calendar-fragile."""

    def test_in_window_is_under_three_working_days_but_over_24h(self):
        self.assertFalse(working_time.working_deadline_passed(
            REMINDER_IN_WINDOW, NOW, 3 * 24 * 3600))
        self.assertTrue(working_time.working_deadline_passed(
            REMINDER_IN_WINDOW, NOW, 24 * 3600))     # would otherwise be stale

    def test_past_window_is_over_three_working_days(self):
        self.assertTrue(working_time.working_deadline_passed(
            REMINDER_PAST_WINDOW, NOW, 3 * 24 * 3600))


class IssueCommentAgesExtractsFinalReminder(unittest.TestCase):
    def _fake_gh(self, comments):
        obj = {"comments": comments}
        import json
        return json.dumps(obj)

    def test_own_final_reminder_from_line_anchored_marker(self):
        comments = [
            {"createdAt": "2026-09-01T09:00:00Z", "author": {"login": "me"},
             "body": "delivered draft, msg 1739000"},
            {"createdAt": "2026-09-02T09:00:00Z", "author": {"login": "me"},
             "body": "Acceptance-reminder: msg 1739648\nposlal som finalnu pripomienku"},
        ]
        with mock.patch.object(airuleset, "_gh_out",
                               lambda *a, **k: self._fake_gh(comments)):
            res = airuleset._issue_comment_ages(41, "me", NOW, cwd="/r")
        self.assertIsNotNone(res.get("own_final_reminder"))
        self.assertEqual(
            res["own_final_reminder"],
            cli_quals._parse_iso_ts("2026-09-02T09:00:00Z"))
        # the marker line also carries a #753 citation → own_cited == it
        self.assertEqual(res["own_cited"], res["own_final_reminder"])

    def test_inline_mention_does_not_open_window(self):
        # a client draft / plan comment MENTIONING the marker inline (not at line
        # start) must NOT register as a final reminder (false-open guard).
        comments = [
            {"createdAt": "2026-09-02T09:00:00Z", "author": {"login": "me"},
             "body": "pripravim text co bude mat Acceptance-reminder: v sebe, "
                     "posts msg 1739648"},
        ]
        with mock.patch.object(airuleset, "_gh_out",
                               lambda *a, **k: self._fake_gh(comments)):
            res = airuleset._issue_comment_ages(41, "me", NOW, cwd="/r")
        self.assertIsNone(res.get("own_final_reminder"))

    def test_norm_ages_legacy_tuple_has_no_final_reminder(self):
        d = cli_quals._norm_ages((123.0, 456.0))
        self.assertIsNone(d["own_final_reminder"])


class TacitWindowClassifier(unittest.TestCase):
    def test_in_window_is_tacit_wait(self):
        rows = _acc_rows(41)
        ages = {41: _ages(cited=REMINDER_IN_WINDOW,
                          final_reminder=REMINDER_IN_WINDOW)}
        tw, tc = cli_quals._tacit_window_flagged(
            rows, now=NOW, ages_fn=lambda n: ages[n])
        self.assertEqual({41}, tw)
        self.assertEqual(set(), tc)

    def test_past_window_is_tacit_close(self):
        rows = _acc_rows(42)
        ages = {42: _ages(cited=REMINDER_PAST_WINDOW,
                          final_reminder=REMINDER_PAST_WINDOW)}
        tw, tc = cli_quals._tacit_window_flagged(
            rows, now=NOW, ages_fn=lambda n: ages[n])
        self.assertEqual(set(), tw)
        self.assertEqual({42}, tc)

    def test_no_marker_is_untagged(self):
        rows = _acc_rows(43)
        ages = {43: _ages(cited=REMINDER_PAST_WINDOW, final_reminder=None)}
        tw, tc = cli_quals._tacit_window_flagged(
            rows, now=NOW, ages_fn=lambda n: ages[n])
        self.assertEqual(set(), tw)
        self.assertEqual(set(), tc)

    def test_non_acceptance_reason_is_untagged(self):
        # a pure ops-wait (release) member is not client-acceptance → never tacit
        rows = _ow_rows(44)
        ages = {44: _ages(cited=REMINDER_PAST_WINDOW,
                          final_reminder=REMINDER_PAST_WINDOW)}
        tw, tc = cli_quals._tacit_window_flagged(
            rows, now=NOW, ages_fn=lambda n: ages[n])
        self.assertEqual(set(), tw)
        self.assertEqual(set(), tc)

    def test_newer_cited_push_exits_tacit(self):
        # a later CITED own push (own_cited > own_final_reminder) proves the
        # session re-engaged (client replied on Discuss) → normal #570 handling.
        rows = _acc_rows(45)
        ages = {45: _ages(cited=NOW - 3600,     # a fresh push after the reminder
                          final_reminder=REMINDER_PAST_WINDOW)}
        tw, tc = cli_quals._tacit_window_flagged(
            rows, now=NOW, ages_fn=lambda n: ages[n])
        self.assertEqual(set(), tw)
        self.assertEqual(set(), tc)

    def test_fetch_failure_is_untagged(self):
        rows = _acc_rows(46)
        tw, tc = cli_quals._tacit_window_flagged(
            rows, now=NOW, ages_fn=lambda n: None)
        self.assertEqual(set(), tw)
        self.assertEqual(set(), tc)


class FlagSetsSubtractsTacitFromStale(unittest.TestCase):
    """`_ops_wait_flag_sets` returns the tacit sets AND removes tacit members
    from `stale!` — the whole point (calendar-robust via a 400-day-old marker)."""

    def test_tacit_member_excluded_from_stale(self):
        VERY_OLD = 400 * 24 * 3600
        import time
        base = time.time()
        ow = _acc_rows(42, 43)
        ages = {
            42: _ages(cited=base - VERY_OLD, final_reminder=base - VERY_OLD),
            43: _ages(cited=base - VERY_OLD, final_reminder=None),
        }
        with mock.patch.object(airuleset, "_stream_self_login", lambda: "me"), \
                mock.patch.object(airuleset, "_issue_comment_ages",
                                  lambda n, *a, **k: ages[n]), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"), \
                mock.patch.object(airuleset, "_watchdog_release_state_fetch",
                                  lambda cwd: None):
            sets = cli_quals_cmd._ops_wait_flag_sets(ow, "/r")
        self.assertEqual(6, len(sets))
        stale, _recheck, _gkh, _unpark, tacit_wait, tacit_close = sets
        self.assertIn(42, tacit_close)          # delivered+reminded, silent
        self.assertNotIn(42, stale)             # NOT double-flagged stale!
        self.assertIn(43, stale)                # no marker → stale! stands
        self.assertNotIn(43, tacit_close)


class PrintRowsTacitTags(unittest.TestCase):
    def test_tags_rendered(self):
        rows = _acc_rows(41, 42, 43)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                tacit_wait_numbers={41}, tacit_close_numbers={42})
        lines = {ln.split("\t", 1)[0]: ln for ln in buf.getvalue().splitlines()}
        self.assertIn("tacit-wait", lines["41"].split("\t")[3])
        self.assertIn("tacit-close?", lines["42"].split("\t")[3])
        self.assertNotIn("tacit", lines["43"].split("\t")[3])


class SummaryLineTacit(unittest.TestCase):
    def test_reports_tacit_counts(self):
        ow = _acc_rows(1, 2, 3)
        line = cli_quals_cmd._ops_wait_summary_line(
            ow, set(), set(), set(), unpark_numbers=set(),
            tacit_wait_numbers={1}, tacit_close_numbers={2})
        self.assertIn("tacit-wait=1", line)
        self.assertIn("tacit-close=1", line)


class WatchdogFetchParsesTacitClose(unittest.TestCase):
    def test_tacit_close_parsed_acceptance_survives(self):
        out = ("41\t2026-01-01T00:00:00Z\taction-only\tacceptance tacit-close?\ttitle\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tacceptance tacit-wait\ttitle\n"
               "45\t2026-01-01T00:00:00Z\taction-only\tacceptance\ttitle\n")
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch("subprocess.run", return_value=cp), \
                mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"):
            members = airuleset._watchdog_ops_wait_fetch("/r")
        by_num = {m["number"]: m for m in members}
        self.assertTrue(by_num[41]["tacit_close"])
        self.assertFalse(by_num[43]["tacit_close"])
        # the base `acceptance` reason survives → UNPARK-AUDIT still counts it
        self.assertTrue(by_num[41]["acceptance"])
        self.assertTrue(by_num[43]["acceptance"])


class NudgeTacitCloseClause(unittest.TestCase):
    def test_tacit_close_numbers_helper(self):
        members = [{"number": 41, "tacit_close": True},
                   {"number": 43, "tacit_close": False},
                   {"number": 45, "tacit_close": True}]
        self.assertEqual([41, 45], owr._tacit_close_numbers(members))

    def test_tacit_close_clause_fires_with_close_action(self):
        members = [{"number": 41, "tacit_close": True}]
        t = owr._nudge_text(None, members, now=1000.0)
        self.assertIn("TACIT-CLOSE 1", t)
        self.assertIn("#799", t)
        self.assertIn("NEpripomínaj", t)       # the operative negation — no 2nd reminder
        self.assertNotIn("#41", t)             # no member enumeration (#714)

    def test_no_clause_when_zero(self):
        members = [{"number": 41, "tacit_close": False}]
        t = owr._nudge_text(None, members, now=1000.0)
        self.assertNotIn("TACIT-CLOSE", t)


class DoctrineNamesTacitMechanism(unittest.TestCase):
    def _w_line(self):
        text = STATUS.read_text(encoding="utf-8")
        for ln in text.splitlines():
            if "`· W N` (#510" in ln:
                return ln
        self.fail("W bullet not found in statusline-vocabulary.md")

    def test_marker_and_tags_and_negation(self):
        line = self._w_line()
        for tok in ("Acceptance-reminder:", "tacit-wait", "tacit-close?",
                    "#818", "NEpripomínaj", "acceptance-scoped", "N=3"):
            self.assertIn(tok, line, "statusline W bullet missing %r" % tok)


if __name__ == "__main__":
    unittest.main()
