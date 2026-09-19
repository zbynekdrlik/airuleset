"""#1089 — the LANE-FILL Stop gate must ENFORCE on the long-running sessions it
targets (fix-forward of #1078).

Root cause (#1078): `gates.lanefill._goal_armed` scanned the transcript on EVERY
Stop with `seed_goal_marker` (backward, capped at `GOAL_MARK_SEED_CAP_BYTES` =
32 MB). The sessions the gate targets are the 650–730 MB autopilot supervisors
whose `/goal` arm marker sits deeper than 32 MB from EOF → `unknown-past-cap` →
`_Unreadable` → fail-open (0 blocks ever on gk/montalu1). The watchdog already
tracks each session's newest `/goal` marker INCREMENTALLY (`goal_dark_watch`,
persisted per sid in `~/.claude/api-watchdog-state.json`), so the gate must read
THAT single source (a shared helper) instead of re-scanning.

Covers Approach 1 (the #1089 design comment):
  1. `watchdog.goal_scan.persisted_goal_mark(sid, state_path)` — the shared
     goal-mark reader; `_goal_armed` reads it FIRST, falls to the seed only when
     the record is missing/stale; `unknown-past-cap` + a watchdog `set` ⇒ ARMED
     (never `_Unreadable`); a genuinely absent record + `unknown-past-cap` stays
     fail-open.
  2. the per-Stop decision journal `~/.claude/lanefill/decisions.log`.
  3. `count_live_workers` classifies a foreground CI-wait worker `live`.
  4. the `lane-occupancy` nudge DELIVERY is retired (decision line kept).
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import gates.lanefill as lf  # noqa: E402
import watchdog  # noqa: E402
import watchdog.goal_scan as gs  # noqa: E402
import watchdog.transcripts as tr  # noqa: E402


WORKING = "…work in progress\n⏳ WORKING: still going"


def _armset_line():
    return json.dumps({"type": "user", "message": {"content":
                      "<local-command-stdout>Goal set: work the backlog"
                      "</local-command-stdout>"},
                      "timestamp": "2026-09-19T09:00:00Z"})


def _deep_arm_transcript(tmp, arm_line, pad_bytes):
    """Arm marker at the TOP, then `pad_bytes` of non-marker padding — so a
    cap-shrunk seed scan ends `unknown-past-cap` (the exact gk/montalu1 shape)."""
    p = os.path.join(tmp, "t.jsonl")
    pad = json.dumps({"type": "user", "message": {"content": "x" * 200}}) + "\n"
    with open(p, "w") as f:
        f.write(arm_line + "\n")
        written = 0
        while written < pad_bytes:
            f.write(pad)
            written += len(pad)
    return p


def _state_file(tmp, sid, mark_state, tmtime, off=0, mark_ts=None):
    """Write an `api-watchdog-state.json` carrying the dark-watch per-sid record
    exactly as `goal_dark_watch` persists it: state["goal_mark"][sid].

    `mark_ts` (a POSIX float, the same `ts` `_newest_marker` stamps onto a mark)
    lets a test set the record mark's timestamp so the #1089-forward 🟡-1
    tail-scan `newer` comparison (`_tail_overrides_cleared`) can be exercised on
    its timestamp branch; omitted → the record mark carries no `ts`, the shape a
    real dark-watch record with an un-timestamped marker leaves."""
    p = os.path.join(tmp, "api-watchdog-state.json")
    rec = {"off": off, "tmtime": tmtime, "pv": 2}
    if mark_state is not None:
        rec["mark"] = {"state": mark_state, "payload": "work the backlog"}
        if mark_ts is not None:
            rec["mark"]["ts"] = mark_ts
    else:
        rec["mark"] = None
    with open(p, "w") as f:
        json.dump({"goal_mark": {sid: rec}}, f)
    return p


def _arm_then_clear_transcript(tmp, pad_bytes, clear_ts="2026-09-19T10:00:00Z"):
    """`Goal set:` at the TOP, then `pad_bytes` of padding, then a LATER
    `Goal cleared:` near EOF — so a cheap tail-only `scan_goal_markers` finds the
    CLEAR as the newest marker (the exact #1089-forward 🟡-1 race shape: the
    watchdog dark-watch recorded `set`, a `/goal` clear landed since, the record
    lags by up to one sweep)."""
    p = os.path.join(tmp, "t.jsonl")
    pad = json.dumps({"type": "user", "message": {"content": "x" * 200}}) + "\n"
    cleared = json.dumps({"type": "user", "message": {"content":
                         "<local-command-stdout>Goal cleared: done"
                         "</local-command-stdout>"},
                         "timestamp": clear_ts}) + "\n"
    with open(p, "w") as f:
        f.write(_armset_line() + "\n")
        written = 0
        while written < pad_bytes:
            f.write(pad)
            written += len(pad)
        f.write(cleared)
    return p


# --------------------------------------------------------------------------- #
# Item 1 — persisted_goal_mark + _goal_armed reads the watchdog state FIRST.
# --------------------------------------------------------------------------- #
class TestPersistedGoalMark(unittest.TestCase):
    def test_reads_set_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = _state_file(tmp, "sid-A", "set", time.time())
            rec = gs.persisted_goal_mark("sid-A", state_path=sp)
            self.assertIsInstance(rec, dict)
            self.assertEqual(rec["mark"]["state"], "set")

    def test_missing_sid_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = _state_file(tmp, "sid-A", "set", time.time())
            self.assertIsNone(gs.persisted_goal_mark("sid-OTHER", state_path=sp))

    def test_missing_file_returns_none(self):
        self.assertIsNone(gs.persisted_goal_mark(
            "sid-A", state_path="/nonexistent/api-watchdog-state.json"))

    def test_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.json")
            Path(p).write_text("{not json")
            self.assertIsNone(gs.persisted_goal_mark("sid-A", state_path=p))

    def test_empty_sid_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = _state_file(tmp, "sid-A", "set", time.time())
            self.assertIsNone(gs.persisted_goal_mark("", state_path=sp))

    def test_default_state_path_is_watchdog_state_path(self):
        # single source: the default resolves to watchdog.STATE_PATH's file.
        self.assertEqual(Path(watchdog.STATE_PATH).name, "api-watchdog-state.json")


class TestGoalArmedReadsWatchdogFirst(unittest.TestCase):
    """The core #1089 fix: on the gk shape (arm deeper than the seed cap →
    `unknown-past-cap`) a FRESH watchdog `set` record makes the gate read ARMED,
    NOT fail-open."""

    def _payload(self, tp, sid="sid-A"):
        return json.dumps({"transcript_path": tp, "session_id": sid})

    def test_unknown_past_cap_plus_watchdog_set_is_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=200_000)
            sp = _state_file(tmp, "sid-A", "set", os.path.getmtime(tp))
            out = {}
            # shrink the seed cap so the arm is unknown-past-cap (the gk shape):
            with mock.patch.object(gs, "GOAL_MARK_TAIL_BYTES", 10_000), \
                 mock.patch.object(gs, "GOAL_MARK_SEED_CAP_BYTES", 50_000):
                # prove the seed itself cannot see the arm (the #1078 blind spot):
                _o, _m, status = gs.seed_goal_marker(tp)
                self.assertEqual(status, "unknown-past-cap")
                armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertTrue(armed)                       # ARMED, not _Unreadable
            self.assertEqual(out.get("src"), "watchdog")

    def test_fresh_watchdog_set_short_circuits_without_seed(self):
        # a FRESH set record skips the DEEP seed scan; #1089 fix-forward (🟡-1)
        # still does the CHEAP tail scan, and with no newer clear the `set`
        # stands (armed). The no-newer-marker case — see
        # test_fresh_set_still_does_cheap_tail_scan for the tail-scan assertion.
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=1000)
            sp = _state_file(tmp, "sid-A", "set", os.path.getmtime(tp))
            called = {"seed": False}
            _orig = gs.seed_goal_marker

            def _spy(*a, **k):
                called["seed"] = True
                return _orig(*a, **k)
            with mock.patch.object(gs, "seed_goal_marker", _spy):
                armed = lf._goal_armed(self._payload(tp), state_path=sp)
            self.assertTrue(armed)
            self.assertFalse(called["seed"])             # no DEEP seed rescan

    def test_fresh_set_ts_guard_keeps_armed_when_tail_clear_is_older(self):
        # ts guard: a rewound/skewed transcript whose tail `cleared` is OLDER
        # than the record's `set` ts must NOT un-arm (the record wins).
        with tempfile.TemporaryDirectory() as tmp:
            tp = _arm_then_clear_transcript(tmp, pad_bytes=1000,
                                            clear_ts="2026-09-19T10:00:00Z")
            sp = _state_file(tmp, "sid-A", "set", os.path.getmtime(tp),
                             mark_ts=9_999_999_999.0)   # yr 2286 — newer than clear
            out = {}
            armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertTrue(armed)                       # record set is newer
            self.assertEqual(out.get("src"), "watchdog")

    def test_fresh_watchdog_cleared_is_not_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=1000)
            sp = _state_file(tmp, "sid-A", "cleared", os.path.getmtime(tp))
            out = {}
            armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertFalse(armed)
            self.assertEqual(out.get("src"), "watchdog")

    # ---- #1089 fix-forward — Review B 🟡-1: the watchdog record LAGS the
    # transcript by up to one sweep, so a fresh `set` short-circuit that trusts
    # the record blindly spuriously ARMS (→ over-blocks) a session that CLEARED
    # its goal in the lag window. The fresh-`set` path must ALSO run the cheap
    # tail-only scan (last 4 MB) and let a NEWER tail `cleared` win.
    def test_fresh_set_but_newer_tail_clear_is_not_armed(self):
        # record says `set` (ts OLD), the transcript tail carries a LATER
        # `Goal cleared:` — the tail truth is newer → NOT armed.
        with tempfile.TemporaryDirectory() as tmp:
            tp = _arm_then_clear_transcript(tmp, pad_bytes=1000)
            sp = _state_file(tmp, "sid-A", "set", os.path.getmtime(tp),
                             mark_ts=1000.0)          # 1970 — older than the clear
            out = {}
            armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertFalse(armed)                   # the newer tail clear wins
            self.assertIn("tail", out.get("src", ""))

    def test_fresh_set_no_ts_record_newer_tail_clear_is_not_armed(self):
        # a record whose mark carries no `ts` (older dark-watch shape): the tail
        # scan returns the NEWEST marker in the recent window, so a tail
        # `cleared` there is authoritative → NOT armed.
        with tempfile.TemporaryDirectory() as tmp:
            tp = _arm_then_clear_transcript(tmp, pad_bytes=1000)
            sp = _state_file(tmp, "sid-A", "set", os.path.getmtime(tp))  # no ts
            out = {}
            armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertFalse(armed)
            self.assertIn("tail", out.get("src", ""))

    def test_fresh_set_still_does_cheap_tail_scan(self):
        # the fresh-`set` path must do the CHEAP tail scan (never the deep seed
        # scan); with no newer clear in the tail the `set` stands (armed).
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=1000)
            sp = _state_file(tmp, "sid-A", "set", os.path.getmtime(tp))
            called = {"seed": False, "scan": False}
            _seed = gs.seed_goal_marker
            _scan = gs.scan_goal_markers

            def _seed_spy(*a, **k):
                called["seed"] = True
                return _seed(*a, **k)

            def _scan_spy(*a, **k):
                called["scan"] = True
                return _scan(*a, **k)
            with mock.patch.object(gs, "seed_goal_marker", _seed_spy), \
                 mock.patch.object(gs, "scan_goal_markers", _scan_spy):
                armed = lf._goal_armed(self._payload(tp), state_path=sp)
            self.assertTrue(armed)                    # no newer clear → set stands
            self.assertFalse(called["seed"])          # no DEEP seed rescan
            self.assertTrue(called["scan"])           # but the CHEAP tail scan ran

    def test_missing_record_falls_back_to_seed(self):
        # no watchdog record → the seed scan governs (arm within default cap).
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=1000)
            sp = _state_file(tmp, "OTHER", "set", time.time())     # different sid
            out = {}
            armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertTrue(armed)
            self.assertEqual(out.get("src"), "seed")

    def test_stale_record_falls_back_to_seed(self):
        # the record's snapshot is > 10 min behind the current transcript mtime →
        # STALE → the seed governs; a definite cleared marker overrides the stale set.
        with tempfile.TemporaryDirectory() as tmp:
            cleared = json.dumps({"type": "user", "message": {"content":
                                 "<local-command-stdout>Goal cleared: done"
                                 "</local-command-stdout>"},
                                 "timestamp": "2026-09-19T09:00:00Z"})
            tp = _deep_arm_transcript(tmp, cleared, pad_bytes=1000)
            sp = _state_file(tmp, "sid-A", "set",
                             os.path.getmtime(tp) - 3600)          # 1 h stale
            armed = lf._goal_armed(self._payload(tp), state_path=sp)
            self.assertFalse(armed)                                # seed cleared wins

    def test_stale_set_record_plus_unknown_past_cap_is_armed(self):
        # STALE set record + the seed cannot disprove it (unknown-past-cap) →
        # honour the record (armed), never fail-open.
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=200_000)
            sp = _state_file(tmp, "sid-A", "set",
                             os.path.getmtime(tp) - 3600)          # stale
            out = {}
            with mock.patch.object(gs, "GOAL_MARK_TAIL_BYTES", 10_000), \
                 mock.patch.object(gs, "GOAL_MARK_SEED_CAP_BYTES", 50_000):
                armed = lf._goal_armed(self._payload(tp), state_path=sp, out=out)
            self.assertTrue(armed)
            self.assertIn("watchdog", out.get("src", ""))

    def test_absent_record_plus_unknown_past_cap_stays_fail_open(self):
        # the genuinely-absent case is UNCHANGED (#1078): _Unreadable → fail-open.
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=200_000)
            sp = _state_file(tmp, "OTHER", "set", time.time())     # no record for sid-A
            with mock.patch.object(gs, "GOAL_MARK_TAIL_BYTES", 10_000), \
                 mock.patch.object(gs, "GOAL_MARK_SEED_CAP_BYTES", 50_000):
                with self.assertRaises(lf._Unreadable):
                    lf._goal_armed(self._payload(tp), state_path=sp)

    def test_no_session_id_uses_seed_only(self):
        # a payload with no session_id (the DeepScan tests' shape) never reads state.
        with tempfile.TemporaryDirectory() as tmp:
            tp = _deep_arm_transcript(tmp, _armset_line(), pad_bytes=1000)
            armed = lf._goal_armed(json.dumps({"transcript_path": tp}))
            self.assertTrue(armed)


# --------------------------------------------------------------------------- #
# Item 2 — the per-Stop decision journal.
# --------------------------------------------------------------------------- #
class TestDecisionJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lf-dec-")
        self._env = mock.patch.dict(os.environ,
                                    {lf._LANEFILL_DIR_ENV: self.tmp})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _log(self):
        p = os.path.join(self.tmp, "decisions.log")
        return Path(p).read_text() if os.path.exists(p) else ""

    def _run(self, msg, **fakes):
        code = None
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            try:
                lf.run(json.dumps({"last_assistant_message": msg, "cwd": "/repo",
                                   "session_id": "s1", "transcript_path": "/t.jsonl"}),
                       **fakes)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def _fakes(self, count=6, live=1, cap=4, mode="parallel", armed=True):
        tickets = [(100 + i, "t%d" % i) for i in range(count)]
        return dict(goal_fn=lambda p: armed, mode_fn=lambda c: mode,
                    cap_fn=lambda c: cap, live_fn=lambda p, c: live,
                    quals_fn=lambda p, c: (count, tickets))

    def test_block_writes_journal_line(self):
        code, _ = self._run(WORKING, **self._fakes(count=6, live=1, cap=4))
        self.assertEqual(code, 2)
        line = self._log()
        self.assertIn("verdict=block", line)
        self.assertIn("mode=parallel", line)
        self.assertIn("cap=4", line)
        self.assertIn("dispatchable=6/", line)
        self.assertIn("armed=true/", line)

    def test_allow_writes_journal_line(self):
        code, _ = self._run(WORKING, **self._fakes(count=6, live=4, cap=4))
        self.assertIsNone(code)
        self.assertIn("verdict=allow", self._log())

    def test_not_armed_writes_allow_line(self):
        code, _ = self._run(WORKING, **self._fakes(armed=False))
        self.assertIsNone(code)
        self.assertIn("armed=false/", self._log())

    def test_sequential_writes_allow_line(self):
        code, _ = self._run(WORKING, **self._fakes(mode="sequential"))
        self.assertIsNone(code)
        self.assertIn("mode=sequential", self._log())

    def test_unreadable_writes_journal_line(self):
        fakes = self._fakes()
        fakes["quals_fn"] = lambda p, c: (_ for _ in ()).throw(lf._Unreadable("meta boom"))
        code, _ = self._run(WORKING, **fakes)
        self.assertIsNone(code)
        self.assertIn("verdict=unreadable", self._log())

    def test_non_terminal_turn_writes_nothing(self):
        # a non-⏳/✅ turn is not a gate evaluation — no journal noise.
        code, _ = self._run("a status\n❓ NEEDS YOU: pick?", **self._fakes())
        self.assertIsNone(code)
        self.assertEqual(self._log(), "")

    def test_journal_line_is_single_line_capped_shape(self):
        self._run(WORKING, **self._fakes(count=6, live=1, cap=4))
        lines = [ln for ln in self._log().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        # ISO8601 timestamp lead + all fields present, token-free.
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertIn("live=1", lines[0])

    def test_fmt_live_breakdown(self):
        ev = [tr.WorkerLane("a", "live", 1, None, ""),
              tr.WorkerLane("b", "finished", 2, None, ""),
              tr.WorkerLane("c", "wedged", 3, None, "err"),
              tr.WorkerLane("d", "stale", 9999, None, "")]
        s = lf._fmt_live(1, ev)
        self.assertEqual(s, "1(f=1 s=1 w=1)")

    def test_fmt_live_no_evidence_is_plain_int(self):
        self.assertEqual(lf._fmt_live(3, None), "3")

    def test_fmt_live_dash_when_unknown(self):
        self.assertEqual(lf._fmt_live("-", None), "-")

    def test_fmt_live_surfaces_unreadable_bucket(self):
        # #1089 F-7: an `unreadable` lane is a real non-live exclusion → shown.
        ev = [tr.WorkerLane("a", "live", 1, None, ""),
              tr.WorkerLane("b", "unreadable", 2, None, ""),
              tr.WorkerLane("c", "finished", 3, None, "")]
        self.assertEqual(lf._fmt_live(1, ev), "1(f=1 s=0 w=0 u=1)")

    def test_fmt_live_omits_unreadable_when_zero(self):
        ev = [tr.WorkerLane("a", "live", 1, None, ""),
              tr.WorkerLane("b", "finished", 2, None, "")]
        self.assertEqual(lf._fmt_live(1, ev), "1(f=1 s=0 w=0)")


# --------------------------------------------------------------------------- #
# Item 3 — count_live_workers classifies a foreground CI-wait worker `live`.
# --------------------------------------------------------------------------- #
class TestLiveCountClassification(unittest.TestCase):
    """The 20:15 gk read showed 3 fresh subagent transcripts and live=0; verify
    the three canonical shapes so a saturated session is never blocked for
    '0 live' (a false block on a genuinely-busy box)."""

    FRESH = 15 * 60

    def _one_worker(self, entries):
        tmp = tempfile.mkdtemp(prefix="clw-")
        cwd = os.path.join(tmp, "repo")
        sid = "s1"
        sub = (Path(tmp) / "proj" / tr.encode_project_dir(cwd) / sid / "subagents")
        sub.mkdir(parents=True)
        p = sub / "agent-w.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in entries))
        now = time.time()
        os.utime(p, (now, now))
        return tmp, cwd, sid

    def _count(self, tmp, cwd, sid):
        return tr.count_live_workers(os.path.join(tmp, "proj"), cwd, sid,
                                     time.time(), self.FRESH)

    def test_inflight_tool_use_is_live(self):
        tmp, cwd, sid = self._one_worker([
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "polling CI"},
                {"type": "tool_use", "id": "t", "name": "Bash",
                 "input": {"command": "gh run view"}}]}}])
        count, ev = self._count(tmp, cwd, sid)
        self.assertEqual(count, 1)
        self.assertEqual([lane.state for lane in ev], ["live"])

    def test_final_text_end_turn_is_finished(self):
        tmp, cwd, sid = self._one_worker([
            {"type": "assistant", "message": {"role": "assistant",
             "stop_reason": "end_turn", "content": [
                {"type": "text", "text": "All done."}]}}])
        count, ev = self._count(tmp, cwd, sid)
        self.assertEqual(count, 0)
        self.assertEqual([lane.state for lane in ev], ["finished"])

    def test_api_error_is_wedged(self):
        tmp, cwd, sid = self._one_worker([
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "API Error: 500"}]},
             "isApiErrorMessage": True}])
        count, ev = self._count(tmp, cwd, sid)
        self.assertEqual(count, 0)
        self.assertEqual([lane.state for lane in ev], ["wedged"])


# --------------------------------------------------------------------------- #
# Item 4 — the lane-occupancy nudge DELIVERY is retired (decision line kept).
# --------------------------------------------------------------------------- #
class TestLaneOccupancyDeliveryRetired(unittest.TestCase):
    def test_kind_never_calls_a_delivery_primitive(self):
        """AST: `goal_lane_occupancy_nudge` must not reference any pane-keystroke
        delivery primitive (`send_verified` / `_try_stash_nudge` /
        `deliver_with_stash` / `submit_own_draft_verified`) — the Stop gate is the
        refill lever now (#1089)."""
        import ast
        import inspect
        import watchdog.goal as goal
        src = inspect.getsource(goal.goal_lane_occupancy_nudge)
        tree = ast.parse(src.lstrip())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        for banned in ("send_verified", "_try_stash_nudge", "deliver_with_stash",
                       "submit_own_draft_verified"):
            self.assertNotIn(banned, names,
                             "lane-occupancy delivery retired: must not call %s" % banned)

    def test_registry_documents_retirement(self):
        import watchdog.tmux_io as tio
        src = Path(tio.__file__).read_text()
        # the kind stays a recognized observability identity but its DELIVERY is
        # retired — the registry must say so where the kind is listed (#1089).
        self.assertIn("lane-occupancy", src)
        self.assertRegex(src, r"lane-occupancy.*(RETIRED|retired|#1089)"
                         r"|(RETIRED|retired|#1089).*lane-occupancy")


if __name__ == "__main__":
    unittest.main()
