"""Locks the model tiering after the Fable-everywhere revert (2026-07-02).

The 2026-07-01 MAX-PERFORMANCE policy (Fable 5 on every judgment dispatch, cost no
object) burned tokens brutally and kept tripping the usage limits mid-work — the user
reverted it. ACTIVE policy now: default back to **Opus 4.8 + Sonnet 5** (the `opusplan`
split — Opus plans/reviews, Sonnet executes), with **Fable 5 reserved for ONLY the
genuinely hardest / frontier tasks** — either the orchestrator escalates a dispatch to
it, or the user manually switches `/model`. Fable is never a default anywhere. The
Fable-everywhere mode is retained as a DORMANT fallback, re-activated only by the user.

These assertions prove the revert landed and Fable is demoted to the rare ceiling.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestOpusPlanActiveFableReserved(TestCase):
    def test_model_awareness_active_policy_is_opusplan(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn(
            "Opus 4.8 + Sonnet 5 by default; Fable 5 ONLY for the hardest tasks (ACTIVE policy, 2026-07-02)", t)
        self.assertIn("reverted the 2026-07-01", t)
        self.assertIn("Main interactive session = Opus 4.8.", t)
        self.assertIn('EXECUTION of settled, scoped code = Sonnet 5 (`model: "sonnet"`)', t)

    def test_fable_is_reserved_top_escalation(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Fable 5 = the reserved TOP escalation", t)
        self.assertIn("GENUINELY HARDEST / frontier tasks ONLY", t)
        # Both escalation paths documented: orchestrator OR manual /model.
        self.assertIn('user** manually switches `/model` to Fable', t)
        self.assertIn("burns tokens brutally and trips the usage limits", t)

    def test_fable_everywhere_is_dormant_not_active(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Dormant — the Fable-everywhere MAX-PERFORMANCE mode", t)
        self.assertIn("re-activate ONLY on the user's explicit say-so", t)
        # The old ACTIVE heading + its blanket rule must be gone.
        self.assertNotIn("MAX-PERFORMANCE mode: Fable 5 everywhere judgment matters (ACTIVE", t)
        self.assertNotIn('EVERY dispatch where judgment affects the outcome = Fable 5', t)

    def test_primary_agent_is_opus_not_fable(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("The primary Claude Code agent runs **Opus 4.8**", t)

    def test_workflow_stage_tiering_routes_execution_to_sonnet(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("`opusplan` split", t)
        self.assertIn("EXECUTION stages", t)
        self.assertIn("`opts.model: 'sonnet'` (= Sonnet 5)", t)
        # Fable only for a deliberately-escalated frontier stage.
        self.assertIn("`opts.model: 'fable'` ONLY for a genuinely FRONTIER stage", t)

    def test_autopilot_worker_defaults_to_sonnet(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("model: sonnet", w.split("---")[1])   # frontmatter block
        self.assertIn("You run on Sonnet 5", w)
        self.assertNotIn("model: fable", w.split("---")[1])

    def test_autopilot_supervisor_escalates_rarely(self):
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Model = Sonnet 5 by default", s)
        self.assertIn("`model: fable` ONLY", s)          # split: the line wraps after ONLY
        self.assertIn("genuinely FRONTIER ticket", s)
        self.assertNotIn("Model = Fable 5 by default", s)


if __name__ == "__main__":
    main()
