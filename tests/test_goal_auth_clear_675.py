"""#675 -- CC's transient-auth `Goal cleared after an unrecoverable error
(authentication failed)` variant must be recognized by the transcript-marker
parser (so the loop flips armed->false and a fresh /autopilot request is not
dropped `already-armed`), and the watchdog must AUTO-RE-ARM it (owner ruling
#662/#676: auth blips are NORMAL -- silence + mechanical recovery), while NEVER
re-arming a user-intentional `/goal clear` (#170). Plus the two request-lifecycle
defects surfaced from the same live logs: a re-arm request repeatedly SKIPped for
recent-human presence must WAIT (not drop at the 300s dark-rearm staleness gate),
and a grossly-future presence marker must not read as recent-human.

Deliberately a NEW file (conflict-free vs the in-flight goal.py lanes).
"""

import json
import os
import time
import unittest
import unittest.mock as m
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import watchdog as wd
from watchdog import goal, goal_scan

from _goal_arm_helpers import (
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
    _write_goal_marker,
)

# The EXACT shape CC writes on a transient-auth clear (camera-box 90bc51f3,
# 2026-08-18): a plain `system` entry whose TOP-LEVEL `content` is this string,
# NOT `<local-command-stdout>`-wrapped and NOT starting with `Goal cleared:`.
AUTH_CLEAR = ('Goal cleared after an unrecoverable error (authentication '
              'failed): "STOP CONDITIONS the loop is DONE...". Run /goal '
              'again to continue.')
# A NON-auth unrecoverable error clear (same envelope, different reason).
ERROR_CLEAR = ('Goal cleared after an unrecoverable error (some other '
               'failure): "STOP CONDITIONS...". Run /goal again to continue.')


def _write_auth_clear_marker(base, cwd, sid, content=AUTH_CLEAR, ts_epoch=None):
    """Append a BARE `system` auth/error-clear entry (no LCS wrapper) to the
    SAME transcript the marker readers consume."""
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    ts = ts_epoch if ts_epoch is not None else time.time()
    iso = datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    entry = {"type": "system", "subtype": "goal", "timestamp": iso,
             "content": content}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return p


def _auth_line(content=AUTH_CLEAR, ts="2026-08-18T13:16:34.404Z"):
    return json.dumps({"type": "system", "timestamp": ts,
                       "content": content}).encode()


class AuthClearParser(unittest.TestCase):
    """Scope 1 -- the parser + `_newest_marker` pre-filter recognize EVERY
    `Goal cleared` variant, tagging the CC transient-auth one `clear_kind=auth`
    (auto-rearm eligible) and a user `/goal clear` `clear_kind=user` (#170)."""

    def test_auth_variant_parses_as_cleared_auth(self):
        mark = goal_scan._parse_goal_marker(AUTH_CLEAR)
        self.assertIsNotNone(mark, "the auth-variant clear must be recognized")
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "auth")

    def test_non_auth_error_variant_parses_as_cleared_error(self):
        mark = goal_scan._parse_goal_marker(ERROR_CLEAR)
        self.assertIsNotNone(mark)
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "error",
                         "a non-auth unrecoverable error is NOT auto-rearm eligible")

    def test_user_command_clear_tagged_clear_kind_user(self):
        user = "<local-command-stdout>Goal cleared: /goal x</local-command-stdout>"
        mark = goal_scan._parse_goal_marker(user)
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "user",
                         "a deliberate /goal clear must NEVER be auto-rearmed")

    def test_set_marker_unchanged(self):
        setm = "<local-command-stdout>Goal set: /goal x</local-command-stdout>"
        mark = goal_scan._parse_goal_marker(setm)
        self.assertEqual(mark.get("state"), "set")
        self.assertIsNone(mark.get("clear_kind"))

    def test_newest_marker_prefilter_recognizes_auth_clear(self):
        mark = goal_scan._newest_marker(_auth_line())
        self.assertIsNotNone(mark, "pre-filter must not skip the auth-clear line")
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "auth")

    def test_scan_goal_markers_flips_set_to_cleared_on_auth(self):
        # A real transcript: Goal set: then the CC auth clear -> the newest
        # marker read must be `cleared` (the loop is NOT armed), not the stale set.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cwd = "/home/newlevel/devel/authscan"
        sid = "sess-auth-scan"
        _write_marker_transcript(d.name, cwd, sid)
        _write_goal_marker(d.name, cwd, sid, "Goal set: /goal x", ts_epoch=500)
        _write_auth_clear_marker(d.name, cwd, sid, ts_epoch=600)
        p = Path(d.name) / wd.encode_project_dir(cwd) / (sid + ".jsonl")
        _off, mark = wd.scan_goal_markers(str(p))
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "auth")


