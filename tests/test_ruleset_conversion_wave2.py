"""Locks the "2nd wave" ruleset trim (#37, 2026-07-25): situational always-on
modules moved VERBATIM to on-demand skills, per the user's hard "TVRDÁ
PODMIENKA" directive on the issue — MOVE, never DELETE. Every converted
module keeps a short stub at its old path (so cross-references and the
universal.profile @import never break) and the FULL original text lands,
byte-for-byte, in a new hidden (user-invocable: false) skill.

Candidates that were test-locked heavily enough that on-demand loading would
weaken enforcement (claude-code-tooling, ci-monitoring, salvage-before-
discarding-work, durable-decisions-to-tickets) were deliberately left inline
— see the #37 completion report for the per-candidate rationale.
"""

from pathlib import Path
from unittest import TestCase, main

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# skill name -> (old module stub path, [anchor phrases that must appear
# VERBATIM in the new skill, taken from the start/middle/end of the original
# file so the test proves nothing was summarized away])
NEW_SKILLS = {
    "subagent-type-discipline": (
        "modules/core/subagent-type-discipline.md",
        [
            "**The `Agent` tool's `subagent_type` parameter MUST be one of the "
            "agent types listed in the Agent tool's own description in your "
            "environment. NEVER invent agent names. Hallucinated names burn "
            "tokens on silent fallback dispatches.**",
            "49.8k tokens spent on a real dispatch with the wrong agent",
            "`superpowers:subagent-driven-development` uses `general-purpose` "
            "for all three roles",
            "Applies to all rewordings and semantic equivalents — any "
            "made-up `<plugin>:<agent>` string is banned regardless of "
            "plugin name.",
        ],
    ),
    "verify-issue-still-valid": (
        "modules/quality/verify-issue-still-valid.md",
        [
            "**Tickets rot.**",
            "read-only **`ticket-validator`** subagent, and its verdict "
            "gates the work",
            "The intent: every ticket is re-validated against reality "
            "before a single line is written",
        ],
    ),
    "investigate-existing-first": (
        "modules/quality/investigate-existing-first.md",
        [
            "Before recommending custom development, you MUST investigate",
            "If you catch yourself listing items in a \"custom layer\" "
            "without having read the existing solution's source for each "
            "item — STOP.",
            "The bar is the same: investigate, then propose. Not the other "
            "way around.",
        ],
    ),
    "post-deploy-verification": (
        "modules/deploy/post-deploy-verification.md",
        [
            "**Verification has THREE mandatory layers — liveness, version "
            "match, AND functional.**",
            "The completion-report `✅ Deploy:` line MUST include the "
            "version read from the DOM",
            "Curl proves the server is running. Playwright proves the "
            "feature works.",
        ],
    ),
    "regression-test-first": (
        "modules/quality/regression-test-first.md",
        [
            "**Every user-reported bug is proof that a test was missing.**",
            "RED-before-GREEN, in commit order, in the same PR, every time.",
            "Applies to all rewordings and semantic equivalents of the "
            "patterns above.",
        ],
    ),
    "ci-push-discipline": (
        "modules/core/ci-push-discipline.md",
        [
            "**Every push triggers a 15-20 min CI run. Wasted runs cost "
            "time.**",
            "**Do NOT assume a concurrency group exists.**",
            "The rule: sync base → one push → one logical CI cycle → "
            "cancel superseded runs → monitor to completion.",
        ],
    ),
    "comprehensive-logging": (
        "modules/quality/comprehensive-logging.md",
        [
            "These projects are MVPs and bug-prone.",
            "When in doubt: **DB row**. Disk is cheap. Investigation time "
            "is not.",
            "Write logs as if that's the only tool you'll have.",
        ],
    ),
    "verify-launched-work-liveness": (
        "modules/quality/verify-launched-work-liveness.md",
        [
            "**Anything long-running you LAUNCH",
            "compaction",
            "Every wait has an expected duration.",
            "The intent: every long thing you launch is polled for real "
            "liveness on a bounded cadence",
        ],
    ),
    "pr-merge-policy": (
        "modules/core/pr-merge-policy.md",
        [
            "**DEFAULT: when every gate is green, MERGE — do not ask.**",
            "airuleset.py authority",
            "UNSTABLE ≠ clean. \"Functionally ready\" ≠ ready.",
        ],
    ),
    "deliver-files-as-urls": (
        "modules/core/deliver-files-as-urls.md",
        [
            "receive-files-via-upload-url",
            "python3 ~/devel/airuleset/airuleset.py share <path-to-file>",
            "The intent: every file the user needs lands in their hands as "
            "one clickable LAN link",
        ],
    ),
}


