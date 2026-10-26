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


class TheLanesOwnSessionPausedUnderUnresolvedPanes(_Stuck):
    """Review 7, finding 1: two panes share the cwd and NEITHER resolves to its
    own session (no claude start). The owner paused the cwd's newest session,
    which is the lane's `sid`; both panes hold on that session's own latch.
    The fix for review 6 keyed the episode end on the pane resolving its own
    session, so here the stall alert pinged the owner about her own pause
    and a held pane kept the newest session's batch for after the release."""

    T9, T8 = APausedPaneBesideAWorkingOne.T9, APausedPaneBesideAWorkingOne.T8

    def _two_panes(self):
        proj, sid, cap, fake = APausedPaneBesideAWorkingOne._two_panes(self)
        t = proj / wd.encode_project_dir(self.h.CWD) / (sid + ".jsonl")
        aged = os.path.getmtime(t)
        with open(t, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_u("pauza", self.T9 + 10)) + "\n")
        os.utime(t, (aged, aged))
        return proj, sid, cap, fake

    def _sweep(self, proj, sid, now, state, panes, tmux, send_fn):
        self.h._heartbeat(sid, age_s=self.goal.GOAL_LANE_IDLE_S + 600, now=now,
                          goal_armed=True)
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: None):
            owner_stop.refresh(state, [("%9", self.h.CWD), ("%8", self.h.CWD)],
                               proj, now, run=lambda argv, timeout=8: "")
        for pid in ("%9", "%8"):
            latch = owner_stop.pane_latch(pid) or {}
            self.assertEqual((latch.get("fact"), latch.get("via")), ("pause", None))
        self.goal.goal_lane_sweep(now, run=tmux(panes), projects_dir=proj,
                                  state=state, backlog_fetch=lambda cwd: 40,
                                  send_fn=send_fn)

    def test_no_stuck_alert_about_her_own_pause(self):
        proj, sid, cap, fake = self._two_panes()
        state, sends = {}, []

        def spy(*a, **k):
            sends.append(k.get("dedup_key"))
            return "sent"
        panes = [("%9", "claude", self.h.CWD, "111"), ("%8", "claude", self.h.CWD, "112")]
        for i in range(self.goal.GOAL_LANE_STUCK_ALERT_STREAK + 3):
            self._sweep(proj, sid, 1_000_000 + 60 * i, state, panes,
                        lambda p: fake(p, cap), spy)
        self.assertEqual([k for k in sends if k and k.startswith("stuckalert:")], [])

    def test_a_held_pane_drops_the_lanes_batch(self):
        proj, sid, cap, fake = self._two_panes()
        state = {"nudge_batch": {sid: [["goal-guard", "a queued goal-guard line"]]}}
        self._sweep(proj, sid, 1_000_000, state,
                    [("%8", "claude", self.h.CWD, "112")], lambda p: fake(p, cap),
                    lambda *a, **k: "sent")
        self.assertNotIn(sid, state.get("nudge_batch", {}))


