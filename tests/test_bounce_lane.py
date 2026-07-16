"""Locks the prio:bounce priority lane (odoo-erp #1599, user request 2026-07-16).

Incident: gatekeeper /process-subdev and a sub-dev's running autopilot could not
run concurrently — gatekeeper findings arrived as raw tmux prompts that would
derail the running /goal loop, so the user serialized the two streams and dozens
of sub-dev tickets rotted. The convention (odoo-erp #1599 + PR #1600): findings
are filed as tickets labeled `prio:bounce` (+ `stream:<name>`), full content ON
the ticket; the tmux message is only a short nudge. The autopilot skill must:
(1) seed every NEW batch from open `prio:bounce` tickets FIRST (oldest first),
    never preempting a running batch;
(2) on an injected nudge, ACK + ensure the label + let the loop take the ticket
    next turn — never work the finding inline;
(3) keep the label a GENERIC cross-repo convention (no odoo-specific hardcode);
(4) the worker removes the label at its done-point so a resolved bounce leaves
    the lane automatically.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSkillPriorityLane(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def test_seed_ordering_takes_bounce_first_oldest_first(self):
        t = read(self.SKILL)
        self.assertIn("prio:bounce", t)
        self.assertIn("PRIORITY LANE", t)
        self.assertIn("OLDEST open `prio:bounce`", t)

    def test_running_batch_is_never_preempted(self):
        self.assertIn("NEVER preempted", read(self.SKILL))

    def test_all_three_goal_templates_carry_the_bounce_lane(self):
        # The /goal line is the durable engine text (survives compaction, the
        # skill body may not) — every authority profile's template must carry
        # the lane, or a compacted loop silently loses the ordering.
        goal_lines = re.findall(r"^/goal STOP CONDITIONS.*$",
                                read(self.SKILL), re.MULTILINE)
        self.assertEqual(len(goal_lines), 3)
        for line in goal_lines:
            self.assertIn("prio:bounce", line)

    def test_nudge_ack_never_works_the_finding_inline(self):
        t = read(self.SKILL)
        self.assertIn("nudge", t.lower())
        self.assertIn("NEVER start working the finding inline", t)

    def test_nudge_ensures_the_label_best_effort(self):
        t = read(self.SKILL)
        self.assertIn("gh label create prio:bounce", t)
        self.assertIn("--add-label prio:bounce", t)

    def test_label_is_a_generic_cross_repo_convention(self):
        self.assertIn("cross-repo convention", read(self.SKILL))


class TestWorkerClearsBounceLabel(TestCase):
    def test_worker_removes_bounce_label_at_done_point(self):
        self.assertIn("--remove-label prio:bounce",
                      read("agents/autopilot-worker.md"))


if __name__ == "__main__":
    main()
