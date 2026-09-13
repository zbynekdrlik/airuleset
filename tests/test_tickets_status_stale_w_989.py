"""#989: tickets-status --refresh: stale W count TypeError (set - tuple).

``_tacit_window_flagged`` returns ``(tacit_wait, tacit_close)`` — a TUPLE of
two sets.  The #986 review commit (55864130) added ``_stale - _tacit`` in
``cmd_tickets_status`` without unpacking it, producing:

    TypeError: unsupported operand type(s) for -: 'set' and 'tuple'

which the surrounding ``except Exception`` swallowed as "stale W count skipped".
The CLI path (``cli_quals_cmd.py``) unpacks correctly::

    tacit_wait, tacit_close = _tacit_window_flagged(...)
    tacit = tacit_wait | tacit_close
    stale = stale - tacit

These tests drive the exact functions with controlled data and assert the
``ops_wait_stale`` field is computed correctly for W=0, W>0 (none stale),
W>0 (with stale members).
"""
import sys
import pathlib
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# --------------------------------------------------------------------------- #
# Deterministic clocks (#607 working-time coupling -- fix-forward for #999).
#
# ``cli_quals._compute_net_stale_w`` / ``_stale_ops_wait_flagged`` measure
# staleness in WORKING time -- Saturday/Sunday in Europe/Bratislava do NOT count
# toward the 24h window (``working_time.working_deadline_passed``, #607).
# Anchoring ``now = time.time()`` on the REAL clock, with the stale member at
# ``now - 200_000`` s (~2.3 real days), made two tests below fail on weekends:
# the window is dominated by the excluded weekend, so the member falls UNDER the
# 24h working window and is not flagged. Passing a FIXED ``now`` through the
# existing ``now=`` seam removes the calendar coupling (the production code is
# correct; only these tests were calendar-coupled).
_TZ = ZoneInfo("Europe/Bratislava")

# MID-WEEK ``now``: the whole ``now - 200_000`` s span is weekdays (Mon->Wed),
# so the stale anchor is ~2.3 WORKING days old -> the member IS stale on every
# run (probed: working_seconds_between = 200_000 s > 86_400 s window).
_MIDWEEK_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=_TZ).timestamp()  # Wed 12:00

# WEEKEND ``now`` where BOTH Sat and Sun fall inside the ``now - 200_000`` s
# window, so working-time exclusion drops the SAME member BELOW the 24h window
# -> NOT stale (probed: working_seconds_between = 48_800 s < 86_400 s). Must be
# a SUNDAY, not a Saturday: for a Saturday ``now`` only ONE weekend day lies in
# the window, leaving >24h of weekday -> still stale. This LOCKS the working-
# time semantics against a future revert to wall-clock staleness.
_WEEKEND_NOW = datetime(2026, 9, 13, 18, 0, tzinfo=_TZ).timestamp()  # Sun 18:00