class TheJournalFollowsTheSessionsOwner(unittest.TestCase):
    """Review 6, finding 3: since an unowned session is persisted quietly,
    the journal contradicted the latch when a pane gained or lost a session.
    (a) An unresolved pane that resolves to its paused session journalled
    only `pane %B -> cleared` while the latch still held, and no SET. (b) A
    resolved pane whose SET was journalled, then unresolved in the sweep the
    owner typed again, journalled no clear at all."""
    CWD = "/home/newlevel/devel/quietunclaimed"
    NOW = 1_800_000_000

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.addCleanup(owner_stop.reset)
        self.proj = Path(d.name)
        self.sd = self.proj / wd.encode_project_dir(self.CWD)
        self.sd.mkdir(parents=True)
        now = self.NOW
        for sid, rows, mt in (("SA", [_u("sprav ticket 5", now - 900),
                                      _a("robím", now - 800)], now - 800),
                              ("SB", [_u("sprav ticket 6", now - 600),
                                      _u("pauza", now - 550)], now - 600)):
            self._write(sid, rows, mt)
        self.state = {}

    def _write(self, sid, rows, mtime, append=False):
        f = self.sd / (sid + ".jsonl")
        with open(f, "a" if append else "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        os.utime(f, (mtime, mtime))

    def _sweep(self, i, starts):
        with m.patch.object(wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: starts.get(pid)):
            return owner_stop.refresh(self.state, [("%A", self.CWD), ("%B", self.CWD)],
                                      self.proj, self.NOW + 60 * i,
                                      run=lambda argv, timeout=8: "")

    def test_a_pane_that_resolves_its_paused_session_journals_set(self):
        both = {"%A": self.NOW - 950, "%B": self.NOW - 605}
        first = self._sweep(0, {"%A": self.NOW - 950})
        self.assertEqual(sum("SET" in ln for ln in first), 1, first)
        logs = self._sweep(1, both)
        self.assertEqual((owner_stop.pane_latch("%B") or {}).get("fact"), "pause")
        self.assertFalse(any("cleared" in ln for ln in logs), logs)
        self.assertEqual(len(logs), 1, logs)
        self.assertIn("sid=SB -> SET (pause", logs[0])
        self.assertEqual(self._sweep(2, both), [])

    def test_a_clear_after_the_pane_lost_its_session_is_journalled(self):
        logs = self._sweep(0, {"%A": self.NOW - 950, "%B": self.NOW - 605})
        self.assertEqual(sum("SET" in ln and "sid=SB" in ln for ln in logs), 1, logs)
        self._write("SB", [_u("pokračuj s ticketom 7", self.NOW + 30)],
                    self.NOW - 600, append=True)
        logs = self._sweep(1, {"%A": self.NOW - 950})
        self.assertIsNone(owner_stop.pane_latch("%B"))
        self.assertEqual(sum("cleared" in ln and "SB" in ln for ln in logs), 1, logs)

    def test_one_clear_line_when_the_owner_types_in_an_unowned_session(self):
        self._sweep(0, {"%A": self.NOW - 950, "%B": self.NOW - 605})
        logs = self._sweep(1, {"%A": self.NOW - 950})       # %B lost its session
        self.assertEqual((owner_stop.pane_latch("%B") or {}).get("fact"), "pause")
        self._write("SB", [_u("pokračuj s ticketom 7", self.NOW + 90)],
                    self.NOW - 600, append=True)
        logs = self._sweep(2, {"%A": self.NOW - 950})
        self.assertIsNone(owner_stop.pane_latch("%B"))
        self.assertEqual(sum("cleared" in ln for ln in logs), 1, logs)


class TheTmuxServerScanReadsAnyProcBytes(unittest.TestCase):
    """Review 6, finding 2 (already on main since the issue 1256 scan): the
    duplicate-server check decoded /proc/net/unix and the target's `comm`
    strictly and caught only OSError. Any local user can bind an abstract
    socket whose name is not UTF-8; `list_claude_panes`, and so every
    watchdog sweep in that network namespace, then raised
    UnicodeDecodeError."""
    PATH = "/tmp/tmux-1000/default"

    def setUp(self):
        import test_tmux_servers_1256 as t1256
        from watchdog import tmux_servers
        self.ts = tmux_servers
        self.p = t1256.FakeProc()
        self.addCleanup(self.p.close)

    def test_a_non_utf8_socket_name_in_net_unix(self):
        self.p.proc(100, "tmux: server")
        self.p.listen(100, self.PATH)
        with open(os.path.join(self.p.root, "net", "unix"), "ab") as fh:
            fh.write(b"0000: 00000002 00000000 00010000 0001 01 9999 @probe\xff\xfe\n")
        self.assertEqual(self.ts.servers_bound_to(self.PATH, proc_root=self.p.root),
                         [100])

    def test_a_non_utf8_target_name_is_a_changed_target(self):
        self.p.proc(100, "tmux: server")
        Path(self.p.root, "100", "comm").write_bytes(b"bad\xff\xfe\n")
        logs = []
        self.assertEqual(self.ts._signal(100, lambda pid, sig: None, self.p.root,
                                         logs, "head"), "changed")
        self.assertEqual(len(logs), 1, logs)


if __name__ == "__main__":
    unittest.main()
