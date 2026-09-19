"""#1083 — `_split_merged_unreleased` pulls merged-to-develop tickets OUT of I/W
into the `M` bucket, unless a U-class label (owner's court) or `prio:bounce`
(rework) keeps them where they are.

RED-first: `_split_merged_unreleased` does not exist on the base tree.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals


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
        workable, waiting, ops_wait = cli_quals._partition_workable(rows)
        w2, o2, m2 = cli_quals._split_merged_unreleased(workable, ops_wait, merged)
        return w2, waiting, o2, m2

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

    def test_merged_needs_acceptance_plus_ops_wait_stays_W(self):
        # needs-acceptance+ops-wait -> W by #526; U-class label -> NOT pulled
        # into M -> stays in W (waiting on the client, not "ready for cut").
        w, u, o, m = self._partition_then_split(
            {50}, (50, "needs-acceptance", "ops-wait"))
        self.assertIn(50, o)
        self.assertNotIn(50, m)
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
        w2, o2, m2 = cli_quals._split_merged_unreleased(
            dict(workable), dict(ops_wait), set())
        self.assertEqual(w2, workable)
        self.assertEqual(o2, ops_wait)
        self.assertEqual(m2, {})

    def test_merged_number_not_in_any_bucket_is_ignored(self):
        # a merged number that is not among the open rows contributes nothing
        # (intersection with the open partition happens by construction).
        w, u, o, m = self._partition_then_split(
            {999}, (80, "stream:montalu1"))
        self.assertEqual(m, {})
        self.assertIn(80, w)


if __name__ == "__main__":
    unittest.main()
