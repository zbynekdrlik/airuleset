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
