"""#1056 L2 (i0) / #1057 item 4 — a returned bounce is never hidden in W/U.

The montalu1 read the supervisor cited (2026-09-17 08:3x): five open bounces,
the footer showed `bounce 1`, three sat in W (`prio:bounce` + `needs-acceptance`
+ `ops-wait`) — a false "waiting on a third party". A `prio:bounce` label
unconditionally means the gk returned the ticket and the STREAM must rework it,
so:

  * `_partition_workable` pulls a `prio:bounce` row OUT of the ops-wait/W bucket
    into `workable` (extending the #507 NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS
    precedence — which already keeps such a row out of U — into the W branch);
  * `count_bounce_all` counts EVERY open `prio:bounce` across the full footer
    partition (workable ∪ user-waiting ∪ ops-wait), so a bounce parked on a
    genuine owner-answer (needs-answer + prio:bounce, which deliberately STAYS
    in U — you cannot rework without the answer) is still counted in `bounce K`.

RED-first: on the base tree a bounce+ops-wait row lands in `ops_wait` (W), and
`count_bounce_all` does not exist.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals


def _labels(*names):
    return [{"name": n} for n in names]


def _rows(*specs):
    """specs = (number, *label-names) tuples -> a partition-shaped rows dict."""
    out = {}
    for number, *names in specs:
        out[number] = {"number": number, "labels": _labels(*names)}
    return out


class PartitionPullsBounceOutOfW(unittest.TestCase):
    """The five-ticket montalu1 shape: every prio:bounce row → workable."""

    def _part(self, *specs):
        return cli_quals._partition_workable(_rows(*specs))

    def test_bare_bounce_is_workable(self):
        # #7431 — bare bounce (regression lock: already workable on base).
        w, uw, ow = self._part((7431, "stream:montalu1", "prio:bounce"))
        self.assertIn(7431, w)
        self.assertNotIn(7431, ow)
        self.assertNotIn(7431, uw)

    def test_bounce_plus_ops_wait_is_workable(self):
        # #6413 — bounce + ops-wait: a returned bounce is never "waiting on a
        # third party" (RED on base: routed to ops_wait/W).
        w, uw, ow = self._part((6413, "stream:montalu1", "prio:bounce",
                                "ops-wait"))
        self.assertIn(6413, w,
                      "prio:bounce + ops-wait must be workable (I), not W")
        self.assertNotIn(6413, ow)
        self.assertNotIn(6413, uw)

    def test_bounce_plus_needs_acceptance_plus_ops_wait_is_workable(self):
        # #6824/#6474/#5613 — bounce + needs-acceptance + ops-wait (RED on
        # base: _row_is_user_waiting already excludes it from U via the #507
        # override, then it falls to ops_wait/W). The bounce (rework needed)
        # beats the acceptance framing.
        for n in (6824, 6474, 5613):
            with self.subTest(ticket=n):
                w, uw, ow = self._part(
                    (n, "stream:montalu1", "prio:bounce", "needs-acceptance",
                     "ops-wait"))
                self.assertIn(n, w,
                              "prio:bounce + needs-acceptance + ops-wait must "
                              "be workable (I), not W")
                self.assertNotIn(n, ow)
                self.assertNotIn(n, uw)

    def test_the_whole_montalu1_five_ticket_shape_is_all_workable(self):
        w, uw, ow = self._part(
            (7431, "stream:montalu1", "prio:bounce"),
            (6824, "stream:montalu1", "prio:bounce", "needs-acceptance", "ops-wait"),
            (6474, "stream:montalu1", "prio:bounce", "needs-acceptance", "ops-wait"),
            (5613, "stream:montalu1", "prio:bounce", "needs-acceptance", "ops-wait"),
            (6413, "stream:montalu1", "prio:bounce", "ops-wait"),
        )
        self.assertEqual(set(w), {7431, 6824, 6474, 5613, 6413})
        self.assertEqual(ow, {})
        self.assertEqual(uw, {})

    def test_bounce_plus_needs_answer_stays_in_U(self):
        # A bounce that genuinely needs an OWNER answer stays in U — you cannot
        # rework it without the answer. The #507 override is deliberately scoped
        # to needs-acceptance, NOT needs-answer/needs-decision. (No stream:
        # label, so the #654 foreign-stream carve-out cannot route it to I; this
        # isolates MY change, which only touches the ops-wait branch.)
        w, uw, ow = self._part((999, "prio:bounce", "needs-answer"))
        self.assertIn(999, uw, "prio:bounce + needs-answer stays owner-waiting")
        self.assertNotIn(999, w)
        self.assertNotIn(999, ow)

    def test_a_plain_ops_wait_row_still_lands_in_W(self):
        # Negative control: a non-bounce ops-wait row is untouched.
        w, uw, ow = self._part((500, "stream:montalu1", "ops-wait"))
        self.assertIn(500, ow)
        self.assertNotIn(500, w)


class CountBounceAcrossFullPartition(unittest.TestCase):
    """`count_bounce_all(workable, waiting, ops_wait)` — every open prio:bounce
    regardless of parking bucket."""

    def test_counts_bounce_in_every_bucket(self):
        workable = _rows((1, "prio:bounce"), (2, "enhancement"))
        waiting = _rows((3, "prio:bounce", "needs-answer"))   # U-parked bounce
        ops_wait = _rows((4, "prio:bounce", "ops-wait"), (5, "ops-wait"))
        self.assertEqual(
            cli_quals.count_bounce_all(workable, waiting, ops_wait), 3)

    def test_empty_and_none_buckets(self):
        self.assertEqual(cli_quals.count_bounce_all({}, {}, {}), 0)
        self.assertEqual(cli_quals.count_bounce_all(None, None, None), 0)

    def test_matches_count_bounce_when_all_workable(self):
        # After the partition pull, all bounces are in workable; the union count
        # then equals _count_bounce(workable) — the montalu1 "5 of 5" read.
        workable = _rows(
            (7431, "prio:bounce"), (6824, "prio:bounce"), (6474, "prio:bounce"),
            (5613, "prio:bounce"), (6413, "prio:bounce"))
        self.assertEqual(cli_quals.count_bounce_all(workable, {}, {}), 5)
        self.assertEqual(cli_quals._count_bounce(workable), 5)


if __name__ == "__main__":
    unittest.main()
