"""#1399 review: a REOPENED ticket whose fix is on main but not told live
(PENDING, M) must stay workable in I: the reopen says more work is owed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_ticket_facts as tf  # noqa: E402
import cli_ticket_state as ts  # noqa: E402


class PendingReopened(unittest.TestCase):

    def test_reopened_pending_stays_i(self):
        facts = tf._facts((), {}, {}, {7: ts.PENDING, 8: ts.PENDING}, reopened={7})
        self.assertEqual(ts.classify({"labels": []}, facts.of(7), ts.Box(), number=7)[0], "I")
        self.assertEqual(ts.classify({"labels": []}, facts.of(8), ts.Box(), number=8)[0], "M")

    def test_reopened_is_read_for_pending_candidates(self):
        import inspect
        src = inspect.getsource(tf.refresh)
        call = src[src.index("reopened = read_reopened("):][:250]
        self.assertIn("ts.PENDING", call, "a PENDING ticket's reopen must be read")


if __name__ == "__main__":
    unittest.main()
