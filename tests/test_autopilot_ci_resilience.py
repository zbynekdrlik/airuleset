"""Locks the autopilot CI-wait resilience fix (2026-06-29).

Forensics on a live odoo (3-branch) /autopilot run: ~40% of autopilot-worker
subagents "died" by launching a `Bash(run_in_background=True)` CI poll and then
ending their turn — a subagent with no pending foreground tool call RETURNS, and
the detached background task re-invokes the SUPERVISOR, not the gone worker. Fix:
the worker never background-waits (it terminates the subagent); it waits FOREGROUND
for a short CI, or hands the run-id back and the SUPERVISOR owns the wait for a
long / multi-stage pipeline.

These asserts guard against a regression that reinstates the subagent-killing
background-wait or drops the supervisor-owns-long-waits path.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestAutopilotCiResilience(TestCase):
    def test_ci_monitoring_has_subagent_caveat(self):
        t = read("modules/core/ci-monitoring.md")
        # run_in_background is flagged as a MAIN-SESSION-only pattern that kills a subagent.
        self.assertIn("MAIN-SESSION pattern", t)
        self.assertIn("TERMINATES", t)

    def test_worker_bans_background_ci_wait(self):
        t = read("agents/autopilot-worker.md")
        self.assertIn("Bash(run_in_background=True)", t)
        self.assertIn("TERMINATES", t)
        self.assertIn("FOREGROUND", t)
        # The long/multi-stage hand-back path must be present.
        self.assertIn("report the CI run-id", t)
        # The killer instructions that caused the deaths must be GONE — guard against
        # a regression that re-adds them while keeping the new caveat.
        self.assertNotIn("The supervisor does NOT watch your CI", t)
        self.assertNotIn("is a fine default", t)

    def test_supervisor_owns_long_ci_waits(self):
        t = read("skills/autopilot/SKILL.md")
        self.assertIn("YOU own the CI", t)
        self.assertIn("BOUNDED PER STAGE", t)
        # The context-gate line must no longer state UNCONDITIONALLY that the worker owns CI
        # (that contradicted the multi-stage rule); it must now carry the supervisor path.
        self.assertNotIn(
            "the worker monitors its OWN CI to terminal; the main loop just verifies the result", t
        )
        self.assertIn("SUPERVISOR owns the CI waits", t)


if __name__ == "__main__":
    main()