class PresenceClockSkew(unittest.TestCase):
    """Scope 3 -- a slightly-future presence marker is still recent-human
    (mid-sweep drift), but a GROSSLY future one no longer extends the recency
    window to a false 30-min veto."""

    def setUp(self):
        self.now = 1_000_000.0
        self.sid = "authclock-" + uuid.uuid4().hex
        self.marker = "/tmp/claude-user-active-%s" % self.sid
        Path(self.marker).write_text("")
        self.addCleanup(lambda: Path(self.marker).unlink(missing_ok=True))

    def _recent(self, future_s, patch_tprompt=None):
        os.utime(self.marker, (self.now + future_s, self.now + future_s))
        with m.patch.object(wd, "_last_human_prompt_ts",
                            return_value=patch_tprompt):
            return wd._goal_autoarm_recent_human_activity(
                self.sid, "/no/such/tpath", self.now)

    def test_slightly_future_marker_still_recent(self):
        recent, reason = self._recent(5)
        self.assertTrue(recent, "a marker a few seconds in the future is a live human")
        self.assertIn("future", reason)

    def test_grossly_future_marker_not_recent(self):
        # > GOAL_PRESENCE_FUTURE_SKEW_S in the future = clock desync / cross-box
        # sync, NOT a live human -- must not veto for the full window.
        future = wd.GOAL_PRESENCE_FUTURE_SKEW_S + 200
        recent, _ = self._recent(future)
        self.assertFalse(recent)

    def test_grossly_future_transcript_prompt_not_recent(self):
        # The same small-future bound applies to the transcript-prompt signal.
        os.utime(self.marker, (self.now - 10_000, self.now - 10_000))  # marker stale
        future = wd.GOAL_PRESENCE_FUTURE_SKEW_S + 200
        with m.patch.object(wd, "_last_human_prompt_ts",
                            return_value=self.now + future):
            recent, _ = wd._goal_autoarm_recent_human_activity(
                self.sid, "/no/such/tpath", self.now)
        self.assertFalse(recent)


class DarkRearmRequestPersistence(unittest.TestCase):
    """Scope 2 -- a dark-rearm request repeatedly SKIPped for recent-human
    presence must WAIT (skip:recent-human), never DROP at the 300s dark-rearm
    staleness gate. The gate still drops a genuinely-stuck (no-human) dark-rearm."""

    CWD = "/home/newlevel/devel/rearmpersist"
    TEXT = "/goal DONE or stop after 50"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured, now, request_ts, origin, sid="sess-rearm",
            recent=None):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, model_type=True)
        ctx = (m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                              return_value=recent)
               if recent is not None else _nullctx())
        with ctx:
            return goal.deliver_goal(sid, self.CWD, self.TEXT, "full",
                                     run=tmux, projects_dir=proj, now=now,
                                     request_ts=request_ts, origin=origin,
                                     sleep_fn=lambda s: None)

    def test_recent_human_defers_instead_of_stale_drop(self):
        # RED: a stale-aged (>300s) dark-rearm with a present human must NOT be
        # dropped as stale -- it WAITS for the next idle window (skip:recent-human).
        now = 1_000_000
        stale = now - goal.GOAL_DARK_REARM_STALE_S - 50   # 350s old
        word = self._go(GOAL_IDLE_CAP, now, stale, goal._GOAL_REARM_ORIGIN,
                        recent=(True, "presence marker 5s old"))
        self.assertEqual(word, "skip:recent-human")

    def test_no_human_stale_dark_rearm_still_drops(self):
        # Regression lock: with NO human present, the 300s staleness gate still
        # drops a stale dark-rearm (the #524 protection is preserved).
        now = 1_000_000
        stale = now - goal.GOAL_DARK_REARM_STALE_S - 50
        word = self._go(GOAL_IDLE_CAP, now, stale, goal._GOAL_REARM_ORIGIN,
                        recent=(False, ""))
        self.assertEqual(word, "drop:stale-rearm")

    def test_fresh_dark_rearm_no_human_still_sends(self):
        # A just-recorded dark-rearm with no human still types.
        now = 1_000_000
        word = self._go(GOAL_IDLE_CAP, now, now - 10, goal._GOAL_REARM_ORIGIN,
                        recent=(False, ""))
        self.assertEqual(word, "sent")

    def test_general_age_cap_still_expires_a_stale_rearm(self):
        # The 30-min absolute cap remains the bound even under continuous
        # presence: an OVER-cap request expires regardless of the human.
        now = 1_000_000
        old = now - goal.GOAL_REQUEST_MAX_AGE_S - 10
        word = self._go(GOAL_IDLE_CAP, now, old, goal._GOAL_REARM_ORIGIN,
                        recent=(True, "presence marker 5s old"))
        self.assertEqual(word, "expired")


