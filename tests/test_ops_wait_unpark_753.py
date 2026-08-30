"""#753 part 1 — the mechanical `unpark?` signal for a resolvable parked-W ticket.

Owner decision (comment 5469417300, option A): `unpark?` is a SESSION-DELEGATED
audit, NOT a literal network column.
  (a) release-landed → a per-member CLI `unpark?` tag in `--ops-wait`, computed
      from the #698 proof-only ORIGIN release-train signal (`_release_train_drained`)
      + a `resolve_authority ∈ {full, branch-merge}` guard + the release-shaped
      title regex — consistent between the session's on-demand call and the
      watchdog subprocess (both read origin, never a credless PROD/Discuss read),
      fail-safe UNTAGGED on any error/ambiguity (#539/#570 never-false-accuse).
  (b) client-replied → a session-delegated #695 DISCUSS-AUDIT extension: an
      odoo-erp-scoped UNPARK-AUDIT nudge clause counting the acceptance-reason W
      members the session must re-audit (never a cli_quals network tag — that is
      the rejected option B).

These tests lock:
  1. `cli_quals._release_train_drained` mirrors the watchdog's byte-for-byte
     (drift-lock, so the CLI tag and the #698 nudge classify the same origin state);
  2. `_unpark_release_flagged` — the pure decider (release-shaped + authority + drained,
     fail-safe UNTAGGED, ONE lazy per-repo fetch only when a shaped member exists);
  3. `_print_issue_rows(..., unpark_numbers=...)` appends ` unpark?` LAST;
  4. `_ops_wait_summary_line(..., unpark_numbers=...)` reports `unpark=N`;
  5. `_watchdog_ops_wait_fetch` parses `unpark?` → `member["unpark"]` and the base
     reason → `member["acceptance"]`;
  6. `_ops_wait_flag_sets` returns the 4th `unpark` set (authority-gated, one fetch);
  7. the watchdog (b) UNPARK-AUDIT clause fires only odoo-erp-scoped + with an
     acceptance-W count;
  8. the doctrine (statusline W bullet) carries the `unpark?` mechanism.
"""
import io
import contextlib
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_quals
import cli_quals_cmd
from watchdog import ops_wait_recheck as owr

DRAINED = {"train": True, "ahead": 0, "in_flight": False}
NOT_DRAINED = {"train": True, "ahead": 3, "in_flight": False}
TWO_BRANCH = {"train": False, "ahead": 0, "in_flight": False}


def _rel_rows(*nums):
    return {n: {"number": n, "title": "release v2.226.0 nasadenie",
                "labels": [{"name": "ops-wait"}]} for n in nums}


def _plain_rows(*nums):
    return {n: {"number": n, "title": "nejaká iná úloha %d" % n,
                "labels": [{"name": "ops-wait"}]} for n in nums}


class ReleaseTrainDrainedMirror(unittest.TestCase):
    """cli_quals's `_release_train_drained` MUST behave byte-for-byte like the
    watchdog's (#698) — same origin state → same verdict, no drift between the
    CLI `unpark?` tag and the #698 RELEASE-LANDOL nudge."""

    def test_matches_watchdog_across_matrix(self):
        for rstate in (DRAINED, NOT_DRAINED, TWO_BRANCH, None, "x", {},
                       {"train": True, "ahead": True, "in_flight": False},
                       {"train": True, "ahead": 0, "in_flight": None}):
            self.assertEqual(cli_quals._release_train_drained(rstate),
                             owr._release_train_drained(rstate),
                             "drift on %r" % (rstate,))

    def test_true_only_for_proven_drained(self):
        self.assertTrue(cli_quals._release_train_drained(DRAINED))
        self.assertFalse(cli_quals._release_train_drained(NOT_DRAINED))
        self.assertFalse(cli_quals._release_train_drained(TWO_BRANCH))
        self.assertFalse(cli_quals._release_train_drained(None))


class UnparkReleaseDecider(unittest.TestCase):
    """`_unpark_release_flagged(rows, authority, release_fetch)` — pure, one lazy
    fetch, fail-safe UNTAGGED."""

    def test_flags_release_shaped_when_full_and_drained(self):
        got = cli_quals._unpark_release_flagged(
            _rel_rows(41, 43), authority="full",
            release_fetch=lambda: DRAINED)
        self.assertEqual(got, {41, 43})

    def test_branch_merge_authority_also_qualifies(self):
        got = cli_quals._unpark_release_flagged(
            _rel_rows(41), authority="branch-merge",
            release_fetch=lambda: DRAINED)
        self.assertEqual(got, {41})

    def test_not_drained_untagged(self):
        self.assertEqual(set(), cli_quals._unpark_release_flagged(
            _rel_rows(41), authority="full", release_fetch=lambda: NOT_DRAINED))

    def test_fork_no_merge_authority_untagged(self):
        # fork-no-merge origin is the FORK whose frozen branches read drained
        # forever — the exact #698 false-claim the authority guard exists for.
        self.assertEqual(set(), cli_quals._unpark_release_flagged(
            _rel_rows(41), authority="fork-no-merge",
            release_fetch=lambda: DRAINED))

    def test_none_authority_untagged(self):
        self.assertEqual(set(), cli_quals._unpark_release_flagged(
            _rel_rows(41), authority=None, release_fetch=lambda: DRAINED))

    def test_non_release_title_untagged(self):
        self.assertEqual(set(), cli_quals._unpark_release_flagged(
            _plain_rows(41), authority="full", release_fetch=lambda: DRAINED))

    def test_fetch_error_untagged(self):
        def _boom():
            raise RuntimeError("gh down")
        self.assertEqual(set(), cli_quals._unpark_release_flagged(
            _rel_rows(41), authority="full", release_fetch=_boom))

    def test_no_fetch_when_no_release_shaped_member(self):
        # the lazy fetch must NOT fire when nothing could ever be flagged
        # (bounded — the #570 budget: no wasted per-repo origin read).
        calls = []

        def _fetch():
            calls.append(1)
            return DRAINED
        got = cli_quals._unpark_release_flagged(
            _plain_rows(41, 43), authority="full", release_fetch=_fetch)
        self.assertEqual(set(), got)
        self.assertEqual([], calls)

    def test_fetch_fires_at_most_once(self):
        calls = []

        def _fetch():
            calls.append(1)
            return DRAINED
        cli_quals._unpark_release_flagged(
            _rel_rows(41, 43, 45), authority="full", release_fetch=_fetch)
        self.assertEqual(1, len(calls))


