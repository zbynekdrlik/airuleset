"""#107 (2026-07-27, ft5000, live-reported): a session waiting on a ~2h test
run issued a fresh 9-minute FOREGROUND bounded loop over and over -- each one
a separate turn re-sending the whole conversation for one line of log
("preco monitoring nespravis tak aby si kazdych 9min nemusel spravit dalsi
plateny token tah!!"). `ci-monitoring.md` called the foreground loop "the
DEFAULT for CI" with no split by expected wait length, and its only
alternative (a background waiter) was blanket-banned by a claim -- "silently
KILLED (SIGTERM) on context compaction", cited to anthropics/claude-code
#25188 and #43944 -- that does not hold up on primary-source re-check: #25188
closed as a duplicate of a narrower terminal-close scenario, and #43944 is
the OPPOSITE failure mode (orphaned/leaking processes, not killed ones). The
best-documented real mechanism is #29193 (has-repro): across compaction the
OS process usually SURVIVES, but the session's in-process task-handle
registry is dropped, so the notification linkage is what's actually lost.

These asserts lock the fix: mechanism choice by expected wait length, a
background-waiter option for long waits with an explicit recovery step, and
the corrected citation -- without reintroducing ScheduleWakeup (#103) or a
new hook/job (discipline instruction on the ticket).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestCiMonitoringDurationSplit(TestCase):
    def test_mechanism_is_chosen_by_expected_wait_length(self):
        t = read("modules/core/ci-monitoring.md")
        low = t.lower()
        self.assertIn("expected wait", low)
        self.assertIn("short wait", low)
        self.assertIn("long wait", low)

    def test_foreground_loop_still_the_short_wait_default(self):
        # The existing shape/snippet must survive untouched -- this is an
        # ADDITIVE fix, not a replacement (#90's snippet-extraction test
        # depends on the DEADLINE snippet staying the first ```bash block).
        t = read("modules/core/ci-monitoring.md")
        self.assertIn("Foreground bounded poll loop", t)
        self.assertIn("DEADLINE=", t)

    def test_long_wait_recommends_one_background_waiter(self):
        t = read("modules/core/ci-monitoring.md")
        self.assertIn("background waiter", t)
        self.assertIn("run_in_background: true", t)
        # Must NOT be a repeated-print poll -- silent until terminal state.
        self.assertIn("ONE task-notification", t)

    def test_background_waiter_has_a_death_or_timeout_branch(self):
        # verify-launched-work-liveness.md: never a bare success-only wait.
        t = read("modules/core/ci-monitoring.md")
        self.assertTrue(
            "death" in t.lower() or "budget" in t.lower(),
            "the long-wait example must self-bound on failure/timeout too")

    def test_recovery_step_stated_on_next_turn(self):
        t = read("modules/core/ci-monitoring.md")
        low = t.lower()
        self.assertIn("relaunch", low)
        self.assertIn("durable", low)

    def test_never_reintroduces_schedule_wakeup(self):
        # #103: ScheduleWakeup is a /loop-only pacer, a silent no-op
        # outside an armed /loop -- must never come back as a general
        # long-wait mechanism in any of these three files.
        for rel in (
            "modules/core/ci-monitoring.md",
            "modules/quality/verify-launched-work-liveness.md",
            "skills/verify-launched-work-liveness/SKILL.md",
        ):
            self.assertNotIn("ScheduleWakeup", read(rel))

    def test_citation_corrected_away_from_the_two_that_do_not_hold_up(self):
        # #43944 was cited backwards (it's the OPPOSITE failure mode --
        # orphaned/leaking processes, not killed ones) -- drop it. #29193
        # is the actually-relevant, reproduced mechanism.
        t = read("modules/core/ci-monitoring.md")
        self.assertNotIn("#43944", t)
        self.assertIn("#29193", t)

    def test_does_not_overclaim_confirmed_when_not_live_reproduced(self):
        # The old text asserted "This is confirmed CC behavior" outright.
        # The re-check could not force a live compaction inside this
        # ticket's budget -- the rule must not claim direct reproduction it
        # doesn't have.
        t = read("modules/core/ci-monitoring.md")
        self.assertNotIn("This is confirmed CC behavior", t)

    def test_subagent_ban_paragraph_untouched(self):
        # The subagent-specific "wait FOREGROUND or hand back to
        # supervisor" rule (hook-enforced) must survive verbatim -- #107
        # is scoped to the general/main-session long-wait case, not the
        # subagent hand-back mechanism.
        t = read("modules/core/ci-monitoring.md")
        self.assertIn("BROKEN in a subagent", t)
        self.assertIn("TERMINATES", t)
        self.assertIn("subagent-stop-check-bg-work.sh", t)


class TestVerifyLaunchedWorkLivenessConsistency(TestCase):
    """The always-on module stub and the on-demand skill must not keep
    flatly banning a backgrounded long wait now that ci-monitoring.md
    sanctions one with a recovery step -- self-contradicting rules is its
    own bug. Module stub matters most: #104/#105 -- a dispatched subagent
    inherits MODULE bodies, never skill bodies or path-scoped rules."""

    def test_module_stub_no_longer_flatly_bans_background_for_long_waits(self):
        t = read("modules/quality/verify-launched-work-liveness.md")
        self.assertIn("foreground bounded loop", t)
        self.assertIn("compaction", t)
        # unconditional "use a foreground bounded loop instead" (no long-wait
        # carve-out) is the old, now-contradicted framing
        self.assertNotIn(
            "does NOT survive context **compaction** (SIGTERMed with no "
            "re-invoke) — use a foreground bounded loop instead.", t)

    def test_skill_anti_pattern_no_longer_flatly_bans_backgrounding_long_waits(self):
        t = read("skills/verify-launched-work-liveness/SKILL.md")
        self.assertIn("foreground bounded poll loop", t)
        # the old anti-pattern bullet banned ANY backgrounded long wait
        # outright; it must now distinguish "no recovery check" (still
        # wrong) from "with a recovery check" (now correct for long waits)
        self.assertNotIn(
            "Launching a detached `run_in_background` poll for a long "
            "(CI / rebuild) wait and assuming it will re-invoke you", t)


if __name__ == "__main__":
    main()
