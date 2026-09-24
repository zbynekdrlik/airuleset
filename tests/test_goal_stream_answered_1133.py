"""#1133 — a stream `/goal` loop that ended on an owner question (stop (A))
resumes after the owner ANSWERS it.

After #1128 a sub-dev stream loop ends only via stop (A): the last assistant
message ends `❓ NEEDS YOU` and no lane is live. Claude Code leaves the
structured goal mark "set", the footer goes dark, and when the owner answers
nothing re-arms the loop (the #1128 watcher is migration-only and every other
origin is dropped by the #1113 structured-armed refusal).

The design (Design-by: main on #1133, Approach 1): a second "answered-A"
trigger in `watchdog/stream_migrate.py` over the SAME `eligible()` core and the
same `stream-migrate` origin. The load-bearing guard: a REAL human prompt after
the loop's LAST `❓ NEEDS YOU` (a `/goal` command, a task-notification or a
hook/system record does NOT count), and that `❓` is at or after the current arm.

Every test drives the PRODUCTION sweep shape: `goal_dark_watch` then
`deliver_goal` with the SAME sweep `state` (goal_mark "set").

#1143 re-pin: the answered-A trigger is now a CASE of the ONE stream rule (no
open `❓` after the arm), so an answered loop logs the rule's `STREAM-MIGRATE`
label, a `❓` that predates the arm is not an open question (re-armed), and the
memoized read is `open_question` (the unknown-arm fail-closed lives there).
"""

import json
import os
import sys
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import goal_registry as gr  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import stream_migrate as sm  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    GOAL_IDLE_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

_FIX = json.loads((Path(__file__).resolve().parent / "fixtures"
                   / "pre_1128_stream_goals.json").read_text(encoding="utf-8"))
OLD_FORK = _FIX["fork-no-merge"]
NEW_FORK = gr.render("fork-no-merge")
NEW_BRANCH = gr.render("branch-merge")
CWD = "/home/david1/devel/odoo-erp"
_Q = "Potrebujem rozhodnutie o faktúre.\n❓ NEEDS YOU: ktorá možnosť A/B?"
_ANSWER = "Vyber možnosť A, faktúru posielaj mesačne."
_AFTER = "Hotovo podľa odpovede.\n✅ DONE: faktúra nastavená"

# --- user-entry shapes that are NOT a human answer --------------------------- #
_GOAL_CMD = ("<command-name>/goal</command-name>\n"
             "<command-message>goal</command-message>\n<command-args></command-args>")
_TASK_NOTE = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed"
              "</status>\n<summary>stream-wait exited</summary>\n"
              "</task-notification>")


# the watchdog's OWN typed keystrokes (#1133 review): never the owner's answer
_OWN_NUDGES = ("nudge: [u-freshness] stuck-check: U-reconcile",
               "report-owed: montalu — napíš ## ✅ Work Complete",
               "lane-check: 2 lanes", "goal-guard: x", "recheck: y",
               "UNPARK-AUDIT: z", "stuck-check: w", "bounce-backstop: v",
               "gk-request backstop: u", "resume",
               "task-hygiene: A=1 B=0 C=0 — klientske úlohy čakajú na teba.",
               "lane-reconcile: 1 worktree lane returned during a compact",
               "Discord pripomienka: ticket #7 bol znovu otvorený",
               "Užívateľ označil túto tvoju Discord správu ikonkou ❓ (x)")


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _asst(ts, text, mid):
    return {"type": "assistant", "timestamp": _iso(ts),
            "message": {"id": mid, "content": text}}


def _user(ts, text, **extra):
    e = {"type": "user", "timestamp": _iso(ts),
         "message": {"role": "user", "content": text}}
    e.update(extra)
    return e


def _answered_tail():
    return [_asst(600, _Q, "q"), _user(700, _ANSWER), _asst(800, _AFTER, "a")]


