"""#1083 — a merged-to-develop ticket leaves I/W for `M` unless `prio:bounce`
keeps it in I; an owner question stays U. #1141 slice 3: the M step is part
of the ONE route (`bucketize`), and the
separate U-label veto is gone (an owner question is decided first), so a sent
acceptance (needs-acceptance + ops-wait, W) that is merged now leaves for M.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals
import cli_ticket_state as ts


def _labels(*names):
    return [{"name": n} for n in names]


def _rows(*specs):
    out = {}
    for number, *names in specs:
        out[number] = {"number": number, "labels": _labels(*names)}
    return out


class SplitMergedUnreleased(unittest.TestCase):
    def _partition_then_split(self, merged, *specs):
        rows = _rows(*specs)
        b = ts.bucketize(rows, ts.TicketFacts(merged=frozenset(merged)),
                         ts.Box())
        return b["I"], b["U"], b["W"], b["M"]

    def test_plain_merged_ticket_leaves_I_into_M(self):
        w, u, o, m = self._partition_then_split(
            {10}, (10, "stream:montalu1"), (11, "stream:montalu1"))
        self.assertIn(10, m)
        self.assertNotIn(10, w)
        self.assertIn(11, w)          # not merged -> stays I
        self.assertNotIn(11, m)

    def test_merged_ops_wait_ticket_leaves_W_into_M(self):
        # ops-wait, not U-class, not bounce -> M (release-ready, not W).
        w, u, o, m = self._partition_then_split(
            {20}, (20, "ops-wait"))
        self.assertIn(20, m)
        self.assertNotIn(20, o)
        self.assertNotIn(20, w)

    def test_merged_bounce_stays_I(self):
        # prio:bounce = gk returned it for rework -> stays workable, NEVER M.
        w, u, o, m = self._partition_then_split(
            {30}, (30, "stream:montalu1", "prio:bounce"))
        self.assertIn(30, w)
        self.assertNotIn(30, m)

    def test_merged_bounce_plus_ops_wait_stays_I(self):
        w, u, o, m = self._partition_then_split(
            {31}, (31, "stream:montalu1", "prio:bounce", "ops-wait"))
        self.assertIn(31, w)         # bounce override already pulled it to I
        self.assertNotIn(31, m)
        self.assertNotIn(31, o)

    def test_merged_needs_decision_stays_U(self):
        w, u, o, m = self._partition_then_split(
            {40}, (40, "needs-decision"))
        self.assertIn(40, u)         # U-class -> owner's court wins
        self.assertNotIn(40, m)
        self.assertNotIn(40, w)

    def test_merged_needs_acceptance_plus_ops_wait_leaves_W_into_M(self):
        # #1141 slice 3 ruling: the U veto is gone — a sent acceptance is not
        # an owner question, and a merged one waits for the release cut (M).
        w, u, o, m = self._partition_then_split(
            {50}, (50, "needs-acceptance", "ops-wait"))
        self.assertIn(50, m)
        self.assertNotIn(50, o)
        self.assertNotIn(50, u)

    def test_merged_needs_gatekeeper_leaves_I_into_M(self):
        # a hand-off label is NOT U-class and NOT bounce -> merged -> M
        # (nothing for gk to act on until the cut).
        w, u, o, m = self._partition_then_split(
            {60}, (60, "needs-gatekeeper"))
        self.assertIn(60, m)
        self.assertNotIn(60, w)

    def test_empty_merged_set_is_noop(self):
        rows = _rows((70, "stream:montalu1"), (71, "ops-wait"),
                     (72, "needs-decision"))
        workable, waiting, ops_wait = cli_quals._partition_workable(rows)
        b = ts.bucketize(rows, ts.TicketFacts(), ts.Box())
        self.assertEqual(b["I"], workable)
        self.assertEqual(b["W"], ops_wait)
        self.assertEqual(b["M"], {})

    def test_merged_number_not_in_any_bucket_is_ignored(self):
        # a merged number that is not among the open rows contributes nothing
        # (intersection with the open partition happens by construction).
        w, u, o, m = self._partition_then_split(
            {999}, (80, "stream:montalu1"))
        self.assertEqual(m, {})
        self.assertIn(80, w)


if __name__ == "__main__":
    unittest.main()
