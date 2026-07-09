"""Locks the 2026-07-09 /mdreview dynamic-application conversions (axis 3).

13 situational always-on modules moved VERBATIM to on-demand surfaces
(user-approved in the mdreview review loop): 5 knowledge skills + 2 merges
into existing skills + 5 path-scoped rules + 1 pointer shrink. Conversion is
never deletion — every old module path still exists as a stub carrying the
enforcement-critical core + a load pointer, so cross-references never break
and the solved problems cannot silently return.
"""

from pathlib import Path
from unittest import TestCase, main

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


NEW_SKILLS = {
    "mutation-testing": "modules/ci/mutation-testing.md",
    "local-builds": "modules/quality/no-local-builds.md",
    "batch-issue-development": "modules/quality/autonomous-batch-issue-development.md",
    "view-image-urls": "modules/core/view-image-urls.md",
    "version-on-dashboard": "modules/quality/version-on-dashboard.md",
}

NEW_RULES = [
    "rules/no-continue-on-error.md",
    "rules/coverage-thresholds.md",
    "rules/browser-console-zero-errors.md",
    "rules/e2e-real-user-testing.md",
    "rules/database-migrations.md",
]


class TestSkillConversions(TestCase):
    def test_skills_exist_and_are_managed(self):
        for name in NEW_SKILLS:
            self.assertTrue((ROOT / "skills" / name / "SKILL.md").exists(), name)
            self.assertIn(name, airuleset.SKILL_NAMES)

    def test_skills_are_background_knowledge_not_slash_commands(self):
        for name in NEW_SKILLS:
            head = read(f"skills/{name}/SKILL.md")[:600]
            self.assertIn("user-invocable: false", head, name)

    def test_stub_remains_at_old_path_and_points_to_the_skill(self):
        for name, stub in NEW_SKILLS.items():
            t = read(stub)
            self.assertIn(name, t, f"{stub} must point to skill {name}")
            self.assertLess(len(t.splitlines()), 8, f"{stub} must stay a stub")
            self.assertIn(stub, read("profiles/universal.profile"))

    def test_merge_conversion_stubs_keep_the_critical_core(self):
        # mcp-error-handling -> windows-remote-gui: STOP-on-unreachable survives inline
        t = read("modules/deploy/mcp-error-handling.md")
        self.assertIn("STOP", t)
        self.assertIn("windows-remote-gui", t)
        self.assertIn("Windows MCP Server Error Handling",
                      read("skills/windows-remote-gui/SKILL.md"))
        # deploy-from-clean-tree -> deploy-ssh: clean-tree gate survives inline
        t = read("modules/deploy/deploy-from-clean-tree.md")
        self.assertIn("git status --porcelain", t)
        self.assertIn("deploy-ssh", t)
        self.assertIn("Never rsync/scp a Dirty Working Directory",
                      read("skills/deploy-ssh/SKILL.md"))


class TestPathRuleConversions(TestCase):
    def test_rules_exist_with_paths_frontmatter_and_in_profile(self):
        profile = read("profiles/universal.profile")
        for rel in NEW_RULES:
            t = read(rel)
            self.assertTrue(t.startswith("---"), rel)
            self.assertIn("paths:", t[:300], rel)
            self.assertIn(rel, profile)

    def test_old_module_paths_are_gone_for_path_rules(self):
        for old in ["modules/ci/no-continue-on-error.md",
                    "modules/ci/coverage-thresholds.md",
                    "modules/ci/browser-console-zero-errors.md",
                    "modules/ci/e2e-real-user-testing.md",
                    "modules/quality/database-migrations.md"]:
            self.assertFalse((ROOT / old).exists(), old)
            self.assertNotIn(old, read("profiles/universal.profile"))


class TestModelCombinationFixes(TestCase):
    def test_main_session_is_users_model_choice_not_hardcoded_opus(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("MAIN interactive session runs whatever the user set via `/model`",
                      t)
        self.assertNotIn("The primary Claude Code agent runs **Opus 4.8**", t)

    def test_unverified_community_numbers_are_labelled(self):
        self.assertIn("UNVERIFIED indicative numbers",
                      read("modules/core/model-awareness.md"))

    def test_ticket_validator_has_an_explicit_model_tier(self):
        self.assertIn("model: sonnet", read("agents/ticket-validator.md")[:400])


if __name__ == "__main__":
    main()
