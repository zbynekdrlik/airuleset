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
            "orphan-531": {"lts": NOW - 3 * DAY, "ln": 2, "lnbk": 4}}}
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


if __name__ == "__main__":
    unittest.main()
