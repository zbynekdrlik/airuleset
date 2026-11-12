"""#1309: the gk review goal requires its own count at 0 and works its own tickets."""
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
                      "pracuj v každom cykle", line)
        self.assertIn("čakanie na waitery nie je práca", line)


    def test_pre_1309_review_line_stays_recognised(self):
        line, _ = self._review_b()
        old = pre_1309(line, "review")
        self.assertNotIn("#1309", old)
        self.assertIn(old, gr.all_goal_line_variants())
