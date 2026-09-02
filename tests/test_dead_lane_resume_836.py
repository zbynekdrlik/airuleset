"""#836 — a fresh `isolation: "worktree"` resume worker CANNOT reach a dead
worker's worktree (Claude Code's launch pin refuses any cwd outside the freshly
pinned worktree — `ISOLATION MISMATCH`), so the naive "name the branch, dispatch
a fresh isolation:worktree worker and cd into the dead worktree" is impossible.

Two shapes DO work, proven live 2026-09-02, and both docs must name them WITH
the harness-guard reason:
  1. CLEAN dead lane  → a fresh isolation:worktree worker `git merge --no-ff`s
     the dead branch onto its own branch (never `cd` into the dead worktree);
  2. UNCOMMITTED work → a NO-isolation worker whose FIRST command is
     `cd <dead worktree path>`, then the #817 self-check IN that directory.

The autopilot-worker.md #817 self-check ("FIRST STEP, UNCONDITIONAL") must
additionally carve out shape 2 so a legitimate no-isolation resume does not
return `ISOLATION FAILED` from its momentary main-checkout starting cwd.

This locks BOTH doc surfaces. Not a tautology: the tokens asserted are the
LOAD-BEARING commands/reasons a partial revert of either paragraph would drop.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / "skills" / "autopilot" / "SKILL.md"
WORKER_MD = ROOT / "agents" / "autopilot-worker.md"


def _norm(text):
    """Collapse markdown line-wrapping so a phrase spanning a physical wrap
    still matches (the established #500 doc-lock technique)."""
    return " ".join(text.split())


def _window(text, start_anchor, end_marker):
    """The normalized slice from `start_anchor` to the NEXT `end_marker`
    (or end-of-file — the #728 last-bullet fix). `start_anchor` MUST be unique
    to the operative paragraph, never a nearby why-line."""
    raw = text
    i = raw.index(start_anchor)
    j = raw.find(end_marker, i + len(start_anchor))
    if j == -1:
        j = len(raw)
    return _norm(raw[i:j])


class TestSkillNamesBothResumeShapes(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        # bound to the #836 paragraph: from its unique header to the next
        # top-level blockquote paragraph (`> **`).
        self.win = _window(
            self.text, "Two RESUME SHAPES that actually reach a dead lane",
            "\n\n   > **")

    def test_ticket_and_guard_reason_present(self):
        for tok in ("#836", "ISOLATION MISMATCH", "LAUNCH PIN"):
            self.assertIn(tok, self.win,
                          "SKILL.md #836 paragraph must name %r" % tok)

    def test_shape1_ff_merge_takeover(self):
        # CLEAN dead lane → fresh isolation:worktree worker merges the dead
        # branch onto its own (never cd into the dead worktree).
        for tok in ("git merge --no-ff", "batch version",
                    "NEVER `cd`/`git -C` into the dead worktree"):
            self.assertIn(tok, self.win,
                          "SKILL.md must document resume shape 1: %r" % tok)

    def test_shape2_no_isolation_cd(self):
        # UNCOMMITTED work → no-isolation worker cd's into the dead worktree.
        for tok in ("WITHOUT `isolation:`", "cd <dead worktree path>",
                    "#817"):
            self.assertIn(tok, self.win,
                          "SKILL.md must document resume shape 2: %r" % tok)


class TestWorkerDocNamesBothResumeShapes(unittest.TestCase):
    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")
        self.win = _window(
            self.text, "RESUMING a DEAD lane", "\n- **")

    def test_ticket_and_guard_reason_present(self):
        for tok in ("#836", "ISOLATION MISMATCH", "launch pin"):
            self.assertIn(tok, self.win,
                          "autopilot-worker.md #836 bullet must name %r" % tok)

    def test_shape1_ff_merge_takeover(self):
        for tok in ("git merge --no-ff", "batch version",
                    "NEVER `cd`/`git -C`"):
            self.assertIn(tok, self.win,
                          "worker doc must document resume shape 1: %r" % tok)

    def test_shape2_no_isolation_cd(self):
        # _norm collapses the line-wrap, so the phrase matches whether or not
        # it wrapped in the raw file.
        for tok in ("WITHOUT `isolation:`", "cd <dead worktree path>"):
            self.assertIn(tok, self.win,
                          "worker doc must document resume shape 2: %r" % tok)


class TestSelfCheckCarvesOutShape2(unittest.TestCase):
    """The #817 isolation self-check ("FIRST STEP, UNCONDITIONAL") must carve
    out shape 2 — a no-isolation resume whose first command is `cd` into an
    existing worktree runs the self-check AFTER the cd, so its momentary
    main-checkout starting cwd is NOT an isolation failure."""

    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")
        self.win = _window(
            self.text, "FIRST STEP, UNCONDITIONAL", "\n- **")

    def test_exception_names_shape2_and_does_not_abort(self):
        for tok in ("EXCEPTION", "NO-isolation RESUME dispatch", "#836",
                    "do NOT return `ISOLATION FAILED`"):
            self.assertIn(tok, self.win,
                          "self-check must carve out shape 2: %r" % tok)

    def test_carveout_still_aborts_the_default_dispatch(self):
        # the carve-out must NOT weaken the default: a plain isolation worker
        # on a main checkout still aborts.
        self.assertIn("Every OTHER dispatch", self.win)
        self.assertIn("still aborts on a main", self.win)


if __name__ == "__main__":
    unittest.main()
