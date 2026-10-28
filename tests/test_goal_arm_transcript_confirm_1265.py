"""#1265 — a goal arm that LANDED is confirmed from the session transcript, not
only from the pane render.

Incident (owner, dev1 camera-box 28.10.2026, sid 90bc51f3): the owner's
`/autopilot` arm was typed six times. Each time CC answered with
`<local-command-stdout>Goal set: …`, i.e. the arm landed, but the arm's own turn
started at once and covered the `◎ /goal` footer, so `_await_goal_armed` read
`armed=False box=empty`. The transcript had advanced, so `_verify_fail_word`
said `skip:verify-failed-live` (uncounted), and `goal_sweep` retyped the request
until `GOAL_DELIVERY_LIVE_ATTEMPT_CAP`.

Fix (design comment on the ticket): `_await_goal_armed` reads the structured
state first (#486). A `Goal set:` marker in the bytes appended AFTER the
pre-keystroke `scan_goal_markers` offset means the arm landed. A stale marker
(before the offset) or a `cleared` marker after it never counts.
"""
import json
import os
import sys
import time
import unittest
import unittest.mock as m
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
)

CWD = "/home/newlevel/devel/armconfirm1265"
SID = "sess-armconfirm-1265"
PAYLOAD = "work the whole backlog one ticket at a time until every issue is closed"
TEXT = "/goal " + PAYLOAD
PANE = [("%9", "claude", CWD, "111")]


