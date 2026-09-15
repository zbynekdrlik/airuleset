"""#1023 REOPEN — a `delivered-unconfirmed` queue-arrival keystroke must stamp
the per-kind 60-min floor so the SAME kind cannot be re-typed into the SAME pane
within the hour, in BOTH delivery paths (the direct `queue_arrival_recheck`
rider AND the `goal.goal_lane_sweep` #923 batch block), and the journal must not
lie that the stamp is "since last confirmed".

Incident (gk, 2026-09-15): pane `zbynek:1.0` (gk-infra, armed /goal, idle) got
`batch-nudge … queue-arrival (delivered-unconfirmed)` at 08:50:11 AND AGAIN at
08:52:13 — two keystrokes 122 s apart for ONE kind on ONE pane — before the
floor finally held at 08:53. The owner bound (verbatim): „Takyto nudge by nemal
chodit castejsie nez raz za hodinu!"

The floor-stamping on a delivered-unconfirmed send landed in the #1023
fix-forward (batch: `mark_batch_sent` under `if _bdeliv:`; direct:
`queue_arrival_recheck.py` `mark_sent` in the delivered-unconfirmed branch), but
the batch path was only SOURCE-locked (a grep over `goal_lane_sweep`'s source),
never exercised end-to-end. These tests exercise the REAL code path and are
MUTATION-VERIFIED: reverting the batch mark to confirmed-only
(`if _bdeliv:` → `if _bok:`) makes `test_batch_*` re-deliver at T+120 (RED),
byte-for-byte the incident's journal shape.
"""
import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402,F401
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import nudge_gate  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import session_status as ss  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
FLOOR = 3600           # nudge_gate._min_interval() — the owner's 1 h strop
CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"


def _rec(id_, kind="ticket", num=None):
    num = num if num is not None else id_
    return {"id": id_, "kind": kind, "num": num,
            "permalink": "https://x/%d" % num, "tag": "infra"}


