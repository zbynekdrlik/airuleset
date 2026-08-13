"""Content lock for modules/core/gh-cli-recipes.md (#217).

GitHub's issue-linking parser auto-closes `#N` on a literal substring match
(`close(s|d)? #N`, `fix(es|ed)? #N`, `resolve(s|d)? #N`), with zero negation
awareness -- so a PR body written to EXPLAIN it does NOT close a ticket
("does NOT close #N") contains the exact trigger substring and closes it
anyway. This locks the module's warning + safe-phrasing guidance so a
future edit can't silently drop it.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "modules" / "core" / "gh-cli-recipes.md"


def read():
    return MODULE.read_text(encoding="utf-8")


class TestAutoCloseNegationWarningIsDocumented(TestCase):

    def test_warns_that_negation_does_not_protect_against_auto_close(self):
        t = read()
        self.assertIn("does NOT close #N", t)
        self.assertIn("negation awareness", t.lower())

    def test_names_the_concrete_trigger_substrings(self):
        t = read()
        for word in ("close", "closes", "closed", "fix", "fixes", "fixed",
                     "resolve", "resolves", "resolved"):
            self.assertIn(word, t)

    def test_gives_the_safe_phrasing_to_use_instead(self):
        t = read()
        self.assertIn("leaves #N open", t)
        self.assertIn("#N remains open", t)


class TestBareGhApiFileFlagCaseIsDocumented(TestCase):
    """#412: bare `gh api -f body=@file` silently posts the literal `@path`
    string, not the file's content — only uppercase `-F` reads the file.
    Locks the module's warning so a future edit can't silently drop it."""

    def test_warns_that_lowercase_f_is_always_literal(self):
        t = read()
        self.assertIn("-f", t)
        self.assertIn("--raw-field", t)
        self.assertIn("ALWAYS a literal string", t)

    def test_names_the_uppercase_f_magic_file_read(self):
        t = read()
        self.assertIn("-F", t)
        self.assertIn("--field", t)
        self.assertIn("magic `@filename` file-read", t)

    def test_gives_a_bad_and_good_example(self):
        t = read()
        self.assertIn("gh api repos/OWNER/REPO/issues/N/comments -f body=@/tmp/body.md", t)
        self.assertIn("gh api repos/OWNER/REPO/issues/N/comments -F body=@/tmp/body.md", t)


if __name__ == "__main__":
    main()
