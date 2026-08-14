"""#458: `pane_goal_armed` returned `None` (-> `deliver_goal` logged
`skip:undeterminable`, goal.py:546) on an ORDINARY ultracode pane whose only
problem was CC's pinned-preview / attachment row `⧉  <project>` (U+29C9)
rendered at the very BOTTOM of the chrome zone. `_is_bottom_chrome` did not
enumerate that glyph, so `_trailing_bottom_chrome`'s backward walk broke on it
immediately (returned []) and `pane_goal_armed` could not prove the footer's
chrome was in view -> `None`. Live-reproduced on montalu3@subdev (below), and
the same `⧉  <project>` shape is thrice-documented (upomienky-prehlad
2026-07-25, presenter-dev2 #243, email_redesign_preview #458).

Load-bearing invariant (mirrors test_goal_indicator_above_box.py): only a
`False` verdict lets `deliver_goal` proceed to arm. `None` blocks the loop
forever on a healthy, armable pane. The fix teaches the SHARED `_is_bottom_chrome`
about the `⧉` glyph -- widening the recognized-SAFE set by ONE real CC element,
never removing the undeterminable->skip fail-closed default for a genuinely
unreadable pane (a truly-novel glyph, a login overlay, a wedged state)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import pane_goal_armed

# The EXACT live montalu3@subdev capture from #458 (metadata `# …` header
# lines dropped -- a real `tmux capture-pane` never carries them; the two
# leading comment rows sat above the input box and could not have changed the
# verdict either way). Borders reproduced at their rendered widths; every
# substantive row is verbatim, incl. the `⧉  email_redesign_preview` breaker.
_TOP = "─" * 128 + " ultracode ─"
_BOT = "─" * 132
MONTALU3 = "\n".join([
    "● Automatické nasadenie /goal sa opäť nepodarilo (pane nedeterminovateľný)"
    " — vlož riadok ručne.",
    "",
    "  Otázka — projekt odoo-erp (ERP pre MONT-ALU/SlovNormal, stream montalu3):"
    " autopilot je pripravený — slice má 11 otvorených ticketov, 4 workeri"
    " práve bežia (#3094+#3095 SMS",
    "  potvrdenia, #3901 ikonka brány, #3783 OTP, #3892 e-maily).",
    "  • Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne a ide sám",
    "  • Nič nevkladaj — dokončia sa len bežiaci workeri",
    "",
    "  ❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne",
    "",
    "✻ Waiting for 3 background agents to finish",
    "",
    _TOP,
    "❯ ",
    _BOT,
    "  5h 7%(5h)  wk 41%(6d)  F 36%(6d)  fable  I 11 · gk 15 · skip 1  "
    "ctx 399K ~$0.40  claude-13@newlevel.church sub 13.8.(0d)  caveman:lite",
    "  ⏵⏵ bypass permissions on · PR #3971 · 1 shell, 1 monitor",
    "",
    "  ● main",
    "  ◯ autopilot-worker  Polling PR #3971 CI status        2h 6m 51s · ↓ 626.4k tokens",
    "  ◯ autopilot-worker  Writing poll-ci-4.sh script       2h 6m 25s · ↓ 418.4k tokens",
    "  ◯ autopilot-worker  Verifying upstream-sync merges cleanly  4m 22s · ↓ 496.4k tokens",
    "  ◯ autopilot-worker  Polling new CI run statuses       10m 10s · ↓ 585.6k tokens",
    "  ⧉  email_redesign_preview",
])


class Montalu3UndeterminablePane(unittest.TestCase):
    def test_the_pane_is_determinable_and_armable(self):
        # The reported symptom: pane_goal_armed returned None (undeterminable);
        # the pane is a bare, healthy, dark (unarmed) box -> must read False.
        self.assertIs(pane_goal_armed(MONTALU3), False)

    def test_the_rest_of_the_deliver_goal_chain_is_already_armable(self):
        # Everything else deliver_goal checks is already armable pre-fix (the
        # structural box-finder is immune to the ⧉ row) -- proving pane_goal_armed
        # was the SOLE blocker.
        self.assertEqual(wd._classify_boundary(MONTALU3), ("input", ""))
        self.assertEqual(wd._input_box_rows_raw(MONTALU3), ["❯"])
        self.assertFalse(wd.pane_waiting_on_user(MONTALU3))

    def test_the_pinned_preview_row_is_recognized_as_chrome(self):
        # The unit-level lock on the fix: the ⧉ <project> row (with and without
        # the capture's own leading indent) is bottom chrome.
        self.assertTrue(wd._is_bottom_chrome("⧉  email_redesign_preview"))
        self.assertTrue(wd._is_bottom_chrome("⧉  upomienky-prehlad"))
        self.assertTrue(wd._is_bottom_chrome("⧉  presenter-dev2"))

    def test_the_trailing_chrome_walk_no_longer_breaks_on_the_glyph(self):
        lines = MONTALU3.splitlines()
        idx = max(i for i, ln in enumerate(lines)
                  if ln.strip().startswith("❯") and not wd._is_bottom_chrome(ln.strip()))
        footer = lines[idx + 1:]
        trailing = wd._trailing_bottom_chrome(footer)
        self.assertTrue(trailing, "the ⧉ row must not break the trailing-chrome walk")
        self.assertTrue(any(not wd._is_border_rule(s) for s in trailing))

    def test_the_fail_closed_default_survives_for_a_truly_novel_glyph(self):
        # SAFETY: the recognized-SAFE set widened by ONE known glyph; a
        # genuinely-novel chrome glyph (`▶`) stays UNrecognized, and the same
        # pane shape with it at the bottom still reads undeterminable (None).
        self.assertFalse(wd._is_bottom_chrome("▶ brand-new-widget"))
        novel = "\n".join(["  conversation", _TOP, "❯ ", _BOT,
                           "  ▶ brand-new-widget"])
        self.assertIsNone(pane_goal_armed(novel))

    def test_a_draft_row_that_starts_with_the_glyph_is_never_chrome(self):
        # Fail-safe control, mirroring the statusline guard: a row carrying the
        # prompt glyph is the box (`❯ ⧉ …`), never peeled away as chrome.
        self.assertFalse(wd._is_bottom_chrome("❯ ⧉ poznámka o preview"))


if __name__ == "__main__":
    unittest.main()
