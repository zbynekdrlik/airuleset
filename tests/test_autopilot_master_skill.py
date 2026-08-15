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
        # #325: LANE 3 mirrors #317's parallel isolation:worktree fleet-dispatch
        # DEFAULT (several autopilot-workers per ROUND, integrated SERIALLY by the
        # supervisor) instead of the pre-#317 "serial per repo, never a second
        # worker" shape — the stale phrase this test used to pin is retired.
        self.assertIn("worktree", t.lower())
        self.assertIn("serial single-worker shape", t.lower())
        self.assertNotIn("serial per repo", t.lower())

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

    def test_done_means_everything_shipped_also_excludes_ops_channel(self):
        # #364 (#362 follow-up): this loop's own stop-proof hand-rolls its
        # own `-label:autopilot-skip` search string -- separate from
        # airuleset.py's `AUTOPILOT_SKIP_EXCL` #362 fixed -- and must
        # exclude a PERMANENT ops-channel ticket the same way, or the
        # master loop's own `--count 0`-equivalent condition can never be
        # honestly satisfied while one sits open.
        self.assertIn("-label:ops-channel", self._goal_line())

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


class TestRunCardFiredInEveryLane(TestCase):
    """#47: on the gatekeeper (24/7, running autopilot-master), per-ticket
    Discord cards stopped arriving entirely — neither autopilot-master nor
    process-subdev ever mentioned `run-card` in their own bodies (0 hits vs 4
    in skills/autopilot/SKILL.md), so a lane that never dispatches an
    autopilot-worker subagent (LANE 1 REVIEW runs its release inline) had no
    code path that could ever fire one."""

    def test_run_card_mentioned_in_this_skill(self):
        self.assertGreater(read(SKILL).count("run-card"), 0)

    def test_lane1_review_fires_run_card_on_clean_verdict(self):
        # Step 3's lane BULLET list (not the /goal template string in Step 2,
        # which repeats the lane names too) — anchor from "## Step 3" onward.
        t = read(SKILL)
        i_step3 = t.index("## Step 3")
        i_lane1 = t.index("LANE 1 REVIEW", i_step3)
        i_lane2 = t.index("LANE 2 RELEASE", i_step3)
        lane1_block = t[i_lane1:i_lane2]
        self.assertIn("run-card", lane1_block)
        self.assertIn("EVERY ticket in the slice", lane1_block)

    def test_lane3_core_fires_run_card_per_merged_ticket(self):
        t = read(SKILL)
        i_step3 = t.index("## Step 3")
        i_lane3 = t.index("LANE 3 CORE", i_step3)
        i_lane4 = t.index("LANE 4 QUESTIONS", i_step3)
        lane3_block = t[i_lane3:i_lane4]
        self.assertIn("run-card", lane3_block)
        self.assertIn("Each merged", lane3_block)


class TestMissedWindowNeverSilent(TestCase):
    def test_blocked_window_raises_notice(self):
        # 2026-07-21 morning: the 22:00-06:00 window passed with the release
        # blocked by two bounced regressions — CORRECT hold, but the user got
        # NO message all night and woke up expecting a deploy
        t = read(SKILL)
        self.assertIn("ONE deduped notice per window", t)
        g = None
        for line in t.splitlines():
            if line.startswith("/goal "):
                g = line
        self.assertIn("never a silent missed window", g)


class TestPreflightBoardExcludesOpsChannel(TestCase):
    """#364 (#362 follow-up): Step 1's own preflight "core backlog" query
    hand-rolls a bare `-label:autopilot-skip` search string with no
    `ops-channel` awareness -- the same defect class as the `/goal MASTER
    LOOP` stop-proof line itself (covered separately in TestGoalTemplate).
    A permanent ops-channel ticket would still print in the LANE STATUS
    board's CORE count even though it is never actually workable backlog.
    """

    def test_step1_core_backlog_query_excludes_ops_channel(self):
        t = read(SKILL)
        i = t.index("Core backlog + questions")
        window = t[i:i + 300]
        self.assertIn("-label:ops-channel", window)


class TestReviewLaneUnionsBothHandoffLabels(TestCase):
    """#498 -- LANE 1 REVIEW (the master loop's actual gk execution path) must
    key on `ready-for-review UNION needs-gatekeeper`, never `ready-for-review`
    alone. A carve-out stream's hand-off (odoo-erp #4139) exists ONLY under
    `needs-gatekeeper` + `stream:<user>`, so an rfr-only LANE 1 gate would never
    trigger a /process-subdev run for it -> miva rots (live incident #3244).
    A bare `needs-gatekeeper` stream->supervisor ACTION request (no
    `stream:<user>`) is NOT a review hand-off and must stay out of LANE 1."""

    def _lane1_window(self):
        t = read(SKILL)
        i = t.index("- **LANE 1 REVIEW**")
        j = t.index("- **LANE 2 RELEASE**", i)
        return t[i:j]

    def test_lane1_review_bullet_unions_both_handoff_labels(self):
        w = self._lane1_window()
        self.assertIn("ready-for-review", w)
        self.assertIn("needs-gatekeeper", w)

    def test_lane1_distinguishes_carve_out_handoff_from_action_request(self):
        # the needs-gatekeeper arm of LANE 1 is scoped to a real stream
        # hand-off (stream:<user>) -- a bare action-request stays out.
        flat = " ".join(self._lane1_window().split())
        self.assertIn("stream:", flat)
        self.assertIn("action-request", flat.lower().replace(" ", "-"))

    def test_board_handoff_query_includes_needs_gatekeeper(self):
        t = read(SKILL)
        i = t.index("# Per stream: hand-offs waiting")
        j = t.index("# Release debt", i)
        window = t[i:j]
        self.assertIn("needs-gatekeeper", window)

    def test_goal_lane1_summary_mentions_needs_gatekeeper(self):
        for line in read(SKILL).splitlines():
            if line.startswith("/goal MASTER LOOP"):
                i = line.index("LANE 1 REVIEW")
                j = line.index("LANE 2 RELEASE", i)
                self.assertIn("needs-gatekeeper", line[i:j])
                return
        self.fail("no /goal MASTER LOOP line found in autopilot-master SKILL.md")


if __name__ == "__main__":
    main()
