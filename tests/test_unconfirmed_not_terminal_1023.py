"""#1023 — `delivered-unconfirmed` is NO LONGER terminal. Confirmation is the
observed `❯ nudge:` user turn (send_verified's transcript proof), NOT the
keystroke. A submit that cleared the box but was never confirmed (a queued submit,
or a race) does NOT advance the baseline and is NOT stamped into the per-kind
floor — the #372 janitor watch is left set (the undo/recovery path) and the next
idle tick re-delivers and confirms.

RED against the pre-#1023 tree: `delivered = ok OR delivered_unconfirmed` books an
unconfirmed submit as delivered (base advanced, `mark_sent`), so a second idle
tick never re-delivers. GREEN once only a transcript-confirmed `ok` advances.
"""
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_ARMED_CAP, _write_marker_transcript)

NOW = 1_000_000
DAY = 24 * 3600


class _SV:
    """A fake send_verified: returns `result`; when `unconfirmed`, sets
    out["delivered_unconfirmed"]=True and returns False (box cleared, confirm
    raced)."""

    def __init__(self, result=False, unconfirmed=True):
        self.result = result
        self.unconfirmed = unconfirmed
        self.n = 0

    def __call__(self, pid, text, run=None, tpath=None, sleep_fn=None, logs=None,
                 out=None, user_authored=False, nudge=None):
        self.n += 1
        if self.unconfirmed and isinstance(out, dict):
            out["delivered_unconfirmed"] = True
        return self.result


class TestUnconfirmedNotTerminal(unittest.TestCase):
    CWD = "/home/newlevel/devel/unconf"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-unconf")
        self.sid = self.tpath.stem

    def _tmux(self):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath)

    def _run(self, qrecs, state, now=NOW):
        with m.patch("airuleset.resolve_authority", return_value="full"):
            return qa.goal_queue_arrival_recheck(
                now, self._tmux(), qrecs, self.sid, self.CWD, "%9", self.tpath,
                "sess:0", False, set(), queue_fetch=lambda cwd: [1, 2],
                state=state, sleep_fn=lambda *a, **k: None)

    def test_unconfirmed_does_not_advance_baseline_or_floor(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        with m.patch.object(wd, "send_verified", _SV(result=False, unconfirmed=True)):
            logs = self._run(qrecs, state)
        # baseline NOT advanced -> the arrival re-detects next idle tick
        self.assertEqual(qrecs[self.sid]["base"], [1], logs)
        # the per-kind floor is NOT stamped (no confirmed delivery)
        self.assertNotIn("queue-arrival",
                         state.get("nudge_cadence", {}).get(self.sid, {}))
        self.assertTrue(any("delivered-unconfirmed" in ln for ln in logs), logs)

    def test_next_idle_tick_delivers_once_and_confirms(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        # tick 1: unconfirmed -> not booked
        with m.patch.object(wd, "send_verified", _SV(result=False, unconfirmed=True)):
            self._run(qrecs, state)
        self.assertEqual(qrecs[self.sid]["base"], [1])
        # tick 2: the send confirms -> baseline advances + floor stamped
        with m.patch.object(wd, "send_verified", _SV(result=True, unconfirmed=False)):
            self._run(qrecs, state, now=NOW + 120)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])
        self.assertEqual(state["nudge_cadence"][self.sid]["queue-arrival"],
                         NOW + 120)


if __name__ == "__main__":
    unittest.main()