class TestStreamPayloadRecognition(unittest.TestCase):
    def test_every_current_reduced_variant_is_a_stream_payload(self):
        n = 0
        for a in sm.STREAM_AUTHORITIES:
            for mode, role in (("parallel", None), ("sequential", None),
                               ("sequential", "infra")):
                line = gr.render_goal_line(a, mode, role)
                self.assertTrue(sm.is_stream_payload(line), (a, mode, role))
                self.assertTrue(sm.is_stream_payload(line[len("/goal "):]))
                n += 1
        self.assertEqual(n, 6)

    def test_old_stream_templates_are_stream_payloads(self):
        self.assertTrue(sm.is_stream_payload(OLD_FORK))
        self.assertTrue(sm.is_stream_payload(_FIX["branch-merge"]))

    def test_no_full_template_and_no_hand_goal_is_a_stream_payload(self):
        for spec in gr.variant_specs():
            if spec[0] == "full":
                self.assertFalse(sm.is_stream_payload(gr.render_goal_line(*spec)),
                                 spec)
        self.assertFalse(sm.is_stream_payload(gr.render_goal_line(
            "full", "parallel", "review")))
        self.assertFalse(sm.is_stream_payload(_FIX["full"]))
        for p in (None, "", "/goal fix the tests", "stream-wait"):
            self.assertFalse(sm.is_stream_payload(p), p)

    def test_a_soft_wrapped_payload_still_matches(self):
        wrapped = NEW_FORK.replace(" ", "\n", 40)
        self.assertTrue(sm.is_stream_payload(wrapped))


class TestAnsweredProof(unittest.TestCase):
    """`question_state(tpath, mark_ts)` == "answered" -- the answer proof the
    #1143 rule's open-question guard is built on."""

    def _t(self, entries, mark_ts=500):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "s.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in entries),
                     encoding="utf-8")
        st, why = sm.question_state(p, mark_ts)
        return st == "answered", why

    def test_a_human_answer_after_the_last_question_is_answered(self):
        self.assertEqual(self._t(_answered_tail())[0], True)

    def test_a_discord_relayed_answer_counts(self):
        ok, _w = self._t([_asst(600, _Q, "q"),
                          _user(700, "Odpoveď z Discordu: A"),
                          _asst(800, _AFTER, "a")])
        self.assertTrue(ok)

    def test_machine_and_system_records_never_answer(self):
        for label, tail in (
                ("nothing", []),
                ("goal-cmd", [_user(700, _GOAL_CMD)]),
                ("bare-goal", [_user(700, "/goal")]),
                ("goal-args", [_user(700, "/goal status")]),
                ("task-note", [_user(700, _TASK_NOTE), _asst(710, _AFTER, "t")]),
                ("meta", [_user(700, "<system-reminder>x</system-reminder>",
                                isMeta=True)]),
                ("stop-hook", [_user(700, "Stop hook feedback: marker")]),
                ("system", [{"type": "system", "subtype": "stop_hook_summary",
                             "timestamp": _iso(700), "content": "hook ran"}]),
                ("tool-result", [_user(700, [{"type": "tool_result",
                                              "tool_use_id": "t",
                                              "content": "ok"}])]),
                ("continue", [_user(700, "continue")]),
        ) + tuple((t, [_user(700, t), _asst(710, _AFTER, "m")])
                  for t in _OWN_NUDGES):
            ok, why = self._t([_asst(600, _Q, "q")] + tail)
            self.assertFalse(ok, label)
            self.assertTrue(why, label)

    def test_the_last_question_decides_not_an_earlier_answered_one(self):
        ok, _w = self._t(_answered_tail() + [
            _asst(900, "Ďalšia otázka.\n❓ NEEDS YOU: pokračovať?", "q2")])
        self.assertFalse(ok)

    def test_an_asked_and_continue_turn_is_not_a_needs_you(self):
        ok, _w = self._t(_answered_tail() + [
            _asst(900, "❓ ASKED: farba?\nPokračujem.\n⏳ WORKING: lane", "k")])
        self.assertTrue(ok)

    def test_a_question_before_the_arm_never_counts(self):
        ok, why = self._t(_answered_tail(), mark_ts=650)
        self.assertFalse(ok)
        self.assertIn("arm", why)

    def test_fail_closed_on_an_unknown_arm_time(self):
        # #1143 re-pin: the fail-closed moved to the rule's guard -- an unknown
        # arm time makes the question OPEN (never re-arm over it).
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "s.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in _answered_tail()),
                     encoding="utf-8")
        self.assertTrue(sm.open_question(p, None)[0])

    def test_no_question_in_the_tail(self):
        self.assertFalse(self._t([_asst(600, _AFTER, "x")])[0])

    def test_a_long_answered_turn_still_proves_the_answer(self):
        # the owner's answer triggers a real work turn: hundreds of tool
        # entries follow the answer before the session idles (the ❓ must not
        # scroll out of a 200-entry window).
        tail = [_asst(600, _Q, "q"), _user(700, _ANSWER)]
        for k in range(600):
            tail.append({"type": "assistant", "timestamp": _iso(701),
                         "message": {"id": "tu%d" % k, "content": [
                             {"type": "tool_use", "id": "t%d" % k,
                              "name": "Bash", "input": {"command": "x" * 200}}]}})
            tail.append(_user(701, [{"type": "tool_result", "tool_use_id":
                                     "t%d" % k, "content": "y" * 300}]))
        tail.append(_asst(800, _AFTER, "a"))
        self.assertTrue(self._t(tail)[0])


