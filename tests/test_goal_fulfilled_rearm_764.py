"""#764 — a FULFILLED `/goal` loop (stop-(B) completion: footer dark,
`mark=="set"`, a `🏁 BACKLOG EMPTY:` proof in its last turn) whose obligation
cache has REFILLED (open>0, fresh) must be RE-ARMED FAST via the structured
`record_goal_request`/`deliver_goal` channel — the cross-stream ping-pong
re-entry the slow dead-loop confirmation (2/day, 8 clean-dark reads) was never
built for.

Root cause (traced in `watchdog/goal.py`): CC never persists a "fulfilled"
marker, so a completed loop is transcript-identical to a silently-dead one
(`mark=="set"`, footer dark) EXCEPT for the `🏁 BACKLOG EMPTY:` completion line.
The only auto re-arm today is `goal_dark_watch`'s dead-loop self-heal (8 clean
reads over >=600 s, cap 2/day) — built for "confirmed dead", far too slow/capped
for a backlog that REFILLED after a genuine completion (gk↔subdev hand-off,
`prio:bounce` return). This suite locks the new `fulfilled-rearm` lane: the
`🏁` proof (`transcript_last_backlog_empty_ts`) replaces the dark-duration
confirmation, rate-limited (min gap + daily cap), the recent-human/pane-safety
gates preserved (new origin joins `_GOAL_WATCHDOG_REARM_ORIGINS`), and a
stop-(A) ❓-blocked completion — which prints NO `🏁` line — structurally never
fires it.
"""

import json
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

_BACKLOG_DONE = ("Všetko hotové.\n"
                 "🏁 BACKLOG EMPTY: 0 open, main green\n"
                 "✅ DONE: backlog prázdny")
_Q_BLOCKED = ("Potrebujem rozhodnutie.\n"
              "❓ NEEDS YOU: ktorá možnosť A/B?")


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _append_assistant(path, text, ts):
    """Append a real assistant turn WITH a timestamp — the completion turn a
    fulfilled loop writes (its last real assistant message carries the `🏁`)."""
    entry = {"type": "assistant", "timestamp": _iso(ts),
             "message": {"id": "msg_done", "content": text}}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- #
# 1. The pure `🏁 BACKLOG EMPTY:` proof reader.
# --------------------------------------------------------------------------- #
class TestBacklogEmptyProofReader(unittest.TestCase):
    def _tmp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_returns_ts_when_last_turn_carries_the_marker(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, "warmup", 100)
        _append_assistant(p, _BACKLOG_DONE, 600)
        self.assertEqual(wd.transcript_last_backlog_empty_ts(p), 600.0)

    def test_none_when_last_turn_is_a_question_block(self):
        # stop-(A) ❓-blocked completion prints NO 🏁 -> None (never re-armed).
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, _BACKLOG_DONE, 600)     # an OLD completion...
        _append_assistant(p, _Q_BLOCKED, 700)        # ...then a later ❓ block
        self.assertIsNone(wd.transcript_last_backlog_empty_ts(p))

    def test_none_when_no_marker_anywhere(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, "ordinary ✅ DONE turn", 600)
        self.assertIsNone(wd.transcript_last_backlog_empty_ts(p))

    def test_none_on_missing_file(self):
        self.assertIsNone(
            wd.transcript_last_backlog_empty_ts(self._tmp() / "nope.jsonl"))

    # --- #767: backward scan finds a 🏁 shadowed by later chore turns --------- #
    def test_scan_back_finds_shadowed_flag_default_does_not(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, "warmup", 100)
        _append_assistant(p, _BACKLOG_DONE, 600)     # the REAL completion 🏁
        _append_assistant(p, "chore ✅ DONE", 700)    # a later NON-🏁 chore turn
        # default (newest-turn-only) is SHADOWED by the chore turn -> None:
        self.assertIsNone(wd.transcript_last_backlog_empty_ts(p),
                          "newest-turn-only is shadowed by the chore turn")
        # scan_back skips the chore and finds the LAST 🏁:
        self.assertEqual(
            wd.transcript_last_backlog_empty_ts(p, scan_back=True), 600.0,
            "scan_back returns the 🏁 behind post-achieve chores")

    def test_scan_back_returns_the_NEWEST_flag_when_several(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, _BACKLOG_DONE, 300)     # an OLD 🏁
        _append_assistant(p, "chore ✅ DONE", 400)
        _append_assistant(p, _BACKLOG_DONE, 600)     # a NEWER 🏁
        _append_assistant(p, "chore ✅ DONE", 700)
        self.assertEqual(
            wd.transcript_last_backlog_empty_ts(p, scan_back=True), 600.0,
            "scan_back returns the NEWEST 🏁, not the oldest")

    def test_scan_back_skips_a_later_api_error_turn(self):
        p = self._tmp() / "s.jsonl"
        _append_assistant(p, _BACKLOG_DONE, 600)
        entry = {"type": "assistant", "timestamp": _iso(700),
                 "isApiErrorMessage": True,
                 "message": {"id": "err", "content": "API error"}}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        # default: a newest api-error turn is terminal -> None
        self.assertIsNone(wd.transcript_last_backlog_empty_ts(p))
        # scan_back: an api-error turn is never a completion, so skip it -> 🏁
        self.assertEqual(
            wd.transcript_last_backlog_empty_ts(p, scan_back=True), 600.0)


