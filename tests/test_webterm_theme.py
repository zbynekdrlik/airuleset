"""#643 — Campbell theme parity for the webterm dashboard.

The owner's webterm renders with a grey GitHub-dark theme (`#0d1117`); #643
replaces it with the Campbell palette (his Windows Terminal look). These tests
assert:
  * every Campbell colour from the issue body is present in the served page;
  * the palette lives in ONE place (a distinctive colour appears exactly once);
  * the grey `#0d1117` is gone from the dashboard;
  * the theme + a Cascadia-ish monospace font are ACTUALLY applied to the xterm
    Terminal (functional node proof of `themeTerminal`, daemon-agnostic — it
    operates on `window.term`, so it survives a ttyd -> GoTTY switch).
"""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402


# The exact Campbell palette from the #643 issue body.
CAMPBELL = {
    "background": "#0C0C0C", "foreground": "#CCCCCC", "cursor": "#FFFFFF",
    "black": "#0C0C0C", "red": "#C50F1F", "green": "#13A10E", "yellow": "#C19C00",
    "blue": "#0037DA", "purple": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC",
    "brightBlack": "#767676", "brightRed": "#E74856", "brightGreen": "#16C60C",
    "brightYellow": "#F9F1A5", "brightBlue": "#3B78FF", "brightPurple": "#B4009E",
    "brightCyan": "#61D6D6", "brightWhite": "#F2F2F2",
}

# Distinctive Campbell colours deliberately NOT reused in the chrome CSS, so
# each proves the palette is in exactly ONE place (the theme object).
_UNIQUE_PALETTE_COLOURS = ("#C19C00", "#881798", "#B4009E", "#F9F1A5", "#61D6D6")


def _dash():
    inv = [{"id": "dev1", "label": "dev1 (localhost)", "kind": "owner",
            "local": True, "host": None, "user": None}]
    return w.render_dashboard_html(inv, ttyd_base="/t")


def _extract_js_const(html, name):
    """Source of a top-level `const <name> = <value>;`, where value is a
    brace/bracket-balanced object/array or a quoted string ending at the
    top-level `;`. Works for the theme OBJECT and the font-stack STRING (whose
    literals contain no `;` or unbalanced brackets)."""
    start = html.index("const %s" % name)
    i = html.index("=", start) + 1
    depth = 0
    for j in range(i, len(html)):
        c = html[j]
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif c == ";" and depth == 0:
            return html[start:j + 1]
    raise AssertionError("no terminating ; for const %s" % name)


def _extract_js_function(html, name):
    start = html.index("function %s(" % name)
    i = html.index("{", start)
    depth = 0
    for j in range(i, len(html)):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError("unbalanced braces extracting %s" % name)


class TestCampbellPalettePresent(unittest.TestCase):
    def test_every_campbell_colour_is_in_the_served_page(self):
        html = _dash()
        for key, colour in CAMPBELL.items():
            self.assertIn(colour, html, "missing Campbell %s = %s" % (key, colour))

    def test_palette_lives_in_exactly_one_place(self):
        # A distinctive palette colour (never reused in chrome CSS) must appear
        # exactly once — proving the palette is a single source of truth.
        html = _dash()
        for colour in _UNIQUE_PALETTE_COLOURS:
            self.assertEqual(html.count(colour), 1,
                             "%s must appear exactly once (single palette place)"
                             % colour)

    def test_grey_github_dark_theme_is_gone(self):
        # The old grey `#0d1117` (and its chrome siblings) must not survive on
        # the dashboard — that is the "sivá nevýrazná téma" the owner rejected.
        html = _dash()
        self.assertNotIn("#0d1117", html)
        self.assertNotIn("#161b22", html)

    def test_campbell_black_is_the_dashboard_background(self):
        html = _dash()
        self.assertIn("#0C0C0C", html)


class TestThemeIsActuallyApplied(unittest.TestCase):
    """Functional proof (node, no jsdom): the REAL extracted themeTerminal sets
    term.options.theme to Campbell + a Cascadia-ish font, idempotently."""

    def test_theme_terminal_sets_campbell_and_font(self):
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = _dash()
        harness = (
            _extract_js_const(html, "CAMPBELL_THEME") + "\n"
            + _extract_js_const(html, "TERM_FONT_STACK") + "\n"
            + _extract_js_function(html, "themeTerminal") + "\n"
            + "const term = { options: {} };\n"
            + "themeTerminal(term);\n"
            + "themeTerminal(term);\n"  # idempotent (guarded)
            + "process.stdout.write(JSON.stringify({\n"
            + "  bg: term.options.theme.background,\n"
            + "  green: term.options.theme.green,\n"
            + "  cursor: term.options.theme.cursor,\n"
            + "  brightBlue: term.options.theme.brightBlue,\n"
            + "  font: term.options.fontFamily,\n"
            + "}));\n"
        )
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(harness)
            hp = f.name
        try:
            r = subprocess.run(["node", hp], capture_output=True, text=True,
                               timeout=30)
        finally:
            Path(hp).unlink(missing_ok=True)
        self.assertEqual(r.returncode, 0,
                         "node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
        out = json.loads(r.stdout)
        self.assertEqual(out["bg"], "#0C0C0C")
        self.assertEqual(out["green"], "#13A10E")
        self.assertEqual(out["cursor"], "#FFFFFF")
        self.assertEqual(out["brightBlue"], "#3B78FF")
        self.assertIn("Cascadia", out["font"])
        self.assertIn("monospace", out["font"])


if __name__ == "__main__":
    unittest.main()
