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


if __name__ == "__main__":
    main()
