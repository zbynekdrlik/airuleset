"""#1163 — a declared window can be steered by a WATCH (durable scheduled
triggers) instead of a continuous `/goal`.

The gk-quality window's work is schedule-driven (a 12-hourly bounce /
fix-forward / release class watch, a weekly post-release review). Its triggers
lived in session crons that expire after 7 days, vanish on a restart, and miss
slots invisibly (25.9. 19:17). RED against the pre-#1163 tree:

  * `watchdog.watch_triggers` (the cron matcher, the slot state machine, the
    delivery job) does not exist;
  * the gk-quality declaration has no `steer`/`triggers`;
  * `goal-arm --self` in a watch window arms the QUALITY `/goal`;
  * job 9 virgin-arms a watch window, and job 20 dark-watch treats its dark
    goal as a dead loop;
  * the `status` goal row cannot show a watch;
  * `watch-trigger` is not a nudge kind.

Fakes only: no tmux server, no real pane, no gh, no Discord.
"""
import datetime
import io
import json
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
from watchdog import tmux_io  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_BUSY_CAP,
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

BA = ZoneInfo("Europe/Bratislava")
UTC = datetime.timezone.utc
WATCH_CWD = "/home/gatekeeper/devel/odoo/odoo-erp-quality"
PID = "%7"


def _wt():
    from watchdog import watch_triggers
    return watch_triggers


def _at(y, mo, d, h, mi, s=0, tz=BA):
    return datetime.datetime(y, mo, d, h, mi, s, tzinfo=tz).timestamp()


def _utc(ts):
    return datetime.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")


def _window(steer="watch", cwd=WATCH_CWD):
    w = {"name": "gk-quality", "cwd": cwd, "role": "quality",
         "mode": "sequential", "tz": "Europe/Bratislava",
         "triggers": [
             {"name": "watch", "cron": "17 7,19 * * *",
              "prompt": "Spusti gk-quality watch."},
             {"name": "weekly-review", "cron": "17 8 * * 1",
              "prompt": "Spusti týždenný post-release review."}]}
    if steer is None:
        w.pop("steer", None)
        w.pop("triggers")
        w.pop("tz")
    else:
        w["steer"] = steer
    return w


# --------------------------------------------------------------------------- #
# Cron matcher (stdlib, 5 fields, local time, DST-safe)
# --------------------------------------------------------------------------- #

