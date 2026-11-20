"""#1381: Claude Code 2.1.327 renders the armed-goal indicator as one ` · `
segment of a chrome line (weekly-limit notice, update banner). pane_goal_armed
must read that as armed, and must keep prose with separators dark (#393)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import pane_goal_armed

TOP = "─" * 120 + " ultracode ─"
BOT = "─" * 130
STAT = "  5h 97%(2h)  wk 38%(4d)  opus  I 23  ctx 314K ~$0.16  caveman:lite"
STRIP = "  ● main"


class GoalHeaderSegments1381(unittest.TestCase):
    def test_cc_2_1_327_header_with_limit_and_update_segments_reads_True(self):
        # #1381 -- live dasio/dcontroller 20.11.2026: Claude Code 2.1.327 puts
        # the indicator in the MIDDLE of one ` · `-separated chrome line, with
        # the weekly-limit notice before it and the update banner after it.
        hdr = (" " * 37 + "You've used 91% of your weekly limit · resets Nov 23, "
               "11am (Europe/Prague) · ◎ /goal active (7s) · ✔ Update installed"
               " · /restart to apply")
        cap = "\n".join(["  ✻ Crunched for 1m 15s", hdr, TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_cc_2_1_327_header_with_update_suffix_only_reads_True(self):
        # #1381 -- live david3@subdev 20.11.2026.
        hdr = " " * 114 + "◎ /goal active (4d) · ✔ Update installed · /restart to apply"
        cap = "\n".join(["  ✻ Churned for 1m 17s", hdr, TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_prose_with_separators_around_the_glyph_is_never_armed(self):
        # #1381 -- the segment form must not re-open #393: a prose line that
        # merely uses ` · ` around a quoted indicator is NOT chrome.
        cap = "\n".join([
            "  the header shows · ◎ /goal active (7s) · next to the banner",
            TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)


if __name__ == "__main__":
    unittest.main()