class TestWave2SkillConversions(TestCase):
    def test_skills_exist_and_are_managed(self):
        for name in NEW_SKILLS:
            self.assertTrue(
                (ROOT / "skills" / name / "SKILL.md").exists(), name)
            self.assertIn(name, airuleset.SKILL_NAMES)

    def test_skills_are_background_knowledge_not_slash_commands(self):
        for name in NEW_SKILLS:
            head = read(f"skills/{name}/SKILL.md")[:700]
            self.assertIn("user-invocable: false", head, name)
            self.assertIn("name: " + name, head, name)

    def test_skill_contains_the_full_original_text_verbatim(self):
        for name, (stub, anchors) in NEW_SKILLS.items():
            skill_text = read(f"skills/{name}/SKILL.md")
            for anchor in anchors:
                self.assertIn(anchor, skill_text,
                               f"{name}: missing anchor {anchor!r}")

    def test_stub_remains_at_old_path_short_and_points_to_the_skill(self):
        profile = read("profiles/universal.profile")
        for name, (stub, _anchors) in NEW_SKILLS.items():
            self.assertTrue((ROOT / stub).exists(), stub)
            t = read(stub)
            self.assertIn(name, t, f"{stub} must point to skill {name}")
            self.assertLess(len(t.splitlines()), 12,
                             f"{stub} must stay a stub, was {len(t.splitlines())} lines")
            # never deleted from the profile — same import path, now a stub
            self.assertIn(stub, profile)

    def test_stub_never_shorter_than_a_bare_pointer_would_allow_content_loss(self):
        # Sanity: a stub must not be EMPTY / a single line with nothing else —
        # it should carry at minimum the heading + pointer paragraph.
        for name, (stub, _anchors) in NEW_SKILLS.items():
            t = read(stub)
            self.assertGreater(len(t.strip()), 80, stub)

    # --- Light-lock guards that pre-date this ticket: the stub must keep the
    # specific phrases OTHER tests already assert against the module path. ---

    def test_pr_merge_policy_stub_keeps_authority_phrase_and_drops_ping_line(self):
        t = read("modules/core/pr-merge-policy.md")
        self.assertIn("airuleset.py authority", t)
        self.assertNotIn("Send the milestone ping", t)

    def test_deliver_files_stub_keeps_cross_reference(self):
        t = read("modules/core/deliver-files-as-urls.md")
        self.assertIn("receive-files-via-upload-url", t)

    def test_verify_launched_work_liveness_stub_keeps_locked_phrases(self):
        t = read("modules/quality/verify-launched-work-liveness.md")
        self.assertIn("compaction", t)
        self.assertIn("SIGTERMed with no re-invoke", t)