class PrintRowsUnparkFlag(unittest.TestCase):
    """`_print_issue_rows(..., unpark_numbers=...)` appends ` unpark?` LAST."""

    def test_unpark_appended_to_reason_field(self):
        rows = _rel_rows(41, 43)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                unpark_numbers={41})
        lines = {ln.split("\t", 1)[0]: ln for ln in buf.getvalue().splitlines()}
        self.assertIn("unpark?", lines["41"].split("\t")[3])
        self.assertNotIn("unpark?", lines["43"].split("\t")[3])

    def test_unpark_after_recheck(self):
        rows = _rel_rows(41)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                recheck_numbers={41}, unpark_numbers={41})
        reason = buf.getvalue().splitlines()[0].split("\t")[3]
        self.assertLess(reason.index("recheck!"), reason.index("unpark?"),
                        "unpark? must render AFTER recheck!")


class SummaryLineUnpark(unittest.TestCase):
    def test_reports_unpark_count(self):
        ow = _rel_rows(1, 2, 3)
        line = cli_quals_cmd._ops_wait_summary_line(
            ow, set(), set(), set(), unpark_numbers={1, 2})
        self.assertIn("unpark=2", line)

    def test_backward_compatible_four_positional(self):
        # the #754 callers pass 4 positional args — still valid, unpark=0.
        line = cli_quals_cmd._ops_wait_summary_line(
            _rel_rows(1), set(), set(), set())
        self.assertIn("unpark=0", line)


class WatchdogFetchParsesUnpark(unittest.TestCase):
    def test_parses_unpark_and_acceptance(self):
        out = ("41\t2026-01-01T00:00:00Z\taction-only\tops-wait unpark?\ttitle\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tacceptance\ttitle\n"
               "45\t2026-01-01T00:00:00Z\taction-only\tops-wait\ttitle\n")
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch("subprocess.run", return_value=cp), \
                mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"):
            members = airuleset._watchdog_ops_wait_fetch("/r")
        by = {m["number"]: m for m in members}
        self.assertTrue(by[41]["unpark"])
        self.assertFalse(by[43]["unpark"])
        self.assertTrue(by[43]["acceptance"])
        self.assertFalse(by[45]["acceptance"])


class FlagSetsReturnsUnpark(unittest.TestCase):
    def test_returns_fourth_unpark_set(self):
        ow = _rel_rows(41)
        with mock.patch.object(airuleset, "_stream_self_login",
                               lambda: "me"), \
                mock.patch.object(airuleset, "_issue_comment_ages",
                                  lambda *a, **k: None), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"), \
                mock.patch.object(airuleset, "_watchdog_release_state_fetch",
                                  lambda cwd: DRAINED):
            sets = cli_quals_cmd._ops_wait_flag_sets(ow, "/r")
        self.assertEqual(4, len(sets))
        self.assertEqual({41}, sets[3])


class NudgeUnparkAudit(unittest.TestCase):
    """The (b) client-replied branch: an odoo-erp-scoped UNPARK-AUDIT nudge clause
    counting acceptance-reason W members (session re-reads the cited threads)."""

    def test_unpark_audit_clause_fires_with_count(self):
        t = owr._nudge_text(None, [{"number": 41, "acceptance": True}],
                            now=1000.0, unpark_audit_n=1)
        self.assertIn("UNPARK-AUDIT 1", t)
        self.assertIn("#753", t)
        self.assertNotIn("#41", t)      # no member enumeration (#714)

    def test_no_clause_when_zero(self):
        t = owr._nudge_text(None, [{"number": 41, "acceptance": True}],
                            now=1000.0, unpark_audit_n=0)
        self.assertNotIn("UNPARK-AUDIT", t)

    def test_acceptance_numbers_counts_acceptance_members(self):
        members = [{"number": 41, "acceptance": True},
                   {"number": 43, "acceptance": False},
                   {"number": 45, "acceptance": True}]
        self.assertEqual([41, 45], owr._acceptance_numbers(members))

    def test_acceptance_numbers_legacy_int_empty(self):
        self.assertEqual([], owr._acceptance_numbers([41, 43]))


class DoctrineContentLock753Part1(unittest.TestCase):
    """The statusline W bullet carries the `unpark?` mechanism (part 1)."""

    def test_w_bullet_mentions_unpark(self):
        p = (Path(__file__).resolve().parent.parent
             / "modules/core/statusline-vocabulary.md")
        text = p.read_text(encoding="utf-8")
        self.assertIn("unpark?", text)


if __name__ == "__main__":
    unittest.main()
