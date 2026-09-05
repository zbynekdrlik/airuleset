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
        # Deep content moved to companion (#859 batch 4c)
        t = read("skills/ci-monitoring-deep/DEEP.md")
        self.assertIn("Foreground bounded poll loop", t)
        self.assertIn("DEADLINE=", t)

    def test_long_wait_recommends_one_background_waiter(self):
        t = read("skills/ci-monitoring-deep/DEEP.md")
        self.assertIn("background waiter", t)
        self.assertIn("run_in_background: true", t)
        self.assertIn("ONE task-notification", t)

    def test_background_waiter_has_a_death_or_timeout_branch(self):
        t = read("skills/ci-monitoring-deep/DEEP.md")
        self.assertTrue(
            "death" in t.lower() or "budget" in t.lower(),
            "the long-wait example must self-bound on failure/timeout too")

    def test_recovery_step_stated_on_next_turn(self):
        t = read("skills/ci-monitoring-deep/DEEP.md")
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
        # orphaned/leaking processes, not killed ones) -- it may still be
        # MENTIONED for transparency (why it was dropped), but must no
        # longer be presented as CORROBORATING evidence. #29193 is the
        # actually-relevant, reproduced mechanism, and must be present.
        t = read("modules/core/ci-monitoring.md")
        self.assertNotIn("corroborated by", t)
        self.assertIn("#29193", t)
        # "OPPOSITE failure mode" archaeology moved to history (#859 batch 2)
        hist = read(".claude/rules-reference/ci-monitoring-history.md")
        self.assertIn("OPPOSITE failure mode", hist)

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


class TestMemoryPressureReapDeathModeNamed(TestCase):
    """#448: the long-wait background-waiter guidance named ONLY the compaction
    death mode (the OS process usually SURVIVES; only the notification handle is
    orphaned). Claude Code ALSO proactively SIGTERM->SIGKILLs a MAIN-session
    background shell on a Node `memoryPressure` event (telemetry
    `task_local_shell_pressure_reap`; fires only when agentId===undefined, i.e.
    main-session launches; a subagent-launched bg shell is EXEMPT) -- confirmed
    live in the installed 2.1.232 binary. That mode is materially different: it
    kills the OS PROCESS outright, and it hits in MINUTES on a memory-tight box,
    not just at a compaction boundary -- so a reader who internalised "the
    process usually survives, only re-link the handle" will under-react to a
    genuinely dead waiter. These asserts lock the failure mode + its recovery
    onto every surface a session OR a DISPATCHED WORKER reads: the always-on
    module stub is the one that reaches a worker (#104/#105 -- a skill body does
    not)."""

    SURFACES = (
        "modules/core/ci-monitoring.md",
        "skills/verify-launched-work-liveness/SKILL.md",
        "modules/quality/verify-launched-work-liveness.md",
    )
    # The two FULLER surfaces carry the mitigation detail; the always-on stub
    # stays terse (reap named + recovery), so the env kill-switch / minutes /
    # subagent-exempt asserts below apply only to these two.
    FULL = (
        "modules/core/ci-monitoring.md",
        "skills/verify-launched-work-liveness/SKILL.md",
    )

    @staticmethod
    def _window(text, needle, radius=1000):
        i = text.find(needle)
        if i == -1:
            return ""
        return " ".join(text[max(0, i - radius): i + radius].split())

    def test_reap_trigger_named_on_every_surface(self):
        # `memoryPressure` is a single unbreakable token -- it cannot appear by
        # a wrap-accident, so its presence proves the reap failure mode is named.
        for rel in self.SURFACES:
            self.assertIn(
                "memoryPressure", read(rel),
                "%s must name the memoryPressure reap trigger" % rel)

    def test_reap_says_the_process_is_killed_and_ties_to_recovery(self):
        # Not merely the token -- the failure-mode text must say the OS process
        # is KILLED (unlike compaction's orphan-only mode) AND tie to the SAME
        # recovery the rule already prescribes (relaunch / re-derive / durable).
        for rel in self.SURFACES:
            w = self._window(read(rel), "memoryPressure").lower()
            self.assertTrue(
                any(k in w for k in ("sigterm", "sigkill", "kill", "reap")),
                "%s: the reap window must say the process is killed" % rel)
            self.assertTrue(
                any(k in w for k in
                    ("relaunch", "re-derive", "durable", "fresh waiter")),
                "%s: the reap window must tie to the relaunch/re-derive "
                "recovery" % rel)

    def test_reap_distinguished_from_compaction_by_speed(self):
        # The whole point: a DIFFERENT death mode from compaction -- it kills
        # the process and hits in MINUTES. The fuller surfaces must say so.
        for rel in self.FULL:
            w = self._window(read(rel), "memoryPressure").lower()
            self.assertIn(
                "minute", w,
                "%s: name that the reap can kill in MINUTES" % rel)

    def test_reap_notes_main_session_only_subagent_exempt(self):
        # The guard's key property -- a dispatched subagent's own bg shell is
        # EXEMPT -- is what makes the subagent-foreground mitigation valid.
        for rel in self.FULL:
            w = self._window(read(rel), "memoryPressure").lower()
            self.assertIn("subagent", w)
            self.assertIn("exempt", w)

    def test_env_kill_switch_mitigation_named_on_the_fuller_surfaces(self):
        # A cheap, zero-machinery mitigation documented as guidance (not a new
        # hook -- FREEZE): the CLI-env kill-switch restores the documented
        # background-waiter semantics.
        for rel in self.FULL:
            self.assertIn(
                "CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP", read(rel),
                "%s: name the documented env kill-switch mitigation" % rel)

    def test_reap_incident_archaeology_lives_in_the_playbook(self):
        # Mirrors this module's own TestIncidentNarrativeLivesInThePlaybook:
        # the always-on ci-monitoring.md keeps the tight pointer (it is at its
        # word cap); the measurement archaeology lives in the paths:-scoped
        # playbook, which is exactly where ci-monitoring.md's "in the playbook"
        # pointers resolve.
        pb = "\n".join(p.read_text(encoding="utf-8") for p in ([ROOT / ".claude" / "rules" / "airuleset-internals.md"] + sorted((ROOT / ".claude" / "rules").glob("internals-*.md")) + [ROOT / ".claude" / "rules-reference" / "internals-archive.md"]) if p.exists())  # #482: reap narrative in the on-demand archive
        self.assertIn("memoryPressure", pb,
                      "playbook must carry the reap incident narrative")
        self.assertIn("287 MB", pb,
                      "playbook must carry the reap incident measurement")


if __name__ == "__main__":
    main()
