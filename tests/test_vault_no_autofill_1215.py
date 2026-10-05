"""#1215: a drop secret-request page never lets the browser fill a SAVED
password into a new request (owner 5.10.2026: "hned sa tam nieco pastne").

Chrome ignores `autocomplete=off` on a password field and autofills the value
it saved for `drop-<box>.newlevel.media`; the page focuses the field, so the
old value shows up at once and one click would store it as the new secret.
`autocomplete="new-password"` (honoured by browsers) plus the password-manager
opt-outs stop that. The multi-field page builds its inputs in JS, so its
attributes are checked in the script text.
"""
import ast
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "filedrop" / "vault_server.py"


def _constant(name):
    """A module-level string constant of vault_server.py, read with ast: the
    server parses its argv at import time, so it cannot be imported."""
    for node in ast.parse(SRC.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("%s not found" % name)


class vs:  # noqa: N801 — the two page templates under test
    PAGE = _constant("PAGE")
    MULTI_PAGE = _constant("MULTI_PAGE")

OPT_OUTS = ("data-lpignore", "data-1p-ignore", "data-bwignore")


class TestSinglePage(unittest.TestCase):

    def test_the_password_field_asks_for_a_new_password(self):
        tag = re.search(r"<input id=v [^>]*>", vs.PAGE).group(0)
        self.assertIn('autocomplete="new-password"', tag)
        self.assertNotIn("autocomplete=off", tag)
        for attr in OPT_OUTS:
            self.assertIn(attr, tag)

    def test_there_is_no_form_to_save(self):
        self.assertNotIn("<form", vs.PAGE)


class TestMultiPage(unittest.TestCase):

    def test_each_generated_field_asks_for_a_new_password(self):
        self.assertIn("inp.autocomplete='new-password'", vs.MULTI_PAGE)
        self.assertNotIn("inp.autocomplete='off'", vs.MULTI_PAGE)
        for attr in OPT_OUTS:
            self.assertIn("setAttribute('%s'" % attr, vs.MULTI_PAGE)

    def test_there_is_no_form_to_save(self):
        self.assertNotIn("<form", vs.MULTI_PAGE)


if __name__ == "__main__":
    unittest.main()
