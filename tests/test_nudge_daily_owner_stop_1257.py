"""Bundle B9 — at most ONE batched machine nudge per pane per 24 h (owner
26.10.2026: "mali by sa nudges zgrupovať a volať napr. len raz denne") and the
#1257 owner-stop latch (owner 20.10.2026: "nejaké tvoje procesy spustili a dali
pokračovať gk-quality/gk-infra aj keď som ich mal /exit … to nesmieš!").

Daily cap: every NON-recovery kind shares ONE delivery per pane per day — the
per-sid cadence gate (`gate_ok` / `batch_eligible`, all floors and the total cap
24 h) and the per-pane typing budget at the delivery primitives (`send_verified`,
the stash route, the report-owed and dying-subagent riders). Everything due at
the open moment rides ONE `nudge:` batch. RECOVERY kinds stay immediate; the
owner's declared `watch-trigger` slots keep their own hourly floor.

Owner-stop latch (`watchdog/owner_stop.py`): set from structured transcript
facts (exit, goal none/cleared, interrupt, a short pause/stop prompt), cleared
only by a new owner act; while set NO keystroke kind enters the pane (recovery
included) and no `/goal` is armed.
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
from watchdog import goal  # noqa: E402
from watchdog import nudge_gate as ng  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_DRAFT_CAP, GOAL_IDLE_CAP, _isolate_goal_state,
    _write_marker_transcript)

HOUR = 3600
DAY = 24 * HOUR
NOW = 1_800_000_000
CAPPED = ("release-gap", "queue-arrival", "partition-audit", "u-freshness",
          "bounce-verdict", "goal-guard", "infra-priority", "task-hygiene",
          "checkout-lag", "goal-sweep", "subagent-stuck", "bounce",
          "gk-request", "card")


def _os():
    from watchdog import owner_stop
    return owner_stop


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def _u(content, ts):
    return {"type": "user", "timestamp": _iso(ts),
            "message": {"role": "user", "content": content}}


def _sys(content, ts):
    return {"type": "system", "subtype": "local_command", "timestamp": _iso(ts),
            "content": content}


def _a(text, ts):
    return {"type": "assistant", "timestamp": _iso(ts),
            "message": {"content": [{"type": "text", "text": text}]}}


def _cmd(name, args="", ts=0):
    return _u("<command-name>/%s</command-name>\n<command-message>%s"
              "</command-message>\n<command-args>%s</command-args>"
              % (name, name, args), ts)


T0 = NOW - 2 * HOUR
FACTS = {
    "exit": [_cmd("exit", ts=T0 + 10),
             _sys("<local-command-stdout>Bye!</local-command-stdout>", T0 + 11)],
    "goal-none": [_cmd("goal", ts=T0 + 10),
                  _sys("<local-command-stdout>No goal set</local-command-stdout>",
                       T0 + 11)],
    "goal-cleared": [_cmd("goal", "clear", T0 + 10),
                     _sys("<local-command-stdout>Goal cleared: work the backlog"
                          "</local-command-stdout>", T0 + 11)],
    "interrupt": [_u([{"type": "text", "text": "[Request interrupted by user]"}],
                     T0 + 10)],
    "pause": [_u("pauzni sa ked mozes", T0 + 10)],
}
OWNER_WORK = [_u("sprav ticket 12 do konca", T0), _a("Robím.", T0 + 5)]


def _latch(entries, **kw):
    return _os().scan(entries, **kw)[0]


class DailyBounds(unittest.TestCase):
    def test_every_cadence_bound_is_one_day(self):
        for name in ("NUDGE_TOTAL_GAP_S", "NUDGE_TOTAL_GAP_MIN_S",
                     "NUDGE_MIN_INTERVAL_S", "NUDGE_MIN_INTERVAL_MIN_S",
                     "U_RECONCILE_CADENCE_S", "U_RECONCILE_CADENCE_MIN_S",
                     "PANE_ATTEMPT_WINDOW_S"):
            self.assertEqual(getattr(ng, name), DAY, name)
        self.assertEqual(ng.PANE_ATTEMPT_BUDGET, 1)

    def test_env_can_only_raise_the_day(self):
        low = {"AIRULESET_NUDGE_TOTAL_GAP_S": "60",
               "AIRULESET_NUDGE_MIN_INTERVAL_S": "60",
               "AIRULESET_U_RECONCILE_CADENCE_S": "60"}
        with m.patch.dict(os.environ, low):
            self.assertEqual(ng._total_gap(), DAY)
            self.assertEqual(ng._min_interval(), DAY)
            self.assertEqual(ng._u_cadence(), DAY)

    def test_infra_priority_is_capped_like_any_kind(self):
        self.assertNotIn("infra-priority", ng.PRIORITY_CAP_EXEMPT_KINDS)
        self.assertEqual(ng._category_floor("infra-priority"), DAY)

    def test_watch_trigger_keeps_its_hourly_slots(self):
        self.assertEqual(ng.PRIORITY_CAP_EXEMPT_KINDS,
                         frozenset({"watch-trigger"}))
        self.assertEqual(ng._category_floor("watch-trigger"), HOUR)


class DailyCapAcrossKinds(unittest.TestCase):
    def test_one_delivery_closes_every_kind_for_a_day(self):
        for first in CAPPED:
            state = {}
            ng.mark_sent(state, "s", first, NOW)
            for kind in CAPPED:
                self.assertFalse(ng.gate_ok(state, "s", kind, NOW + DAY - 60),
                                 "%s after %s" % (kind, first))
                self.assertTrue(ng.gate_ok(state, "s", kind, NOW + DAY + 1),
                                "%s after %s" % (kind, first))

    def test_due_kinds_share_one_batch_per_day(self):
        state = {}
        due = ng.batch_eligible(state, "s", NOW)
        self.assertEqual(set(due), set(ng.GATED_CATEGORIES))
        text, incl = ng.compose_batch([(c, "x") for c in due],
                                      max_chars=ng.BATCH_MAX_CHARS)
        self.assertEqual(text.count(ng.BATCH_PREFIX), 1)
        ng.mark_batch_sent(state, "s", incl, NOW)
        self.assertEqual(ng.batch_eligible(state, "s", NOW + DAY - 60), [])
        self.assertTrue(ng.batch_eligible(state, "s", NOW + DAY + 1))

    def test_watch_trigger_neither_blocks_nor_counts(self):
        state = {}
        ng.mark_sent(state, "s", "watch-trigger", NOW)
        self.assertTrue(ng.gate_ok(state, "s", "release-gap", NOW + 60))
        ng.mark_sent(state, "s", "release-gap", NOW + 60)
        self.assertTrue(ng.gate_ok(state, "s", "watch-trigger", NOW + HOUR + 1))

    def test_one_pane_attempt_spends_the_day(self):
        state = {}
        ng.mark_pane_attempt(state, "%9", NOW)
        self.assertFalse(ng.pane_budget_ok(state, "%9", NOW + DAY - 60))
        self.assertTrue(ng.pane_budget_ok(state, "%9", NOW + DAY + 1))

    def test_budget_applies_to_capped_kinds_only(self):
        for kind in CAPPED:
            self.assertTrue(ng.pane_budgeted(kind), kind)
        for kind in ng.RECOVERY_NUDGE_KINDS | {"watch-trigger", None}:
            self.assertFalse(ng.pane_budgeted(kind), kind)
        self.assertFalse(ng.pane_budgeted("release-gap", user_authored=True))


class _Pane(unittest.TestCase):
    PID = "%9"
    CWD = "/home/newlevel/devel/nudgedaily"
    SID = "sess-daily"

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = Path(d.name)
        self.tpath = _write_marker_transcript(self.proj, self.CWD, self.SID)
        self.addCleanup(lambda: _os().reset())

    def _tmux(self, cap=GOAL_IDLE_CAP):
        return DeliverGoalFakeTmux([(self.PID, "claude", self.CWD, "111")], cap,
                                   model_type=True, transcript_path=self.tpath)

    def _send(self, nudge, state, user_authored=False):
        tmux, out, logs = self._tmux(), {}, []
        ok = wd.send_verified(self.PID, "nudge text for the pane", tmux,
                              self.tpath, sleep_fn=lambda s: None, logs=logs,
                              out=out, nudge=nudge, state=state,
                              user_authored=user_authored)
        return ok, tmux, out, logs

    def _append(self, *entries):
        with open(self.tpath, "a", encoding="utf-8") as h:
            for e in entries:
                h.write(json.dumps(e) + "\n")
        aged = time.time() - 120
        os.utime(self.tpath, (aged, aged))


class PaneBudgetDaily(_Pane):
    def test_a_second_capped_send_in_a_day_types_nothing(self):
        for kind in ("release-gap", "bounce", "card", "subagent-stuck"):
            state = {"nudge_pane_attempts": {self.PID: [time.time() - 23 * HOUR]}}
            ok, tmux, out, logs = self._send(kind, state)
            self.assertFalse(ok, kind)
            self.assertTrue(out.get("pane_budget_held"), (kind, logs))
            self.assertEqual(tmux.sent, [], kind)

    def test_recovery_and_watch_trigger_type_with_a_spent_day(self):
        for kind in ("resume", "wake-parked", "watch-trigger"):
            state = {"nudge_pane_attempts": {self.PID: [time.time() - 60]}}
            ok, tmux, out, _ = self._send(kind, state)
            self.assertTrue(ok, kind)
            self.assertFalse(out.get("pane_budget_held"), kind)

    def test_stash_route_obeys_the_daily_budget(self):
        state = {"nudge_pane_attempts": {self.PID: [time.time() - 60]}}
        tmux, logs = self._tmux(GOAL_DRAFT_CAP), []
        ok = wd.deliver_with_stash(self.PID, "bounce-backstop: x", tmux,
                                   captured=GOAL_DRAFT_CAP, logs=logs,
                                   sleep_fn=lambda s: None, state=state,
                                   nudge="bounce")
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("pane-budget" in ln for ln in logs), logs)

    def test_report_owed_hold_is_not_a_swallow(self):
        import test_report_owed_kind_off_1092 as ro
        now = int(time.time())
        state = {"nudge_pane_attempts": {"%42": [now - 60]}}
        pings = []
        for _ in range(4):
            logs, vs = ro.run(now, state, {7: now - 9999},
                              nudges_enabled=lambda k: True, vs_result=False,
                              send_fn=lambda *a, **k: pings.append(a))
            self.assertEqual(vs.calls, [], logs)
        self.assertEqual(pings, [], "a budget hold never escalates to the owner")
        self.assertTrue(any("pane-budget" in ln for ln in logs), logs)

    def test_dying_subagent_hold_does_not_count_a_nudge(self):
        sub = self.proj / "agent-x.jsonl"
        sub.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": True,
                                   "message": {"content": "API Error"}}) + "\n")
        state = {"nudge_pane_attempts": {self.PID: [time.time() - 60]}}
        logs, tmux = [], self._tmux()
        wd._nudge_dying_subagent(state, logs, lambda *a, **k: None, self.PID,
                                 tmux, GOAL_IDLE_CAP, "proj", "owner",
                                 time.time(), sub, 900, "api-error", "apierr",
                                 600, 3, False, tpath=self.tpath,
                                 sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])
        self.assertNotIn("subagent-apierr:agent-x", state)
        self.assertTrue(any("pane-budget" in ln for ln in logs), logs)


class LatchFacts(unittest.TestCase):
    def test_each_structured_fact_sets_the_latch(self):
        for fact, entries in FACTS.items():
            latch = _latch(OWNER_WORK + entries)
            self.assertIsNotNone(latch, fact)
            self.assertEqual(latch["fact"], fact)

    def test_pause_words_slovak_and_english(self):
        for text in ("pauzni", "zastav sa", "stop", "nepokracuj",
                     "skonči na dnes", "Please stop now", "pause", "halt"):
            latch = _latch(OWNER_WORK + [_u(text, T0 + 10)])
            self.assertEqual((latch or {}).get("fact"), "pause", text)
        for text in ("Pauza, idem preč", "nepokračuj, zajtra",
                     "pause the loop", "prestaň"):   # not EXACTLY a stop phrase
            self.assertIsNone(_latch(OWNER_WORK + [_u(text, T0 + 10)]), text)

    def test_stop_words_inside_other_words_never_latch(self):
        for text in ("zastavenie servera oprav prosím", "pozri stopky",
                     "nonstop beh", "oprav stop-hook v kóde", "pauzovanie",
                     "skončí to dnes?", "oprav stop hook", "Stop hooks padajú",
                     "stop condition je zlá"):
            self.assertIsNone(_latch(OWNER_WORK + [_u(text, T0 + 10)]), text)

    def test_machine_prompts_never_latch(self):
        for text in ("continue", "nudge: [release-gap] stop the release gap",
                     "stuck-check: zastav sa a over worker",
                     "<task-notification>stop</task-notification>",
                     "Stop hook feedback: stop"):
            self.assertIsNone(_latch(OWNER_WORK + [_u(text, T0 + 10)]), text)

    def test_watchdogs_own_disarm_is_no_owner_stop(self):
        entries = OWNER_WORK + FACTS["goal-cleared"]
        self.assertIsNone(_latch(entries, machine_clears=(T0 + 5,)))
        # once matched, the clear stays machine after its veto is reaped: the
        # match lives in the session's latch record (`mclears`)
        self.assertIsNone(_latch(entries, rec={"mclears": [T0 + 11]}))
        # a veto of ANOTHER episode never makes the owner's own clear machine
        self.assertIsNotNone(_latch(entries, machine_clears=(T0 - DAY,)))


class LatchClears(unittest.TestCase):
    def test_a_new_owner_prompt_clears(self):
        for fact, entries in FACTS.items():
            self.assertIsNone(_latch(OWNER_WORK + entries
                                     + [_u("pokračuj s ticketom 5", T0 + 60)]),
                              fact)

    def test_machine_prompts_never_clear(self):
        tail = [_u("continue", T0 + 60), _u("nudge: [u-freshness] x", T0 + 70),
                _u("<task-notification>done</task-notification>", T0 + 80),
                _u("oauth-resume: x", T0 + 85), _a("ok", T0 + 90)]
        for fact, entries in FACTS.items():
            latch = _latch(OWNER_WORK + entries + tail)
            self.assertEqual((latch or {}).get("fact"), fact)

    def test_an_interrupt_is_not_a_clearing_prompt(self):
        latch = _latch(OWNER_WORK + FACTS["pause"] + FACTS["interrupt"])
        self.assertEqual(latch["fact"], "interrupt")

    def test_an_owner_slash_command_clears(self):
        entries = OWNER_WORK + FACTS["exit"] + [_u(
            "<command-message>autopilot is running…</command-message>\n"
            "<command-name>/autopilot</command-name>", T0 + 60)]
        self.assertIsNone(_latch(entries))

    def test_a_persisted_latch_outlives_the_tail(self):
        rec = {"fact": "pause", "ts": T0 + 10}
        neutral = [_u("continue", T0 + 60), _a("ok", T0 + 61)]
        self.assertEqual(_latch(neutral, rec=rec)["fact"], "pause")
        older = [_u("stará inštrukcia", T0)]
        self.assertEqual(_latch(older + neutral, rec=rec)["fact"], "pause")
        self.assertIsNone(_latch(neutral + [_u("ďalej", T0 + 99)], rec=rec))


class _Latched(_Pane):
    def setUp(self):
        super().setUp()
        self._append(*(OWNER_WORK + FACTS["pause"]))
        self.state, self.logs = {}, []
        self.logs += _os().refresh(self.state, [(self.PID, self.CWD)],
                                   self.proj, NOW)


class LatchBlocksEveryKind(_Latched):
    def test_no_keystroke_kind_enters_a_latched_pane(self):
        calls = []
        run = lambda argv, timeout=8: calls.append(argv) or ""  # noqa: E731
        kinds = [("send", "release-gap"), ("send", "resume"),
                 ("continue", "resume"), ("goal", "goal-arm"),
                 ("goal", "goal-disarm"), ("send", "watch-trigger"),
                 ("send", "model-restore"), ("janitor", None), ("undo", None),
                 ("wedge", None), ("user-draft", None)]
        for kind, nudge in kinds:
            self.assertFalse(wd.keys(self.PID, "Enter", kind=kind, nudge=nudge,
                                     run=run), (kind, nudge))
        self.assertEqual(calls, [])

    def test_owner_reply_and_other_panes_pass(self):
        calls = []
        run = lambda argv, timeout=8: calls.append(argv) or ""  # noqa: E731
        self.assertTrue(wd.keys(self.PID, "Enter", kind="send", run=run,
                                user_authored=True))
        self.assertTrue(wd.keys("%8", "Enter", kind="send", nudge="resume",
                                run=run))
        self.assertEqual(len(calls), 2)

    def test_send_verified_recovery_aborts_before_typing(self):
        ok, tmux, _, logs = self._send("resume", {})
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("owner-stop latch" in ln for ln in logs), logs)

    def test_relaunch_pane_refuses(self):
        calls = []
        self.assertFalse(wd.relaunch_pane(
            self.PID, run=lambda argv, timeout=8: calls.append(argv) or ""))
        self.assertEqual(calls, [])

    def test_refresh_journals_once_per_episode(self):
        latch_lines = [ln for ln in self.logs if "owner-stop latch" in ln]
        self.assertEqual(len(latch_lines), 1, self.logs)
        again = _os().refresh(self.state, [(self.PID, self.CWD)], self.proj,
                              NOW + 60)
        self.assertFalse(any("owner-stop latch" in ln for ln in again), again)
        self.assertIn(self.SID, self.state[_os().STATE_KEY])

    def test_only_a_new_owner_prompt_clears_it(self):
        self._append(_u("continue", T0 + 60), _u("nudge: x", T0 + 61))
        _os().refresh(self.state, [(self.PID, self.CWD)], self.proj, NOW + 60)
        self.assertIsNotNone(_os().pane_latch(self.PID))
        self._append(_u("pokračuj s ticketom 5", T0 + 90))
        logs = _os().refresh(self.state, [(self.PID, self.CWD)], self.proj,
                             NOW + 120)
        self.assertIsNone(_os().pane_latch(self.PID))
        self.assertTrue(any("cleared" in ln for ln in logs), logs)
        ok, _, _, _ = self._send("resume", {})
        self.assertTrue(ok)


class LatchInherits(_Pane):
    def test_a_fresh_session_inherits_the_previous_exit(self):
        self._append(*(OWNER_WORK + FACTS["exit"]))
        old = time.time() - 600
        os.utime(self.tpath, (old, old))
        fresh = self.tpath.parent / "sess-fresh.jsonl"
        fresh.write_text(json.dumps(_a("Ready.", T0 + 300)) + "\n")
        self.assertEqual(_os().cwd_latch(self.proj, self.CWD)["fact"], "exit")
        with open(fresh, "a", encoding="utf-8") as h:
            h.write(json.dumps(_u("ahoj, pokračuj", T0 + 400)) + "\n")
        self.assertIsNone(_os().cwd_latch(self.proj, self.CWD))


class LatchExitPane(_Pane):
    """Claude Code writes NO transcript record for `/exit` (2.1.306, probed
    26.10.2026), so the exit fact is the tmux pane itself: a pane at a bare
    shell whose cwd no live claude pane serves latches the cwd's newest
    session, and the session the owner starts there next inherits it."""

    def _inventory(self, cmd):
        line = "%s\t%s\t%s\t4242" % (self.PID, cmd, self.CWD)
        return lambda argv, timeout=8: line if "list-panes" in argv else ""

    def test_a_bare_shell_pane_latches_until_the_owner_types(self):
        self._append(*OWNER_WORK)
        state = {}
        logs = _os().refresh(state, [], self.proj, NOW, run=self._inventory("bash"))
        self.assertEqual(state[_os().STATE_KEY][self.SID]["fact"], "exit", logs)
        self.assertTrue(any("exit" in ln for ln in logs), logs)
        old = time.time() - 600
        os.utime(self.tpath, (old, old))
        fresh = self.tpath.parent / "sess-restarted.jsonl"
        fresh.write_text(json.dumps(_a("Ready.", T0 + 300)) + "\n")
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW + 60,
                      run=self._inventory("claude"))
        self.assertEqual(_os().pane_latch(self.PID)["fact"], "exit")
        self.assertFalse(wd.keys(self.PID, "Enter", kind="send", nudge="resume",
                                 run=lambda argv, timeout=8: ""))
        with open(fresh, "a", encoding="utf-8") as h:
            h.write(json.dumps(_u("ahoj, pokračuj", T0 + 400)) + "\n")
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW + 120,
                      run=self._inventory("claude"))
        self.assertIsNone(_os().pane_latch(self.PID))

    def test_a_shell_beside_a_live_claude_is_no_exit(self):
        state = {}
        _os().refresh(state, [("%8", self.CWD)], self.proj, NOW,
                      run=self._inventory("bash"))
        self.assertNotIn(_os().STATE_KEY, state)


class LatchRefusesArm(_Latched):
    def setUp(self):
        super().setUp()
        _isolate_goal_state(self)

    def test_no_origin_arms_a_latched_session(self):
        self.assertIn("drop:owner-stop", goal._GOAL_TERMINAL_WORDS)
        for origin in (None, "self-callback", "dark-rearm", "auth-rearm"):
            tmux = self._tmux()
            word = goal.deliver_goal(self.SID, self.CWD, "/goal STOP CONDITIONS x",
                                     "full", run=tmux, projects_dir=self.proj,
                                     now=time.time(), state={},
                                     sleep_fn=lambda s: None, origin=origin)
            self.assertEqual(word, "drop:owner-stop", origin)
            self.assertEqual(tmux.sent, [], origin)


if __name__ == "__main__":
    unittest.main()
