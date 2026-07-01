"""Locks the ownership-gate hardening (2026-07-01).

Incident: Claude designed a "true latency" number (#512), found its own real
TVs don't expose `estimatedPlayoutTimestamp`, and asked the user to pick between
three technical ways to compute it — framing a self-invented technical obstacle
as a "genuine design decision". The user, furious: *"toto je technická vec, TY si
ju vymyslel, TY ju vyrieš... mňa s tým neotravuj!!!"* (same class as the QR-corner
incident). The rule now states: an obstacle in something YOU designed is YOURS to
SOLVE (investigate → best graceful solution), or PROVE unsolvable with evidence —
never a menu of technical workarounds, never relabelled a "design decision".
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestOwnershipGateSelfInvented(TestCase):
    def test_self_invented_obstacle_is_yours_to_solve(self):
        t = read("modules/core/ask-before-assuming.md")
        self.assertIn(
            "A technical OBSTACLE in something YOU designed is YOURS to SOLVE", t)
        # The exact rationalization it must kill.
        self.assertIn('relabelled a self-invented technical obstacle a "genuine design decision"', t)
        # The escalation the user demanded: solve, or prove unsolvable with evidence.
        self.assertIn("present concrete EVIDENCE and DECLARE it unsolvable", t)
        # No workaround menus.
        self.assertIn("menu of technical workarounds", t)

    def test_incident_is_recorded(self):
        t = read("modules/core/ask-before-assuming.md")
        self.assertIn("estimatedPlayoutTimestamp", t)
        self.assertIn("mňa s tým neotravuj", t)

    def test_preanswered_table_row_present(self):
        t = read("modules/core/ask-before-assuming.md")
        self.assertIn("SOLVE it — never ask; if truly impossible, PROVE it + declare unsolvable", t)


if __name__ == "__main__":
    main()
