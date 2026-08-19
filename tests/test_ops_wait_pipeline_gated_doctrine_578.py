"""#578 part 3 — the PIPELINE-GATED / UMBRELLA = W doctrine, and its explicit
distinction from #539 chained-I, on BOTH governance surfaces.

The gk `I 16` inflation was mostly a LABELLING gap the doctrine did not name: a
release-gated tail of the box's OWN pipeline (#4459 — "waiting 2.180 stage-3")
and an umbrella/tracking ticket gated on another ticket (#4520) both sat in bare
`I` instead of `W`, because #526/#539 only named the ACCEPTANCE side-branches.
#578 adds the two missing shapes and pins the boundary with #539 chained-I
(acceptance draft waiting on a SIBLING worker — that one STAYS I).

Content-lock (the #498/#500 shape): the STATUS W bullet is ONE physical line
(the operative tokens are asserted on that line); the autopilot SKILL W paragraph
is a wrapped bullet (asserted in a norm()'d window from the finder to the next
`- **`). Tokens are UNIQUE to the #578 clause so a partial revert has teeth.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATUS = REPO / "modules" / "core" / "statusline-vocabulary.md"
SKILL = REPO / "skills" / "autopilot" / "SKILL.md"

FINDER = "Pipeline-gated tail + umbrella = W (#578)"
# Operative tokens UNIQUE to the #578 clause (never elsewhere on the surface, so
# a partial revert of the clause drops them — the #498 begs-the-question guard).
TOKENS = (
    "release-gated tail",
    "umbrella/tracking ticket gated na INÝ ticket",
    "blocker ticket",
    "acceptance DRAFT",
    "sibling",
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


class PipelineGatedDoctrineLock(unittest.TestCase):
    def test_status_W_bullet_carries_578_clause(self):
        text = STATUS.read_text(encoding="utf-8")
        line = _line_with(text, FINDER)
        self.assertTrue(line, "the STATUS W bullet must carry the #578 clause")
        for tok in TOKENS:
            self.assertTrue(tok in line,
                            "STATUS W bullet lost the #578 token %r" % tok)

    def test_autopilot_skill_carries_578_clause(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(FINDER in text,
                        "the autopilot SKILL must carry the #578 clause")
        win = _norm_window(text, FINDER)
        for tok in TOKENS:
            self.assertTrue(tok in win,
                            "autopilot SKILL lost the #578 token %r" % tok)

    def test_distinguishes_from_chained_i_on_both_surfaces(self):
        # the boundary with #539 chained-I is stated on BOTH surfaces (a bare
        # needs-acceptance whose DRAFT waits on a sibling STAYS I; a release/
        # blocker-gated ticket goes to W).
        for path in (STATUS, SKILL):
            text = path.read_text(encoding="utf-8")
            self.assertTrue("chained-I" in text and "OSTÁVA" in text,
                            "%s must distinguish #578 W from #539 chained-I"
                            % path.name)


if __name__ == "__main__":
    unittest.main()
