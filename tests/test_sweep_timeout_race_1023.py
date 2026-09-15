"""#1023 timeout-race (supervisor gk-journal find) — a sweep that runs past the
`api-watchdog.service` `TimeoutStartSec=2min` is KILLED by systemd BEFORE run_once
persists state at sweep END, so an in-memory `mark_sent` (a delivered keystroke's
per-kind floor) is lost and the next sweep re-types (the live 08:50/08:52 shape).

Three guards, all on existing primitives:
  (1) WRITE-THROUGH — a delivered keystroke's mark + baseline persist immediately
      via the existing atomic `save_state` (through run_once's `persist` callback),
      so a later kill cannot un-record them.
  (2) SWEEP-RELATIVE FETCH BUDGET — the rider skips the (up-to-60s) queue-arrival
      fetch with `hold:budget` + an UNTOUCHED baseline when too little sweep budget
      remains, so a fetch never runs into the 2-min kill.
  (3) SOFT-CAP LOG — run_once journals `sweep budget: <elapsed>s of <cap>s at
      <job>` when a sweep crosses the soft cap, so a kill has a named cause.

RED against the pre-fix tree: (1) the rider/batch make the mark only in memory —
a "killed" sweep (no end-of-sweep save) leaves the on-disk state floor-less and a
fresh sweep re-delivers; (2) the rider has no budget guard — it fetches regardless
of the remaining budget; (3) run_once emits no budget line.
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
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import session_status as ss  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"


def _rec(id_, kind="ticket", num=None):
    num = num if num is not None else id_
    return {"id": id_, "kind": kind, "num": num,
            "permalink": "https://x/%d" % num, "tag": "infra"}


class _UnconfirmedSV:
    """A `send_verified` stub returning `delivered-unconfirmed` (box cleared, the
    keystroke LANDED, transcript confirm raced) — the exact 08:50/08:52 shape."""

    def __init__(self):
        self.calls = 0

    def __call__(self, pid, text, run=None, tpath=None, sleep_fn=None, logs=None,
                 out=None, user_authored=False, nudge=None, state=None,
                 skip_confirm=False):
        self.calls += 1
        self.last_skip_confirm = skip_confirm
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
        self.tpath = _write_marker_transcript(self.projp, CWD, "sess-1023tr")
        self.sid = self.tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(self.tpath, (old, old))
        self.state_path = str(Path(self._sdir.name) / "state.json")

    def _tmux(self):
        return DeliverGoalFakeTmux([("%9", "claude", CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath)


# --------------------------------------------------------------------------- #
# (1) WRITE-THROUGH — the killed-sweep reproduction.
# --------------------------------------------------------------------------- #

class TestWriteThroughDirect(_Base):
    def _run_direct(self, now, qrecs, state, sv, persist):
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "nudges_enabled",
                               lambda kind=None: kind == "queue-arrival"), \
                m.patch.object(wd, "send_verified", sv):
            return qa.goal_queue_arrival_recheck(
                now, self._tmux(), qrecs, self.sid, CWD, "%9", self.tpath,
                "sess:0", False, set(), queue_fetch=lambda cwd: [1, 2],
                state=state, sleep_fn=lambda *a, **k: None,
                persist=persist)

    def test_delivered_unconfirmed_mark_is_on_disk_before_end_of_sweep_save(self):
        # The rider delivers (unconfirmed) and marks the floor; the WRITE-THROUGH
        # persist puts that mark on DISK immediately. Simulate the systemd kill by
        # NEVER doing an end-of-sweep save — only the write-through wrote disk.
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        state = {}
        persist = lambda: wd.save_state(self.state_path, state)  # noqa: E731
        sv = _UnconfirmedSV()
        self._run_direct(NOW, qrecs, state, sv, persist)
        self.assertEqual(sv.calls, 1)
        # The killed sweep persisted nothing else — but the write-through did:
        disk = wd.load_state(self.state_path)
        self.assertEqual(
            disk.get("nudge_cadence", {}).get(self.sid, {}).get("queue-arrival"),
            NOW,
            "the delivered-unconfirmed floor mark must be on DISK (write-through) "
            "so a systemd kill before the end-of-sweep save cannot lose it")
        # A fresh sweep at T+120s loading state FROM DISK holds the floor.
        disk2 = wd.load_state(self.state_path)
        sv2 = _UnconfirmedSV()
        logs2 = self._run_direct(NOW + 120, dict(qrecs), disk2, sv2, persist)
        self.assertEqual(sv2.calls, 0,
                         "a fresh sweep at T+120 must NOT re-deliver — the mark "
                         "survived the kill on disk\n" + "\n".join(logs2))
        self.assertTrue(any("hold:floor" in ln for ln in logs2), logs2)


class TestWriteThroughBatch(_Base):
    """The incident's exact path: the #923 batch delivery (`batch-nudge ...
    delivered-unconfirmed`) via goal_lane_sweep must persist the floor mark
    through the write-through, so a killed sweep does not re-deliver."""

    def _armed_sweep(self, now, state, sv, persist):
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
                sleep_fn=lambda *a, **k: None, persist=persist)

    def test_batch_delivered_unconfirmed_mark_survives_a_kill(self):
        state = {"queue_arrival": {
            self.sid: {"base": [1], "first_seen": NOW - DAY}}}
        persist = lambda: wd.save_state(self.state_path, state)  # noqa: E731
        sv = _UnconfirmedSV()
        logs = self._armed_sweep(NOW, state, sv, persist)
        self.assertTrue(any("batch-nudge" in ln and "delivered-unconfirmed" in ln
                            for ln in logs), logs)  # non-vacuous: batch path hit
        # Simulate the kill: NO end-of-sweep save. Only write-through wrote disk.
        disk = wd.load_state(self.state_path)
        self.assertEqual(
            disk.get("nudge_cadence", {}).get(self.sid, {}).get("queue-arrival"),
            NOW,
            "the batch floor mark must be on DISK after delivery so a mid-sweep "
            "systemd kill cannot lose it (the 08:50/08:52 re-delivery)")


# --------------------------------------------------------------------------- #
# (2) SWEEP-RELATIVE FETCH BUDGET.
# --------------------------------------------------------------------------- #

class TestFetchBudgetSkip(_Base):
    def _run(self, budget_left_fn, fetch_spy):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="review"), \
                m.patch.object(wd, "nudges_enabled",
                               lambda kind=None: kind == "queue-arrival"):
            logs = qa.goal_queue_arrival_recheck(
                NOW, self._tmux(), qrecs, self.sid, CWD, "%9", self.tpath,
                "sess:0", False, set(), queue_fetch=fetch_spy,
                state={}, sleep_fn=lambda *a, **k: None,
                budget_left_fn=budget_left_fn)
        return qrecs, logs

    def test_low_budget_skips_the_fetch_untouched_baseline_zero_gh(self):
        calls = []
        spy = lambda cwd: (calls.append(cwd), [1, 2, 3])[1]  # noqa: E731
        qrecs, logs = self._run(lambda: 5, spy)   # 5 s left
        self.assertTrue(any("hold:budget" in ln for ln in logs), logs)
        self.assertEqual(calls, [], "no fetch may run with 5 s of sweep budget left")
        self.assertEqual(qrecs[self.sid]["base"], [1],
                         "the baseline must be UNTOUCHED on a budget skip")

    def test_ample_budget_still_fetches(self):
        calls = []
        spy = lambda cwd: (calls.append(cwd), [1, 2])[1]  # noqa: E731
        _qrecs, logs = self._run(lambda: 200, spy)   # 200 s left
        self.assertFalse(any("hold:budget" in ln for ln in logs), logs)
        self.assertEqual(len(calls), 1, "an ample budget must NOT skip the fetch")

    def test_unmeasurable_budget_applies_no_guard(self):
        calls = []
        spy = lambda cwd: (calls.append(cwd), [1, 2])[1]  # noqa: E731
        _qrecs, logs = self._run(lambda: (_ for _ in ()).throw(RuntimeError()), spy)
        self.assertFalse(any("hold:budget" in ln for ln in logs), logs)
        self.assertEqual(len(calls), 1,
                         "a raising budget seam must fall back to no guard, not skip")


# --------------------------------------------------------------------------- #
# (3) SOFT-CAP BUDGET LOG in run_once.
# --------------------------------------------------------------------------- #

class TestSweepSoftCapLog(unittest.TestCase):
    def test_run_once_logs_a_budget_line_when_the_soft_cap_is_crossed(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_path = str(Path(tmp.name) / "state.json")
        # A fake monotonic clock: the FIRST call anchors sweep_start; every later
        # call is +200 s so the standalone-job loop sees elapsed >= SWEEP_SOFT_CAP_S.
        seq = {"n": 0}

        def clock():
            seq["n"] += 1
            return 1000.0 if seq["n"] == 1 else 1200.0

        logs = wd.run_once(now=NOW, dry_run=True, run=lambda *a, **k: "",
                           send_fn=lambda *a, **k: None,
                           projects_dir=tmp.name, state_path=state_path,
                           time_fn=clock)
        self.assertTrue(any(ln.startswith("sweep budget:") for ln in logs),
                        "run_once must journal a `sweep budget:` line when a sweep "
                        "crosses the soft cap\n" + "\n".join(logs[-8:]))


if __name__ == "__main__":
    unittest.main()
