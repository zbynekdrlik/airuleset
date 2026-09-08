"""#924 — content-lock: the task-first-on-Discuss-read rule in handover-compose.md.

Owner directive (montalu 2026-09-07): every client Discuss report → Odoo
project.task IN THE SAME TURN it is read.  Streams must NOT reply or file a
GitHub ticket before the task exists.

These tests use the #500/#532 window-teeth pattern: an operative bullet is
bounded by its start anchor and the next ``- **`` marker, tokens are asserted
inside that window.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"

MAX_BODY = 24000  # inject-situational-rule.sh per-body cap


def _strip_frontmatter(text: str) -> str:
    m = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    return text[m.end():].strip() if m else text.strip()


def _norm(text: str) -> str:
    """Collapse whitespace for wrapped-bullet matching."""
    return " ".join(text.split())


def _window(text: str, anchor: str) -> str:
    """Extract the wrapped bullet starting at *anchor* up to next ``- **``."""
    normed = _norm(text)
    idx = normed.find(anchor)
    if idx < 0:
        return ""
    rest = normed[idx + len(anchor):]
    end = rest.find("- **")
    if end < 0:
        return normed[idx:]
    return normed[idx:idx + len(anchor) + end]


class TestTaskFirstDoctrinePresent(unittest.TestCase):
    """The task-first bullet exists with its operative tokens."""

    @classmethod
    def setUpClass(cls):
        cls.raw = COMPANION.read_text(encoding="utf-8")
        cls.body = _strip_frontmatter(cls.raw)

    # -- whole-file presence (catches a full deletion) --

    def test_bullet_anchor_present(self):
        self.assertIn("project.task IMMEDIATELY", self.body)

    def test_ticket_cited(self):
        self.assertIn("#924", self.body)

    # -- window-teeth (catches a partial revert of the operative line) --

    def test_before_reply_token(self):
        win = _window(self.body, "project.task IMMEDIATELY")
        self.assertTrue(win, "anchor not found")
        self.assertIn("Before reply", _norm(win))

    def test_before_github_ticket_token(self):
        win = _window(self.body, "project.task IMMEDIATELY")
        self.assertTrue(win, "anchor not found")
        self.assertIn("GitHub ticket", _norm(win))

    def test_origin_token(self):
        win = _window(self.body, "project.task IMMEDIATELY")
        self.assertTrue(win, "anchor not found")
        self.assertIn("origin", _norm(win))

    def test_stages_token(self):
        win = _window(self.body, "project.task IMMEDIATELY")
        self.assertTrue(win, "anchor not found")
        # #949 Y1: stages moved to client-board-tasks.md; the #924 bullet
        # now carries a pointer instead of the inline stage list.
        normed = _norm(win)
        self.assertIn("client-board-tasks.md", normed)

    def test_source_of_truth_negation(self):
        win = _window(self.body, "project.task IMMEDIATELY")
        self.assertTrue(win, "anchor not found")
        normed = _norm(win)
        self.assertIn("NEVER", normed)
        self.assertIn("source of truth", normed)

    def test_hotovo_authority(self):
        win = _window(self.body, "project.task IMMEDIATELY")
        self.assertTrue(win, "anchor not found")
        # #949 Y1: Hotovo authority pointed to client-board-tasks.md
        normed = _norm(win)
        self.assertIn("Hotovo", normed)
        self.assertIn("authority", normed)


class TestSizeCap(unittest.TestCase):
    """The companion body must stay within MAX_BODY to avoid truncation."""

    def test_body_within_max_body(self):
        raw = COMPANION.read_text(encoding="utf-8")
        body = _strip_frontmatter(raw)
        self.assertLessEqual(
            len(body), MAX_BODY,
            f"handover-compose.md body is {len(body)} chars, "
            f"MAX_BODY is {MAX_BODY} — truncation would occur",
        )


if __name__ == "__main__":
    unittest.main()