# --------------------------------------------------------------------------- #
# 2. The fulfilled-rearm lane inside goal_dark_watch.
# --------------------------------------------------------------------------- #
class TestFulfilledRearmLane(unittest.TestCase):
    CWD = "/home/newlevel/devel/fulfilledrearm"
    ORIGIN = "fulfilled-rearm"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, last_text=_BACKLOG_DONE, mark_ts=500, done_ts=600,
                 cap=GOAL_IDLE_CAP):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid, "warmup")
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x",
                           ts_epoch=mark_ts)
        tpath = next(proj.rglob(sid + ".jsonl"))
        _append_assistant(tpath, last_text, done_ts)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        return proj, tmux

    def _sweep(self, proj, tmux, now, obl, state=None, reqs=None, dry_run=False,
               rearm=None):
        reqs = reqs if reqs is not None else self._dir() / "goal-requests.json"
        rearm = rearm or (lambda cwd: ("/goal DONE or stop after 50", "full"))
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state={} if state is None else state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: obl, rearm_fn=rearm,
            requests_path=reqs, dry_run=dry_run)
        return goal.load_goal_requests(reqs), logs, reqs

    # --- the core: fulfilled + refilled backlog -> a re-arm request --------- #
    def test_fulfilled_and_workable_records_a_rearm(self):
        proj, tmux = self._fixture("sess-f-1")
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        req = reqs.get("sess-f-1")
        self.assertIsInstance(req, dict,
                              "a fulfilled+workable loop must record a re-arm")
        self.assertEqual(req.get("origin"), self.ORIGIN)
        self.assertTrue(any("FULFILLED-REARM" in ln for ln in logs), logs)
        self.assertEqual(tmux.sent, [], "the lane NEVER types (record-only)")

    def test_new_origin_is_a_watchdog_rearm_origin(self):
        # so deliver_goal applies the recent-human + pane-safety gates to it.
        self.assertIn(self.ORIGIN, goal._GOAL_WATCHDOG_REARM_ORIGINS)

    # --- the ❓-blocked completion structurally never fires ----------------- #
    def test_question_blocked_completion_never_rearms(self):
        proj, tmux = self._fixture("sess-q", last_text=_Q_BLOCKED)
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "a ❓-blocked completion prints no 🏁 -> never re-armed")

    # --- user-cleared mark never fires (permanent #170 guard) --------------- #
    def test_user_cleared_mark_never_rearms(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-cl", "warmup")
        _write_goal_marker(proj, self.CWD, "sess-cl", "Goal cleared: /goal x",
                           ts_epoch=500)
        tpath = next(proj.rglob("sess-cl.jsonl"))
        _append_assistant(tpath, _BACKLOG_DONE, 600)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "a cleared marker is NEVER re-armed (#170)")

    # --- stale obligation cache never fires --------------------------------- #
    def test_stale_obligation_cache_never_rearms(self):
        proj, tmux = self._fixture("sess-stale")
        old = 100000 - (goal.GOAL_DARK_CACHE_MAX_AGE_S + 10)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, old))
        self.assertEqual(reqs, {},
                         "a STALE obligation cache can never prove work remains")

    # --- empty backlog never fires (achieved final state) ------------------- #
    def test_empty_backlog_never_rearms(self):
        proj, tmux = self._fixture("sess-empty")
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (0, 100000))
        self.assertNotIn("sess-empty", reqs,
                         "open==0 is the correct achieved final state")

    # --- #766: a 🏁-proven achieved loop (open==0) VETOes the #459 ping ------ #
    def _sweep_capturing(self, proj, tmux, now, obl, state, reqs, pings):
        """A sweep with a CAPTURING send_fn (the #764 `_sweep` uses a no-op, so
        it structurally cannot see the false #459 ping this test locks)."""
        return goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: pings.append((mm, k)),
            projects_dir=proj,
            state=state, sleep_fn=lambda s: None, obligation_fn=lambda cwd: obl,
            rearm_fn=lambda cwd: ("/goal DONE or stop after 50", "full"),
            requests_path=reqs, dry_run=False)

    def test_achieved_open_zero_vetoes_the_459_ping_across_two_sweeps(self):
        # THE #766 REGRESSION: a legitimately-ACHIEVED loop (🏁 BACKLOG EMPTY in
        # its last turn, AFTER the arm; obligation cache fresh with open==0) must
        # NOT get the "💀 /goal loop zomrelo potichu" ping. On PRE-fix code the
        # sweep falls through to the unconditional #459 fallback and pings on the
        # SECOND sweep (the first is "first observation, debouncing"). The fix
        # returns a FULFILLED-SILENT sentinel from _fulfilled_rearm_decide so the
        # sweep site VETOes the ping. Two sweeps + a CAPTURING send_fn are
        # required to observe (and, on the fix, disprove) the second-sweep ping.
        proj, tmux = self._fixture("sess-achieved-766")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        pings = []
        logs1 = self._sweep_capturing(proj, tmux, 100000, (0, 100000),
                                      state, reqs, pings)
        logs2 = self._sweep_capturing(proj, tmux, 100060, (0, 100060),
                                      state, reqs, pings)
        self.assertEqual(pings, [],
                         "an achieved (🏁, open==0) loop must NEVER be pinged")
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "an achieved loop records NO re-arm (backlog empty)")
        self.assertEqual(tmux.sent, [], "the veto types no keystroke")
        self.assertTrue(
            any("FULFILLED-SILENT" in ln for ln in (logs1 + logs2)),
            "the veto emits an explicit FULFILLED-SILENT decision log")

    def test_fulfilled_silent_logs_once_per_episode_not_every_sweep(self):
        # a completed loop sits dark for HOURS -> the FULFILLED-SILENT decision
        # must log ONCE per episode (mirroring "first observation, debouncing"),
        # never every 60s sweep (the #764-documented journal-flood concern).
        proj, tmux = self._fixture("sess-once-766")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        pings = []
        n = 0
        for i in range(4):
            logs = self._sweep_capturing(proj, tmux, 100000 + i * 60,
                                         (0, 100000 + i * 60), state, reqs, pings)
            n += sum(1 for ln in logs if "FULFILLED-SILENT" in ln)
        self.assertEqual(n, 1,
                         "FULFILLED-SILENT logs exactly once across four sweeps")
        self.assertEqual(pings, [], "still never pinged")

    # --- 🏁 that PREDATES the current arm never fires ----------------------- #
    def test_backlog_proof_before_the_mark_never_rearms(self):
        # a re-armed-then-died loop whose LAST turn is a PRE-rearm 🏁: the mark
        # is newer than the 🏁, so this is NOT the current episode's completion.
        proj, tmux = self._fixture("sess-oldflag", mark_ts=900, done_ts=600)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "🏁 older than the current arm is not a fresh completion")

    # --- an UNPARSEABLE mark_ts fails CLOSED (review 🟡) --------------------- #
    def test_unparseable_mark_ts_never_rearms(self):
        # `_newest_marker` sets mark["ts"]=None on any timestamp parse failure;
        # then the 🏁-after-mark ordering is UNPROVEN, so the lane must NOT fire
        # (a possibly-stale 🏁 from a previous episode) -- fail closed.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-nots", "warmup")
        # a `Goal set:` system marker with NO `timestamp` field -> mark_ts None.
        d = next(proj.rglob("sess-nots.jsonl")).parent
        p = d / "sess-nots.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "system", "subtype": "local_command",
                "content": "<local-command-stdout>Goal set: /goal x"
                           "</local-command-stdout>"}) + "\n")
        _append_assistant(p, _BACKLOG_DONE, 600)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "an unparseable arm timestamp must fail CLOSED (no re-arm)")

    # --- armed footer never fires (already re-armed) ------------------------ #
    def test_armed_footer_never_rearms(self):
        proj, tmux = self._fixture("sess-armed", cap=GOAL_ARMED_CAP)
        reqs, _logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        self.assertEqual(reqs, {},
                         "an ALREADY-armed loop is never fulfilled-re-armed")

    # --- rate-limit: min gap between fulfilled-rearms per sid --------------- #
    def test_min_gap_blocks_a_second_rearm_within_the_window(self):
        proj, tmux = self._fixture("sess-gap")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        r1, _l1, _ = self._sweep(proj, tmux, 100000, (7, 100000), state, reqs)
        self.assertEqual(r1.get("sess-gap", {}).get("origin"), self.ORIGIN)
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-gap")
        self.assertEqual(len(recs or []), 1, "exactly ONE record in the window")
        # a 2nd sweep within the min-gap must NOT record a fresh slot.
        _r2, l2, _ = self._sweep(
            proj, tmux, 100000 + goal.GOAL_FULFILLED_REARM_MIN_GAP_S - 5,
            (7, 100000), state, reqs)
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-gap")
        self.assertEqual(len(recs or []), 1,
                         "the min-gap holds a second re-arm inside the window")
        self.assertTrue(any("SKIP:gap" in ln for ln in l2), l2)

    def test_gap_frees_after_the_window(self):
        proj, tmux = self._fixture("sess-gap2")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        self._sweep(proj, tmux, 100000, (7, 100000), state, reqs)
        self._sweep(proj, tmux, 100000 + goal.GOAL_FULFILLED_REARM_MIN_GAP_S + 5,
                    (7, 100000), state, reqs)
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-gap2")
        self.assertEqual(len(recs or []), 2,
                         "once the min-gap passes a fresh re-arm records again")

    # --- rate-limit: daily cap --------------------------------------------- #
    def test_daily_cap_honored(self):
        proj, tmux = self._fixture("sess-cap")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        now = 100000
        # space each sweep past the min gap so only the DAILY cap can bind.
        step = goal.GOAL_FULFILLED_REARM_MIN_GAP_S + 1
        for _i in range(goal.GOAL_FULFILLED_REARM_MAX_PER_DAY + 3):
            self._sweep(proj, tmux, now, (7, now), state, reqs)
            now += step
        recs = state.get("goal_fulfilled_rearm", {}).get("sess-cap") or []
        self.assertEqual(len(recs), goal.GOAL_FULFILLED_REARM_MAX_PER_DAY,
                         "no more than the daily cap of fulfilled-rearms per sid")

    # --- recent-human veto applies to the new origin (delivered path) ------- #
    def test_recent_human_veto_applies_to_fulfilled_rearm(self):
        # the request is RECORDED by dark-watch, but deliver_goal must SKIP it
        # when a human is present -> the new origin honours the recent-human gate
        # exactly like every other watchdog re-arm.
        proj, tmux = self._fixture("sess-human")
        reqs = self._dir() / "goal-requests.json"
        self._sweep(proj, tmux, 100000, (7, 100000), reqs=reqs)
        req = goal.load_goal_requests(reqs).get("sess-human")
        self.assertEqual(req.get("origin"), self.ORIGIN)
        # now deliver with a human just-present -> skip:recent-human, no type.
        with unittest.mock.patch.object(
                wd, "_goal_autoarm_recent_human_activity",
                return_value=(True, "presence marker 3s")):
            verdict = goal.deliver_goal(
                "sess-human", self.CWD, req["text"], req["authority"],
                run=tmux, projects_dir=proj, now=100050,
                origin=self.ORIGIN, request_ts=req["ts"], sleep_fn=lambda s: None)
        self.assertEqual(verdict, "skip:recent-human")
        self.assertEqual(tmux.sent, [], "no keystroke while a human is present")

    # --- deliver_goal treats the new origin like a watchdog re-arm ---------- #
    def _deliver(self, sid, now, request_ts, recent=None, send_fn=None):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid, "warmup")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        ctx = (unittest.mock.patch.object(
                   wd, "_goal_autoarm_recent_human_activity",
                   return_value=recent)
               if recent is not None else None)
        args = dict(run=tmux, projects_dir=proj, now=now, request_ts=request_ts,
                    origin=self.ORIGIN, send_fn=send_fn, sleep_fn=lambda s: None)
        if ctx is None:
            return goal.deliver_goal(sid, self.CWD, "/goal x", "full", **args), tmux
        with ctx:
            return goal.deliver_goal(sid, self.CWD, "/goal x", "full", **args), tmux

    def test_fulfilled_rearm_expiry_is_SILENT(self):
        # review 🟡: a fulfilled-rearm most often fails delivery on recent-human;
        # its 30-min expiry must NOT ping the very human whose presence deferred
        # it (the #675 banned shape) -- unlike a genuinely-dead dark-rearm.
        now = 1_000_000
        old = now - goal.GOAL_REQUEST_MAX_AGE_S - 10
        pings = []
        word, _ = self._deliver(
            "sess-exp", now, old, recent=(True, "presence marker 5s old"),
            send_fn=lambda *a, **k: pings.append(a))
        self.assertEqual(word, "expired")
        self.assertEqual(pings, [],
                         "fulfilled-rearm expiry must be SILENT (self-healing loop)")

    def test_fulfilled_rearm_not_subject_to_the_300s_stale_drop(self):
        # the GOAL_DARK_REARM_STALE_S drop is dark/auth ONLY (#764 accepted
        # residual) -- a fulfilled-rearm at a 300s+ (but < 30 min) age still
        # delivers, bounded by its own min-gap/cap, never drop:stale-rearm.
        now = 1_000_000
        stale = now - goal.GOAL_DARK_REARM_STALE_S - 50
        word, tmux = self._deliver("sess-notstale", now, stale,
                                   recent=(False, ""))
        self.assertEqual(word, "sent",
                         "fulfilled-rearm is not in the dark/auth 300s stale gate")

    # --- dry-run records nothing ------------------------------------------- #
    def test_dry_run_never_records(self):
        proj, tmux = self._fixture("sess-dry")
        state = {}
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000), state,
                                    dry_run=True)
        self.assertEqual(reqs, {}, "dry-run records no request")
        self.assertNotIn("sess-dry", state.get("goal_fulfilled_rearm", {}),
                         "dry-run consumes no rate-limit slot")
        self.assertTrue(any("would record" in ln for ln in logs), logs)

    # ===================================================================== #
    # #767 -- post-achieve chores SHADOW the 🏁 proof (newest-turn-only read).
    # A completed loop that keeps working after 🏁 leaves a non-🏁 NEWEST turn;
    # `transcript_last_backlog_empty_ts` (newest-turn-only) then returns None and
    # both the fulfilled-rearm lane AND the #766 veto silently fail. These lock
    # the backward-scan fix. RED on current code (newest=chore -> bts None).
    # ===================================================================== #
    _CHORE = "pokračujem po dokončení...\n✅ DONE: server lockdown KB2003"

    def _shadowed_fixture(self, sid, mark_ts=500, done_ts=600, chore_ts=700,
                          cap=GOAL_IDLE_CAP):
        """A FULFILLED fixture (🏁 at done_ts, AFTER the mark) whose NEWEST turn
        is a later NON-🏁 chore (chore_ts) -- the #767 shadowing shape."""
        proj, tmux = self._fixture(sid, mark_ts=mark_ts, done_ts=done_ts, cap=cap)
        tpath = next(proj.rglob(sid + ".jsonl"))
        _append_assistant(tpath, self._CHORE, chore_ts)
        return proj, tmux

    def test_shadowed_flag_still_records_a_rearm(self):
        # THE #767 REGRESSION: 🏁 is still inside the 2 MB tail but SHADOWED by a
        # later chore turn; the fulfilled-rearm lane must find it and re-arm.
        proj, tmux = self._shadowed_fixture("sess-shadow")
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000))
        req = reqs.get("sess-shadow")
        self.assertIsInstance(req, dict,
                              "a shadowed 🏁 (chore turn newest) must still re-arm")
        self.assertEqual(req.get("origin"), self.ORIGIN)
        self.assertTrue(any("FULFILLED-REARM" in ln for ln in logs), logs)

    def test_shadowed_flag_open_zero_still_vetoes_the_ping(self):
        # the #766 veto keys on the SAME detection: a shadowed 🏁 with a fresh
        # open==0 must STILL veto the 💀 dead-loop ping (never a false death).
        proj, tmux = self._shadowed_fixture("sess-shadow-veto")
        state = {}
        reqs = self._dir() / "goal-requests.json"
        pings = []
        l1 = self._sweep_capturing(proj, tmux, 100000, (0, 100000),
                                   state, reqs, pings)
        l2 = self._sweep_capturing(proj, tmux, 100060, (0, 100060),
                                   state, reqs, pings)
        self.assertEqual(pings, [],
                         "a shadowed-🏁 achieved loop must NEVER be pinged")
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "an achieved loop records no re-arm (backlog empty)")
        self.assertTrue(any("FULFILLED-SILENT" in ln for ln in (l1 + l2)), l1 + l2)

    def test_proof_cache_carries_a_rearm_when_flag_scrolled_out(self):
        # heavy post-achieve output BURIES the 🏁 past the 200-entry bounded tail;
        # a proof recorded on an EARLIER sweep of the SAME arm still re-arms.
        now = 1_000_000
        mark_ts = now - 100
        done_ts = now - 50
        proj, tmux = self._fixture("sess-cache", mark_ts=mark_ts, done_ts=done_ts)
        tpath = next(proj.rglob("sess-cache.jsonl"))
        for i in range(260):                      # bury the 🏁 past the tail
            _append_assistant(tpath, "chore %d ✅ DONE" % i, done_ts + 1 + i)
        # the scan alone can no longer see the 🏁 (out of the 200-entry window):
        self.assertIsNone(
            wd.transcript_last_backlog_empty_ts(tpath, scan_back=True),
            "the 🏁 must be scrolled out of the bounded tail for this test")
        # pre-seed the cache as an EARLIER sweep of THIS arm would have:
        state = {"goal_fulfilled_proof":
                 {"sess-cache": {"mark_ts": mark_ts, "bts": done_ts}}}
        reqs, logs, _ = self._sweep(proj, tmux, now, (7, now), state)
        self.assertEqual(reqs.get("sess-cache", {}).get("origin"), self.ORIGIN,
                         "the per-episode cache carries the proof when 🏁 scrolled out")
        self.assertTrue(any("FULFILLED-REARM" in ln for ln in logs), logs)

    def test_proof_cache_from_a_different_arm_is_ignored(self):
        # a cache from a PREVIOUS arm (mark_ts M1) whose 🏁 (bts) is RECENT enough
        # to pass the current arm's bts>=mark_ts ordering gate on its own -> the
        # ONLY thing preventing a cross-episode false re-arm is the mark_ts match.
        # (Fixture is chosen so the ordering gate alone does NOT catch it: bts is
        # AFTER the current arm; the guard's teeth are on the mark_ts mismatch.)
        now = 1_000_000
        m2 = now - 3000                          # the CURRENT arm
        proj, tmux = self._fixture("sess-stalecache",
                                   last_text="bežný ✅ DONE (žiadne 🏁)",
                                   mark_ts=m2, done_ts=now - 50)
        state = {"goal_fulfilled_proof":
                 {"sess-stalecache":
                  {"mark_ts": now - 5000, "bts": now - 100}}}  # M1 != M2; bts>M2
        reqs, _logs, _ = self._sweep(proj, tmux, now, (7, now), state)
        self.assertEqual(reqs, {},
                         "a cache from a DIFFERENT arm (mark mismatch) never re-arms")

    def test_proof_cache_is_written_on_a_rearm_sweep(self):
        # a 🏁-in-window rearm sweep persists the proof for the current arm.
        proj, tmux = self._shadowed_fixture("sess-writecache")
        state = {}
        self._sweep(proj, tmux, 100000, (7, 100000), state)
        proof = state.get("goal_fulfilled_proof", {}).get("sess-writecache")
        self.assertIsInstance(proof, dict, "the sweep persists the 🏁 proof")
        self.assertEqual(proof.get("mark_ts"), 500)
        self.assertEqual(proof.get("bts"), 600.0)

    def test_non_flag_dead_loop_still_falls_through_unchanged(self):
        # a genuinely-dead loop (NO 🏁 anywhere, no cache) is NOT this lane:
        # records no fulfilled-rearm and debounces on its first observation.
        proj, tmux = self._fixture("sess-dead", last_text="pracujem... (žiadne 🏁)")
        state = {}
        reqs, logs, _ = self._sweep(proj, tmux, 100000, (7, 100000), state)
        self.assertEqual(reqs, {}, "no 🏁 -> the fulfilled lane never fires")
        self.assertNotIn("sess-dead", state.get("goal_fulfilled_proof", {}),
                         "no 🏁 -> no proof cache entry written")
        self.assertTrue(any("first observation" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
