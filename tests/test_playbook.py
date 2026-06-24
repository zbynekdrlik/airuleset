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


import json

class TestPlaybookStopHook(TestCase):
    HOOK = str(REPO / "hooks" / "stop-check-playbook-review.sh")
    RETRY_FILE = "/tmp/airuleset-playbook-block-test-pb"

    def setUp(self):
        import os
        try:
            os.remove(self.RETRY_FILE)
        except FileNotFoundError:
            pass

    def tearDown(self):
        import os
        try:
            os.remove(self.RETRY_FILE)
        except FileNotFoundError:
            pass

    def _run(self, msg):
        payload = json.dumps({"last_assistant_message": msg, "session_id": "test-pb"})
        return subprocess.run(["bash", self.HOOK], input=payload,
                              capture_output=True, text=True)

    def test_completion_report_without_marker_blocks(self):
        msg = "## ✅ Work Complete\n\nGoal: x\nPR #5 merged abc123"
        out = self._run(msg)
        self.assertIn("decision", out.stdout)
        self.assertIn("block", out.stdout)

    def test_completion_report_with_marker_passes(self):
        msg = "## ✅ Work Complete\n\n📔 Playbook: naučil som build cez CI\nPR #5"
        out = self._run(msg)
        self.assertNotIn("block", out.stdout)

    def test_non_completion_message_passes(self):
        out = self._run("just a normal status update ✅ DONE: hotovo")
        self.assertNotIn("block", out.stdout)
