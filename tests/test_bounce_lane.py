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


class TestReviewWatchLifecycle(TestCase):
    """2026-07-19 incident: BOTH sides of the gatekeeper↔sub-dev ping-pong
    stall because each side's loop ends while the counterpart still has work
    in flight (4 bounced david tickets sat re-handed-off with no re-review;
    a sub-dev loop that ended at hand-off never picks up later bounces). The
    sub-dev /goal templates must hold the loop ALIVE in an hourly REVIEW-WATCH
    until the gatekeeper closes/releases everything, and a nudge arriving with
    NO armed loop must dispatch the worker directly instead of a dead ACK."""

    SKILL = "skills/autopilot/SKILL.md"

    def reduced_goal_lines(self):
        import re
        lines = re.findall(r"^/goal STOP CONDITIONS.*$", read(self.SKILL),
                           re.MULTILINE)
        self.assertEqual(len(lines), 3)
        # order in the file: full, branch-merge, fork-no-merge
        return lines[1], lines[2]

    def test_branch_merge_holds_until_release_and_no_bounce(self):
        bm, _ = self.reduced_goal_lines()
        self.assertIn("REVIEW-WATCH", bm)
        self.assertIn("contained in origin/main", bm)

    def test_fork_holds_until_maintainer_closes(self):
        _, fk = self.reduced_goal_lines()
        self.assertIn("REVIEW-WATCH", fk)
        self.assertIn("CLOSED by the maintainer", fk)

    def test_fork_holds_until_released_too(self):
        # 2026-07-20 morning incident: david's loop ended when the maintainer
        # closed his tickets at the develop merge — but nothing was RELEASED
        # and the user found prod empty ("ping pong moze skoncit az ked je
        # vsetko deploynute do produ a nie skor"). The fork loop holds until
        # the merged work is contained in origin/main, same as branch-merge.
        _, fk = self.reduced_goal_lines()
        self.assertIn("contained in origin/main", fk)

    def test_review_watch_cadence_is_hourly_and_working(self):
        for line in self.reduced_goal_lines():
            self.assertIn("hourly", line)
            self.assertIn("never park", line)

    def test_nudge_without_armed_loop_dispatches_worker(self):
        t = read(self.SKILL)
        self.assertIn("NO `/goal` loop is armed", t)
        self.assertIn("dispatch the background `autopilot-worker` for the bounce ticket", t)


class TestCrossStreamProtocolCanonical(TestCase):
    """2026-07-19 user directive: airuleset OWNS the gatekeeper↔sub-dev
    protocol ('musis pochopit co vsetko sa pod gatekeeper rules riesilo a
    prevziat to pod svoju spravu'). The autopilot skill carries the canonical
    section BOTH sides read; repo-local commands (odoo-erp /process-subdev)
    must conform to it, never define their own variant."""

    SKILL = "skills/autopilot/SKILL.md"

    def test_canonical_section_exists(self):
        self.assertIn("## Cross-stream protocol", read(self.SKILL))

    def test_no_prompt_interrupts_rule(self):
        t = read(self.SKILL)
        self.assertIn("NEVER a payload prompt into a working session", t)

    def test_label_lifecycle_owned(self):
        t = read(self.SKILL)
        self.assertIn("who removes `prio:bounce`", t)
        self.assertIn("read-only role cannot remove labels", t)

    def test_both_loops_hold_alive(self):
        t = read(self.SKILL)
        self.assertIn("BOTH loops stay alive", t)
        self.assertIn("gatekeeper's own loop", t)


if __name__ == "__main__":
    main()
