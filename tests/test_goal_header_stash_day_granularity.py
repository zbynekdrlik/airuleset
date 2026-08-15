"""#487: `_GOAL_HEADER_INDICATOR_RX` false-negative on the REAL live gk render
`› stashed · ◎ /goal active (1d)` — the lane-fill guard (#442/#481) went dead on
gk (deployed 645ed47, timer running, but no nudge fired at 0 workers / I=43).

Two independent reasons the closed-form regex failed on the observed pane:

1. CC prepends the stash-slot marker `› stashed · ` onto the SAME header line
   the goal glyph rides on, so the `^`-anchor never matches.
2. The goal age crossed to day granularity `(1d)`/`(2d)`, and the age-unit
   class `[hm]` rejects a day suffix.

`pane_goal_armed` therefore returns a CONFIDENT `False` (not `None`), and
`goal_lane_sweep` silently drops the pane as "not a candidate", so the #481
floor `min(5, backlog)` is never evaluated. Exactly the risk class #393-review
MINOR-1 predicted ("widen the class only if a real render is ever observed
live") — now observed live, so the widening is sanctioned: closed-form,
`[hmd]` + an optional known `› stashed · ` prefix.

The widening stays CLOSED-form (the #393 lesson): the tail is EXACTLY
` active` or ` active (<1-3 digits><h|m|d>)`, nothing between or after, and the
stash prefix is the literal `› stashed · ` derived from the repo's own
`STASH_MARKER` constant — never a divergent hardcoded copy. Every #393
false-positive control (wrapped-prose continuations with and without
punctuation) stays rejected."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import _GOAL_HEADER_INDICATOR_RX, pane_goal_armed, STASH_MARKER

# Chrome fixtures shared with test_goal_indicator_above_box.py — a valid CC
# render: box header (indicator line + top border), the `❯` input box, then the
# closing border + statusline + agent strip below it.
TOP = "─" * 120 + " ultracode ─"
BOT = "─" * 130
STAT = "  5h 97%(2h)  wk 38%(4d)  opus  I 43  ctx 314K ~$0.16  caveman:lite"
STRIP = "  ● main"


def _armed(m):
    return bool(_GOAL_HEADER_INDICATOR_RX.match(m))


class GoalHeaderClosedFormMatrix(unittest.TestCase):
    """Direct characterization of `_GOAL_HEADER_INDICATOR_RX` — the ONLY
    anchored parser of the goal-header line (the other two, cross_stream.py
    and notify-discord-pending.sh, are substring checks already tolerant of
    both the stash prefix and day granularity)."""

    def test_live_gk_render_stash_prefix_plus_day_matches(self):
        # #487 primary live render — currently a false NEGATIVE.
        self.assertTrue(_armed("› stashed · ◎ /goal active (1d)"))

    def test_day_granularity_age_matches(self):
        # #487 second live render — day granularity, no stash prefix.
        self.assertTrue(_armed("◎ /goal active (2d)"))
        self.assertTrue(_armed("◎ /goal active (30d)"))

    def test_stash_prefix_with_hour_and_minute_and_bare_matches(self):
        # The stash prefix is orthogonal to the age suffix: it must combine
        # with every already-accepted tail, not only the day one.
        self.assertTrue(_armed("› stashed · ◎ /goal active (2h)"))
        self.assertTrue(_armed("› stashed · ◎ /goal active (8m)"))
        self.assertTrue(_armed("› stashed · ◎ /goal active"))
        self.assertTrue(_armed("› stashed · ◎ /goal"))

    def test_stash_prefix_is_derived_from_the_repo_constant(self):
        # Lock the prefix to the repo's own STASH_MARKER + observed separator,
        # so a future rename of the constant cannot silently diverge the two.
        self.assertTrue(_armed(STASH_MARKER + " · ◎ /goal active (1d)"))

    def test_pre_existing_accepted_shapes_stay_green(self):
        # Regression: the shapes accepted before #487 must all still match.
        for s in ("◎ /goal",
                  "◎ /goal active",
                  "◎ /goal active (1m)",
                  "◎ /goal active (58m)",
                  "◎ /goal active (2h)"):
            self.assertTrue(_armed(s), s)

    def test_393_false_positive_controls_stay_rejected(self):
        # #393 wrapped-prose continuations (with and without punctuation) —
        # the closed form must keep rejecting every one, prefix or not.
        for s in ("◎ /goal active, right where the earlier bug expected "
                  "only chrome.",
                  "◎ /goal armed",
                  "◎ /goal active and the loop keeps going",
                  "◎ /goal active (footer) so we know",
                  "The glyph ◎ /goal active moved above the box (CC update).",
                  # the widened prefix must NOT open a new false-positive door:
                  "› stashed · ◎ /goal armed",
                  "› stashed · ◎ /goal active and the loop keeps going"):
            self.assertFalse(_armed(s), s)

    def test_partial_or_fractional_shapes_stay_rejected(self):
        # Closed-form boundary: a partial prefix (missing the `›`), and a
        # fractional-hour suffix (#393 MINOR-2 accepted residual, unobserved),
        # must NOT match — widen only on a real live render.
        self.assertFalse(_armed("stashed · ◎ /goal active (1d)"))
        self.assertFalse(_armed("◎ /goal active (1.5h)"))
        self.assertFalse(_armed("◎ /goal active (1w)"))


class GoalHeaderRenderThroughPaneGoalArmed(unittest.TestCase):
    """The behavioral proof: the two live renders, placed on a standalone line
    directly above the input box (CC's current header render, #388), must make
    `pane_goal_armed` read True — never the confident False that killed the
    lane-fill guard on gk."""

    def test_gk_stash_render_above_box_reads_True(self):
        cap = "\n".join(["  ✔ prior output", "   … +22 completed",
                         "› stashed · ◎ /goal active (1d)",
                         TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_day_granularity_render_above_box_reads_True(self):
        cap = "\n".join(["  ✔ prior output",
                         " " * 90 + "◎ /goal active (2d)",
                         TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_stash_prefixed_prose_mention_is_never_armed(self):
        # A conversation line that merely quotes the stash+glyph, with real
        # trailing prose, must still read False — the widening does not relax
        # the closed-form tail.
        cap = "\n".join([
            "  Discussing the stashed goal indicator right now:",
            "  › stashed · ◎ /goal active, as the pane showed after settling.",
            TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)


if __name__ == "__main__":
    unittest.main()
