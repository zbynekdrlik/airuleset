"""#993 item 4 (+ round 2b) — the queue-arrival nudge DECISION is DEPENDENCY
aware: a dep-wait arrival is HELD, not nudged. (#1137 removed the lane-occupancy
nudge and its candidate gate.) The class-based infra-serial half was removed in round 2b (infra
serialisation is ROUTING via `--role`, not a live-lane gate), so this file no
longer tests any infra-serial nudge behaviour.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import watchdog.queue_arrival_recheck as qa  # noqa: E402


NOW = 1_000_000


class TestQueueDecisionDepAware(TestCase):
    """#993 item 4 — the queue-arrival DECISION filters arrivals by dispatch
    class: a dep-wait arrival is HELD (no nudge); the nudge fires only for
    dispatchable arrivals."""

    def _base(self, base):
        return {"base": base, "first_seen": NOW - 100, "last_nudge": None}

    def test_no_classify_fn_is_legacy_all_dispatchable(self):
        action, _out, _r, arr = qa._queue_decision(self._base([1]), [1, 9], NOW)
        self.assertEqual(action, "nudge")
        self.assertEqual(arr, [9])

    def test_all_dep_wait_arrivals_hold_with_dep_wait_reason(self):
        action, out, reason, arr = qa._queue_decision(
            self._base([1]), [1, 9], NOW,
            classify_fn=lambda n: "dep-wait")
        self.assertEqual(action, "hold")
        self.assertEqual(reason, "dep-wait")
        self.assertEqual(out["base"], [1])   # base kept OLD → re-detect later
        self.assertEqual(arr, [9])

    def test_mixed_nudges_only_dispatchable_arrivals(self):
        cls = {8: "dispatchable", 9: "dep-wait"}
        action, _out, _r, arr = qa._queue_decision(
            self._base([1]), [1, 8, 9], NOW,
            classify_fn=lambda n: cls[n])
        self.assertEqual(action, "nudge")
        self.assertEqual(arr, [8])          # only the dispatchable arrival named


if __name__ == "__main__":
    main()
