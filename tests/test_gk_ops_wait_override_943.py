"""#943 — `needs-gatekeeper`/`ready-for-review` + `ops-wait` partition override.

A ticket carrying a MAINTAINER_ACTION_LABELS label AND `ops-wait` (but no
user-waiting label) must land in `workable` (action-only I), not `ops_wait` (W).
Only the full-authority box can action a hand-off, so the hand-off label
overrides ops-wait — the #589/#636 over-count-safe direction.

Reduced-authority slice behaviour is LOCKED (unchanged): a sub-dev's own
slice never carries a `needs-gatekeeper` row from the partition's perspective
(those rows are search-excluded from the obligation set or counted via the
`_slice_mine_and_handed` `gk` bucket), so the override is structurally inert
there — but we lock the expectation explicitly.
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import airuleset
from cli_quals import (
    _partition_workable, _row_is_user_waiting, MAINTAINER_ACTION_LABELS,
)


def _labels(*names):
    return [{"name": n} for n in names]


def _row(number, *label_names):
    return {number: {"number": number, "labels": _labels(*label_names)}}


class TestGkOpsWaitOverride943(unittest.TestCase):
    """needs-gatekeeper + ops-wait → workable (I), not ops_wait (W)."""

    def test_needs_gatekeeper_plus_ops_wait_is_workable(self):
        """The incident case: needs-gatekeeper + ops-wait routed to W for 2
        days. It must route to workable (I) — the gatekeeper must action it."""
        rows = _row(6294, "needs-gatekeeper", "ops-wait")
        workable, user_waiting, ops_wait = _partition_workable(rows)
        self.assertIn(6294, workable,
                      "#943: needs-gatekeeper + ops-wait must be workable (I)")
        self.assertNotIn(6294, ops_wait,
                         "#943: needs-gatekeeper + ops-wait must NOT be W")
        self.assertNotIn(6294, user_waiting)

    def test_ready_for_review_plus_ops_wait_is_workable(self):
        """ready-for-review is the sibling MAINTAINER_ACTION_LABELS label."""
        rows = _row(100, "ready-for-review", "ops-wait")
        workable, user_waiting, ops_wait = _partition_workable(rows)
        self.assertIn(100, workable)
        self.assertNotIn(100, ops_wait)

    def test_plain_ops_wait_still_routes_to_w(self):
        """A plain ops-wait ticket (no maintainer label) still goes to W —
        unchanged behaviour."""
        rows = _row(200, "ops-wait")
        workable, user_waiting, ops_wait = _partition_workable(rows)
        self.assertIn(200, ops_wait)
        self.assertNotIn(200, workable)

    def test_user_waiting_plus_ops_wait_routing_unchanged(self):
        """Existing routing for user-waiting labels + ops-wait is unchanged.
        needs-acceptance + ops-wait → W (the #526 acceptance override).
        needs-answer + ops-wait → U (owner beats third-party)."""
        acc_rows = _row(300, "needs-acceptance", "ops-wait")
        _, _, ops_wait = _partition_workable(acc_rows)
        self.assertIn(300, ops_wait, "#526 acceptance+ops-wait→W unchanged")

        ans_rows = _row(301, "needs-answer", "ops-wait")
        _, user_waiting, _ = _partition_workable(ans_rows)
        self.assertIn(301, user_waiting, "needs-answer+ops-wait→U unchanged")

    def test_needs_gatekeeper_is_not_user_waiting(self):
        """Confirm needs-gatekeeper is not in USER_WAITING_LABELS — the
        precondition for this bug: it falls through to the ops-wait branch."""
        self.assertFalse(_row_is_user_waiting(_labels("needs-gatekeeper")))

    def test_gk_override_with_needs_acceptance_still_workable(self):
        """A needs-acceptance + needs-gatekeeper row is already overridden by
        NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS (#507) → stays workable.
        Adding ops-wait to this combo must NOT change the outcome."""
        rows = _row(400, "needs-acceptance", "needs-gatekeeper", "ops-wait")
        workable, user_waiting, ops_wait = _partition_workable(rows)
        self.assertIn(400, workable,
                      "#507 gk override + ops-wait → still workable")

    def test_reduced_authority_lock(self):
        """On a reduced-authority box (own_stream set), the override is
        structurally inert (the rows that reach the partition from
        _slice_mine_and_handed never carry needs-gatekeeper as a foreign
        row). But IF such a row DID appear, it should still land in workable
        (the over-count-safe direction). Lock this expectation."""
        rows = _row(500, "needs-gatekeeper", "ops-wait",
                    "stream:montalu")
        workable, _, ops_wait = _partition_workable(rows, own_stream="david1")
        # The partition is a pure label partition — the override fires
        # regardless of own_stream (by design, per the #943 design comment).
        self.assertIn(500, workable)
        self.assertNotIn(500, ops_wait)


class TestGkHandoffFlagStillWorks(unittest.TestCase):
    """The #636 _gk_handoff_ops_wait_flagged function must still detect
    needs-gatekeeper + ops-wait rows for the nudge text — but with the
    partition fix, these rows now live in `workable` instead of `ops_wait`.
    The flagging function operates on ANY rows dict, so it stays valid."""

    def test_flagging_detects_gk_ops_wait(self):
        from cli_quals import _gk_handoff_ops_wait_flagged
        rows = _row(6294, "needs-gatekeeper", "ops-wait")
        self.assertEqual(_gk_handoff_ops_wait_flagged(rows), {6294})


if __name__ == "__main__":
    unittest.main()
