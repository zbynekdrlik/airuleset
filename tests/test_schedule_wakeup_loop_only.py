"""Locks the ScheduleWakeup /loop-only fix (#103, 2026-07-27).

Live incident (codex-bridge, reported by the user): Claude announced "I'll
wake up at 13:30", called ScheduleWakeup, the harness accepted it and printed
"Next wakeup scheduled for 13:30" -- and nobody ever woke up. ScheduleWakeup
is, by the tool's own description, a pacer for `/loop` dynamic mode -- outside
an armed `/loop` it is a SILENT no-op: the call succeeds, the time is echoed
back, and nothing fires. Our own rules recommended it as a general "very long
wait" mechanism in `modules/core/ci-monitoring.md` and
`verify-launched-work-liveness` (module stub + skill) WITHOUT ever stating the
`/loop`-armed condition -- so Claude followed our own advice into a dead
session, twice (2026-07-20 and 2026-07-27).

The user's correction (issue comment, 2026-07-27) explicitly RULES OUT adding
a detector/enforcement mechanism for this -- the bug is one wrong
recommendation in prose, and the fix is to REMOVE it, not to build machinery
that compensates for bad text ("miesto toho aby claude a modely sa spravali
stale lepsie a lepsie tak len dalsie a dalsie obchadzky a vymysly"). GREEN
criterion: the recommendation is gone; the mechanisms that actually work
outside `/loop` (foreground bounded poll loop, a run_in_background loop that
re-arms, a bounded Monitor) remain untouched -- no new hook, job, or gate.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent

FILES = [
    "modules/core/ci-monitoring.md",
    "modules/quality/verify-launched-work-liveness.md",
    "skills/verify-launched-work-liveness/SKILL.md",
]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestScheduleWakeupLoopOnly(TestCase):
    def test_no_general_wait_recommends_schedule_wakeup(self):
        # ScheduleWakeup is a /loop dynamic-mode pacer; outside an armed /loop
        # it is a silent no-op. None of these general-purpose long-wait rules
        # may recommend it -- see #103.
        for rel in FILES:
            t = read(rel)
            self.assertNotIn(
                "ScheduleWakeup",
                t,
                f"{rel} still recommends ScheduleWakeup for a general long "
                "wait -- outside an armed /loop it never fires (#103).",
            )

    def test_working_alternatives_still_present(self):
        # The fix is subtractive only -- no new mechanism. The mechanisms
        # that actually work outside /loop must survive the edit.
        ci = read("modules/core/ci-monitoring.md")
        lv_module = read("modules/quality/verify-launched-work-liveness.md")
        lv_skill = read("skills/verify-launched-work-liveness/SKILL.md")
        self.assertIn("Foreground bounded poll loop", ci)
        self.assertIn("foreground bounded loop", lv_module)
        self.assertIn("foreground bounded poll loop", lv_skill)


if __name__ == "__main__":
    main()
