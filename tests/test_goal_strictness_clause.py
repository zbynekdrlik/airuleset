"""Locks the per-ticket verification clause in the /goal templates (2026-07-16).

User concern: /autopilot defines the discipline at run start, but the /goal
loop then grinds ticket after ticket — can later tickets get processed less
strictly than the first? Per-ticket strictness is held by the fresh-worker
protocol + deterministic hooks; the ONE soft spot is the long-lived SUPERVISOR
whose Step 4 independent verification lives only in the skill body, which
context compaction can thin out. Fix (same pattern as the bounce lane): the
verification discipline rides IN each /goal template — the durable engine text
re-evaluated every turn — so the last ticket is verified exactly as strictly
as the first, from primary sources, never from the worker's claim alone.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestGoalTemplatesCarryVerificationClause(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def goal_lines(self):
        lines = re.findall(r"^/goal STOP CONDITIONS.*$",
                           read(self.SKILL), re.MULTILINE)
        self.assertEqual(len(lines), 3)
        return lines

    def test_every_template_verifies_from_primary_sources(self):
        for line in self.goal_lines():
            self.assertIn("primary sources", line)

    def test_every_template_pins_last_ticket_as_strict_as_first(self):
        for line in self.goal_lines():
            self.assertIn("as strictly as the first", line)

    def test_every_template_rejects_worker_claim_alone(self):
        for line in self.goal_lines():
            self.assertIn("never from the worker", line)


if __name__ == "__main__":
    main()
