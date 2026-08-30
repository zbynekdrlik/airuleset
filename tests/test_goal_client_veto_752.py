"""#752 -- the `goal-arm --self` client-activity veto is ORIGIN-SCOPED.

The 5-min client-input DEFER (#731) used to fire for EVERY origin, so an owner
who typed `/autopilot` and kept typing (watching the terminal) starved their own
`self-callback` arm forever -- auto-arm worked ONLY unattended, the inverse of
what the owner expects (owner ruling 2026-08-30: origin=self => ZERO client-
activity veto). These tests lock:

  * a `self-callback` arm is NEVER deferred by a recent attached-client input
    (delivery is attempted on the first idle sweep);
  * a WATCHDOG re-arm (dark/stale/auth) still IS deferred (the veto is preserved
    for the unattended-injection hazard, via the recent-human gate that since
    #731 includes the same client-input signal);
  * a future-skewed `client_activity` renders no confusing "in the future" text;
  * a `self-callback` request older than GOAL_DARK_REARM_STALE_S (300 s) but
    younger than GOAL_REQUEST_MAX_AGE_S (1800 s) is NOT dropped as stale -- it is
    governed by the 30-min max-age, never the dark-rearm 300-s gate.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal

from _goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    DeliverGoalFakeTmux,
    _write_marker_transcript,
)


class _ClientFake(DeliverGoalFakeTmux):
    """A DeliverGoalFakeTmux that answers `tmux list-clients -F
    '#{client_activity}'` with the given attached-client epoch(s); everything
    else delegates to the base fake."""

    def __init__(self, *a, client_epochs=(), **kw):
        super().__init__(*a, **kw)
        self.client_epochs = list(client_epochs)

    def __call__(self, argv, timeout=8):
        if "list-clients" in " ".join(argv):
            if not self.client_epochs:
                return ""
            return "\n".join(str(int(e)) for e in self.client_epochs) + "\n"
        return super().__call__(argv, timeout)


class TestSelfCallbackClientVeto752(unittest.TestCase):
    CWD = "/home/newlevel/devel/clientveto"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_self_callback_ignores_recent_client_input(self):
        # RED (today): a self-callback with a live attached client is vetoed
        # `skip:client-active` on every sweep. GREEN: origin=self is never
        # deferred by the client-activity signal -> delivery is attempted.
        proj = self._dir()
        sid = "sess-self-active"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=2000, path=self.reqp, origin="self-callback")
        # attached client with input 5 s ago -- the owner typing /autopilot.
        tmux = _ClientFake([("%9", "claude", self.CWD, "111")],
                           GOAL_IDLE_CAP, model_type=True, client_epochs=[1995])
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda msg, **k: None,
                               sleep_fn=lambda *a, **k: None)
        self.assertFalse(any("skip:client-active" in ln for ln in logs),
                         "the owner's own /autopilot arm must NOT be vetoed by "
                         "their keyboard activity: %s" % logs)
        self.assertEqual(tmux.typed_texts(), ["/goal x"],
                         "the self-callback arm must be typed despite a live "
                         "attached client")
        self.assertTrue(any("OK (goal-sweep)" in ln for ln in logs), logs)

    def test_watchdog_rearm_still_vetoed_by_client_input(self):
        # The veto is PRESERVED for a watchdog-initiated re-arm: a dark-rearm
        # into a pane with a recent attached-client input is deferred (no
        # keystroke), so an unexpected /goal never lands in a human-active pane.
        proj = self._dir()
        sid = "sess-dark-active"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=2000, path=self.reqp, origin="dark-rearm")
        # client input 60 s ago -- inside the 5-min veto window.
        tmux = _ClientFake([("%9", "claude", self.CWD, "111")],
                           GOAL_IDLE_CAP, model_type=True, client_epochs=[1940])
        logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda msg, **k: None,
                               sleep_fn=lambda *a, **k: None)
        self.assertEqual(tmux.typed_texts(), [],
                         "a watchdog re-arm must NOT type into a human-active "
                         "pane")
        self.assertTrue(any("skip:" in ln for ln in logs), logs)
        self.assertIn(sid, goal.load_goal_requests(self.reqp),
                      "a deferred re-arm leaves the request pending")

    def test_self_callback_governed_by_max_age_not_stale_rearm(self):
        # Max-age reconcile lock (#752): a self-callback older than
        # GOAL_DARK_REARM_STALE_S (300 s) but younger than
        # GOAL_REQUEST_MAX_AGE_S (1800 s) is NOT dropped as stale -- that
        # 300-s gate is reached ONLY for dark/auth-rearm. It proceeds to
        # delivery, so the shortened-veto self path can never expire early.
        self.assertLess(goal.GOAL_DARK_REARM_STALE_S, 959)
        self.assertLess(959, goal.GOAL_REQUEST_MAX_AGE_S)
        proj = self._dir()
        sid = "sess-self-959"
        _write_marker_transcript(proj, self.CWD, sid)
        goal.record_goal_request(sid, self.CWD, "/goal x", "full",
                                 now=1000, path=self.reqp, origin="self-callback")
        tmux = _ClientFake([("%9", "claude", self.CWD, "111")],
                           GOAL_IDLE_CAP, model_type=True, client_epochs=[])
        logs = goal.goal_sweep(1959, run=tmux, projects_dir=proj,
                               requests_path=self.reqp,
                               send_fn=lambda msg, **k: None,
                               sleep_fn=lambda *a, **k: None)
        self.assertFalse(any("drop:stale-rearm" in ln for ln in logs),
                         "a 959-s self-callback must NOT be dropped by the "
                         "300-s dark-rearm gate: %s" % logs)
        self.assertTrue(any("OK (goal-sweep)" in ln for ln in logs), logs)


class TestFutureSkewClamp752(unittest.TestCase):
    def _run(self, epoch):
        def run(argv, timeout=8):
            return ("%d\n" % int(epoch)) if "list-clients" in " ".join(argv) else ""
        return wd._tmux_client_recent_input("%9", run, 2000)

    def test_future_client_activity_has_no_in_the_future_text(self):
        # RED (today): a future-dated client_activity renders
        # "10s in the future". GREEN: it clamps to "now" and carries the raw
        # skew as a diagnostic token, never the confusing "in the future".
        recent, reason = self._run(2010)      # 10 s in the future
        self.assertTrue(recent, "a small future skew is still 'recent' (veto)")
        self.assertNotIn("in the future", reason)
        self.assertIn("future-skew", reason)

    def test_past_client_activity_still_reads_age(self):
        recent, reason = self._run(1990)       # 10 s ago
        self.assertTrue(recent)
        self.assertIn("10s old", reason)
        self.assertNotIn("future-skew", reason)


if __name__ == "__main__":
    unittest.main()
