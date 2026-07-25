"""Locks the `session-context-cost.md` module (2026-07-25 cost-fix package) —
the missing discipline behind the measured burn: Claude Code resends the
ENTIRE conversation every turn, so context size is a budget, not a side
effect. Also locks the cross-reference pointers from `main-context-hygiene.md`
and `claude-code-tooling.md`, and the ultracode-is-opt-in note on the latter.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSessionContextCostModule(TestCase):
    def test_module_exists_and_is_short(self):
        p = ROOT / "modules" / "core" / "session-context-cost.md"
        self.assertTrue(p.is_file())
        lines = p.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 35, f"module is {len(lines)} lines, must be <=35")

    def test_registered_in_universal_profile_core_section(self):
        profile = read("profiles/universal.profile")
        self.assertIn("modules/core/session-context-cost.md", profile)
        # lands in the core group, not orphaned after the last core module
        self.assertIn("modules/core/main-context-hygiene.md\nmodules/core/session-context-cost.md",
                       profile.replace("\r\n", "\n"))

    def test_states_resend_every_turn_and_measured_split(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("resends the ENTIRE conversation", t)
        self.assertIn("92%", t)
        self.assertIn("8%", t)
        self.assertIn("code.claude.com/docs/en/costs", t)
        self.assertIn("457,000", t)
        self.assertIn("570,303", t)
        self.assertIn("999,759", t)
        self.assertIn("74 hours", t)
        self.assertIn("42,489", t)

    def test_states_manage_actions(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("/clear", t)
        self.assertIn("/compact", t)

    def test_delegate_subagent_measured_zero(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("isSidechain", t)
        self.assertIn("code.claude.com/docs/en/sub-agents", t)
        self.assertIn("main-context-hygiene.md", t)

    def test_1m_variant_cost(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("[1m]", t)
        self.assertIn("570K", t)
        self.assertIn("115K", t)
        self.assertIn("5x", t)

    def test_cache_ttl_death_spiral(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("1h to 5min", t)
        self.assertIn("code.claude.com/docs/en/prompt-caching", t)

    def test_diagnostics_pointer(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("airuleset.py burn", t)
        self.assertIn("/usage", t)

    def test_applies_to_all_rewordings_clause(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("applies to all rewordings", t.lower())

    def test_has_context_gate(self):
        t = read("modules/core/session-context-cost.md")
        self.assertIn("main-context-hygiene.md", t)
        self.assertIn("model-awareness.md", t)


class TestCrossReferences(TestCase):
    def test_main_context_hygiene_points_at_session_context_cost(self):
        t = read("modules/core/main-context-hygiene.md")
        self.assertIn("session-context-cost.md", t)
        self.assertIn("Delegate Heavy Reading to Subagents", t)   # title untouched

    def test_claude_code_tooling_points_at_session_context_cost(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("session-context-cost.md", t)

    def test_ultracode_is_opt_in_via_claude_ultra(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("claude-ultra", t)
        self.assertIn("opt-in", t.lower())
        # the existing locked anchors of the Dynamic Workflows section survive
        self.assertIn("Opt-in is harness-level, not rule-level", t)
        self.assertIn("STOP and ASK for ultracode when you'd benefit from it", t)


if __name__ == "__main__":
    main()
