"""#607 časť 3 — gk-lane 24h freshness: durable ticket comment + gk session nudge.

A hand-off parked on the gatekeeper (`ready-for-review`/`needs-gatekeeper`) with
NO gk action for >24h WORKING days (weekend-aware, the shared `working_time`
helper) gets a durable "gk, vieš o tom?" ticket COMMENT (robust even with no gk
session running) + a gk-session nudge reusing the existing keystroke channel.
Distinct from #399's 6h owner-phone alarm (different threshold, channel, purpose).

Locknuté:
  1. `_stale_handoff_push` (handoff_alarm) — comment on a >24h-working stale
     hand-off, weekend-aware, fail-safe (unmeasurable → skip; comment failure →
     no dedup advance/retry), per-ticket ~daily dedup, dry_run mutates nothing;
  2. `GK_STALE_PUSH_S == 24h`; re-export identity;
  3. `_apply_stale_handoff_comment` (cross_stream) posts the durable comment;
  4. `_stale_handoff_session_nudge` reuses `_send_bare_nudge_verified` on an idle
     pane, skips a busy pane / no pane (comment is the durable record).
"""
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd
import watchdog.handoff_alarm as ha
import watchdog.cross_stream as cs

TZ = ZoneInfo("Europe/Bratislava")
H = 3600


def ts(y, mo, d, h=0):
    return datetime(y, mo, d, h, tzinfo=TZ).timestamp()


WED = ts(2026, 8, 19, 12)          # Wednesday noon — mid-week anchor
SUN_UPD = ts(2026, 8, 16, 12)      # the prior Sunday -> ~60 WORKING hrs before WED


class StaleHandoffPushDecider(unittest.TestCase):
    def _push(self, handoffs, g=None, now=WED, comment_fn=None, dry_run=False):
        g = {} if g is None else g
        calls = []

        def cf(num):
            calls.append(num)
            return True
        logs, pushed = wd._stale_handoff_push(
            "demo", "/root/demo", handoffs, g, now, comment_fn or cf,
            dry_run=dry_run, persist=lambda: None)
        return logs, pushed, calls, g

    def test_stale_handoff_gets_a_durable_comment(self):
        logs, pushed, calls, g = self._push({7: SUN_UPD})
        self.assertEqual(pushed, [7])
        self.assertEqual(calls, [7])
        self.assertIn("7", str(g.get("stale_push_seen")))

    def test_fresh_handoff_is_not_commented(self):
        logs, pushed, calls, g = self._push({7: WED - 12 * H})
        self.assertEqual(pushed, [])
        self.assertEqual(calls, [])

    def test_friday_park_not_commented_on_sunday(self):
        # Fri 15:00 -> Sun 15:00 = 9 WORKING hrs (weekend excluded) -> not stale
        logs, pushed, calls, g = self._push(
            {7: ts(2026, 8, 21, 15)}, now=ts(2026, 8, 23, 15))
        self.assertEqual(pushed, [])

    def test_comment_failure_does_not_advance_dedup(self):
        g = {}
        self._push({7: SUN_UPD}, g=g, comment_fn=lambda n: False)
        self.assertEqual(g.get("stale_push_seen", {}).get("demo", {}), {})
        # a subsequent SUCCESSFUL attempt still comments (dedup never advanced)
        _, pushed2, calls2, _ = self._push({7: SUN_UPD}, g=g)
        self.assertEqual(calls2, [7])

    def test_dedup_holds_within_reping_window(self):
        g = {}
        self._push({7: SUN_UPD}, g=g)                       # first comment
        _, pushed2, calls2, _ = self._push({7: SUN_UPD}, g=g, now=WED + 12 * H)
        self.assertEqual(pushed2, [], "re-comment must wait for the 24h stage")

    def test_dedup_repings_after_the_staged_window(self):
        g = {}
        self._push({7: SUN_UPD}, g=g)
        _, pushed2, _c, _ = self._push({7: SUN_UPD}, g=g, now=WED + 25 * H)
        self.assertEqual(pushed2, [7])

    def test_unmeasurable_updated_is_skipped(self):
        logs, pushed, calls, g = self._push({7: None, 8: "bad", 9: True})
        self.assertEqual(pushed, [])
        self.assertEqual(calls, [])

    def test_resolved_handoff_pruned_from_dedup(self):
        g = {}
        self._push({7: SUN_UPD}, g=g)                       # 7 tracked
        # next sweep: 7 no longer an open hand-off -> pruned from the dedup
        self._push({8: WED - 12 * H}, g=g)                  # 8 fresh, not stale
        self.assertNotIn("7", g.get("stale_push_seen", {}).get("demo", {}))

    def test_dry_run_posts_nothing_and_mutates_no_state(self):
        logs, pushed, calls, g = self._push({7: SUN_UPD}, dry_run=True)
        self.assertEqual(calls, [])
        self.assertEqual(pushed, [])
        self.assertNotIn("demo", g.get("stale_push_seen", {}))