class CronMatcher(unittest.TestCase):

    def test_parses_the_declared_expressions(self):
        wt = _wt()
        s = wt.parse_cron("17 7,19 * * *")
        self.assertEqual(s["minute"], {17})
        self.assertEqual(s["hour"], {7, 19})
        self.assertEqual(len(s["dom"]), 31)
        s = wt.parse_cron("17 8 * * 1")
        self.assertEqual(s["dow"], {1})

    def test_ranges_steps_lists_and_sunday_seven(self):
        wt = _wt()
        s = wt.parse_cron("*/15 9-17/4 1,15 * 7")
        self.assertEqual(s["minute"], {0, 15, 30, 45})
        self.assertEqual(s["hour"], {9, 13, 17})
        self.assertEqual(s["dom"], {1, 15})
        self.assertEqual(s["dow"], {0})

    def test_invalid_expressions_raise(self):
        wt = _wt()
        for bad in ("", "17 7 * *", "60 7 * * *", "17 24 * * *", "x 7 * * *",
                    "17 7 * * MON", "*/0 7 * * *", "5-1 7 * * *",
                    "17 7,,8 * * *", "17 7 * * * *"):
            with self.subTest(expr=bad):
                with self.assertRaises(wt.CronError):
                    wt.parse_cron(bad)

    def test_day_fields_follow_the_cron_or_rule(self):
        wt = _wt()
        s = wt.parse_cron("0 12 13 * 5")      # the 13th OR any Friday
        fri = datetime.datetime(2026, 9, 25, 12, 0)     # a Friday, not the 13th
        the13 = datetime.datetime(2026, 10, 13, 12, 0)  # a Tuesday the 13th
        other = datetime.datetime(2026, 9, 29, 12, 0)   # a Tuesday
        self.assertTrue(wt.cron_matches(s, fri))
        self.assertTrue(wt.cron_matches(s, the13))
        self.assertFalse(wt.cron_matches(s, other))
        s = wt.parse_cron("17 8 * * 1")      # dom '*' -> dow alone decides
        self.assertTrue(wt.cron_matches(s, datetime.datetime(2026, 9, 28, 8, 17)))
        self.assertFalse(wt.cron_matches(s, datetime.datetime(2026, 9, 27, 8, 17)))

    def test_summer_and_winter_slots_follow_the_owners_wall_clock(self):
        wt = _wt()
        s = wt.parse_cron("17 7,19 * * *")
        day = _at(2026, 9, 27, 0, 0)
        got = [_utc(t) for t, _k in wt.due_slots(s, day, day + 86399, BA)]
        self.assertEqual(got, ["2026-09-27 05:17", "2026-09-27 17:17"])   # CEST
        day = _at(2026, 11, 2, 0, 0)
        got = [_utc(t) for t, _k in wt.due_slots(s, day, day + 86399, BA)]
        self.assertEqual(got, ["2026-11-02 06:17", "2026-11-02 18:17"])   # CET

    def test_the_dst_change_days(self):
        wt = _wt()
        s = wt.parse_cron("17 7,19 * * *")
        day = _at(2026, 10, 25, 0, 0)          # fall-back day, 25 h long
        slots = wt.due_slots(s, day, day + 25 * 3600 - 1, BA)
        self.assertEqual([k for _t, k in slots],
                         ["2026-10-25T07:17", "2026-10-25T19:17"])
        # a repeated local hour fires ONCE
        s2 = wt.parse_cron("17 2 * * *")
        slots = wt.due_slots(s2, day, day + 25 * 3600 - 1, BA)
        self.assertEqual(len(slots), 1, slots)
        # a skipped local hour never fires (spring-forward 29.3.2026)
        day = _at(2026, 3, 29, 0, 0)
        self.assertEqual(wt.due_slots(s2, day, day + 23 * 3600 - 1, BA), [])
        slots = wt.due_slots(s, day, day + 23 * 3600 - 1, BA)
        self.assertEqual([_utc(t) for t, _k in slots],
                         ["2026-03-29 05:17", "2026-03-29 17:17"])

    def test_next_slot_crosses_to_the_weekly_monday(self):
        wt = _wt()
        s = wt.parse_cron("17 8 * * 1")
        after = _at(2026, 9, 27, 20, 0)        # a Sunday evening
        ts, key = wt.next_slot(s, after, BA)
        self.assertEqual(key, "2026-09-28T08:17")
        self.assertEqual(ts, _at(2026, 9, 28, 8, 17))
        self.assertIsNone(wt.next_slot(wt.parse_cron("0 0 30 2 *"), after, BA))

    def test_no_tz_means_the_box_local_time(self):
        wt = _wt()
        ts = 1_790_000_000
        self.assertEqual(wt.slot_key(ts, None),
                         datetime.datetime.fromtimestamp(ts).strftime(
                             "%Y-%m-%dT%H:%M"))
        self.assertIsNone(wt.zone(None))
        with self.assertRaises(wt.CronError):
            wt.zone("Mars/Olympus")


# --------------------------------------------------------------------------- #
# Slot state: fire / hold / missed with an injected clock
# --------------------------------------------------------------------------- #

