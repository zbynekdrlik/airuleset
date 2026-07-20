"""Watchdog job 9 — /goal auto-arm (user directive 2026-07-20: 'dost mi vadi
ze musim pracne vsade chodit a zadavat goal — malo by sa to samo').

/autopilot and /process-subdev end by PRINTING the /goal template and asking
the user to paste it — the ONE manual step left in every stream. The watchdog
now performs the paste itself: an IDLE pane whose tail asks to paste a /goal
(the arm question) and carries a printed `/goal ` line gets that exact line
typed + submitted. Safety gates: bare empty prompt only (never over user
text), never when a goal is already armed (`◎ /goal` in the statusline),
never into a busy pane, one arm per pane per window (dedup)."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

GOAL_LINE = ("/goal STOP CONDITIONS — the loop is DONE ... (B) SLICE DONE ... "
             "REVIEW-WATCH ... never park silently ...")

ARM_PANE = ("● autopilot · merge=auto · authority=branch-merge · 7 ticketov\n"
            + GOAL_LINE + "\n"
            "**Otázka — projekt odoo-erp (Money→Odoo):** autopilot je pripravený.\n"
            "• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám\n"
            "• Nič nevkladaj — autopilot sa nespustí\n"
            "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
            "❯ \n  ctx ███░  caveman\n")

ARMED_PANE = ARM_PANE.replace("  ctx ███░  caveman",
                              "  ctx ███░  caveman  ◎ /goal active (1m)")
BUSY_PANE = ARM_PANE.replace("❯ \n", "✳ Baking… (2m · esc to interrupt)\n❯ \n")
USER_TEXT_PANE = ARM_PANE.replace("❯ \n", "❯ rozpisany draft\n")
NO_QUESTION_PANE = ("● Bežná odpoveď bez arm otázky.\n❯ \n  ctx ███░\n")


class FakeTmux:
    def __init__(self, captured):
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "%1\tclaude\t/home/x/devel/demo"
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


def go(captured, state=None, now=None):
    tmux = FakeTmux(captured)
    logs = wd.goal_autoarm(now or time.time(), tmux, state if state is not None
                           else {})
    return tmux, logs


class TestGoalAutoarm(unittest.TestCase):
    def test_arm_question_gets_the_goal_typed(self):
        tmux, logs = go(ARM_PANE)
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertTrue(typed[0].startswith("/goal STOP CONDITIONS"), typed[0])
        self.assertIn("REVIEW-WATCH", typed[0])
        self.assertTrue(any("goal-autoarm" in ln for ln in logs), logs)

    def test_already_armed_goal_is_skipped(self):
        tmux, _ = go(ARMED_PANE)
        self.assertFalse(tmux.typed())

    def test_busy_pane_is_skipped(self):
        tmux, _ = go(BUSY_PANE)
        self.assertFalse(tmux.typed())

    def test_user_typed_text_is_never_overwritten(self):
        tmux, _ = go(USER_TEXT_PANE)
        self.assertFalse(tmux.typed())

    def test_no_arm_question_no_typing(self):
        tmux, _ = go(NO_QUESTION_PANE)
        self.assertFalse(tmux.typed())

    def test_dedup_one_arm_per_window(self):
        state = {}
        now = time.time()
        t1, _ = go(ARM_PANE, state, now)
        self.assertTrue(t1.typed())
        t2, _ = go(ARM_PANE, state, now + 60)
        self.assertFalse(t2.typed(), "re-arm within the window must be deduped")

    def test_rearm_after_window_passes(self):
        state = {}
        now = time.time()
        go(ARM_PANE, state, now)
        t2, _ = go(ARM_PANE, state, now + wd.GOAL_ARM_WINDOW_S + 5)
        self.assertTrue(t2.typed())


if __name__ == "__main__":
    unittest.main()
