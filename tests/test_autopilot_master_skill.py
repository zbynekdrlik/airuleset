"""Gatekeeper umbrella loop /autopilot-master — lane scheduler, never idle (#22).

User directive 2026-07-20: the gatekeeper stands idle for long stretches —
each single-lane armed loop (/autopilot on its own backlog, /process-subdev on
a stream queue) parks the WHOLE session while waiting (deploy window, bounced
tickets), even though other lanes have workable items and questions for the
user go unasked. /autopilot-master multiplexes the lanes under ONE /goal:
review → release (prep anytime, prod deploy only inside the declared window) →
own core backlog → user questions; HOLD only when every lane is empty.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "autopilot-master" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


class TestSkillExistsAndScoped(TestCase):
    def test_skill_registered(self):
        self.assertTrue(SKILL.exists())
        self.assertIn("autopilot-master", airuleset.SKILL_NAMES)

    def test_gatekeeper_gets_it_subdevs_do_not(self):
        self.assertIn("autopilot-master",
                      airuleset.skill_names_for_user("gatekeeper"))
        self.assertIn("autopilot-master",
                      airuleset.skill_names_for_user("newlevel"))
        for u in ("david", "marek", "montalu"):
            self.assertNotIn("autopilot-master",
                             airuleset.skill_names_for_user(u), u)


class TestLaneScheduler(TestCase):
    def test_four_lanes_plus_hold_in_priority_order(self):
        t = read(SKILL)
        for lane in ("LANE 1 REVIEW", "LANE 2 RELEASE", "LANE 3 CORE",
                     "LANE 4 QUESTIONS"):
            self.assertIn(lane, t, lane)
        # priority order is positional — review before release before core
        self.assertLess(t.index("LANE 1 REVIEW"), t.index("LANE 2 RELEASE"))
        self.assertLess(t.index("LANE 2 RELEASE"), t.index("LANE 3 CORE"))
        self.assertLess(t.index("LANE 3 CORE"), t.index("LANE 4 QUESTIONS"))

    def test_never_idle_while_any_lane_has_work(self):
        # the 2026-07-20 pain: single-lane waits parked the whole gatekeeper
        self.assertIn("NEVER idles while ANY lane has work", read(SKILL))

    def test_hold_is_foreground_and_rechecks_all_lanes(self):
        t = read(SKILL)
        self.assertIn("FOREGROUND sleep-poll", t)
        self.assertIn("NEVER a wakeup/schedule mechanism", t)
        self.assertIn("re-check", t.lower())
        self.assertIn("ALL lanes", t)


class TestReleaseWindowSemantics(TestCase):
    def test_prep_anytime_prod_only_inside_window(self):
        t = read(SKILL)
        self.assertIn("airuleset:release-window", t)
        self.assertIn("PREP", t)
        self.assertIn("STAGED", t)
        self.assertIn("Europe/Bratislava", t)

    def test_window_spanning_midnight_wraps(self):
        self.assertIn("midnight", read(SKILL).lower())

    def test_approval_asked_at_stage_time_and_carries(self):
        # ask when the release is STAGED (possibly daytime), deploy inside the
        # window WITHOUT re-asking — the granted approval carries over
        t = read(SKILL)
        self.assertIn("airuleset:prod-approval", t)
        self.assertIn("no re-ask", t.lower())


class TestQuestionLane(TestCase):
    def test_ask_and_continue_one_at_a_time(self):
        t = read(SKILL)
        self.assertIn("❓ ASKED", t)
        self.assertIn("⏳ WORKING", t)
        self.assertIn("ONE at a time", t)
        self.assertIn("needs-decision", t)

    def test_answers_also_read_from_ticket_comments(self):
        # the watchdog's ticket-fallback (2026-07-20 #1832 incident) delivers a
        # blocked answer as a gh comment — the lane must re-read asked tickets
        self.assertIn("ticket-fallback", read(SKILL))


class TestCanonicalBodiesReused(TestCase):
    def test_lanes_delegate_to_canonical_skills(self):
        t = read(SKILL)
        self.assertIn("process-subdev", t)
        self.assertIn("autopilot", t)
        self.assertIn("ticket-validator", t)
        self.assertIn("autopilot-worker", t)
        # serial-per-repo worker guard survives under the master
        self.assertIn("serial per repo", t.lower())

    def test_anti_degradation_clause_ported(self):
        self.assertIn("depth NEVER degrades", read(SKILL))


class TestGoalTemplate(TestCase):
    def _goal_line(self):
        for line in read(SKILL).splitlines():
            if line.startswith("/goal "):
                return line
        return None

    def test_goal_is_one_pasteable_line(self):
        # The goal MUST stay ONE physical line: stop-check-prose-violations.sh
        # MSG_NOGOAL strips only `/goal `-prefixed lines — a reflowed template's
        # continuation lines would escape the strip and re-trip the
        # dispatch-or-hold check (the 2026-07-20 montalu hook spin). The tail
        # assertions below run against this SAME line, so a reflow fails them.
        self.assertIsNotNone(self._goal_line())

    def test_done_means_everything_shipped(self):
        g = self._goal_line()
        self.assertIn("-label:autopilot-skip", g)
        self.assertIn("RELEASED", g)
        self.assertIn("verified", g.lower())

    def test_goal_carries_rearm_and_stop_conditions(self):
        g = self._goal_line()
        self.assertIn("re-print", g)
        self.assertIn("❓ NEEDS YOU", g)
        self.assertIn("two real attempts", g)

    def test_goal_never_gates_beyond_declared_params(self):
        self.assertIn("Never gate on prod-usage", self._goal_line())

    def test_arm_question_block_present(self):
        # the arm question must match the machine-question exemptions
        # (vlož + /goal) so it neither pings Discord nor trips the gate
        t = read(SKILL)
        self.assertIn("**Otázka — projekt", t)
        self.assertIn("❓ NEEDS YOU: vlož /goal", t)


if __name__ == "__main__":
    main()
