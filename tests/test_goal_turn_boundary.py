"""Goal-template turn-boundary fix (#58, david #2129 live incident).

Root cause: `skills/autopilot/SKILL.md` Step 5 already said "report the batch,
then STOP the turn — the ARMED GOAL, not `⏳`, continues the loop" — but all
THREE `/goal` templates (Step 2), the text a running loop actually re-reads
every turn, ended with "After every merge|hand-off immediately pick the next
issue." A model reading "immediately" every turn dispatched the next ticket in
the SAME turn, ended `⏳ WORKING`, and the ticket-boundary `/compact` hook
(which only fires on a terminal `✅ DONE` / `## ✅ Work Complete`, never on
`⏳`) never got a chance to fire — context grew unbounded across the whole
backlog instead of compacting per ticket.

Fix: all three templates now say "immediately" in a way that explicitly means
the NEXT TURN — END the turn with the full completion report, do NOT dispatch
in the same turn, the ARMED GOAL fires next turn. Step 5 also now explicitly
names the reduced-authority (branch-merge / fork-no-merge) hand-off shape so a
sub-dev stream recognizes the mandate applies to it too.
"""

import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = "skills/autopilot/SKILL.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def goal_lines():
    lines = re.findall(r"^/goal STOP CONDITIONS.*$", read(SKILL), re.MULTILINE)
    return lines


class TestGoalTemplatesEndTurnBeforeNextTicket(TestCase):
    def test_all_three_templates_present(self):
        self.assertEqual(len(goal_lines()), 3)

    def test_no_template_says_bare_immediately_pick_the_next(self):
        # The exact broken phrase (from EITHER the "merge" or "hand-off"
        # variant) must be entirely gone -- replaced with turn-boundary text.
        for line in goal_lines():
            self.assertNotIn("immediately pick the next issue.", line)
            self.assertNotIn("immediately pick the next assigned issue.", line)

    def test_every_template_ends_the_turn_before_the_next_ticket(self):
        # #848 CONTINUOUS REFILL retires the #723 drained-batch-boundary tail: the
        # compact fires at EVERY integration cycle, live lanes or not. #741's
        # HOLD-until-delivered ordering half survives (the armed goal does NOT
        # dispatch a new lane before the compact runs).
        for line in goal_lines():
            self.assertIn("END the turn", line)
            self.assertIn("HOLD each later goal turn until that compact runs", line)
            self.assertIn("✅ DONE:", line)
            # the retired batch-boundary tail must be gone
            self.assertNotIn("WHOLE batch has returned", line)
            self.assertNotIn("ZERO live tasks", line)
            self.assertNotIn("do NOT integrate a SECOND branch this turn", line)
            self.assertNotIn("do NOT hand off a SECOND branch this turn", line)

    def test_every_template_holds_before_a_new_lane(self):
        # #848: the tail HOLDS each later goal turn until the compact runs,
        # dispatching no NEW LANE first (continuous refill; was "no next batch").
        for line in goal_lines():
            self.assertIn("no new lane first", line)
            self.assertNotIn("no next batch first", line)
            self.assertNotIn("compacting then dispatching the next batch", line)

    def test_full_authority_references_completion_report(self):
        full = goal_lines()[0]
        self.assertIn("completion-report.md", full)
        self.assertIn("After EVERY integration", full)

    def test_branch_merge_references_its_reduced_authority_variant(self):
        branch_merge = goal_lines()[1]
        self.assertIn("completion-report.md", branch_merge)
        # #621 tightened "branch-merge reduced-authority variant" -> the shorter
        # "the branch-merge variant"; the completion-report.md reference (which
        # variant to use) is preserved.
        self.assertIn("the branch-merge variant", branch_merge)
        self.assertIn("After EVERY integration", branch_merge)

    def test_fork_no_merge_references_its_hand_off_variant(self):
        fork = goal_lines()[2]
        self.assertIn("completion-report.md", fork)
        self.assertIn("the fork-no-merge variant", fork)
        self.assertIn("READY-FOR-REVIEW", fork)
        self.assertIn("After EVERY hand-off", fork)

    def test_generic_phrase_lock_immediately_must_pair_with_end_the_turn(self):
        # Forward-looking guard: if a future edit reintroduces "immediately
        # pick the next" anywhere in a /goal template line, it MUST be
        # accompanied by "END the turn" on the SAME line -- never bare.
        for line in goal_lines():
            if "immediately pick the next" in line:
                self.assertIn("END the turn", line)


class TestStep5NamesReducedAuthority_Shape(TestCase):
    def test_step5_mentions_branch_merge_and_fork_no_merge(self):
        t = read(SKILL)
        idx = t.index("5. **Report each COMPLETED INTEGRATION CYCLE")
        step5 = t[idx:idx + 3600]
        self.assertIn("branch-merge", step5)
        self.assertIn("fork-no-merge", step5)
        self.assertIn("completion-report.md", step5)

    def test_step5_names_the_hand_off_fields_not_just_merge_fields(self):
        t = read(SKILL)
        idx = t.index("5. **Report each COMPLETED INTEGRATION CYCLE")
        step5 = t[idx:idx + 3600]
        self.assertIn("READY-FOR-REVIEW", step5)
        self.assertIn("Lokálne overenie", step5)

    def test_step5_references_the_2129_incident(self):
        t = read(SKILL)
        idx = t.index("5. **Report each COMPLETED INTEGRATION CYCLE")
        step5 = t[idx:idx + 3600]
        self.assertIn("2129", step5)


if __name__ == "__main__":
    main()
