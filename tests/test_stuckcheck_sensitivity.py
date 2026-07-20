"""Stuck-check sensitivity (user complaint 2026-07-20, codex-bridge live log):

A session honestly waiting on a SCHEDULED external event ('⏳ WORKING: čakám na
14:15 auto-sync — budík armovaný') got drilled by stuck-check nudges until it
was pressured into doing unnecessary work. Two defects:

1. No respect for a DECLARED future time in the ⏳ marker — the watchdog must
   not nudge before the declared clock time (+grace) passes.
2. An ANSWERED nudge reset idle but the next nudge came after the short 5-min
   retry interval, and 3 answered nudges ESCALATED a 'wedged' Discord ping —
   escalation is for NO-RESPONSE (a dead process), never for a session that
   answered every check. Answered nudges back off exponentially instead.
"""

import sys
import time
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd


class TestDeclaredWaitUntil(TestCase):
    def _at(self, hhmm):
        """Epoch for today's hh:mm in the watchdog tz (Europe/Bratislava)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        nowd = datetime.now(tz).replace(second=0, microsecond=0)
        h, m = map(int, hhmm.split(":"))
        return nowd.replace(hour=h, minute=m).timestamp()

    def test_future_time_suppresses_until_then_plus_grace(self):
        now = self._at("08:46")
        line = "⏳ WORKING: čakám na 14:15 auto-sync (živé potvrdenie #338) — budík armovaný"
        until = wd.declared_wait_until(line, now)
        self.assertAlmostEqual(until, self._at("14:15") + wd.DECLARED_WAIT_GRACE_S,
                               delta=61)

    def test_no_time_token_means_no_suppression(self):
        self.assertEqual(wd.declared_wait_until(
            "⏳ WORKING: worker beží, ozvem sa", self._at("08:46")), 0)

    def test_past_time_beyond_cap_is_ignored(self):
        # '08:00' at 08:46 → next occurrence is tomorrow (>12h cap) → no hold
        now = self._at("08:46")
        self.assertEqual(wd.declared_wait_until(
            "⏳ WORKING: sync bežal o 08:00, analyzujem", now), 0)

    def test_deploy_window_evening_time_holds(self):
        # 'okno 22:00' declared at 14:00 → hold until 22:00+grace
        now = self._at("14:00")
        until = wd.declared_wait_until(
            "⏳ WORKING: release pripravený, deploy okno 22:00 — čakám", now)
        self.assertAlmostEqual(until, self._at("22:00") + wd.DECLARED_WAIT_GRACE_S,
                               delta=61)


class TestAnsweredNudgeBackoff(TestCase):
    BASE = wd.STALL_WORKING_SECONDS          # 30 min

    def test_answered_nudge_backs_off_not_5min_retry(self):
        state, now = {}, time.time()
        a, e = wd.decide_working(state, "w", now, idle=self.BASE + 60)
        state["w"] = e
        self.assertEqual(a, "nudge")
        # session ANSWERED; 35 min later idle re-crossed the threshold —
        # the OLD code re-nudged after 5 min; now the gap must be >= 2×BASE
        later = now + 35 * 60
        a2, e2 = wd.decide_working(state, "w", later, idle=self.BASE + 60,
                                   responded=True)
        self.assertEqual(a2, "wait")

    def test_answered_nudge_fires_after_backoff_passes(self):
        state, now = {}, time.time()
        _, e = wd.decide_working(state, "w", now, idle=self.BASE + 60)
        state["w"] = e
        later = now + 2 * self.BASE + 120
        a2, e2 = wd.decide_working(state, "w", later, idle=self.BASE + 60,
                                   responded=True)
        self.assertEqual(a2, "nudge")

    def test_answered_nudges_never_escalate(self):
        state, now = {}, time.time()
        _, e = wd.decide_working(state, "w", now, idle=self.BASE + 60)
        state["w"] = e
        t = now
        for _i in range(6):                  # many answered rounds, huge gaps
            t += 9 * 3600
            a, e = wd.decide_working(state, "w", t, idle=self.BASE + 60,
                                     responded=True)
            state["w"] = e
            self.assertNotEqual(a, "escalate",
                                "answered nudges must NEVER escalate")

    def test_wedged_no_response_path_unchanged(self):
        state, now = {}, time.time()
        _, e = wd.decide_working(state, "w", now, idle=self.BASE + 60)
        state["w"] = e
        t = now
        actions = []
        for _i in range(4):
            t += wd.WORKING_RETRY_INTERVAL_SECONDS + 5
            a, e = wd.decide_working(state, "w", t, idle=self.BASE + 60,
                                     responded=False)
            state["w"] = e
            actions.append(a)
        self.assertIn("escalate", actions)   # dead process still escalates


if __name__ == "__main__":
    main()
