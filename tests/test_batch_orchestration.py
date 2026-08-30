"""Locks the #723 BATCH-mode autopilot orchestration doctrine.

Owner directive (2026-08-26, verbatim): "rozbehne sa 5 subagentov a nechaju sa
vsetky dokoncit a tak sa spravi compact a znova dalsich 5." Root cause it fixes:
the continuous-saturation doctrine (#317/#456) kept a worker lane ALWAYS live,
so `compact-request --self`'s live-tasks veto (watchdog/compact.py condition (b))
always skipped and the boundary compact NEVER fired -> the Fable main context
grew unbounded; and a compact forced mid-fleet breaks task handles / the armed
goal (CC #29193, unfixed as of CC 2.1.246).

The fix (BATCH mode, reversing #456 FOR autopilot): dispatch a batch of up to 5
parallel worktree lanes, NO refill while a batch is open, integrate each returned
branch serially under the mutex, and compact ONLY at the DRAINED batch boundary
(whole batch returned + integrated = zero live tasks) before the next batch.

These are content-locks (the tests/test_model_tiering.py pattern) so a future
edit cannot silently revert the three load-bearing sentences -- the batch cap,
no-refill-while-open, and compact-at-drained-boundary -- back to continuous mode.
The registry char-budget / drift / clause-coverage locks live in
tests/test_goal_registry.py; the fleet/turn-boundary reconciliations in
tests/test_fleet_concurrency_doctrine.py + test_goal_turn_boundary.py +
test_goal_backlog_proof.py. This file is the single dedicated batch-doctrine lock.
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


class TestRegistryClausesAreBatch(TestCase):
    """The three edited /goal clauses carry the batch directive, not the
    superseded continuous / paces-one-per-turn framing."""

    def _clause(self, cid, profile="full"):
        return next(c for c in gr.CLAUSES if c.id == cid).text_for(profile)

    def test_saturation_core_is_batch_no_refill(self):
        core = self._clause("saturation-core")
        for tok in ("BATCH MODE", "up to 5", "PARALLEL", "isolation:worktree",
                    "autopilot-worker", "NO refill while a batch runs"):
            self.assertIn(tok, core)
        # the continuous directive it replaced must be gone
        self.assertNotIn("SATURATE", core)
        self.assertNotIn("refill PARALLEL", core)
        self.assertNotIn("to saturation", core)

    def test_compact_boundary_fires_at_drained_boundary_every_profile(self):
        for p in gr.PROFILES:
            cb = self._clause("compact-boundary", p)
            self.assertIn("WHOLE batch has returned", cb)
            self.assertIn("ZERO live tasks", cb)
            self.assertIn("next batch", cb)
            self.assertIn("NEVER compact while lanes live", cb)
            self.assertIn("#29193", cb)
            # the superseded continuous tails
            self.assertNotIn("paces ONE", cb)
            self.assertNotIn("keep building", cb)
            self.assertNotIn("do NOT dispatch the next", cb)

    def test_every_rendered_goal_line_carries_batch_mode(self):
        for p in gr.PROFILES:
            line = gr.render(p)
            self.assertIn("BATCH MODE", line)
            self.assertIn("NO refill while a batch runs", line)
            self.assertIn("ZERO live tasks", line)


class TestSkillBatchDoctrine(TestCase):
    """The autopilot SKILL body states the three load-bearing batch sentences."""

    def test_the_batch_dispatch_section_exists(self):
        body = read(SKILL)
        self.assertIn("**Batch dispatch — up to 5 lanes, NO refill while a batch is open", body)

    def test_no_refill_while_a_batch_is_open_is_stated(self):
        body = read(SKILL).lower()
        self.assertIn("no refill while a batch", body)
        self.assertIn("no new lane is dispatched while the batch is open", body)

    def test_compact_only_at_the_drained_batch_boundary(self):
        body = read(SKILL)
        self.assertIn("DRAINED BATCH BOUNDARY", body)
        # the compact is gated on zero live tasks, never per integration cycle
        self.assertIn("zero live tasks", body.lower())
        self.assertNotIn("fires PER INTEGRATION CYCLE", body)

    def test_the_batch_cap_is_five(self):
        body = read(SKILL).lower()
        self.assertIn("batch cap", body)
        self.assertIn("up to 5", body)

    def test_the_tail_lane_tradeoff_is_named_honestly(self):
        body = read(SKILL).lower()
        self.assertIn("tail-lane", body)
        # the #456 reversal is acknowledged, not hidden ("deliberately reverses
        # #456's continuous refill" contains this substring)
        self.assertIn("reverses #456's continuous refill", body)


class TestSkillBakesInTheResearchFacts(TestCase):
    """Both research facts from the #723 comment must be in the doctrine so a
    future editor cannot re-introduce a mid-fleet compact."""

    def test_never_compact_with_live_background_tasks(self):
        body = read(SKILL)
        self.assertIn("#29193", body)
        self.assertIn("NEVER break task handles", body)

    def test_goal_survives_a_normal_compaction_is_documented(self):
        body = read(SKILL)
        self.assertIn("PRESERVES the armed `/goal`", body)
        self.assertIn("goal.md", body)

    def test_cc_version_of_the_unfixed_upstream_is_cited(self):
        # the research was run on the latest CC; the doctrine cites it so a
        # future re-check knows the baseline the "unfixed" claim was true at.
        self.assertIn("CC 2.1.246", read(SKILL))


class TestNoContinuousReversion(TestCase):
    """Negative lock (review finding, #723; scope extended to master by #724):
    an ADDITIVE re-introduction of the pre-#723 continuous-refill phrasing
    ALONGSIDE the batch text passes every positive-presence lock above, so guard
    the exact affirmative phrases too. #724 migrated autopilot-MASTER to batch
    mode as well (LANE 3's drained batch = the compact boundary), so this lock
    now covers BOTH skills — master no longer legitimately keeps continuous
    refill phrasing. Both bodies use 'continuous refill' solely inside the
    reverses/superseding-#456's-continuous-refill negations, which none of these
    affirmative phrases hit. The membership check is case-insensitive so a
    lower-case re-add ('dispatch is continuous') cannot slip the lock."""

    BANNED_AFFIRMATIVE = (
        "refilling to saturation",
        "refill to saturation",
        "refills the next lanes",
        "it refills the next lanes",
        "keep every lane full",
        "saturating lanes",
        "DISPATCH is CONTINUOUS",
        "CONTINUOUSLY refill",
        "refills continuously",
    )

    def test_neither_skill_re_adds_the_old_continuous_directive(self):
        for rel in (SKILL, SKILL_MASTER):
            body = read(rel).lower()
            present = [p for p in self.BANNED_AFFIRMATIVE if p.lower() in body]
            self.assertEqual(present, [],
                             "%s re-introduced pre-#723 continuous phrasing: %r"
                             % (rel, present))


class TestToolingModuleReconciled(TestCase):
    """The always-on max-acceleration module points at the batch boundary
    without re-deriving the doctrine (pointer-class, #701)."""

    def test_the_pointer_names_bounded_batches(self):
        body = read(TOOLING)
        self.assertIn("BOUNDED BATCHES", body)
        self.assertIn("#723", body)
        # parallel lanes stay the default WITHIN a batch
        self.assertIn("Parallel lanes stay the default WITHIN a batch", body)


class TestWatchdogLaneNudgeIsBatch(TestCase):
    """#726 -- the job-20 lane-check nudge (`watchdog/goal.py`) carries the BATCH
    doctrine, not the superseded continuous saturation (#456). The validator
    found this the ONE batch surface with no coverage: the skill/registry locks
    above never touched the watchdog nudge, which kept firing "fill lanes to 5"
    into a DRAINING batch (2 false nudges live, 2026-08-26). Two invariants:
    (1) the empty-lane nudge TEXT teaches "start a NEW batch", never "fill
    lanes"; (2) a RUNNING batch (live_workers>0 -- saturated OR draining) is
    NEVER nudged to refill: it logs skip:batch-running and delivers nothing. The
    under-saturated fill TEXT + surplus constant are RETIRED."""

    CWD = "/home/newlevel/devel/lanenudge726"
    SID = "sess-lane-726"

    # ---- content locks (stable, no driving) ----

    def test_empty_lane_text_teaches_start_a_new_batch(self):
        rendered = goal.GOAL_LANE_NUDGE_TEXT % (37, 2)
        low = rendered.lower()
        # batch doctrine language (goal_registry.py::saturation-core / master LANE 3)
        self.assertIn("várk", low)          # "začni NOVÚ várku"
        self.assertIn("refill", low)        # "ŽIADNY refill kým várka beží"
        self.assertIn("worktree", low)
        self.assertIn("paraleln", low)
        self.assertIn("sériovo", low)       # serial integration under the mutex
        self.assertIn("5", rendered)        # a BATCH of up to 5
        # the within-batch bound is the canonical post-#723 resource-signal
        # backoff, NOT the retired #442 fixed "cap 8" (review finding 2)
        self.assertIn("rate-limit", low)
        self.assertNotIn("8", rendered)

    def test_empty_lane_text_dropped_the_continuous_refill_phrasing(self):
        low = goal.GOAL_LANE_NUDGE_TEXT.lower()
        # the retired #456 "dispatch MORE lanes NOW" refill wording is gone
        self.assertNotIn("dispatchni teraz ďalšie", low)

    def test_under_saturated_fill_text_and_surplus_constant_are_retired(self):
        self.assertFalse(hasattr(goal, "GOAL_LANE_UNDERSAT_NUDGE_TEXT"))
        self.assertFalse(hasattr(goal, "GOAL_LANE_UNDERSAT_SURPLUS"))

    # ---- behavioral lock: a running batch is never nudged to refill ----

    def _drive(self, workers, backlog, now=100000):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmtime = now - 100
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        # #726/#729: the nudge consults no memory gate (the memory OOM subsystem
        # was under-saturated-only and is now deleted), so no mem patch is needed.
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "count_live_workers",
                            return_value=(workers, [])):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, {}, self.SID, self.CWD, "111", GOAL_ARMED_CAP,
                tpath, tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: backlog, state={},
                sleep_fn=lambda s: None)
        return logs, tmux

    def test_running_batch_is_skipped_never_refilled(self):
        # a DRAINING batch: 2 live lanes < 5, large backlog (surplus 35). Under
        # the retired #456 doctrine this fired the "fill to 5" nudge; under batch
        # mode (#723/#724) it must SKIP -- NO refill while a batch is open.
        logs, tmux = self._drive(workers=2, backlog=37)
        self.assertTrue(any("skip:batch-running" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        # the retired under-saturated decision lines are gone
        self.assertFalse(any("surplus-floor" in ln for ln in logs), logs)
        self.assertFalse(any("(fill)" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], tmux.sent)

    def test_one_worker_draining_batch_is_skipped_at_the_boundary(self):
        # the exact live shape (#726 incident): 1 lane still draining, backlog
        # waiting. The `live_workers > 0` boundary must SKIP, never nudge -- a
        # `> 1` off-by-one would refill a single-lane draining batch.
        logs, tmux = self._drive(workers=1, backlog=37)
        self.assertTrue(any("skip:batch-running" in ln for ln in logs), logs)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], tmux.sent)

    def test_closed_batch_with_backlog_still_fires_the_start_a_batch_nudge(self):
        # live_workers==0 (batch CLOSED) + workable backlog -> the empty-lane
        # nudge DOES fire: "you should have started a NEW batch and did not".
        logs, tmux = self._drive(workers=0, backlog=37)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs), logs)
        self.assertFalse(any("skip:batch-running" in ln for ln in logs), logs)


if __name__ == "__main__":
    main()
