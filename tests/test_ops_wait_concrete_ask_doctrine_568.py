"""#568 — the "daily W reminder must be a SUBSTANTIVE, CONCRETE ask, never a
content-free ack" doctrine content-lock.

#570 shipped the W = tlač-dopredu-KAŽDÝ-deň doctrine + the mechanical `stale!`
freshness tag, but `stale!` resets its 24h clock on ANY stream-authored
comment — so a content-free "still working on it" ack silences the tag while
the third party stays genuinely un-nudged (the "vobec si tých ľudí
nepovzbudil" failure in tag-compliant disguise). #568 closes that gaming
surface with a non-mechanizable doctrine clause: the mandated daily reminder
must be a substantive concrete ask that MOVES the ticket.

The W bullet (`modules/core/statusline-vocabulary.md`) is ONE physical line, so
its teeth are per-line (`_line_with` — every operative token on the finder's
line). The autopilot SKILL W paragraph (`skills/autopilot/SKILL.md`) is a
WRAPPED bullet, so its teeth are a norm()'d WINDOW bounded to the bullet
(#500). The finder is UNIQUE to the #568 clause (never a #570 token), so a
partial revert dropping ONLY the #568 clause genuinely fails the lock (#498
begs-the-question guard)."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STATUS = REPO / "modules" / "core" / "statusline-vocabulary.md"
SKILL = REPO / "skills" / "autopilot" / "SKILL.md"

# UNIQUE to the #568 clause (verified absent elsewhere): the operative
# sentence lead-in.
FINDER = "A daily reminder must be a SUBSTANTIVE, CONCRETE ask"
# co-tokens carried by the SAME operative clause: the ack ban (with the #568
# provenance), the move-the-ticket requirement, the tail.
TOKENS = (
    "content-free ack (#568)",
    "MOVES the ticket",
    "a real povzbudenie, not status noise",
)


def _line_with(text, finder):
    for ln in text.splitlines():
        if finder in ln:
            return ln
    return ""


def _norm_window(text, start_token, end_marker="\n- **"):
    i = text.index(start_token)
    j = text.find(end_marker, i)
    return " ".join(text[i:(j if j > 0 else len(text))].split())


class ConcreteAskDoctrineContentLock(unittest.TestCase):
    def test_statusline_W_bullet_carries_568_clause(self):
        text = STATUS.read_text(encoding="utf-8")
        line = _line_with(text, FINDER)
        self.assertTrue(line, "the W bullet must carry the #568 concrete-ask clause")
        for tok in TOKENS:
            self.assertIn(tok, line,
                          "W bullet lost the #568 operative token %r" % tok)

    def test_autopilot_skill_carries_568_clause(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(FINDER in text,
                        "autopilot SKILL must carry the #568 concrete-ask clause")
        win = _norm_window(text, FINDER)
        for tok in TOKENS:
            self.assertIn(tok, win,
                          "autopilot SKILL lost the #568 token %r" % tok)

    def test_568_clause_lives_inside_the_570_W_push_block(self):
        # provenance guard: the #568 clause must sit next to the #570
        # stale!/tlač-dopredu mechanism it guards, not float off elsewhere.
        for path in (STATUS, SKILL):
            text = path.read_text(encoding="utf-8")
            self.assertIn("stale! freshness tag is MECHANICAL (#570)", text,
                          "%s lost the #570 anchor the #568 clause guards" % path.name)


if __name__ == "__main__":
    unittest.main()
