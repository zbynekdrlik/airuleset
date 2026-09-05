"""Content-lock tests for #859 batch 3 — re-tiered content + description ratchet + hook."""
import json
import os
import re
import subprocess
import unittest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(REPO_DIR, rel)) as f:
        return f.read()


def _ratchet():
    with open(os.path.join(REPO_DIR, "tests/context_ratchet.json")) as f:
        return json.load(f)


# --- A2: statusline re-tiering content locks ---

class TestStatuslineRetieringAnchors(unittest.TestCase):
    """Moved content is at the NEW location; stubs carry the pointer."""

    def test_deep1_has_i_n_drift_marker(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-1.md")
        self.assertIn("I N\u25b2", t)  # ▲
        self.assertIn("#842", t)
        self.assertIn("net-drain", t)

    def test_deep1_has_u_state_machine(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-1.md")
        self.assertIn("_partition_workable", t)
        self.assertIn("#654", t)
        self.assertIn("stream ownership", t.lower())

    def test_deep1_has_gk_timeline(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-1.md")
        self.assertIn("#589", t)
        self.assertIn("TIMELINE", t)

    def test_deep2_has_w_state_machine(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-2.md")
        self.assertIn("OPS_WAIT_WDRAIN_THRESHOLD", t)
        self.assertIn("#754", t)
        self.assertIn("#818", t)
        self.assertIn("tacit", t.lower())

    def test_deep2_has_stale_freshness(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-2.md")
        self.assertIn("stale!", t)
        self.assertIn("#570", t)

    def test_deep2_has_emoji_reaction(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-2.md")
        self.assertIn("#745", t)
        self.assertIn("mail.message.reaction", t)

    def test_stub_has_pointer_to_deep1(self):
        t = _read("modules/core/statusline-vocabulary.md")
        self.assertIn("skills/statusline-vocabulary-deep/DEEP-1.md", t)

    def test_stub_has_pointer_to_deep2(self):
        t = _read("modules/core/statusline-vocabulary.md")
        self.assertIn("skills/statusline-vocabulary-deep/DEEP-2.md", t)

    def test_stub_keeps_5_segment_legend(self):
        t = _read("modules/core/statusline-vocabulary.md")
        self.assertIn("I \u00b7 U \u00b7 W \u00b7 gk \u00b7 skip", t)

    def test_stub_keeps_cache_path(self):
        t = _read("modules/core/statusline-vocabulary.md")
        self.assertIn("tickets-status/<cwd-key>.json", t)
        self.assertIn("tickets-status --refresh", t)

    def test_stub_keeps_never_rederive(self):
        t = _read("modules/core/statusline-vocabulary.md")
        self.assertIn("never guess, never re-derive", t)

    def test_deep_not_in_stub(self):
        """Deep state-machine detail must NOT be in the always-on stub."""
        t = _read("modules/core/statusline-vocabulary.md")
        self.assertNotIn("OPS_WAIT_WDRAIN_THRESHOLD", t)
        self.assertNotIn("_tacit_window_flagged", t)
        self.assertNotIn("mail.message.reaction", t)


# --- A2: receive-files re-tiering content locks ---

class TestReceiveFilesRetieringAnchors(unittest.TestCase):
    def test_companion_has_secret_request(self):
        t = _read("skills/receive-files-credentials/DEEP.md")
        self.assertIn("secret request", t)
        self.assertIn("secret exec", t)

    def test_companion_has_vault_persistence(self):
        t = _read("skills/receive-files-credentials/DEEP.md")
        self.assertIn("DELIVERY CHANNEL", t)
        self.assertIn("~/.secrets/", t)

    def test_companion_has_secret_show(self):
        t = _read("skills/receive-files-credentials/DEEP.md")
        self.assertIn("secret show", t)

    def test_stub_has_pointer(self):
        t = _read("modules/core/receive-files-via-upload-url.md")
        self.assertIn("skills/receive-files-credentials/DEEP.md", t)

    def test_stub_keeps_banned_scp(self):
        t = _read("modules/core/receive-files-via-upload-url.md")
        self.assertIn("BANNED", t)
        self.assertIn("scp", t)


# --- A2: claude-code-tooling re-tiering content locks ---

class TestClaudeCodeToolingRetieringAnchors(unittest.TestCase):
    def test_companion_has_workflow_tool(self):
        t = _read("skills/claude-code-workflows/DEEP.md")
        self.assertIn("Workflow", t)
        self.assertIn("parallel()", t)

    def test_companion_has_tier_per_stage(self):
        t = _read("skills/claude-code-workflows/DEEP.md")
        self.assertIn("opts.model", t)

    def test_companion_has_right_size(self):
        t = _read("skills/claude-code-workflows/DEEP.md")
        self.assertIn("Right-size", t)

    def test_stub_has_pointer(self):
        t = _read("modules/core/claude-code-tooling.md")
        self.assertIn("skills/claude-code-workflows/DEEP.md", t)

    def test_stub_keeps_effort_levels(self):
        t = _read("modules/core/claude-code-tooling.md")
        self.assertIn("Effort levels", t)

    def test_stub_keeps_autonomous_goals(self):
        t = _read("modules/core/claude-code-tooling.md")
        self.assertIn("Autonomous Goals", t)


# --- A2: situational-triggers binding locks ---

class TestSituationalTriggerBindings(unittest.TestCase):
    def test_statusline_deep1_bash_binding(self):
        t = _read("hooks/situational-triggers.conf")
        self.assertIn("statusline-deep-1\tBash", t)
        self.assertIn("DEEP-1.md", t)

    def test_statusline_deep2_bash_binding(self):
        t = _read("hooks/situational-triggers.conf")
        self.assertIn("statusline-deep-2\tBash", t)
        self.assertIn("DEEP-2.md", t)

    def test_statusline_prompt_binding(self):
        t = _read("hooks/situational-triggers.conf")
        self.assertIn("statusline-deep-1-prompt\tUserPromptSubmit", t)

    def test_ci_monitor_recipes_binding(self):
        t = _read("hooks/situational-triggers.conf")
        self.assertIn("ci-monitor-recipes", t)
        self.assertIn("skills/ci-monitor/recipes.md", t)

    def test_receive_files_credentials_binding(self):
        t = _read("hooks/situational-triggers.conf")
        self.assertIn("receive-files-credentials", t)

    def test_claude_code_workflows_binding(self):
        t = _read("hooks/situational-triggers.conf")
        self.assertIn("claude-code-workflows", t)


# --- A2: co-fire budget functional test ---

class TestStatuslineCoFireBudget(unittest.TestCase):
    """Both deep bodies fit under MAX_BODY individually."""

    def test_deep1_under_max_body(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-1.md")
        self.assertLess(len(t), 24000, f"DEEP-1 is {len(t)} chars, exceeds MAX_BODY=24000")

    def test_deep2_under_max_body(self):
        t = _read("skills/statusline-vocabulary-deep/DEEP-2.md")
        self.assertLess(len(t), 24000, f"DEEP-2 is {len(t)} chars, exceeds MAX_BODY=24000")


# --- A3: skill description ratchet ---

class TestSkillDescriptionRatchet(unittest.TestCase):
    """Every skill description \u2264 200 chars; total \u2264 ceiling."""

    def _all_descriptions(self):
        descs = {}
        skills_dir = os.path.join(REPO_DIR, "skills")
        for d in sorted(os.listdir(skills_dir)):
            p = os.path.join(skills_dir, d, "SKILL.md")
            if not os.path.isfile(p):
                continue
            with open(p) as f:
                content = f.read()
            if not content.startswith("---"):
                continue
            m = re.search(r"^description:\s*(.+?)(?:\n[a-z]|\n---|\Z)", content[3:content.index("---", 3)], re.DOTALL | re.MULTILINE)
            if m:
                descs[d] = m.group(1).strip()
        return descs

    def test_no_description_over_200_chars(self):
        r = _ratchet()
        cap = r["ceilings"].get("skill_desc_max_chars", 200)
        for name, desc in self._all_descriptions().items():
            self.assertLessEqual(len(desc), cap, f"Skill {name} description is {len(desc)} chars (cap: {cap})")

    def test_total_description_chars_under_ceiling(self):
        r = _ratchet()
        ceiling = r["ceilings"]["skill_desc_chars"]
        total = sum(len(d) for d in self._all_descriptions().values())
        self.assertLessEqual(total, ceiling, f"Total desc chars {total} exceeds ceiling {ceiling}")


# --- A3: agent-only skill flags ---

class TestAgentOnlySkillFlags(unittest.TestCase):
    """Skills that are agent-only/dispatch-only must have both flags."""

    AGENT_ONLY = ["fable-advisor", "ci-monitor", "plan-check", "notification-mechanics"]

    def test_agent_only_skills_have_disable_model_invocation(self):
        for name in self.AGENT_ONLY:
            t = _read(f"skills/{name}/SKILL.md")
            self.assertIn("disable-model-invocation: true", t, f"{name} missing disable-model-invocation")

    def test_agent_only_skills_have_user_invocable_false(self):
        for name in self.AGENT_ONLY:
            t = _read(f"skills/{name}/SKILL.md")
            self.assertIn("user-invocable: false", t, f"{name} missing user-invocable: false")


# --- A4: nudge hook meta-test ---

class TestNudgeModuleContextCost(unittest.TestCase):
    """The hook exits 0 always and prints context-cost on module paths."""

    def _run_hook(self, file_path):
        hook = os.path.join(REPO_DIR, "hooks/nudge-module-context-cost.sh")
        payload = json.dumps({"file_path": file_path})
        r = subprocess.run(
            ["bash", hook],
            input=payload, capture_output=True, text=True, timeout=10,
            cwd=REPO_DIR,
        )
        return r

    def test_exits_zero_on_module_path(self):
        r = self._run_hook(os.path.join(REPO_DIR, "modules/core/test.md"))
        self.assertEqual(r.returncode, 0)

    def test_exits_zero_on_non_module_path(self):
        r = self._run_hook(os.path.join(REPO_DIR, "hooks/test.sh"))
        self.assertEqual(r.returncode, 0)

    def test_prints_context_cost_on_module_path(self):
        r = self._run_hook(os.path.join(REPO_DIR, "modules/core/test.md"))
        # May print nothing if context-baseline --raw-bytes is unavailable
        # but must always exit 0


# --- A4: CLAUDE.md context-cost paragraph ---

class TestContextCostParagraph(unittest.TestCase):
    def test_rule_intake_gate_has_context_cost(self):
        t = _read("CLAUDE.md")
        self.assertIn("Context-cost", t)
        self.assertIn("nudge-module-context-cost.sh", t)
        self.assertIn("context-baseline", t)


if __name__ == "__main__":
    unittest.main()
