"""Locks the durable-decisions policy (2026-07-09).

The user's recurring loss: findings + decisions converged in conversation are
never written to GitHub issues, the context compacts mid-session, and the
session forgets everything that was agreed ("pozabuda všetko na čo sme prišli
a čo sa rozhodol urobiť"). The rule moves the persistence deadline from
"before you stop" (no-dropped-work) to "THE MOMENT the decision/finding lands",
mandates tickets-first for converged plans, and prefers /autopilot for
executing them ticket-by-ticket from durable state.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestDurableDecisionsRule(TestCase):
    MOD = "modules/quality/durable-decisions-to-tickets.md"

    def test_module_exists_and_is_in_the_universal_profile(self):
        self.assertTrue((ROOT / self.MOD).exists())
        self.assertIn("modules/quality/durable-decisions-to-tickets.md",
                      read("profiles/universal.profile"))

    def test_deadline_is_the_moment_not_the_stop(self):
        t = read(self.MOD)
        self.assertIn("THE MOMENT It Is Made", t)
        self.assertIn("persist IN THE SAME TURN", t)
        self.assertIn("gh issue comment", t)

    def test_compaction_is_named_as_the_threat(self):
        t = read(self.MOD)
        self.assertIn("context window is DISPOSABLE", t)
        self.assertIn("Compaction fires MID-SESSION", t)

    def test_converged_plan_goes_tickets_first_then_autopilot(self):
        t = read(self.MOD)
        self.assertIn("tickets FIRST, implementation SECOND", t)
        self.assertIn("Prefer `/autopilot` for executing a converged multi-ticket plan",
                      t)
        self.assertIn("FRESH session could work it with zero conversation context", t)

    def test_continuous_self_test_present(self):
        t = read(self.MOD)
        self.assertIn("Keby sa context skompaktoval TERAZ", t)

    def test_anti_patterns_ban_conversation_only_agreements(self):
        t = read(self.MOD)
        self.assertIn("čo sme si povedali", t)
        self.assertIn("Tickets first", t)
        self.assertIn("Holding findings for the completion report", t)
        self.assertIn("all rewordings and semantic equivalents", t)

    def test_no_dropped_work_context_gate_points_here(self):
        self.assertIn("durable-decisions-to-tickets.md",
                      read("modules/quality/no-dropped-work.md"))

    def test_autopilot_worker_persists_decisions_per_turn(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("Decisions & findings land on the ticket THE MOMENT they happen", w)
        self.assertIn("durable-decisions-to-tickets.md", w)


if __name__ == "__main__":
    main()
