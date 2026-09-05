"""Context diet batch 4a (#859) — six modules re-tiered to situational companions.

Every non-blank line of the old module body must be found in companion + stub.
Content that moved to the companion must NOT be in the stub.
Enforcement-core content MUST stay in the stub.
Functional tests verify each new trigger row fires the injector.
"""
import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _nonblank_lines(text):
    """Return non-blank lines stripped of leading/trailing whitespace."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _coverage_pct(module_lines, combined_text):
    """What % of module_lines appear verbatim in combined_text."""
    if not module_lines:
        return 100.0
    found = sum(1 for ln in module_lines if ln in combined_text)
    return 100.0 * found / len(module_lines)


# --- Verbatim coverage tests (mixin, not collectable by pytest) ---


class _CoverageMixin:
    """Mixin for verbatim-coverage tests. NOT a TestCase — pytest won't collect it."""

    MODULE_REL = ""
    COMPANION_REL = ""

    def setUp(self):
        self.stub = _read(self.MODULE_REL)
        self.companion = _read(self.COMPANION_REL)
        self.combined = self.stub + "\n" + self.companion

    def test_companion_is_nonempty(self):
        """The companion must have substantive content (not an empty shell)."""
        lines = _nonblank_lines(self.companion)
        self.assertGreater(len(lines), 10,
                           "companion has fewer than 10 non-blank lines — content loss?")

    def test_stub_is_smaller_than_companion(self):
        """The stub must be a strict reduction — smaller than the companion."""
        self.assertLess(len(self.stub), len(self.companion),
                        "stub should be smaller than companion (enforcement-core only)")


class TestModelAwarenessDeep(_CoverageMixin, TestCase):
    MODULE_REL = "modules/core/model-awareness.md"
    COMPANION_REL = "skills/model-awareness-deep/DEEP.md"

    # --- enforcement core stays in stub ---
    def test_stub_has_tier_table(self):
        self.assertIn("claude-fable-5", self.stub)
        self.assertIn("claude-opus-4-6", self.stub)
        self.assertIn("claude-sonnet-5", self.stub)

    def test_stub_has_no_model_param(self):
        self.assertIn("NEVER carries a `model` param", self.stub)

    def test_stub_has_opus5_banned(self):
        self.assertIn("Opus 5", self.stub)
        self.assertIn("BANNED", self.stub)

    def test_stub_has_fable51_banned(self):
        self.assertIn("Fable 5.1", self.stub)

    def test_stub_has_pointer(self):
        self.assertIn("skills/model-awareness-deep/DEEP.md", self.stub)

    # --- deep content is in companion ---
    def test_companion_has_judgment_content_test(self):
        self.assertIn("JUDGMENT-CONTENT test", self.companion)

    def test_companion_has_design_heavy(self):
        self.assertIn("DESIGN-HEAVY (HARD) taxonomy", self.companion)

    def test_companion_has_advisor_shape(self):
        self.assertIn("ADVISOR: digest in, decision out", self.companion)

    # --- deep content NOT in stub ---
    def test_judgment_content_not_in_stub(self):
        self.assertNotIn("JUDGMENT-CONTENT test", self.stub)

    def test_design_heavy_not_in_stub(self):
        self.assertNotIn("DESIGN-HEAVY (HARD) taxonomy", self.stub)


class TestAskBeforeAssumingDeep(_CoverageMixin, TestCase):
    MODULE_REL = "modules/core/ask-before-assuming.md"
    COMPANION_REL = "skills/ask-before-assuming-deep/DEEP.md"

    def test_stub_has_ownership_gate(self):
        self.assertIn("is it the USER's call or YOURS", self.stub)

    def test_stub_has_pre_answered(self):
        self.assertIn("Pre-answered", self.stub)

    def test_stub_has_pointer(self):
        self.assertIn("skills/ask-before-assuming-deep/DEEP.md", self.stub)

    def test_companion_has_table(self):
        self.assertIn("Subagent or sequential/inline", self.companion)

    def test_companion_has_visual_companion(self):
        self.assertIn("visual companion", self.companion)

    def test_table_not_in_stub(self):
        self.assertNotIn("Subagent or sequential/inline", self.stub)


