"""#486 G6 -- the lane-nudge armed action gate reads STRUCTURED state, not the
render footer.

The bug this locks (the live gk incident, evidence issuecomment-5308022173): a
genuinely-armed supervisor whose FOOTER is obscured every sweep (busy / chrome
redraws from running workers / a large unsent draft push the `◎ /goal`
statusline row off the captured view) reads `pane_goal_armed -> None`
(undeterminable). The pre-G6 render action gate then logged
`skip:armed-undeterminable` and `continue`d -- for HOURS -- so the lane nudge
never fired even though the box was armed, under-saturated and had a backlog.
The heartbeat's own `goal_armed` shares the SAME 4 MB-tail blind spot, so it
alone would ALSO have missed a day-old arm; the tail-proof signal is dark_watch's
incremental `state["goal_mark"]` marker.

RED (against the pre-G6 tree): `goal_lane_sweep` over an obscured-footer pane
with `goal_mark` set + a heartbeat that reads NOT-armed (the 4 MB lie) skips at
the render gate and delivers NO keystroke. GREEN (with the structured gate + the
1867 None-flip): the nudge is delivered because the structured armed signal
gates it, and the pre-send race re-check no longer re-vetoes on the same
unreadable footer.
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
    _encode,
    _write_marker_transcript,
)

# A pane whose FOOTER is off the captured view (the `◎ /goal` statusline row
# scrolled past the bottom behind a worker strip / chrome redraw) so
# `pane_goal_armed` reads None -- while the `❯ ` input box is a settled, typeable
# idle prompt. Probed live: pane_goal_armed=None, _lane_boundary_ok=(True,input).
OBSCURED_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n"


class TestG6StructuredArmedGate(unittest.TestCase):
    CWD = "/home/newlevel/devel/g6armed"

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

    def _heartbeat(self, sid, *, goal_armed, marker, now):
        pth = ss.status_path(sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps(
            {"schema": 1, "sid": sid, "kind": "main", "last_turn": "stop",
             "ts": now, "cwd": self.CWD, "marker": marker,
             "goal_armed": goal_armed, "_note": "test"}), encoding="utf-8")
        return pth

    def _run_sweep(self, *, goal_mark_state, hb_goal_armed, backlog, cap):
        """Drive `goal_lane_sweep` over ONE obscured-footer pane whose
        transcript is idle (old mtime), 0 live workers, `backlog` open, a
        heartbeat that reads `hb_goal_armed`, and `state["goal_mark"][sid]`
        holding `goal_mark_state` ("set"/"cleared"/None) -- exactly what
        dark_watch (which runs first, same run_once, shared state) would have
        left. Returns (logs, tmux)."""
        proj = self._dir()
        now = 1_000_000
        tpath = _write_marker_transcript(proj, self.CWD, "sess-g6")
        sid = tpath.stem
        # idle: transcript mtime older than the empty-lane idle floor
        old = now - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat(sid, goal_armed=hb_goal_armed, marker="working", now=now)
        mark = {"state": goal_mark_state, "ts": now} if goal_mark_state else None
        state = {"goal_mark": {sid: {"off": 0, "mark": mark}}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap,
                                   model_type=True, transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            logs = goal.goal_lane_sweep(now, run=tmux, projects_dir=proj,
                                        state=state,
                                        backlog_fetch=lambda cwd: backlog)
        return logs, tmux

    def test_obscured_footer_with_structured_arm_delivers_the_nudge(self):
        # THE #486 G6 CASE (RED against pre-G6): footer obscured
        # (pane_goal_armed -> None), heartbeat reads NOT-armed (the 4 MB-tail
        # lie), but dark_watch's tail-proof goal_mark says the /goal IS armed.
        # The structured gate MUST let the empty-lane nudge through, and the
        # pre-send race re-check (which re-captures the SAME obscured footer)
        # must NOT re-veto on the unreadable None.
        self.assertIsNone(wd.pane_goal_armed(OBSCURED_IDLE_CAP))
        logs, tmux = self._run_sweep(goal_mark_state="set", hb_goal_armed=False,
                                     backlog=5, cap=OBSCURED_IDLE_CAP)
        self.assertTrue(any("lane-occupancy nudge" in ln for ln in logs),
                        "structured-armed obscured pane must deliver a nudge: %r"
                        % logs)
        self.assertTrue(any("-l" in a for a in tmux.sent),
                        "expected real keystrokes typed: %r" % tmux.sent)
        # the pre-G6 render gate must be GONE -- no armed-undeterminable skip
        self.assertFalse(any("armed-undeterminable" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
