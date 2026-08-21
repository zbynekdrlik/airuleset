"""#607 časť 2 — víkendovo-vedomé 24h okno (Europe/Bratislava).

`working_time.working_seconds_between(start, end, tz)` počíta uplynulé sekundy
EXCLUDING sobotu + nedeľu v Europe/Bratislava (víkend sa do pracovno-dňového
deadline nepočíta), a `working_deadline_passed` je predikát ktorý časť 2
(`cli_quals._stale_ops_wait_flagged`) aj časť 3 (gk-lane) zdieľajú.

Locknuté:
  1. pure helper — mid-week 24h, Fri→Mon boundary, Fri→Sun (víkend preskočený),
     víkend-only span = 0, negatívny span = 0, DST víkend transition, tz fail-safe;
  2. `_stale_ops_wait_flagged` je víkendovo-vedomé — piatok-park sa v nedeľu
     NEflagne (len ~9 pracovných hodín), ale v pondelok popoludní áno.
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_quals
import working_time

TZ = ZoneInfo("Europe/Bratislava")
H = 3600
DAY = 24 * H


def ts(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


class WorkingSecondsBetween(unittest.TestCase):
    """Pure `working_seconds_between` — weekend-excluded elapsed seconds."""

    def test_midweek_24h_counts_full(self):
        # Tue 2026-08-18 10:00 -> Wed 2026-08-19 10:00 = 24h, no weekend
        got = working_time.working_seconds_between(ts(2026, 8, 18, 10),
                                                   ts(2026, 8, 19, 10))
        self.assertAlmostEqual(got, 24 * H, delta=1)

    def test_friday_to_monday_is_24_working_hours(self):
        # Fri 15:00 -> Mon 15:00: Fri15->Sat00 = 9h, Sat/Sun excluded,
        # Mon00->Mon15 = 15h -> exactly 24 working hours
        got = working_time.working_seconds_between(ts(2026, 8, 21, 15),
                                                   ts(2026, 8, 24, 15))
        self.assertAlmostEqual(got, 24 * H, delta=1)

    def test_friday_to_sunday_only_counts_friday_evening(self):
        # Fri 15:00 -> Sun 15:00: only Fri15->Sat00 = 9h counts (weekend skipped)
        got = working_time.working_seconds_between(ts(2026, 8, 21, 15),
                                                   ts(2026, 8, 23, 15))
        self.assertAlmostEqual(got, 9 * H, delta=1)

    def test_weekend_only_span_is_zero(self):
        # Sat 10:00 -> Sun 20:00 entirely inside the weekend
        got = working_time.working_seconds_between(ts(2026, 8, 22, 10),
                                                   ts(2026, 8, 23, 20))
        self.assertAlmostEqual(got, 0, delta=1)

    def test_negative_span_is_zero(self):
        self.assertEqual(
            working_time.working_seconds_between(ts(2026, 8, 20, 10),
                                                 ts(2026, 8, 20, 8)), 0)

    def test_saturday_start_into_tuesday(self):
        # Sat 10:00 -> Tue 10:00: Sat/Sun excl; Mon full 24h + Tue00->10 = 34h
        got = working_time.working_seconds_between(ts(2026, 8, 22, 10),
                                                   ts(2026, 8, 25, 10))
        self.assertAlmostEqual(got, 34 * H, delta=1)

    def test_dst_spring_weekend_excluded_does_not_leak(self):
        # Fri 2026-03-27 12:00 -> Mon 2026-03-30 12:00; the DST spring-forward is
        # Sun 2026-03-29 (excluded weekend), so it never shortens the count:
        # Fri12->Sat00 = 12h + Mon00->Mon12 = 12h = 24 working hours.
        got = working_time.working_seconds_between(ts(2026, 3, 27, 12),
                                                   ts(2026, 3, 30, 12))
        self.assertAlmostEqual(got, 24 * H, delta=1)

    def test_full_workweek_span(self):
        # Mon 08-17 00:00 -> Fri 08-21 00:00 = 4 full weekdays
        got = working_time.working_seconds_between(ts(2026, 8, 17),
                                                   ts(2026, 8, 21))
        self.assertAlmostEqual(got, 4 * DAY, delta=1)

    def test_tz_error_falls_back_to_full_span(self):
        # An unresolvable tz never SUPPRESSES detection: it reverts to the flat
        # pre-#607 full span (never MORE lenient than the shipped baseline).
        got = working_time.working_seconds_between(ts(2026, 8, 21, 15),
                                                   ts(2026, 8, 23, 15),
                                                   tz="Not/AZone")
        self.assertAlmostEqual(got, 48 * H, delta=1)


class WorkingDeadlinePassed(unittest.TestCase):
    """`working_deadline_passed(start, now, window)` — strictly-greater gate."""

    def test_friday_park_not_passed_on_sunday(self):
        self.assertFalse(working_time.working_deadline_passed(
            ts(2026, 8, 21, 15), ts(2026, 8, 23, 15), DAY))

    def test_friday_park_passed_by_monday_afternoon(self):
        # Mon 16:00 = 25 working hours > 24h window
        self.assertTrue(working_time.working_deadline_passed(
            ts(2026, 8, 21, 15), ts(2026, 8, 24, 16), DAY))

    def test_midweek_23h_not_passed(self):
        self.assertFalse(working_time.working_deadline_passed(
            ts(2026, 8, 18, 10), ts(2026, 8, 19, 9), DAY))


class StaleWeekendAware(unittest.TestCase):
    """`_stale_ops_wait_flagged` uses the weekend-aware window (#607)."""

    def _run(self, own_ts, now):
        return cli_quals._stale_ops_wait_flagged(
            {41: {"number": 41, "labels": [{"name": "ops-wait"}]}},
            now=now, self_login="me", ages_fn=lambda n: (own_ts, own_ts))

    def test_friday_park_not_flagged_on_sunday(self):
        # 48h wall but only 9 working hours -> must NOT be flagged (pre-#607 bug)
        self.assertEqual(self._run(ts(2026, 8, 21, 15), ts(2026, 8, 23, 15)),
                         set())

    def test_friday_park_flagged_by_monday_afternoon(self):
        # 25 working hours by Mon 16:00 -> flagged
        self.assertEqual(self._run(ts(2026, 8, 21, 15), ts(2026, 8, 24, 16)),
                         {41})

    def test_midweek_25h_flagged(self):
        self.assertEqual(self._run(ts(2026, 8, 18, 10), ts(2026, 8, 19, 11)),
                         {41})


if __name__ == "__main__":
    unittest.main()