class TestStaleWTacitSubtraction(unittest.TestCase):
    """Reproduce #989: set - tuple TypeError in cmd_tickets_status."""

    def test_tacit_returns_tuple_of_two_sets(self):
        """_tacit_window_flagged always returns (set, set)."""
        from cli_quals import _tacit_window_flagged
        result = _tacit_window_flagged({})
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], set)
        self.assertIsInstance(result[1], set)

    def test_stale_returns_set(self):
        """_stale_ops_wait_flagged always returns a set."""
        from cli_quals import _stale_ops_wait_flagged
        result = _stale_ops_wait_flagged({})
        self.assertIsInstance(result, set)

    def test_raw_subtraction_raises_type_error(self):
        """set - tuple(set, set) is the exact #989 bug."""
        stale = {1, 2, 3}
        tacit_raw = ({2}, {3})
        with self.assertRaises(TypeError):
            _ = stale - tacit_raw

    def test_unpacked_subtraction_works(self):
        """Correct pattern: unpack, union, then subtract."""
        stale = {1, 2, 3}
        tacit_wait, tacit_close = {2}, {3}
        tacit = tacit_wait | tacit_close
        net = stale - tacit
        self.assertEqual(net, {1})

    def test_compute_net_stale_w_empty(self):
        """W=0 must produce ops_wait_stale=0, not crash -- the #989 repro."""
        import airuleset
        ops_wait = {}
        count = airuleset._compute_net_stale_w(ops_wait)
        self.assertEqual(count, 0)

    def test_compute_net_stale_w_with_stale_member(self):
        """W>0 with a stale member must count it."""
        import airuleset
        now = _MIDWEEK_NOW

        def ages_fn(n):
            if n == 10:
                # Stale: anchor well past the 24h working window.
                return {"own": now - 200_000, "any": now - 200_000,
                        "own_cited": now - 200_000, "own_oldest": now - 200_000,
                        "own_final_reminder": None, "own_target": None,
                        "own_target_event": None}
            # Fresh: anchor just now.
            return {"own": now, "any": now,
                    "own_cited": now, "own_oldest": now,
                    "own_final_reminder": None, "own_target": None,
                    "own_target_event": None}

        ops_wait = {10: {"labels": ["ops-wait"]}, 20: {"labels": ["ops-wait"]}}
        count = airuleset._compute_net_stale_w(
            ops_wait, ages_fn=ages_fn, now=now)
        self.assertEqual(count, 1)

    def test_compute_net_stale_w_none_stale(self):
        """W>0 with no stale members must produce 0."""
        import airuleset
        now = _MIDWEEK_NOW

        def ages_fn(n):
            return {"own": now, "any": now,
                    "own_cited": now, "own_oldest": now,
                    "own_final_reminder": None, "own_target": None,
                    "own_target_event": None}

        ops_wait = {10: {"labels": ["ops-wait"]}, 20: {"labels": ["ops-wait"]}}
        count = airuleset._compute_net_stale_w(
            ops_wait, ages_fn=ages_fn, now=now)
        self.assertEqual(count, 0)

    def test_stale_minus_tacit_end_to_end(self):
        """End-to-end: _stale minus unpacked _tacit with a controlled ages_fn."""
        from cli_quals import (
            _stale_ops_wait_flagged, _tacit_window_flagged)

        now = _MIDWEEK_NOW

        def ages_fn(n):
            if n == 10:
                return {"own": now - 200_000, "any": now - 200_000,
                        "own_cited": now - 200_000, "own_oldest": now - 200_000,
                        "own_final_reminder": None, "own_target": None,
                        "own_target_event": None}
            return {"own": now, "any": now,
                    "own_cited": now, "own_oldest": now,
                    "own_final_reminder": None, "own_target": None,
                    "own_target_event": None}

        rows = {10: {"labels": ["ops-wait"]}, 20: {"labels": ["ops-wait"]}}
        stale = _stale_ops_wait_flagged(rows, ages_fn=ages_fn, now=now)
        self.assertIn(10, stale)
        self.assertNotIn(20, stale)

        tacit_result = _tacit_window_flagged(rows, ages_fn=ages_fn, now=now)
        tacit_wait, tacit_close = tacit_result
        tacit = tacit_wait | tacit_close
        net_stale = stale - tacit
        self.assertEqual(net_stale, {10})

    def test_working_time_semantics_weekend_now_not_stale(self):
        """LOCK (#607): with a WEEKEND ``now`` where both Sat and Sun fall in
        the ``now - 200_000`` s window, working-time exclusion drops the SAME
        member BELOW the 24h window -> NOT stale. Guards against a future revert
        to wall-clock staleness; the mid-week tests above prove it IS stale."""
        import airuleset
        from cli_quals import _stale_ops_wait_flagged
        now = _WEEKEND_NOW

        def ages_fn(n):
            if n == 10:
                return {"own": now - 200_000, "any": now - 200_000,
                        "own_cited": now - 200_000, "own_oldest": now - 200_000,
                        "own_final_reminder": None, "own_target": None,
                        "own_target_event": None}
            return {"own": now, "any": now,
                    "own_cited": now, "own_oldest": now,
                    "own_final_reminder": None, "own_target": None,
                    "own_target_event": None}

        rows = {10: {"labels": ["ops-wait"]}, 20: {"labels": ["ops-wait"]}}
        self.assertNotIn(
            10, _stale_ops_wait_flagged(rows, ages_fn=ages_fn, now=now))
        self.assertEqual(
            airuleset._compute_net_stale_w(rows, ages_fn=ages_fn, now=now), 0)


if __name__ == "__main__":
    unittest.main()
