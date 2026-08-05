"""#243 — job 9 (`goal_autoarm`) never armed the presenter pane on dev2.

Live incident 2026-08-05, pane `%20`, project `presenter`. The session had
printed the arm question AND its `/goal` line, the pane was genuinely idle at a
bare `❯`, and job 9 logged nothing at all — every gate `continue`s silently, so
there was no journal trace to debug from. Evaluating the gates by hand against
the real capture put the stop at `_classify_boundary`, which reported `busy`
for an idle pane.

TWO independent faults close the two box-detection paths, and only their
coincidence is visible:

1. The STRUCTURAL strategy (`separator / ❯ <draft> / separator`) is defeated by
   a LABELLED top border. Claude Code renders the session's effort mode into
   the box's own top edge (`──── ultracode ─`); `_is_separator_line` is strict
   by design, rejects it, and the pair is never found.

2. The GLYPH FALLBACK no longer recognises airuleset's own managed statusline.
   `_is_bottom_chrome` matched it with `startswith("ctx ")`, which held only
   while the ctx fill bar LED the line. #223 dropped the bar and put the usage
   windows first, so the row now starts `5h 7%(4h)` — not chrome — and the
   bottom-up peel stops ON the statusline and returns it as the box row.

The golden fixture below is the live capture. The two isolating classes then
pin each fault on its own, so neither fix can pass for the other's reason.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd


# The live dev2 `%20` capture (presenter), transcribed from
# `tmux capture-pane -p -J`. The separator after `❯` is a NON-BREAKING SPACE,
# exactly as a real CC 2.1.220 pane renders it.
PRESENTER_PANE = (
    "  ❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
    "\n"
    "✻ Churned for 1m 1s\n"
    "\n"
    "──────────────────────────────────────────────────── ultracode ─\n"
    "❯\xa0                                                            \n"
    "────────────────────────────────────────────────────────────────\n"
    "  5h 7%(4h)  wk 65%(3d)  F 67%(2d)  sub 24.8.(19d)  "
    "I 4 core · str 0 · skip 2  ctx 292K ~$0.29  vychod@varos.sk  caveman:lite\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)

# The SAME box, but with an unrecognised chrome row below the statusline (the
# live `⧉  <project>` shape already documented in `_find_boundary_line`). That
# makes the glyph fallback structurally unable to reach the box, so this
# fixture can ONLY be resolved by the structural strategy — which is what
# isolates fault 1 from fault 2.
LABELLED_BORDER_ONLY = (
    "  ❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
    "\n"
    "──────────────────────────────────────────────────── ultracode ─\n"
    "❯\xa0                                                            \n"
    "────────────────────────────────────────────────────────────────\n"
    "  ⧉  presenter-dev2\n"
)

# A BORDERLESS box (no separator pair at all) followed by the post-#223
# statusline — resolvable only by the glyph fallback, which isolates fault 2.
BORDERLESS_NEW_STATUSLINE = (
    "  ❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
    "\n"
    "❯\xa0                                                            \n"
    "  5h 7%(4h)  wk 65%(3d)  F 67%(2d)  sub 24.8.(19d)  "
    "I 4 core · str 0 · skip 2  ctx 292K ~$0.29  vychod@varos.sk  caveman:lite\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)


class GoldenPresenterPane(unittest.TestCase):
    """The live capture job 9 refused to arm."""

    def test_the_bare_input_box_is_found(self):
        self.assertEqual(wd._find_input_box(PRESENTER_PANE), ("❯", "❯", False))

    def test_the_boundary_is_input_not_busy(self):
        self.assertEqual(wd._classify_boundary(PRESENTER_PANE), ("input", ""))

    def test_the_pane_reads_as_a_free_bare_prompt(self):
        self.assertTrue(wd._has_free_prompt(PRESENTER_PANE, bare_only=True))

    def test_the_arm_question_is_still_above_the_box(self):
        self.assertIn("NEEDS YOU", wd._above_input_box(PRESENTER_PANE))


class LabelledTopBorderIsAcceptedStructurally(unittest.TestCase):
    """Fault 1 alone: the glyph fallback cannot reach this box."""

    def test_the_box_rows_come_from_between_the_borders(self):
        self.assertEqual(wd._input_box_rows_raw(LABELLED_BORDER_ONLY), ["❯"])

    def test_the_boundary_is_input_not_busy(self):
        self.assertEqual(wd._classify_boundary(LABELLED_BORDER_ONLY), ("input", ""))

    def test_the_bottom_edge_still_has_to_be_a_strict_separator(self):
        # A labelled BOTTOM edge is not accepted as the box's closing border —
        # the loosening is deliberately one-sided.
        capture = LABELLED_BORDER_ONLY.replace(
            "────────────────────────────────────────────────────────────────",
            "──────────────────────────────────────────────── something ─")
        self.assertNotEqual(wd._input_box_rows_raw(capture), ["❯"])


class ManagedStatuslineIsChromeInAnySegmentOrder(unittest.TestCase):
    """Fault 2 alone: no border to resolve structurally."""

    def test_the_post_223_statusline_is_chrome(self):
        self.assertTrue(wd._is_bottom_chrome(
            "5h 7%(4h)  wk 65%(3d)  F 67%(2d)  sub 24.8.(19d)  "
            "I 4 core · str 0 · skip 2  ctx 292K ~$0.29  a@b.sk  caveman:lite"))

    def test_the_pre_223_statusline_is_still_chrome(self):
        # regression control — the ctx meter used to lead the line
        self.assertTrue(wd._is_bottom_chrome(
            "ctx ██░░░░░░░░  5h 19% (1h)  wk 46% (5d)  Fable 32% (5d)  "
            "Issues 3/14  caveman   /rc"))

    def test_a_borderless_pane_still_finds_its_box(self):
        self.assertEqual(
            wd._classify_boundary(BORDERLESS_NEW_STATUSLINE), ("input", ""))

    def test_a_draft_quoting_the_statusline_is_never_chrome(self):
        # fail-safe direction: a row carrying the prompt glyph is the box, so
        # it must never be peeled away as chrome however it reads.
        self.assertFalse(wd._is_bottom_chrome("❯ wk 65% of the budget is gone"))
        self.assertFalse(wd._is_bottom_chrome("❯ ctx 292K looks high"))

    def test_a_selected_agent_strip_row_is_still_chrome(self):
        # control for the guard above — these DO start with the glyph and must
        # stay chrome (#36).
        self.assertTrue(wd._is_bottom_chrome("❯ ● main"))
        self.assertTrue(wd._is_bottom_chrome("❯ ◯ autopilot-worker"))

    def test_ordinary_prose_is_not_chrome(self):
        self.assertFalse(wd._is_bottom_chrome("the ctx budget is the problem"))
        self.assertFalse(wd._is_bottom_chrome("wk is short for week"))


if __name__ == "__main__":
    unittest.main()
