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
        # v2 (#858) restructured: axes referenced in Step 6 + Rules section
        self.assertIn("native-now", t.lower())
        self.assertIn("model-combination", t.lower())
        self.assertIn("dynamic-application", t.lower())

    def test_conversion_never_deletion(self):
        t = read(self.SKILL)
        # v2 (#858): the principle is "content is reduced by conversion ...
        # never by deletion" — same intent, restructured phrasing
        self.assertIn("never by deletion", t)

    def test_stale_length_claim_removed(self):
        # Official docs (code.claude.com best-practices + memory) say the
        # opposite; the uncited claim must never return.
        t = read(self.SKILL)
        self.assertNotIn("do NOT drop instructions due to length", t)

    def test_generation_shift_check_present(self):
        # v2 (#858): model-generation check via Step 6 live web research
        # and the watchdog model-generation trigger
        t = read(self.SKILL)
        self.assertIn("model", t.lower())
        self.assertIn("live model", t.lower())

    def test_ratchet_mechanism_recorded(self):
        # v2 (#858): the context ratchet replaces the calibration precedent
        t = read(self.SKILL)
        self.assertIn("ratchet", t.lower())
        self.assertIn("context_ratchet.json", t)


class TestSizeTargetContradictionGone(TestCase):
    def test_rules_audit_is_stub(self):
        # v2 (#858): rules-audit is now a stub pointing to /mdreview
        t = read("skills/rules-audit/SKILL.md")
        self.assertNotIn("<400 lines", t)
        self.assertNotIn("<400/<30", t)
        # The stub references mdreview as the replacement
        self.assertIn("mdreview", t.lower())

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
    """#423/#858: the native-now axis must cover rules + machinery, plus a
    standing 'after every CC release' re-audit trigger. v2 restructured the
    skill to Steps 0-7 and the cadence into a watchdog Job 43."""

    SKILL = "skills/mdreview/SKILL.md"

    def test_axis1_native_now_present(self):
        # v2 Step 6 carries the live web research axes
        t = read(self.SKILL)
        self.assertIn("Native-now", t)

    def test_model_combination_axis_present(self):
        t = read(self.SKILL)
        self.assertIn("Model-combination", t)

    def test_web_research_step_present(self):
        # v2 (#858): Step 6 carries the live web research
        t = read(self.SKILL)
        self.assertIn("Live web research", t)
        self.assertIn("WebSearch", t)

    def test_release_reaudit_trigger_present(self):
        t = read(self.SKILL)
        self.assertIn("after every Claude Code release", t)

    def test_user_invocable_true(self):
        # the skill is user-invocable (the owner runs /mdreview by hand)
        t = read(self.SKILL)
        self.assertIn("user-invocable: true", t)


if __name__ == "__main__":
    main()
