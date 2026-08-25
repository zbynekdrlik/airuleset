"""#699 bod 1 — RELEASE-parked W freshness `recheck!` tag (1h working cadence).

A release-SHAPED parked W ticket (title names a release/version/stage) the OWNING
session has NOT re-checked within RELEASE_RECHECK_MAX_S (1h of WORKING time) is
`recheck!`-tagged in `core-quals`/`slice-quals --ops-wait`
(`cli_quals._release_recheck_flagged`), and job 20's re-check nudge NAMES the
overdue members with the deployed-state re-check cadence duty (#588, EVERY work
cycle, min 1×/hour, don't wait for the daily nudge). The tag makes NO "landed"
claim (that stays the #698 proof-only train-drained clause) — only "a re-check is
overdue", directly falsifiable from title + own-comment age.

These tests lock (faithful mirror of the #570 `stale!` pipeline):
  1. the pure decider (`_release_recheck_flagged`) — release-shaped + own-comment
     >1h flagged; fresh / non-release / own-None / gh-None / cap fail-safe;
  2. `_print_issue_rows` appends ` recheck!` in the reason field;
  3. `_watchdog_ops_wait_fetch` parses `recheck!` → `{number, release_recheck}`;
  4. the nudge text names the recheck members with the cadence duty;
  5. the cli_quals release-shaped regex is drift-locked to the watchdog's.
"""
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_quals_cmd  # noqa: E402
import watchdog.ops_wait_recheck as owr  # noqa: E402

DAY = 24 * 3600


def _rel_rows(*nums):
    """W rows with RELEASE-shaped titles (title names a release/version/stage)."""
    return {n: {"number": n, "title": "release 2.181 stage-3 (#%d)" % n,
                "createdAt": "2026-01-01T00:00:00Z",
                "labels": [{"name": "ops-wait"}]} for n in nums}


def _plain_rows(*nums):
    """W rows whose title names NO release/version/stage (must never flag)."""
    return {n: {"number": n, "title": "importer retry flake (#%d)" % n,
                "createdAt": "2026-01-01T00:00:00Z",
                "labels": [{"name": "ops-wait"}]} for n in nums}


class ReleaseRecheckDecider(unittest.TestCase):
    """`_release_recheck_flagged(rows, now, self_login, ages_fn)` — pure, no gh."""

    def setUp(self):
        # mid-week Wednesday noon (Europe/Bratislava) so a `now - 2h` own comment
        # is a same-working-day span of ~7200 s (unambiguously > the 1h window),
        # and `now - 30min` is unambiguously fresh — no weekend crossing (#607).
        self.now = datetime(2026, 8, 19, 12,
                            tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()

    def _run(self, rows, ages):
        return cli_quals._release_recheck_flagged(
            rows, now=self.now, self_login="me", ages_fn=lambda n: ages.get(n))

    def test_release_shaped_stale_own_comment_is_flagged(self):
        got = self._run(_rel_rows(41), {41: (self.now - 2 * 3600, self.now - 2 * 3600)})
        self.assertEqual(got, {41})

    def test_release_shaped_fresh_own_comment_is_not_flagged(self):
        got = self._run(_rel_rows(41), {41: (self.now - 1800, self.now - 1800)})
        self.assertEqual(got, set())

    def test_non_release_title_is_never_flagged(self):
        # a genuinely stale (>1h) own comment, but the title names no release
        got = self._run(_plain_rows(41), {41: (self.now - 2 * 3600, self.now - 2 * 3600)})
        self.assertEqual(got, set())

    def test_no_own_comment_is_never_flagged(self):
        # STRICTER than #570 stale!: NO any-comment fallback — absence of the
        # session's own re-check evidence is ambiguous, never proof of a miss
        got = self._run(_rel_rows(41), {41: (None, self.now - 2 * 3600)})
        self.assertEqual(got, set())

    def test_gh_failure_None_is_never_flagged(self):
        got = self._run(_rel_rows(41), {41: None})
        self.assertEqual(got, set())

    def test_cap_bounds_the_per_member_fetches(self):
        many = _rel_rows(*range(1, cli_quals.OPS_WAIT_STALE_MAX_FETCHES + 6))
        seen = []

        def ages(n):
            seen.append(n)
            return (self.now - 2 * 3600, self.now - 2 * 3600)   # all overdue
        cli_quals._release_recheck_flagged(
            many, now=self.now, self_login="me", ages_fn=ages)
        self.assertLessEqual(len(seen), cli_quals.OPS_WAIT_STALE_MAX_FETCHES)

    def test_window_is_one_hour(self):
        self.assertEqual(cli_quals.RELEASE_RECHECK_MAX_S, 3600)


class PrintRowsRecheckFlag(unittest.TestCase):
    """`_print_issue_rows(..., recheck_numbers=...)` appends ` recheck!`."""

    def test_recheck_appended_to_reason_field(self):
        import io
        import contextlib
        rows = _rel_rows(41, 43)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                recheck_numbers={41})
        lines = {ln.split("\t", 1)[0]: ln for ln in buf.getvalue().splitlines()}
        self.assertIn("recheck!", lines["41"].split("\t")[3])
        self.assertNotIn("recheck!", lines["43"].split("\t")[3])


