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
CROSS_STREAM = (ROOT / "skills" / "autopilot" / "references" /
                "cross-stream-protocol.md")


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
        # Anchored on the FULL bullet-opening phrase, not a bare "FINDINGS"
        # substring -- the file legitimately mentions "FINDINGS" elsewhere
        # too (the frontmatter description, and #278's own step 2b FAIL
        # cross-reference), and a bare-substring anchor would resolve to
        # whichever of those sits FIRST in the file (#278 review, MINOR-4).
        t = read(SKILL)
        i = t.index("**FINDINGS → the bounce lane**")
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
        # #278 review MINOR-1/-2: assert the EXACT bullet openers and the
        # FAIL cross-reference target, not bare substrings a rationale
        # paragraph could also satisfy (e.g. "PASS" appears in prose too).
        t = read(SKILL)
        i2b = t.index("Mechanical pre-review gate re-check")
        i3 = t.index("### 3. INDEPENDENT REVIEW")
        window = t[i2b:i3]
        self.assertIn("**FAIL**", window)
        self.assertIn("bounce", window.lower())
        self.assertIn("step 5", window.lower())
        self.assertIn("**PASS**", window)
        self.assertIn("step 3", window.lower())
        # normalize before this one check: the real prose wraps "never a"
        # onto its own line in the markdown source, which a raw substring
        # match against un-normalized text would miss (playbook: markdown
        # line-wrap breaks a literal multi-word assertIn).
        flat = " ".join(window.lower().split())
        self.assertIn("never a substitute for the cold read", flat)

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


class TestQueueUnionsBothHandoffLabels(TestCase):
    """#498 -- the gatekeeper review queue MUST be
    `ready-for-review UNION needs-gatekeeper`, never `ready-for-review` alone.

    Root cause: a carve-out stream (no erp-test shadow box, phase 1) fails the
    validation hand-off gate STRUCTURALLY, so its `ready-for-review` label is
    stripped at EVERY hand-off; the repo-side gate (odoo-erp #4139) applies
    `needs-gatekeeper` INSTEAD of silently stripping, so that stream's hand-offs
    exist ONLY under `needs-gatekeeper` + `stream:<user>`. An rfr-only queue
    never surfaces them -> both sides claim done, the ticket rots for hours
    (live incident odoo-erp #3244, miva). The union is safe because the queue is
    already `stream:<stream>`-scoped and a bare `needs-gatekeeper`
    stream->supervisor ACTION request never carries `stream:<user>`
    (`cmd_gk_request` uses `handed-by:<user>`, never `stream:<user>`)."""

    def _step1_window(self):
        t = read(SKILL)
        i = t.index("### 1. Pick up the queue")
        j = t.index("### 2. Get the work in front of you")
        return t[i:j]

    def test_step1_query_unions_both_handoff_labels_stream_scoped(self):
        w = self._step1_window()
        self.assertIn("ready-for-review", w)
        self.assertIn("needs-gatekeeper", w)
        self.assertIn("stream:", w)

    def test_step1_never_lists_ready_for_review_as_the_lone_queue_label(self):
        # the exact rfr-only shape the bug was: `--label ready-for-review`
        # as the whole queue signal, `needs-gatekeeper` nowhere in the query.
        w = self._step1_window()
        self.assertNotIn("--label ready-for-review --label stream", w)

    def test_carve_out_stream_arrives_via_needs_gatekeeper(self):
        flat = " ".join(read(SKILL).split())
        self.assertIn("needs-gatekeeper", flat)
        self.assertTrue(
            "carve-out" in flat.lower(),
            "process-subdev must name the carve-out stream that arrives via "
            "needs-gatekeeper")

    def test_comments_are_never_a_queue_signal_labels_carry_queue_state(self):
        flat = " ".join(read(SKILL).split()).lower()
        self.assertIn("never a queue signal", flat)
        self.assertIn("labels carry queue state", flat)

    def test_goal_continuation_stop_proof_counts_needs_gatekeeper(self):
        # step 7's continuation /goal must not declare the queue done while a
        # needs-gatekeeper hand-off of this stream is still open.
        for line in read(SKILL).splitlines():
            if line.startswith("/goal The"):
                self.assertIn("needs-gatekeeper", line)
                return
        self.fail("no /goal continuation line found in process-subdev SKILL.md")

    def test_close_removes_whichever_handoff_label_was_applied(self):
        # a carve-out hand-off closes under needs-gatekeeper, not
        # ready-for-review -- the close step must clear the hand-off label
        # that was actually applied, not only ready-for-review.
        flat = " ".join(read(SKILL).split())
        i = flat.index("close the stream's tickets with merge")
        window = flat[max(0, i - 200):i + 40]
        self.assertIn("needs-gatekeeper", window)


class TestCrossStreamProtocolRecordsCarveOutHandoff(TestCase):
    """#498 -- the canonical cross-stream protocol (rule 7's home) must record
    that a carve-out stream's HAND-OFF arrives via `needs-gatekeeper`, DISTINCT
    from a bare `needs-gatekeeper` stream->supervisor ACTION request (which
    never carries `stream:<user>`). That distinction is what makes the review
    queue's `(ready-for-review UNION needs-gatekeeper) AND stream:<stream>`
    union safe."""

    def test_carve_out_handoff_documented(self):
        flat = " ".join(read(CROSS_STREAM).split())
        self.assertIn("needs-gatekeeper", flat)
        self.assertTrue(
            "carve-out" in flat.lower(),
            "cross-stream protocol must record the carve-out hand-off path")

    def test_action_request_distinguished_by_stream_label(self):
        flat = " ".join(read(CROSS_STREAM).split())
        i = flat.lower().index("carve-out")
        window = flat[i:i + 700]
        self.assertIn("stream:", window)


if __name__ == "__main__":
    main()
