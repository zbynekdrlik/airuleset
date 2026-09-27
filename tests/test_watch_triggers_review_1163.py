"""#1163 review round — locks for the two adversarial reviews' findings on the
watch-trigger job (Job 52, `watchdog/watch_triggers.py`).

Each class names the finding it locks:

  * a pane "Waiting for N background agents" (its own worker) is never typed
    into, nor is a pane whose transcript shows a live turn (#1110);
  * a pane another job typed this sweep is skipped, and a fire marks it;
  * an Enter that went in (even unconfirmed) fires the slot at most once;
  * a future watermark (clock stepped back) is clamped, never a silent gap;
  * a changed cron/tz restarts the watermark (no retroactive fire);
  * pending slots fire oldest first across triggers;
  * a corrupt slot record never wedges the job; an undeclared trigger's history
    is pruned; declaration errors are journaled once per change;
  * `/autopilot` names a delivery staged OFF and prints the migration guidance;
  * `status` shows the acknowledgement, a leftover armed `/goal`, and dates for
    old history;
  * every declared watch window validates; Job 52 is enabled in production
    (`cmd_watchdog`) and reached through `run_once`.

Fakes only: no tmux server, no real pane, no gh, no Discord.
"""
import datetime
import inspect
import io
import os
import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import airuleset  # noqa: E402
import cli_fleet  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import nudge_gate  # noqa: E402
from watchdog import watch_triggers as wt  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_marker_transcript,
)

BA = ZoneInfo("Europe/Bratislava")
CWD = "/home/gatekeeper/devel/odoo/odoo-erp-quality"
PID = "%7"
WAITING_CAP = ("● Unit 34 dispatched.\n"
               "  ✻ Waiting for 1 background agent to finish\n"
               "❯ \n  ctx ███░  caveman:lite\n")


def _at(y, mo, d, h, mi, s=0):
    return datetime.datetime(y, mo, d, h, mi, s, tzinfo=BA).timestamp()


def _win(triggers=None, tz="Europe/Bratislava"):
    return {"name": "gk-quality", "cwd": CWD, "role": "quality",
            "mode": "sequential", "steer": "watch", "tz": tz,
            "triggers": triggers or [
                {"name": "watch", "cron": "17 7,19 * * *", "prompt": "W."},
                {"name": "weekly-review", "cron": "17 8 * * 1",
                 "prompt": "R."}]}


class _Fake:
    def __init__(self, cap=GOAL_IDLE_CAP, outcome="submitted"):
        self.cap, self.outcome, self.sent = cap, outcome, []

    def kw(self, **over):
        fake = self

        def deliver(pid, tpath, text):
            fake.sent.append(text)
            return types.SimpleNamespace(kind=fake.outcome)

        d = dict(projects_dir="/p",
                 find_transcript=lambda _p, _c: (Path("/t/sess-q.jsonl"), 0),
                 capture=lambda pid: fake.cap, in_mode=lambda pid: False,
                 at_idle=lambda cap: "❯" in cap,
                 recent_human=lambda *a: False,
                 gate_ok=nudge_gate.gate_ok, mark_sent=nudge_gate.mark_sent,
                 nudges_enabled=lambda k: True, deliver=deliver)
        d.update(over)
        return d


def _job(now, state, fake, win=None, panes=None, **over):
    return wt.watch_trigger_job(now, state, panes or [(PID, CWD)],
                                windows=[win or _win()], **fake.kw(**over))


def _slot(state, key):
    return state["watch_triggers"]["slots"].get(key)


class BusyAndLiveTurnHold(unittest.TestCase):
    """🔴 both reviews: a pane waiting on its own worker; 🟡 #1110 live turn."""

    def test_the_real_waiting_line_holds_through_run_job(self):
        with TemporaryDirectory() as home, \
                m.patch.dict(os.environ, {"HOME": home}):
            proj = Path(home) / "p"
            tp = _write_marker_transcript(proj, CWD, "sess-q")
            idle = _at(2026, 9, 27, 19, 8)                  # on OUR clock
            os.utime(tp, (idle, idle))
            fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")],
                                       WAITING_CAP, model_type=True,
                                       transcript_path=tp)
            state = {}
            self.assertTrue(wd.pane_at_idle_prompt(WAITING_CAP),
                            "fixture must pass the idle gate on its own")
            with m.patch.object(cli_fleet, "box_windows",
                                return_value=[_win()]):
                for now in (_at(2026, 9, 27, 18, 0), _at(2026, 9, 27, 19, 18)):
                    logs = wt.run_job(now, state, [(PID, CWD)], run=fake,
                                      sleep_fn=lambda _s: None,
                                      projects_dir=proj)
        self.assertEqual([a for a in fake.sent if "send-keys" in a], [], logs)
        rec = _slot(state, "gk-quality/watch@2026-09-27T19:17")
        self.assertEqual((rec["s"], rec["why"]), ("held", "busy-waiting"))

    def test_a_fresh_transcript_is_a_live_turn(self):
        with TemporaryDirectory() as home, \
                m.patch.dict(os.environ, {"HOME": home}):
            proj = Path(home) / "p"
            tp = _write_marker_transcript(proj, CWD, "sess-q",
                                          transcript_age_s=None)   # fresh
            now = os.path.getmtime(tp) + 5
            fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")],
                                       GOAL_IDLE_CAP, model_type=True,
                                       transcript_path=tp)
            state = {"watch_triggers": {"since": {"gk-quality/watch": {
                "ts": now - 3600, "sig": "*/5 * * * *|Europe/Bratislava"}}}}
            win = _win([{"name": "watch", "cron": "*/5 * * * *",
                         "prompt": "W."}])
            with m.patch.object(cli_fleet, "box_windows", return_value=[win]):
                logs = wt.run_job(now, state, [(PID, CWD)], run=fake,
                                  sleep_fn=lambda _s: None, projects_dir=proj)
        self.assertEqual([a for a in fake.sent if "send-keys" in a], [], logs)
        whys = {v["why"] for v in state["watch_triggers"]["slots"].values()
                if v["s"] == "held"}
        self.assertIn("live-turn", whys, logs)