class WatchdogFetchParsesRecheck(unittest.TestCase):
    """`_watchdog_ops_wait_fetch` returns [{number, release_recheck}] parsed from
    the `--ops-wait` reason field; legacy `int` members still supported."""

    def test_parses_recheck_marker_into_dicts(self):
        out = ("41\t2026-01-01T00:00:00Z\taction-only\tops-wait recheck!\ttitle\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tops-wait\ttitle\n")
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch("subprocess.run", return_value=cp), \
                mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                mock.patch.object(airuleset, "resolve_authority", lambda cwd=None: "full"):
            members = airuleset._watchdog_ops_wait_fetch("/r")
        by_num = {m["number"]: m for m in members}
        self.assertTrue(by_num[41]["release_recheck"])
        self.assertFalse(by_num[43]["release_recheck"])


class NudgeNamesRecheckMembers(unittest.TestCase):
    """The job-20 nudge text NAMES the recheck W members with the cadence duty
    (deployed-state re-check #588 every work cycle, min 1×/hour)."""

    def test_recheck_members_named_with_cadence(self):
        members = [{"number": 41, "release_recheck": True},
                   {"number": 43, "release_recheck": False}]
        t = owr._nudge_text(None, members, now=1000.0,
                            w_seen={"41": 1000.0, "43": 1000.0})
        self.assertIn("#41", t)
        self.assertIn("RELEASE-RECHECK", t)      # the recheck sub-clause fires
        self.assertIn("1×/hod", t)               # the cadence duty
        self.assertNotIn("#43", t.split("RELEASE-RECHECK", 1)[1])

    def test_legacy_int_members_no_recheck_clause(self):
        t = owr._nudge_text(None, [41, 43], now=1000.0,
                            w_seen={"41": 1000.0, "43": 1000.0})
        self.assertNotIn("RELEASE-RECHECK", t)


class ReleaseShapedRegexDriftLock(unittest.TestCase):
    """cli_quals's release-shaped title regex MUST stay byte-identical to the
    watchdog's (#698 live-probed) so the tag and the nudge classify the same
    titles — no drift between the two release-shaped detections."""

    def test_patterns_are_identical(self):
        self.assertEqual(cli_quals._RELEASE_RECHECK_TITLE_RX.pattern,
                         owr._RELEASE_SHAPED_RX.pattern)


class DoctrineContentLock699(unittest.TestCase):
    """The W bullet (statusline-vocabulary.md) + the autopilot SKILL W paragraph
    must carry the #699 release-recheck cadence: a release-parked W ticket is
    deployed-state re-checked by the OWNING session EVERY work cycle (min 1×/hour),
    job 20 = backstop only."""

    STATUS = REPO / "modules" / "core" / "statusline-vocabulary.md"
    SKILL = REPO / "skills" / "autopilot" / "SKILL.md"
    FINDER = "recheck! kadencia je MECHANICKÁ (#699"

    def _line_with(self, text, finder):
        for ln in text.splitlines():
            if finder in ln:
                return ln
        return ""

    def _norm_window(self, text, start_token, end_marker="\n- **"):
        i = text.index(start_token)
        j = text.find(end_marker, i)
        return " ".join(text[i:(j if j > 0 else len(text))].split())

    def test_statusline_W_bullet_carries_699_cadence(self):
        text = self.STATUS.read_text(encoding="utf-8")
        line = self._line_with(text, self.FINDER)
        self.assertTrue(line, "the W bullet must carry the #699 recheck! sentence")
        for tok in ("recheck!", "KAŽDÝ pracovný cyklus", "#699"):
            self.assertIn(tok, line, "W bullet lost the #699 token %r" % tok)

    def test_autopilot_skill_carries_699_cadence(self):
        # #500: the SKILL clause WRAPS across indented physical lines, so the
        # finder/tokens are not contiguous in the raw text — normalise first.
        # `recheck!` + `KAŽDÝ pracovný cyklus` appear ONLY in the #699 clause, so
        # a full-clause removal drops all three from the normed text (real teeth).
        norm = " ".join(self.SKILL.read_text(encoding="utf-8").split())
        for tok in (self.FINDER, "recheck!", "KAŽDÝ pracovný cyklus"):
            self.assertIn(tok, norm,
                          "autopilot SKILL lost the #699 token %r" % tok)


if __name__ == "__main__":
    unittest.main()
