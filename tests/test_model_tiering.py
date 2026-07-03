"""Locks the model tiering: Fable-on-HARD auto-escalation, budget-gated (2026-07-03).

History: the 2026-07-01 MAX-PERFORMANCE policy (Fable 5 on every judgment dispatch)
burned tokens brutally and kept tripping the usage limits mid-work — the user reverted
it 2026-07-02 to plain Opus+Sonnet with Fable as a rare manual/orchestrator exception.
On 2026-07-03 the user directed the middle tier: genuinely HARD tasks (architecture /
design synthesis, hard debugging, adversarial verify of critical changes, architectural
autopilot tickets) escalate to **Fable 5 AUTOMATICALLY** — but ONLY through the
**budget gate** (`airuleset.py fable-gate`, watchdog usage-cache based), so automatic
Fable can never again drain the weekly limits and stop the user's work.

These assertions prove: the auto-escalation is documented on every surface (module,
tooling, autopilot skill, worker), the gate is MANDATORY for every automatic Fable
dispatch, defaults stay Opus/Sonnet, and Fable-everywhere stays DORMANT.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestFableOnHardBudgetGated(TestCase):
    def test_model_awareness_active_policy_is_fable_on_hard(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn(
            "Fable 5 AUTO-escalates on genuinely HARD tasks, budget-gated (ACTIVE policy, 2026-07-03)", t)
        self.assertIn("reverted the 2026-07-01", t)          # history preserved
        self.assertIn('EXECUTION of settled, scoped code = Sonnet 5 (`model: "sonnet"`)', t)

    def test_hard_criteria_are_enumerated(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("HARD-task AUTO-escalation = Fable 5", t)
        # The four criteria families:
        self.assertIn("Architecture / design / synthesis of a genuinely COMPLEX or cross-cutting", t)
        # "multi-file" must NOT be a standalone sufficient condition (most features
        # are multi-file — that disjunct would escalate routine design work):
        self.assertIn('"Multi-file" alone is NOT the bar', t)
        self.assertIn("Hard debugging", t)
        self.assertIn("Adversarial final review / verify of a safety-critical", t)
        self.assertIn("autopilot ticket that is architectural / cross-cutting", t)
        # Routine work explicitly excluded + doubt resolves DOWN:
        self.assertIn("Routine work is NOT hard", t)
        self.assertIn("When unsure whether a task is hard → it is NOT; use Opus.", t)

    def test_gate_is_mandatory_for_automatic_fable(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("airuleset.py fable-gate", t)
        self.assertIn("Never skip the gate for an automatic escalation", t)
        # Gate semantics: once per hard task, CLOSED → opus, fail-safe on stale cache.
        self.assertIn("run the gate ONCE per hard task/batch", t)
        self.assertIn("missing/stale cache = CLOSED", t)

    def test_execution_never_escalates_to_fable(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Execution does NOT escalate to Fable", t)

    def test_fable_everywhere_is_dormant_not_active(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Dormant — the Fable-everywhere MAX-PERFORMANCE mode", t)
        self.assertIn("re-activate ONLY on the user's explicit say-so", t)
        self.assertNotIn("MAX-PERFORMANCE mode: Fable 5 everywhere judgment matters (ACTIVE", t)
        self.assertNotIn("EVERY dispatch where judgment affects the outcome = Fable 5", t)

    def test_workflow_stage_tiering_gates_fable_stages(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("EXECUTION stages", t)
        self.assertIn("`opts.model: 'sonnet'` (= Sonnet 5)", t)
        # Hard judgment stages may take fable — but only pre-gated by the orchestrator
        # (Workflow scripts cannot exec, so the gate runs BEFORE authoring).
        self.assertIn("`opts.model: 'fable'` for the genuinely HARD judgment stages", t)
        self.assertIn("ONLY when the budget gate is OPEN", t)
        self.assertIn("BEFORE authoring the script", t)
        self.assertIn("never bake in an ungated Fable stage", t)

    def test_autopilot_worker_defaults_to_sonnet(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("model: sonnet", w.split("---")[1])   # frontmatter block
        self.assertIn("You run on Sonnet 5", w)
        self.assertNotIn("model: fable", w.split("---")[1])

    def test_autopilot_supervisor_escalates_hard_tickets_through_gate(self):
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Model = Sonnet 5 by default", s)
        self.assertIn("Fable through the budget gate", s)
        self.assertIn("fable-gate", s)
        self.assertIn("gate OPEN (exit 0) → dispatch `model: fable`", s)
        self.assertIn("gate CLOSED (exit 1)", s)
        # No ungated automatic fable dispatch:
        self.assertIn("Never dispatch an automatic `model: fable` without the gate check", s)
        self.assertNotIn("Model = Fable 5 by default", s)

    def test_worker_mid_ticket_hard_wall_climbs_opus_first(self):
        # A Sonnet worker's "first real attempt" is a SONNET attempt — ordinary bugs
        # clear that bar constantly. The ladder is Sonnet → OPUS → (gate) → Fable,
        # never Sonnet → Fable, and the tie-breaker must be present on this surface.
        w = read("agents/autopilot-worker.md")
        self.assertIn("fable-gate", w)
        self.assertIn("HARD wall mid-ticket", w)
        self.assertIn('FIRST at `model: "opus"`', w)
        self.assertIn("Opus rung comes\nBEFORE Fable", w.replace("\r\n", "\n"))
        self.assertIn("When unsure whether it is HARD → it is NOT; use Opus.", w)


if __name__ == "__main__":
    main()
