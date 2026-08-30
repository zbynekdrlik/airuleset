"""#753 — "a W-push resets stale! ONLY with a source citation" doctrine lock.

The W bullet (`modules/core/statusline-vocabulary.md`) + the autopilot SKILL W
paragraph must carry the #753 mechanism: the daily W-push resets `stale!` ONLY
when it CITES a source (version / Discuss thread or msg-id / `#N`), and must end
in a STATE CHANGE or a cited blocker re-verification — never a bare waiting
comment. Finder + co-tokens are UNIQUE to the #753 sentence (the helper name
`_comment_has_citation` and `STATE CHANGE` do not appear elsewhere in the W
bullet), so a partial revert dropping only the #753 sentence fails the lock
(the #498 begs-the-question guard)."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STATUS = REPO / "modules" / "core" / "statusline-vocabulary.md"
SKILL = REPO / "skills" / "autopilot" / "SKILL.md"

FINDER = "A W-push resets `stale!` ONLY with a source CITATION (#753)"
TOKENS = ("_comment_has_citation", "STATE CHANGE")


def _line_with(text, finder):
    for ln in text.splitlines():
        if finder in ln:
            return ln
    return ""


def _norm_window(text, start_token, end_marker="\n- **"):
    i = text.index(start_token)
    j = text.find(end_marker, i)
    return " ".join(text[i:(j if j > 0 else len(text))].split())


class DoctrineContentLock(unittest.TestCase):
    def test_statusline_W_bullet_carries_753_mechanism(self):
        text = STATUS.read_text(encoding="utf-8")
        line = _line_with(text, FINDER)
        self.assertTrue(line, "the W bullet must carry the #753 citation sentence")
        for tok in TOKENS:
            self.assertIn(tok, line,
                          "W bullet lost the #753 operative token %r" % tok)

    def test_autopilot_skill_carries_753_mechanism(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn(FINDER, text,
                      "autopilot SKILL must carry the #753 citation sentence")
        win = _norm_window(text, FINDER)
        for tok in TOKENS:
            self.assertIn(tok, win,
                          "autopilot SKILL lost the #753 token %r" % tok)


if __name__ == "__main__":
    unittest.main()
