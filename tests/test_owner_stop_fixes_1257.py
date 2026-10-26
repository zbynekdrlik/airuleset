"""Bundle B9 fix round — the #1257 owner-stop latch (`watchdog/owner_stop.py`)
after its two adversarial reviews (owner 20.10.2026: "nejaké tvoje procesy
spustili a dali pokračovať gk-quality/gk-infra aj keď som ich mal /exit … to
nesmieš!").

Pause vocabulary (decided by the main session): a prompt SETS the latch only
when, after lowercasing, stripping punctuation and removing politeness/time
fillers, what remains is EXACTLY one stop phrase. A prompt that merely
CONTAINS a stop word is NEUTRAL: it neither sets nor clears the latch.
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_nudge_daily_owner_stop_1257 as base  # noqa: E402

T0, OWNER_WORK, FACTS, _u = base.T0, base.OWNER_WORK, base.FACTS, base._u
NOW, DAY, _os, _sys = base.NOW, base.DAY, base._os, base._sys
GOAL_SET = "<local-command-stdout>Goal set: work the backlog</local-command-stdout>"
GOAL_CLEARED = ("<local-command-stdout>Goal cleared: work the backlog"
                "</local-command-stdout>")
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


class MachineClearSameSweep(base._Pane):
    """Review finding 3: the watchdog's own #522 disarm (`qrepoke` writes the
    `goal_disarmed_q` veto, then types `/goal clear`) can land AFTER `refresh`
    in the same sweep; the veto must be read live, not from refresh's copy."""

    def test_a_veto_written_after_refresh_still_reads_machine(self):
        self._append(*OWNER_WORK, _sys(GOAL_SET, T0 + 5))
        state = {}
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
        self.assertIsNone(_os().pane_latch(self.PID))
        clear_ts = time.time() - 30
        state["goal_disarmed_q"] = {self.SID: {"disarmed_ts": clear_ts - 3}}
        self._append(_sys(GOAL_CLEARED, clear_ts))
        later = time.time() - 20
        os.utime(self.tpath, (later, later))
        self.assertIsNone(_os().pane_latch(self.PID))


class MachineClearPersists(base._Pane):
    """Review finding 2: once `refresh` matched a `Goal cleared:` to the
    watchdog's own #522 disarm, that clear stays machine for the life of the
    session's latch record (7-day TTL), after `goal_disarmed_q` is reaped."""

    def test_the_own_disarm_stays_machine_after_its_veto_is_reaped(self):
        clear_ts = T0 + 3600
        self._append(*OWNER_WORK, _sys(GOAL_SET, T0 + 5),
                     _sys(GOAL_CLEARED, clear_ts),
                     _u("<task-notification>done</task-notification>",
                        clear_ts + 100))
        state = {"goal_disarmed_q": {self.SID: {"disarmed_ts": clear_ts - 10}}}
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
        self.assertIsNone(_os().pane_latch(self.PID))
        self.assertEqual(state[_os().STATE_KEY][self.SID]["mclears"], [clear_ts])
        state.pop("goal_disarmed_q")       # `_reap_qdisarm_state`, 24 h later
        logs = _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW + DAY)
        self.assertIsNone(_os().pane_latch(self.PID), logs)
        self.assertFalse(any("SET" in ln for ln in logs), logs)
        sent = []
        self.assertTrue(base.wd.keys(
            self.PID, "Enter", kind="continue", nudge="resume",
            run=lambda argv, timeout=8: sent.append(argv) or ""))
        self.assertEqual(len(sent), 1)

    def test_the_record_ages_out_with_the_ttl(self):
        self._append(*OWNER_WORK, _sys(GOAL_CLEARED, T0 + 60))
        state = {"goal_disarmed_q": {self.SID: {"disarmed_ts": T0 + 55}}}
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
        self.assertIn(self.SID, state[_os().STATE_KEY])
        _os().refresh(state, [], self.proj, NOW + _os().RECORD_TTL_S + 1)
        self.assertNotIn(_os().STATE_KEY, state)


if __name__ == "__main__":
    unittest.main()
