"""#675 -- CC's transient-auth `Goal cleared after an unrecoverable error
(authentication failed)` variant must be recognized by the transcript-marker
parser (so the loop flips armed->false and a fresh /autopilot request is not
dropped `already-armed`); a grossly-future presence marker must not read as
recent-human on the delivery path; and a parser bump RESEEDS a stale-offset
marker. #1324 retired the auth RE-ARM itself (owner 14.11.2026: the watchdog
never arms a goal) -- an auth clear is only journalled, see
tests/test_goal_no_auto_arm_1324.py.
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
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
    _write_goal_marker,
)

# The autopilot condition SIGNATURE (goal_registry's `header` clause) — every
# real autopilot /goal condition opens with it; a FOREIGN (hand-armed) goal does
# not. CC TRUNCATES the cleared condition, but the truncation is AFTER the
# signature, so a real auth-cleared autopilot goal still classifies.
_SIG = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"
_TEMPLATE = ("/goal " + _SIG + ", both checkable from the transcript: (A) "
             "BLOCKED ON MY ANSWER; (B) backlog empty. Or stop after 200 turns.")
_COND = _TEMPLATE[len("/goal "):]

# The EXACT envelope CC writes on a transient-auth clear (camera-box 90bc51f3,
# 2026-08-18): a plain `system` entry whose TOP-LEVEL `content` is this string,
# NOT `<local-command-stdout>`-wrapped and NOT starting with `Goal cleared:`. The
# condition CC quotes is truncated with `…` but keeps the signature opening.
AUTH_CLEAR = ('Goal cleared after an unrecoverable error (authentication '
              'failed): "%s, both checkable from the transcript: (A) BLOCKED…". '
              'Run /goal again to continue.' % _SIG)
# A NON-auth unrecoverable error clear (same envelope, different reason) whose
# CONDITION happens to mention authentication — must still be `error`, never auth.
ERROR_CLEAR = ('Goal cleared after an unrecoverable error (rate limit exceeded): '
               '"%s, then fix the authentication bug…". Run /goal again to '
               'continue.' % _SIG)
# A transient-auth clear of a FOREIGN (hand-armed) goal — must NOT be re-armed.
FOREIGN_AUTH_CLEAR = ('Goal cleared after an unrecoverable error (authentication '
                      'failed): "fix the login bug and make all tests pass…". '
                      'Run /goal again to continue.')


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_auth_clear_marker(base, cwd, sid, content=AUTH_CLEAR, ts_epoch=None,
                             entry_type="system"):
    """Append a bare auth/error-clear entry (no LCS wrapper) to the SAME
    transcript the marker readers consume. `entry_type` lets a test forge a
    `user` entry (the 🔴-1 injection vector)."""
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    ts = ts_epoch if ts_epoch is not None else time.time()
    if entry_type == "user":
        entry = {"type": "user", "timestamp": _iso(ts),
                 "message": {"id": "m", "content": content}}
    else:
        entry = {"type": "system", "subtype": "goal", "timestamp": _iso(ts),
                 "content": content}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return p


def _line(content, entry_type="system", ts="2026-08-18T13:16:34.404Z"):
    if entry_type == "user":
        return json.dumps({"type": "user", "timestamp": ts,
                           "message": {"id": "m", "content": content}}).encode()
    return json.dumps({"type": "system", "timestamp": ts,
                       "content": content}).encode()


class AuthClearParser(unittest.TestCase):
    """Scope 1 + 🔴-1 + 🟡-2/3 + 🟡-4b."""

    def test_auth_variant_parses_as_cleared_auth_from_system(self):
        mark = goal_scan._parse_goal_marker(AUTH_CLEAR, is_system=True)
        self.assertIsNotNone(mark)
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "auth")
        # payload extracted (truncated) AND opens with the autopilot signature.
        self.assertTrue((mark.get("payload") or "").startswith(_SIG))

    def test_error_variant_reason_keyed_not_condition_keyed(self):
        # ERROR_CLEAR's reason is "rate limit exceeded"; its CONDITION mentions
        # "authentication" — must still be `error`, never auth.
        mark = goal_scan._parse_goal_marker(ERROR_CLEAR, is_system=True)
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "error")

    def test_auth_clear_from_user_entry_is_NOT_recognized(self):
        # 🔴-1: a user paste beginning with the clear text is forgeable content.
        self.assertIsNone(goal_scan._parse_goal_marker(AUTH_CLEAR, is_system=False))

    def test_user_command_clear_tagged_clear_kind_user(self):
        user = "<local-command-stdout>Goal cleared: %s</local-command-stdout>" % _COND
        mark = goal_scan._parse_goal_marker(user)
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "user")

    def test_set_marker_unchanged(self):
        setm = "<local-command-stdout>Goal set: %s</local-command-stdout>" % _COND
        mark = goal_scan._parse_goal_marker(setm)
        self.assertEqual(mark.get("state"), "set")
        self.assertIsNone(mark.get("clear_kind"))

    def test_newest_marker_prefilter_recognizes_auth_system_line(self):
        mark = goal_scan._newest_marker(_line(AUTH_CLEAR, entry_type="system"))
        self.assertIsNotNone(mark)
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "auth")

    def test_newest_marker_rejects_auth_text_in_user_line(self):
        # 🔴-1 end-to-end: a user entry with the auth-clear text is not a marker.
        self.assertIsNone(goal_scan._newest_marker(_line(AUTH_CLEAR, entry_type="user")))

    def test_scan_goal_markers_flips_set_to_cleared_on_auth(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cwd = "/home/newlevel/devel/authscan"
        sid = "sess-auth-scan"
        _write_marker_transcript(d.name, cwd, sid)
        _write_goal_marker(d.name, cwd, sid, "Goal set: %s" % _COND, ts_epoch=500)
        _write_auth_clear_marker(d.name, cwd, sid, ts_epoch=600)
        p = Path(d.name) / wd.encode_project_dir(cwd) / (sid + ".jsonl")
        _off, mark = wd.scan_goal_markers(str(p))
        self.assertEqual(mark.get("state"), "cleared")
        self.assertEqual(mark.get("clear_kind"), "auth")


class PresenceClockSkew(unittest.TestCase):
    """Scope 3 + 🟡-5/6 + 🔵-9 -- the narrow future skew is DELIVERY-scoped (a
    param), the default stays symmetric for every other (incl. destructive) caller."""

    def setUp(self):
        self.now = 1_000_000.0
        self.sid = "authclock-" + uuid.uuid4().hex
        self.marker = "/tmp/claude-user-active-%s" % self.sid
        Path(self.marker).write_text("")
        self.addCleanup(lambda: Path(self.marker).unlink(missing_ok=True))

    def _recent(self, future_s, future_skew_s=None):
        os.utime(self.marker, (self.now + future_s, self.now + future_s))
        with m.patch.object(wd, "_last_human_prompt_ts", return_value=None):
            return wd._goal_autoarm_recent_human_activity(
                self.sid, "/no/such/tpath", self.now, future_skew_s=future_skew_s)

    def test_slightly_future_marker_still_recent(self):
        recent, reason = self._recent(5, future_skew_s=wd.GOAL_PRESENCE_FUTURE_SKEW_S)
        self.assertTrue(recent)
        self.assertIn("future", reason)

    def test_grossly_future_not_recent_under_delivery_skew(self):
        future = wd.GOAL_PRESENCE_FUTURE_SKEW_S + 200
        recent, _ = self._recent(future, future_skew_s=wd.GOAL_PRESENCE_FUTURE_SKEW_S)
        self.assertFalse(recent)

    def test_grossly_future_STILL_recent_under_symmetric_default(self):
        # The destructive consumers keep the symmetric default -> a future-dated
        # stamp still VETOES (the fail-safe direction #339 relied on).
        future = wd.GOAL_PRESENCE_FUTURE_SKEW_S + 200
        recent, _ = self._recent(future, future_skew_s=None)
        self.assertTrue(recent)

    def test_grossly_future_transcript_prompt_not_recent_delivery(self):
        os.utime(self.marker, (self.now - 10_000, self.now - 10_000))  # marker stale
        future = wd.GOAL_PRESENCE_FUTURE_SKEW_S + 200
        with m.patch.object(wd, "_last_human_prompt_ts", return_value=self.now + future):
            recent, _ = wd._goal_autoarm_recent_human_activity(
                self.sid, "/no/such/tpath", self.now,
                future_skew_s=wd.GOAL_PRESENCE_FUTURE_SKEW_S)
        self.assertFalse(recent)


class ParserVersionReseed(unittest.TestCase):
    """🟡-4 (deployed-but-inert) -- a persisted goal_mark whose offset already
    advanced PAST the auth-clear (old pre-filter) is RESEEDED on a parser bump."""

    CWD = "/home/newlevel/devel/reseed"

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_stale_set_mark_at_old_pv_is_reseeded_to_cleared(self):
        proj = self._dir()
        sid = "sess-reseed"
        _write_marker_transcript(proj, self.CWD, sid)
        _write_goal_marker(proj, self.CWD, sid, "Goal set: %s" % _COND, ts_epoch=500)
        _write_auth_clear_marker(proj, self.CWD, sid, ts_epoch=600)
        tp = Path(proj) / wd.encode_project_dir(self.CWD) / (sid + ".jsonl")
        size = tp.stat().st_size
        # Simulate a PRE-fix persisted entry: offset already PAST EOF (past the
        # auth-clear), mark stale "set", stamped with NO parser version.
        state = {"goal_mark": {sid: {"off": size, "mark": {"state": "set",
                 "payload": _COND, "ts": 500}, "tmtime": 0}}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        logs = goal.goal_dark_watch(100000, run=tmux, send_fn=lambda mm, **k: None,
                                    projects_dir=proj, state=state,
                                    sleep_fn=lambda s: None,
                                    rearm_fn=lambda cwd: (_TEMPLATE, "full"))
        rec = state["goal_mark"][sid]
        self.assertEqual(rec["mark"]["state"], "cleared",
                         "the reseed must re-read the auth-clear the old offset skipped")
        self.assertEqual(rec.get("pv"), goal._GOAL_MARK_PARSER_VERSION)
        # and the reseed surfaced the auth clear -- journalled, never re-armed
        # (#1324).
        self.assertTrue(any("goal-lost:auth-clear" in ln for ln in logs), logs)
        self.assertEqual(goal.load_goal_requests(), {})


class _nullctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main()
