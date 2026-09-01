"""#623 — a LIVE, armed `/goal` loop whose stored condition PREDATES the
shipped template must be re-armed.

Root cause (validated live on montalu1): `watchdog/goal.py` re-arms only a
CONFIRMED-DEAD loop (`goal_dark_watch`) or on a real `/autopilot` invocation.
An ALIVE armed loop carrying a condition older than the deployed SKILL.md
template is never re-read, so a `/goal` template change (e.g. #621's saturation
clause) lands on disk and stays INERT until the loop dies. This suite locks the
detection (`_classify_armed_condition`) and the re-arm-request path integrated
into `goal_dark_watch`'s `armed is True` branch, delivered by the EXISTING
`goal_sweep`/`deliver_goal` verified channel as a `/goal` REPLACE.
"""

import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal
import goal_registry

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

# The stable opening every autopilot /goal condition carries (goal_registry's
# `header` clause). An OLD armed condition (some earlier template) and the NEW
# shipped template BOTH open with it; they DIFFER only past it -> "stale".
_SIG = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"
_OLD_COND = (_SIG + ", both checkable from the transcript: (A) an OLDER wording "
             "of the stop conditions, from before the shipped template changed.")
_NEW_TEMPLATE = ("/goal " + _SIG + ", both checkable from the transcript: (A) the "
                 "NEW wording carrying the saturation clause: SATURATE parallel "
                 "isolation:worktree autopilot-worker lanes.")
_NEW_COND = _NEW_TEMPLATE[len("/goal "):]


class TestClassifyArmedCondition(unittest.TestCase):
    """The pure comparison heart — exact, not fuzzy."""

    def test_stale_when_autopilot_condition_differs(self):
        self.assertEqual(
            goal._classify_armed_condition(_OLD_COND, _NEW_TEMPLATE), "stale")

    def test_current_when_equal_to_template_condition(self):
        self.assertEqual(
            goal._classify_armed_condition(_NEW_COND, _NEW_TEMPLATE), "current")

    def test_current_ignores_a_goal_prefix_on_the_payload(self):
        # a defensively-carried `/goal ` prefix on either side is stripped
        # symmetrically -> still recognized as the same condition.
        self.assertEqual(
            goal._classify_armed_condition("/goal " + _NEW_COND, _NEW_TEMPLATE),
            "current")

    def test_foreign_when_no_autopilot_signature(self):
        # a goal the user armed by hand -> NEVER touched.
        self.assertEqual(
            goal._classify_armed_condition("fix the login bug and all tests pass",
                                           _NEW_TEMPLATE), "foreign")

    def test_unknown_on_missing_inputs(self):
        self.assertEqual(goal._classify_armed_condition(None, _NEW_TEMPLATE),
                         "unknown")
        self.assertEqual(goal._classify_armed_condition(_OLD_COND, None),
                         "unknown")
        self.assertEqual(goal._classify_armed_condition("", ""), "unknown")

    def test_unknown_when_template_itself_lacks_the_signature(self):
        # self-validation: if the CURRENT template does not open with the
        # signature, our signature has drifted -> disable detection (never
        # misclassify a stale/foreign against a broken template).
        self.assertEqual(
            goal._classify_armed_condition(_OLD_COND, "/goal do something else"),
            "unknown")

    def test_whitespace_and_softwrap_robust_not_fuzzy(self):
        # a wrapped/whitespace-noisy copy of the CURRENT condition still reads
        # "current" (normalization collapses runs) -- but a one-word change is
        # still detected (NOT fuzzy).
        wrapped = _NEW_COND.replace(" ", "  \n  ")   # inject newlines + spaces
        self.assertEqual(
            goal._classify_armed_condition(wrapped, _NEW_TEMPLATE), "current")
        changed = _NEW_COND.replace("SATURATE", "SATURATEX")
        self.assertEqual(
            goal._classify_armed_condition(changed, _NEW_TEMPLATE), "stale")

    def test_signature_is_a_real_prefix_of_every_registry_render(self):
        # DRIFT-LOCK: the hardcoded signature MUST stay a prefix of the actual
        # shipped condition for all three profiles, or detection silently
        # disables itself (unknown). Locks it to goal_registry (the source).
        for p in goal_registry.PROFILES:
            cond = goal_registry.render(p)[len("/goal "):]
            self.assertTrue(cond.startswith(goal._AUTOPILOT_GOAL_SIGNATURE),
                            "signature drifted from the %s template" % p)

    def test_a_real_shipped_condition_classifies_current_against_itself(self):
        # convergence proof: the marker CC writes after a re-arm (== the
        # template condition) reads "current" next sweep -> the loop stops.
        line = goal_registry.render("branch-merge")
        cond = line[len("/goal "):]
        self.assertEqual(goal._classify_armed_condition(cond, line), "current")