def _append_marker(tpath, text):
    """Append a CC `/goal` stdout marker (`Goal set: …` / `Goal cleared: …`)
    the way CC writes it: a top-level `system` entry, LCS-wrapped."""
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    entry = {"type": "system", "subtype": "local_command", "timestamp": iso,
             "content": "<local-command-stdout>%s</local-command-stdout>" % text}
    with open(tpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class _ArmCoveredFake(DeliverGoalFakeTmux):
    """The camera-box shape: the `/goal` submit ARMS (CC writes the marker to
    the transcript) but the pane never shows the `◎ /goal` glyph, because the
    arm's own turn covers it (`arm_on_submit=False` keeps the footer dark).
    `on_submit` lists the marker texts CC appends on a `/goal` submit."""

    def __init__(self, *a, tpath=None, on_submit=(), **kw):
        kw.setdefault("arm_on_submit", False)
        super().__init__(*a, **kw)
        self._tpath = tpath
        self._on_submit = list(on_submit)

    def __call__(self, argv, timeout=8):
        if (self.model_type and "send-keys" in " ".join(argv)
                and argv[-1] == "Enter" and "-l" not in argv
                and self.box.strip().startswith("/goal ")
                and self.enters_swallowed == 0):
            for mark in self._on_submit:
                _append_marker(self._tpath, mark)
        return super().__call__(argv, timeout)


class _Base(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = Path(d.name)
        self.tpath = _write_marker_transcript(self.proj, CWD, SID,
                                              transcript_age_s=None)

    def _quiet(self, now):
        # idle at the gate: last write 120 s before the sweep `now`
        os.utime(self.tpath, (now - 120, now - 120))

    def _deliver(self, fake, now):
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            return goal.deliver_goal(SID, CWD, TEXT, "full", run=fake,
                                     projects_dir=self.proj, now=now, state={},
                                     sleep_fn=lambda s: None)


class TestLandedArmIsSent(_Base):
    def test_incident_arm_landed_but_pane_dark_is_sent(self):
        now = time.time()
        self._quiet(now)
        fake = _ArmCoveredFake(PANE, GOAL_IDLE_CAP, model_type=True,
                               tpath=self.tpath,
                               on_submit=["Goal set: " + PAYLOAD])
        word = self._deliver(fake, now)
        self.assertEqual(word, "sent",
                         "a `Goal set:` written after the keystroke IS a landed arm")
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("ARM-CONFIRMED src=transcript", log)
        self.assertIn("SEND typed", log)
        self.assertNotIn("ARM-CONFIRM-FAIL", log)

    def test_sweep_clears_request_and_second_sweep_types_nothing(self):
        now = time.time()
        self._quiet(now)
        goal.record_goal_request(SID, CWD, TEXT, "full", now=now,
                                 path=self.reqp, origin="self-callback")
        fake = _ArmCoveredFake(PANE, GOAL_IDLE_CAP, model_type=True,
                               tpath=self.tpath,
                               on_submit=["Goal set: " + PAYLOAD])
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(now, run=fake, projects_dir=self.proj,
                                   requests_path=self.reqp,
                                   send_fn=lambda msg, **kw: None,
                                   sleep_fn=lambda s: None)
        self.assertTrue(any("-> sent" in ln for ln in logs), logs)
        self.assertNotIn(SID, goal.load_goal_requests(self.reqp))
        # the next idle sweep has nothing pending -> zero keystrokes
        later = now + 70
        self._quiet(later)
        fake2 = _ArmCoveredFake(PANE, GOAL_IDLE_CAP, model_type=True,
                                tpath=self.tpath)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal.goal_sweep(later, run=fake2, projects_dir=self.proj,
                            requests_path=self.reqp,
                            send_fn=lambda msg, **kw: None,
                            sleep_fn=lambda s: None)
        self.assertEqual(fake2.typed_texts(), [], "a landed arm is never retyped")


class TestNotALandedArm(_Base):
    def test_stale_goal_set_before_keystroke_does_not_count(self):
        # an OLD arm marker already in the transcript; the submit is read as a
        # plain prompt (a `user` turn advances the transcript, no new marker).
        _append_marker(self.tpath, "Goal set: an older loop condition")
        now = time.time()
        self._quiet(now)
        fake = _ArmCoveredFake(PANE, GOAL_IDLE_CAP, model_type=True,
                               tpath=self.tpath, transcript_path=str(self.tpath))
        word = self._deliver(fake, now)
        self.assertEqual(word, "skip:verify-failed-live")
        self.assertNotIn("ARM-CONFIRMED src=transcript",
                         Path(self.syncp).read_text(encoding="utf-8"))

    def test_cleared_after_keystroke_does_not_count(self):
        now = time.time()
        self._quiet(now)
        fake = _ArmCoveredFake(PANE, GOAL_IDLE_CAP, model_type=True,
                               tpath=self.tpath,
                               on_submit=["Goal set: " + PAYLOAD,
                                          "Goal cleared: " + PAYLOAD])
        word = self._deliver(fake, now)
        self.assertNotEqual(word, "sent", "the newest marker is `cleared`")
        self.assertEqual(word, "skip:verify-failed-live")


class TestOtherArmRoutes(_Base):
    """#1265 review -- the stash and stranded-own routes share the confirm; prove
    both pass the offset end to end (dropping it would re-open the retype)."""

    def test_stranded_own_goal_landed_with_dark_pane_is_sent(self):
        now = time.time()
        self._quiet(now)
        fake = _ArmCoveredFake(PANE, GOAL_IDLE_CAP, model_type=True,
                               tpath=self.tpath, initial_box=TEXT,
                               on_submit=["Goal set: " + PAYLOAD])
        word = self._deliver(fake, now)
        self.assertEqual(word, "sent")
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("SEND recover-swallowed", log)
        self.assertIn("ARM-CONFIRMED src=transcript sid=%s" % SID, log)

    def test_stash_route_landed_with_dark_pane_is_sent(self):
        now = time.time()
        self._quiet(now)
        fake = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   initial_box="rozpisany draft",
                                   arm_on_submit=False)

        def _stash_send(pid, text, run, **kw):
            # CC arms the stash-delivered /goal; the pane stays dark
            _append_marker(self.tpath, "Goal set: " + PAYLOAD)
            return True

        with m.patch.object(wd, "deliver_with_stash", side_effect=_stash_send):
            word = self._deliver(fake, now)
        self.assertEqual(word, "sent")
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("SEND stash", log)
        self.assertIn("ARM-CONFIRMED src=transcript", log)


class TestStructuredConfirmUnit(_Base):
    """`_await_goal_armed` + `_transcript_arm_landed` directly (the confirm the
    stash and stranded-own routes share with the bare route)."""

    def _dark(self):
        return DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP)   # footer never armed

    def test_marker_after_offset_confirms_with_dark_pane(self):
        off, _ = wd.scan_goal_markers(str(self.tpath))
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        self.assertTrue(goal._await_goal_armed("%9", self._dark(), lambda s: None,
                                               tpath=str(self.tpath), mark_off=off))

    def test_marker_before_offset_does_not_confirm(self):
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        off, _ = wd.scan_goal_markers(str(self.tpath))
        self.assertFalse(goal._await_goal_armed("%9", self._dark(), lambda s: None,
                                                tpath=str(self.tpath), mark_off=off))

    def test_no_offset_or_path_is_pane_only(self):
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        self.assertFalse(goal._await_goal_armed("%9", self._dark(), lambda s: None))
        self.assertFalse(goal._transcript_arm_landed(None, 0))
        self.assertFalse(goal._transcript_arm_landed(str(self.tpath), None))

    def test_foreign_session_transcript_gets_no_confirm_offset(self):
        # the newest transcript of the cwd belongs to ANOTHER session: its
        # marker must never confirm our arm (pane-only).
        off, _mark = goal._scan_with_confirm_offset(str(self.tpath), "other-sid")
        self.assertIsNone(off)
        off, _mark = goal._scan_with_confirm_offset(str(self.tpath), SID)
        self.assertEqual(off, os.path.getsize(self.tpath))

    def test_truncated_transcript_never_confirms(self):
        # a rotated/truncated file (size < offset) would make the scan fall back
        # to the tail bootstrap and could return a STALE marker: refuse it.
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        size = os.path.getsize(self.tpath)
        self.assertFalse(goal._transcript_arm_landed(str(self.tpath), size + 500))

    def test_missing_transcript_never_confirms(self):
        self.assertFalse(goal._transcript_arm_landed(
            str(self.proj / "no-such.jsonl"), 0))