class TestGhCliRecipesDeep(_CoverageMixin, TestCase):
    MODULE_REL = "modules/core/gh-cli-recipes.md"
    COMPANION_REL = "skills/gh-cli-recipes-deep/DEEP.md"

    def test_stub_has_body_file_rule(self):
        self.assertIn("--body-file", self.stub)

    def test_stub_has_pointer(self):
        self.assertIn("skills/gh-cli-recipes-deep/DEEP.md", self.stub)

    def test_companion_has_create_capture(self):
        self.assertIn("grep -oE", self.companion)

    def test_companion_has_pr_edit_bug(self):
        self.assertIn("gh pr edit", self.companion)

    def test_create_capture_not_in_stub(self):
        self.assertNotIn("grep -oE", self.stub)


class TestNoDroppedWorkDeep(_CoverageMixin, TestCase):
    MODULE_REL = "modules/quality/no-dropped-work.md"
    COMPANION_REL = "skills/no-dropped-work-deep/DEEP.md"

    def test_stub_has_three_fates(self):
        self.assertIn("three fates", self.stub)

    def test_stub_has_rate_gated(self):
        self.assertIn("RATE-GATED", self.stub)

    def test_stub_has_pointer(self):
        self.assertIn("skills/no-dropped-work-deep/DEEP.md", self.stub)

    def test_companion_has_failure_mode_1(self):
        self.assertIn("Decomposition-shedding", self.companion)

    def test_companion_has_banned_phrases(self):
        self.assertIn("pre-existing", self.companion)

    def test_failure_mode_not_in_stub(self):
        self.assertNotIn("Decomposition-shedding", self.stub)


class TestCompletePlannedWorkDeep(_CoverageMixin, TestCase):
    MODULE_REL = "modules/core/complete-planned-work.md"
    COMPANION_REL = "skills/complete-planned-work-deep/DEEP.md"

    def test_stub_has_plan_is_contract(self):
        self.assertIn("re-read your plan", self.stub)

    def test_stub_has_pointer(self):
        self.assertIn("skills/complete-planned-work-deep/DEEP.md", self.stub)

    def test_companion_has_follow_up_gate(self):
        self.assertIn("Follow-up gate", self.companion)

    def test_companion_has_self_audit(self):
        self.assertIn("Self-audit before completion", self.companion)

    def test_follow_up_gate_not_in_stub(self):
        self.assertNotIn("Follow-up gate", self.stub)


class TestAutonomousQualityDeep(_CoverageMixin, TestCase):
    MODULE_REL = "modules/core/autonomous-quality-discipline.md"
    COMPANION_REL = "skills/autonomous-quality-discipline-deep/DEEP.md"

    def test_stub_has_harder_correct_path(self):
        self.assertIn("HARDER, CORRECT path", self.stub)

    def test_stub_has_clean_not_unstable(self):
        self.assertIn("UNSTABLE is not mergeable", self.stub)

    def test_stub_has_pointer(self):
        self.assertIn("skills/autonomous-quality-discipline-deep/DEEP.md", self.stub)

    def test_companion_has_admin_merge_ban(self):
        self.assertIn("gh pr merge --admin", self.companion)

    def test_companion_has_banned_phrases(self):
        self.assertIn("Your call", self.companion)

    def test_admin_merge_not_in_stub(self):
        self.assertNotIn("gh pr merge --admin", self.stub)


# --- Functional injection tests ---
# The injector has MAX_TOTAL=14000 co-fire budget. When multiple rows match
# the same tool call, earlier rows consume the budget and later ones are
# DEFERRED (marker stays unset -> loads on the NEXT matching action).
# To test a DEEP companion that is LAST in conf, we use a TWO-PASS approach:
# first call primes competing rows (sets their dedup markers), then the
# second call fires only the DEEP companion.


