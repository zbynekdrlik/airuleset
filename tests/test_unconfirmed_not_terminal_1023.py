"""#1023 — a `delivered-unconfirmed` submit is NON-TERMINAL for the BASELINE.
Confirmation is the observed `❯ nudge:` user turn (send_verified's transcript
proof), NOT the keystroke. A submit that cleared the box but was never confirmed
(a queued submit, or a race) does NOT advance the baseline — the #372 janitor
watch is left set (the undo/recovery path) and the arrival re-nudges until it is
confirmed.

#1023-review 🟡4: the per-kind FLOOR *is* stamped on a delivered-unconfirmed
submit — the text DID reach the pane, so the owner's "raz za hodinu" rule makes
the NEXT attempt of this kind defer a full hour. That is what bounds the re-fire
to <= 1/hour; the confirm therefore lands on the first idle tick AFTER the floor,
never the very next sweep.

RED against the pre-#1023 tree: `delivered = ok OR delivered_unconfirmed` advances
the BASELINE on an unconfirmed submit, so a second idle tick never re-delivers.
GREEN once only a transcript-confirmed `ok` advances the baseline (while the floor
still bounds the re-fire).
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
                 out=None, user_authored=False, nudge=None, state=None):
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

    def test_unconfirmed_advances_floor_but_not_baseline(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        with m.patch.object(wd, "send_verified", _SV(result=False, unconfirmed=True)):
            logs = self._run(qrecs, state)
        # baseline NOT advanced -> the arrival re-detects (after the floor)
        self.assertEqual(qrecs[self.sid]["base"], [1], logs)
        # 🟡4: the per-kind floor IS stamped -> the re-fire is bounded to 1/hour
        self.assertEqual(state["nudge_cadence"][self.sid]["queue-arrival"], NOW)
        self.assertTrue(any("delivered-unconfirmed" in ln for ln in logs), logs)

    def test_re_confirm_is_deferred_by_the_floor_then_confirms(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        # tick 1: unconfirmed -> baseline unadvanced, floor stamped at NOW
        with m.patch.object(wd, "send_verified", _SV(result=False, unconfirmed=True)):
            self._run(qrecs, state)
        self.assertEqual(qrecs[self.sid]["base"], [1])
        # tick 2 (NOW+120, inside the 1h floor): HELD, no re-delivery
        sv2 = _SV(result=True, unconfirmed=False)
        with m.patch.object(wd, "send_verified", sv2):
            logs2 = self._run(qrecs, state, now=NOW + 120)
        self.assertEqual(sv2.n, 0, logs2)               # never re-typed inside the hour
        self.assertEqual(qrecs[self.sid]["base"], [1])
        self.assertTrue(any("hold:floor" in ln for ln in logs2), logs2)
        # tick 3 (past the floor): the send confirms -> baseline advances + floor re-stamped
        after = NOW + 3600 + 60
        with m.patch.object(wd, "send_verified", _SV(result=True, unconfirmed=False)):
            self._run(qrecs, state, now=after)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2])
        self.assertEqual(state["nudge_cadence"][self.sid]["queue-arrival"], after)


class TestBatchQaUnconfirmed1023(unittest.TestCase):
    """#1023 🔵6 — the #923 batch delivery block in `deliver_goal` must NOT run
    queue-arrival's post-delivery callback on a `delivered-unconfirmed` batch
    (baseline stays OLD, re-confirmed later), while the per-kind floor
    (`mark_batch_sent`) is still stamped for ALL included kinds.

    The batch delivery is an inline block (not a standalone function), so this is
    a source-lock over `goal.goal_lane_sweep` — mutation-verified (revert the
    guard -> RED). Its behavioral sibling (the single-path 🟡4 floor stamp +
    baseline skip) is covered by TestUnconfirmedNotTerminal above.
    """

    def _src(self):
        import inspect
        from watchdog import goal
        return " ".join(inspect.getsource(goal.goal_lane_sweep).split())

    def test_batch_skips_queue_arrival_callback_on_unconfirmed(self):
        src = self._src()
        # the guard that skips queue-arrival's baseline callback on unconfirmed
        self.assertIn('_bc == "queue-arrival" and not _bok', src)
        self.assertIn('_qa_unconfirmed = (not _bok) and "queue-arrival" in _incl_set',
                      src)

    def test_batch_still_stamps_the_floor_for_all_included(self):
        src = self._src()
        # mark_batch_sent (the per-kind floor for ALL included kinds) still runs
        # inside the delivered branch, bounding re-fire to <= 1/hour (🟡4)
        self.assertIn("_nudge_gate.mark_batch_sent(state, sid, _incl, now)", src)


if __name__ == "__main__":
    unittest.main()
