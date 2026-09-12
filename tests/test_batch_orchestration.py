"""Locks the #848 CONTINUOUS-REFILL autopilot orchestration doctrine.

Owner decision (2026-09-02, verbatim): "no ale na co sme potom robili pracu po
varkach ked ju ignorujes?! v takom pripade sa mozme vratit k compact za kazdym
work complete!". #848 RETIRES the #723/#724 BATCH doctrine after the STEP-0 live
experiment (CC 2.1.258) proved a `/compact` over live worktree lanes + a bg-bash
waiter + an armed `/goal` does NOT break the task registry (lanes commit,
notifications survive, task IDs resolve, the goal survives). The batch model's
premise (a compact mid-fleet breaks task handles, CC issue 29193) is gone.

The doctrine (CONTINUOUS REFILL, restoring the #456 shape FOR autopilot): keep up
to 5 parallel worktree lanes live, refill a returned lane's slot immediately,
integrate each returned branch serially under the mutex, and compact at EVERY
integration cycle's `## ✅ Work Complete` — live lanes or not.

These are content-locks (the tests/test_model_tiering.py pattern) so a future
edit cannot silently revert the load-bearing sentences -- the lane cap, the
immediate refill, and the compact-at-every-cycle -- back to batch mode. Flipped
from the #723 batch locks (flip-never-delete, #723 lesson). The registry
char-budget / drift / clause-coverage locks live in tests/test_goal_registry.py;
the fleet/turn-boundary reconciliations in tests/test_fleet_concurrency_doctrine.py
+ test_goal_turn_boundary.py + test_goal_backlog_proof.py. This file is the single
dedicated continuous-refill lock.
"""

import sys
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import goal_registry as gr  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _encode,
    _write_marker_transcript,
)

SKILL = "skills/autopilot/SKILL.md"
SKILL_MASTER = "skills/autopilot-master/SKILL.md"
TOOLING = "modules/core/claude-code-tooling.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestRegistryClausesAreContinuous(TestCase):
    """The three edited /goal clauses carry the continuous-refill directive, not
    the retired batch / drained-boundary framing."""

    def _clause(self, cid, profile="full"):
        return next(c for c in gr.CLAUSES if c.id == cid).text_for(profile)

    def test_saturation_core_is_continuous_refill(self):
        core = self._clause("saturation-core")
        # #993 r2b: refill is DEPENDENCY aware (dispatchable-only, deps closed);
        # the class-based infra-serial half was removed (routing replaces it), and
        # the blanket "IMMEDIATELY while backlog remains" is gone.
        for tok in ("CONTINUOUS REFILL", "isolation:worktree",
                    "autopilot-worker", "DISPATCHABLE", "dependencies closed"):
            self.assertIn(tok, core)
        self.assertNotIn("infra units are SERIAL", core)
        # #991: the fixed lane cap wording is gone (count = box + backlog).
        self.assertNotIn("up to 5", core)
        # the batch directive it replaced must be gone
        self.assertNotIn("BATCH MODE", core)
        self.assertNotIn("NO refill while a batch runs", core)
        self.assertNotIn("IMMEDIATELY while backlog remains", core)

    def test_compact_boundary_is_disabled_911(self):
        # #911: callback compact DISABLED by owner flag.
        for p in gr.PROFILES:
            cb = self._clause("compact-boundary", p)
            self.assertIn("DISABLED", cb)
            self.assertIn("#911", cb)
            # the retired batch/mid-fleet framing must stay gone
            self.assertNotIn("WHOLE batch has returned", cb)
            self.assertNotIn("ZERO live tasks", cb)
            self.assertNotIn("next batch", cb)
            self.assertNotIn("NEVER compact while lanes live", cb)
            self.assertNotIn("#29193", cb)
            # the pre-#723 one-at-a-time tails stay banned
            self.assertNotIn("paces ONE", cb)
            self.assertNotIn("keep building", cb)
            self.assertNotIn("do NOT dispatch the next", cb)

    def test_every_rendered_goal_line_carries_continuous_refill(self):
        for p in gr.PROFILES:
            line = gr.render(p)
            self.assertIn("CONTINUOUS REFILL", line)
            self.assertIn("DISABLED", line)
            self.assertNotIn("BATCH MODE", line)
            self.assertNotIn("ZERO live tasks", line)


