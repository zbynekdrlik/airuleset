"""Content-lock tests for /skill-doctor integration in /mdreview (#893).

/skill-doctor (CC v2.1.252+) reports per-skill context cost, 7-day token
attribution, duplicates, and plugin freshness — signals that skill-usage
and validate do NOT provide. The mdreview skill covers these in Step 5b,
with an explicit dedup guard: the artifact's fleet 90-day data (Step 5)
is the sole authority for retirement, never /skill-doctor's per-machine
7-day columns.
"""

import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills" / "mdreview" / "SKILL.md").read_text(encoding="utf-8")


def _step5b_window():
    """Extract the Step 5b section as a normalized (whitespace-collapsed) string.

    Bounded from '## Step 5b' to the next '## ' heading — the #500 window
    pattern, with #728's fallback to end-of-file if no next heading.
    """
    start = SKILL.index("## Step 5b")
    rest = SKILL[start + len("## Step 5b"):]
    end_match = re.search(r"\n## ", rest)
    end = start + len("## Step 5b") + end_match.start() if end_match else len(SKILL)
    window = SKILL[start:end]
    # Normalize whitespace for cross-line token matching
    return " ".join(window.split())


class TestSkillDoctorStepPresent(TestCase):
    """Step 5b heading and the /skill-doctor token must exist."""

    def test_step_5b_heading_exists(self):
        self.assertIn("## Step 5b", SKILL)

    def test_skill_doctor_token_present(self):
        w = _step5b_window()
        self.assertIn("/skill-doctor", w)


class TestSkillDoctorInvocation(TestCase):
    """The non-interactive invocation shape must be documented in Step 5b."""

    def test_claude_p_invocation_shape(self):
        w = _step5b_window()
        self.assertIn("claude -p", w)


class TestSkillDoctorDedupGuard(TestCase):
    """The dedup guard must explicitly state that the artifact (Step 5)
    owns retirement authority, and /skill-doctor's usage columns are
    NOT used for that decision. Asserts on the NEGATION-BEARING phrases
    per #799 (lock the negation, not just the nouns)."""

    def test_sole_authority_in_step5b(self):
        w = _step5b_window()
        self.assertIn("sole authority", w)

    def test_never_use_skill_doctor_for_retirement(self):
        w = _step5b_window()
        self.assertIn("never use", w.lower())
        self.assertIn("per-machine", w.lower())

    def test_ignore_usage_columns_in_step5b(self):
        w = _step5b_window()
        self.assertIn("Ignore", w)
        self.assertIn("uses", w)
        self.assertIn("last used", w)


class TestSkillDoctorUniqueSignals(TestCase):
    """The step must name the unique signals /skill-doctor provides,
    within the Step 5b window."""

    def test_context_cost_signal(self):
        w = _step5b_window()
        self.assertIn("context cost", w.lower())

    def test_duplicate_detection_signal(self):
        w = _step5b_window()
        self.assertIn("Duplicate detection", w)

    def test_plugin_freshness_signal(self):
        w = _step5b_window()
        self.assertIn("Plugin freshness", w)


class TestSkillDoctorFleetInvariant(TestCase):
    """The fleet-invariant rationale must be stated within Step 5b."""

    def test_fleet_invariant_rationale(self):
        w = _step5b_window()
        self.assertIn("fleet-invariant", w.lower())
        self.assertIn("one box", w.lower())

    def test_7day_per_machine_scoped(self):
        """The 7-day attribution must be scoped as per-machine/SECONDARY."""
        w = _step5b_window()
        self.assertIn("SECONDARY", w)


if __name__ == "__main__":
    main()
