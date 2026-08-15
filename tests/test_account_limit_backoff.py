"""#502 — the lane-occupancy nudge must NOT dispatch into an account that just
died on a cap (session / weekly / usage / MONTHLY-SPEND / org-disabled), and its
text must not assert OPEN-ticket counts as if they were all dispatchable.

Two defects, two RED->GREEN pairs:
  (a) `goal_lane_occupancy_nudge` reads the supervisor's own transcript and backs
      off (bounded, self-releasing) while it shows an account-level dispatch block.
  (b) the nudge TEXT qualifies `backlog=N` as OPEN (not necessarily workable).
"""

import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    _encode,
    DeliverGoalFakeTmux,
    _write_marker_transcript,
)

WEEKLY = "You've hit your weekly limit · resets Aug 20, 1am (Europe/Bratislava)"
SESSION = "You've hit your session limit · resets 2am (Europe/Bratislava)"
MONTHLY = "You've hit your monthly spend limit · raise it at claude.ai/settings/usage"
ORG = "Your organization has disabled Claude subscription access for Claude Code"


class TestAccountLimitClassifier(unittest.TestCase):
    """`is_account_dispatch_block` catches ALL three incident shapes (including
    the two `is_usage_cap` misses), never a transient throttle."""

    def test_weekly_and_session_are_blocks(self):
        self.assertTrue(wd.is_account_dispatch_block(WEEKLY))
        self.assertTrue(wd.is_account_dispatch_block(SESSION))

    def test_monthly_spend_is_a_block_even_though_is_usage_cap_misses_it(self):
        self.assertFalse(wd.is_usage_cap(MONTHLY))          # documents the gap
        self.assertTrue(wd.is_account_dispatch_block(MONTHLY))

    def test_org_disabled_is_a_block_even_though_is_usage_cap_misses_it(self):
        self.assertFalse(wd.is_usage_cap(ORG))              # documents the gap
        self.assertTrue(wd.is_account_dispatch_block(ORG))

    def test_transient_throttle_is_never_a_block(self):
        for t in ("(not your usage limit) temporarily limiting requests",
                  "overloaded, please try again",
                  "rate limit exceeded",
                  "server error 529"):
            self.assertFalse(wd.is_account_dispatch_block(t), t)

    def test_empty_and_normal_text_are_not_blocks(self):
        self.assertFalse(wd.is_account_dispatch_block(""))
        self.assertFalse(wd.is_account_dispatch_block(None))
        self.assertFalse(wd.is_account_dispatch_block(
            "Done. Merged PR #7 and deployed v1.2.3."))


class TestAccountLimitReleaseAt(unittest.TestCase):
    """`_account_limit_release_at`: honour a near reset, cap a far/absent one."""

    def test_near_reset_is_honoured(self):
        f = 1_000_000
        self.assertEqual(goal._account_limit_release_at(f, f + 3600), f + 3600)

    def test_far_reset_is_capped(self):
        f = 1_000_000
        cap = f + goal.ACCOUNT_LIMIT_BACKOFF_MAX_S
        self.assertEqual(goal._account_limit_release_at(f, f + 5 * 86400), cap)

    def test_missing_reset_is_the_cap(self):
        f = 1_000_000
        self.assertEqual(goal._account_limit_release_at(f, None),
                         f + goal.ACCOUNT_LIMIT_BACKOFF_MAX_S)

    def test_past_reset_is_the_cap(self):
        f = 1_000_000
        self.assertEqual(goal._account_limit_release_at(f, f - 10),
                         f + goal.ACCOUNT_LIMIT_BACKOFF_MAX_S)


class TestAccountLimitDecisionHelper(unittest.TestCase):
    """`_account_limit_decision` in isolation: seed/skip, re-probe+re-arm, clear."""

    def test_block_within_window_backs_off_and_seeds(self):
        rec = {}
        back_off, log = goal._account_limit_decision(rec, 100000, WEEKLY, "loc", 0)
        self.assertTrue(back_off)
        self.assertIn("skip:account-limit", log)
        self.assertEqual(rec["alim"]["first_seen"], 100000)

    def test_not_a_block_clears_the_episode(self):
        rec = {"alim": {"first_seen": 1, "resets_at": None}}
        back_off, log = goal._account_limit_decision(rec, 100000, "", "loc", 0)
        self.assertFalse(back_off)
        self.assertIsNone(log)
        self.assertNotIn("alim", rec)

    def test_transient_throttle_is_not_a_block_and_clears(self):
        rec = {"alim": {"first_seen": 1, "resets_at": None}}
        back_off, _ = goal._account_limit_decision(
            rec, 100000, "overloaded, please try again", "loc", 0)
        self.assertFalse(back_off)
        self.assertNotIn("alim", rec)

    def test_elapsed_window_reprobes_once_and_rearms(self):
        now = 100000
        rec = {"alim": {"first_seen": now - 7 * 3600, "resets_at": None}}
        back_off, log = goal._account_limit_decision(rec, now, ORG, "loc", 0)
        self.assertFalse(back_off)                          # re-probe (do NOT skip)
        self.assertIn("back-off elapsed", log)
        self.assertEqual(rec["alim"]["first_seen"], now)    # re-armed


