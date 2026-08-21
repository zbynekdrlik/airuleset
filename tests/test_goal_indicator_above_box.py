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

    def test_wrapped_prose_continuation_starting_with_the_glyph_is_never_armed(
            self):
        # #393 -- a bare `.startswith(GOAL_INDICATOR)` prefix check is
        # defeated by ordinary word-wrapped assistant prose: the block's
        # own leading indent is stripped along with everything before the
        # glyph, so a rendered CONTINUATION row that happens to start with
        # "◎ /goal" still satisfies `.startswith` even though genuine
        # trailing prose (with real punctuation) follows it -- something
        # the real indicator's own render ("right-aligned, alone on its
        # line") never has. NOT a genuinely-armed pane.
        cap = "\n".join([
            "  Confirmed the render now shows the indicator on its own line:",
            "  ◎ /goal active, right where the earlier bug expected only "
            "chrome.",
            TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)

    def test_pending_cc_update_notification_prefix_before_glyph_reads_True(self):
        # #617 -- live montalu1@subdev 2026-08-21: when Claude Code has a
        # PENDING UPDATE it renders its own update-notification chrome on the
        # SAME standalone line the glyph rides on, directly above the box:
        # `✔ Update installed · Restart to update◎ /goal active (21m)` (the
        # glyph directly ABUTS "update", no separator -- byte-faithful from a
        # hexdump of the real capture). The `^`-anchored header regex missed
        # it -> pane_goal_armed returned False on a genuinely ARMED, alive
        # loop -> dark-watch re-armed it and typed a second (truncated) /goal
        # into the box (the #617 poisoned draft). Same class as #487 (the
        # stash-marker prefix), one chrome prefix over.
        upd = "✔ Update installed · Restart to update◎ /goal active (21m)"
        cap = "\n".join(["  ✻ Waiting for 1 background agent to finish",
                         upd, TOP, "❯ ", BOT, STAT, STRIP, WORKER])
        self.assertIs(pane_goal_armed(cap), True)

    def test_update_notification_prefix_with_trailing_prose_is_never_armed(
            self):
        # #617 -- the widened prefix must NOT re-open the #393 wrapped-prose
        # false-positive: the CLOSED-form tail ($ right after the glyph +
        # optional age) still rejects a line that CONTINUES past the glyph,
        # even one carrying the update-notification phrase. NOT armed.
        cap = "\n".join([
            "  Discussing the CC banner: Restart to update◎ /goal active and "
            "the loop keeps going",
            TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)

    def test_short_unpunctuated_continuation_is_also_never_armed(self):
        # #393-review MINOR-1 (fresh-context adversarial review,
        # 2026-08-12, executed against the real function) -- the FIRST
        # cut of this fix bounded the tail to an open allowlist of
        # spaces/word-chars/parens, which still accepted a wrapped-prose
        # continuation whose first 40 chars happen to contain NO
        # punctuation at all ("◎ /goal armed", "◎ /goal active and the
        # loop keeps going" -- 7 of 8 constructed cases matched). The
        # closed-form regex must reject the SHORTEST, most dangerous one
        # of those: a bare "◎ /goal armed" continuation row with nothing
        # else on the line, which the open allowlist alone could not
        # distinguish from a genuine (but truncated) render.
        cap = "\n".join([
            "  Discussing whether the indicator is lit right now:",
            "  ◎ /goal armed",
            TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)


if __name__ == "__main__":
    unittest.main()
