"""Context diet tier 2 (#859 batch 1) — stub-pattern conversion of top-6 modules.

Content that moved VERBATIM from always-on modules to
`.claude/rules-reference/<module>-history.md` must stay at the NEW location
and must NOT reappear in the always-on module. Anchors from the START,
MIDDLE, and END of each moved block prove the whole block moved.

Core enforcement content that MUST stay in the always-on module is also
locked — a future "cleanup" cannot delete it.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / ".claude" / "rules-reference"


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestModelAwarenessConversion(TestCase):
    """model-awareness.md: owner directive chain → history file."""

    def setUp(self):
        self.module = _read("modules/core/model-awareness.md")
        self.history = (REF / "model-awareness-history.md").read_text(encoding="utf-8")

    # --- moved content is at the NEW location ---
    def test_standing_directive_in_history(self):
        self.assertIn("intenet je plny obrovskej nespokojnosti", self.history)

    def test_revised_2026_08_25_in_history(self):
        self.assertIn("ale ja by som chcel aby sa fable pouzival", self.history)

    def test_revised_2026_08_26_in_history(self):
        self.assertIn("Najviacej by sa mi pacilo keby sa tickety", self.history)

    def test_ultracode_history_in_file(self):
        self.assertIn("Chcel by som este aby sa claude v targetoch", self.history)

    # --- moved content is NOT in the always-on module ---
    def test_verbatim_quotes_not_in_module(self):
        self.assertNotIn("intenet je plny obrovskej nespokojnosti", self.module)

    def test_ultracode_history_not_in_module(self):
        # The full ultracode owner quote should not be in the module
        self.assertNotIn(
            "Chcel by som este aby sa claude v targetoch nespustali",
            self.module,
        )

    # --- core enforcement stays in the module ---
    def test_tier_table_stays(self):
        self.assertIn("claude-fable-5", self.module)
        self.assertIn("claude-opus-4-6", self.module)

    def test_banned_opus5_stays(self):
        self.assertIn("Opus 5", self.module)
        self.assertIn("BANNED", self.module)

    def test_fable_gate_stays(self):
        self.assertIn("fable-gate", self.module)

    def test_pointer_exists(self):
        self.assertIn("model-awareness-history.md", self.module)


class TestStatuslineVocabularyConversion(TestCase):
    """statusline-vocabulary.md: #367/#512/#526 + Q/A retired → history."""

    def setUp(self):
        self.module = _read("modules/core/statusline-vocabulary.md")
        self.history = (REF / "statusline-vocabulary-history.md").read_text(
            encoding="utf-8"
        )

    # --- moved content at NEW location ---
    def test_367_in_history(self):
        self.assertIn("THIRD footer simplification round", self.history)

    def test_512_in_history(self):
        self.assertIn("parked-segment CONSOLIDATION", self.history)

    def test_526_in_history(self):
        self.assertIn("REFINES the `needs-acceptance` placement", self.history)

    def test_qa_retired_in_history(self):
        self.assertIn("reping_stale_questions", self.history)

    # --- moved content NOT in module ---
    def test_367_not_in_module(self):
        self.assertNotIn("THIRD footer simplification round", self.module)

    def test_512_consolidation_not_in_module(self):
        self.assertNotIn("parked-segment CONSOLIDATION", self.module)

    def test_526_refines_not_in_module(self):
        self.assertNotIn(
            "REFINES the `needs-acceptance` placement into a STATE MACHINE",
            self.module,
        )

    # --- core stays ---
    def test_five_segments_stay(self):
        self.assertIn("5 segments", self.module)

    def test_i_segment_stays(self):
        self.assertIn("I N", self.module)

    def test_u_segment_stays(self):
        self.assertIn("U N", self.module)

    def test_w_segment_stays(self):
        self.assertIn("W N", self.module)

    def test_pointer_exists(self):
        self.assertIn("statusline-vocabulary-history.md", self.module)


class TestAskBeforeAssumingConversion(TestCase):
    """ask-before-assuming.md: hook coverage mapping → history."""

    def setUp(self):
        self.module = _read("modules/core/ask-before-assuming.md")
        self.history = (REF / "ask-before-assuming-history.md").read_text(
            encoding="utf-8"
        )

    def test_hook_mapping_in_history(self):
        self.assertIn("spustím/rozbehnem/začnem", self.history)

    def test_319_coverage_in_history(self):
        self.assertIn("zlúči/zmerg/mergn", self.history)

    def test_detailed_mapping_not_in_module(self):
        self.assertNotIn("spustím/rozbehnem/začnem", self.module)

    # core stays
    def test_pre_answered_table_stays(self):
        self.assertIn("Pre-answered questions", self.module)

    def test_ownership_gate_stays(self):
        self.assertIn("FIRST — before ANY question", self.module)

    def test_pointer_exists(self):
        self.assertIn("ask-before-assuming-history.md", self.module)


