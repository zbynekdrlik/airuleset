"""Locks the Sonnet 5 tiering shift (2026-06-30).

Sonnet 5 (`claude-sonnet-5`) released today: ~40-60% of Opus's price, full effort
range, 63.2% SWE-bench Pro vs Opus 4.8's 69.2%, and Anthropic's default model for
most accounts. The user observed Sonnet was barely used under the old "anything
touching code = Opus" rule and asked for it to be used substantially more (without
quality loss). The policy flips to Anthropic's own `opusplan` split: Opus PLANS +
REVIEWS, Sonnet 5 EXECUTES (at high effort). The autopilot-worker defaults to
Sonnet 5, escalating to Opus only for genuinely hard / architectural tickets.

These assertions prove the flip actually landed (and the old starving rule is gone).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSonnet5Tiering(TestCase):
    def test_model_awareness_adopts_opusplan_split(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("claude-sonnet-5", t)
        self.assertIn("Opus PLANS + REVIEWS, Sonnet 5 EXECUTES", t)
        self.assertIn("opusplan", t)
        # Execution of scoped code is now the Sonnet 5 default, with Opus escalation.
        self.assertIn("EXECUTION of settled, scoped code = Sonnet 5", t)
        self.assertIn("Escalate a single dispatch back to Opus", t)
        # Quality is held by EFFORT, not by keeping the whole job on Opus.
        self.assertIn("Sonnet 5 runs code work at `high`/`xhigh`", t)

    def test_old_starving_rule_is_gone(self):
        t = read("modules/core/model-awareness.md")
        # The exact lines that starved Sonnet ("everything touching code = Opus") must be gone.
        self.assertNotIn("Opus for everything that touches the code", t)
        self.assertNotIn(
            "Anything that writes, edits, judges, or reasons about CODE / LOGIC = Opus", t
        )

    def test_workflow_stage_tiering_routes_execution_to_sonnet(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("EXECUTION stages — implementing a settled plan", t)
        self.assertIn("`opts.model: 'sonnet'` (= Sonnet 5)", t)
        # Design/synthesis/review bookends stay on Opus.
        self.assertIn("The hard-judgment bookends stay on Opus.", t)
        # The old "inherit Opus for any code-logic stage" must be gone.
        self.assertNotIn(
            "synthesize CODE / LOGIC (implement, review, verify, design-synthesis) → omit the override",
            t,
        )

    def test_autopilot_worker_defaults_to_sonnet5(self):
        w = read("agents/autopilot-worker.md")
        # Frontmatter pins the worker to Sonnet 5 by default.
        self.assertIn("model: sonnet", w)
        self.assertIn("Sonnet 5 EXECUTES, Opus plans + reviews", w)

    def test_autopilot_supervisor_escalates_only_hard_tickets(self):
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Model = Sonnet 5 by default", s)
        self.assertIn("Override to `model: opus`", s)


if __name__ == "__main__":
    main()
