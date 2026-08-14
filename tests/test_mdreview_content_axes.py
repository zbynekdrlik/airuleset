"""Locks the /mdreview content-axes reframe (user directive, 2026-07-09).

Growth/line count is NOT the indicator — CONTENT is. Every rule originates
from a concrete development problem; that work is never deleted to chase a
size number. The review runs along three axes: (1) native-now — what the live
model generation already does correctly by itself, (2) model-combination
correctness, (3) dynamic application — rules load context only when needed
(conversion to skill / path-scoped rule / hook, never bare deletion).

Also kills the stale claim ("current models do NOT drop instructions due to
length") that current official docs contradict, and the three-way size-target
contradiction (profile <800/<50KB vs rules-audit <400/<30KB vs mdreview
no-target).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestMdreviewContentAxes(TestCase):
    SKILL = "skills/mdreview/SKILL.md"

    def test_content_is_the_indicator_not_line_count(self):
        t = read(self.SKILL)
        self.assertIn("CONTENT is the indicator", t)
        self.assertIn("never a target", t)

    def test_three_axes_present(self):
        t = read(self.SKILL)
        self.assertIn("Native-now", t)
        self.assertIn("Model-combination correctness", t)
        self.assertIn("Dynamic application", t)

    def test_conversion_never_deletion(self):
        t = read(self.SKILL)
        self.assertIn("conversion is never deletion", t)
        self.assertIn("Deleting a working rule to save lines is a REGRESSION", t)

    def test_stale_length_claim_removed(self):
        # Official docs (code.claude.com best-practices + memory) say the
        # opposite; the uncited claim must never return.
        t = read(self.SKILL)
        self.assertNotIn("do NOT drop instructions due to length", t)

    def test_generation_shift_scaffolding_check_present(self):
        t = read(self.SKILL)
        self.assertIn("Generation-shift scaffolding check", t)
        self.assertIn("OVER-BIND", t)

    def test_calibration_precedent_recorded(self):
        # The user's real examples: runtime-buggy Python era -> Rust
        # everywhere + days-long mutation runs, both since retired.
        t = read(self.SKILL)
        self.assertIn("runtime-buggy Python", t)
        self.assertIn("mutation", t)


class TestSizeTargetContradictionGone(TestCase):
    def test_rules_audit_has_no_size_target(self):
        t = read("skills/rules-audit/SKILL.md")
        self.assertNotIn("<400 lines", t)
        self.assertNotIn("<400/<30", t)
        self.assertIn("never a target", t)

    def test_universal_profile_has_no_size_target(self):
        t = read("profiles/universal.profile")
        self.assertNotIn("Target: <800", t)
        self.assertIn("metric, never a target", t)


class TestRuleIntakeGate(TestCase):
    def test_project_claude_md_carries_the_gate(self):
        t = read("CLAUDE.md")
        self.assertIn("Rule intake gate", t)
        self.assertIn("Mechanically checkable?", t)
        self.assertIn("Situational", t)
        self.assertIn("originating incident + date", t)


class TestMachineryNativeNowAxis(TestCase):
    """#423: the native-now axis (axis 1) must cover the supervision
    MACHINERY (watchdog / hooks / notify), not just rules, plus a standing
    'after every Claude Code release' re-audit trigger. Seeded from the
    #416 native+ecosystem audit and its sibling replacement tickets."""

    SKILL = "skills/mdreview/SKILL.md"

    def test_axis1_scope_extends_to_machinery(self):
        t = read(self.SKILL)
        self.assertIn("supervision MACHINERY", t)
        # the machinery sub-step of the native-now pass, added in-place
        self.assertIn("D-machinery", t)

    def test_machinery_verdicts_present(self):
        # #416's own per-area verdict vocabulary must be reused verbatim.
        t = read(self.SKILL)
        self.assertIn("REPLACE-with-native", t)
        self.assertIn("KEEP-custom", t)
        self.assertIn("HYBRID", t)

    def test_seed_checklist_cites_416_and_siblings(self):
        t = read(self.SKILL)
        for ref in ("#416", "#421", "#422"):
            self.assertIn(ref, t)
        # the concrete native candidates named in the seed checklist
        self.assertIn("SendMessage", t)
        self.assertIn("agents --json", t)
        self.assertIn("/usage", t)

    def test_release_reaudit_trigger_present(self):
        t = read(self.SKILL)
        self.assertIn("after every Claude Code release", t)

    def test_manual_invocation_still_holds(self):
        # the release trigger is a run CONDITION, not an auto-schedule --
        # the no-cron manual-invocation rule must survive the change.
        t = read(self.SKILL)
        self.assertIn("manually invoked", t)
        self.assertIn("no auto-schedule", t)


if __name__ == "__main__":
    main()