class TestSkillContinuousDoctrine(TestCase):
    """The autopilot SKILL body states the continuous-refill sentences."""

    def test_the_continuous_refill_section_exists(self):
        body = read(SKILL)
        self.assertIn("**Continuous refill — sized to box + backlog", body)

    def test_refill_a_returned_slot_immediately_is_stated(self):
        body = read(SKILL).lower()
        self.assertIn("refill a returned lane's slot", body)
        self.assertNotIn("no new lane is dispatched while the batch is open", body)

    def test_compact_at_every_integration_cycle(self):
        body = read(SKILL)
        self.assertIn("compact at EVERY integration cycle", body)
        self.assertIn("live lanes or not", body)
        self.assertNotIn("DRAINED BATCH BOUNDARY", body)

    def test_the_lane_count_is_sized_to_box_and_backlog(self):
        # #991: no fixed lane cap — the live lane count is sized to what the
        # box and backlog bear (declared resource caps + rate-limit signal).
        body = read(SKILL).lower()
        self.assertIn("lane count", body)
        self.assertIn("box and backlog", body)
        self.assertNotIn("up to 5 live lanes", body)

    def test_the_doctrine_reversal_is_named_honestly(self):
        body = read(SKILL)
        # #848 restores #456's continuous refill, retiring #723's batch mode —
        # acknowledged, not hidden.
        self.assertIn("#848", body)
        self.assertIn("restores #456's continuous refill", body)


class TestSkillBakesInTheResearchFacts(TestCase):
    """The STEP-0 experiment fact must be in the doctrine so a future editor
    cannot re-introduce the batch veto premise."""

    def test_compact_over_live_lanes_is_safe(self):
        body = read(SKILL)
        self.assertIn("STEP-0", body)
        self.assertIn("task registry", body)
        # the old affirmative "NEVER break task handles" veto claim is gone
        self.assertNotIn("NEVER break task handles", body)

    def test_goal_survives_a_normal_compaction_is_documented(self):
        body = read(SKILL)
        self.assertIn("PRESERVES the armed `/goal`", body)
        self.assertIn("goal.md", body)

    def test_cc_version_of_the_experiment_is_cited(self):
        # the STEP-0 experiment ran on this CC build; the doctrine cites it so a
        # future re-check knows the baseline the "safe" claim was proven at.
        self.assertIn("CC 2.1.258", read(SKILL))


class TestNoBatchReversion(TestCase):
    """Negative lock (flip of #723's TestNoContinuousReversion): an ADDITIVE
    re-introduction of the retired batch phrasing ALONGSIDE the continuous text
    passes every positive-presence lock above, so guard the exact batch phrases
    too, on BOTH skills. The membership check is case-insensitive so a lower-case
    re-add cannot slip the lock. (Both bodies may still mention 'batch' inside a
    retiring/reversal negation — the banned phrases below are the affirmative
    directives only.)"""

    BANNED_AFFIRMATIVE = (
        "no refill while a batch",
        "drained batch boundary",
        "whole batch has returned",
        "whole batch has drained",
        "zero live tasks",
        "no new lane is dispatched while the batch is open",
        "no new lane is dispatched while this batch",
        "batch-open state",
        "never compact while lanes live",
        "batch cap",
    )

    def test_neither_skill_re_adds_the_batch_directive(self):
        for rel in (SKILL, SKILL_MASTER):
            body = read(rel).lower()
            present = [p for p in self.BANNED_AFFIRMATIVE if p.lower() in body]
            self.assertEqual(present, [],
                             "%s re-introduced the retired batch phrasing: %r"
                             % (rel, present))


class TestToolingModuleReconciled(TestCase):
    """The always-on module states parallelism is the working model's decision,
    sized to box + backlog, without re-deriving the doctrine (pointer-class,
    #701/#991)."""

    def test_the_pointer_names_parallelism_decision(self):
        body = read(TOOLING)
        self.assertIn("Parallelism is the working model's decision", body)
        self.assertIn("box and backlog", body)
        self.assertIn("cli_resource_guards", body)
        self.assertNotIn("BOUNDED BATCHES", body)