class AuthClearRearm(unittest.TestCase):
    """Scope 4 -- goal_dark_watch AUTO-RE-ARMS a loop CC cleared on transient
    auth (records a dark-rearm request), only when the session is ALIVE again
    (readable dark footer), and NEVER re-arms a user-intentional clear (#170)."""

    CWD = "/home/newlevel/devel/authrearm"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _fixture(self, sid, cap=GOAL_IDLE_CAP, clear=AUTH_CLEAR):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        _write_auth_clear_marker(proj, self.CWD, sid, content=clear, ts_epoch=600)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], cap)
        return proj, tmux

    def _watch(self, tmux, proj, sid, open_n=5, cts=100, now=100000):
        reqs = self._dir() / "goal-requests.json"

        def rearm(cwd):
            return ("/goal DONE or stop after 50", "full")

        goal.goal_dark_watch(now, run=tmux, send_fn=lambda mm, **k: None,
                             projects_dir=proj, state={},
                             sleep_fn=lambda s: None,
                             obligation_fn=lambda cwd: (open_n, cts),
                             rearm_fn=rearm, requests_path=reqs)
        return reqs

    def test_auth_clear_alive_records_rearm(self):
        # RED: a loop CC cleared on auth, pane ALIVE + dark (armed False), a
        # workable backlog -> record a dark-rearm request on the FIRST sweep
        # (no 8-read confirmation -- the auth clear is unambiguous).
        proj, tmux = self._fixture("sess-auth-alive")
        reqs = self._watch(tmux, proj, "sess-auth-alive")
        d = goal.load_goal_requests(reqs)
        self.assertIn("sess-auth-alive", d, "auth-clear + alive must re-arm")
        self.assertEqual(d["sess-auth-alive"]["origin"], "dark-rearm")
        self.assertEqual(tmux.sent, [], "dark-watch never types -- only records")

    def test_user_clear_never_rearms(self):
        # #170: a user-intentional clear (clear_kind=user) is NEVER auto-rearmed.
        proj = self._dir()
        sid = "sess-user-clear"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: /goal x", ts_epoch=500)
        _write_goal_marker(proj, self.CWD, sid, "Goal cleared: /goal x",
                           ts_epoch=600)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        reqs = self._watch(tmux, proj, sid)
        self.assertEqual(goal.load_goal_requests(reqs), {},
                         "a user /goal clear must never re-arm")

    def test_non_auth_error_clear_never_rearms(self):
        # A non-auth unrecoverable error is recognized (flips armed->false) but
        # NOT auto-rearmed -- it needs human attention, not a re-type loop.
        proj, tmux = self._fixture("sess-err-clear", clear=ERROR_CLEAR)
        reqs = self._watch(tmux, proj, "sess-err-clear")
        self.assertEqual(goal.load_goal_requests(reqs), {})

    def test_auth_clear_already_rearmed_pane_no_rearm(self):
        # armed True (something already re-armed the loop) -> nothing to do.
        proj, tmux = self._fixture("sess-auth-armed", cap=GOAL_ARMED_CAP)
        reqs = self._watch(tmux, proj, "sess-auth-armed")
        self.assertEqual(goal.load_goal_requests(reqs), {})

    def test_auth_clear_dead_pane_no_rearm(self):
        # armed None (busy/undeterminable -> no readable input box) -> can't
        # confirm the session is alive again, so no re-arm.
        proj, tmux = self._fixture("sess-auth-dead", cap=GOAL_BUSY_CAP)
        reqs = self._watch(tmux, proj, "sess-auth-dead")
        self.assertEqual(goal.load_goal_requests(reqs), {})

    def test_auth_clear_unworkable_backlog_no_rearm(self):
        # An achieved/empty backlog is not worth a keystroke.
        proj, tmux = self._fixture("sess-auth-empty")
        reqs = self._watch(tmux, proj, "sess-auth-empty", open_n=0)
        self.assertEqual(goal.load_goal_requests(reqs), {})


class _nullctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main()
