"""Hook INTERNALS leave the always-on prose; the actionable rule stays (#92 item 4).

Item 4 asked for 8 hook-covered modules to be trimmed to one-line pointers.
The per-phrase verification the ticket itself mandates (the repo's #14 lesson —
hooks parse the assistant's OUTPUT, not module text) refuted that for 7 of the
8: the hooks cover a NARROWER slice than the modules teach, so trimming those
modules would silently delete enforcement that only exists as prose. The full
verdict table is on the ticket.

What the verification DID clear is a different, smaller shape: prose that
describes a hook's own INTERNALS. `script-failure-policy.md` spent ~900 B on
which ruff rule numbers fire, why `S110` is not enabled, how many pre-existing
`except ...: pass` sites this repo has, and which tool inputs the PreToolUse
hook scans. None of it changes what a session should DO — the hook behaves
identically whether or not the model read it — and it is airuleset-repo
specific, so it belongs on the path-scoped internals surface that loads when
`hooks/**` is touched.

The one genuinely actionable fact in that paragraph — the bypass marker a
blocked session needs — stays inline.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestScriptFailurePolicyKeepsOnlyTheActionableRule(TestCase):
    MODULE = "modules/quality/script-failure-policy.md"

    def test_the_four_language_rules_survive(self):
        # none of these are hook-covered end-to-end (PowerShell and CI steps
        # have no hook at all), so all four must stay in prose
        t = read(self.MODULE)
        for needle in ("set -euo pipefail", '$ErrorActionPreference = "Stop"',
                       "Never silently catch and ignore exceptions",
                       "exit non-zero on failure"):
            self.assertIn(needle, t, needle)

    def test_the_bypass_marker_stays_inline(self):
        # a session blocked mid-write needs this without loading anything
        self.assertIn("# airuleset:script-ok", read(self.MODULE))

    def test_hook_internals_are_gone_from_the_always_on_prose(self):
        t = read(self.MODULE)
        for machinery in ("pycodestyle E722", "S110", "24 pre-existing"):
            self.assertNotIn(machinery, t, machinery)

    def test_hook_internals_moved_verbatim_to_the_path_scoped_surface(self):
        internals = read(".claude/rules/internals-hooks.md")  # #482: pre-write-script-check moved here
        for machinery in ("pycodestyle E722", "S110", "24 pre-existing",
                          "pre-write-script-check.sh"):
            self.assertIn(machinery, internals, machinery)


if __name__ == "__main__":
    main()