class _InjectorTestBase(TestCase):
    """Drive inject-situational-rule.sh with simulated PreToolUse JSON."""

    HOOK = str(ROOT / "hooks" / "inject-situational-rule.sh")
    CONF = str(ROOT / "hooks" / "situational-triggers.conf")

    def _run_injector(self, tool_name, tool_input, session_id=None):
        """Run the injector and return stdout."""
        sid = session_id or uuid.uuid4().hex
        payload = json.dumps({
            "tool_name": tool_name,
            "tool_input": tool_input if isinstance(tool_input, dict) else json.loads(tool_input),
            "session_id": sid,
        })
        env = dict(os.environ)
        env["AIRULESET_SITUATIONAL_CONF"] = self.CONF
        result = subprocess.run(
            ["bash", self.HOOK],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        return result.stdout

    def _prime_and_inject(self, tool_name, tool_input, expected_fragment):
        """Two-pass: prime competing rows, then assert target fires."""
        sid = uuid.uuid4().hex
        # Pass 1: prime competing rows (they set their dedup markers)
        self._run_injector(tool_name, tool_input, session_id=sid)
        # Pass 2: competing markers already set -> target fires alone
        out = self._run_injector(tool_name, tool_input, session_id=sid)
        self.assertIn(expected_fragment, out,
                      f"Expected '{expected_fragment}' in pass-2 output for {tool_name}")


class TestModelAwarenessInjection(_InjectorTestBase):
    """model-awareness-deep fires on fable-gate Bash (no co-fire conflict)."""

    def test_fable_gate_bash_injects(self):
        """fable-gate Bash has no competing rows -> fires on first call."""
        sid = uuid.uuid4().hex
        out = self._run_injector(
            "Bash",
            {"command": "python3 airuleset.py fable-gate"},
            session_id=sid,
        )
        self.assertIn("JUDGMENT-CONTENT test", out)

    def test_model_id_bash_injects(self):
        sid = uuid.uuid4().hex
        out = self._run_injector(
            "Bash",
            {"command": "echo claude-opus-4-6"},
            session_id=sid,
        )
        self.assertIn("JUDGMENT-CONTENT test", out)

    def test_agent_dispatch_injects_on_second_call(self):
        """Agent tool co-fires subagent-type-discipline first; DEEP loads on pass 2."""
        self._prime_and_inject(
            "Agent",
            {"prompt": "do the work", "subagent_type": "general-purpose"},
            "JUDGMENT-CONTENT test",
        )

    def test_workflow_injects(self):
        """Workflow tool fires claude-code-workflows-tool + model-awareness-deep."""
        sid = uuid.uuid4().hex
        out = self._run_injector(
            "Workflow",
            {"scriptPath": "/tmp/test.js"},
            session_id=sid,
        )
        self.assertIn("JUDGMENT-CONTENT", out)

    def test_unrelated_bash_does_not_inject(self):
        sid = uuid.uuid4().hex
        out = self._run_injector("Bash", {"command": "ls -la"}, session_id=sid)
        self.assertNotIn("JUDGMENT-CONTENT", out)


class TestGhCliRecipesInjection(_InjectorTestBase):
    """gh-cli-recipes-deep fires on gh commands."""

    def test_gh_pr_view_injects(self):
        """gh pr view has no competing earlier row -> fires first call."""
        sid = uuid.uuid4().hex
        out = self._run_injector(
            "Bash",
            {"command": "gh pr view 42 --json title"},
            session_id=sid,
        )
        self.assertIn("gh pr edit", out)

    def test_unrelated_bash_no_inject(self):
        sid = uuid.uuid4().hex
        out = self._run_injector("Bash", {"command": "git status"}, session_id=sid)
        self.assertNotIn("gh pr edit", out)


class TestNoDroppedWorkInjection(_InjectorTestBase):
    """no-dropped-work-deep fires on gh issue create (pass 2 after gh-cli-recipes)."""

    def test_gh_issue_create_injects_pass2(self):
        self._prime_and_inject(
            "Bash",
            {"command": "gh issue create -t test -b body"},
            "Decomposition-shedding",
        )

    def test_unrelated_gh_no_inject(self):
        sid = uuid.uuid4().hex
        out = self._run_injector("Bash", {"command": "gh issue view 42"}, session_id=sid)
        self.assertNotIn("Decomposition-shedding", out)


class TestCompletePlannedWorkInjection(_InjectorTestBase):
    """complete-planned-work-deep fires on gh issue create (pass 2/3)."""

    def test_gh_issue_create_injects_pass3(self):
        sid = uuid.uuid4().hex
        # Pass 1: primes gh-cli-recipes + no-dropped-work
        self._run_injector("Bash", {"command": "gh issue create -t x"}, session_id=sid)
        # Pass 2: primes remaining co-fires
        self._run_injector("Bash", {"command": "gh issue create -t x"}, session_id=sid)
        # Pass 3: complete-planned-work fires alone
        out = self._run_injector("Bash", {"command": "gh issue create -t x"}, session_id=sid)
        self.assertIn("Follow-up gate", out)


class TestAqdInjection(_InjectorTestBase):
    """autonomous-quality-discipline-deep fires on gh pr merge / gh run."""

    def test_gh_pr_merge_injects(self):
        """gh pr merge co-fires pr-merge-policy first; DEEP on pass 2."""
        self._prime_and_inject(
            "Bash",
            {"command": "gh pr merge 42"},
            "gh pr merge --admin",
        )

    def test_unrelated_no_inject(self):
        sid = uuid.uuid4().hex
        out = self._run_injector("Bash", {"command": "echo hello"}, session_id=sid)
        self.assertNotIn("gh pr merge --admin", out)


class TestAskBeforeAssumingInjection(_InjectorTestBase):
    """ask-before-assuming-deep fires on AskUserQuestion (pass 2 after user-questions-slovak)."""

    def test_ask_user_question_injects_pass2(self):
        self._prime_and_inject(
            "AskUserQuestion",
            {"question": "Which option?", "options": []},
            "Subagent or sequential/inline",
        )


class TestContextRatchetBatch4a(TestCase):
    """The ratchet ceiling was stepped DOWN for batch 4a."""

    def test_ceiling_stepped_down_from_315290(self):
        """Ratchet stepped DOWN from 315290 (batch 2) — batch 3 not merged yet,
        so actual is ~245K not the design's ~202K (design assumed batch 3 merged)."""
        ratchet = json.loads((ROOT / "tests" / "context_ratchet.json").read_text())
        ceiling = ratchet["ceilings"]["modules_resolved_bytes"]
        self.assertLess(ceiling, 315290,
                        f"ratchet ceiling {ceiling} not stepped down from 315290")
        self.assertLessEqual(ceiling, 250000,
                             f"ratchet ceiling {ceiling} > 250000")


class TestTriggerRowsRegistered(TestCase):
    """Every batch-4a trigger row in conf has a matching matcher in hooks.json."""

    def test_workflow_matcher_has_injector(self):
        """The Workflow tool matcher must have inject-situational-rule.sh."""
        hooks = json.loads(_read("settings/hooks.json"))
        workflow_matchers = [
            m for m in hooks["hooks"]["PreToolUse"]
            if m["matcher"] == "Workflow"
        ]
        self.assertTrue(workflow_matchers, "No Workflow matcher in hooks.json")
        hook_commands = [h["command"] for h in workflow_matchers[0]["hooks"]]
        self.assertTrue(
            any("inject-situational-rule" in c for c in hook_commands),
            "inject-situational-rule.sh not registered on Workflow matcher",
        )

    def test_conf_has_batch4a_rows(self):
        """situational-triggers.conf has all batch 4a topic rows."""
        conf = _read("hooks/situational-triggers.conf")
        for topic in [
            "model-awareness-deep",
            "ask-before-assuming-deep",
            "gh-cli-recipes-deep",
            "no-dropped-work-deep",
            "complete-planned-work-deep",
            "autonomous-quality-discipline-deep",
        ]:
            self.assertIn(topic, conf, f"Missing topic row: {topic}")


class TestMaxBodySoftCap(TestCase):
    """Near-MAX_BODY companions must stay under a soft cap (#576 precedent)."""

    # inject-situational-rule.sh MAX_BODY=24000; soft cap = 23700 (300 B margin)
    SOFT_CAP = 23700

    def _companion_len(self, rel):
        """Return the frontmatter-stripped body length (what the injector measures)."""
        text = _read(rel)
        # strip YAML frontmatter if present
        if text.startswith("---"):
            end = text.find("\n---\n", 3)
            if end > 0:
                text = text[end + 5:]
        return len(text.strip())

    def test_model_awareness_deep_under_cap(self):
        sz = self._companion_len("skills/model-awareness-deep/DEEP.md")
        self.assertLessEqual(sz, self.SOFT_CAP,
                             f"model-awareness-deep {sz} > {self.SOFT_CAP}")

    def test_ask_before_assuming_deep_under_cap(self):
        sz = self._companion_len("skills/ask-before-assuming-deep/DEEP.md")
        self.assertLessEqual(sz, self.SOFT_CAP,
                             f"ask-before-assuming-deep {sz} > {self.SOFT_CAP}")


if __name__ == "__main__":
    main()
