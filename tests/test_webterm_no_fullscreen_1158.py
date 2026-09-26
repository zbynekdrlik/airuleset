"""#1158 (owner 26.9.2026, verbatim: "vo webterme fillscreen tlacitko len
zavadzia, odoberho"): the webterm dashboard has no Fullscreen control and no
fullscreen / Keyboard-Lock script. The #585 Layer-1 close-confirm (the
beforeunload guard for a stray Ctrl+W) is a separate safety net and stays."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_webterm as w  # noqa: E402


def _html():
    inv = [{"id": "s%d" % i, "label": "sess %d" % i, "kind": "owner",
            "local": False, "host": "10.0.0.%d" % i, "user": "u%d" % i}
           for i in range(1, 4)]
    return w.render_dashboard_html(inv, ttyd_base="/t")


class TestNoFullscreenControl1158(unittest.TestCase):

    def test_no_fullscreen_button(self):
        html = _html()
        self.assertNotIn('id="fs"', html)
        self.assertNotIn("&#9974;", html)             # the button glyph
        self.assertNotIn('id="nav"', html)            # the now-empty control strip

    def test_no_fullscreen_or_keyboard_lock_script(self):
        html = _html()
        self.assertNotIn("requestFullscreen", html)
        self.assertNotIn("navigator.keyboard", html)
        self.assertNotIn("function goFullscreen(", html)
        self.assertNotIn("function keyboardLockSupported(", html)
        self.assertNotIn("fsBtn", html)

    def test_close_confirm_stays(self):
        html = _html()
        self.assertIn("'beforeunload'", html)
        self.assertIn("hasLiveTerminal()", html)
        self.assertIn("returnValue", html)

    def test_tabs_still_render_and_switch(self):
        html = _html()
        self.assertIn("function activate(", html)
        self.assertEqual(html.count('class="tab'), 3)


if __name__ == "__main__":
    unittest.main()
