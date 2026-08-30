"""#754 — I0∧U0∧W0: W je DLH so stropom (W-drain lane + agregátna eskalácia).

Locks the mechanisms that make a ballooning W bucket VISIBLE and DRAINABLE
instead of a silent parking lot (live: odoo-erp montalu3 grew to W 34 while the
armed /goal loop kept dispatching new I lanes):

  1. A shared W-drain threshold constant (`OPS_WAIT_WDRAIN_THRESHOLD` in
     cli_quals, `WDRAIN_ESCALATE_N` in the watchdog nudge) — locked equal so the
     CLI summary marker and the job-20 escalation fire at the SAME |W|.
  2. `cli_quals_cmd._ops_wait_summary_line` — a `#`-prefixed stdout summary line
     (total / oldest member / stale·recheck·gk-handoff counts / OVER-THRESHOLD
     marker) appended to `--ops-wait` output.
  3. `airuleset._watchdog_ops_wait_fetch` SKIPS the `#`-comment summary line
     (never trips the malformed→None guard that would drop the whole nudge).
  4. `watchdog.ops_wait_recheck._flag_items` / `_nudge_text` carry an aggregate
     `W-OVERFLOW` escalation clause (drain PRED novým I workom; ak sa nedá
     skonsolidovať → zhrň ownerovi ❓) when `|W| > WDRAIN_ESCALATE_N`.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_quals_cmd  # noqa: E402
import watchdog.ops_wait_recheck as owr  # noqa: E402

NOW = 1_000_000


def _mem(num, title, **kw):
    d = {"number": num, "stale": False, "gk_handoff": False, "title": title}
    d.update(kw)
    return d


def _row(num, created="2026-08-01T00:00:00Z"):
    return {"number": num, "title": "t%d" % num, "createdAt": created,
            "labels": [{"name": "ops-wait"}]}


# --------------------------------------------------------------------------- #
# 1. Shared threshold, locked across the two modules.
# --------------------------------------------------------------------------- #

class ThresholdConstant(unittest.TestCase):
    def test_canonical_value_is_8(self):
        self.assertEqual(cli_quals.OPS_WAIT_WDRAIN_THRESHOLD, 8)

    def test_reexported_on_airuleset(self):
        self.assertEqual(airuleset.OPS_WAIT_WDRAIN_THRESHOLD, 8)

    def test_watchdog_constant_locked_to_canonical(self):
        # No drift: the CLI summary marker and the job-20 escalation must fire
        # at the SAME |W|.
        self.assertEqual(owr.WDRAIN_ESCALATE_N,
                         cli_quals.OPS_WAIT_WDRAIN_THRESHOLD)


# --------------------------------------------------------------------------- #
# 2. CLI summary line.
# --------------------------------------------------------------------------- #

class SummaryLine(unittest.TestCase):
    def test_none_when_empty(self):
        self.assertIsNone(
            cli_quals_cmd._ops_wait_summary_line({}, set(), set(), set()))

    def test_comment_prefixed(self):
        line = cli_quals_cmd._ops_wait_summary_line(
            {1: _row(1)}, set(), set(), set())
        self.assertTrue(line.startswith("#"),
                        "summary MUST be a #-comment so the watchdog fetch skips it")

    def test_total_and_flag_counts(self):
        ow = {n: _row(n) for n in (1, 2, 3)}
        line = cli_quals_cmd._ops_wait_summary_line(
            ow, stale_numbers={1, 2}, recheck_numbers={3}, gk_handoff_numbers={1})
        self.assertIn("total=3", line)
        self.assertIn("stale=2", line)
        self.assertIn("recheck=1", line)
        self.assertIn("gk-handoff=1", line)

    def test_oldest_member_by_created(self):
        ow = {5: _row(5, "2026-08-10T00:00:00Z"),
              9: _row(9, "2026-07-01T00:00:00Z")}  # #9 is oldest
        line = cli_quals_cmd._ops_wait_summary_line(ow, set(), set(), set())
        self.assertIn("oldest=#9", line)

    def test_over_threshold_marker_only_above(self):
        under = {n: _row(n) for n in range(1, cli_quals.OPS_WAIT_WDRAIN_THRESHOLD + 1)}
        self.assertNotIn(
            "OVER-THRESHOLD",
            cli_quals_cmd._ops_wait_summary_line(under, set(), set(), set()))
        over = {n: _row(n) for n in range(1, cli_quals.OPS_WAIT_WDRAIN_THRESHOLD + 2)}
        self.assertIn(
            "OVER-THRESHOLD",
            cli_quals_cmd._ops_wait_summary_line(over, set(), set(), set()))


# --------------------------------------------------------------------------- #
# 3. The watchdog fetch skips the #-comment summary line.
# --------------------------------------------------------------------------- #

class FetchSkipsSummary(unittest.TestCase):
    def _fetch(self, out):
        import subprocess
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with m.patch("subprocess.run", return_value=cp), \
                m.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                m.patch.object(airuleset, "resolve_authority",
                               lambda cwd=None: "full"):
            return airuleset._watchdog_ops_wait_fetch("/r")

    def test_summary_line_ignored_not_malformed(self):
        out = ("41\t2026-08-01T00:00:00Z\timplement\tops-wait\tklient\n"
               "# W-summary: total=1 oldest=#41 stale=0 recheck=0 gk-handoff=0\n")
        members = self._fetch(out)
        self.assertIsNotNone(members, "the #-summary line must NOT trip malformed→None")
        self.assertEqual([mm["number"] for mm in members], [41])

    def test_still_none_on_real_malformed(self):
        out = ("41\t2026-08-01T00:00:00Z\timplement\tops-wait\tklient\n"
               "garbage-not-an-int-and-not-a-comment\n")
        self.assertIsNone(self._fetch(out))


# --------------------------------------------------------------------------- #
# 4. The aggregate W-OVERFLOW escalation clause.
# --------------------------------------------------------------------------- #

class WOverflowFlag(unittest.TestCase):
    def _members(self, k):
        return [_mem(100 + i, "klient nepotvrdil") for i in range(k)]

    def test_flag_absent_at_or_below_threshold(self):
        items = owr._flag_items(self._members(owr.WDRAIN_ESCALATE_N), None)
        self.assertFalse(any("W-OVERFLOW" in it for it in items))

    def test_flag_present_above_threshold(self):
        items = owr._flag_items(self._members(owr.WDRAIN_ESCALATE_N + 1), None)
        self.assertTrue(any("W-OVERFLOW" in it for it in items),
                        "an over-threshold W bucket must fire the aggregate flag")

    def test_flag_is_first_most_urgent(self):
        # STALE etc. would otherwise dominate; the aggregate drain signal leads.
        many = [_mem(100 + i, "klient", stale=True)
                for i in range(owr.WDRAIN_ESCALATE_N + 1)]
        items = owr._flag_items(many, None)
        self.assertIn("W-OVERFLOW", items[0])

    def test_nudge_text_carries_overflow_clause_and_drain_doctrine(self):
        members = self._members(owr.WDRAIN_ESCALATE_N + 1)
        seen = {str(mm["number"]): float(NOW) for mm in members}
        t = owr._nudge_text(0, members, NOW, seen)
        self.assertIn("W-OVERFLOW", t)
        self.assertIn("#754", t)          # doctrine pointer
        self.assertIn("W-drain", t)       # drain PRED novým I workom
        self.assertIn("❓", t)            # owner escalation route

    def test_nudge_text_no_clause_below_threshold(self):
        members = self._members(owr.WDRAIN_ESCALATE_N)
        seen = {str(mm["number"]): float(NOW) for mm in members}
        self.assertNotIn("W-OVERFLOW", owr._nudge_text(0, members, NOW, seen))

    def test_overflow_survives_cap_worst_case(self):
        # The aggregate drain signal must NEVER be the item the greedy
        # NUDGE_MAX_CHARS cap drops — even at I>0 with every per-category flag
        # firing on a release-shaped, all-stale, all-recheck, all-gk bucket.
        members = [{"number": 100 + i, "title": "release 2.180 stage-3",
                    "stale": True, "gk_handoff": True, "release_recheck": True}
                   for i in range(owr.WDRAIN_ESCALATE_N + 1)]
        seen = {str(mm["number"]): float(NOW) for mm in members}
        t = owr._nudge_text(5, members, NOW, seen,
                            release_landed=[mm["number"] for mm in members],
                            discuss_audit=True)
        self.assertLessEqual(len(t), owr.NUDGE_MAX_CHARS)
        self.assertIn("W-OVERFLOW", t)


if __name__ == "__main__":
    unittest.main()