class TestCompletionReportConversion(TestCase):
    """completion-report.md: compact-at-boundary mechanics → history."""

    def setUp(self):
        self.module = _read("modules/core/completion-report.md")
        self.history = (REF / "completion-report-history.md").read_text(
            encoding="utf-8"
        )

    def test_822_mechanics_in_history(self):
        self.assertIn("type-ahead queue drain is NOT idempotent", self.history)

    def test_400_fallback_in_history(self):
        self.assertIn("PERMANENT NO-OP", self.history)

    def test_411_backstop_in_history(self):
        self.assertIn("SAFETY NET", self.history)

    def test_detailed_mechanics_not_in_module(self):
        self.assertNotIn(
            "type-ahead queue drain is NOT idempotent", self.module
        )

    # core stays
    def test_template_stays(self):
        self.assertIn("## ✅ Work Complete", self.module)

    def test_hard_rules_stay(self):
        self.assertIn("Hard rules", self.module)

    def test_compact_instruction_stays(self):
        self.assertIn("compact-request --self", self.module)

    def test_boundary_trigger_skips_gates_not_applies(self):
        # Y1 fix: --self deliberately SKIPS the #99/#48 gates, never "applies"
        self.assertIn("deliberately SKIPS", self.module)
        self.assertNotIn("gates apply", self.module)

    def test_pointer_exists(self):
        self.assertIn("completion-report-history.md", self.module)


class TestAutonomousVerificationConversion(TestCase):
    """autonomous-verification.md: mobile/utility/mechanization → history."""

    def setUp(self):
        self.module = _read("modules/core/autonomous-verification.md")
        self.history = (REF / "autonomous-verification-history.md").read_text(
            encoding="utf-8"
        )

    def test_mobile_incident_in_history(self):
        self.assertIn("hand-install an APK on their OWN phone 10x", self.history)

    def test_utility_directive_in_history(self):
        self.assertIn(
            "ak ti niečo chýba, máš to doinštalovať", self.history
        )

    def test_mechanization_516_in_history(self):
        self.assertIn("block-gk-request-without-selfservice.sh", self.history)

    def test_montalu3_incident_in_history(self):
        self.assertIn("montalu3 (2026-08-13) shipped order-status", self.history)

    def test_detailed_incident_not_in_module(self):
        self.assertNotIn(
            "hand-install an APK on their OWN phone 10x", self.module
        )

    # core stays
    def test_verification_protocol_stays(self):
        self.assertIn("Functional verification", self.module)

    def test_banned_phrases_stay(self):
        self.assertIn("Banned hand-off phrases", self.module)

    def test_prod_decision_tree_stays(self):
        self.assertIn("Decision tree for a prod-STATE READ", self.module)

    def test_pointer_exists(self):
        self.assertIn("autonomous-verification-history.md", self.module)


class TestClaudeCodeToolingConversion(TestCase):
    """claude-code-tooling.md: ultracode history + right-sizing → history."""

    def setUp(self):
        self.module = _read("modules/core/claude-code-tooling.md")
        self.history = (REF / "claude-code-tooling-history.md").read_text(
            encoding="utf-8"
        )

    def test_ultracode_directive_in_history(self):
        self.assertIn(
            "kazdy claude spravne pouzival multiple git worktreee",
            self.history,
        )

    def test_right_sizing_incident_in_history(self):
        self.assertIn("6 agents that EACH re-read the same three", self.history)

    def test_ultracode_directive_not_in_module(self):
        self.assertNotIn(
            "kazdy claude spravne pouzival multiple git worktreee",
            self.module,
        )

    def test_right_sizing_incident_not_in_module(self):
        self.assertNotIn(
            "6 agents that EACH re-read the same three", self.module
        )

    # core stays
    def test_effort_levels_stay(self):
        self.assertIn("Effort levels", self.module)

    def test_goal_stays(self):
        self.assertIn("Autonomous Goals", self.module)

    def test_ground_once_rule_in_companion(self):
        # #859 batch 3: moved to skills/claude-code-workflows/DEEP.md
        companion = _read("skills/claude-code-workflows/DEEP.md")
        self.assertIn("Ground ONCE, pass a digest", companion)

    def test_pointer_exists(self):
        self.assertIn("claude-code-tooling-history.md", self.module)

    def test_workflows_companion_pointer(self):
        # #859 batch 3: module carries pointer to companion
        self.assertIn("skills/claude-code-workflows/DEEP.md", self.module)


