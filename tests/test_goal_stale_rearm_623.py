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


class TestStaleArmedRearmObserved(unittest.TestCase):
    """#1113 (RETIRES the #623 record path) -- goal_dark_watch's armed-True
    branch only OBSERVES a template drift on an ALIVE armed loop; it NEVER
    records a re-arm request and NEVER types (the >10x david1-3 regression).
    A template change waits for the next NATURAL arm."""

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

    def test_stale_armed_loop_observes_drift_without_recording(self):
        reqs, tmux, logs, _ = self._sweep("sess-stale-1", _OLD_COND)
        self.assertEqual(reqs, {},
                         "an ACTIVE armed loop is NEVER re-armed by a keystroke")
        self.assertEqual(tmux.sent, [],
                         "dark_watch OBSERVES the drift, never keystrokes")
        self.assertTrue(any("stale-drift" in ln
                            and "waits for the next natural arm" in ln
                            for ln in logs), logs)

    def test_current_armed_loop_is_silent(self):
        reqs, _, logs, _ = self._sweep("sess-current-1", _NEW_COND)
        self.assertEqual(reqs, {}, "a current condition needs no observation")
        self.assertFalse(any("stale-drift" in ln for ln in logs), logs)

    def test_foreign_armed_loop_is_never_touched(self):
        reqs, _, logs, _ = self._sweep("sess-foreign-1",
                                       "fix the login bug and ship it")
        self.assertEqual(reqs, {}, "a hand-armed foreign goal is never observed")
        self.assertFalse(any("stale-drift" in ln for ln in logs), logs)

    def test_drift_observed_regardless_of_backlog(self):
        # #1113: the former workability/cache/attempt-cap gates governed a
        # KEYSTROKE, which no longer happens -- an empty/stale-cache backlog no
        # longer suppresses the pure OBSERVATION.
        reqs, _, logs, _ = self._sweep("sess-stale-empty", _OLD_COND,
                                       obl=(0, 100000))
        self.assertEqual(reqs, {})
        self.assertTrue(any("stale-drift" in ln for ln in logs), logs)

    def test_drift_observed_even_on_stale_cache(self):
        reqs, _, logs, _ = self._sweep("sess-stale-oldcache", _OLD_COND,
                                       obl=(7, 1), now=400000)
        self.assertEqual(reqs, {})
        self.assertTrue(any("stale-drift" in ln for ln in logs), logs)

    def test_dry_run_observes_but_records_nothing(self):
        reqs, tmux, logs, _ = self._sweep("sess-stale-dry", _OLD_COND,
                                          dry_run=True)
        self.assertEqual(reqs, {}, "dry-run records nothing")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("stale-drift" in ln for ln in logs), logs)

    def test_drift_observation_deduped_per_template_version(self):
        # #1113 (was #1092 (e), reused for the observation): the drift is logged
        # ONCE per (session, template hash), so a template deploy does not
        # re-log every armed session's drift every ~30 min sweep.
        state = {}
        _, _, l1, _ = self._sweep("sess-cap", _OLD_COND, state=state)
        self.assertTrue(any("stale-drift" in ln for ln in l1), l1)
        _, _, l2, _ = self._sweep("sess-cap", _OLD_COND, state=state, now=100200)
        self.assertFalse(any("stale-drift" in ln for ln in l2),
                         "same template version -> observed once")
        # a CHANGED template re-opens the observation.
        newer = _NEW_TEMPLATE + " EXTRA v2 clause."
        _, _, l3, _ = self._sweep("sess-cap", _OLD_COND, tmpl=newer,
                                  state=state, now=100500)
        self.assertTrue(any("stale-drift" in ln for ln in l3),
                        "a changed template version re-observes the drift")


class TestDeliverStaleRearmRetired(unittest.TestCase):
    """#1113 -- deliver_goal never REPLACES an armed loop. A leftover on-disk
    `stale-rearm` request (recorded before the origin was retired) is dropped
    `drop:stale-rearm-retired` before any pane work / keystroke; an armed footer
    drops `already-armed` for every origin. The #623 REPLACE path is deleted."""

    CWD = "/home/newlevel/devel/stalereplace"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, sid, armed_cond, text=_NEW_TEMPLATE, recent=False,
                 model_type=True, origin="stale-rearm"):
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
                origin=origin)
        return word, tmux

    def test_leftover_stale_rearm_request_dropped_retired(self):
        word, tmux = self._deliver("sess-repl-1", _OLD_COND)
        self.assertEqual(word, "drop:stale-rearm-retired",
                         "a leftover stale-rearm request is never typed")
        self.assertEqual(tmux.sent, [])

    def test_retired_drop_precedes_the_recent_human_gate(self):
        # the retired drop is terminal BEFORE any pane resolution, so even a
        # human-present pane yields the same terminal drop (never a keystroke).
        word, tmux = self._deliver("sess-repl-4", _OLD_COND, recent=True)
        self.assertEqual(word, "drop:stale-rearm-retired")
        self.assertEqual(tmux.sent, [])

    def test_retired_drop_even_for_a_current_or_foreign_condition(self):
        # the origin drops FIRST, whatever the loop's stored condition is.
        for cond in (_NEW_COND, "fix the login bug"):
            word, tmux = self._deliver("sess-repl-c-" + cond[:4], cond)
            self.assertEqual(word, "drop:stale-rearm-retired")
            self.assertEqual(tmux.sent, [])

    def test_stale_rearm_expiry_never_reached_no_false_ping(self):
        # #623-review 🟡 held: an ALIVE (just stale) loop must NOT get the "arm
        # failed" ping. #1113: it now drops retired BEFORE the expiry gate, so
        # no ping regardless of age.
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
        self.assertEqual(word, "drop:stale-rearm-retired")
        self.assertEqual(pings, [],
                         "a retired stale-rearm gets NO arm-failed ping")

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

    def test_stale_rearm_retired_is_terminal_so_goal_sweep_clears_it(self):
        self.assertIn("drop:stale-rearm-retired", goal._GOAL_TERMINAL_WORDS)

    def test_already_current_word_removed(self):
        # the #623 stale-rearm REPLACE re-verify (the only producer of
        # drop:already-current) is deleted, so the terminal word is gone.
        self.assertNotIn("drop:already-current", goal._GOAL_TERMINAL_WORDS)

    def test_stale_rearm_origin_left_the_partition_tuples(self):
        self.assertNotIn(goal._GOAL_STALE_REARM_ORIGIN,
                         goal._GOAL_WATCHDOG_REARM_ORIGINS)
        self.assertNotIn(goal._GOAL_STALE_REARM_ORIGIN,
                         goal._GOAL_ATTEMPTS_STATE_KEYS)

if __name__ == "__main__":
    unittest.main()
