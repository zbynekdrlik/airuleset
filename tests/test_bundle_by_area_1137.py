"""Locks autopilot bundling by AREA and PHASE (issue 1137, owner ROZHODNUTÉ 27.10.2026).

Finding: Step 3.1 grew a unit "greedily" from ANY small open ticket and both
bundling skills required members to be "independent", so related work never
clustered and the dependent phases of one feature shipped as separate PRs
(odoo-erp: last 40 merged PRs carried <= 1 ticket each; odoo-erp 9258 shipped
in 4 PRs). The doctrine now: priority first, then grow from the seed's OWN
area; dependent phases of one feature may share a unit; a member must only not
wait on a design decision still open with the owner. Size / schema / API /
security / cross-cut ceilings stay.

Each lock is sliced to its own bullet / section, so a phrase surviving
elsewhere in the 130 KB skill cannot satisfy it.
"""

import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _norm(s):
    return re.sub(r"\s+", " ", s)


def _window(text, start, end):
    i = text.index(start)
    j = text.index(end, i + len(start))
    return _norm(text[i:j])


class TestAutopilotUnitSelection(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def setUp(self):
        self.t = _read(self.SKILL)
        # The growth bullet runs to the next Step-3.1 sub-bullet.
        self.grow = _window(self.t, "**Grow by AREA", "\n   - ")

    def test_normal_queue_is_priority_then_oldest(self):
        seed = _window(self.t, "No bounce ticket open", "the normal queue")
        self.assertIn("a priority label first, then oldest first", seed)

    def test_growth_pool_is_the_seed_area(self):
        self.assertIn("seed's OWN area", self.grow)
        self.assertIn("`.claude/rules/internals-<area>` / module / area label / files", self.grow)
        self.assertIn("a generic type label like `bug` is not an area", self.grow)
        self.assertIn("an unrelated small ticket is NOT the growth pool", self.grow)

    def test_solo_only_when_the_area_has_no_partner(self):
        solo = _window(self.t, "**Solo only after a real search:**", "\n1b.")
        self.assertIn("a seed that passes the gate ships alone only when no open ticket of its area "
                      "passes the gate too", solo)
        self.assertIn("never fall back to unrelated tickets", solo)
        self.assertNotIn("today's behavior", self.t)

    def test_bounce_tickets_bundle_by_area_too(self):
        bounce = _window(self.t, "Several open bounce tickets", "No bounce ticket open")
        self.assertIn("like any other same-area issues", bounce)

    def test_a_dropped_phase_takes_its_dependents(self):
        step1b = _window(self.t, "**One unclear/overcome member must NOT block", "\n   - ")
        self.assertIn("a dropped member's dependent later phases drop with it", step1b)

    def test_dependent_phases_may_share_a_unit(self):
        self.assertIn("Dependent PHASES of one feature / ticket family MAY share the unit",
                      self.grow)
        self.assertIn("sequential commits on one branch (one PR, one CI cycle)", self.grow)
        self.assertIn("never one PR per phase", self.grow)
        self.assertIn("over the ceilings that ticket runs solo, still as ONE PR", self.grow)
        self.assertIn("barring a live-DB backfill split", self.grow)

    def test_only_an_open_owner_decision_blocks_a_member(self):
        self.assertIn("no member waits on a design decision still open with the owner",
                      self.grow)

    def test_ceilings_are_kept_in_the_growth_bullet(self):
        for kept in ("≤ ~300 LoC", "≤ ~600 LoC", "≤ 4 issues", "no DB schema/migration",
                     "no public-API break", "no security-boundary change",
                     "no cross-cutting refactor"):
            self.assertIn(kept, self.grow, kept)

    def test_greedy_size_only_growth_and_independence_are_gone(self):
        self.assertNotIn("Grow greedily", self.t)
        self.assertNotIn("independent — no member depends", self.t)
        self.assertIsNone(re.search(r"\bindependent\b", self.grow))


class TestBatchSkillGate(TestCase):
    SKILL = "skills/batch-issue-development/SKILL.md"

    def setUp(self):
        self.t = _read(self.SKILL)
        self.gate = _window(self.t, "#### Bundling gate", "**Cumulative ceiling")
        self.model = _window(self.t, "#### The model", "#### Bundling gate")

    def test_related_means_same_area(self):
        self.assertIn("Related = the same AREA", self.model)
        self.assertIn("a generic type label like `bug` is not an area", self.model)
        self.assertIn("an unrelated small issue is not a bundle partner", self.model)

    def test_dependent_phases_replace_independence(self):
        self.assertIn("No issue waits on a design decision still open with the owner",
                      self.gate)
        self.assertIn("dependent PHASES of one feature / ticket family may share the PR",
                      self.gate)
        self.assertIn("never one PR per phase", self.gate)
        self.assertIn("over the ceilings that ticket gets its OWN single PR", self.gate)
        self.assertIn("only the rare Acceptable splits below may separate them", self.gate)

    def test_ceiling_split_is_at_a_ticket_boundary(self):
        ceiling = _window(self.t, "**Cumulative ceiling", "\n")
        self.assertIn("splits into two PRs at a ticket boundary (one ticket's phases never split)",
                      ceiling)
        self.assertNotIn("Issues are independent", self.t)
        self.assertIsNone(re.search(r"\bindependent\b", self.gate))

    def test_split_order_is_the_agents_call(self):
        split = _window(self.t, "If ANY selected issue fails the gate", "\n")
        self.assertIn("(agent's choice)", split)
        self.assertNotIn("AskUserQuestion", split)

    def test_ceilings_are_kept(self):
        for kept in ("≤ 300 LoC", "No DB schema change", "No public API breaking change",
                     "No security boundary modification", "No cross-cutting refactor"):
            self.assertIn(kept, self.gate, kept)


class TestWorkerPhaseOrder(TestCase):
    def setUp(self):
        self.w = _read("agents/autopilot-worker.md")
        self.step3 = _window(self.w, "3. Implement **the named issue(s) ONLY**", "\n4. ")

    def test_members_run_in_dependency_order(self):
        self.assertIn("Do each member in dependency order (phases first-to-last)", self.step3)

    def test_a_dropped_member_takes_its_dependent_phases(self):
        self.assertIn("a dropped member takes its dependent later phases with it", self.step3)
        self.assertIn("re-dispatches it, with its dependent phases, as its own unit", self.step3)
        self.assertNotIn("re-dispatches it solo", self.step3)


if __name__ == "__main__":
    main()
