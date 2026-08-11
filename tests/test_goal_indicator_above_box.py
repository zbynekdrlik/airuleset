"""#388: pane_goal_armed must detect the ◎ /goal indicator now rendered on a
standalone line directly ABOVE the input box (a CC rendering change confirmed
live on 3 panes 2026-08-11), not only in the footer below the box. A clean
armed pane previously returned False -> the watchdog's dark-by-age re-armed it
-> the camera-box spurious /goal stuff. Load-bearing invariant: pane_goal_armed
must NEVER return False on a pane whose goal is armed (True/None both prevent
re-arm; only False triggers it)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import pane_goal_armed

TOP = "─" * 120 + " ultracode ─"
BOT = "─" * 130
IND = " " * 100 + "◎ /goal active (2h)"      # right-aligned, alone on line
STAT = "  5h 97%(2h)  wk 38%(4d)  opus  I 23  ctx 314K ~$0.16  caveman:lite"
STRIP = "  ● main"
WORKER = "  ◯ autopilot-worker  Monitoring CI"


class GoalIndicatorAboveBox(unittest.TestCase):
    def test_clean_armed_pane_indicator_above_box_reads_True(self):
        cap = "\n".join(["  ✔ REGRESSION done", "   … +22 completed",
                         IND, TOP, "❯ ", BOT, STAT,
                         "  ⏵⏵ bypass permissions on (shift+tab)",
                         "", STRIP, WORKER])
        self.assertIs(pane_goal_armed(cap), True)

    def test_conversation_mention_not_armed_reads_False(self):
        cap = "\n".join([
            "● The glyph ◎ /goal active moved above the box (CC update).",
            "  pane_goal_armed searched only the footer for ◎ /goal before.",
            TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)

    def test_genuinely_dark_pane_reads_False(self):
        cap = "\n".join(["  just some conversation output",
                         TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)

    def test_legacy_footer_indicator_still_reads_True(self):
        cap = "\n".join(["  conversation", TOP, "❯ ", BOT,
                         STAT + "  ◎ /goal active (8m)", STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_stuck_draft_indicator_offscreen_footer_preserves_None(self):
        cap = "\n".join([
            "   … +22 completed", IND, TOP,
            "❯ open issue THIS box is OBLIGED to action - the CORE slice",
            "  owns it (needs-gatekeeper) - is resolved, and (B) holds ONLY",
            "  commands: python3 airuleset.py core-quals --count printing 0",
            "  more draft continuation text with no chrome below it at all"])
        self.assertIsNone(pane_goal_armed(cap))


if __name__ == "__main__":
    unittest.main()
