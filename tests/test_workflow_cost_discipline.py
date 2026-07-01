"""Locks the Workflow / subagent cost-discipline fix (2026-06-30).

Incident: a review Workflow in the live odoo session fanned 6 agents that EACH
re-read the same ~4,500 lines of CI YAML + the full design, plus a fresh verifier
PER finding — ~5 MB of tokens, all on Opus, for a design the user had already
hand-converged. When called out, the agent KILLED the run and harvested NOTHING
("este horsie!!!"). Three governance gaps, three guards:

  A) Workflow fan-out over-grounds (N agents re-read the same big files) and
     over-scopes (a fleet where one pass sufficed) — ground once, right-size.
  B) Model tiering never bites — a stage's model must be chosen by what the stage
     IS. (2026-07-01 update: under the MAX-PERFORMANCE policy the miss is INVERTED —
     a judgment stage on a cheap tier; mechanical lookups still stay light. The
     redundancy guards A+C are policy-independent and unchanged.)
  C) Killing expensive in-flight work and discarding its partial output — a NEW
     salvage-before-discarding-work module, wired into the universal profile.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestWorkflowCostDiscipline(TestCase):
    def test_ground_once_and_right_size_fanout(self):
        t = read("modules/core/claude-code-tooling.md")
        # Ground-once rule + the residual-uncertainty sizing + the ultracode clarification.
        self.assertIn("Ground ONCE", t)
        self.assertIn("RESIDUAL UNCERTAINTY", t)
        self.assertIn("Ultracode buys DEPTH, never REDUNDANCY", t)
        # Per-item fan-out multiplication bound.
        self.assertIn("O(findings × context)", t)
        # The anti-patterns line must name the kill-and-discard waste + point at the module.
        self.assertIn("salvage-before-discarding-work.md", t)

    def test_tiering_still_names_a_miss_and_keeps_lookups_light(self):
        t = read("modules/core/model-awareness.md")
        # The self-audit must still name a tiering MISS (inverted under max-performance).
        self.assertIn("tiering MISS", t)
        # Mechanical lookups stay on a light tier under EVERY policy (latency, not cost).
        self.assertIn("Purely MECHANICAL / READ-ONLY plumbing = Sonnet 5 (or Haiku", t)
        # Redundancy stays banned even with unlimited budget.
        self.assertIn("Redundancy is still waste, not rigor", t)

    def test_salvage_module_exists_and_wired(self):
        m = read("modules/core/salvage-before-discarding-work.md")
        self.assertIn("HARVEST", m)
        self.assertIn("kill-and-discard", m)
        self.assertIn("resumeFromRunId", m)
        # Cross-linked to the dropped-work sibling.
        self.assertIn("no-dropped-work.md", m)
        # Wired into the universal profile so it loads globally.
        profile = read("profiles/universal.profile")
        self.assertIn("modules/core/salvage-before-discarding-work.md", profile)


if __name__ == "__main__":
    main()