class HandledThisSweep(unittest.TestCase):

    def test_a_pane_typed_by_another_job_is_skipped_and_a_fire_marks_it(self):
        state, fake = {}, _Fake()
        _job(_at(2026, 9, 27, 18, 0), state, fake)
        handled = {"sess-q"}
        _job(_at(2026, 9, 27, 19, 18), state, fake, handled=handled)
        self.assertEqual(fake.sent, [])
        self.assertEqual(_slot(state, "gk-quality/watch@2026-09-27T19:17")["why"],
                         "handled-this-sweep")
        handled = set()
        _job(_at(2026, 9, 27, 19, 19), state, fake, handled=handled)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(handled, {"sess-q"})


class AtMostOnce(unittest.TestCase):
    """🟡 review B: an `unconfirmed` send (Enter went in) must not re-type."""

    def test_unconfirmed_fires_and_is_never_retyped(self):
        state, fake = {}, _Fake(outcome="unconfirmed")
        _job(_at(2026, 9, 27, 18, 0), state, fake)
        _job(_at(2026, 9, 27, 19, 18), state, fake)
        for mins in (80, 100):
            _job(_at(2026, 9, 27, 19, 18) + mins * 60, state, fake)
        self.assertEqual(len(fake.sent), 1)
        rec = _slot(state, "gk-quality/watch@2026-09-27T19:17")
        self.assertEqual((rec["s"], rec["why"]), ("fired", "unconfirmed"))


class Watermark(unittest.TestCase):

    def test_a_future_watermark_is_clamped_not_a_silent_gap(self):
        state, fake = {}, _Fake()
        _job(_at(2026, 9, 27, 18, 0), state, fake)
        state["watch_triggers"]["since"]["gk-quality/watch"]["ts"] = \
            _at(2026, 10, 27, 18, 0)                       # clock stepped back
        logs = _job(_at(2026, 9, 27, 18, 30), state, fake)
        self.assertTrue(any("clock stepped back" in ln for ln in logs), logs)
        _job(_at(2026, 9, 27, 19, 18), state, fake)
        self.assertEqual(len(fake.sent), 1, "the next real slot still fires")

    def test_a_changed_cron_never_fires_past_slots(self):
        state, fake = {}, _Fake()
        _job(_at(2026, 9, 27, 17, 0), state, fake)
        edited = _win([{"name": "watch", "cron": "0 18 * * *", "prompt": "W."}])
        logs = _job(_at(2026, 9, 27, 18, 30), state, fake, win=edited)
        self.assertEqual(fake.sent, [], logs)
        self.assertTrue(any("schedule changed" in ln for ln in logs), logs)
        _job(_at(2026, 9, 28, 18, 1), state, fake, win=edited)
        self.assertEqual(len(fake.sent), 1, "the edited schedule fires next day")


class OrderingAndRobustness(unittest.TestCase):

    def test_pending_slots_fire_oldest_first_across_triggers(self):
        win = _win([{"name": "a", "cron": "30 10 * * *", "prompt": "A."},
                    {"name": "b", "cron": "0 10 * * *", "prompt": "B."}])
        state, fake = {}, _Fake()
        _job(_at(2026, 9, 27, 9, 0), state, fake, win=win)
        _job(_at(2026, 9, 27, 10, 31), state, fake, win=win)
        self.assertEqual(len(fake.sent), 1)
        self.assertTrue(fake.sent[0].startswith("10:00 b."), fake.sent[0])

    def test_a_corrupt_slot_record_never_wedges_the_job(self):
        state, fake = {}, _Fake()
        _job(_at(2026, 9, 27, 18, 0), state, fake)
        state["watch_triggers"]["slots"][
            "gk-quality/watch@2026-09-27T19:17"] = "garbage"
        _job(_at(2026, 9, 27, 19, 18), state, fake)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(_slot(state, "gk-quality/watch@2026-09-27T19:17")["s"],
                         "fired")

    def test_an_undeclared_triggers_recent_history_is_pruned(self):
        state, fake = {}, _Fake()
        now = _at(2026, 9, 27, 18, 0)
        state["watch_triggers"] = {"slots": {
            "gk-quality/retired@2026-09-27T07:17": {"s": "held", "slot": now}}}
        _job(now, state, fake)
        self.assertEqual(state["watch_triggers"]["slots"], {})

    def test_declaration_errors_are_journaled_once_per_change(self):
        bad = _win([{"name": "watch", "cron": "99 * * * *", "prompt": "W."}])
        state, fake = {}, _Fake()
        first = _job(_at(2026, 9, 27, 18, 0), state, fake, win=bad)
        again = _job(_at(2026, 9, 27, 18, 1), state, fake, win=bad)
        self.assertEqual(sum("declaration error" in ln for ln in first), 1)
        self.assertEqual(sum("declaration error" in ln for ln in again), 0)