class _UnconfirmedSV:
    """A `send_verified` stub reproducing the `delivered-unconfirmed` outcome:
    the box cleared (submit accepted/queued) but the transcript `❯ nudge:` turn
    was not proven inside the window — `out["delivered_unconfirmed"]=True`,
    return False. This is the exact 08:50/08:52 shape."""

    def __init__(self):
        self.calls = 0

    def __call__(self, pid, text, run=None, tpath=None, sleep_fn=None, logs=None,
                 out=None, user_authored=False, nudge=None, state=None,
                 skip_confirm=False):
        self.calls += 1
        if isinstance(out, dict):
            out["delivered_unconfirmed"] = True
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.projp = Path(self._proj.name)
        self.tpath = _write_marker_transcript(self.projp, CWD, "sess-1023ff")
        self.sid = self.tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(self.tpath, (old, old))

    def _tmux(self):
        return DeliverGoalFakeTmux([("%9", "claude", CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath)


class TestBatchDeliveredUnconfirmedStampsFloor(_Base):
    """The #923 batch delivery block: a delivered-unconfirmed batch nudge stamps
    the per-kind floor so a re-collection cannot re-type within the hour."""

    def _armed_sweep(self, now, state, sv):
        # ARMED via the structured goal_mark (the #486 G6 authoritative signal)
        # + a fresh heartbeat, so the sweep runs the #923 batch delivery block.
        pth = ss.status_path(self.sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps(
            {"schema": 1, "sid": self.sid, "kind": "main", "last_turn": "stop",
             "ts": now, "cwd": CWD, "marker": "working", "goal_armed": True}),
            encoding="utf-8")
        state.setdefault("goal_mark", {})[self.sid] = {
            "off": 0, "mark": {"state": "set", "ts": now}}
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="sequential"), \
                m.patch.object(wd, "_owner_disabled", return_value=False), \
                m.patch.object(wd, "nudges_enabled",
                               lambda kind=None: kind == "queue-arrival"), \
                m.patch.object(wd, "send_verified", sv):
            return goal.goal_lane_sweep(
                now, run=self._tmux(), projects_dir=self.projp, state=state,
                dry_run=False, handled=set(), backlog_fetch=lambda cwd: 0,
                queue_fetch=None,
                infra_queue_fetch=lambda cwd: [_rec(1), _rec(6883)],
                resolve_role_fn=lambda cwd: "infra",
                sleep_fn=lambda *a, **k: None)

    def test_delivered_unconfirmed_stamps_floor_and_holds_at_t_plus_120(self):
        state = {"queue_arrival": {
            self.sid: {"base": [1], "first_seen": NOW - DAY}}}
        # Sweep 1: the arrival fires ONE batch nudge, delivered-unconfirmed.
        sv1 = _UnconfirmedSV()
        logs1 = self._armed_sweep(NOW, state, sv1)
        self.assertEqual(sv1.calls, 1, logs1)
        self.assertTrue(any("batch-nudge" in ln and "delivered-unconfirmed" in ln
                            for ln in logs1), logs1)
        # The per-kind floor IS stamped even though the submit was unconfirmed.
        self.assertEqual(
            state["nudge_cadence"][self.sid]["queue-arrival"], NOW, logs1)
        # Sweep 2, 122 s later (the incident's 08:50 -> 08:52 gap): NO second
        # keystroke — the floor holds. This is the incident, prevented.
        sv2 = _UnconfirmedSV()
        logs2 = self._armed_sweep(NOW + 122, state, sv2)
        self.assertEqual(sv2.calls, 0,
                         "a second queue-arrival keystroke 122 s later violates "
                         "the owner's 1/hour rule\n" + "\n".join(logs2))
        self.assertFalse(any("batch-nudge" in ln for ln in logs2), logs2)

    def test_both_gate_sites_agree_at_t_plus_120(self):
        # The reopen's exact RED contract: BOTH read the SAME mark.
        state = {"queue_arrival": {
            self.sid: {"base": [1], "first_seen": NOW - DAY}}}
        self._armed_sweep(NOW, state, _UnconfirmedSV())
        t = NOW + 120
        self.assertNotIn("queue-arrival",
                         nudge_gate.batch_eligible(state, self.sid, t),
                         "batch_eligible must EXCLUDE queue-arrival within the floor")
        self.assertFalse(
            nudge_gate.gate_ok(state, self.sid, "queue-arrival", t),
            "gate_ok must be False for queue-arrival within the floor")


class TestDirectDeliveredUnconfirmedStampsFloor(_Base):
    """The direct rider path (a non-armed infra pane / the floor-eligible-single
    path): a delivered-unconfirmed send stamps the floor, both gates hold."""

    def _run_direct(self, now, qrecs, state, sv):
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "nudges_enabled",
                               lambda kind=None: kind == "queue-arrival"), \
                m.patch.object(wd, "send_verified", sv):
            return qa.goal_queue_arrival_recheck(
                now, self._tmux(), qrecs, self.sid, CWD, "%9", self.tpath,
                "sess:0", False, set(), queue_fetch=lambda cwd: [1, 2],
                state=state, sleep_fn=lambda *a, **k: None)

    def test_delivered_unconfirmed_stamps_floor_and_holds(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        sv1 = _UnconfirmedSV()
        logs1 = self._run_direct(NOW, qrecs, state, sv1)
        self.assertEqual(sv1.calls, 1, logs1)
        self.assertTrue(any("delivered-unconfirmed" in ln for ln in logs1), logs1)
        # baseline NOT advanced (re-detect after the floor) but the FLOOR stamped.
        self.assertEqual(qrecs[self.sid]["base"], [1], logs1)
        self.assertEqual(
            state["nudge_cadence"][self.sid]["queue-arrival"], NOW, logs1)
        # 122 s later: the floor holds, no second keystroke.
        sv2 = _UnconfirmedSV()
        logs2 = self._run_direct(NOW + 122, qrecs, state, sv2)
        self.assertEqual(sv2.calls, 0, "\n".join(logs2))
        self.assertTrue(any("hold:floor" in ln for ln in logs2), logs2)
        # both gate sites agree within the floor
        t = NOW + 122
        self.assertNotIn("queue-arrival",
                         nudge_gate.batch_eligible(state, self.sid, t))
        self.assertFalse(nudge_gate.gate_ok(state, self.sid, "queue-arrival", t))


class TestFloorHoldWordingHonesty(unittest.TestCase):
    """#1023 reopen — the floor is stamped on a delivered-UNCONFIRMED send, so
    the journal clause must say "since last send", never "since last confirmed"
    (which is what misled the reopen's own reconstruction into believing the
    stamp only happens on a confirmed turn)."""

    def test_floor_hold_reason_says_since_last_send(self):
        st = {}
        nudge_gate.mark_sent(st, "s", "queue-arrival", NOW)
        reason = nudge_gate.floor_hold_reason(st, "s", "queue-arrival", NOW + 120)
        self.assertIn("hold:floor", reason)
        self.assertIn("since last send", reason,
                      "the mark is stamped on a delivered-UNCONFIRMED send, so "
                      "'confirmed' is a lie — say 'since last send'")
        self.assertNotIn("since last confirmed", reason)


if __name__ == "__main__":
    unittest.main()