class _Pane:
    """A fake pane seen through the job's injected seams."""

    def __init__(self, idle=True, mode=False, human=False):
        self.idle, self.mode, self.human = idle, mode, human
        self.delivered = []
        self.outcome = "submitted"

    def deps(self, **over):
        pane = self

        def deliver(pid, tpath, text):
            pane.delivered.append((pid, str(tpath), text))
            return types.SimpleNamespace(kind=pane.outcome)

        d = dict(
            projects_dir="/nonexistent",
            find_transcript=lambda _p, cwd: (Path("/t/sess-q.jsonl"), 0),
            capture=lambda pid: GOAL_IDLE_CAP if pane.idle else GOAL_BUSY_CAP,
            in_mode=lambda pid: pane.mode,
            at_idle=lambda cap: cap == GOAL_IDLE_CAP,
            recent_human=lambda *a: pane.human,
            gate_ok=nudge_gate.gate_ok, mark_sent=nudge_gate.mark_sent,
            nudges_enabled=lambda k: True,
            deliver=deliver)
        d.update(over)
        return d


class SlotTransitions(unittest.TestCase):

    def setUp(self):
        self.win = _window()
        self.panes = [(PID, WATCH_CWD)]

    def _run(self, now, state, pane, **kw):
        return _wt().watch_trigger_job(now, state, self.panes,
                                       windows=[self.win], **pane.deps(), **kw)

    def _slots(self, state):
        return state["watch_triggers"]["slots"]

    def test_decide_slot_table(self):
        wt = _wt()
        g = wt.GRACE_S
        self.assertEqual(wt.decide_slot(None, 100, 100, g, True), "fire")
        self.assertEqual(wt.decide_slot(None, 100, 100, g, False), "hold")
        self.assertEqual(wt.decide_slot({"s": "held"}, 100, 100 + g + 1, g,
                                        True), "miss")
        self.assertEqual(wt.decide_slot({"s": "fired"}, 100, 150, g, True),
                         "skip")
        self.assertEqual(wt.decide_slot({"s": "missed"}, 100, 150, g, True),
                         "skip")

    def test_first_sight_records_a_watermark_and_never_retro_fires(self):
        pane = _Pane()
        state = {}
        logs = self._run(_at(2026, 9, 27, 19, 30), state, pane)
        self.assertEqual(pane.delivered, [], logs)
        self.assertEqual(set(state["watch_triggers"]["since"]),
                         {"gk-quality/watch", "gk-quality/weekly-review"})
        self.assertEqual(self._slots(state), {})

    def test_a_due_slot_fires_once_into_an_idle_pane(self):
        pane = _Pane()
        state = {}
        self._run(_at(2026, 9, 27, 18, 0), state, pane)          # watermark
        now = _at(2026, 9, 27, 19, 18)
        logs = self._run(now, state, pane)
        self.assertEqual(len(pane.delivered), 1, logs)
        pid, _tp, text = pane.delivered[0]
        self.assertEqual(pid, PID)
        self.assertTrue(text.startswith("19:17 watch. Spusti gk-quality watch."),
                        text)
        self.assertIn("2026-09-27 19:17", text)
        rec = self._slots(state)["gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual(rec["s"], "fired")
        self.assertEqual(rec["slot"], _at(2026, 9, 27, 19, 17))
        # the per-kind floor is stamped for the pane's session
        self.assertIn("watch-trigger", state["nudge_cadence"]["sess-q"])
        # a later sweep never re-fires the same slot
        self._run(now + 300, state, pane)
        self.assertEqual(len(pane.delivered), 1)

    def test_a_busy_pane_holds_then_fires_within_grace(self):
        pane = _Pane(idle=False)
        state = {}
        self._run(_at(2026, 9, 27, 18, 0), state, pane)
        self._run(_at(2026, 9, 27, 19, 18), state, pane)
        rec = self._slots(state)["gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual((rec["s"], rec["why"]), ("held", "busy-pane"))
        self.assertEqual(pane.delivered, [])
        pane.idle = True
        self._run(_at(2026, 9, 27, 20, 40), state, pane)
        self.assertEqual(self._slots(state)[
            "gk-quality/watch@2026-09-27T19:17"]["s"], "fired")
        self.assertEqual(len(pane.delivered), 1)

    def test_a_slot_never_ready_within_grace_becomes_missed(self):
        pane = _Pane(idle=False)
        state = {}
        self._run(_at(2026, 9, 27, 18, 0), state, pane)
        self._run(_at(2026, 9, 27, 19, 20), state, pane)
        logs = self._run(_at(2026, 9, 27, 21, 18), state, pane)
        rec = self._slots(state)["gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual((rec["s"], rec["why"]), ("missed", "busy-pane"), logs)
        pane.idle = True
        self._run(_at(2026, 9, 27, 21, 30), state, pane)
        self.assertEqual(pane.delivered, [], "a missed slot never fires late")

    def test_a_slot_inside_a_watchdog_outage_is_recorded_missed(self):
        pane = _Pane()
        state = {}
        self._run(_at(2026, 9, 27, 6, 0), state, pane)
        # the watchdog was down from 06:00 to 10:00: 07:17 is past its grace
        self._run(_at(2026, 9, 27, 10, 0), state, pane)
        rec = self._slots(state)["gk-quality/watch@2026-09-27T07:17"]
        self.assertEqual((rec["s"], rec["why"]), ("missed", "not-delivered"))
        self.assertEqual(pane.delivered, [])

    def test_every_readiness_gate_holds(self):
        cases = (("kind-off", {"nudges_enabled": lambda k: False}, {}),
                 ("in-mode", {}, {"mode": True}),
                 ("recent-human", {}, {"human": True}),
                 ("no-pane", {}, {}),
                 ("no-transcript", {"find_transcript": lambda _p, _c: None},
                  {}))
        for why, over, pane_kw in cases:
            with self.subTest(why=why):
                pane = _Pane(**pane_kw)
                state = {}
                panes = [] if why == "no-pane" else self.panes
                wt = _wt()
                deps = pane.deps(**over)
                wt.watch_trigger_job(_at(2026, 9, 27, 18, 0), state, panes,
                                     windows=[self.win], **deps)
                wt.watch_trigger_job(_at(2026, 9, 27, 19, 18), state, panes,
                                     windows=[self.win], **deps)
                rec = state["watch_triggers"]["slots"][
                    "gk-quality/watch@2026-09-27T19:17"]
                self.assertEqual((rec["s"], rec["why"]), ("held", why))
                self.assertEqual(pane.delivered, [])

    def test_only_the_windows_own_pane_counts(self):
        pane = _Pane()
        state = {}
        self.panes = [("%1", WATCH_CWD + "/worktree"), ("%2", "/elsewhere")]
        self._run(_at(2026, 9, 27, 18, 0), state, pane)
        self._run(_at(2026, 9, 27, 19, 18), state, pane)
        self.assertEqual(pane.delivered, [])
        self.panes = [("%1", WATCH_CWD), ("%2", WATCH_CWD)]
        self._run(_at(2026, 9, 27, 19, 19), state, pane)
        rec = self._slots(state)["gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual(rec["why"], "ambiguous-pane(2)")

    def test_a_typed_but_undelivered_send_holds_and_stamps_the_floor(self):
        pane = _Pane()
        pane.outcome = "swallowed"
        state = {}
        self._run(_at(2026, 9, 27, 18, 0), state, pane)
        self._run(_at(2026, 9, 27, 19, 18), state, pane)
        rec = self._slots(state)["gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual((rec["s"], rec["why"]), ("held", "swallowed"))
        self.assertIn("watch-trigger", state["nudge_cadence"]["sess-q"])
        # the floor now holds the retry (never re-typed every sweep)
        self._run(_at(2026, 9, 27, 19, 20), state, pane)
        self.assertEqual(len(pane.delivered), 1)
        self.assertEqual(self._slots(state)[
            "gk-quality/watch@2026-09-27T19:17"]["why"], "floor")

    def test_monday_watch_does_not_starve_the_weekly_review(self):
        pane = _Pane()
        state = {}
        self._run(_at(2026, 9, 28, 6, 0), state, pane)
        self._run(_at(2026, 9, 28, 7, 17, 30), state, pane)
        self._run(_at(2026, 9, 28, 8, 17, 10), state, pane)   # floor: 59.7 min
        self._run(_at(2026, 9, 28, 8, 18, 10), state, pane)
        slots = self._slots(state)
        self.assertEqual(slots["gk-quality/watch@2026-09-28T07:17"]["s"],
                         "fired")
        self.assertEqual(
            slots["gk-quality/weekly-review@2026-09-28T08:17"]["s"], "fired")
        self.assertEqual(len(pane.delivered), 2)

    def test_dry_run_mutates_and_types_nothing(self):
        pane = _Pane()
        state = {}
        self._run(_at(2026, 9, 27, 18, 0), state, pane)          # watermark
        before = json.dumps(state, sort_keys=True)
        logs = self._run(_at(2026, 9, 27, 19, 18), state, pane, dry_run=True)
        self.assertEqual(json.dumps(state, sort_keys=True), before)
        self.assertEqual(pane.delivered, [])
        self.assertTrue(any("would fire" in ln for ln in logs), logs)

    def test_old_history_is_pruned(self):
        pane = _Pane()
        now = _at(2026, 9, 27, 18, 0)
        state = {"watch_triggers": {"since": {"gone/x": now}, "slots": {
            "gk-quality/watch@2026-09-01T07:17": {
                "s": "fired", "slot": now - 26 * 86400}}}}
        self._run(now, state, pane)
        self.assertEqual(state["watch_triggers"]["slots"], {})
        self.assertNotIn("gone/x", state["watch_triggers"]["since"])


# --------------------------------------------------------------------------- #
# Delivery through the ONE primitive into a fake pane, as ONE pointer line
# --------------------------------------------------------------------------- #

class _WidthPane(DeliverGoalFakeTmux):
    def __call__(self, argv, timeout=8):
        if "display-message" in argv and argv[-1] == "#{pane_width}":
            return "176\n"
        return super().__call__(argv, timeout)

    def literals(self):
        return [a[-1] for a in self.sent if "-l" in a]


class DeliveryThroughThePrimitive(unittest.TestCase):

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = Path(d.name)
        env = m.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AIRULESET_NUDGE_FILE_DIR", None)
        self.proj = self.home / "projects"
        self.tpath = _write_marker_transcript(self.proj, WATCH_CWD, "sess-q")
        self.now = _at(2026, 9, 27, 19, 18)
        os.utime(self.tpath, (self.now - 600, self.now - 600))   # idle, on OUR clock
        self.state = {}

    def _run(self, cap):
        fake = _WidthPane([(PID, "claude", WATCH_CWD, "111")], cap,
                          model_type=True, transcript_path=self.tpath)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=[_window()]):
            for now in (self.now - 3600, self.now):     # watermark, then due
                logs = _wt().run_job(now, self.state, [(PID, WATCH_CWD)],
                                     run=fake, sleep_fn=lambda _s: None,
                                     projects_dir=self.proj)
        return fake, logs

    def test_an_idle_pane_receives_one_pointer_line(self):
        fake, logs = self._run(GOAL_IDLE_CAP)
        lits = fake.literals()
        self.assertEqual(len(lits), 1, (lits, logs))
        line = lits[0]
        self.assertTrue(line.startswith("nudge: [watch-trigger] "), line)
        # the headline is the slot time + trigger, cut to the one-row budget
        head = line[len("nudge: [watch-trigger] "):line.index(" — celý text:")]
        self.assertTrue(head, line)
        self.assertTrue("19:17 watch.".startswith(head.rstrip("…")), head)
        self.assertNotIn("\n", line)
        self.assertIn("— celý text: ~/.claude/nudges/watch-trigger-", line)
        files = sorted((self.home / ".claude" / "nudges").glob("*.md"))
        self.assertEqual(len(files), 1)
        body = files[0].read_text(encoding="utf-8")
        self.assertTrue(body.startswith("19:17 watch. Spusti gk-quality "
                                        "watch."), body)
        self.assertIn("2026-09-27 19:17", body)
        rec = self.state["watch_triggers"]["slots"][
            "gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual(rec["s"], "fired", logs)

    def test_a_busy_pane_gets_no_keystroke(self):
        fake, logs = self._run(GOAL_BUSY_CAP)
        self.assertEqual([a for a in fake.sent if a[:2] == ["tmux",
                                                            "send-keys"]], [])
        rec = self.state["watch_triggers"]["slots"][
            "gk-quality/watch@2026-09-27T19:17"]
        self.assertEqual((rec["s"], rec["why"]), ("held", "busy-pane"), logs)


# --------------------------------------------------------------------------- #
# Steering: /autopilot (goal-arm --self) and job 9 arm NO /goal
# --------------------------------------------------------------------------- #

class Steering(unittest.TestCase):

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = Path(d.name)
        env = m.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def test_goal_arm_self_in_a_watch_window_arms_no_goal(self):
        out = io.StringIO()
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=(PID, WATCH_CWD, "sess-w")), \
                m.patch.object(cli_fleet, "box_windows",
                               return_value=[_window()]), \
                m.patch.object(goal, "deliver_goal",
                               side_effect=AssertionError("no /goal")), \
                m.patch("sys.stdout", out):
            airuleset.cmd_goal_arm(types.SimpleNamespace(self=True,
                                                         template=""))
        printed = out.getvalue()
        self.assertIn("watch armed (2 triggers, next ", printed)
        self.assertEqual(goal.load_goal_requests(self.reqp), {})
        armed = json.loads((self.home / ".claude" / "watch-armed.json")
                           .read_text())
        self.assertEqual(armed["gk-quality"]["sid"], "sess-w")

    def test_goal_arm_self_elsewhere_still_arms_the_goal(self):
        with m.patch.object(goal._compact, "resolve_self_pane",
                            return_value=(PID, WATCH_CWD, "sess-x")), \
                m.patch.object(cli_fleet, "box_windows",
                               return_value=[_window(steer=None)]), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(goal, "deliver_goal",
                               return_value="skip:no-pane"), \
                m.patch("sys.stdout", io.StringIO()):
            airuleset.cmd_goal_arm(types.SimpleNamespace(self=True,
                                                         template=""))
        self.assertIn("sess-x", goal.load_goal_requests(self.reqp))

    def test_goal_template_is_none_only_for_a_watch_window(self):
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=[_window()]):
            self.assertIsNone(goal.goal_template_for("full", WATCH_CWD))
            self.assertIsNone(goal._default_rearm_fn(WATCH_CWD)[0])
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=[_window(steer=None)]):
            self.assertTrue(goal.goal_template_for("full", WATCH_CWD))

    def _virgin_sweep(self, win):
        proj = self.home / ("proj-%s" % (win.get("steer") or "none"))
        _write_marker_transcript(proj, WATCH_CWD, "sess-v")
        reqp = str(self.home / ("req-%s.json" % (win.get("steer") or "none")))
        tmux = DeliverGoalFakeTmux([(PID, "claude", WATCH_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows", return_value=[win]), \
                m.patch.object(goal, "_default_rearm_fn",
                               return_value=("/goal STOP CONDITIONS q", "full")):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=reqp, state={},
                                   sleep_fn=lambda s: None)
        typed = [a for a in tmux.sent if "-l" in a
                 and str(a[-1]).startswith("/goal")]
        return typed, logs

    def test_job9_never_virgin_arms_a_watch_window(self):
        typed, logs = self._virgin_sweep(_window())
        self.assertEqual(typed, [], logs)
        # teeth: the SAME fixture without steer=watch is virgin-armed
        typed, logs = self._virgin_sweep(_window(steer=None))
        self.assertTrue(typed, logs)

    def test_dark_watch_never_rearms_or_pings_a_watch_window(self):
        proj = self.home / "proj-dark"
        tpath = _write_marker_transcript(proj, WATCH_CWD, "sess-d")
        _write_goal_marker(proj, WATCH_CWD, "sess-d",
                           "Goal set: STOP CONDITIONS q", ts_epoch=1000)
        os.utime(tpath, (1100, 1100))
        tmux = DeliverGoalFakeTmux([(PID, "claude", WATCH_CWD, "111")],
                                   GOAL_IDLE_CAP)
        sent = []
        for win, label in ((_window(), "watch"), (_window(steer=None), "none")):
            state = {}
            with m.patch.object(cli_fleet, "box_windows", return_value=[win]):
                logs = []
                for i in range(12):
                    logs += goal.goal_dark_watch(
                        5000 + i * 700, run=tmux, state=state,
                        send_fn=lambda *a, **k: sent.append(a),
                        projects_dir=proj, requests_path=self.reqp,
                        obligation_fn=lambda c: (3, 5000 + i * 700),
                        rearm_fn=lambda c: ("/goal STOP CONDITIONS q", "full"))
            if label == "watch":
                self.assertTrue(any("skip:watch-window" in ln for ln in logs),
                                logs)
                self.assertEqual(sum("skip:watch-window" in ln for ln in logs),
                                 1, "logged once per episode")
                self.assertEqual(goal.load_goal_requests(self.reqp), {})
                self.assertEqual(sent, [])
                self.assertEqual(state.get("goal_dark_confirm", {}), {})
            else:
                self.assertTrue(any("first observation" in ln for ln in logs),
                                logs)


# --------------------------------------------------------------------------- #
# Declaration, nudge-kind registration, status row
# --------------------------------------------------------------------------- #

class DeclarationAndRegistration(unittest.TestCase):

    def _gk_quality(self):
        for r in cli_fleet.REMOTE_HOSTS:
            if r.get("user") == "gatekeeper":
                for w in r["windows"]:
                    if w["name"] == "gk-quality":
                        return w
        self.fail("gk-quality window not declared")

    def test_gk_quality_declares_the_two_watch_triggers(self):
        w = self._gk_quality()
        self.assertEqual(w.get("steer"), "watch")
        self.assertEqual(w.get("tz"), "Europe/Bratislava")
        got = {t["name"]: t["cron"] for t in w["triggers"]}
        self.assertEqual(got, {"watch": "17 7,19 * * *",
                               "weekly-review": "17 8 * * 1"})
        prompts = {t["name"]: t["prompt"] for t in w["triggers"]}
        self.assertIn("odoo-erp 7924", prompts["watch"])
        self.assertIn("core-quals --post-release 7", prompts["weekly-review"])
        self.assertIn("odoo-erp 8376", prompts["weekly-review"])
        for p in prompts.values():
            self.assertNotIn("#", p)
        self.assertEqual(_wt().validate_watch(w), [])

    def test_only_gk_quality_is_watch_steered(self):
        steered = [(r["name"], w["name"]) for r in cli_fleet.REMOTE_HOSTS
                   for w in r.get("windows") or [] if w.get("steer")]
        self.assertEqual(steered, [("gatekeeper", "gk-quality")])

    def test_validate_windows_rejects_a_bad_watch(self):
        base = _window(cwd="~/devel/odoo/odoo-erp-quality")
        self.assertEqual(cli_fleet.validate_windows([base]), [])
        bad = dict(base, steer="cron")
        self.assertTrue(cli_fleet.validate_windows([bad]))
        bad = dict(base, triggers=[{"name": "w", "cron": "99 * * * *",
                                    "prompt": "x"}])
        self.assertTrue(_wt().validate_watch(bad))
        bad = dict(base, triggers=[])
        self.assertTrue(_wt().validate_watch(bad))
        bad = dict(base, tz="Nowhere/Land")
        self.assertTrue(_wt().validate_watch(bad))
        bad = {k: v for k, v in base.items() if k != "steer"}
        self.assertTrue(_wt().validate_watch(bad))

    def test_watch_trigger_is_a_staged_cap_exempt_machine_kind(self):
        self.assertIn("watch-trigger", tmux_io.MACHINE_NUDGE_KINDS)
        self.assertNotIn("watch-trigger", tmux_io.RECOVERY_NUDGE_KINDS)
        self.assertIn("watch-trigger", nudge_gate.PRIORITY_CAP_EXEMPT_KINDS)
        self.assertEqual(_wt().NUDGE_KIND, "watch-trigger")
        # keeps its 60-min per-kind floor, never blocked by the total cap
        st = {}
        nudge_gate.mark_sent(st, "s", "queue-arrival", 1000)
        self.assertTrue(nudge_gate.gate_ok(st, "s", "watch-trigger", 1000))
        nudge_gate.mark_sent(st, "s", "watch-trigger", 1000)
        self.assertFalse(nudge_gate.gate_ok(st, "s", "watch-trigger", 4000))
        self.assertTrue(nudge_gate.gate_ok(st, "s", "watch-trigger", 4601))

    def test_default_off_until_staged(self):
        with TemporaryDirectory() as home, \
                m.patch.dict(os.environ, {"HOME": home}):
            os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
            self.assertFalse(wd.nudges_enabled("watch-trigger", home=home))
            tmux_io.set_nudge_kind("watch-trigger", True, home=home)
            self.assertTrue(wd.nudges_enabled("watch-trigger", home=home))


class StatusRow(unittest.TestCase):

    def test_status_row_shows_next_last_and_missed(self):
        wt = _wt()
        now = _at(2026, 9, 28, 10, 0)            # Monday
        state = {"watch_triggers": {"slots": {
            "gk-quality/watch@2026-09-28T07:17": {
                "s": "fired", "slot": _at(2026, 9, 28, 7, 17)},
            "gk-quality/weekly-review@2026-09-28T08:17": {
                "s": "missed", "slot": _at(2026, 9, 28, 8, 17),
                "why": "busy-pane"},
            "gk-quality/watch@2026-09-27T19:17": {
                "s": "fired", "slot": _at(2026, 9, 27, 19, 17)}}}}
        row = wt.status_row(_window(), now, state)
        self.assertEqual(
            row, "goal: watch armed (2 triggers; next watch 19:17; last "
                 "weekly-review 08:17 missed; missed weekly-review 08:17)")

    def test_status_row_with_no_history_and_kind_off(self):
        row = _wt().status_row(_window(), _at(2026, 9, 27, 20, 0), {},
                               kind_on=False)
        self.assertEqual(
            row, "goal: watch armed (2 triggers; next watch Mon 07:17; "
                 "delivery OFF — stage: airuleset.py nudges on --kind "
                 "watch-trigger)")

    def test_status_probe_renders_the_watch_row(self):
        with m.patch.object(cli_fleet, "box_windows", return_value=[_window()]), \
                m.patch("watchdog.watch_triggers.load_watchdog_state",
                        return_value={}):
            row = airuleset.goal_status_probe(WATCH_CWD, run=lambda *a, **k: "",
                                              pane_env="")
        self.assertTrue(row.startswith("goal: watch armed (2 triggers; next "),
                        row)

    def test_arm_line(self):
        line = _wt().arm_line(_window(), _at(2026, 9, 27, 18, 0))
        self.assertEqual(line, "watch armed (2 triggers, next watch 19:17)")


class SkillDocumentsTheWatch(unittest.TestCase):

    def test_step2_names_the_watch_steering(self):
        body = (REPO / "skills" / "autopilot" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("steer=watch", body)
        self.assertIn("watch armed", body)


if __name__ == "__main__":
    unittest.main()