class TestMilestoneNotificationsPartialSplit(TestCase):
    """milestone-notifications.md keeps the BEHAVIORAL core inline (the
    decision policy + everything test-locked elsewhere) and moves the pure
    MECHANISM narration (API-error watchdog, per-owner thread + mirror
    routing, the per-ticket card's field-by-field composition) to a new
    `notification-mechanics` skill — the actionable exception itself stays
    summarized inline."""

    MOD = "modules/core/milestone-notifications.md"
    SKILL = "skills/notification-mechanics/SKILL.md"

    def test_skill_exists_and_managed(self):
        self.assertTrue((ROOT / self.SKILL).exists())
        self.assertIn("notification-mechanics", airuleset.SKILL_NAMES)
        head = read(self.SKILL)[:700]
        self.assertIn("user-invocable: false", head)

    def test_moved_sections_present_verbatim_in_skill(self):
        s = read(self.SKILL)
        for anchor in [
            # #546: the api-error PING was retired; the skill now documents the
            # suppression mechanism instead of the old sanctioned-ping wording.
            "These automated alert classes are owner-suppressed at "
            "`notify.send()` and no longer ping the device.",
            "Each project runs in a tmux session grouped `zbynek` or "
            "`marek`.",
            "**Parallel mirror recipients (`DISCORD_MIRROR_<OWNER>`).**",
            "header **🎫 #N** (number only — the technical title is "
            "dropped)",
            "**Deduped on repo-name#issue**",
        ]:
            self.assertIn(anchor, s, f"missing anchor {anchor!r}")

    def test_required_phrases_survive_inline(self):
        # These are asserted by pre-existing tests (test_airuleset.py,
        # test_question_policy.py) directly against the module path — they
        # MUST still be true after the split.
        t = read(self.MOD)
        self.assertIn("Mobile-App Model", t)
        self.assertIn(
            "do NOT call the discord `reply` tool or `PushNotification`", t)
        self.assertIn("⏳", t)
        self.assertIn("FULL completion", t)
        self.assertIn("IMMEDIATELY", t)
        self.assertIn("ONLY while other answer-independent work exists", t)
        self.assertIn("even at night", t)

    def test_exception_still_summarized_inline(self):
        t = read(self.MOD)
        self.assertIn("EXCEPTION", t)
        self.assertIn("notify --run-card", t)
        self.assertIn("notification-mechanics", t)

    def test_module_shrank_by_moving_pure_mechanism_sections(self):
        # A real, meaningful reduction (original was 2082 words) — not a
        # rewrite of the kept content, just the pure-mechanism sections gone.
        # #546 raised the cap 1700 -> 1750: the api-error/limit/burn alert
        # class was RETIRED, and that is a genuine always-on POLICY inversion
        # this always-on module must carry (a concise note; the mechanism
        # detail stays in the notification-mechanics skill) — still 17% below
        # the 2082-word original, so the "shrank meaningfully" intent holds.
        t = read(self.MOD)
        self.assertLess(len(t.split()), 1750)


class TestDeliberatelyKeptInlineCandidates(TestCase):
    """Documents (and locks) the candidates from the #37 table that were
    evaluated and deliberately NOT converted, because on-demand loading
    would weaken enforcement that other tests already depend on."""

    def test_claude_code_tooling_stays_inline(self):
        self.assertIn("modules/core/claude-code-tooling.md",
                       read("profiles/universal.profile"))
        self.assertNotIn("claude-code-tooling", airuleset.SKILL_NAMES)

    def test_ci_monitoring_stays_inline(self):
        self.assertIn("modules/core/ci-monitoring.md",
                       read("profiles/universal.profile"))
        self.assertNotIn("ci-monitoring", airuleset.SKILL_NAMES)

    def test_salvage_before_discarding_work_stays_inline(self):
        self.assertIn("modules/core/salvage-before-discarding-work.md",
                       read("profiles/universal.profile"))
        self.assertNotIn("salvage-before-discarding-work", airuleset.SKILL_NAMES)

    def test_durable_decisions_to_tickets_stays_inline(self):
        self.assertIn("modules/quality/durable-decisions-to-tickets.md",
                       read("profiles/universal.profile"))
        self.assertNotIn("durable-decisions-to-tickets", airuleset.SKILL_NAMES)


if __name__ == "__main__":
    main()
