"""#432: the SDK's "session-specific scratchpad" directory is keyed off the
DISPATCHING session's own top-level conversation id -- every sibling worker
that same session dispatches in one fleet round (#317) inherits the
IDENTICAL path verbatim, even though each worker runs in its own isolated
git worktree. `agents/autopilot-worker.md`'s WORKTREE AWARENESS section
already teaches several worktree-specific gotchas (never touch the shared
tree, never push/merge yourself, return the branch+worktree path) but said
nothing about the scratchpad -- so two sibling workers both reaching for a
common conventional scratch filename (`gh-cli-recipes.md` itself
recommends names like `red-commit-msg.txt`/`body.md`) can silently clobber
each other (live incident: presenter #683, one worker's commit shipped
under a sibling's unrelated message text).

Locks that the standing instruction exists, lives in the section every
worker actually reads (WORKTREE AWARENESS), and says the load-bearing
things: the scratchpad is SHARED across the round's siblings, and the fix
is a per-worker-namespaced subdirectory created before writing any
transient/conventionally-named file.
"""
import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
AGENT_DOC = ROOT / "agents" / "autopilot-worker.md"


def _worktree_awareness_section(text):
    """The WORKTREE AWARENESS section's own body -- from its `## WORKTREE
    AWARENESS` heading up to (not including) the next `## ` heading."""
    m = re.search(r"(?m)^## WORKTREE AWARENESS\b.*?$", text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^## ", rest)
    return rest[:nxt.start()] if nxt else rest


class TestWorktreeAwarenessSectionExists(TestCase):
    def test_worktree_awareness_section_is_present(self):
        text = AGENT_DOC.read_text(encoding="utf-8")
        section = _worktree_awareness_section(text)
        self.assertTrue(section.strip(),
                         "agents/autopilot-worker.md must have a "
                         "## WORKTREE AWARENESS section")


class TestScratchpadNamespacingIsDocumented(TestCase):
    """#432 fix lock: the WORKTREE AWARENESS section must teach the
    scratchpad-sharing hazard and its fix, not leave it to a per-dispatch
    supervisor courtesy (the single point of failure #432 was filed over)."""

    def setUp(self):
        self.section = _worktree_awareness_section(
            AGENT_DOC.read_text(encoding="utf-8"))

    def test_mentions_scratchpad_is_shared_across_sibling_workers(self):
        self.assertRegex(
            self.section.lower(), r"scratchpad",
            "WORKTREE AWARENESS must mention the scratchpad directory at all")
        self.assertTrue(
            re.search(r"shared|share[sd]?", self.section, re.IGNORECASE),
            "must say the scratchpad is SHARED across sibling workers")
        self.assertTrue(
            re.search(r"sibling", self.section, re.IGNORECASE),
            "must name the actual hazard: sibling worktree workers")

    def test_instructs_a_per_worker_namespaced_subdirectory(self):
        self.assertTrue(
            re.search(r"subdirector(?:y|ies)|sub-?directory", self.section,
                      re.IGNORECASE),
            "must instruct creating a per-worker SUBDIRECTORY, not just "
            "warn about the hazard")
        self.assertTrue(
            re.search(r"unique|namespac", self.section, re.IGNORECASE),
            "must say the subdirectory needs to be uniquely identifying, "
            "not just any subdirectory")

    def test_cites_the_432_incident_or_ticket(self):
        self.assertIn("#432", self.section,
                       "must cite #432 so the reasoning traces back to the "
                       "live incident this guidance exists for")

    def test_lives_before_the_serial_fallback_bullet(self):
        # sanity: the new guidance must be INSIDE the worktree-mode bullet
        # list (before the "serial-fallback ... is UNCHANGED" bullet that
        # already closes the list), not appended after the section ends.
        fallback = self.section.find("serial-fallback")
        scratchpad = self.section.lower().find("scratchpad")
        self.assertGreater(scratchpad, -1)
        if fallback != -1:
            self.assertLess(scratchpad, fallback,
                             "scratchpad guidance must be one of the "
                             "WORKTREE AWARENESS bullets, not trail after "
                             "the serial-fallback bullet")


if __name__ == "__main__":
    main()