class ArmAndStatus(unittest.TestCase):

    def setUp(self):
        self.reqp, _ = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = d.name
        env = m.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)

    def _arm(self, sid, kind_on=True):
        out = io.StringIO()
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=(PID, CWD, sid)), \
                m.patch.object(cli_fleet, "box_windows", return_value=[_win()]), \
                m.patch.object(wd, "nudges_enabled", return_value=kind_on), \
                m.patch("sys.stdout", out):
            airuleset.cmd_goal_arm(types.SimpleNamespace(self=True,
                                                         template=""))
        return out.getvalue()

    def test_arm_names_an_off_delivery_and_prints_the_migration(self):
        out = self._arm("sess-w", kind_on=False)
        self.assertIn("watch armed (2 triggers, next ", out)
        self.assertIn("delivery OFF — stage: airuleset.py nudges on --kind "
                      "watch-trigger", out)
        self.assertIn("/goal clear", out)
        self.assertIn("CronDelete", out)

    def test_no_session_records_no_acknowledgement(self):
        self._arm("")
        self.assertFalse(os.path.exists(wt.armed_path(self.home)))

    def test_status_shows_the_ack_and_a_leftover_armed_goal(self):
        now = _at(2026, 9, 27, 20, 0)
        row = wt.status_row(_win(), now, {}, armed={"ts": now - 600},
                            goal_armed=True)
        self.assertIn("acknowledged 19:50", row)
        self.assertIn("/goal STILL ARMED — clear it (/goal clear)", row)

    def test_old_history_carries_its_date(self):
        now = _at(2026, 9, 27, 20, 0)
        state = {"watch_triggers": {"slots": {
            "gk-quality/weekly-review@2026-09-14T08:17": {
                "s": "missed", "slot": _at(2026, 9, 14, 8, 17)}}}}
        row = wt.status_row(_win(), now, state)
        self.assertIn("missed weekly-review 14.09. 08:17", row)

    def test_status_probe_reports_a_leftover_armed_goal(self):
        cap = GOAL_IDLE_CAP.replace("caveman:lite", "caveman:lite  ◎ /goal active")
        with m.patch.object(cli_fleet, "box_windows", return_value=[_win()]), \
                m.patch.object(goal._compact, "resolve_self_pane",
                               return_value=(PID, CWD, "sess-w")), \
                m.patch.object(wd, "capture_pane", return_value=cap), \
                m.patch.object(wt, "load_watchdog_state", return_value={}):
            row = airuleset.goal_status_probe(CWD)
        self.assertIn("/goal STILL ARMED", row)


class FleetAndWiring(unittest.TestCase):

    def test_every_declared_watch_window_validates(self):
        seen = 0
        for remote in cli_fleet.REMOTE_HOSTS:
            self.assertEqual(cli_fleet.validate_windows(
                cli_fleet.managed_windows(remote)), [], remote.get("name"))
            for w in cli_fleet.managed_windows(remote):
                if w.get("steer"):
                    seen += 1
                    self.assertEqual(wt.validate_watch(w), [], w["name"])
        self.assertGreaterEqual(seen, 1)

    def test_cmd_watchdog_enables_job_52_in_production(self):
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("watch_triggers_enabled=True", src)

    def test_run_once_reaches_the_job_only_when_enabled(self):
        # the characterization harness: every canonical seam is a recorder and
        # state/projects are a throwaway dir (the suite's run_once isolation)
        from test_run_once_characterization import _drive
        calls = []

        def _rec(now, state, panes, **kw):
            calls.append(sorted(kw))
            return ["watch-trigger: probe"]

        with m.patch.object(wt, "box_watch_windows", return_value=[_win()]), \
                m.patch.object(wt, "run_job", side_effect=_rec):
            _drive({})
            self.assertEqual(calls, [])
            _labels, _calls, logs = _drive({"watch_triggers_enabled": True})
        self.assertEqual(len(calls), 1)
        self.assertIn("handled", calls[0])
        self.assertIn("budget_left", calls[0])
        self.assertIn("watch-trigger: probe", logs)


if __name__ == "__main__":
    unittest.main()