class Constants(unittest.TestCase):
    def test_gk_stale_push_is_24_working_hours(self):
        self.assertEqual(wd.GK_STALE_PUSH_S, 24 * 3600)

    def test_distinct_from_the_399_owner_alarm_threshold(self):
        self.assertNotEqual(wd.GK_STALE_PUSH_S, wd.GKREQ_STALE_HANDOFF_S)

    def test_reexported_from_handoff_alarm(self):
        self.assertIs(wd._stale_handoff_push, ha._stale_handoff_push)


class ApplyComment(unittest.TestCase):
    def test_dry_run_returns_true_without_posting(self):
        with mock.patch("subprocess.run",
                        side_effect=AssertionError("must not post on dry_run")):
            self.assertTrue(
                cs._apply_stale_handoff_comment("/r", 7, "demo", dry_run=True))

    def test_posts_comment_and_returns_success(self):
        cp = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=cp) as m:
            self.assertTrue(cs._apply_stale_handoff_comment("/r", 7, "demo"))
        argv = m.call_args[0][0]
        self.assertEqual(argv[:3], ["gh", "issue", "comment"])
        self.assertIn("7", argv)

    def test_nonzero_rc_returns_false(self):
        cp = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with mock.patch("subprocess.run", return_value=cp):
            self.assertFalse(cs._apply_stale_handoff_comment("/r", 7, "demo"))


class SessionNudge(unittest.TestCase):
    def test_no_pid_no_nudge(self):
        logs = cs._stale_handoff_session_nudge(
            {}, None, "/r", [7], None, 0, "/proj", None, False)
        self.assertEqual(logs, [])

    def test_idle_pane_fires_a_verified_nudge(self):
        sent = []
        with mock.patch.object(wd, "capture_pane", lambda pid, run: "IDLE"), \
                mock.patch.object(wd, "pane_in_mode", lambda pid, run: False), \
                mock.patch.object(wd, "pane_at_idle_prompt", lambda cap: True), \
                mock.patch.object(cs, "_safe_to_bounce_nudge",
                                  lambda cap, root, pd: True), \
                mock.patch.object(cs, "_send_bare_nudge_verified",
                                  lambda *a, **k: sent.append(a) or True):
            logs = cs._stale_handoff_session_nudge(
                {}, 42, "/r", [7], None, 0, "/proj", None, False)
        self.assertTrue(sent)
        self.assertTrue(any("gkstale-nudge" in ln for ln in logs), logs)

    def test_busy_pane_skips_the_keystroke(self):
        with mock.patch.object(wd, "capture_pane", lambda pid, run: "BUSY"), \
                mock.patch.object(wd, "pane_in_mode", lambda pid, run: True), \
                mock.patch.object(cs, "_send_bare_nudge_verified",
                                  side_effect=AssertionError("must not type")):
            logs = cs._stale_handoff_session_nudge(
                {}, 42, "/r", [7], None, 0, "/proj", None, False)
        self.assertTrue(any("skip" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
