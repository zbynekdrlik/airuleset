"""#486 G3 -- the one-glance STRUCTURED predicate wired into `goal_lane_sweep`.

The bug this locks (the #486 gk diagnostic): a genuinely-armed supervisor whose
footer reads not-armed (`pane_goal_armed` -> False, e.g. a stash prefix or a
`(1d)` age granularity the closed-form regex goes blind on) was skipped BY THE
RENDER PATH with a deliberately-SILENT `continue` -- 0 workers + big backlog +
armed goal, and the machine said NOTHING. G3 evaluates the structured
one-glance predicate (heartbeat + G2 worker count + backlog cache -- NO pane
text) for EVERY candidate pane BEFORE the render gate, so exactly that
render-blind case now emits ONE `one-glance ... stuck` decision line.

RED (against the pre-G3 tree): `goal_lane_sweep` with a not-armed footer returns
NO `one-glance` line. GREEN (with the wiring): the structured STUCK verdict is
journalled even though the footer read not-armed.
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
    GOAL_IDLE_CAP,      # readable footer, NO ◎ /goal -> pane_goal_armed -> False
    GOAL_ARMED_CAP,     # ◎ /goal footer -> pane_goal_armed -> True
    DeliverGoalFakeTmux,
    _write_marker_transcript,
)


class TestGoalLaneOneGlance(unittest.TestCase):
    CWD = "/home/newlevel/devel/oneglance"

    def setUp(self):
        # Hermetic session-status dir for ALL run modes (pytest's autouse fixture
        # + cmd_push's unittest-discover env already isolate this; a bare
        # `python3 tests/...py` run does not, so set it explicitly here and
        # restore, per the batch-31 HOME-leak discipline).
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        patcher = m.patch.dict(
            os.environ, {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _heartbeat(self, sid, *, goal_armed, marker, age_s, now):
        """Write a real heartbeat file for `sid` (armed/marker) with its mtime
        set `age_s` in the past -- so `read_status(stale_after_s=idle_threshold)`
        reads it as idle-over-threshold when `age_s` exceeds the threshold."""
        p = ss.status_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema": 1, "sid": sid, "kind": "main", "last_turn": "stop",
                "ts": int(now - age_s), "cwd": self.CWD, "marker": marker,
                "goal_armed": goal_armed, "_note": "test"}
        p.write_text(json.dumps(data), encoding="utf-8")
        old = now - age_s
        os.utime(p, (old, old))
        return p

    def _sweep(self, captured, *, goal_armed, marker, age_s, backlog):
        proj = self._dir()
        now = 1_000_000
        tpath = _write_marker_transcript(proj, self.CWD, "sess-oneglance")
        sid = tpath.stem
        self._heartbeat(sid, goal_armed=goal_armed, marker=marker,
                        age_s=age_s, now=now)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], captured)
        logs = goal.goal_lane_sweep(now, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: backlog)
        return logs, tmux

    def _one_glance_lines(self, logs):
        return [ln for ln in logs if ln.startswith("one-glance ")]

    def test_render_blind_but_structurally_stuck_is_no_longer_silent(self):
        # THE #486 CASE: footer reads not-armed (pane_goal_armed -> False), but
        # the heartbeat says a /goal IS armed, 0 workers, big backlog, idle over
        # the threshold. The render path skips (no nudge, correct in G3), but the
        # structured one-glance line MUST journal `stuck` -- the exact
        # observability the silent branch destroyed.
        self.assertIs(wd.pane_goal_armed(GOAL_IDLE_CAP), False)
        logs, tmux = self._sweep(
            GOAL_IDLE_CAP, goal_armed=True, marker="working",
            age_s=goal.GOAL_LANE_IDLE_S + 500, backlog=43)
        og = self._one_glance_lines(logs)
        self.assertTrue(og, "no one-glance decision line emitted: %r" % logs)
        self.assertTrue(any("-> stuck (" in ln for ln in og),
                        "expected a STUCK one-glance verdict: %r" % og)
        # the line must name the render<->structured DIVERGENCE
        self.assertTrue(any("render=not-armed" in ln and "structured state is armed"
                            in ln for ln in og), og)
        # DIAGNOSTIC in G3: render path stays authoritative -> no keystroke sent
        self.assertEqual(tmux.sent, [], "G3 must not act -- render path skipped")

    def test_render_blind_working_when_the_structured_count_sees_workers(self):
        # A render-blind pane whose structured state shows LIVE workers is
        # `working`, never `stuck` -- proves the predicate reads the G2 count,
        # not the footer.
        def _count(*a, **k):
            return 3, []
        with m.patch.object(wd, "count_live_workers", _count):
            logs, _tmux = self._sweep(
                GOAL_IDLE_CAP, goal_armed=True, marker="working",
                age_s=goal.GOAL_LANE_IDLE_S + 500, backlog=43)
        og = self._one_glance_lines(logs)
        self.assertTrue(any("-> working (" in ln for ln in og), og)
        self.assertFalse(any("-> stuck" in ln for ln in og), og)

    def test_no_heartbeat_reads_as_unknown_never_stuck(self):
        # No heartbeat at all (a box where G1's hooks have not fired) -> the
        # structured predicate can't assert armed, so it says `no-heartbeat`
        # (render stays authoritative), never a false STUCK.
        proj = self._dir()
        now = 1_000_000
        _write_marker_transcript(proj, self.CWD, "sess-oneglance")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_lane_sweep(now, run=tmux, projects_dir=proj,
                                    backlog_fetch=lambda cwd: 43)
        og = self._one_glance_lines(logs)
        self.assertTrue(any("-> no-heartbeat (" in ln for ln in og), og)
        self.assertFalse(any("-> stuck" in ln for ln in og), og)

    def test_armed_footer_also_gets_a_one_glance_line(self):
        # An armed footer (render agrees) proceeds to the nudge path AND gets a
        # one-glance line -- two distinct evaluations, each one line. Here the
        # structured state also shows workers, so the verdict is `working`.
        def _count(*a, **k):
            return 2, []
        with m.patch.object(wd, "count_live_workers", _count):
            logs, _tmux = self._sweep(
                GOAL_ARMED_CAP, goal_armed=True, marker="working",
                age_s=30, backlog=43)
        og = self._one_glance_lines(logs)
        self.assertTrue(og, logs)
        self.assertTrue(any("render=armed" in ln for ln in og), og)


if __name__ == "__main__":
    unittest.main()
