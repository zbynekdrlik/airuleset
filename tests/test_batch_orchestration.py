"""Autopilot orchestration locks that survive #1137 (owner 25.10.2026): the
continuous-refill / lane-count doctrine (#848) is REMOVED — Claude Code decides
subagents, worktrees and order itself — so this file keeps only the still-true
locks: machine compacts stay removed (#1084), the native autocompact preserves
the armed `/goal`, and neither autopilot skill re-adds the retired batch
directive.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import goal_registry as gr  # noqa: E402

SKILL = "skills/autopilot/SKILL.md"
SKILL_MASTER = "skills/autopilot-master/SKILL.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestRegistryCompactBoundary(TestCase):
    """The compact-boundary clause keeps machine compacts removed."""

    def _clause(self, cid, profile="full"):
        return next(c for c in gr.CLAUSES if c.id == cid).text_for(profile)

    def test_compact_boundary_is_removed_1084(self):
        # #1084: machine compacts are REMOVED for good (was #911 DISABLED).
        for p in gr.PROFILES:
            cb = self._clause("compact-boundary", p)
            self.assertIn("REMOVED", cb)
            self.assertIn("#1084", cb)
            self.assertNotIn("compact-request", cb)
            # the retired batch/mid-fleet framing must stay gone
            self.assertNotIn("WHOLE batch has returned", cb)
            self.assertNotIn("ZERO live tasks", cb)
            self.assertNotIn("next batch", cb)
            self.assertNotIn("NEVER compact while lanes live", cb)
            self.assertNotIn("#29193", cb)
            # the pre-#723 one-at-a-time tails stay banned
            self.assertNotIn("paces ONE", cb)
            self.assertNotIn("keep building", cb)
            self.assertNotIn("do NOT dispatch the next", cb)

class TestSkillCompactDoctrine(TestCase):
    """The autopilot SKILL body keeps machine compacts removed."""

    def test_machine_compacts_removed_from_the_skill(self):
        # #1084: the "compact at EVERY integration cycle" doctrine is GONE —
        # machine compacts are removed; the SKILL says so and never types /compact.
        body = read(SKILL)
        self.assertNotIn("compact at EVERY integration cycle", body)
        self.assertIn("machine compacts are REMOVED", body)
        self.assertNotIn("DRAINED BATCH BOUNDARY", body)

class TestSkillBakesInTheResearchFacts(TestCase):
    """#1084: machine compacts are REMOVED, so the STEP-0 "compact over live
    lanes is safe" experiment premise is OBSOLETE and must be GONE from the
    doctrine — only the still-true "a native autocompact preserves the armed
    /goal" fact remains."""

    def test_the_step0_compact_over_live_lanes_premise_is_gone(self):
        body = read(SKILL)
        # #1084: there is no machine compact over live lanes to justify anymore.
        self.assertNotIn("STEP-0", body)
        self.assertNotIn("compact over live lanes", body)
        self.assertNotIn("NEVER break task handles", body)

    def test_goal_survives_a_normal_compaction_is_documented(self):
        body = read(SKILL)
        self.assertIn("PRESERVES the armed `/goal`", body)
        self.assertIn("goal.md", body)

    def test_the_cc_step0_experiment_citation_is_gone(self):
        # #1084: the STEP-0 experiment citation is obsolete — machine compacts
        # are removed, so the "safe over live lanes" baseline is not cited anymore.
        self.assertNotIn("CC 2.1.258", read(SKILL))


class TestNoBatchReversion(TestCase):
    """Negative lock (flip of #723's TestNoContinuousReversion): an ADDITIVE
    re-introduction of the retired batch phrasing ALONGSIDE the continuous text
    passes every positive-presence lock above, so guard the exact batch phrases
    too, on BOTH skills. The membership check is case-insensitive so a lower-case
    re-add cannot slip the lock. (Both bodies may still mention 'batch' inside a
    retiring/reversal negation — the banned phrases below are the affirmative
    directives only.)"""

    BANNED_AFFIRMATIVE = (
        "no refill while a batch",
        "drained batch boundary",
        "whole batch has returned",
        "whole batch has drained",
        "zero live tasks",
        "no new lane is dispatched while the batch is open",
        "no new lane is dispatched while this batch",
        "batch-open state",
        "never compact while lanes live",
        "batch cap",
    )

    def test_neither_skill_re_adds_the_batch_directive(self):
        for rel in (SKILL, SKILL_MASTER):
            body = read(rel).lower()
            present = [p for p in self.BANNED_AFFIRMATIVE if p.lower() in body]
            self.assertEqual(present, [],
                             "%s re-introduced the retired batch phrasing: %r"
                             % (rel, present))


if __name__ == "__main__":
    main()