# ===== Batch 2 — deeper top-6 pass + new modules =====


class TestModelAwarenessB2(TestCase):
    """model-awareness.md batch 2: 2026-09-04 owner directive → history."""

    def setUp(self):
        self.module = _read("modules/core/model-awareness.md")
        self.history = (REF / "model-awareness-history.md").read_text(encoding="utf-8")

    def test_fable51_ban_directive_in_history(self):
        self.assertIn("Znova mam extremne zle vysleddky", self.history)

    def test_fable51_ban_directive_not_in_module(self):
        self.assertNotIn("Znova mam extremne zle vysleddky", self.module)

    def test_721_burn_narrative_not_in_module(self):
        self.assertNotIn("burn only SHIFTED, it did not shrink", self.module)

    def test_fable51_ban_rule_stays(self):
        self.assertIn("Fable 5.1", self.module)
        self.assertIn("BANNED fleet-wide", self.module)


class TestMessageStatusMarkerConversion(TestCase):
    """message-status-marker.md: incident narratives → history."""

    def setUp(self):
        self.module = _read("modules/core/message-status-marker.md")
        self.history = (REF / "message-status-marker-history.md").read_text(
            encoding="utf-8"
        )

    # --- moved content at NEW location ---
    def test_740_incident_in_history(self):
        self.assertIn("miva1 re-emitted ONE question", self.history)

    def test_791_verbatim_in_history(self):
        self.assertIn("Nech nie je rozdiel medzi nocou a dnom", self.history)

    def test_codex_bridge_in_history(self):
        self.assertIn("codex-bridge", self.history)

    def test_400_backstory_in_history(self):
        self.assertIn("permanent no-op #400", self.history)

    # --- moved content NOT in module ---
    def test_740_incident_not_in_module(self):
        self.assertNotIn("miva1 re-emitted ONE question", self.module)

    def test_791_verbatim_not_in_module(self):
        self.assertNotIn("Nech nie je rozdiel medzi nocou a dnom", self.module)

    # --- core stays ---
    def test_three_markers_stay(self):
        for m in ("NEEDS YOU", "WORKING", "DONE"):
            self.assertIn(m, self.module)

    def test_banned_section_stays(self):
        self.assertIn("stop-check-status-marker.sh", self.module)

    def test_pointer_exists(self):
        self.assertIn("message-status-marker-history.md", self.module)


class TestMilestoneNotificationsConversion(TestCase):
    """milestone-notifications.md: #134 incident + alert retirement → history."""

    def setUp(self):
        self.module = _read("modules/core/milestone-notifications.md")
        self.history = (REF / "milestone-notifications-history.md").read_text(
            encoding="utf-8"
        )

    def test_134_incident_in_history(self):
        self.assertIn("85 merged PRs", self.history)

    def test_546_retirement_in_history(self):
        self.assertIn("owner-suppressed at", self.history)

    def test_134_incident_not_in_module(self):
        self.assertNotIn("85 merged PRs", self.module)

    # core stays
    def test_enforcement_stays(self):
        self.assertIn("ENFORCED, not advisory", self.module)

    def test_anti_patterns_stay(self):
        self.assertIn("Anti-patterns", self.module)

    def test_pointer_exists(self):
        self.assertIn("milestone-notifications-history.md", self.module)


class TestCIMonitoringConversion(TestCase):
    """ci-monitoring.md: probe results + measurement stories → history."""

    def setUp(self):
        self.module = _read("modules/core/ci-monitoring.md")
        self.history = (REF / "ci-monitoring-history.md").read_text(encoding="utf-8")

    def test_110_probes_in_history(self):
        self.assertIn("8 of 8 probes", self.history)

    def test_gh_run_watch_measurement_in_history(self):
        self.assertIn("71 API calls", self.history)

    def test_owner_quote_in_history(self):
        self.assertIn("preco monitoring nespravis", self.history)

    def test_110_probes_not_in_module(self):
        self.assertNotIn("8 of 8 probes", self.module)

    def test_gh_run_watch_stats_not_in_module(self):
        self.assertNotIn("71 API calls", self.module)

    # core stays
    def test_all_jobs_rule_stays(self):
        self.assertIn("ALL jobs must pass", self.module)
        self.assertIn("Never stop at partial green", self.module)

    def test_poll_recipe_stays(self):
        self.assertIn("AIRULESET_POLL_BUDGET_S", self.module)

    def test_deploy_watch_stays(self):
        self.assertIn("DEPLOY_JOB_RE", self.module)

    def test_pointer_exists(self):
        self.assertIn("ci-monitoring-history.md", self.module)


if __name__ == "__main__":
    main()
