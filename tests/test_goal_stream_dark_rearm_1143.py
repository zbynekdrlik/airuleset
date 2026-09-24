"""#1143 — ONE stream-watcher rule: a DARK stream loop the owner did not end is
re-armed.

A stream session that was relaunched, or crashed and was resumed, comes back
without its `/goal` (live: david2 and david3 after the #1142 respawn,
24.9.2026). Its structured goal mark still reads "set" and its payload is the
CURRENT stream template, and it ended on neither a 🏁 (the #1128 migration
trigger) nor an answered ❓ (the #1133 answered-A trigger). Every other re-arm
origin is dropped by the #1113 structured-armed refusal, so the loop stays
dead until the owner types `/autopilot`.

The design (Design-by: main on #1143, Approach 1) replaces the two special
triggers with ONE rule. On a reduced-authority stream box, the CURRENT stream
template is re-armed when ALL of these hold:
- the mark is "set", never "cleared" (an owner `/goal clear` is the stop);
- the pane runs claude, never a bare shell;
- the footer is dark, proven by the #524 confirmation run;
- the transcript has been idle >= 10 min, re-checked at delivery;
- there is no UNANSWERED `❓ NEEDS YOU` after the arm;
- at most 1 per hour, with no pending request;
- delivery is verified, and the resolved template is not itself old.

A full box is unchanged.

Every behaviour test drives the PRODUCTION sweep shape: `goal_dark_watch`
then `deliver_goal`, with the SAME sweep `state` (goal_mark "set"). The
fail-closed guards (`delivery_ok` on an unknown arm time, `open_question` on an
untimed `❓`, `decide` with no confirmation run) are also called directly.
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
CWD = "/home/david2/devel/odoo-erp"
_WORK = "Pracujem na tikete 7.\n⏳ WORKING: lane beží, čakám na návrat"
_DONE = "Lane integrovaná.\n✅ DONE: tiket 7 odovzdaný"
_Q = "Potrebujem rozhodnutie o faktúre.\n❓ NEEDS YOU: ktorá možnosť A/B?"
_TASK_NOTE = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed"
              "</status>\n<summary>stream-wait exited</summary>\n"
              "</task-notification>")


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


def _relaunch_tail():
    """The loop was working (a lane live, ⏳) when the session died; the
    relaunched / resumed session shows its last turns and nothing after."""
    return [_asst(600, _WORK, "w"), _asst(700, _DONE, "d")]


class TestDarkStreamLoopRule(unittest.TestCase):
    # A dark-watch CONFIRMATION RUN (#524): 8 dark reads over >= 10 min, the
    # last at `self.now`; the transcript was last written 25 min before that.
    RUN = [-630 + 90 * k for k in range(8)]

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        self.now = float(int(time.time()))
        # #1143 ruling (option 2): the process-tree read is an injected seam --
        # tests never read the real /proc; a relaunched session has no child.
        self.children = []
        _p = unittest.mock.patch.object(
            sm, "claude_children", lambda pane, run: self.children, create=True)
        _p.start()
        self.addCleanup(_p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, payload=NEW_FORK, tail=None, idle_s=1500,
                 mark="Goal set: ", mark_ts=500):
        proj = self._dir()
        tpath = _write_marker_transcript(proj, CWD, sid, "warmup")
        _write_goal_marker(proj, CWD, sid, mark + payload, ts_epoch=mark_ts)
        tail = _relaunch_tail() if tail is None else tail
        with open(tpath, "a", encoding="utf-8") as f:
            for e in tail:
                f.write(json.dumps(e) + "\n")
        self._age(tpath, idle_s)
        return proj

    def _age(self, tpath, idle_s):
        aged = self.now - idle_s
        os.utime(tpath, (aged, aged))

    def _sweep(self, proj, cap=GOAL_IDLE_CAP, authority="fork-no-merge",
               state=None, cmd="claude", now=None, tmpl=None, obl=None,
               dry_run=False):
        state = {} if state is None else state
        tmux = DeliverGoalFakeTmux([("%9", cmd, CWD, "111")], cap)
        now = self.now if now is None else now
        tmpl = NEW_FORK if tmpl is None else tmpl
        obl = (0, now) if obl is None else obl
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state=state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: obl,
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
            cap = caps[k] if caps else kw.get("cap", GOAL_IDLE_CAP)
            kw2 = {a: b for a, b in kw.items() if a != "cap"}
            reqs, lg, state, tmux = self._sweep(proj, state=state, cap=cap,
                                                now=self.now + base + off, **kw2)
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

    def _assert_never(self, proj, sid, state, reqs, reason,
                      authority="fork-no-merge", **deliver_kw):
        """Nothing recorded, and a FORCED stream-migrate request is refused at
        the #1113 structured-armed refusal for THIS guard's `reason`."""
        self.assertNotEqual((reqs.get(sid) or {}).get("origin"), sm.ORIGIN)
        word, live = self._deliver(proj, sid, state,
                                   req=self._forced(authority), **deliver_kw)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])
        self.assertIn(reason, Path(self.syncp).read_text())

    def _assert_rearmed(self, proj, sid, state, reqs, text=NEW_FORK):
        req = reqs.get(sid)
        self.assertIsInstance(req, dict, reqs)
        self.assertEqual(req["origin"], sm.ORIGIN)
        self.assertEqual(req["text"], text)
        word, live = self._deliver(proj, sid, state)
        self.assertEqual(word, "sent", Path(self.syncp).read_text())
        self.assertTrue(any("-l" in a for a in live.sent), live.sent)
        self.assertIn("PASS structured-armed", Path(self.syncp).read_text())

    # --- fires: the relaunch / resume shape ------------------------------------ #
    def test_a_relaunched_current_template_loop_is_re_armed(self):
        proj = self._fixture("r-ok")
        reqs, logs, state, tmux = self._run(proj)
        self.assertEqual(tmux.sent, [], "dark-watch only records")
        self.assertTrue(any("STREAM-MIGRATE" in ln for ln in logs), logs)
        self.assertEqual(state["goal_mark"]["r-ok"]["mark"]["state"], "set")
        self._assert_rearmed(proj, "r-ok", state, reqs)

    def test_a_relaunched_branch_merge_loop_is_re_armed(self):
        proj = self._fixture("r-bm", payload=NEW_BRANCH)
        reqs, _l, state, _t = self._run(proj, authority="branch-merge",
                                        tmpl=NEW_BRANCH)
        self._assert_rearmed(proj, "r-bm", state, reqs, text=NEW_BRANCH)

    def test_re_armed_whatever_the_obligation_cache_says(self):
        # a workable cache must not hand the loop to the dead-loop `dark-rearm`
        # (dropped at the #1113 refusal); a stale / empty one must not stop it.
        for sid, obl in (("r-open", (5, None)), ("r-zero", (0, None)),
                         ("r-none", (None, None))):
            proj = self._fixture(sid)
            reqs, logs, state, _t = self._run(
                proj, obl=(obl[0], self.now) if obl[0] is not None else obl)
            self._assert_rearmed(proj, sid, state, reqs)

    def test_a_loop_that_ended_on_a_worked_turn_re_arms(self):
        # resumed mid-lane: the newest turn is a ⏳ WORKING, no ❓, no 🏁
        proj = self._fixture("r-work", tail=[_asst(600, _WORK, "w")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "r-work", state, reqs)

    def test_an_old_template_dark_loop_is_a_case_of_the_rule(self):
        # the #1128 migration without its 🏁: a dark, set, OLD payload
        proj = self._fixture("r-old", payload=OLD_FORK)
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "r-old", state, reqs)

    def test_an_answered_question_is_a_case_of_the_rule(self):
        proj = self._fixture("r-ans", tail=[
            _asst(600, _Q, "q"), _user(700, "Vyber A."),
            _asst(800, _DONE, "a")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "r-ans", state, reqs)

    def test_a_question_before_the_arm_is_not_an_open_question(self):
        # the owner answered by RE-ARMING (a new arm after the ❓)
        proj = self._fixture("r-prearm", tail=[_asst(400, _Q, "q")] + [
            _asst(600, _WORK, "w"), _asst(700, _DONE, "d")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "r-prearm", state, reqs)

    # --- never: an unanswered ❓ ---------------------------------------------- #
    def test_never_while_a_question_is_unanswered(self):
        # the ❓ is not the last turn (a task-notification woke the loop), so
        # the #737 awaiting-user veto does not hold it: the rule itself must.
        proj = self._fixture("n-q", tail=[
            _asst(600, _Q, "q"), _user(650, _TASK_NOTE),
            _asst(700, "Waiter skončil.\n✅ DONE: nič nové", "n")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_never(proj, "n-q", state, reqs, "is unanswered")

    def test_never_while_the_question_is_the_last_turn(self):
        proj = self._fixture("n-qlast", tail=[_asst(600, _Q, "q")])
        reqs, _l, state, _t = self._run(proj)
        self._assert_never(proj, "n-qlast", state, reqs, "is unanswered")

    def test_delivery_rechecks_the_open_question(self):
        proj = self._fixture("n-toctou")
        _r, _l, state, _t = self._run(proj)
        tpath = next(proj.rglob("n-toctou.jsonl"))
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(_asst(900, _Q, "q2")) + "\n")
        self._age(tpath, 1200)
        word, live = self._deliver(proj, "n-toctou", state)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])
        self.assertIn("is unanswered", Path(self.syncp).read_text())

    def test_delivery_refuses_an_unknown_arm_time(self):
        # fail CLOSED: with no arm time the open-question guard is unprovable
        proj = self._fixture("n-nots")
        tinfo = wd.find_active_transcript(proj, CWD)
        ok, why = sm.delivery_ok(sm.ORIGIN, "fork-no-merge",
                                 lambda: {"payload": NEW_FORK, "ts": None},
                                 lambda: tinfo, self.now, "n-nots")
        self.assertFalse(ok)
        self.assertIn("arm time", why)

    # --- never: the owner's stop / a live, busy or stopped pane ---------------- #
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

    # --- never: before the #524 confirmation run ------------------------------- #
    def test_never_on_a_single_dark_read(self):
        proj = self._fixture("c-one")
        reqs, logs, _s, _t = self._sweep(proj)
        self.assertEqual(reqs, {})
        self.assertTrue(any("not yet confirmed" in ln for ln in logs), logs)

    def test_an_armed_read_restarts_the_confirmation_run(self):
        proj = self._fixture("c-flick")
        caps = [GOAL_IDLE_CAP] * 8
        caps[4] = GOAL_ARMED_CAP
        reqs, _l, state, _t = self._run(proj, caps=caps)
        self.assertEqual(reqs, {})
        reqs, _l, state, _t = self._run(proj, base=720, state=state)
        self.assertEqual(reqs["c-flick"]["origin"], sm.ORIGIN)

    # --- never: not idle, re-checked at delivery ------------------------------- #
    def test_never_on_a_recently_written_transcript(self):
        proj = self._fixture("i-fresh", idle_s=300)
        reqs, _l, _s, _t = self._run(proj)
        self.assertEqual(reqs, {})

    def test_delivery_rechecks_the_idle_window(self):
        proj = self._fixture("i-toctou")
        _r, _l, state, _t = self._run(proj)
        tpath = next(proj.rglob("i-toctou.jsonl"))
        self._age(tpath, 60)                           # a turn ran since
        word, live = self._deliver(proj, "i-toctou", state)
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])
        self.assertIn("not idle", Path(self.syncp).read_text())

    # --- never: 1/h, a pending request, an old resolved template ---------------- #
    def test_one_attempt_per_session_per_hour(self):
        proj = self._fixture("g-rate", idle_s=2000)
        _r, _l, state, _t = self._run(proj)
        goal.clear_goal_request("g-rate", path=self.reqp)
        reqs, logs, state, _t = self._run(proj, base=1800, state=state)
        self.assertEqual(reqs, {}, "a second attempt inside the hour")
        self.assertTrue(any("1/h" in ln for ln in logs), logs)
        reqs, _l, _s, _t = self._run(proj, base=3700, state=state)
        self.assertEqual(reqs["g-rate"]["origin"], sm.ORIGIN)

    def test_a_pending_request_blocks_a_new_record(self):
        proj = self._fixture("g-pend")
        goal.record_goal_request("g-pend", CWD, "/goal y", "fork-no-merge",
                                 now=self.now, path=self.reqp,
                                 origin="self-callback")
        reqs, logs, _s, _t = self._run(proj)
        self.assertEqual(reqs["g-pend"]["origin"], "self-callback")
        self.assertTrue(any("already pending" in ln for ln in logs), logs)

    def test_never_with_a_resolved_template_that_is_itself_old(self):
        proj = self._fixture("g-oldtpl")
        reqs, logs, _s, _t = self._run(proj, tmpl=OLD_FORK)
        self.assertNotEqual((reqs.get("g-oldtpl") or {}).get("origin"),
                            sm.ORIGIN)
        self.assertTrue(any("still the old" in ln for ln in logs), logs)

    def test_never_on_a_foreign_hand_armed_goal(self):
        proj = self._fixture("g-foreign", payload="/goal oprav testy v module X")
        reqs, _l, state, _t = self._run(proj)
        self._assert_never(proj, "g-foreign", state, reqs,
                           "not a stream template")

    # --- a full box is unchanged ------------------------------------------------ #
    def test_a_full_box_is_unchanged(self):
        full = gr.render("full")
        proj = self._fixture("f-full", payload=full)
        reqs, _l, state, _t = self._run(proj, authority="full", tmpl=full,
                                        obl=(0, self.now))
        self.assertNotEqual((reqs.get("f-full") or {}).get("origin"), sm.ORIGIN)
        for key in ("goal_stream_migrate", "goal_stream_answered_memo"):
            self.assertNotIn(key, state)
        word, live = self._deliver(proj, "f-full", state,
                                   req=self._forced("full"))
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(live.sent, [])

    def test_a_stream_payload_on_a_full_box_is_never_re_armed(self):
        proj = self._fixture("f-stream")
        reqs, _l, state, _t = self._run(proj, authority="full",
                                        tmpl=gr.render("full"))
        self._assert_never(proj, "f-stream", state, reqs, "not a stream box",
                           authority="full")

    # --- ROZHODNUTÉ option 2: structured liveness holds, never ◎ alone ------- #
    _WAITER = ("bash", "/bin/bash -c source /home/d/.claude/shell-snapshots/s.sh "
               "&& python3 ~/devel/airuleset/airuleset.py stream-wait --max 3600")

    def _subagent(self, proj, sid, age_s):
        tpath = next(proj.rglob(sid + ".jsonl"))
        d = tpath.parent / tpath.stem / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "agent-lane.jsonl"
        p.write_text('{"type":"assistant"}\n', encoding="utf-8")
        os.utime(p, (self.now - age_s, self.now - age_s))

    def test_never_while_a_stream_wait_child_is_live(self):
        # the healthy idle stream loop: dark-read footer, idle transcript, no
        # ❓ -- only its live waiter under claude says it is alive.
        proj = self._fixture("l-wait")
        self.children = [self._WAITER]
        reqs, logs, _s, _t = self._run(proj)
        self.assertNotEqual((reqs.get("l-wait") or {}).get("origin"), sm.ORIGIN)
        self.assertTrue(any("stream-wait" in ln and "SKIP" in ln for ln in logs),
                        logs)

    def test_never_while_a_background_shell_is_live(self):
        proj = self._fixture("l-bash")
        self.children = [("bash", "/bin/bash -c gh run view 123 --json status")]
        reqs, logs, _s, _t = self._run(proj)
        self.assertNotEqual((reqs.get("l-bash") or {}).get("origin"), sm.ORIGIN)

    def test_an_mcp_server_child_is_not_liveness(self):
        # every live claude has long-lived MCP-server children (live dev box:
        # `npm exec @playwright/mcp`); they must never hold the rule forever.
        proj = self._fixture("l-mcp")
        self.children = [("npm exec @playw", "npm exec @playwright/mcp@0.0.81")]
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "l-mcp", state, reqs)

    def test_an_unresolved_claude_process_holds(self):
        # fail CLOSED: liveness unprovable -> never re-arm on ◎ alone
        proj = self._fixture("l-none")
        self.children = None
        reqs, logs, _s, _t = self._run(proj)
        self.assertNotEqual((reqs.get("l-none") or {}).get("origin"), sm.ORIGIN)
        self.assertTrue(any("unresolved" in ln for ln in logs), logs)

    def test_the_reader_is_asked_about_the_claude_pane(self):
        proj = self._fixture("l-pane")
        seen = []
        with unittest.mock.patch.object(
                sm, "claude_children",
                lambda pane, run: seen.append(pane) or [], create=True):
            reqs, _l, _s, _t = self._run(proj)
        self.assertEqual(reqs["l-pane"]["origin"], sm.ORIGIN)
        self.assertIn("%9", seen)

    def test_never_while_a_subagent_transcript_is_fresh(self):
        proj = self._fixture("l-sub")
        self._subagent(proj, "l-sub", 120)
        reqs, logs, state, _t = self._run(proj)
        self.assertNotEqual((reqs.get("l-sub") or {}).get("origin"), sm.ORIGIN)
        self.assertTrue(any("subagent" in ln for ln in logs), logs)
        self._assert_never(proj, "l-sub", state, reqs, "subagent")

    def test_a_stale_subagent_transcript_is_not_liveness(self):
        proj = self._fixture("l-oldsub")
        self._subagent(proj, "l-oldsub", 1500)
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "l-oldsub", state, reqs)

    def test_a_relaunch_has_no_live_sign_and_is_re_armed(self):
        proj = self._fixture("l-relaunch")
        self.children = [("npm exec @playw", "npm exec @playwright/mcp@0.0.81")]
        reqs, _l, state, _t = self._run(proj)
        self._assert_rearmed(proj, "l-relaunch", state, reqs)

    def test_a_bare_shell_pane_is_never_typed_into(self):
        # owner 24.9.2026: a pane at a bare shell = the OWNER stopped that
        # Claude -- never relaunch it, never type into it (dark-watch nor
        # delivery), even with a forced stream-migrate request.
        proj = self._fixture("l-shell")
        calls = []
        base = DeliverGoalFakeTmux([("%9", "bash", CWD, "111")], GOAL_IDLE_CAP)

        def run(argv, timeout=8):
            calls.append(list(argv))
            return base(argv, timeout=timeout)
        state = {}
        for off in self.RUN:
            goal.goal_dark_watch(
                self.now + off, run=run, send_fn=lambda mm, **k: None,
                projects_dir=proj, state=state, sleep_fn=lambda s: None,
                obligation_fn=lambda cwd: (5, self.now + off),
                rearm_fn=lambda cwd: (NEW_FORK, "fork-no-merge"),
                requests_path=self.reqp)
        word, live = self._deliver(proj, "l-shell", state, cmd="bash",
                                   req=self._forced())
        self.assertNotEqual(word, "sent")
        self.assertEqual(live.sent, [])
        bad = ("send-keys", "respawn-pane", "respawn-window", "new-window",
               "kill-pane", "kill-session")
        self.assertFalse([c for c in calls if any(b in c for b in bad)], calls)

    # --- review round 1: the fall-through, the reset, the legacy memo ---------- #
    def test_a_not_idle_or_open_question_pane_falls_through(self):
        # handled=False keeps the #459 dead-loop / #890 lanes for states that
        # are not this rule's own: they still see the pane (first observation).
        for sid, kw in (("h-fresh", {"idle_s": 300}),
                        ("h-open", {"tail": [
                            _asst(600, _Q, "q"), _user(650, _TASK_NOTE),
                            _asst(700, "Nič nové.\n✅ DONE: nič", "n")]})):
            proj = self._fixture(sid, **kw)
            _r, logs, _s, _t = self._sweep(proj, obl=(5, self.now))
            self.assertTrue(any("first observation" in ln for ln in logs),
                            (sid, logs))

    def test_recording_ends_the_dead_loop_episode(self):
        proj = self._fixture("h-reset")
        ep = {"mark_ts": 500.0, "first_seen": self.now - 900, "last": self.now}
        state = {"goal_dark_seen": {"h-reset": dict(ep)},
                 "goal_dark_pinged": {"h-reset": dict(ep)}}
        reqs, _l, state, _t = self._run(proj, state=state)
        self.assertEqual(reqs["h-reset"]["origin"], sm.ORIGIN)
        for key in ("goal_dark_seen", "goal_dark_pinged", "goal_dark_confirm"):
            self.assertNotIn("h-reset", state.get(key) or {}, key)

    def test_a_legacy_memo_entry_is_never_read_as_no_question(self):
        # a pre-#1143 memo row ({key, ok, why, seen}) with a MATCHING key must
        # be re-read, never taken as "no open question".
        proj = self._fixture("h-memo", tail=[
            _asst(600, _Q, "q"), _user(650, _TASK_NOTE),
            _asst(700, "Nič nové.\n✅ DONE: nič", "n")])
        tpath = next(proj.rglob("h-memo.jsonl"))
        key = [500.0, os.stat(tpath).st_mtime_ns]
        state = {"goal_stream_answered_memo": {"h-memo": {
            "key": key, "ok": True, "why": "",
            "seen": self.now + self.RUN[0] - 60}}}   # live at the first read
        reqs, _l, state, _t = self._run(proj, state=state)
        self.assertNotEqual((reqs.get("h-memo") or {}).get("origin"), sm.ORIGIN)
        self.assertIs(state["goal_stream_answered_memo"]["h-memo"]["open"], True)

    def test_dry_run_mutates_no_state(self):
        proj = self._fixture("d-dry")
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


class TestRuleGuardsDirect(unittest.TestCase):
    """The two fail-CLOSED guards, called directly (review round 1)."""

    def _tpath(self, entries, idle_s=1500):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "g.jsonl"
        p.write_text("".join(json.dumps(e) + "\n" for e in entries),
                     encoding="utf-8")
        aged = time.time() - idle_s
        os.utime(p, (aged, aged))
        return p

    def test_an_untimed_question_counts_as_open(self):
        q = _asst(600, _Q, "q")
        q.pop("timestamp")
        is_open, why = sm.open_question(self._tpath([q]), 500.0)
        self.assertTrue(is_open)
        self.assertIn("unanswered", why)

    def test_no_confirmation_run_never_records(self):
        recorded = []
        line, handled = sm.decide(
            "g-nc", CWD, self._tpath(_relaunch_tail()), NEW_FORK, time.time(),
            "loc", False, {}, lambda cwd: (NEW_FORK, "fork-no-merge"),
            lambda s: False, lambda t, a: recorded.append(t), lambda: None,
            mark_ts=500.0)
        self.assertTrue(handled)
        self.assertEqual(recorded, [])
        self.assertIn("not yet confirmed", line)


if __name__ == "__main__":
    unittest.main()
