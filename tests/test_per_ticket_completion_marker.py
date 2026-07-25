"""Per-ticket completion marker convention (2026-07-25 correction batch).

OLD convention: inside an `/autopilot` / `/goal` loop, every per-ticket /
per-batch turn ended `⏳ WORKING` and `✅ DONE` was reserved for the very end
of the WHOLE backlog. Consequence: the user could never see when an
individual ticket finished, and the ticket-boundary `/compact` hook (which
keys on a terminal `✅ DONE` / `## ✅ Work Complete` and deliberately skips
`⏳`) fired roughly once per whole backlog run instead of once per ticket.

NEW convention (user directive, adopted verbatim): a completed ticket/batch
inside a loop ends with the completion report and `✅ DONE` — a genuine,
full completion of THAT unit's work. The signal that the run continues is
the ARMED GOAL Claude Code shows in its footer (`◎ /goal`), never a `⏳`
marker bolted onto an already-finished ticket. The device idle-ping is
separately guarded while the goal stays armed (the per-ticket run-card
already gives phone visibility) — see notify-discord-pending.sh's
`goal_armed()` and its behavioral tests in test_airuleset.py
(TestGoalArmedSuppressesIdlePing).

These tests lock the DOCUMENTATION side of the change: the rule modules and
skill files that encoded the OLD convention must now encode the NEW one.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestMessageStatusMarkerRule(TestCase):
    MOD = "modules/core/message-status-marker.md"

    def test_armed_goal_exception_documented(self):
        t = read(self.MOD)
        self.assertIn("ARMED", t)
        self.assertIn("◎ /goal", t)
        self.assertIn("ticket-boundary", t)
        self.assertIn("once PER TICKET", t)

    def test_reserve_working_for_genuinely_in_flight_work(self):
        t = read(self.MOD)
        self.assertIn("GENUINELY still in flight", t)


class TestMilestoneNotificationsRule(TestCase):
    MOD = "modules/core/milestone-notifications.md"

    def test_new_armed_goal_section_present(self):
        t = read(self.MOD)
        self.assertIn("ARMED GOAL", t)
        self.assertIn("◎ /goal", t)
        self.assertIn("once PER TICKET instead of once for the whole backlog", t)

    def test_idle_ping_guarded_while_goal_armed(self):
        t = read(self.MOD)
        self.assertIn("must NOT queue a SECOND idle Discord ping", t)
        self.assertIn("notify-discord-pending.sh", t)

    def test_old_reserve_for_terminal_turn_language_is_gone(self):
        t = read(self.MOD)
        self.assertNotIn(
            "ONLY the terminal turn — backlog empty, loop stops, nothing "
            "running", t)
        self.assertNotIn("never end an intermediate turn with", t)
        self.assertNotIn("reserve `✅ DONE` for the true end", t)

    def test_required_phrases_still_survive(self):
        # Locked elsewhere (test_airuleset.py, test_question_policy.py,
        # test_ruleset_conversion_wave2.py) — must still hold after this
        # section's rewrite.
        t = read(self.MOD)
        for phrase in ["Mobile-App Model",
                       "do NOT call the discord `reply` tool or `PushNotification`",
                       "⏳", "FULL completion", "IMMEDIATELY",
                       "ONLY while other answer-independent work exists",
                       "even at night", "EXCEPTION", "notify --run-card",
                       "notification-mechanics"]:
            self.assertIn(phrase, t, phrase)

    def test_question_ping_unaffected_by_goal_armed_guard(self):
        t = read(self.MOD)
        self.assertIn("a genuine question ALWAYS pings regardless of an "
                      "armed goal", t)


class TestAutopilotSkillLoopBody(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def test_per_batch_completion_ends_done_not_working(self):
        t = read(self.SKILL)
        self.assertIn("ARMED GOAL", t)
        self.assertIn("◎ /goal", t)
        self.assertIn("re-enters Step 1 for the next batch", t)

    def test_old_immediately_assemble_language_is_gone(self):
        t = read(self.SKILL)
        self.assertNotIn("Immediately assemble the next batch", t)
        self.assertNotIn("Do NOT stop to report between batches", t)
        self.assertNotIn("this loop turn ends `⏳ WORKING`", t)

    def test_batch_report_cites_the_full_template(self):
        t = read(self.SKILL)
        self.assertIn("completion-report.md", t)
        self.assertIn("/plan-check", t)
        self.assertIn("/requesting-code-review", t)

    def test_end_of_run_stop_condition_step4_untouched(self):
        # the WHOLE-backlog-empty completion report (a DIFFERENT, unchanged
        # concept from the per-batch one) must still be documented exactly
        # as before this batch.
        t = read(self.SKILL)
        self.assertIn("## Step 4 — When to actually STOP", t)
        self.assertIn("end-of-run reconciliation sweep", t)


class TestAutopilotMasterSkillLane3(TestCase):
    SKILL = "skills/autopilot-master/SKILL.md"

    def test_lane3_notes_the_new_completion_convention(self):
        t = read(self.SKILL)
        self.assertIn("never `⏳`", t)
        self.assertIn("re-evaluates all lanes fresh", t)
        # ordering must still hold (locked separately in
        # test_autopilot_master_skill.py) — sanity-check it here too.
        self.assertLess(t.index("LANE 3 CORE"), t.index("LANE 4 QUESTIONS"))


class TestGoalArmedHookStructure(TestCase):
    """Structural companion to the behavioral tests in test_airuleset.py
    (TestGoalArmedSuppressesIdlePing) — the hook must define the shared
    detector function, reusing the watchdog's OWN `◎ /goal` signal rather
    than inventing a second one."""

    HOOK = ROOT / "hooks" / "notify-discord-pending.sh"

    def test_goal_armed_function_defined(self):
        t = self.HOOK.read_text(encoding="utf-8")
        self.assertIn("goal_armed()", t)
        self.assertIn('"◎ /goal"', t)

    def test_test_injection_hook_present(self):
        t = self.HOOK.read_text(encoding="utf-8")
        self.assertIn("ND_FAKE_PANE_CAPTURE", t)


if __name__ == "__main__":
    main()
