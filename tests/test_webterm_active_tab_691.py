"""#691 REWORK — the ACTIVE dashboard tab, re-styled after the owner REJECTED
v0.1.55.

Owner (2026-08-25, verbatim rejection): "nepaci sa mi to lebo teraz text tabu sa
dotyka modreho pruzku a aj cervena bodka vobec to nedycha, alava sprava je
odsatenie ale zhora a z dola skoro vobec ... cervena bodka mal byt doplnok, teraz
to je navyraznejsi prvok, pritom najvyrznejsi mal byt nazov tabu, potom to ze je
tab vybraty a potom cervena bodka, bohate by stacilo keby ta zelena sipocka
nalavo ... bola cervena ked je tab v U a select tabu aby bol nejak inak rieseny
este ak teda nechces roztahovat tab vertikalne aby viacej dychal".

The PRIOR version of this file locked the v0.1.55 design (top inset blue stripe +
a separate red `.udot` corner dot). That design was REJECTED by the owner, so its
locks are a REQUIREMENT CHANGE, not a test-weakening — every replaced assertion
maps to one of the owner's four binding rework points below. These tests lock the
replacement:

  * VERTICAL BREATHING (point 1): `.tab` gains real top/bottom padding
    (`9px 12px 9px 16px`, up from `6px`); left/right stay (owner: already fine).
  * SELECTED (point 4): the top inset stripe that touched the label is retired;
    the selected tab reads through the lightest background (`#333333`), a bolder
    label, a lighter rim (`#3f3f3f`), and a BOTTOM accent underline
    (`box-shadow: inset 0 -2px 0 0 #3B78FF`) that never touches the text.
  * U INDICATOR (points 2+3): the separate `.udot` is GONE; the existing green ▸
    `.ico` arrow on the left turns Campbell brightRed (`#E74856`) when the tab is
    in U state — the accessory, the LEAST prominent element.
  * HIERARCHY (point 2): NAME (bold, largest mass) > SELECTED (bg + underline) >
    U (a small arrow colour-swap) — encoded structurally below.

The U-state DATA channel (`applyUStatus`'s `.has-u` toggle, the collector) is
UNCHANGED — only the RENDER of U moved from a dot to the arrow colour.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402


def _render():
    inv = w.webterm_inventory(profile=profiles.OWNER)
    return w.render_dashboard_html(
        inv, ttyd_base="/t", human="zbynek", term_grid=(176, 51))


def _rule(html, selector_re):
    # Same extraction pattern as test_webterm_per_human_tabs: expects exactly
    # one space before `{` and clips at the first `}` (so a `}`-containing
    # comment inside a block would truncate it). Both failure modes turn a
    # test RED, never falsely green — acceptable for locking literal values.
    m = re.search(selector_re + r" \{[^}]*\}", html)
    return m.group(0) if m else None


class TestActiveTabVerticalBreathing(unittest.TestCase):
    def test_tab_has_real_vertical_padding(self):
        """Point 1: text must not touch the accent bar or (former) dot — the
        tab now carries genuine top/bottom padding, taller than the crammed
        v0.1.55 `6px`; left/right stay put (owner: those were already fine)."""
        html = _render()
        base = _rule(html, r"\.tab")
        self.assertIsNotNone(base, ".tab CSS rule not found")
        m = re.search(r"padding: (\d+)px (\d+)px (\d+)px (\d+)px", base)
        self.assertIsNotNone(m, "no 4-value padding in .tab rule: %s" % base)
        top, right, bottom, left = (int(m.group(i)) for i in (1, 2, 3, 4))
        # Real vertical breathing — strictly more than the rejected 6px.
        self.assertGreater(top, 6, "top padding must grow past the crammed 6px")
        self.assertGreater(bottom, 6, "bottom padding must grow past the crammed 6px")
        self.assertEqual(top, bottom, "vertical padding symmetric")
        self.assertEqual(top, 9, "the chosen taller value")
        # Left/right unchanged from #661 (owner: left/right spacing already fine).
        self.assertEqual((right, left), (12, 16))


class TestActiveTabDistinctBackground(unittest.TestCase):
    def test_active_background_is_the_lightest_tab_shade(self):
        html = _render()
        rule = _rule(html, r"\.tab\.active")
        self.assertIsNotNone(rule, ".tab.active CSS rule not found")
        # The recessed body-matching background is gone; the active tab is now
        # the clearly lightest shade on the tab ramp.
        self.assertIn("background: #333333", rule)
        self.assertNotIn("#0C0C0C", rule)

    def test_active_background_outshines_inactive_and_hover(self):
        html = _render()
        base = _rule(html, r"\.tab")
        hover = _rule(html, r"\.tab:hover")
        active = _rule(html, r"\.tab\.active")
        self.assertIsNotNone(base)
        self.assertIsNotNone(hover)
        self.assertIsNotNone(active)

        def bg(rule):
            m = re.search(r"background: (#[0-9A-Fa-f]{6})", rule)
            self.assertIsNotNone(m, "no background colour in rule: %s" % rule)
            r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (1, 3, 5))
            # Brightness proxy sound only on ACHROMATIC greys (r == g == b) —
            # the tab ramp is grey by design (chroma confined to the accent).
            # Assert that precondition so a future chromatic background can't
            # silently game the ordering check.
            self.assertEqual(r, g, "tab background must stay achromatic grey: %s" % m.group(1))
            self.assertEqual(g, b, "tab background must stay achromatic grey: %s" % m.group(1))
            return r

        # Strictly increasing luminance ramp: inactive < hover < active.
        self.assertLess(bg(base), bg(hover))
        self.assertLess(bg(hover), bg(active))


class TestSelectedAccentIsBottomUnderline(unittest.TestCase):
    def test_active_tab_carries_a_bottom_underline_accent(self):
        html = _render()
        rule = _rule(html, r"\.tab\.active")
        self.assertIsNotNone(rule)
        # Point 4: the selected accent is a 2px Campbell-brightBlue underline on
        # the tab's BOTTOM edge (note the -2px) — a familiar "selected tab"
        # convention that never touches the label at the top. Zero layout shift.
        self.assertIn("box-shadow: inset 0 -2px 0 0 #3B78FF", rule)

    def test_top_inset_stripe_is_retired(self):
        """The v0.1.55 top stripe (`inset 0 2px 0 0 #3B78FF`) touched the label
        and is exactly what the owner rejected — it must be gone."""
        html = _render()
        rule = _rule(html, r"\.tab\.active")
        self.assertIsNotNone(rule)
        self.assertNotIn("inset 0 2px 0 0", rule)

    def test_accent_is_confined_to_the_active_state(self):
        html = _render()
        for sel in (r"\.tab", r"\.tab:hover"):
            rule = _rule(html, sel)
            self.assertIsNotNone(rule)
            self.assertNotIn("#3B78FF", rule)

    def test_active_rim_is_readable_on_the_lighter_body(self):
        html = _render()
        rule = _rule(html, r"\.tab\.active")
        self.assertIsNotNone(rule)
        # #2b2b2b would be DARKER than the #333333 body; the rim lightens.
        self.assertIn("border-color: #3f3f3f", rule)


class TestActiveTabBoldLabel(unittest.TestCase):
    def test_active_label_is_bold(self):
        html = _render()
        rule = _rule(html, r"\.tab\.active \.al")
        self.assertIsNotNone(rule, ".tab.active .al CSS rule not found")
        self.assertIn("font-weight: 700", rule)


class TestActiveStateWinsOverHover(unittest.TestCase):
    def test_active_rule_declared_after_hover(self):
        """`.tab:hover` and `.tab.active` have EQUAL specificity (0,2,0), so
        source order decides which background a hovered ACTIVE tab shows. The
        active rule must come later, or hovering the active tab would dim it
        back to the hover shade."""
        html = _render()
        hover_at = html.find(".tab:hover {")
        active_at = html.find(".tab.active {")
        self.assertGreater(hover_at, -1)
        self.assertGreater(active_at, -1)
        self.assertLess(hover_at, active_at)


class TestUIndicatorIsTheArrowColour(unittest.TestCase):
    def test_arrow_turns_red_when_the_tab_is_in_u(self):
        """Points 2+3: the U indicator is no longer a separate dot — the
        existing green ▸ arrow on the left turns Campbell brightRed when the
        tab's box has U > 0."""
        html = _render()
        rule = _rule(html, r"\.tab\.has-u \.ico")
        self.assertIsNotNone(rule, ".tab.has-u .ico CSS rule not found")
        self.assertIn("#E74856", rule)

    def test_default_arrow_stays_green(self):
        html = _render()
        rule = _rule(html, r"\.tab \.ico")
        self.assertIsNotNone(rule, ".tab .ico CSS rule not found")
        self.assertIn("#13A10E", rule)

    def test_separate_dot_element_and_css_are_retired(self):
        """The loud `.udot` (rejected as the most-prominent element) is gone
        entirely — from the rendered markup AND the stylesheet."""
        html = _render()
        self.assertNotIn('class="udot"', html)
        self.assertNotIn(".tab .udot", html)
        self.assertNotIn(".tab.has-u .udot", html)

    def test_u_toggle_plumbing_is_intact(self):
        """The U-state DATA channel is unchanged — applyUStatus still toggles
        the `.has-u` class the recoloured arrow keys on."""
        html = _render()
        apply = re.search(r"function applyUStatus\(.*?\n\}", html, re.S)
        self.assertIsNotNone(apply)
        self.assertIn("has-u", apply.group(0))


class TestHierarchyStructure(unittest.TestCase):
    """Point 2 (binding): NAME > SELECTED > U. Encoded structurally — the name
    is bold (largest visual mass), SELECTED carries the bg + underline cue, and
    U is only a colour-swap of the small existing arrow with NO separate loud
    element competing for attention."""

    def test_name_rank1_selected_rank2_u_rank3(self):
        html = _render()
        # rank 1 — NAME: the active label is bold.
        self.assertIn("font-weight: 700", _rule(html, r"\.tab\.active \.al"))
        # rank 2 — SELECTED: bottom underline accent present on active.
        self.assertIn("inset 0 -2px 0 0 #3B78FF", _rule(html, r"\.tab\.active"))
        # rank 3 — U: an accessory only (arrow colour swap), never a separate
        # loud badge — the udot that used to shout is gone.
        self.assertNotIn('class="udot"', html)
        self.assertIsNotNone(_rule(html, r"\.tab\.has-u \.ico"))


if __name__ == "__main__":
    unittest.main()
