"""#531 -- orphan reaper for state["goal_lane"] per-sid records.

`goal_lane_sweep` persists `recs[sid] = rec` into `state["goal_lane"]` for every
ARMED candidate pane on every sweep, but had NO age/orphan reaper -- so a
session's `rec` survived forever once its pane was gone, growing the persisted
JSON one entry per distinct armed sid ever seen (the same per-sid-dict-leak
class as goal_mark #519 and confirm_state/attempts_state #524).

RED (against the pre-fix tree): an aged, gone-session entry in `state["goal_lane"]`
SURVIVES the sweep, and an armed pane's persisted rec carries NO age anchor.
GREEN (with `_prune_goal_lane_orphans` + the `lts` write-time stamp): the aged
orphan is reaped, a live/visited pane is never reaped even with a stale anchor,
and a recent not-visited entry is kept by the age gate.
"""

import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal
from watchdog import session_status as ss

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600


class _LaneSweepBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/laneorphan"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _heartbeat(self, sid, now):
        pth = ss.status_path(sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps(
            {"schema": 1, "sid": sid, "kind": "main", "last_turn": "stop",
             "ts": now, "cwd": self.CWD, "marker": "working",
             "goal_armed": True, "_note": "test"}), encoding="utf-8")

    def _armed_sweep(self, state, *, dry_run=False, backlog=5):
        """Drive `goal_lane_sweep` over ONE genuinely-armed candidate pane
        (goal_mark set + heartbeat armed), sharing `state`. Returns the live
        pane's sid so the caller can assert its rec was kept + stamped."""
        proj = self._dir()
        tpath = _write_marker_transcript(proj, self.CWD, "sess-531-live")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500   # idle transcript
        os.utime(tpath, (old, old))
        self._heartbeat(sid, NOW)
        gmarks = state.setdefault("goal_mark", {})
        gmarks[sid] = {"off": 0, "mark": {"state": "set", "ts": NOW}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(NOW, run=tmux, projects_dir=proj, state=state,
                                 dry_run=dry_run, backlog_fetch=lambda cwd: backlog)
        return sid


class TestGoalLaneOrphanReap(_LaneSweepBase):
    def test_531_orphan_goal_lane_entry_is_reaped(self):
        # An aged entry for a session with NO live candidate pane this sweep is
        # an orphan -> reaped. RED pre-fix: no reaper, so it survives forever.
        state = {"goal_lane": {
            "orphan-531": {"lts": NOW - 3 * DAY, "ln": 2}}}
        live = self._armed_sweep(state)
        recs = state["goal_lane"]
        self.assertNotIn("orphan-531", recs,
                         "an aged, gone-session goal_lane orphan must be reaped")
        self.assertIn(live, recs, "the live/visited pane's rec must be kept")

    def test_531_armed_pane_gets_an_lts_age_anchor_stamped(self):
        # The reaper's SECONDARY age gate needs a guaranteed timestamp; the sweep
        # stamps `lts = now` on every persisted rec. RED pre-fix: no `lts` key.
        state = {}
        live = self._armed_sweep(state)
        rec = state["goal_lane"][live]
        self.assertIn("lts", rec, "an armed pane's persisted rec must carry an lts anchor")
        self.assertEqual(rec["lts"], NOW)

    def test_531_dry_run_mutates_no_goal_lane_state(self):
        # #516 class: a `watchdog --once --dry-run` writes to the REAL STATE_PATH,
        # so the reaper is guarded `if not dry_run:` (mirroring #519). A dry-run
        # over an aged orphan must LEAVE it in place. Mutation-lock for that guard.
        state = {"goal_lane": {"orphan-531": {"lts": NOW - 3 * DAY}}}
        self._armed_sweep(state, dry_run=True)
        self.assertIn("orphan-531", state["goal_lane"],
                      "a dry-run must not reap (mutate) persisted goal_lane state")

    def test_531_state_survives_json_round_trip(self):
        # goal_lane state round-trips through save_state/load_state (JSON), so a
        # stored `lts` returns as int/float and the reaper still keys on it.
        state = {"goal_lane": {"orphan-531": {"lts": NOW - 3 * DAY},
                               "recent-531": {"lts": NOW - 60}}}
        rt = json.loads(json.dumps(state))
        recs = rt["goal_lane"]
        goal._prune_goal_lane_orphans(recs, visited_sids=set(), now=NOW)
        self.assertNotIn("orphan-531", recs, "aged orphan reaped after JSON round-trip")
        self.assertIn("recent-531", recs, "recent entry kept after JSON round-trip")

    def test_531_visited_not_armed_pane_keeps_a_stale_rec(self):
        # The visited gate is collected BEFORE the armed `continue`, so a LIVE but
        # temporarily NOT-ARMED pane (its rec dormant, NOT re-stamped this sweep)
        # is still in visited_sids and never reaped -- even with a stale `lts`.
        # Mutation-lock for the `visited_sids.add(sid)` placement + PRIMARY gate.
        proj = self._dir()
        tpath = _write_marker_transcript(proj, self.CWD, "sess-531-dormant")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat(sid, NOW)   # heartbeat present but goal not armed below
        # NOT armed: no goal_mark verdict, footer shows no glyph -> glance not True.
        state = {"goal_lane": {sid: {"lts": NOW - 3 * DAY, "ln": 1}}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   "● Hotová práca.\n❯ \n  ctx ███░\n",
                                   model_type=True, transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(NOW, run=tmux, projects_dir=proj, state=state,
                                 backlog_fetch=lambda cwd: 5)
        self.assertIn(sid, state["goal_lane"],
                      "a visited (live) but not-armed pane's rec must be kept")


class TestPruneGoalLaneOrphansUnit(unittest.TestCase):
    """Direct, pure-function mutation-locks for `_prune_goal_lane_orphans`."""

    def test_aged_not_visited_entry_is_reaped(self):
        recs = {"gone": {"lts": NOW - 3 * DAY, "ln": 2}}
        goal._prune_goal_lane_orphans(recs, visited_sids=set(), now=NOW)
        self.assertEqual(recs, {})

    def test_visited_entry_is_never_reaped_even_with_stale_lts(self):
        # PRIMARY gate: an age-only reaper would reap this (stale lts); the
        # visited check keeps it.
        recs = {"live": {"lts": NOW - 3 * DAY}}
        goal._prune_goal_lane_orphans(recs, visited_sids={"live"}, now=NOW)
        self.assertIn("live", recs)

    def test_recent_not_visited_entry_is_kept_by_age_gate(self):
        # SECONDARY gate: a visited-only reaper (no age check) would reap this
        # not-visited entry; the age gate keeps a recent one (budget-deferred).
        recs = {"recent": {"lts": NOW - 60}}
        goal._prune_goal_lane_orphans(recs, visited_sids=set(), now=NOW)
        self.assertIn("recent", recs)

    def test_malformed_not_visited_entries_are_reaped(self):
        recs = {"nodict": "junk", "nots": {"ln": 1}, "badts": {"lts": "x"}}
        goal._prune_goal_lane_orphans(recs, visited_sids=set(), now=NOW)
        self.assertEqual(recs, {})

    def test_future_lts_is_kept_the_safe_direction(self):
        # A future `lts` (clock skew) is ambiguous -> keep (never reap a possibly
        # live rec), matching #519's `< ttl_s` predicate.
        recs = {"skew": {"lts": NOW + 5000}}
        goal._prune_goal_lane_orphans(recs, visited_sids=set(), now=NOW)
        self.assertIn("skew", recs)

    def test_non_dict_recs_never_raises(self):
        goal._prune_goal_lane_orphans(None, visited_sids=set(), now=NOW)
        goal._prune_goal_lane_orphans([1, 2], visited_sids=set(), now=NOW)


if __name__ == "__main__":
    unittest.main()