if __name__ == "__main__":
    unittest.main()


class TestLateMarkerDropsPendingSelfArm(_Base):
    """#1265 follow-up (supervisor ROZHODNUTÉ): CC may write `Goal set:` AFTER the
    8 s confirm window. A pending `self-callback` request whose session's OWN
    transcript has a newest marker `set` written after the request was recorded
    is dropped terminally (`drop:already-armed`, zero keystrokes). A marker that
    predates the owner's /autopilot never blocks the arm (#1113)."""

    def _record_busy(self):
        # the owner's /autopilot turn is still running at record time -> the
        # sync attempt defers (busy-transcript) with zero keystrokes
        now = time.time()
        os.utime(self.tpath, (now - 5, now - 5))
        fake = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True)
        word = goal._goal_sync_attempt(SID, CWD, TEXT, "full", "self-callback",
                                       run=fake, projects_dir=self.proj,
                                       requests_path=self.reqp, state={},
                                       sleep_fn=lambda s: None)
        self.assertEqual(word, "skip:busy-transcript")
        self.assertEqual(fake.typed_texts(), [])

    def _sweep(self, arm=True):
        now = time.time()
        self._quiet(now)
        fake = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                   arm_on_submit=arm)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs = goal.goal_sweep(now, run=fake, projects_dir=self.proj,
                                   requests_path=self.reqp, state={},
                                   send_fn=lambda msg, **kw: None,
                                   sleep_fn=lambda s: None)
        return logs, fake

    def test_late_goal_set_after_request_drops_with_no_keystroke(self):
        self._record_busy()
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)   # landed late
        logs, fake = self._sweep()
        self.assertEqual(fake.typed_texts(), [], "a landed arm is never retyped")
        self.assertTrue(any("drop:already-armed" in ln for ln in logs), logs)
        self.assertNotIn(SID, goal.load_goal_requests(self.reqp))

    def test_incident_path_typed_unconfirmed_then_late_marker_drops(self):
        # sweep 1 types the arm; CC writes nothing within the confirm window
        # (a plain `user` turn advances the transcript) -> verify-failed-live.
        self._record_busy()
        now = time.time()
        self._quiet(now)
        fake1 = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True,
                                    arm_on_submit=False, transcript_path=str(self.tpath))
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            logs1 = goal.goal_sweep(now, run=fake1, projects_dir=self.proj,
                                    requests_path=self.reqp, state={},
                                    send_fn=lambda msg, **kw: None,
                                    sleep_fn=lambda s: None)
        self.assertEqual(fake1.typed_texts(), [TEXT])
        self.assertTrue(any("verify-failed-live" in ln for ln in logs1), logs1)
        self.assertIn("moff", goal.load_goal_requests(self.reqp)[SID],
                      "the record-time offset survives a failed delivery")
        # the arm lands late; the next sweep must type nothing
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        logs2, fake2 = self._sweep()
        self.assertEqual(fake2.typed_texts(), [], logs2)
        self.assertTrue(any("drop:already-armed" in ln for ln in logs2), logs2)
        self.assertIn("drop:already-armed", Path(self.syncp).read_text(encoding="utf-8"))
        self.assertNotIn(SID, goal.load_goal_requests(self.reqp))

    def test_goal_set_before_request_does_not_block_owner_arm(self):
        _append_marker(self.tpath, "Goal set: an older loop condition")
        self._record_busy()
        logs, fake = self._sweep()
        self.assertEqual(fake.typed_texts(), [TEXT], logs)
        self.assertTrue(any("-> sent" in ln for ln in logs), logs)

    def test_cleared_after_set_does_not_drop(self):
        self._record_busy()
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        _append_marker(self.tpath, "Goal cleared: " + PAYLOAD)
        logs, fake = self._sweep()
        # newest marker is `cleared`: never already-armed; today's owner-clear
        # handling (#170 / #1257 owner-stop) still decides, unchanged.
        self.assertFalse(any("drop:already-armed" in ln for ln in logs), logs)
        self.assertTrue(any("drop:owner-stop" in ln for ln in logs), logs)

    def test_request_without_offset_keeps_todays_behaviour(self):
        # a legacy request (recorded before this change) carries no offset
        goal.record_goal_request(SID, CWD, TEXT, "full", path=self.reqp,
                                 origin="self-callback")
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        logs, fake = self._sweep()
        self.assertEqual(fake.typed_texts(), [TEXT], logs)


