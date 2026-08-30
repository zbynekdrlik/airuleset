"""#670 — lane-check nudge dedup on UNCHANGED state.

The hourly cap (`GOAL_LANE_INTERVAL_S`) already holds (~1/h per pane), but past
the cap an IDENTICAL `(workers, backlog)` re-nudged every hour — the owner's
"kazdu chvilu" (live dev1: `workers=0 backlog=22` delivered at 10:34 AND 11:42).
The fix adds a dedup: past the cap, an unchanged lane-state signature never
re-nudges; only a genuinely CHANGED state (or the very first nudge) proceeds,
still subject to the 1h floor. Pure-decider tests on `_lane_cooldown_decision`
(the cadence gate) + `_lane_record_nudge` (which stamps the signature), the same
level the #571 one_glance deciders are locked at.

#729: the #509 under-saturated effectiveness backoff branch of the cadence gate
was DELETED (reachable only from the #726-retired fill nudge), so these locks now
exercise the single un-branched hourly-cap + #670 dedup gate.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog.goal as goal  # noqa: E402

HOUR = goal.GOAL_LANE_INTERVAL_S       # 3600
NOW = 1_000_000_000.0                  # a fixed, mid-week-agnostic wall clock


def _decide(rec, live_workers, backlog_n):
    """Call the cadence gate with the fixed NOW and stable loc/count args.
    #729: the under_saturated/effectiveness branch is gone -- the single
    un-branched hourly-cap + #670 dedup gate."""
    return goal._lane_cooldown_decision(
        rec, NOW, backlog_n, loc="zbynek:1.0", live_workers=live_workers,
        waiters=1)


class DedupUnchangedState(unittest.TestCase):
    def test_empty_lane_unchanged_past_cooldown_is_suppressed(self):
        # A landed nudge >1h ago at (workers=0, backlog=22); the state is
        # STILL (0, 22). Past the hourly cap, an unchanged signature must NOT
        # re-nudge — the exact live 10:34/11:42 repetition.
        rec = {"llast": NOW - 2 * HOUR, "lsw": 0, "lsb": 22}
        skip, log = _decide(rec, live_workers=0, backlog_n=22)
        self.assertTrue(skip, "unchanged state past the cooldown must be deduped")
        self.assertIn("dedup-unchanged", log or "")

    def test_changed_backlog_past_cooldown_still_nudges(self):
        # A genuinely CHANGED state (backlog 22 -> 21) past the cap is allowed
        # to nudge — dedup keys on the (workers, backlog) signature only.
        rec = {"llast": NOW - 2 * HOUR, "lsw": 0, "lsb": 22}
        skip, log = _decide(rec, live_workers=0, backlog_n=21)
        self.assertFalse(skip, "a changed backlog must re-nudge (log=%r)" % (log,))

    def test_changed_worker_count_past_cooldown_still_nudges(self):
        # workers 0 -> 1 (a lane appeared) is a changed signature -- the dedup
        # keys on (workers, backlog), so it must re-nudge.
        rec = {"llast": NOW - 2 * HOUR, "lsw": 0, "lsb": 10}
        skip, _ = _decide(rec, live_workers=1, backlog_n=10)
        self.assertFalse(skip, "a changed worker count must re-nudge")

    def test_within_cooldown_still_hourly_capped(self):
        # The 1h floor is untouched: within the cooldown, an unchanged state is
        # still skip:hourly-cap (NOT dedup — the floor wins first).
        rec = {"llast": NOW - HOUR // 2, "lsw": 0, "lsb": 22}
        skip, log = _decide(rec, live_workers=0, backlog_n=22)
        self.assertTrue(skip)
        self.assertIn("hourly-cap", log or "")

    def test_first_ever_nudge_never_deduped(self):
        # No prior landed nudge (llast None) -> the very first nudge always
        # fires, regardless of any stray signature keys.
        rec = {}
        skip, _ = _decide(rec, live_workers=0, backlog_n=22)
        self.assertFalse(skip)

    def test_pre_deploy_rec_without_signature_grace_nudges_once(self):
        # A rec that landed a nudge BEFORE this fix has llast but no lsw/lsb.
        # None != an int, so dedup no-ops -> one grace nudge fires (then
        # _lane_record_nudge stamps lsw/lsb and dedup engages next time).
        rec = {"llast": NOW - 2 * HOUR}
        skip, _ = _decide(rec, live_workers=0, backlog_n=22)
        self.assertFalse(skip, "a pre-deploy rec must not be silently deduped")


class RecordStampsSignature(unittest.TestCase):
    def test_record_writes_signature(self):
        # A landed nudge stamps lsw/lsb (the dedup signature) unconditionally.
        rec = {}
        goal._lane_record_nudge(rec, live_workers=0, backlog_n=22, n=0, now=NOW)
        self.assertEqual((rec.get("lsw"), rec.get("lsb")), (0, 22))

    def test_record_then_decide_dedups_the_same_state(self):
        # End-to-end at the decider level: record a nudge, then a later sweep
        # with the SAME state past the cap is deduped.
        rec = {}
        goal._lane_record_nudge(rec, live_workers=0, backlog_n=22, n=0,
                                now=NOW - 2 * HOUR)
        skip, log = _decide(rec, live_workers=0, backlog_n=22)
        self.assertTrue(skip)
        self.assertIn("dedup-unchanged", log or "")


if __name__ == "__main__":
    unittest.main()
