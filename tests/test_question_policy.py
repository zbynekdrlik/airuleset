"""Locks the autopilot question-asking policy + the main-context-hygiene module.

User instruction (2026-07-02): a genuine per-ticket question is ASKED the moment
the ticket needs it AND it ALWAYS pings the phone (the user does not watch the
terminal). During waking hours the loop picks one of two honest forms — BLOCK
(`❓ NEEDS YOU`, wait, when nothing else is workable) or ASK-AND-CONTINUE
(`❓ ASKED` + track on the issue with a `needs-answer` comment, then work other
answer-independent tickets, ending `⏳ WORKING`). A question is NEVER suppressed,
NEVER buried (continue only AFTER the ping fired), and an unanswered pinged
question is NEVER a reason to stop the loop or reproach the user. The sleep window
00:00-05:59 Europe/Bratislava still defers with no ping. And a general rule
mandates delegating heavy reading to subagents to keep the main thread thin.

These asserts guard against a regression that silently reinstates either the old
"defer the per-ticket question and keep grinding" default OR the old "ask-and-HOLD,
block the whole loop, moving to another ticket is banned" default — both are now
superseded by ask-and-continue-with-a-guaranteed-ping.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestQuestionPolicy(TestCase):
    def test_marker_rule_asks_the_moment_and_always_pings(self):
        t = read("modules/core/message-status-marker.md")
        self.assertIn("Europe/Bratislava", t)
        self.assertIn("ASKED THE MOMENT", t)
        # A question ALWAYS pings — the core fix (removes the suppression bug).
        self.assertIn("ALWAYS pings the phone", t)
        # The two honest forms must both be documented.
        self.assertIn("❓ ASKED", t)
        self.assertIn("❓ NEEDS YOU", t)
        self.assertIn("ask-and-continue", t)
        # Lock the sleep-window hours so a silent window change trips.
        self.assertIn("00..05", t)
        # The old defer-by-default clause must be gone.
        self.assertNotIn("a per-ticket question is ALWAYS deferred", t)

    def test_marker_rule_bans_reproach_and_burying(self):
        t = read("modules/core/message-status-marker.md")
        # Never reproach the user for an unanswered (pinged) question.
        self.assertIn("čakajú na tvoje odpovede", t)
        # The buried-question form is banned: continue only AFTER the ping fired.
        self.assertIn("buried question", t.lower())

    def test_autopilot_skill_ask_and_continue_with_ping(self):
        t = read("skills/autopilot/SKILL.md")
        self.assertIn("Europe/Bratislava", t)
        # New model: ASK NOW (it pings) + ask-and-continue OR block.
        self.assertIn("ASK NOW", t)
        self.assertIn("ASK-AND-CONTINUE", t)
        self.assertIn("❓ ASKED", t)
        self.assertIn("needs-answer", t)
        # Lock the hour boundaries: defer 00..05, ask from 06:00.
        self.assertIn("00..05", t)
        self.assertIn("06:00", t)
        # The reproach / false-stop must be explicitly banned.
        self.assertIn("čakajú na tvoje odpovede", t)
        # The old "ASK NOW and HOLD, block the whole loop" wording must be gone.
        self.assertNotIn("ASK NOW and HOLD", t)
        self.assertNotIn("loop nemá stáť na čakaní", t)

    def test_worker_asks_the_moment_and_pings(self):
        t = read("agents/autopilot-worker.md")
        self.assertIn("ASK THE MOMENT", t)
        self.assertIn("MUST ping the phone", t)
        self.assertIn("❓ ASKED", t)
        self.assertIn("Europe/Bratislava", t)
        self.assertIn("00..05", t)

    def test_questions_must_be_self_contained(self):
        # The #1 repeated complaint: questions assume context the away user does not
        # have. Every question must be self-contained (zero-context briefing) and
        # every cross-project/ticket link explained. Locks the rule + the incident.
        uq = read("modules/core/user-questions-slovak.md")
        self.assertIn("ZERO context", uq)
        self.assertIn("cross-reference", uq)          # explain every cross-project link
        self.assertIn("restreamer", uq)               # the real incident as banned example
        # The autopilot ask-path + worker must cite self-containment.
        self.assertIn("self-contained", read("skills/autopilot/SKILL.md").lower())
        self.assertIn("zero context", read("agents/autopilot-worker.md").lower())

    def test_away_user_question_uses_text_marker_not_60s_dialog(self):
        # A genuine away-user question is delivered via the ❓ text marker (unlimited
        # wait + phone ping), NOT a 60-second AskUserQuestion dialog (auto-continues).
        for rel in ["modules/core/user-questions-slovak.md",
                    "modules/core/message-status-marker.md"]:
            t = read(rel)
            self.assertIn("60", t)
            self.assertIn("UNLIMITED", t)
            self.assertIn("AskUserQuestion", t)
        # worker + skill say not-a-60s-dialog too
        self.assertIn("AskUserQuestion", read("agents/autopilot-worker.md"))
        self.assertIn("60-second", read("skills/autopilot/SKILL.md"))

    def test_main_context_hygiene_module_exists_and_wired(self):
        mod = ROOT / "modules" / "core" / "main-context-hygiene.md"
        self.assertTrue(mod.is_file(), "main-context-hygiene.md must exist")
        self.assertIn("Delegate Heavy Reading to Subagents", mod.read_text(encoding="utf-8"))
        self.assertIn("modules/core/main-context-hygiene.md", read("profiles/universal.profile"))


if __name__ == "__main__":
    main()