class TestStaleArmedRearmRecorded(unittest.TestCase):
    CWD = "/home/newlevel/devel/stalerearm"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _sweep(self, sid, cond, obl=(7, 100000), tmpl=_NEW_TEMPLATE, now=100000,
               reqs=None, state=None, dry_run=False):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + cond, ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP)
        reqs = reqs if reqs is not None else self._dir() / "goal-requests.json"
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None,
            projects_dir=proj, state={} if state is None else state,
            sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: obl,
            rearm_fn=lambda cwd: (tmpl, "branch-merge"),
            requests_path=reqs, dry_run=dry_run)
        return goal.load_goal_requests(reqs), tmux, logs, reqs

    def test_stale_armed_loop_records_a_rearm(self):
        reqs, tmux, logs, _ = self._sweep("sess-stale-1", _OLD_COND)
        req = reqs.get("sess-stale-1")
        self.assertIsInstance(req, dict, "a stale-rearm request must be recorded")
        self.assertEqual(req.get("origin"), "stale-rearm")
        self.assertEqual(req.get("text"), _NEW_TEMPLATE)
        self.assertEqual(tmux.sent, [],
                         "dark_watch records a request, never keystrokes")
        self.assertTrue(any("STALE: recording re-arm" in ln for ln in logs), logs)

    def test_current_armed_loop_records_nothing(self):
        reqs, _, logs, _ = self._sweep("sess-current-1", _NEW_COND)
        self.assertEqual(reqs, {}, "a current condition needs no re-arm")
        self.assertFalse(any("stale-rearm" in ln for ln in logs), logs)

    def test_foreign_armed_loop_is_never_touched(self):
        reqs, _, logs, _ = self._sweep("sess-foreign-1",
                                       "fix the login bug and ship it")
        self.assertEqual(reqs, {}, "a hand-armed foreign goal is never clobbered")
        self.assertFalse(any("stale-rearm" in ln for ln in logs), logs)

    def test_not_workable_stale_loop_skips_with_log(self):
        reqs, _, logs, _ = self._sweep("sess-stale-empty", _OLD_COND, obl=(0, 100000))
        self.assertEqual(reqs, {}, "an empty backlog is not worth a keystroke")
        self.assertTrue(any("backlog not workable" in ln for ln in logs), logs)

    def test_stale_cache_too_old_is_not_workable(self):
        # a stale obligation cache (older than GOAL_DARK_CACHE_MAX_AGE_S = 3d) is
        # not trusted -> no re-arm (fail toward no keystroke). now=400000, cts=1
        # -> age ~4.6d > cap.
        reqs, _, logs, _ = self._sweep("sess-stale-oldcache", _OLD_COND,
                                       obl=(7, 1), now=400000)
        self.assertEqual(reqs, {})
        self.assertTrue(any("backlog not workable" in ln for ln in logs), logs)

    def test_dry_run_records_nothing(self):
        reqs, tmux, logs, _ = self._sweep("sess-stale-dry", _OLD_COND, dry_run=True)
        self.assertEqual(reqs, {}, "dry-run must not mutate the request store")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("would record re-arm (dry-run" in ln for ln in logs),
                        logs)

    def test_already_pending_is_not_re_recorded(self):
        reqs_path = self._dir() / "goal-requests.json"
        # seed a pending stale-rearm request, then a sweep must NOT overwrite it
        # (it is being delivered by goal_sweep).
        goal.record_goal_request("sess-stale-pend", self.CWD, _NEW_TEMPLATE,
                                 "branch-merge", now=90000, path=reqs_path,
                                 origin="stale-rearm")
        reqs, _, logs, _ = self._sweep("sess-stale-pend", _OLD_COND,
                                       reqs=reqs_path)
        # the pending request stands, unchanged (same ts anchor), no new log
        self.assertEqual(reqs["sess-stale-pend"]["ts"], 90000)
        self.assertFalse(any("recording re-arm" in ln for ln in logs), logs)

    def test_defers_to_a_pending_request_of_any_origin(self):
        # #623-review 🔵: a pending self-callback (user /autopilot) is already
        # being delivered and arms the SAME current template -> the stale-rearm
        # must never clobber it (nor pile on a pending dark-rearm).
        reqs_path = self._dir() / "goal-requests.json"
        goal.record_goal_request("sess-defer", self.CWD, _NEW_TEMPLATE,
                                 "branch-merge", now=90000, path=reqs_path,
                                 origin="self-callback")
        reqs, _, logs, _ = self._sweep("sess-defer", _OLD_COND, reqs=reqs_path)
        self.assertEqual(reqs["sess-defer"]["origin"], "self-callback",
                         "a pending self-callback is never clobbered")
        self.assertFalse(any("recording re-arm" in ln for ln in logs), logs)

    def test_attempt_backoff_bounds_then_rearms_804(self):
        # #804 mode-2 -- SHARES the dark-rearm gate. Past the fast base cap
        # (2/24h) the stale-rearm is NOT silent-until-midnight: it re-arms on an
        # ESCALATING backoff. A 3rd attempt INSIDE the first (30m) backoff window
        # is DEFERRED (BACKOFF log, no record); once the window elapses it
        # re-arms again. (The pre-#804 flat cap skipped forever with ATTEMPT-CAP.)
        state = {}
        reqs_path = self._dir() / "goal-requests.json"
        # record #1 (now=100000)
        self._sweep("sess-cap", _OLD_COND, reqs=reqs_path, state=state)
        self.assertEqual(goal.load_goal_requests(reqs_path)["sess-cap"]["origin"],
                         "stale-rearm")
        # clear the pending request so the next sweep is not blocked by the
        # already-pending guard, exercising the ATTEMPT gate specifically.
        goal.clear_goal_request("sess-cap", path=reqs_path)
        # record #2 (now=100100) -- the fast base cap is now full
        self._sweep("sess-cap", _OLD_COND, reqs=reqs_path, state=state, now=100100)
        goal.clear_goal_request("sess-cap", path=reqs_path)
        # 3rd INSIDE the first 30m backoff window -> deferred, no record
        reqs, _, logs, _ = self._sweep("sess-cap", _OLD_COND, reqs=reqs_path,
                                       state=state, now=100200)
        self.assertEqual(reqs, {}, "a 3rd re-arm inside the backoff window is deferred")
        self.assertTrue(any("BACKOFF" in ln for ln in logs), logs)
        self.assertFalse(any("ATTEMPT-CAP" in ln for ln in logs),
                         "inside the backoff window is NOT the hard strop")
        # 3rd AFTER the 30m backoff window elapses -> re-arm (never silent)
        reqs, _, logs, _ = self._sweep(
            "sess-cap", _OLD_COND, reqs=reqs_path, state=state,
            now=100100 + goal.GOAL_DARK_REARM_BACKOFF_S[0] + 10)
        self.assertEqual(reqs["sess-cap"]["origin"], "stale-rearm",
                         "past the backoff window the loop re-arms -- never silent")


