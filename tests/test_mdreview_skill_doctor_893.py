"""Content-lock tests for /skill-doctor integration in /mdreview (#893).

/skill-doctor (CC v2.1.252+) reports per-skill context cost, 7-day token
attribution, duplicates, and plugin freshness — signals that skill-usage
and validate do NOT provide. The mdreview skill covers these in Step 5b,
with an explicit dedup guard: the artifact's fleet 90-day data (Step 5)
is the sole authority for retirement, never /skill-doctor's per-machine
7-day columns.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills" / "mdreview" / "SKILL.md").read_text(encoding="utf-8")


class TestSkillDoctorStepPresent(TestCase):
    """Step 5b heading and the /skill-doctor token must exist."""

    def test_step_5b_heading_exists(self):
        self.assertIn("Step 5b", SKILL)

    def test_skill_doctor_token_present(self):
        self.assertIn("/skill-doctor", SKILL)


class TestSkillDoctorInvocation(TestCase):
    """The non-interactive invocation shape must be documented."""

    def test_claude_p_invocation_shape(self):
        self.assertIn("claude -p", SKILL)


class TestSkillDoctorDedupGuard(TestCase):
    """The dedup guard must explicitly state that the artifact (Step 5)
    owns retirement authority, and /skill-doctor's usage columns are
    NOT used for that decision."""

    def test_artifact_authority_for_retirement(self):
        # The step must reference the artifact/Step 5 as the retirement authority
        t = SKILL.lower()
        self.assertIn("step 5", t)
        # Must mention the artifact or fleet data owns the decision
        has_artifact = "artifact" in t
        has_fleet = "fleet" in t
        self.assertTrue(has_artifact or has_fleet,
                        "Step 5b must reference artifact/fleet data as retirement authority")

    def test_ignore_usage_columns(self):
        # Must explicitly say to ignore uses/last-used for retirement
        t = SKILL.lower()
        self.assertIn("ignore", t)


class TestSkillDoctorUniqueSignals(TestCase):
    """The step must name the unique signals /skill-doctor provides."""

    def test_context_cost_signal(self):
        self.assertIn("context cost", SKILL.lower())

    def test_duplicate_detection_signal(self):
        t = SKILL.lower()
        self.assertTrue("duplicate" in t or "duplicat" in t,
                        "Step 5b must cover duplicate detection")


class TestSkillDoctorFleetInvariant(TestCase):
    """The fleet-invariant rationale must be stated — one box suffices
    for install-shape signals because airuleset manages them identically."""

    def test_fleet_invariant_rationale(self):
        t = SKILL.lower()
        has_fleet_inv = "fleet" in t and ("invariant" in t or "identic" in t)
        has_one_box = "one box" in t or "one run" in t
        self.assertTrue(has_fleet_inv or has_one_box,
                        "Step 5b must explain why one box suffices")


if __name__ == "__main__":
    main()
