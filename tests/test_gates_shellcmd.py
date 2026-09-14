"""#1020 -- gates.shellcmd is the ONE quote-aware strip/split for the gate
family, replacing four hand-written copies. This file locks it in isolation
(pure/offline, no subprocess) AND proves byte-for-byte equivalence with the
pre-rework oracle (design_gate._strip_quoted) so the extraction is faithful.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import design_gate as dg                                   # noqa: E402
from gates import shellcmd                                 # noqa: E402


# The corpus deliberately spans the shapes each original copy cared about:
# a bare command, a git-merge inside a quoted -m message (design_gate's F2),
# nested/mixed quotes, an unterminated quote, a bypass marker inside a message
# (block-sensitive-staging's bypass parse), and empty/None input.
_STRIP_CORPUS = [
    "",
    "git commit -m 'Merge branch develop'",
    'git commit -m "Merge branch \'develop\'"',
    "echo 'a && git merge b' && git commit -m \"x\"",
    "git merge origin/develop",
    'run --flag "value with ; and | inside" more',
    "mix 'single' and \"double\" spans",
    "unterminated 'quote here",
    'unterminated "quote here',
    "# airuleset:secret-ok reason after a 'quoted ok' span",
    "no quotes at all",
]


class TestStripQuoted(unittest.TestCase):
    def test_matches_design_gate_oracle(self):
        """shellcmd.strip_quoted is byte-for-byte design_gate._strip_quoted --
        the pre-rework oracle for the two copies it replaces."""
        for text in _STRIP_CORPUS:
            self.assertEqual(
                shellcmd.strip_quoted(text),
                dg._strip_quoted(text),
                msg="diverged on %r" % (text,),
            )

    def test_removes_single_then_double(self):
        self.assertEqual(shellcmd.strip_quoted("a 'bb' c \"dd\" e"), "a  c  e")

    def test_none_is_empty(self):
        self.assertEqual(shellcmd.strip_quoted(None), "")


class TestSplitTopLevel(unittest.TestCase):
    def test_plain_separators(self):
        self.assertEqual(
            shellcmd.split_top_level("a && b || c ; d & e | f"),
            ["a ", " b ", " c ", " d ", " e ", " f"],
        )

    def test_quote_aware_semicolon_in_title(self):
        """A ; inside a quoted argument is NOT a separator (the whole reason
        filing needs a quote-aware split, not a regex)."""
        self.assertEqual(
            shellcmd.split_top_level("gh issue create --title 'a; b | c'"),
            ["gh issue create --title 'a; b | c'"],
        )

    def test_double_quote_span(self):
        self.assertEqual(
            shellcmd.split_top_level('cmd --body "x && y" && next'),
            ['cmd --body "x && y" ', " next"],
        )

    def test_backslash_escapes_next_char(self):
        self.assertEqual(
            shellcmd.split_top_level(r"a \&\& still-one-seg"),
            [r"a \&\& still-one-seg"],
        )

    def test_newline_is_a_separator(self):
        self.assertEqual(
            shellcmd.split_top_level("line1\nline2"),
            ["line1", "line2"],
        )

    def test_trailing_separator_yields_trailing_empty(self):
        # The original keeps a trailing empty segment after a terminal ';'.
        self.assertEqual(shellcmd.split_top_level("a ;"), ["a ", ""])

    def test_empty_and_none(self):
        self.assertEqual(shellcmd.split_top_level(""), [""])
        self.assertEqual(shellcmd.split_top_level(None), [""])


if __name__ == "__main__":
    unittest.main()
