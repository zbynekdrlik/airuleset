"""Bundle B9 fix round — the #1257 owner-stop latch (`watchdog/owner_stop.py`)
after its two adversarial reviews (owner 20.10.2026: "nejaké tvoje procesy
spustili a dali pokračovať gk-quality/gk-infra aj keď som ich mal /exit … to
nesmieš!").

Pause vocabulary (decided by the main session): a prompt SETS the latch only
when, after lowercasing, stripping punctuation and removing politeness/time
fillers, what remains is EXACTLY one stop phrase. A prompt that merely
CONTAINS a stop word is NEUTRAL: it neither sets nor clears the latch.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_nudge_daily_owner_stop_1257 as base  # noqa: E402

T0, OWNER_WORK, FACTS, _u = base.T0, base.OWNER_WORK, base.FACTS, base._u
LONG_TASK = ("oprav prosím ten stop button na webe, lebo po kliknutí sa nič "
             "nestane a formulár zostane visieť na obrazovke")


def _latch(entries, **kw):
    return base._latch(entries, **kw)


class ExactStopPhrase(unittest.TestCase):
    NEUTRAL = ("prestaň sa pýtať a pokračuj", "skonči review a mergni PR",
               "oprav stop button na webe",
               "zastav deploy a oprav test, potom pokračuj",
               "nepokračuj s A, sprav radšej B",
               "pridaj pauzu 2 s medzi requestami", "stop asking, just do it",
               "why does the stop marker fail?", LONG_TASK)

    def test_an_exact_stop_phrase_latches(self):
        for text in ("pauza", "pauzni sa keď môžeš", "stop", "skonči na dnes",
                     "Stop!", "prosím, zastav sa", "STOJ", "koniec, zatiaľ",
                     "halt for now", "Please stop now.", "nepokracuj",
                     "pauzni sa ked mozes teraz"):
            latch = _latch(OWNER_WORK + [_u(text, T0 + 10)])
            self.assertEqual((latch or {}).get("fact"), "pause", text)

    def test_a_prompt_that_contains_a_stop_word_never_latches(self):
        for text in self.NEUTRAL:
            self.assertIsNone(_latch(OWNER_WORK + [_u(text, T0 + 10)]), text)

    def test_a_prompt_that_contains_a_stop_word_never_clears(self):
        for text in self.NEUTRAL:
            latch = _latch(OWNER_WORK + FACTS["pause"] + [_u(text, T0 + 60)])
            self.assertEqual((latch or {}).get("fact"), "pause", text)
            self.assertEqual(latch.get("ts"), base._os()._ts(FACTS["pause"][0]),
                             text)

    def test_any_other_owner_prompt_still_clears(self):
        for text in ("pokračuj s ticketom 5", "sprav review PR 12", "ahoj"):
            self.assertIsNone(_latch(OWNER_WORK + FACTS["pause"]
                                     + [_u(text, T0 + 60)]), text)


if __name__ == "__main__":
    unittest.main()
