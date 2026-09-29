"""#1178 — an idle supervisor learns of NEW tickets in its OWN workable backlog.

INCIDENT (controller, 2026-09-29): the airuleset supervisor ended its /goal with
stop condition (B) at 01:xx. The owner filed #1176 at 06:29 and the montalu4
stream filed #1177 at 06:45. Both were workable in `core-quals --list`, yet the
idle supervisor never noticed. Three causes, one per part of the design
(issuecomment-5885185094, Approach 1):

(a) the #733 queue-arrival rider diffed the gk HAND-OFF union
    (`ready-for-review ∪ needs-gatekeeper ∪ prio:bounce`), not the box's own
    workable set — an owner-filed ticket never entered it;
(b) the rider only rode ARMED /goal panes — a pane whose goal ENDED was never a
    target;
(c) on the controller the fetch returned None on every sweep: `gh` lives only in
    `~/.local/bin` there and the systemd unit's PATH has no `~/.local/bin`, so
    every watchdog gh call (and the #1067 quals snapshot refresher it spawns)
    died with FileNotFoundError, silently (`skip:undetermined (state unchanged)`).

RED against the pre-#1178 tree: the snapshot carries no `i_titles`, the
refresher drops the derivation's error text, `_watchdog_queue_fetch` shells gh,
the rider refuses a reduced-authority box and only an ARMED pane reaches it,
`cmd_watchdog` never repairs PATH, and an undetermined fetch is never reported.
"""
import contextlib
import io
import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_quals_snapshot  # noqa: E402
import cli_ticket_state  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import ops_wait_refresh as owref  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402
from watchdog import session_status as ss  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
HOUR = 3600
CWD = "/home/newlevel/devel/qa1178"


def _recs(*pairs):
    return [{"id": n, "title": t} for n, t in pairs]


# --------------------------------------------------------------------------- #
# (a) the arrival set IS the box's own workable set (the quals snapshot "I").
# --------------------------------------------------------------------------- #

class TestFullAuthoritySetKeepsHandoffLabels(unittest.TestCase):
    """Regression lock (#733 → #1178): the full-authority workable set already
    contains every gk hand-off, so moving the arrival signal to it keeps #733's
    case covered. A bare `prio:bounce` stays OUT on purpose (#307 — a stream's
    own rework; the stream's own slice and #1066's bounce rider carry it)."""

    def test_obligation_quals_union_the_handoff_labels(self):
        q = cli_quals._obligation_quals()
        self.assertIn("label:ready-for-review", q)
        self.assertIn("label:needs-gatekeeper", q)
        self.assertNotIn("label:prio:bounce", q)

    def test_a_stream_handoff_is_in_the_full_box_I_bucket(self):
        def row(n, *labels):
            return {"number": n, "title": "t%d" % n,
                    "labels": [{"name": lb} for lb in labels]}
        rows = {5177: row(5177, "stream:david", "ready-for-review"),
                5310: row(5310, "stream:montalu", "needs-gatekeeper"),
                1176: row(1176, "bug")}
        got = cli_ticket_state.bucketize(rows, cli_ticket_state.TicketFacts(),
                                         cli_ticket_state.Box())
        self.assertEqual(sorted(got["I"]), [1176, 5177, 5310])


class TestSnapshotCarriesTitles(unittest.TestCase):
    def test_emit_snapshot_json_adds_i_titles(self):
        rows = {1177: {"number": 1177, "title": "Money gate tunel"},
                1176: {"number": 1176, "title": "Checkout freshness"}}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_snapshot.emit_snapshot_json(
                rows, {}, "/r", [], None,
                lambda *a, **k: None, lambda r, root: (2, None))
        snap = json.loads(buf.getvalue())
        self.assertEqual(snap["i_members"], [1176, 1177])
        self.assertEqual(snap["i_titles"], {"1176": "Checkout freshness",
                                            "1177": "Money gate tunel"})

    def test_parse_snapshot_keeps_valid_titles_drops_malformed(self):
        base = {"open_count": 1, "i_members": [7], "dispatchable_count": 1,
                "dispatchable_reason": None, "ops_wait_members": []}
        ok = owref.parse_snapshot(json.dumps(dict(base, i_titles={"7": "x"})))
        self.assertEqual(ok["i_titles"], {"7": "x"})
        bad = owref.parse_snapshot(json.dumps(dict(base, i_titles=["x"])))
        self.assertIsNone(bad["i_titles"])


