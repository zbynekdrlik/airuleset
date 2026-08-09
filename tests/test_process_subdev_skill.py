"""Global process-subdev skill — airuleset owns the gatekeeper side (#21).

User directive 2026-07-20: multi-subdev development is the GLOBAL approach;
airuleset owns BOTH sides of the process (autopilot = sub-dev, process-subdev
= gatekeeper); repos carry only thin parameters. Driven by the live incidents:
the 2026-07-20 morning "done without release" (the odoo-erp command's david
/goal ended at the develop merge — prod got nothing while both sides reported
done), the label-lifecycle gap (read-role sub-dev cannot remove prio:bounce),
and the user's slovnormal deploy window (22:00-06:00, prod steps only there).
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "process-subdev" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


class TestSkillExistsAndScoped(TestCase):
    def test_skill_registered(self):
        self.assertTrue(SKILL.exists())
        self.assertIn("process-subdev", airuleset.SKILL_NAMES)

    def test_gatekeeper_gets_it_subdevs_do_not(self):
        self.assertIn("process-subdev",
                      airuleset.skill_names_for_user("gatekeeper"))
        self.assertIn("process-subdev",
                      airuleset.skill_names_for_user("newlevel"))
        for u in ("david", "marek", "montalu"):
            self.assertNotIn("process-subdev",
                             airuleset.skill_names_for_user(u), u)


class TestReleaseLifecycle(TestCase):
    def test_done_means_released_for_every_stream(self):
        t = read(SKILL)
        self.assertIn("EVERY stream", t)
        self.assertIn("RELEASED", t)
        # the exact 2026-07-20 hole: a fork slice ending at the integration
        # merge is NOT done
        self.assertIn("integration merge is the MIDPOINT", t)

    def test_deploy_window_is_a_repo_parameter(self):
        t = read(SKILL)
        self.assertIn("airuleset:release-window=", t)
        self.assertIn("airuleset:prod-approval=", t)

    def test_goal_template_holds_review_watch(self):
        t = read(SKILL)
        self.assertIn("review-watch", t.lower())
        self.assertIn("⏳ WORKING", t)

    def test_anti_degradation_clause_ported(self):
        self.assertIn("depth NEVER degrades", read(SKILL))


class TestBounceLaneAlignment(TestCase):
    def test_ticket_first_never_payload_prompt(self):
        t = read(SKILL)
        self.assertIn("prio:bounce", t)
        self.assertIn("never a payload prompt", t.lower())

    def test_label_removal_is_repo_automation(self):
        # read-role sub-devs cannot remove labels — the workflow template does
        t = read(SKILL)
        self.assertIn("--remove-label prio:bounce", read(
            ROOT / "skills" / "process-subdev" / "templates" /
            "subdev-handoff-label.yml"))
        self.assertIn("subdev-handoff-label", t)

    def test_canonical_protocol_referenced(self):
        self.assertIn("Cross-stream protocol", read(SKILL))


class TestBounceRuleUpdateLoop(TestCase):
    """#222 -- every bounce is evidence the sub-dev RULES are deficient, so
    the bounce path must feed back into those rules instead of only
    re-queuing the same class of finding. User directive 2026-08-04, live
    evidence: odoo-erp #2183/#2181/#2301 bounced simultaneously; an earlier
    kiosk hand-off took 4 review rounds."""

    def test_mandatory_question_is_asked_on_every_bounce(self):
        t = read(SKILL)
        self.assertIn("which sub-dev rule", t.lower())
        self.assertIn("before hand-off", t.lower())

    def test_skipped_rule_is_named_in_the_bounce_comment(self):
        t = read(SKILL)
        self.assertIn("name", t.lower())
        self.assertIn("skipped rule", t.lower())

    def test_missing_rule_updates_the_repo_hand_off_contract(self):
        t = read(SKILL)
        self.assertIn("hand-off contract", t.lower())
        self.assertIn("same review cycle", t.lower())

    def test_airuleset_owned_gap_is_filed(self):
        t = read(SKILL)
        self.assertIn("gh issue create -R zbynekdrlik/airuleset", t)

    def test_bounce_rate_metric_is_named(self):
        t = read(SKILL)
        self.assertIn("bounce-rate", t.lower())

    def test_step_lands_inside_the_findings_bounce_bullet(self):
        # not a stray paragraph elsewhere -- it must sit right where the
        # bounce actually happens (step 5's FINDINGS branch), so a
        # gatekeeper reading that one bullet sees the whole obligation.
        t = read(SKILL)
        i = t.index("FINDINGS")
        j = t.index("Parallel-run rule")   # the next bullet after step 5
        window = t[i:j]
        self.assertIn("which sub-dev rule", window.lower())


class TestIndependentReviewFrame(TestCase):
    def test_core_review_rules_ported(self):
        t = read(SKILL)
        for phrase in ("diff FIRST", "cold read",
                       "never patches a sub-dev", "blast radius",
                       "upgrade-path", "RED→GREEN"):
            self.assertIn(phrase.lower(), t.lower(), phrase)

    def test_repo_specifics_delegated_not_hardcoded(self):
        t = read(SKILL)
        self.assertIn("repo CLAUDE.md", t)
        # generic skill must not hardcode odoo-erp stream infrastructure
        self.assertNotIn("zbynek-0:4", t)
        self.assertNotIn("kvaskodev", t)


class TestRunCardFiredOnReleasedSlice(TestCase):
    """#47: the gatekeeper's review lane completed real releases with ZERO
    Discord run-cards for a full day — process-subdev never mentioned
    `run-card` anywhere in its own body, so the ONE place that instruction
    lives (agents/autopilot-worker.md) was never loaded for this lane, which
    never dispatches an autopilot-worker subagent at all."""

    def test_run_card_instruction_present(self):
        t = read(SKILL)
        self.assertGreater(t.count("run-card"), 0)
        self.assertIn("notify --run-card", t)

    def test_fires_per_ticket_in_the_released_slice(self):
        t = read(SKILL)
        self.assertIn("EVERY ticket in the released slice", t)
        # one call per ticket, not a single card for the whole slice
        self.assertIn("one call per ticket", t)

    def test_fires_before_closing_tickets(self):
        t = read(SKILL)
        i_card = t.index("Fire the per-ticket Discord run-card")
        i_close = t.index("close the stream's tickets with merge")
        self.assertLess(i_card, i_close)

    def test_never_a_hand_fired_reply(self):
        t = read(SKILL)
        self.assertIn("never a hand-fired", t)


class TestMechanicalPreReviewGateRecheck(TestCase):
    """#278 (promoted from odoo-erp#3046): a repo-parameterized mechanical
    pre-review gate re-check must sit between step 2 (get the work) and step
    3 (independent review), as a FILTER -- never a hardcoded script path.

    Root cause: an async/periodic gate's label-time PASS can be stale by the
    time gatekeeper actually reviews (real queue latency exists), so the
    review procedure must re-run the repo's own declared gate command as the
    first thing it does, before any deep-review effort is spent."""

    def test_step_sits_between_step_2_and_step_3(self):
        t = read(SKILL)
        i2 = t.index("### 2. Get the work in front of you")
        i2b = t.index("Mechanical pre-review gate re-check")
        i3 = t.index("### 3. INDEPENDENT REVIEW")
        self.assertLess(i2, i2b)
        self.assertLess(i2b, i3)

    def test_fail_bounces_before_deep_review_pass_proceeds(self):
        t = read(SKILL)
        i2b = t.index("Mechanical pre-review gate re-check")
        i3 = t.index("### 3. INDEPENDENT REVIEW")
        window = t[i2b:i3]
        self.assertIn("FAIL", window)
        self.assertIn("bounce", window.lower())
        self.assertIn("PASS", window)
        self.assertIn("step 3", window.lower())

    def test_absent_repo_command_skips_straight_to_step_3(self):
        t = read(SKILL)
        i2b = t.index("Mechanical pre-review gate re-check")
        i3 = t.index("### 3. INDEPENDENT REVIEW")
        window = t[i2b:i3]
        self.assertIn("no such command named", window.lower())

    def test_never_hardcodes_a_specific_repo_script_path(self):
        # generic skill must not hardcode odoo-erp stream infrastructure --
        # same discipline TestIndependentReviewFrame already locks elsewhere
        t = read(SKILL)
        self.assertNotIn("subdev_handoff_gate.py", t)
        self.assertNotIn("zbynek-0:4", t)
        self.assertNotIn("kvaskodev", t)

    def test_step_is_a_repo_parameter_like_the_others(self):
        t = read(SKILL)
        i2b = t.index("Mechanical pre-review gate re-check")
        i3 = t.index("### 3. INDEPENDENT REVIEW")
        window = t[i2b:i3]
        self.assertIn("repo PARAMETER", window)


if __name__ == "__main__":
    main()
