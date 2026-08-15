"""#386: `pane_goal_armed`'s True-fast-path trusted a bare `◎ /goal` substring
ANYWHERE in `footer` (everything rendered below the input box's `❯` head). A
parked, UNSENT multi-line DRAFT renders its wrapped CONTINUATION rows BELOW the
head, so a draft that merely QUOTES the rendered glyph read as `True` (armed)
even on a genuinely DARK goal — the mirror image of the false-NEGATIVE #383
closed one branch over (#383 stopped the function trusting a MISSING indicator
as "dark" without real trailing chrome; this stopped it trusting a PRESENT
indicator string as "armed" with no chrome check at all).

Load-bearing invariant (shared with test_goal_indicator_above_box.py /
test_goal_undeterminable_pane.py): only a `False` verdict lets `deliver_goal`
proceed. Here the danger is the OPPOSITE direction — a spurious `True` on a dead
loop SUPPRESSES job 20's dark-watch re-arm/recovery for that session, silent
until a human notices the loop stopped.

The fix mirrors #383 exactly: scope the fast-path's `◎ /goal` scan to the
genuine unbroken TRAILING-chrome block (`_trailing_bottom_chrome`, already used
one line below for the False-path), NOT the whole `footer`. The real legacy
footer render (glyph appended to the statusline chrome row) is part of that
block and still matches; a draft-continuation quote is excluded by construction
(the backward walk stops at the first non-chrome draft row). No new
function/regex/heuristic — the exact classifier #383 already established."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import pane_goal_armed

# Chrome fixtures shared verbatim with test_goal_indicator_above_box.py — a
# valid CC render surrounding the input box.
TOP = "─" * 120 + " ultracode ─"
BOT = "─" * 130
# A genuinely DARK statusline — NO `◎ /goal` glyph on it.
STAT = "  5h 97%(2h)  wk 38%(4d)  opus  I 23  ctx 314K ~$0.16  caveman:lite"
STRIP = "  ● main"
# The real LEGACY footer render — the glyph rides the statusline chrome row.
STAT_ARMED = STAT + "  ◎ /goal active (8m)"

CONV = "  earlier conversation output"

# The #386 draft: three rows of an UNSENT draft parked in the box. The MIDDLE
# continuation row QUOTES the rendered glyph as plain draft text; it is neither
# the box head nor genuine trailing chrome.
DRAFT_HEAD = "❯ note the render I saw while debugging the watchdog earlier: the"
DRAFT_GLYPH = "  supervisor pane showed ◎ /goal active (2h) in its header, and I"
DRAFT_TAIL = "  want to paste that whole observation here before I forget it"


class Footer386DraftGlyphReadsDark(unittest.TestCase):
    def test_draft_glyph_with_dark_statusline_in_view_reads_False(self):
        # THE #386 bug: a draft-continuation row quotes `◎ /goal` while the real
        # DARK statusline is fully in view below the box. Genuinely dark ->
        # must read False (pre-fix returned True).
        cap = "\n".join([CONV, TOP, DRAFT_HEAD, DRAFT_GLYPH, DRAFT_TAIL,
                         BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)

    def test_draft_glyph_with_real_chrome_offscreen_reads_None(self):
        # The off-screen variant (mirror of #383's own scenario): the draft
        # quoting the glyph fills the viewport, so the box border + real
        # statusline have scrolled off. The glyph sits in a footer continuation
        # row -> pre-fix returned True; correct answer is None (undeterminable).
        cap = "\n".join([
            CONV, TOP,
            "❯ pasting my observation about the render and it wraps across",
            "  several rows, one quoting ◎ /goal active (2h) verbatim, and it",
            "  keeps going past the bottom so the box border and statusline",
            "  have scrolled off the top of what tmux captured here"])
        self.assertIsNone(pane_goal_armed(cap))

    def test_the_draft_glyph_row_is_excluded_from_the_trailing_chrome(self):
        # Unit-level lock on the fix mechanism: the quoted-glyph continuation
        # row is not part of the unbroken trailing-chrome block, so the scoped
        # fast-path can never see it.
        lines = "\n".join([CONV, TOP, DRAFT_HEAD, DRAFT_GLYPH, DRAFT_TAIL,
                           BOT, STAT, STRIP]).splitlines()
        idx = max(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("❯")
                  and not wd._is_bottom_chrome(ln.strip()))
        footer = lines[idx + 1:]
        trailing = wd._trailing_bottom_chrome(footer)
        self.assertTrue(trailing, "real chrome (border/statusline/strip) is in view")
        self.assertFalse(any(wd.GOAL_INDICATOR in s for s in trailing),
                         "the quoted-glyph draft row must not reach the trailing block")


class TrueCasesStayTrue(unittest.TestCase):
    """Every genuinely-armed render must keep reading True after the fix."""

    def test_legacy_footer_indicator_on_statusline_reads_True(self):
        # The real legacy render: glyph appended to the statusline chrome row,
        # box empty. (Verbatim mirror of
        # test_goal_indicator_above_box.test_legacy_footer_indicator_still_reads_True.)
        cap = "\n".join([CONV, TOP, "❯ ", BOT, STAT_ARMED, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_legacy_footer_indicator_with_a_draft_also_in_the_box_reads_True(self):
        # A genuinely-armed pane whose box ALSO holds a draft: the glyph is on
        # the real trailing statusline, the draft continuation sits ABOVE it.
        # Must still read True — the fix must not sacrifice this real case.
        cap = "\n".join([CONV, TOP, DRAFT_HEAD, DRAFT_TAIL,
                         BOT, STAT_ARMED, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_clean_header_armed_pane_reads_True(self):
        # #388 current CC render: glyph on a standalone line ABOVE the box.
        ind = " " * 100 + "◎ /goal active (2h)"
        cap = "\n".join(["  ✔ REGRESSION done", ind, TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), True)

    def test_fix_confined_to_footer_a_header_glyph_never_reaches_the_fast_path(self):
        # #487 (a PARALLEL, not-yet-integrated lane at the time this landed)
        # widened the HEADER regex `_GOAL_HEADER_INDICATOR_RX` for the live gk
        # render `› stashed · ◎ /goal active (1d)`. This fix touches ONLY the
        # footer fast-path, so it cannot interact with that widening: for a
        # header-only glyph render, the footer carries no glyph on any
        # trailing-chrome row, so the scoped fast-path never fires and the
        # header check alone decides the verdict. Asserting that
        # non-interaction directly (not the header verdict itself, which is
        # False pre-#487 and True post-#487) keeps this test correct whether or
        # not #487 is integrated.
        cap = "\n".join(["  ✔ prior output",
                         "› stashed · ◎ /goal active (1d)",
                         TOP, "❯ ", BOT, STAT, STRIP])
        lines = cap.splitlines()
        idx = max(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("❯")
                  and not wd._is_bottom_chrome(ln.strip()))
        trailing = wd._trailing_bottom_chrome(lines[idx + 1:])
        self.assertFalse(any(wd.GOAL_INDICATOR in s for s in trailing),
                         "a header-only glyph must never reach the footer fast-path")


class DarkAndUndeterminableStayCorrect(unittest.TestCase):
    def test_genuinely_dark_pane_reads_False(self):
        cap = "\n".join([CONV, TOP, "❯ ", BOT, STAT, STRIP])
        self.assertIs(pane_goal_armed(cap), False)

    def test_383_offscreen_footer_with_no_chrome_reads_None(self):
        # #383 regression: box holds a big draft, real footer scrolled off,
        # no glyph anywhere -> None (undeterminable), never a confident dark.
        cap = "\n".join([
            "   … +22 completed", TOP,
            "❯ open issue THIS box is OBLIGED to action - the CORE slice",
            "  owns it (needs-gatekeeper) - is resolved, and (B) holds ONLY",
            "  more draft continuation text with no chrome below it at all"])
        self.assertIsNone(pane_goal_armed(cap))


if __name__ == "__main__":
    unittest.main()