class _Home(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        p = m.patch.dict(os.environ, {"HOME": self._td.name})
        p.start()
        self.addCleanup(p.stop)
        owref._PROBLEMS.clear()

    def _write(self, obj, cwd=CWD):
        with open(owref.cache_path(cwd), "w") as f:
            json.dump(obj, f)


class TestWorkableRecords(_Home):
    def _good(self, **kw):
        e = {"ts": NOW - 10, "v": owref.SNAPSHOT_VERSION, "members": [],
             "members_ts": NOW - 10, "open_count": 2, "i_members": [1176, 1177],
             "i_titles": {"1176": "a", "1177": "b"},
             "dispatchable_count": 2, "dispatchable_reason": None}
        e.update(kw)
        return e

    def test_good_snapshot_yields_records_with_titles(self):
        self._write(self._good())
        out = owref.workable_records(CWD, "core-quals", now=NOW,
                                     spawn_fn=m.Mock(), alive_fn=lambda c: False)
        self.assertEqual(out, _recs((1176, "a"), (1177, "b")))

    def test_missing_titles_still_yield_ids(self):
        self._write(self._good(i_titles=None))
        out = owref.workable_records(CWD, "core-quals", now=NOW,
                                     spawn_fn=m.Mock(), alive_fn=lambda c: False)
        self.assertEqual(out, _recs((1176, ""), (1177, "")))

    def test_failing_snapshot_is_none_and_names_the_reason(self):
        self._write({"ts": NOW - 5, "error": True, "fail_streak": 267,
                     "error_detail": "core-quals: a gh query failed"})
        out = owref.workable_records(CWD, "core-quals", now=NOW,
                                     spawn_fn=m.Mock(), alive_fn=lambda c: False)
        self.assertIsNone(out)
        why = owref.problem(CWD)
        self.assertIn("267", why)
        self.assertIn("a gh query failed", why)

    def test_no_i_members_is_none_never_an_empty_set(self):
        # an older snapshot without i_members must read UNDETERMINED — an empty
        # list would re-seed the baseline and swallow the next real arrivals.
        self._write(self._good(i_members=None))
        self.assertIsNone(owref.workable_records(
            CWD, "core-quals", now=NOW, spawn_fn=m.Mock(),
            alive_fn=lambda c: False))


class TestRefresherKeepsErrorText(_Home):
    def test_error_detail_recorded_from_child_stderr(self):
        owref.run_refresh_child(
            CWD, "core-quals", owref.cache_path(CWD), owref.pid_path(CWD),
            "/x/airuleset.py", backoff_fn=lambda: 0,
            run_fn=lambda *a: (1, "", "Traceback…\ncore-quals: a gh query "
                               "failed — this is NOT a reliable 0\n"))
        with open(owref.cache_path(CWD)) as f:
            entry = json.load(f)
        self.assertTrue(entry["error"])
        self.assertEqual(entry["error_detail"],
                         "core-quals: a gh query failed — this is NOT a reliable 0")

    def test_ok_run_stores_i_titles(self):
        out = json.dumps({"open_count": 1, "i_members": [9],
                          "i_titles": {"9": "nový"}, "dispatchable_count": 1,
                          "dispatchable_reason": None, "ops_wait_members": []})
        owref.run_refresh_child(
            CWD, "core-quals", owref.cache_path(CWD), owref.pid_path(CWD),
            "/x/airuleset.py", backoff_fn=lambda: 0, run_fn=lambda *a: (0, out))
        with open(owref.cache_path(CWD)) as f:
            self.assertEqual(json.load(f)["i_titles"], {"9": "nový"})


class TestProductionFetchReadsTheSnapshot(unittest.TestCase):
    def test_fetch_reads_the_quals_snapshot_and_never_shells_gh(self):
        seen = {}

        def fake_records(cwd, cmd_name, argv0=None, **kw):
            seen["args"] = (cwd, cmd_name)
            return _recs((1176, "a"))

        with m.patch("airuleset._watchdog_quals_cmd", return_value="slice-quals"), \
                m.patch.object(owref, "workable_records", fake_records), \
                m.patch("subprocess.run", side_effect=AssertionError("gh")):
            out = airuleset._watchdog_queue_fetch("/r")
        self.assertEqual(out, _recs((1176, "a")))
        self.assertEqual(seen["args"], ("/r", "slice-quals"))

    def test_unresolvable_authority_is_none(self):
        with m.patch("airuleset._watchdog_quals_cmd",
                     side_effect=RuntimeError("x")):
            self.assertIsNone(airuleset._watchdog_queue_fetch("/r"))


# --------------------------------------------------------------------------- #
# (c) the controller's silent skip: PATH repair + a visible undetermined.
# --------------------------------------------------------------------------- #

class TestWatchdogPath(unittest.TestCase):
    def test_path_fix_puts_user_local_bin_first(self):
        with TemporaryDirectory() as home, m.patch.dict(
                os.environ, {"HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin"}):
            airuleset._watchdog_path_fix()
            parts = os.environ["PATH"].split(":")
        self.assertEqual(parts[0], os.path.join(home, ".local", "bin"))
        self.assertIn("/usr/bin", parts)

    def test_path_fix_is_idempotent(self):
        with TemporaryDirectory() as home:
            lb = os.path.join(home, ".local", "bin")
            with m.patch.dict(os.environ, {"HOME": home, "PATH": lb + ":/bin"}):
                airuleset._watchdog_path_fix()
                self.assertEqual(os.environ["PATH"], lb + ":/bin")

    def test_cmd_watchdog_applies_it_before_run_once(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("_watchdog_path_fix()", src)
        self.assertLess(src.index("_watchdog_path_fix()"),
                        src.index("logs = run_once("))


class TestUndeterminedIsVisible(unittest.TestCase):
    def _sweep(self, state, now, fetch=lambda cwd: None):
        return qa.goal_queue_arrival_recheck(
            now, DeliverGoalFakeTmux([], GOAL_IDLE_CAP), {}, "sid-u", CWD, "%1",
            None, "box:0.0", False, set(), queue_fetch=fetch, state=state,
            sleep_fn=lambda *a, **k: None)

    def test_persistent_undetermined_warns_once_per_hour_and_shows_in_status(self):
        state = {}
        lines = []
        for i in range(0, 70):          # one sweep a minute for 70 minutes
            lines += self._sweep(state, NOW + i * 60)
        warns = [ln for ln in lines if ln.startswith("WARN queue-arrival")]
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("undetermined since", warns[0])
        lines = self._sweep(state, NOW + 3 * HOUR)
        self.assertEqual(
            len([ln for ln in lines if ln.startswith("WARN queue-arrival")]), 1)
        with TemporaryDirectory() as home:
            p = Path(home) / ".claude" / "api-watchdog-state.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(state))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset._print_nudges_status(home=home)
        self.assertIn("queue-arrival: undetermined since", buf.getvalue())

    def test_a_determined_fetch_clears_the_health_record(self):
        state = {}
        for i in range(0, 20):
            self._sweep(state, NOW + i * 60)
        self.assertIn(CWD, state.get("queue_arrival_health", {}))
        self._sweep(state, NOW + 30 * 60, fetch=lambda cwd: [1])
        self.assertNotIn(CWD, state.get("queue_arrival_health", {}))

    def test_a_short_blip_never_warns(self):
        state = {}
        lines = []
        for i in range(0, 3):
            lines += self._sweep(state, NOW + i * 60)
        self.assertFalse([ln for ln in lines if ln.startswith("WARN")])


# --------------------------------------------------------------------------- #
# the rider on the own-workable set (any authority, titled nudge).
# --------------------------------------------------------------------------- #

class TestRiderOwnSet(unittest.TestCase):
    def setUp(self):
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, CWD, "sess-1178")
        self.sid = self.tpath.stem

    def _run(self, qrecs, fetch, authority, state=None):
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=self.tpath)
        with m.patch("airuleset.resolve_authority", return_value=authority):
            logs = qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, CWD, "%9", self.tpath, "box:0.0",
                False, set(), queue_fetch=fetch,
                state=state if state is not None else {},
                sleep_fn=lambda *a, **k: None)
        return logs, tmux

    def test_reduced_authority_box_now_watches_its_own_slice(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - HOUR}}
        logs, tmux = self._run(qrecs, lambda c: _recs((1, "x"), (1177, "Tunel")),
                               "fork-no-merge")
        self.assertIn("#1177", "".join(tmux.typed_texts()), logs)

    def test_nudge_names_title_and_points_at_autopilot(self):
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - HOUR}}
        logs, tmux = self._run(
            qrecs, lambda c: _recs((1, "x"), (1176, "Checkout freshness")), "full")
        typed = "".join(tmux.typed_texts())
        self.assertIn("#1176 (Checkout freshness)", typed)
        self.assertIn("/autopilot", typed)
        self.assertFalse(typed.lstrip().startswith("/"))   # never types /goal

    def test_nudge_text_is_capped(self):
        many = list(range(1, 80))
        titles = {n: "dlhý názov ticketu číslo %d " % n * 3 for n in many}
        txt = qa._nudge_text(many, 80, titles)
        self.assertLessEqual(len(txt), qa.NUDGE_MAX_CHARS)
        self.assertTrue(txt.startswith("stuck-check:"))


