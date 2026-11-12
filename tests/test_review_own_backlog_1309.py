"""#1309: the gk review goal requires its own count at 0 and works its own tickets."""
import hashlib
from unittest import TestCase

import goal_registry as gr
from goal_legacy_1309 import pre_1309
# --------------------------------------------------------------------------- #
# #1309 (owner 12.11.2026): gk's review window spent a whole night reporting
# release/hand-off waiters while `core-quals --role review --count` printed 5
# every cycle. The review (B) never required that count to be 0 and nothing
# told the window to WORK its own tickets, so "print the count" was the whole
# obligation. The count is now part of (B) and the window works it each cycle.
# --------------------------------------------------------------------------- #
class TestReviewOwnBacklog1309(TestCase):
    def _review_b(self):
        line = gr.render_goal_line("full", "review")
        start = line.index("(B) BACKLOG EMPTY — gk REVIEW okno")
        end = line.index("REVIEW ROLE —")
        return line, line[start:end]

    def test_review_B_requires_own_count_zero(self):
        _, b = self._review_b()
        self.assertIn("`core-quals --role review --count` == 0", b)

    def test_review_window_works_its_own_tickets_each_cycle(self):
        line, _ = self._review_b()
        self.assertIn("vlastné I tickety (`core-quals --role review --list`) "
                      "pracuj každý cyklus", line)
        self.assertIn("waiter nie je práca", line)
        # review round 2: hand-off SLAs first, dep-wait rows get unblocked
        self.assertIn("po (b)/(c)", line)
        self.assertIn("dep-wait odblokuj či založ", line)


    def test_pre_1309_review_line_stays_recognised(self):
        line, _ = self._review_b()
        old = pre_1309(line, "review")
        self.assertNotIn("#1309", old)
        self.assertIn(old, gr.all_goal_line_variants())

    def test_pre_1309_reverses_to_the_frozen_shipped_line(self):
        # sha256 of render_goal_line("full", "review") at 8f4c791d (0.1.638),
        # the text armed in gk's live loop before #1309. A later edit of either
        # replaced substring would make pre_1309 a silent no-op; this catches it.
        line, _ = self._review_b()
        old = pre_1309(line, "review")
        self.assertEqual(
            hashlib.sha256(old.encode()).hexdigest(),
            "644e95bf176d7476dc25b5264a23349cec87655fa81001c0532b3e1054908674")