class TestLateMarkerLeaf(_Base):
    def test_origin_constant_matches_goal_module(self):
        from watchdog import goal_arm_confirm as gac
        self.assertEqual(gac.SELF_CALLBACK_ORIGIN, goal._GOAL_SELF_CALLBACK_ORIGIN)

    def test_offset_only_for_owner_arm(self):
        from watchdog import goal_arm_confirm as gac
        self.assertIsNone(gac.request_mark_offset(self.proj, CWD, SID, "dark-rearm"))
        self.assertEqual(gac.request_mark_offset(self.proj, CWD, SID, "self-callback"),
                         os.path.getsize(self.tpath))
        self.assertIsNone(gac.request_mark_offset(self.proj, CWD, "no-such", "self-callback"))

    def test_other_origin_or_bad_offset_never_late_dropped(self):
        from watchdog import goal_arm_confirm as gac
        _append_marker(self.tpath, "Goal set: " + PAYLOAD)
        self.assertIsNone(gac.late_arm_drop({"origin": "dark-rearm", "moff": 0},
                                            SID, CWD, self.proj))
        self.assertIn("drop:already-armed",
                      gac.late_arm_drop({"origin": "self-callback", "moff": 0},
                                        SID, CWD, self.proj))
        for bad in (None, True, -1, "0"):
            self.assertIsNone(gac.late_arm_drop({"origin": "self-callback", "moff": bad},
                                                SID, CWD, self.proj), bad)