# --------------------------------------------------------------------------- #
# (b) an IDLE supervisor pane whose goal ENDED is a target.
# --------------------------------------------------------------------------- #

class TestEndedSupervisorPane(unittest.TestCase):
    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ, {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, CWD, "sess-1178-end")
        self.sid = self.tpath.stem
        self.state = {}

    def _heartbeat(self, marker, armed=False):
        pth = ss.status_path(self.sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps({
            "schema": 1, "sid": self.sid, "kind": "main", "last_turn": "stop",
            "ts": NOW, "cwd": CWD, "marker": marker, "goal_armed": armed}),
            encoding="utf-8")

    def _sweep(self, now, members, *, marker="done", mark="cleared",
               human=False, cap=GOAL_IDLE_CAP):
        self._heartbeat(marker)
        self.state.setdefault("goal_mark", {})[self.sid] = {
            "off": 0, "mark": {"state": mark, "ts": NOW - 5 * HOUR}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], cap,
                                   model_type=True, transcript_path=self.tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False), \
                m.patch.object(goal, "_recovery_recent_human",
                               return_value=human):
            logs = goal.goal_lane_sweep(
                now, run=tmux, projects_dir=Path(self._proj.name),
                state=self.state, handled=set(), backlog_fetch=lambda cwd: 0,
                queue_fetch=lambda cwd: _recs(*[(n, "t%d" % n) for n in members]),
                sleep_fn=lambda *a, **k: None)
        return logs, tmux.typed_texts()

    def test_after_goal_end_a_new_ticket_gets_exactly_one_nudge(self):
        logs, typed = self._sweep(NOW, [10])
        self.assertEqual(typed, [])                                  # seed
        self.assertTrue(any("queue-arrival" in ln and "seed" in ln for ln in logs), logs)
        logs, typed = self._sweep(NOW + 120, [10, 1176])
        self.assertEqual(len(typed), 1, logs)
        self.assertIn("#1176", typed[0])
        self.assertNotIn("#10 ", typed[0])
        logs, typed = self._sweep(NOW + 240, [10, 1176])             # no repeat
        self.assertEqual(typed, [])

    def test_floor_holds_a_second_wave_and_accumulates_it(self):
        self._sweep(NOW, [10])
        _l, typed = self._sweep(NOW + 60, [10, 11])
        self.assertEqual(len(typed), 1)
        logs, typed = self._sweep(NOW + 10 * 60, [10, 11, 12])
        self.assertEqual(typed, [])
        self.assertTrue(any("queue-arrival" in ln and "hold:floor" in ln
                            for ln in logs), logs)
        self._sweep(NOW + 20 * 60, [10, 11, 12, 13])
        logs, typed = self._sweep(NOW + 62 * 60, [10, 11, 12, 13])
        self.assertEqual(len(typed), 1, logs)
        self.assertIn("#12", typed[0])
        self.assertIn("#13", typed[0])
        self.assertNotIn("#11 ", typed[0])

    def test_human_active_pane_gets_nothing(self):
        self._sweep(NOW, [10])
        logs, typed = self._sweep(NOW + 60, [10, 11], human=True)
        self.assertEqual(typed, [])
        self.assertTrue(any("hold:human-active" in ln for ln in logs), logs)
        # the arrival is kept: it delivers once the human is quiet
        _l, typed = self._sweep(NOW + 120, [10, 11])
        self.assertEqual(len(typed), 1)
        self.assertIn("#11", typed[0])

    def test_busy_or_drafted_pane_gets_nothing(self):
        self._sweep(NOW, [10])
        logs, typed = self._sweep(NOW + 60, [10, 11],
                                  cap="● Hotovo.\n❯ rozpisany draft\n  ctx ███░\n")
        self.assertEqual(typed, [])
        self.assertTrue(any("hold:not-idle-prompt" in ln for ln in logs), logs)

    def test_a_ticket_leaving_I_is_no_nudge(self):
        self._sweep(NOW, [10, 11])
        logs, typed = self._sweep(NOW + 60, [10])
        self.assertEqual(typed, [])
        self.assertTrue(any("queue-arrival" in ln and "track" in ln for ln in logs), logs)

    def test_last_turn_not_done_is_not_a_target(self):
        logs, typed = self._sweep(NOW, [10], marker="needs_you")
        self.assertEqual(typed, [])
        self.assertNotIn(self.sid, self.state.get("queue_arrival", {}))

    def test_armed_unknown_pane_is_not_a_target(self):
        # no goal_mark verdict and no heartbeat armed flag → armed None: never a
        # guess (fail-closed, the #486 G6 direction).
        self._heartbeat("done", armed=None)
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=self.tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=Path(self._proj.name), state={},
                handled=set(), backlog_fetch=lambda cwd: 0,
                queue_fetch=lambda cwd: _recs((10, "a")),
                sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.typed_texts(), [])

    def test_kind_off_never_reads_the_snapshot(self):
        calls = []
        self._heartbeat("done")
        self.state.setdefault("goal_mark", {})[self.sid] = {
            "off": 0, "mark": {"state": "cleared", "ts": NOW}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=self.tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False), \
                m.patch.object(wd, "nudges_enabled", return_value=False):
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=Path(self._proj.name),
                state=self.state, handled=set(), backlog_fetch=lambda cwd: 0,
                queue_fetch=lambda cwd: calls.append(cwd) or _recs((1, "a")),
                sleep_fn=lambda *a, **k: None)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
