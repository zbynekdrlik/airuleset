"""#472 — cross-stream + governing-design validation before dispatch.

A ticket valid against CURRENT MERGED code can still collide with (a) an
in-flight branch/PR of ANOTHER stream, or (b) a governing epic / frozen
design decision the working ticket doesn't know about. Origin: montalu
subdev 2026-08-14 (three streams' open PRs bumped one shared addon to the
same manifest version with colliding migrations/<version>/ dirs; a merge
past a frozen governing decision).

These lock the governance-text extension across the three surfaces the
issue names — the `ticket-validator` agent (where the check runs + the two
new verdict fields), the `verify-issue-still-valid` skill (the protocol),
and the `autopilot` supervisor Step 1b (the branch that actions the new
verdict fields). Teeth: each asserts a distinctive statement that a revert
of the content would remove.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "agents" / "ticket-validator.md"
VERIFY_SKILL = ROOT / "skills" / "verify-issue-still-valid" / "SKILL.md"
AUTOPILOT = ROOT / "skills" / "autopilot" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(s):
    """Collapse markdown line-wraps so a multi-line phrase still matches."""
    return " ".join(s.split())


class TestTicketValidatorRunsTheCrossStreamCheck(unittest.TestCase):
    def setUp(self):
        self.t = read(VALIDATOR)
        self.n = norm(self.t)

    def test_multi_stream_scoped_deep_validation_step_present(self):
        # the check is a real deep-validation step, scoped to multi-stream repos
        self.assertIn("Cross-stream + governing-design check", self.t)
        self.assertIn("multi-stream repos", self.n)
        # a single-stream repo skips it (no-op) — it must not add cost there
        self.assertIn("single-stream repo this is a fast no-op", self.n)

    def test_scans_foreign_in_flight_branches_and_prs(self):
        self.assertIn("git branch -r", self.t)
        self.assertIn("gh pr list --state open", self.n)
        self.assertIn("FILE-LEVEL or domain", self.n)

    def test_checks_governing_epic_and_frozen_decisions(self):
        self.assertIn("needs-design", self.t)
        self.assertIn("needs-decision", self.t)
        # a ticket past a frozen governing decision is escalated, never buried
        self.assertIn("frozen governing decision", self.n)

    def test_shared_addon_migration_collision_is_a_ci_guard_not_a_hook(self):
        # issue item 5: flag it, but the mechanical guard is repo CI, not here
        self.assertIn("migrations/<version>/", self.t)
        self.assertIn("mechanical CI guard", self.n)

    def test_verdict_block_carries_the_two_new_fields(self):
        # issue item 4: the ticket-validator template MUST return a verdict for
        # both cross-stream/in-flight conflict AND governing-design conflict
        self.assertRegex(self.t, r"(?m)^cross_stream:\s*clear\s*\|\s*conflict:")
        self.assertRegex(
            self.t, r"(?m)^governing_design:\s*clear\s*\|\s*conflict:.*\|\s*follows:")

    def test_verdict_meanings_document_both_new_fields(self):
        # the caller actions differ: cross-link+wait vs ask-the-user vs cite
        self.assertIn("cross-links + waits", self.n)
        self.assertIn("the caller asks the user", self.n)
        self.assertIn("`follows`", self.t)


class TestVerifySkillDocumentsTheProtocol(unittest.TestCase):
    def setUp(self):
        self.t = read(VERIFY_SKILL)
        self.n = norm(self.t)

    def test_original_verbatim_anchors_survive(self):
        # the skill is a VERBATIM-converted module (test_ruleset_conversion_wave2);
        # my addition must not disturb its locked anchors.
        self.assertIn("**Tickets rot.**", self.t)
        self.assertIn(
            "read-only **`ticket-validator`** subagent, and its verdict "
            "gates the work", self.t)

    def test_cross_stream_section_present_and_scoped(self):
        self.assertIn(
            "Cross-stream + governing-design validation (multi-stream repos)",
            self.t)
        self.assertIn("scan OTHER streams' in-flight work", self.n)

    def test_governing_epic_and_two_verdict_fields_named(self):
        self.assertIn("governing epic", self.n)
        self.assertIn("cross_stream", self.t)
        self.assertIn("governing_design", self.t)


class TestAutopilotStep1b_ActionsTheNewVerdicts(unittest.TestCase):
    def setUp(self):
        self.t = read(AUTOPILOT)

    def test_cross_stream_conflict_drops_and_waits(self):
        self.assertIn("`cross_stream: conflict`", self.t)
        # scope the check to the Step 1b branch clause itself, not anywhere
        i = self.t.index("`cross_stream: conflict`")
        clause = norm(self.t[i:i + 600])
        self.assertIn("DROP from the batch", clause)
        self.assertIn("WAIT", clause)

    def test_governing_design_conflict_asks_and_follows_grounds(self):
        self.assertIn("`governing_design: conflict`", self.t)
        i = self.t.index("`governing_design: conflict`")
        clause = norm(self.t[i:i + 600])
        self.assertIn("ask the user", clause)
        # a `follows` verdict is NOT a drop — it feeds the Step 1c grounding
        self.assertIn("`governing_design: follows`", clause)
        self.assertIn("design grounding", clause)


if __name__ == "__main__":
    unittest.main()