class TestAccountLimitBackoff(unittest.TestCase):
    CWD = "/home/newlevel/devel/lanenudge-alim"
    SID = "sess-lane-alim-1"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _call(self, now, tmtime, err_text, rec=None):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "transcript_last_error", return_value=err_text):
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec if rec is not None else {}, self.SID, self.CWD,
                "111", GOAL_ARMED_CAP, tpath, tmtime, "loc", None, False, None,
                proj, backlog_fetch=lambda cwd: 5, state={},
                sleep_fn=lambda s: None)
        return logs, owns, tmux, (rec if rec is not None else None)

    def test_account_limit_skips_the_nudge_and_seeds_the_episode(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {}
        logs, owns, tmux, _ = self._call(now, tmtime, WEEKLY, rec=rec)
        # NEVER dispatched into the dead cap
        self.assertEqual(tmux.sent, [], tmux.sent)
        self.assertFalse(any("lane-occupancy nudge" in ln for ln in logs), logs)
        # journalled a decision line (never silent)
        self.assertTrue(any("skip:account-limit" in ln for ln in logs), logs)
        # a bounded episode was seeded on the lane's own state dict
        self.assertIsInstance(rec.get("alim"), dict)
        self.assertEqual(rec["alim"]["first_seen"], now)

    def test_monthly_spend_and_org_also_back_off(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        for err in (MONTHLY, ORG):
            logs, owns, tmux, _ = self._call(now, tmtime, err, rec={})
            self.assertEqual(tmux.sent, [], (err, tmux.sent))
            self.assertTrue(any("skip:account-limit" in ln for ln in logs),
                            (err, logs))

    def test_recovered_transcript_clears_the_episode_and_nudges(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        rec = {"alim": {"first_seen": now - 60, "resets_at": None}}
        logs, owns, tmux, _ = self._call(now, tmtime, "", rec=rec)   # recovered
        self.assertNotIn("alim", rec)                                # cleared
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)  # nudged

    def test_back_off_elapsed_reprobes_once_and_rearms(self):
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        # first_seen 7h ago -> release (<=6h cap) already elapsed
        rec = {"alim": {"first_seen": now - 7 * 3600, "resets_at": None}}
        logs, owns, tmux, _ = self._call(now, tmtime, WEEKLY, rec=rec)
        self.assertTrue(any("-l" in a for a in tmux.sent), tmux.sent)  # re-probed
        self.assertEqual(rec["alim"]["first_seen"], now)              # re-armed


class TestNudgeTextQualifiesOpenCount(unittest.TestCase):
    """#502 defect (b): the nudge text names OPEN tickets honestly, not as if all
    were dispatchable -- while keeping the whole #442/#481 fleet doctrine."""

    def test_empty_lane_text_qualifies_open_not_workable(self):
        rendered = goal.GOAL_LANE_NUDGE_TEXT % (7, 2)
        low = rendered.lower()
        self.assertIn("nie všetky", low)
        self.assertIn("rozpracovate", low)
        self.assertIn("workable", low)
        # doctrine preserved
        self.assertIn("worktree", low)
        self.assertIn("8", rendered)
        self.assertIn("sériovo", low)

    def test_undersat_text_qualifies_open_not_workable(self):
        rendered = goal.GOAL_LANE_UNDERSAT_NUDGE_TEXT % (2, 5, 37, 1)
        low = rendered.lower()
        self.assertIn("nie všetky", low)
        self.assertIn("rozpracovate", low)
        # doctrine preserved
        self.assertIn("beží len 2", rendered)
        self.assertIn("cieľových 5", rendered)
        self.assertIn("worktree", low)


if __name__ == "__main__":
    unittest.main()
