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
        # #723 BATCH mode reconciled the tail: the compact fires ONLY at the
        # DRAINED batch boundary (whole batch returned + integrated = zero live
        # tasks). #741 REVERSES the ordering half: the armed goal no longer
        # "fires the NEXT TURN, compacting then dispatching the next batch" (an
        # order nothing enforced); it HOLDS until the compact is delivered.
        # Its twin in test_goal_backlog_proof.py was reconciled the same way.
        for line in goal_lines():
            self.assertIn("END the turn", line)
            self.assertIn("WHOLE batch has returned", line)
            self.assertIn("ZERO live tasks", line)
            self.assertIn("HOLD each later goal turn until that compact runs", line)
            self.assertIn("✅ DONE:", line)
            # the superseded serializing/continuous tails must be gone
            self.assertNotIn("do NOT integrate a SECOND branch this turn", line)
            self.assertNotIn("do NOT hand off a SECOND branch this turn", line)

    def test_every_template_explains_the_compaction_benefit(self):
        # #741: the tail no longer asserts the unenforced "compacting then
        # dispatching the next batch" ordering; it HOLDS each later goal turn
        # until the compact runs, dispatching no next batch first.
        for line in goal_lines():
            self.assertIn("no next batch first", line)
            self.assertNotIn("compacting then dispatching the next batch", line)

    def test_full_authority_references_completion_report(self):
        full = goal_lines()[0]
        self.assertIn("completion-report.md", full)
        self.assertIn("After each integration", full)

    def test_branch_merge_references_its_reduced_authority_variant(self):
        branch_merge = goal_lines()[1]
        self.assertIn("completion-report.md", branch_merge)
        # #621 tightened "branch-merge reduced-authority variant" -> the shorter
        # "the branch-merge variant"; the completion-report.md reference (which
        # variant to use) is preserved.
        self.assertIn("the branch-merge variant", branch_merge)
        self.assertIn("After each integration", branch_merge)

    def test_fork_no_merge_references_its_hand_off_variant(self):
        fork = goal_lines()[2]
        self.assertIn("completion-report.md", fork)
        self.assertIn("the fork-no-merge variant", fork)
        self.assertIn("READY-FOR-REVIEW", fork)
        self.assertIn("After each hand-off", fork)

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
