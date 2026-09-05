"""#570 bod 3 — the "W = tlač dopredu KAŽDÝ deň" doctrine content-lock.

The W bullet (`modules/core/statusline-vocabulary.md`) + the autopilot SKILL W
paragraph must carry the #570 stale-freshness mechanism: W is push-forward-every-
day (a ≥1×/day third-party reminder + blocker re-verification by RE-READING the
referenced ticket, recorded as a ticket comment which IS the `stale!` evidence).

Finder + co-tokens are UNIQUE to the #570 sentence (never a token #547/#552 also
assert — `job 20`/`--ops-wait`/`stuck-check:`/`partition-audit`/`re-audit`/
`OPPOSITE direction` — so a partial revert dropping ONLY the #570 sentence
genuinely fails the lock, the #498 begs-the-question guard)."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STATUS = REPO / "modules" / "core" / "statusline-vocabulary.md"
# #859 batch 3: the deep W-bullet state-machine detail (incl. the #570
# stale-freshness mechanism this class locks) moved to this companion.
STATUS_DEEP2 = REPO / "skills" / "statusline-vocabulary-deep" / "DEEP-2.md"
SKILL = REPO / "skills" / "autopilot" / "SKILL.md"

FINDER = "stale! freshness tag is MECHANICAL (#570)"
# co-tokens UNIQUE to the #570 addition (verified absent elsewhere in the W
# bullet): the tag, the doctrine phrase, the freshness threshold.
TOKENS = ("stale!", "tlač dopredu", "24h")


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
    def test_statusline_W_bullet_carries_570_mechanism(self):
        text = STATUS_DEEP2.read_text(encoding="utf-8")  # #859 batch 3: moved to companion
        line = _line_with(text, FINDER)
        self.assertTrue(line, "the W bullet must carry the #570 stale! sentence")
        for tok in TOKENS:
            self.assertIn(tok, line,
                          "W bullet lost the #570 operative token %r" % tok)

    def test_autopilot_skill_carries_570_mechanism(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn(FINDER, text,
                      "autopilot SKILL must carry the #570 stale! sentence")
        win = _norm_window(text, FINDER)
        for tok in TOKENS:
            self.assertIn(tok, win,
                          "autopilot SKILL lost the #570 token %r" % tok)

    def test_owner_verbatim_quote_present_in_status(self):
        text = STATUS_DEEP2.read_text(encoding="utf-8")  # #859 batch 3: moved to companion
        self.assertIn("15 veci ktore mas pushovat dopredu", text,
                      "the owner's verbatim W quote must anchor the doctrine")


if __name__ == "__main__":
    unittest.main()
