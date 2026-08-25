"""#694 — the dashboard HTML/CSS/JS template lives in its own sibling leaf
module, out of the logic module.

`cli_webterm.py` (2173 lines pre-#694) mixed two kinds of content: the fleet
inventory/provisioning/connect/render LOGIC and the ~642-line presentation
ASSET `_DASHBOARD_TEMPLATE`. Every dashboard CSS/JS ticket (#643/#661/#672/
#677/#678/#691/#700) edited a giant Python string inside the logic module.
#694 extracts the template VERBATIM into `cli_webterm_dash_template.py`, a
pure constant leaf, with `cli_webterm.py` aliasing it so the `@@…@@`
single-pass substitution contract and `render_dashboard_html` stay
byte-identical.

These tests lock the split invariant so the template can never silently move
back inline (the #614/#638 drop-in lesson: an invariant without a negative
lock gets re-violated by hand):
  * the sibling module exists, exports the real template, and is a PURE leaf
    (zero imports, zero defs — logic must never creep into the asset module);
  * `cli_webterm._DASHBOARD_TEMPLATE` IS the extracted constant (identity,
    not a copy), so every existing render path/test keeps working unchanged;
  * `cli_webterm.py` source carries no inline dashboard HTML any more;
  * the LIVE substitution sentinels (`@@BUTTONS@@`/`@@CFG_JSON@@`/
    `@@THEME_JSON@@` — `@@COUNT@@` left the template with #671/#674's top-bar
    rework and survives only as a vestigial subst entry) are present, are the
    ONLY sentinel-shaped tokens in the template, and NO sentinel-shaped token
    survives rendering — with a #700 content-continuity spot check
    (`stretchFrameToFill` shipped in the output).
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

_SENTINELS = {"@@BUTTONS@@", "@@CFG_JSON@@", "@@THEME_JSON@@"}


def _inv():
    return [{"id": "s1", "label": "sess 1", "kind": "owner",
             "local": False, "host": "10.0.0.1", "user": "u1"}]


class TestTemplateExtraction694(unittest.TestCase):

    def test_template_module_exists_and_exports_the_dashboard(self):
        import cli_webterm_dash_template as t
        self.assertIsInstance(t.DASHBOARD_TEMPLATE, str)
        self.assertTrue(t.DASHBOARD_TEMPLATE.startswith("<!DOCTYPE html>"))
        self.assertTrue(t.DASHBOARD_TEMPLATE.endswith("</html>\n"))
        # the real ~642-line template, not a stub
        self.assertGreater(len(t.DASHBOARD_TEMPLATE), 20_000)

    def test_template_module_is_a_pure_constant_leaf(self):
        src = (REPO / "cli_webterm_dash_template.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*(?:import|from)\s")
        self.assertNotRegex(src, r"(?m)^\s*(?:def|class)\s")

    def test_cli_webterm_aliases_the_extracted_constant(self):
        import cli_webterm as w
        import cli_webterm_dash_template as t
        self.assertIs(w._DASHBOARD_TEMPLATE, t.DASHBOARD_TEMPLATE)

    def test_logic_module_carries_no_inline_dashboard_html(self):
        src = (REPO / "cli_webterm.py").read_text(encoding="utf-8")
        self.assertNotIn("<!DOCTYPE html>", src)
        self.assertNotIn("<html lang=", src)

    def test_substitution_contract_intact_end_to_end(self):
        import cli_webterm as w
        import cli_webterm_dash_template as t
        for s in _SENTINELS:
            self.assertIn(s, t.DASHBOARD_TEMPLATE)
        # no extra sentinel the single-pass render would leave unsubstituted
        self.assertEqual(set(re.findall(r"@@[A-Z_]+@@", t.DASHBOARD_TEMPLATE)),
                         _SENTINELS)
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        # NO sentinel-shaped token survives rendering at all
        self.assertNotRegex(html, r"@@[A-Z_]+@@")
        # #700 content-continuity spot check: the third fill layer still ships
        self.assertIn("function stretchFrameToFill", html)


if __name__ == "__main__":
    unittest.main()