class TestStreamAnsweredRearm(unittest.TestCase):
    # A dark-watch CONFIRMATION RUN (#524): 8 dark reads over >= 10 min, the
    # last at `self.now`; the transcript was last written 25 min before that.
    RUN = [-630 + 90 * k for k in range(8)]

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        self.now = float(int(time.time()))

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, payload=NEW_FORK, tail=None, idle_s=1500,
                 mark="Goal set: ", mark_ts=500):
        proj = self._dir()
        tpath = _write_marker_transcript(proj, CWD, sid, "warmup")
        _write_goal_marker(proj, CWD, sid, mark + payload, ts_epoch=mark_ts)
        tail = _answered_tail() if tail is None else tail
        with open(tpath, "a", encoding="utf-8") as f:
            for e in tail:
                f.write(json.dumps(e) + "\n")
        self._age(tpath, idle_s)
        return proj

    def _age(self, tpath, idle_s):
        aged = self.now - idle_s
        os.utime(tpath, (aged, aged))

    def _sweep(self, proj, cap=GOAL_IDLE_CAP, authority="fork-no-merge",
               state=None, cmd="claude", now=None, tmpl=None, dry_run=False):
        state = {} if state is None else state
        tmux = DeliverGoalFakeTmux([("%9", cmd, CWD, "111")], cap)
        now = self.now if now is None else now
        tmpl = NEW_FORK if tmpl is None else tmpl
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state=state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (0, now),
            rearm_fn=lambda cwd: (tmpl, authority),
            requests_path=self.reqp, dry_run=dry_run)
        return goal.load_goal_requests(self.reqp), logs, state, tmux

    def _run(self, proj, base=0, state=None, caps=None, **kw):
        """The production cadence: one sweep per RUN offset (relative to
        `self.now + base`) with the SAME state; `caps` overrides the pane
        render per sweep. Returns (requests, all logs, state, last tmux)."""
        state = {} if state is None else state
        logs = []
        for k, off in enumerate(self.RUN):
            cap = caps[k] if caps else kw.pop("cap", GOAL_IDLE_CAP)
            reqs, lg, state, tmux = self._sweep(proj, state=state, cap=cap,
                                                now=self.now + base + off, **kw)
            logs += lg
        return reqs, logs, state, tmux

    def _deliver(self, proj, sid, state, cap=GOAL_IDLE_CAP, cmd="claude",
                 req=None):
        req = req or goal.load_goal_requests(self.reqp)[sid]
        live = DeliverGoalFakeTmux([("%9", cmd, CWD, "111")], cap,
                                   model_type=True)
        with unittest.mock.patch.object(
                wd, "_goal_autoarm_recent_human_activity",
                return_value=(False, "")):
            word = goal.deliver_goal(
                sid, CWD, req["text"], req["authority"], run=live,
                projects_dir=proj, now=self.now + 30, origin=req["origin"],
                request_ts=req["ts"], sleep_fn=lambda s: None, state=state)
        return word, live

    def _forced(self, authority="fork-no-merge"):
        return {"text": NEW_FORK, "authority": authority,
                "origin": sm.ORIGIN, "ts": self.now}

    def _assert_never(self, proj, sid, state, reqs, reason, **deliver_kw):
        """Nothing recorded, and a FORCED stream-migrate request is refused at
        the #1113 structured-armed refusal for THIS guard's `reason`."""
        self.assertNotEqual((reqs.get(sid) or {}).get("origin"), sm.ORIGIN)
        word, live = self._deliver(proj, sid, state, req=self._forced(),
                                   **deliver_kw)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])
        self.assertIn(reason, Path(self.syncp).read_text())

    # --- fires -------------------------------------------------------------- #
    def test_answered_question_re_arms_the_current_template(self):
        proj = self._fixture("a-ok")
        reqs, logs, state, tmux = self._run(proj)
        req = reqs.get("a-ok")
        self.assertIsInstance(req, dict, logs)
        self.assertEqual(req["origin"], sm.ORIGIN)
        self.assertEqual(req["text"], NEW_FORK)
        self.assertEqual(tmux.sent, [], "dark-watch only records")
        self.assertTrue(any("STREAM-MIGRATE" in ln for ln in logs), logs)
        self.assertEqual(state["goal_mark"]["a-ok"]["mark"]["state"], "set")
        word, live = self._deliver(proj, "a-ok", state)
        self.assertEqual(word, "sent", Path(self.syncp).read_text())
        self.assertTrue(any("-l" in a for a in live.sent), live.sent)
        self.assertIn("PASS structured-armed", Path(self.syncp).read_text())

    def test_branch_merge_answered_re_arms(self):
        proj = self._fixture("a-bm", payload=NEW_BRANCH)
        reqs, _l, state, _t = self._run(proj, authority="branch-merge",
                                        tmpl=NEW_BRANCH)
        self.assertEqual(reqs["a-bm"]["origin"], sm.ORIGIN)
        self.assertEqual(reqs["a-bm"]["text"], NEW_BRANCH)
        self.assertEqual(self._deliver(proj, "a-bm", state)[0], "sent")

    def test_an_old_template_loop_that_ended_on_a_question_re_arms_too(self):
        proj = self._fixture("a-old", payload=OLD_FORK)
        reqs, _l, state, _t = self._run(proj)
        self.assertEqual(reqs["a-old"]["origin"], sm.ORIGIN)
        self.assertEqual(reqs["a-old"]["text"], NEW_FORK)
        self.assertEqual(self._deliver(proj, "a-old", state)[0], "sent")

    def test_a_discord_relayed_answer_re_arms(self):
        proj = self._fixture("a-dc", tail=[
            _asst(600, _Q, "q"), _user(700, "Odpoveď z Discordu: A"),
            _asst(800, _AFTER, "a")])
        reqs, _l, state, _t = self._run(proj)
        self.assertEqual(reqs["a-dc"]["origin"], sm.ORIGIN)

    # --- never: an unconfirmed dark footer (#524) ----------------------------- #
    def test_never_on_a_single_dark_read(self):
        proj = self._fixture("c-one")
        reqs, logs, _s, _t = self._sweep(proj)
        self.assertEqual(reqs, {})
        self.assertTrue(any("not yet confirmed" in ln for ln in logs), logs)

    def test_an_armed_read_restarts_the_confirmation_run(self):
        # an idle-but-ALIVE loop whose glyph flickers: one ◎ read mid-run.
        proj = self._fixture("c-flick")
        caps = [GOAL_IDLE_CAP] * 8
        caps[4] = GOAL_ARMED_CAP
        reqs, _l, state, _t = self._run(proj, caps=caps)
        self.assertEqual(reqs, {})
        reqs, _l, _s, _t = self._run(proj, base=720, state=state)
        self.assertEqual(reqs["c-flick"]["origin"], sm.ORIGIN)

    # --- never: unanswered ---------------------------------------------------- #
    def test_never_while_the_question_is_the_last_turn(self):
        proj = self._fixture("u-last", tail=[_asst(600, _Q, "q")])
        reqs, _l, state, _t = self._run(proj)
        self.assertEqual(reqs, {})
        self._assert_never(proj, "u-last", state, reqs, "is unanswered")

    def test_never_when_only_a_goal_command_followed(self):
        for sid, text in (("u-gcmd", _GOAL_CMD), ("u-gbare", "/goal")):
            proj = self._fixture(sid, tail=[_asst(600, _Q, "q"),
                                            _user(700, text)])
            reqs, logs, state, _t = self._run(proj)
            self._assert_never(proj, sid, state, reqs, "is unanswered")

    def test_never_when_only_a_task_notification_followed(self):
        proj = self._fixture("u-note", tail=[
            _asst(600, _Q, "q"), _user(700, _TASK_NOTE),
            _asst(710, "Waiter skončil, nič nové.\n✅ DONE: nič", "n")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_never(proj, "u-note", state, reqs, "is unanswered")

    def test_never_when_only_a_watchdog_nudge_followed(self):
        for k, text in enumerate(_OWN_NUDGES[:2]):
            sid = "u-nudge%d" % k
            proj = self._fixture(sid, tail=[
                _asst(600, _Q, "q"), _user(700, text),
                _asst(710, "U opravené.\n✅ DONE: U", "n")])
            reqs, _l, state, _t = self._run(proj)
            self._assert_never(proj, sid, state, reqs, "is unanswered")

    def test_never_when_only_hook_or_system_records_followed(self):
        proj = self._fixture("u-hook", tail=[
            _asst(600, _Q, "q"),
            _user(700, "<system-reminder>x</system-reminder>", isMeta=True),
            _user(701, "Stop hook feedback: marker missing"),
            {"type": "system", "subtype": "stop_hook_summary",
             "timestamp": _iso(702), "content": "hook ran"},
            _asst(710, "Opravené.\n✅ DONE: marker", "h")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_never(proj, "u-hook", state, reqs, "is unanswered")

    def test_a_question_before_the_arm_is_not_an_open_question(self):
        # #1143 re-pin: this locked the answered trigger's own proof ("the ❓
        # must follow the arm"). Under the one rule a ❓ before the current arm
        # is not an open question -- the owner re-armed after it -> re-armed.
        proj = self._fixture("u-pre", mark_ts=900)
        reqs, _l, state, _t = self._run(proj)
        self.assertEqual(reqs["u-pre"]["origin"], sm.ORIGIN)
        self.assertEqual(self._deliver(proj, "u-pre", state)[0], "sent")

    def _new_question_before_delivery(self, proj, sid):
        tpath = next(proj.rglob(sid + ".jsonl"))
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(_asst(900, "Ešte jedna vec.\n"
                                     "❓ NEEDS YOU: potvrdíš?", "q2")) + "\n")
        self._age(tpath, 1200)

    def test_delivery_rechecks_the_answer(self):
        # recorded while answered; a NEW unanswered ❓ turn lands before the
        # delivery (and the pane goes quiet again) -> never typed.
        for sid, payload in (("u-toctou", NEW_FORK), ("u-toctou-old", OLD_FORK)):
            proj = self._fixture(sid, payload=payload)
            _r, _l, state, _t = self._run(proj)
            self._new_question_before_delivery(proj, sid)
            word, live = self._deliver(proj, sid, state)
            self.assertEqual(word, "drop:already-armed", sid)
            self.assertEqual(live.sent, [], sid)
        self.assertIn("is unanswered", Path(self.syncp).read_text())

    def test_delivery_refuses_a_newer_foreign_transcript(self):
        proj = self._fixture("u-sid")
        _r, _l, state, _t = self._run(proj)
        other = _write_marker_transcript(proj, CWD, "u-other", "x")
        self._age(other, 900)                 # newer than ours, still idle
        word, live = self._deliver(proj, "u-sid", state)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])
        self.assertIn("not this session's", Path(self.syncp).read_text())

    # --- never: owner stop / live / busy / shell / not idle -------------------- #
    def test_never_on_an_owner_cleared_goal(self):
        proj = self._fixture("n-clr", mark="Goal cleared: ")
        reqs, _l, _s, _t = self._run(proj)
        self.assertEqual(reqs, {})

    def test_never_on_an_armed_pane(self):
        proj = self._fixture("n-armed")
        reqs, _l, state, _t = self._run(proj, cap=GOAL_ARMED_CAP)
        self.assertEqual(reqs, {})
        word, live = self._deliver(proj, "n-armed", state, cap=GOAL_ARMED_CAP,
                                   req=self._forced())
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_never_on_a_busy_pane(self):
        proj = self._fixture("n-busy")
        reqs, _l, _s, _t = self._run(proj, cap=GOAL_BUSY_CAP)
        self.assertEqual(reqs, {})
        _r, _l, state, _t = self._run(proj)               # recorded idle...
        word, live = self._deliver(proj, "n-busy", state, cap=GOAL_BUSY_CAP)
        self.assertTrue(word.startswith("skip:"), word)    # ...never typed busy
        self.assertEqual(live.sent, [])

    def test_never_on_a_bare_shell(self):
        proj = self._fixture("n-shell")
        reqs, _l, _s, _t = self._run(proj, cmd="bash")
        self.assertEqual(reqs, {})
        _r, _l, state, _t = self._run(proj)
        word, live = self._deliver(proj, "n-shell", state, cmd="bash")
        self.assertNotEqual(word, "sent")
        self.assertEqual(live.sent, [])

    def test_never_on_a_recently_written_transcript(self):
        proj = self._fixture("n-fresh", idle_s=300)
        reqs, logs, _s, _t = self._sweep(proj)
        self.assertEqual(reqs, {})

    def test_never_on_a_foreign_hand_armed_goal(self):
        proj = self._fixture("n-foreign", payload="/goal oprav testy v module X")
        reqs, _l, state, _t = self._run(proj)
        self._assert_never(proj, "n-foreign", state, reqs,
                           "not a stream template")

    # --- a full box is unchanged ---------------------------------------------- #
    def test_a_full_box_is_unchanged(self):
        full = gr.render("full")
        proj = self._fixture("f-full", payload=full)
        reqs, _l, state, _t = self._run(proj, authority="full", tmpl=full)
        self.assertNotEqual((reqs.get("f-full") or {}).get("origin"), sm.ORIGIN)
        self.assertNotIn("goal_stream_migrate", state)
        self.assertNotIn("goal_stream_answered_memo", state)
        word, live = self._deliver(proj, "f-full", state,
                                   req=self._forced("full"))
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    # --- shared #1128 guards: 1/h, dry-run, memo ------------------------------ #
    def test_one_attempt_per_session_per_hour(self):
        proj = self._fixture("r-rate", idle_s=2000)
        _r, _l, state, _t = self._run(proj)
        goal.clear_goal_request("r-rate", path=self.reqp)
        reqs, logs, state, _t = self._run(proj, base=1800, state=state)
        self.assertEqual(reqs, {}, "a second attempt inside the hour")
        self.assertTrue(any("1/h" in ln for ln in logs), logs)
        reqs, _l, _s, _t = self._run(proj, base=3700, state=state)
        self.assertEqual(reqs["r-rate"]["origin"], sm.ORIGIN)

    def test_the_proof_is_read_once_per_transcript_change(self):
        # a dark idle pane is swept every 60 s; its unchanged transcript must
        # not be re-read (the bounded 8 MB tail) on every sweep.
        proj = self._fixture("r-memo", tail=[_asst(600, _Q, "q"),
                                             _user(700, _TASK_NOTE),
                                             _asst(710, _AFTER, "n")])
        real, calls = sm.open_question, []

        def _count(*a):
            calls.append(a)
            return real(*a)
        state = {}
        with unittest.mock.patch.object(sm, "open_question", _count):
            for k in range(3):
                reqs, _l, state, _t = self._sweep(proj, state=state,
                                                  now=self.now + k * 60)
            self.assertEqual(len(calls), 1, calls)
            tpath = next(proj.rglob("r-memo.jsonl"))
            self._age(tpath, 1100)                     # the transcript changed
            for k in (4, 5):   # 1st: the mtime-advance liveness veto; 2nd: re-read
                reqs, _l, state, _t = self._sweep(proj, state=state,
                                                  now=self.now + k * 60)
        self.assertEqual(len(calls), 2)
        self.assertEqual(reqs, {})

    def test_the_run_advances_once_per_sweep_on_a_fall_through(self):
        # an unresolvable template hands the pane to the dead-loop path, which
        # advances the SAME #524 run -- this trigger must not advance it too.
        proj = self._fixture("r-once")
        state = {}
        for k in range(3):
            self._sweep(proj, state=state, now=self.now + k * 60, tmpl="")
        self.assertEqual(state["goal_dark_confirm"]["r-once"]["clean_run"], 2)

    def test_a_migration_never_records_over_an_unanswered_question(self):
        # an old-template loop asked ❓ with a lane live (stop (A) held off), a
        # task-notification woke it and it printed 🏁: the question is open.
        proj = self._fixture("m-q", payload=OLD_FORK, tail=[
            _asst(600, _Q, "q"), _user(650, _TASK_NOTE),
            _asst(700, "Hotovo.\n🏁 BACKLOG EMPTY: 0 open, released\n"
                       "✅ DONE: slice prázdny", "b")])
        reqs, logs, state, _t = self._sweep(proj)
        self.assertNotEqual((reqs.get("m-q") or {}).get("origin"), sm.ORIGIN)
        # #1143 re-pin: the one rule journals its skip in the dark-watch log
        # (the migration's goal-sync side journal left with the migration block)
        self.assertTrue(any("stream-migrate SKIP: the last ❓ NEEDS YOU is "
                            "unanswered" in ln for ln in logs), logs)
        self._assert_never(proj, "m-q", state, reqs, "is unanswered")

    def test_dry_run_mutates_no_state(self):
        proj = self._fixture("r-dry")
        state = {}
        for off in self.RUN[:-1]:        # 7 real reads: not yet confirmed
            reqs, _l, state, _t = self._sweep(proj, state=state,
                                              now=self.now + off)
        self.assertEqual(reqs, {})
        keys = ("goal_stream_migrate", "goal_stream_answered_memo",
                "goal_dark_confirm")
        before = json.dumps({k: state.get(k) for k in keys}, sort_keys=True)
        reqs, logs, state, _t = self._sweep(proj, state=state, dry_run=True)
        self.assertEqual(reqs, {})
        self.assertEqual(json.dumps({k: state.get(k) for k in keys},
                                    sort_keys=True), before)
        self.assertTrue(any("would record" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
