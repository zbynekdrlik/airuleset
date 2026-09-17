"""Locks the design-before-code step in the ticket path (#104, 2026-07-27).

The loss: commit e9d1022 (2026-07-09) shrank
`modules/quality/autonomous-batch-issue-development.md` from 152 lines to 3.
The removed block carried the per-issue cycle -- "Brainstorm approach" and
"Write plan" BEFORE "Implement". That module sits in
`profiles/universal.profile`, so until 2026-07-09 the design-first pattern was
ambient in EVERY session, every turn, with no invocation needed.

The content moved to `skills/batch-issue-development/SKILL.md`. Three empirical
probes (2x general-purpose, 1x autopilot-worker, 2026-07-27) established that a
dispatched subagent DOES inherit the expanded global + project CLAUDE.md and the
auto-memory, but does NOT inherit skill BODIES -- only the one-line skill-list
descriptions. Nothing in the worker dispatch path invokes that skill for this
content, so the step reached no worker: `## CYCLE` went fetch -> implement.

That is the #91 silent-deletion class, not a conversion. The user, live:
"vsetky moje prisne pravidla na riesenie uloh sa stratili a len sa striela ako
pride, nahodne riesenie, nasledne milion oprav".

The fix restores the step on surfaces a worker actually receives, and makes its
OUTPUT a durable artifact (a ticket comment before the first code commit + an
`approach:` line in the evidence block the supervisor already re-verifies) --
so it is provable from primary sources, not merely written down somewhere.
Deliberately NO new hook/job/gate: restoring the content suffices (#102 deleted
two watchdog jobs for exactly that reason).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent

WORKER = "agents/autopilot-worker.md"
MODULE = "modules/quality/autonomous-batch-issue-development.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestWorkerCycleReadsMainDesign(TestCase):
    """#1061 (owner escalation 2026-09-17) REVERSED #104's worker-authors-design:
    the DESIGN is authored by the Fable MAIN, the worker READS + CONFIRMS it and
    NEVER authors it. The design-first discipline is preserved -- it just lives
    with the main now. These lock the worker's read-not-author step."""

    HEADER = "READ THE MAIN'S DESIGN"

    def _step(self):
        w = read(WORKER)
        step = w[w.index(self.HEADER):]
        return step[:step.index("\n3. ")]

    def test_cycle_carries_a_read_the_main_design_step(self):
        self.assertIn(self.HEADER, read(WORKER))

    def test_read_step_comes_before_the_implement_step(self):
        w = read(WORKER)
        design = w.index(self.HEADER)
        implement = w.index("Implement **the named issue(s) ONLY**")
        self.assertLess(
            design, implement,
            "the read-the-main's-design step must precede implementation")

    def test_step_content_is_inline_not_a_skill_pointer(self):
        """A skill body never reaches a dispatched worker (probes 2026-07-27),
        so the step must carry its own instruction, not delegate to a skill."""
        step = self._step()
        # the worker READS the main's design (which carries these) and greps code
        self.assertIn("root cause", step.lower())
        self.assertIn("alternative", step.lower())
        self.assertIn("Design-by: main", step)
        self.assertIn("grep", step.lower())
        self.assertNotIn("load the `batch-issue-development` skill", step)

    def test_worker_must_not_author_the_design(self):
        """The core of #1061: the worker never authors the design."""
        step = self._step()
        self.assertIn("NEVER author the design", step)
        self.assertIn("Anchors-confirmed", step)
        # it must NOT tell the worker to write the design shape itself
        self.assertNotIn("post it to the issue with", step)

    def test_step_is_unconditional(self):
        """A self-assessed "if non-trivial" hedge is no condition at all."""
        step = self._step()
        self.assertIn("UNCONDITIONAL", step)
        for hedge in ("if non-trivial", "if it is non-trivial",
                      "when non-trivial", "if multi-step"):
            self.assertNotIn(hedge, step.lower(),
                             f"banned self-assessed hedge: {hedge}")

    def test_step_output_lands_on_the_ticket(self):
        """The worker's confirmation/question is a durable ticket comment,
        checkable from primary sources."""
        step = self._step()
        self.assertIn("gh issue comment", step)
        self.assertIn("Design-question:", step)

    def test_evidence_block_reports_the_approach(self):
        """The supervisor re-verifies every evidence-block line; the approach
        line now points at the MAIN's Design-by comment + the worker's
        Anchors-confirmed comment."""
        w = read(WORKER)
        self.assertIn("approach:", w)
        self.assertIn("Anchors-confirmed", w)


class TestAmbientDesignFirstClause(TestCase):
    """The user's complaint covers /issue-planner and hand-run sessions too,
    not only autopilot workers -- so one short clause returns to the always-on
    module. One sentence, NOT the 152 lines e9d1022 removed."""

    def test_module_is_in_the_universal_profile(self):
        self.assertIn(MODULE, read("profiles/universal.profile"))

    def test_module_carries_the_design_before_code_clause(self):
        self.assertIn("Design the approach BEFORE writing code", read(MODULE))

    def test_clause_names_the_durable_record(self):
        self.assertIn("gh issue comment", read(MODULE))

    def test_module_stays_a_lean_stub(self):
        """Restoring all 152 lines would just be e9d1022's opposite mistake."""
        self.assertLess(len(read(MODULE).splitlines()), 20)


class TestSubagentInheritanceRecordIsCorrected(TestCase):
    """docs/superpowers/plans/2026-06-13-pipeline-v2.md claimed in-session
    subagents "boot with a reduced system prompt and no rules". Three probes on
    2026-07-27 disproved it. A false claim left standing is what future work
    would reason from, so the correction is recorded next to it."""

    PLAN = "docs/superpowers/plans/2026-06-13-pipeline-v2.md"

    def test_stale_claim_is_marked_disproven(self):
        t = read(self.PLAN)
        self.assertIn("SUPERSEDED 2026-07-27", t)
        self.assertIn("#104", t)

    def test_correction_states_what_is_and_is_not_inherited(self):
        t = read(self.PLAN)
        i = t.index("SUPERSEDED 2026-07-27")
        note = t[i:i + 1200]
        self.assertIn("skill", note.lower())
        self.assertIn("inherit", note.lower())


if __name__ == "__main__":
    main()
