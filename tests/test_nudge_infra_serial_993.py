"""#992 req 1 / #993 item 2 — the lane-occupancy + queue-arrival nudges must
state that refill applies ONLY to independent units: infra work is SERIAL, so
when the only workable tickets are infra and one infra lane is live, NO refill.
The nudges must SAY this instead of unconditionally pushing dispatch.

Phrase-locked (the nudge is FACTS + doctrine, never a count/priority prescription
— #994): a partial revert of the clause fails these.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import watchdog.lane_resources as lr  # noqa: E402
import watchdog.queue_arrival_recheck as qa  # noqa: E402


class TestLaneNudgeInfraSerial(TestCase):
    def test_lane_nudge_states_infra_serial_no_refill(self):
        t = lr._lane_nudge_text(4, 1, {"total": 5}).lower()
        self.assertIn("infra", t)
        self.assertIn("sériov", t)          # SÉRIOVÉ (serial)
        self.assertIn("nerefill", t)        # no refill for infra-only + live infra lane


class TestQueueNudgeInfraSerial(TestCase):
    def test_queue_nudge_states_infra_serial_no_refill(self):
        t = qa._nudge_text([5177, 5310], 8).lower()
        self.assertIn("infra", t)
        self.assertIn("sériov", t)
        self.assertIn("nerefill", t)

    def test_queue_nudge_stays_within_cap(self):
        # #978: a folded clause must not push the nudge past NUDGE_MAX_CHARS.
        t = qa._nudge_text([5177, 5310], 8)
        self.assertLessEqual(len(t), qa.NUDGE_MAX_CHARS)


if __name__ == "__main__":
    main()