class TestDeliverStaleRearmReplaces(unittest.TestCase):
    """deliver_goal REPLACES a still-stale armed autopilot goal, and drops a
    loop that is no longer stale — never clobbers a foreign / current goal."""

    CWD = "/home/newlevel/devel/stalereplace"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, sid, armed_cond, text=_NEW_TEMPLATE, recent=False,
                 model_type=True):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + armed_cond,
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=model_type)
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(recent, "test")):
            word = goal.deliver_goal(
                sid, self.CWD, text, "branch-merge", run=tmux, projects_dir=proj,
                now=100000, request_ts=100000, sleep_fn=lambda s: None,
                origin="stale-rearm")
        return word, tmux

    def test_replaces_a_still_stale_armed_goal(self):
        word, tmux = self._deliver("sess-repl-1", _OLD_COND)
        self.assertEqual(word, "sent", "a still-stale armed loop is REPLACED")
        # `_type_literal` chunks a long paste across several `-l` send-keys.
        self.assertEqual("".join(tmux.typed_texts()), _NEW_TEMPLATE)

    def test_drops_when_the_loop_is_already_current(self):
        word, tmux = self._deliver("sess-repl-2", _NEW_COND)
        self.assertEqual(word, "drop:already-current")
        self.assertEqual(tmux.sent, [], "an already-current loop is not retyped")

    def test_drops_a_foreign_armed_goal_without_clobbering(self):
        word, tmux = self._deliver("sess-repl-3", "fix the login bug")
        self.assertEqual(word, "drop:already-current")
        self.assertEqual(tmux.sent, [], "a foreign goal is NEVER replaced")

    def test_recent_human_defers_the_replace(self):
        word, tmux = self._deliver("sess-repl-4", _OLD_COND, recent=True)
        self.assertEqual(word, "skip:recent-human")
        self.assertEqual(tmux.sent, [], "never keystroke a human-active pane")

    def test_replace_uses_seed_reach_when_marker_is_past_the_tail(self):
        # #623-review 🟡: the stale marker is PAST the 4 MB tail
        # (`scan_goal_markers` returns None) but findable by `seed_goal_marker`'s
        # reverse-scan. The re-verify MUST use seed's reach, else a still-stale
        # long-running loop (the ones most likely to be stale after a deploy)
        # drops forever. Simulate the past-tail read by patching
        # `scan_goal_markers` to None; `seed_goal_marker` still reads the file.
        proj = self._dir()
        sid = "sess-repl-seed"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: " + _OLD_COND,
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True)
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "")), \
             m.patch.object(wd, "scan_goal_markers", return_value=(0, None)):
            word = goal.deliver_goal(
                sid, self.CWD, _NEW_TEMPLATE, "branch-merge", run=tmux,
                projects_dir=proj, now=100000, request_ts=100000,
                sleep_fn=lambda s: None, origin="stale-rearm")
        self.assertEqual(word, "sent",
                         "seed_goal_marker's reach finds the past-tail stale marker")
        self.assertEqual("".join(tmux.typed_texts()), _NEW_TEMPLATE)

    def test_stale_rearm_expiry_is_silent_no_false_ping(self):
        # #623-review 🟡: an ALIVE (just stale) loop must NOT get the "arm
        # failed, re-run /autopilot" ping when its stale-rearm request expires.
        proj = self._dir()
        sid = "sess-exp-stale"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP)
        pings = []
        word = goal.deliver_goal(
            sid, self.CWD, _NEW_TEMPLATE, "branch-merge", run=tmux,
            projects_dir=proj, now=100000 + goal.GOAL_REQUEST_MAX_AGE_S + 10,
            request_ts=100000, send_fn=lambda mm, **k: pings.append(mm),
            sleep_fn=lambda s: None, origin="stale-rearm")
        self.assertEqual(word, "expired")
        self.assertEqual(pings, [],
                         "an alive stale loop gets NO false arm-failed ping")

    def test_dark_rearm_expiry_still_pings(self):
        # contrast: a dead-loop dark-rearm expiry DOES ping (unchanged).
        proj = self._dir()
        sid = "sess-exp-dark"
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP)
        pings = []
        word = goal.deliver_goal(
            sid, self.CWD, "/goal x", "full", run=tmux, projects_dir=proj,
            now=100000 + goal.GOAL_REQUEST_MAX_AGE_S + 10, request_ts=100000,
            send_fn=lambda mm, **k: pings.append(mm), sleep_fn=lambda s: None,
            origin="dark-rearm")
        self.assertEqual(word, "expired")
        self.assertEqual(len(pings), 1, "a dead-loop dark-rearm expiry pings")

    def test_drop_already_current_is_terminal_so_goal_sweep_clears_it(self):
        self.assertIn("drop:already-current", goal._GOAL_TERMINAL_WORDS)


if __name__ == "__main__":
    unittest.main()
