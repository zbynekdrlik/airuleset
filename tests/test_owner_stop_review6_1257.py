"""Bundle B9 fix round, sixth adversarial review of the issue 1257 owner-stop
latch: the stall episodes a pause ends (all five keys, never in a dry run),
a paused pane that shares its cwd with a working one, the latch journal when
a session moves between a pane that owns it and none, and the issue 1256
tmux server scan on /proc bytes that are not UTF-8.
"""
import json
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import owner_stop  # noqa: E402
from test_owner_stop_review5_1257 import _lane  # noqa: E402

EPISODE_KEYS = ("soa", "soalert", "soa_ts", "gks_ts", "gks_alert")


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def _u(text, ts):
    return {"type": "user", "timestamp": _iso(ts),
            "message": {"role": "user", "content": text}}


def _a(text, ts):
    return {"type": "assistant", "timestamp": _iso(ts),
            "message": {"content": [{"type": "text", "text": text}]}}


class _Stuck(unittest.TestCase):
    """The issue 662 stuck-alert sweep harness (one armed, stuck pane %9 on
    session `sess-662`)."""

    def setUp(self):
        import test_oauth_stuck_alert_662 as sa
        self.goal = sa.goal
        self.h = sa.SweepStuckOwnerAlert("run")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.addCleanup(owner_stop.reset)

    def _dir(self, kw):
        return Path(kw["projects_dir"]) / wd.encode_project_dir(self.h.CWD)

    def _own_pause(self, now, kw):
        t = self._dir(kw) / "sess-662.jsonl"
        if "pauza" not in t.read_text(encoding="utf-8"):
            aged = os.path.getmtime(t)
            with open(t, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_u("pauza", now - 600)) + "\n")
            os.utime(t, (aged, aged))
        owner_stop.refresh(kw["state"], [("%9", self.h.CWD)], kw["projects_dir"], now)


class AnOwnPauseEndsEveryStallEpisode(_Stuck):
    """Review 6, finding 4: removing the dry-run guard of the episode reset,
    or dropping `soalert` / `gks_alert` from it, left every test green (the
    old test seeded three of the five keys and never ran a dry sweep).
    `run_once` saves state even in a dry run, so a dry pop would persist."""

    def _swept(self, dry_run):
        state = {"goal_lane": {"sess-662": {k: 1 for k in EPISODE_KEYS}}}
        with _lane(self, self.goal, self.h.CWD, self._own_pause):
            _s, state, sid = self.h._run(self.h._stuck(1), state=state,
                                         dry_run=dry_run)
        return state["goal_lane"][sid]

    def test_a_live_sweep_ends_all_five(self):
        rec = self._swept(False)
        self.assertEqual([k for k in EPISODE_KEYS if k in rec], [], rec)

    def test_a_dry_run_leaves_them(self):
        rec = self._swept(True)
        self.assertEqual([k for k in EPISODE_KEYS if k in rec], list(EPISODE_KEYS), rec)


class APausedPaneBesideAWorkingOne(_Stuck):
    """Review 6, finding 1: the goal-lane `sid` is the cwd's NEWEST session,
    but a pause is a fact about one pane. Pane %8 (resolved to its own older
    session, paused) shares the cwd with pane %9 (no latch, its session is
    the newest and stuck). Every sweep %8 ended %9's stall episodes, so %9's
    stuck alert never fired; and %8's hold discarded the newest session's
    nudge batch, which %9 runs."""
    T9, T8 = 900_000, 800_000

    def _two_panes(self):
        from _goal_arm_helpers import DeliverGoalFakeTmux, _write_marker_transcript
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        tpath = _write_marker_transcript(proj, self.h.CWD, "sess-662")
        aged = os.path.getmtime(tpath)
        with open(tpath, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_u("sprav ticket 9", self.T9)) + "\n")
        os.utime(tpath, (aged, aged))
        own = tpath.parent / "sess-b.jsonl"
        own.write_text("".join(json.dumps(r) + "\n" for r in (
            _u("sprav ticket 8", self.T8), _u("pauza", self.T8 + 10))),
            encoding="utf-8")
        os.utime(own, (1000, 1000))
        cap = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite  ◎ /goal active\n"
        return proj, tpath.stem, cap, DeliverGoalFakeTmux

    def _sweep(self, proj, sid, now, state, panes, tmux, send_fn):
        self.h._heartbeat(sid, age_s=self.goal.GOAL_LANE_IDLE_S + 600, now=now,
                          goal_armed=True)
        starts = {"%9": self.T9 - 5, "%8": self.T8 - 5}
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: starts.get(pid)):
            owner_stop.refresh(state, [("%9", self.h.CWD), ("%8", self.h.CWD)],
                               proj, now, run=lambda argv, timeout=8: "")
        self.assertEqual((owner_stop.pane_latch("%8") or {}).get("fact"), "pause")
        self.assertIsNone(owner_stop.pane_latch("%9"))
        self.goal.goal_lane_sweep(now, run=tmux(panes), projects_dir=proj,
                                  state=state, backlog_fetch=lambda cwd: 40,
                                  send_fn=send_fn)

    def test_the_working_panes_stuck_alert_still_fires(self):
        proj, sid, cap, fake = self._two_panes()
        state, sends = {}, []

        def spy(*a, **k):
            sends.append(k.get("dedup_key"))
            return "sent"
        panes = [("%9", "claude", self.h.CWD, "111"), ("%8", "claude", self.h.CWD, "112")]
        for i in range(self.goal.GOAL_LANE_STUCK_ALERT_STREAK + 3):
            self._sweep(proj, sid, 1_000_000 + 60 * i, state, panes,
                        lambda p: fake(p, cap), spy)
        self.assertEqual(len([k for k in sends if k and k.startswith("stuckalert:")]),
                         1, sends)

    def test_the_paused_pane_keeps_the_newest_sessions_batch(self):
        proj, sid, cap, fake = self._two_panes()
        state = {"nudge_batch": {sid: [["goal-guard", "a queued goal-guard line"]]}}
        self._sweep(proj, sid, 1_000_000, state,
                    [("%8", "claude", self.h.CWD, "112")], lambda p: fake(p, cap),
                    lambda *a, **k: "sent")
        self.assertIn(sid, state.get("nudge_batch", {}), state.get("nudge_batch"))


if __name__ == "__main__":
    unittest.main()
