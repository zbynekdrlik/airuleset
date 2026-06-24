# tests/test_playbook.py
import subprocess, sys
from pathlib import Path
from unittest import TestCase, main
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

REPO = airuleset.REPO_DIR

class TestPlaybookRuleModule(TestCase):
    def test_module_exists_and_in_profile(self):
        mod = REPO / "modules" / "core" / "project-playbook-maintenance.md"
        self.assertTrue(mod.exists(), "rule module missing")
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertIn("modules/core/project-playbook-maintenance.md", entries)

    def test_module_states_the_boundaries_and_marker(self):
        text = (REPO / "modules" / "core" / "project-playbook-maintenance.md").read_text()
        for needle in ["Playbook router", "📔 Playbook:", ".claude/skills/", "po každom tickete"]:
            self.assertIn(needle, text, f"rule module missing: {needle}")


class TestPlaybookReviewSkill(TestCase):
    def test_skill_present_and_registered(self):
        skill = REPO / "skills" / "playbook-review" / "SKILL.md"
        self.assertTrue(skill.exists(), "playbook-review SKILL.md missing")
        self.assertIn("playbook-review", airuleset.SKILL_NAMES)

    def test_skill_frontmatter_and_emits_marker(self):
        text = (REPO / "skills" / "playbook-review" / "SKILL.md").read_text()
        self.assertTrue(text.startswith("---"), "missing YAML frontmatter")
        self.assertIn("name: playbook-review", text)
        self.assertIn("description:", text)
        self.assertIn("📔 Playbook:", text)          # emits the gated line
        self.assertIn("routing", text.lower())        # applies the routing rule
