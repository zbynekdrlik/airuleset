"""Locks the autopilot question-asking policy + the main-context-hygiene module.

User instruction (2026-07-02): a genuine per-ticket question is ASKED the moment
the ticket needs it AND it ALWAYS pings the phone (the user does not watch the
terminal). During waking hours the loop picks one of two honest forms — BLOCK
(`❓ NEEDS YOU`, wait, when nothing else is workable) or ASK-AND-CONTINUE
(`❓ ASKED` + track on the issue with a `needs-answer` comment, then work other
answer-independent tickets, ending `⏳ WORKING`). A question is NEVER suppressed,
NEVER buried (continue only AFTER the ping fired), and an unanswered pinged
question is NEVER a reason to stop the loop or reproach the user. The sleep window
00:00-05:59 Europe/Bratislava defers ONLY while other work exists; a NECESSARY
question (nothing else workable) pings even at night. And a general rule
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
        self.assertIn("ASKED THE MOMENT", t)
        # A question ALWAYS pings — the core fix (removes the suppression bug).
        self.assertIn("ALWAYS pings the phone", t)
        # The two honest forms must both be documented.
        self.assertIn("❓ ASKED", t)
        self.assertIn("❓ NEEDS YOU", t)
        self.assertIn("ask-and-continue", t)
        # #791: NO night/day difference — the sleep window is GONE. Lock the
        # 24/7 doctrine and guard against a silent re-introduction.
        self.assertIn("24/7", t)
        self.assertNotIn("sleep window", t)
        self.assertNotIn("00..05", t)
        # The old defer-by-default clause must be gone.
        self.assertNotIn("a per-ticket question is ALWAYS deferred", t)

    def test_marker_rule_owner_scoped_delivery_710(self):
        # #710 (owner directive 2026-08-26): the DELIVERY of a question ping is
        # owner-scoped. #859 batch 4b: deep content moved to companions/skills.
        surfaces = {
            "modules/core/message-status-marker.md": None,
            "modules/core/milestone-notifications.md": "skills/milestone-notifications-deep/DEEP.md",
            "modules/core/user-questions-slovak.md": "skills/user-questions-slovak/SKILL.md",
        }
        for rel, companion in surfaces.items():
            t = read(rel)
            if companion:
                t += "\n" + read(companion)
            self.assertIn("#710", t, f"{rel}: missing the #710 owner-scope")
            self.assertIn("zbynek", t)
            self.assertIn("marek", t)
            self.assertIn("david", t)
            self.assertIn("footer `U N`", t)
        # The session discipline is explicitly stated as unchanged, not dropped.
        msm = read("modules/core/message-status-marker.md")
        self.assertIn("ALWAYS pings the phone", msm)   # the invariant is kept
        self.assertIn("owner-scoped", msm.lower())

    def test_marker_rule_bans_reproach_and_burying(self):
        t = read("modules/core/message-status-marker.md")
        # Never reproach the user for an unanswered (pinged) question.
        self.assertIn("čakajú na tvoje odpovede", t)
        # The buried-question form is banned: continue only AFTER the ping fired.
        self.assertIn("buried question", t.lower())

    def test_autopilot_skill_ask_and_continue_with_ping(self):
        t = read("skills/autopilot/SKILL.md")
        # New model: ASK NOW (it pings) + ask-and-continue OR block.
        self.assertIn("ASK NOW", t)
        self.assertIn("ASK-AND-CONTINUE", t)
        self.assertIn("❓ ASKED", t)
        self.assertIn("needs-answer", t)
        # #791: NO night/day difference — 24/7, no sleep window, no hour gate.
        self.assertIn("24/7", t)
        self.assertNotIn("sleep window", t)
        self.assertNotIn("00..05", t)
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
        # #791: NO night/day difference — 24/7, no sleep window, no hour gate.
        self.assertIn("24/7", t)
        self.assertNotIn("sleep window", t)
        self.assertNotIn("00..05", t)

    def test_questions_must_be_self_contained(self):
        # The #1 repeated complaint: questions assume context the away user does not
        # have. Every question must be self-contained (zero-context briefing) and
        # every cross-project/ticket link explained. Locks the rule + the incident.
        uq = read("modules/core/user-questions-slovak.md")
        self.assertIn("ZERO context", uq)
        self.assertIn("cross-reference", uq)          # explain every cross-project link
        # real incident (restreamer example) in the SKILL body
        skill = read("skills/user-questions-slovak/SKILL.md")
        self.assertIn("restreamer", uq + "\n" + skill)
        # The autopilot ask-path + worker must cite self-containment.
        self.assertIn("self-contained", read("skills/autopilot/SKILL.md").lower())
        self.assertIn("zero context", read("agents/autopilot-worker.md").lower())

    def test_away_user_question_uses_text_marker_not_60s_dialog(self):
        # A genuine away-user question is delivered via the ❓ text marker (unlimited
        # wait + phone ping), NOT a 60-second AskUserQuestion dialog (auto-continues).
        # #859 batch 4b: user-questions-slovak stub + SKILL carry the full detail
        uqs = read("modules/core/user-questions-slovak.md") + "\n" + read("skills/user-questions-slovak/SKILL.md")
        for t in [uqs, read("modules/core/message-status-marker.md")]:
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


class TestNoNightDayDifference(TestCase):
    """#791 (owner directive 2026-09-01: "Nech nie je rozdiel medzi nocou a
    dnom. Claude ma robit 24/7"). ALL night/sleep-window restrictions are
    REMOVED — a question is asked the moment it arises 24/7, there is no
    sleep window, no night-hour cutoff, and no question-deferral queue tied
    to the time of day. These asserts guard against a silent re-introduction
    of any night gate. Live failure that motivated it: Claude derived from
    the old sleep-window text that it should rest at night and did NOT work."""

    NIGHT_TOKENS = ("sleep window", "sleep-window", "00..05", "00:00-05:59",
                    "00:00–05:59", "even at night", "05:59")

    def _assert_no_night_gate(self, rel):
        t = read(rel)
        for tok in self.NIGHT_TOKENS:
            self.assertNotIn(tok, t, f"{rel}: night gate token {tok!r} must be gone (#791)")
        self.assertIn("24/7", t, f"{rel}: must state the 24/7 doctrine")

    def test_marker_rule_no_night_gate(self):
        self._assert_no_night_gate("modules/core/message-status-marker.md")

    def test_autopilot_skill_no_night_gate(self):
        t = read("skills/autopilot/SKILL.md")
        for tok in ("00..05", "00:00-05:59", "even at night"):
            self.assertNotIn(tok, t, f"night gate token {tok!r} must be gone (#791)")
        self.assertIn("24/7", t)
        # The idle-park ban survives, now decoupled from night.
        self.assertIn("idle-park", t)

    def test_worker_no_night_gate(self):
        self._assert_no_night_gate("agents/autopilot-worker.md")

    def test_milestone_rule_no_night_gate(self):
        self._assert_no_night_gate("modules/core/milestone-notifications.md")

    def test_master_skill_no_night_gate(self):
        # The autopilot-master LANE 4 must not carry a sleep-window deferral;
        # the airuleset:release-window DEPLOY window (a repo deploy-timing
        # param, out of scope) legitimately keeps TZ=Europe/Bratislava.
        t = read("skills/autopilot-master/SKILL.md")
        for tok in ("sleep window", "sleep-window", "00:00-06:00", "even at night"):
            self.assertNotIn(tok, t, f"night gate token {tok!r} must be gone (#791)")
        self.assertIn("24/7", t)


class TestUnansweredQuestionReaskedFull(TestCase):
    """#45 (user, 2026-07-25): an unanswered question must never be referenced
    by allusion after an intervening conversation ("pýtal som sa skôr") — it
    is asked NANOVO A CELÁ (the full self-contained block again). This is a
    DIFFERENT branch from the pre-existing VERBATIM-repeat re-poke exception
    (no user input since the last ask) documented in message-status-marker.md
    — the two must never be confused."""

    def test_marker_documents_the_reask_branch_distinct_from_verbatim(self):
        t = read("modules/core/message-status-marker.md")
        self.assertIn("Any OTHER conversation happened since the question was last asked", t)
        self.assertIn("NANOVO A CELÁ", t)
        # The pre-existing VERBATIM clause must be untouched (still there).
        self.assertIn("STILL blocked on the SAME unanswered", t)

    def test_user_questions_slovak_has_the_dedicated_section(self):
        # #859 batch 4b: stub keeps the enforcement-core; SKILL carries the full detail
        stub = read("modules/core/user-questions-slovak.md")
        skill = read("skills/user-questions-slovak/SKILL.md")
        t = stub + "\n" + skill
        self.assertIn("NANOVO", t)
        self.assertIn("zákaz odvolávok do histórie", t)
        self.assertIn("VERBATIM, byte-identical", t)
        self.assertIn("ANY conversation happened in between", t)
        self.assertIn("pýtal som sa skôr", t)
        self.assertIn("jediné otvorené rozhodnutie je X", t)
        self.assertIn("stop-check-question-quality.sh", t)
        # stub itself carries re-poke discipline + hook ref
        self.assertIn("NANOVO", stub)
        self.assertIn("stop-check-question-quality.sh", stub)

    def test_hook_documents_and_implements_the_reference_check(self):
        h = read("hooks/stop-check-question-quality.sh")
        self.assertIn("HISTORY ALLUSION", h)
        self.assertIn('VIOLATION="reference"', h)
        self.assertIn("pýtal som sa skôr", h)