class TestWatchdogLaneNudgeIsContinuous(TestCase):
    """#848 -- the job-20 lane-check nudge (`watchdog/goal.py`) carries the
    CONTINUOUS REFILL doctrine, not the retired #723/#726 batch mode. Two
    invariants: (1) the nudge TEXT teaches "refill a returned slot", never
    "start a NEW batch"; (2) a box with ROOM (live_workers < min(5, backlog),
    whether empty OR partially-full) IS nudged to refill; only a SATURATED box
    (>= 5 lanes) skips."""

    CWD = "/home/newlevel/devel/lanenudge848"
    SID = "sess-lane-848"

    # ---- content locks (stable, no driving) ----

    def test_nudge_text_teaches_refill(self):
        rendered = goal.GOAL_LANE_NUDGE_TEXT_FN(37, 2)
        low = rendered.lower()
        self.assertIn("refill", low)        # refill mechanism kept (#848)
        self.assertIn("doplň", low)         # "refill (doplň) vrátený slot"
        self.assertIn("worktree", low)
        self.assertIn("paraleln", low)
        self.assertIn("sériovo", low)       # serial integration under the mutex
        self.assertIn("rate-limit", low)
        self.assertNotIn("cap 8", low)      # not the retired #442 fixed "cap 8"
        # the retired batch noun must be GONE
        self.assertNotIn("várk", low)
        # #994: no lane-COUNT prescription (was `assertIn("5")` for "up to 5
        # lanes") and no priority override — defers to the #993 session priority.
        self.assertIn("#993", rendered)
        self.assertNotIn("drž až", low)
        self.assertNotIn("saturuj", low)

    def test_nudge_text_dropped_the_batch_phrasing(self):
        low = goal.GOAL_LANE_NUDGE_TEXT_FN(1, 0).lower()
        self.assertNotIn("začni novú várku", low)
        self.assertNotIn("žiadny refill kým", low)

    def test_under_saturated_fill_text_and_surplus_constant_are_retired(self):
        self.assertFalse(hasattr(goal, "GOAL_LANE_UNDERSAT_NUDGE_TEXT"))
        self.assertFalse(hasattr(goal, "GOAL_LANE_UNDERSAT_SURPLUS"))

    # ---- behavioral lock: a box with room IS nudged to refill ----

    def _drive(self, workers, backlog, now=100000, dispatchable=None):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmtime = now - 100
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        disp_fetch = None
        if dispatchable is not None:
            disp_fetch = lambda cwd: [dispatchable]  # noqa: E731
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "count_live_workers",
                            return_value=(workers, [])):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: backlog, state={},
                sleep_fn=lambda s: None, dispatchable_fetch=disp_fetch)
        return logs, tmux

    def test_dep_wait_only_free_slot_skips_no_keystroke(self):
        # #993 item 3/6a (r2b): a free slot but 0 dispatchable candidates (all
        # dep-wait) -> skip:dep-wait, NO keystroke.
        logs, tmux = self._drive(
            workers=1, backlog=37,
            dispatchable={"count": 0, "reason": "dep-wait"})
        self.assertTrue(any("skip:dep-wait" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], tmux.sent)

    def test_dispatchable_candidates_nudge_names_count(self):
        # #993 item 3/6b: with dispatchable candidates the nudge fires and the
        # text names ONLY the candidate count.
        logs, tmux = self._drive(
            workers=0, backlog=37, dispatchable={"count": 4, "reason": None})
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertIn("4 DISPATCHOVATEĽNÝCH", "".join(tmux.typed_texts()))

    def test_partially_full_box_is_nudged_to_refill(self):
        # #848 FLIP (was test_running_batch_is_skipped_never_refilled): 2 live
        # lanes < 5 + a large backlog means there are FREE slots — the refill
        # nudge FIRES now (under batch mode it was skip:batch-running).
        logs, tmux = self._drive(workers=2, backlog=37)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertFalse(any("skip:batch-running" in ln for ln in logs), logs)

    def test_single_lane_box_is_nudged_to_refill(self):
        # #848 FLIP (was test_one_worker_draining_batch_is_skipped...): 1 live
        # lane < 5 has room to refill up to 5 — the nudge fires.
        logs, tmux = self._drive(workers=1, backlog=37)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertFalse(any("skip:batch-running" in ln for ln in logs), logs)

    def test_empty_box_with_backlog_still_fires_the_refill_nudge(self):
        # live_workers==0 + workable backlog -> the refill nudge DOES fire.
        logs, tmux = self._drive(workers=0, backlog=37)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)

    def test_saturated_box_skips(self):
        # #848: a FULL box (>= 5 lanes) has no free slot — it skips, no refill.
        logs, tmux = self._drive(workers=5, backlog=37)
        self.assertTrue(any("saturated" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], tmux.sent)


if __name__ == "__main__":
    main()
