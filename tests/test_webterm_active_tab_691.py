"""#691 — the ACTIVE dashboard tab must be distinguishable at first glance.

Owner (2026-08-25, verbatim): "aktualne zvyrazneny tab v webterme je skoro na
nerozoznanie od ostatnych, potrebujem vyraznejsie vyznacenie na ktorom tabe sa
nachadzam".

Root cause locked here: the pre-#691 `.tab.active` was `background: #0C0C0C`
(the page body colour — actually DARKER than the inactive `#1b1b1b` tabs, so
the focused tab read as recessed) and the one chromatic active cue, the
`.tab.active .ord` blue chip, was removed by #661. These tests lock the #691
replacement — one coherent restrained combination:

  * a clearly LIGHTER active background (`#333333`), a full step above the
    hover shade (`#262626`) so a hovered inactive tab can't masquerade as
    active;
  * a 2px Campbell-brightBlue accent bar along the tab's top edge
    (`box-shadow: inset 0 2px 0 0 #3B78FF` — zero layout shift);
  * a lighter rim (`border-color: #3f3f3f`) readable on the lighter body;
  * a bolder label (`.tab.active .al { font-weight: 700 }` — monospace face,
    so no advance-width change / no tab-row reflow);

while the pieces #691 must NOT touch stay locked: the `.ico` green marker, the
`.udot` red corner dot (must survive on the active background), and the active
text colour `#F2F2F2` (already asserted by test_webterm_per_human_tabs).
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
    m = re.search(selector_re + r" \{[^}]*\}", html)
    return m.group(0) if m else None


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
            return int(m.group(1)[1:], 16)

        # Strictly increasing luminance ramp: inactive < hover < active — the
        # "active = lifted/brightest" direction, and hover can never reach the
        # active shade.
        self.assertLess(bg(base), bg(hover))
        self.assertLess(bg(hover), bg(active))


class TestActiveTabAccentBar(unittest.TestCase):
    def test_active_tab_carries_the_brightblue_accent_bar(self):
        html = _render()
        rule = _rule(html, r"\.tab\.active")
        self.assertIsNotNone(rule)
        # 2px inset top bar in Campbell brightBlue — the single chromatic cue
        # (quietly restoring what the #661-removed .ord blue chip provided).
        self.assertIn("box-shadow: inset 0 2px 0 0 #3B78FF", rule)

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


class TestUntouchedNeighbours(unittest.TestCase):
    def test_udot_survives_unchanged(self):
        html = _render()
        rule = _rule(html, r"\.tab \.udot")
        self.assertIsNotNone(rule, ".tab .udot CSS rule not found")
        # Red dot + body-coloured ring — high contrast on BOTH the inactive
        # #1b1b1b and the new active #333333 backgrounds.
        self.assertIn("#E74856", rule)
        self.assertIn("box-shadow: 0 0 0 1px #0C0C0C", rule)
        self.assertIn(".tab.has-u .udot", html)

    def test_ico_survives_unchanged(self):
        html = _render()
        rule = _rule(html, r"\.tab \.ico")
        self.assertIsNotNone(rule, ".tab .ico CSS rule not found")
        self.assertIn("#13A10E", rule)


if __name__ == "__main__":
    unittest.main()
