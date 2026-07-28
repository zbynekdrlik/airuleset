"""Locks item 8 of #95: the "Pre-answered questions" table in
ask-before-assuming.md was trimmed from 26 rows to 9 (+ one pointer line).

Every dropped row's justification was "a real hook already hard-blocks this
question" — proven empirically (real hook invocation, real STDIN payload,
never a re-implementation of the hook's own regex) BEFORE the row was
removed. This test is the durable version of that empirical check: it
re-runs the SAME hooks against a representative phrase from each dropped
row, so a future edit that narrows hook coverage (e.g. a regex refactor
that accidentally drops a clause) is caught here instead of silently
reopening a question the file no longer documents as pre-answered.

Full row-by-row mapping (hook file + exact regex fragment per row):
https://github.com/zbynekdrlik/airuleset/issues/95 (design comment).
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402

HOOKS = airuleset.REPO_DIR / "hooks"


def _run_pre_ask(text):
    """PreToolUse(AskUserQuestion) hook — exit 2 means the tool call itself
    is blocked before the question ever reaches the user."""
    payload = json.dumps({"tool_input": {"question": text}})
    p = subprocess.run(
        ["bash", str(HOOKS / "pre-ask-auto-answer.sh")],
        input=payload, capture_output=True, text=True,
    )
    return p.returncode == 2


def _run_stop_prose(text):
    """Stop hook — {"decision":"block"} on stdout means the same question
    written as prose (not the AskUserQuestion tool) is also hard-blocked."""
    sid = f"test-dropped-row-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(
        ["bash", str(HOOKS / "stop-check-prose-violations.sh")],
        input=payload, capture_output=True, text=True,
    )
    return '"decision"' in p.stdout and '"block"' in p.stdout


def _run_stop_untracked_work(text):
    """Stop hook — {"decision":"block"} means "asking permission to file
    issues" is hard-blocked. Fresh session id per call: the hook caps at
    3 blocks/session, so a shared session id across calls would silently
    stop blocking after the 3rd and look like a coverage regression."""
    sid = f"test-dropped-row-ndw-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(
        ["bash", str(HOOKS / "stop-check-untracked-work.sh")],
        input=payload, capture_output=True, text=True,
    )
    return '"decision"' in p.stdout and '"block"' in p.stdout


class TestPreAskAutoAnswerCoversDroppedRows(TestCase):
    """Rows dropped because hooks/pre-ask-auto-answer.sh hard-blocks the
    AskUserQuestion tool call itself (exit code 2)."""

    def test_subagent_vs_inline_blocked(self):
        self.assertTrue(_run_pre_ask("Subagent or sequential/inline?"))

    def test_visual_companion_question_blocked(self):
        self.assertTrue(_run_pre_ask("Want visual companion / mockups?"))

    def test_ready_to_execute_say_go_blocked(self):
        self.assertTrue(_run_pre_ask("Ready to execute — say go"))

    def test_ready_for_next_step_blocked(self):
        self.assertTrue(_run_pre_ask("ready for next step?"))

    def test_spec_plan_review_handoff_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Please review the spec/plan/design and let me know if you "
            "want changes before I hand off to writing-plans"))

    def test_does_this_design_look_right_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Does this design look right? Specifically the X core. "
            "If yes, I'll write the spec to docs/.../spec.md and commit."))

    def test_dispatch_now_or_hold_for_review_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Plan committed locally as abc123. Dispatch all tasks via "
            "subagent-driven-development now, or hold for your review "
            "of the plan first?"))

    def test_admin_merge_shortcut_menu_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Realistic options: admin-merge / close PR / stop runner"))

    def test_merge_despite_failing_check_blocked(self):
        self.assertTrue(_run_pre_ask("Should I merge despite the failing check?"))

    def test_investigate_or_merge_despite_blocked(self):
        self.assertTrue(_run_pre_ask(
            "Want me to investigate the codecov issue, or merge despite it?"))

    def test_functionally_ready_unstable_blocked(self):
        self.assertTrue(_run_pre_ask(
            "PR is functionally ready but UNSTABLE — you decide on merge?"))

    def test_approve_merge_question_blocked(self):
        self.assertTrue(_run_pre_ask("Approve merge?"))


class TestStopCheckProseCoversDroppedRows(TestCase):
    """Rows dropped because hooks/stop-check-prose-violations.sh hard-blocks
    the SAME question asked in prose (a Stop-gate, not the AskUserQuestion
    tool — the constraint 4 case: Stop-gate coverage counts too)."""

    def test_subagent_vs_inline_prose_blocked(self):
        self.assertTrue(_run_stop_prose("Subagent or sequential/inline?"))

    def test_design_review_handoff_prose_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Please review the spec/plan/design and let me know if you "
            "want changes before I hand off to writing-plans"))

    def test_merge_despite_prose_blocked(self):
        self.assertTrue(_run_stop_prose("Should I merge despite the failing check?"))

    def test_ascii_art_layout_mockup_blocked(self):
        # ASCII-art ban for a layout/position question — the mechanical
        # half of the dropped visual-companion row's "never ASCII art".
        msg = (
            "Where should the version label go? Here's the header layout:\n"
            "┌─────────────┐\n"
            "│ header  logo │\n"
            "└─────────────┘"
        )
        self.assertTrue(_run_stop_prose(msg))

    def test_tester_handoff_can_you_test_blocked(self):
        self.assertTrue(_run_stop_prose("Can you test it on your end?"))

    def test_tester_handoff_please_verify_blocked(self):
        self.assertTrue(_run_stop_prose("Please verify it works"))

    def test_tester_handoff_next_user_test_blocked(self):
        self.assertTrue(_run_stop_prose("Next user test"))

    def test_tester_handoff_stop_using_you_as_tester_blocked(self):
        self.assertTrue(_run_stop_prose("stop using you as tester"))

    def test_tester_handoff_fix_locally_before_next_test_blocked(self):
        self.assertTrue(_run_stop_prose("I'll fix locally before next user test"))

    def test_tester_handoff_could_you_try_on_staging_blocked(self):
        # This is the "blocked-by-a-missing-tool" phrasing — the same
        # hard block fires, and its own violation message already carries
        # the ask-for-the-tool-not-the-test decision tree.
        self.assertTrue(_run_stop_prose("Could you try it on staging?"))


class TestStopCheckUntrackedWorkCoversDroppedRows(TestCase):
    """Rows dropped because hooks/stop-check-untracked-work.sh hard-blocks
    asking permission to file GitHub issues (Group 3 of that hook)."""

    def test_follow_up_or_do_it_now_blocked(self):
        self.assertTrue(_run_stop_untracked_work(
            "Should I file this cleanup as a follow-up issue, or do it now?"))

    def test_give_the_word_to_create_issues_blocked(self):
        self.assertTrue(_run_stop_untracked_work(
            "Give the word and I'll create the issues"))

    def test_should_i_file_these_or_hold_blocked(self):
        self.assertTrue(_run_stop_untracked_work(
            "Should I file these issues or hold?"))

    def test_want_me_to_open_the_issues_blocked(self):
        self.assertTrue(_run_stop_untracked_work(
            "Want me to open the issues?"))


class TestKeptRowsRemainUncoveredByHooks(TestCase):
    """The 9 rows that STAYED are the ones with NO matching hook pattern —
    this is the negative control proving the drop decisions above weren't
    just "every row blocks something": these representative phrases must
    NOT be hard-blocked by any of the three hooks."""

    KEPT_PHRASES = [
        "Where should I place the diagnostic QR code?",
        # NOTE: deliberately avoids the standalone phrase "which approach?" —
        # that wording alone trips pre-ask-auto-answer.sh's UNRELATED
        # process-pause pattern (`which (approach|execution|...)\?`), a
        # coincidental match on wording, not real coverage of this row's
        # actual intent (self-invented technical obstacles). This is
        # exactly the false-positive rejected-alternative-2 in #95's
        # design comment — the negative control must not reintroduce it.
        "Feature X I designed hit a technical wall — investigate the "
        "cause, ship an estimate, or show n/a?",
        "Should I continue with phase N?",
        "Should I monitor CI?",
        "Want me to verify with Playwright?",
        "Ready for issue #N+1?",
        "Should I bundle these issues or do separate PRs?",
        "Rollout plan: PR1 schema, PR2 module, PR3 route, PR4 enable",
        "Should I just say UNVERIFIED and let user test?",
    ]

    def test_kept_phrases_not_blocked_by_any_hook(self):
        for phrase in self.KEPT_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertFalse(_run_pre_ask(phrase), f"pre-ask blocked: {phrase!r}")
                self.assertFalse(_run_stop_prose(phrase), f"stop-prose blocked: {phrase!r}")
                self.assertFalse(
                    _run_stop_untracked_work(phrase),
                    f"stop-untracked-work blocked: {phrase!r}")


if __name__ == "__main__":
    main()
