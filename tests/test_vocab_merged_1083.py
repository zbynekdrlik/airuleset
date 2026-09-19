"""#1083 — the statusline-vocabulary module documents the `M` segment: the
canonical segment sentence becomes `I · M · U · W · gk · skip` (6 segments), an
`M` bullet is present, and the retired `Q N`/`A N` bullet has MOVED to the
history file (so the always-on module does not grow — the context-baseline
ceiling is enforced separately by `airuleset.py context-baseline --check`).

RED-first: the base module says "5 segments" and has no M bullet.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MODULE = REPO / "modules" / "core" / "statusline-vocabulary.md"
HISTORY = REPO / ".claude" / "rules-reference" / "statusline-vocabulary-history.md"


class VocabDocumentsMSegment(unittest.TestCase):
    def setUp(self):
        self.text = MODULE.read_text(encoding="utf-8")

    def test_canonical_sentence_lists_six_segments_with_M(self):
        self.assertIn("6 segments", self.text)
        self.assertNotIn("5 segments", self.text)
        self.assertIn("`I · M · U · W · gk · skip`", self.text)
        self.assertNotIn("`I · U · W · gk · skip`", self.text)

    def test_m_bullet_present(self):
        self.assertIn("`· M N`", self.text)

    def test_qa_bullet_moved_to_history(self):
        # The retired Q N / A N bullet leaves the always-on module (byte budget)
        # and lands in the on-demand history file.
        self.assertNotIn("`Q N` and `A N` no longer exist as segments",
                         self.text)
        self.assertIn("`Q N` and `A N` no longer exist as segments",
                      HISTORY.read_text(encoding="utf-8"))


class VocabByteBudget(unittest.TestCase):
    def test_module_did_not_grow(self):
        # The whole point of the word-swap: the always-on module must not grow
        # (it was 9962 B before #1083; the M content is offset by moving the Q/A
        # bullet out). The authoritative ceiling is context-baseline --check.
        self.assertLessEqual(len(MODULE.read_text(encoding="utf-8").encode()),
                             9962)


if __name__ == "__main__":
    unittest.main()
