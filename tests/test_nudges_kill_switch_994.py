"""#994 — global nudge kill switch.

One owner-controlled off switch for the watchdog's machine-typed nudges, read by
ONE predicate (`watchdog.nudges_enabled`) consulted at the top of each of the FIVE
keystroke helpers in `watchdog/tmux_io.py`. When OFF: nothing is typed, ONE journal
line is written, and each helper returns its EXISTING "not delivered" shape so no
caller books state as delivered. The ONLY exception is the owner's own Discord reply
(`user_authored=True`, passed solely by `watchdog/discord_replies.py`). The nudge
texts must not prescribe priority or lane count.

These tests drive the predicate, the five helpers' suppression (no send-keys +
journal + return shape), the owner-reply bypass, the compact-pending outcome, the
CLI marker read/write + status + `--fleet` fan-out, the statusline badge, and the
phrase-lock on the nudge-text composers.
"""

import argparse
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import statusbar  # noqa: E402
import airuleset  # noqa: E402

PID = "%9"


class _Recorder:
    """A fake `run` that records every argv and echoes "" — a suppressed helper
    must never reach it, so `calls` stays empty."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout=8):
        self.calls.append(argv)
        return ""

    def sent_keys(self):
        return [a for a in self.calls if "send-keys" in " ".join(a)]


# --------------------------------------------------------------------------- #
# The predicate.
# --------------------------------------------------------------------------- #
class TestNudgesEnabledPredicate(unittest.TestCase):
    def test_marker_absent_present_corrupt_and_bypass(self):
        with TemporaryDirectory() as home:
            # The suite sets AIRULESET_TEST_IGNORE_DISABLE (autouse in conftest +
            # cmd_push injection) — remove it here to exercise the real marker.
            with m.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
                # No marker -> enabled.
                self.assertTrue(wd.nudges_enabled(home=home))
                path = wd.nudges_marker_path(home=home)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as h:
                    h.write('{"since": "2026-09-11T00:00:00Z", '
                            '"by": "owner", "reason": null}')
                # Marker present -> OFF.
                self.assertFalse(wd.nudges_enabled(home=home))
                # A corrupt marker STILL means OFF (fail-safe, never re-enable).
                with open(path, "w", encoding="utf-8") as h:
                    h.write("not json {{{")
                self.assertFalse(wd.nudges_enabled(home=home))
                # The test-ignore bypass re-enables (parity with _owner_disabled).
                os.environ["AIRULESET_TEST_IGNORE_DISABLE"] = "1"
                self.assertTrue(wd.nudges_enabled(home=home))


# --------------------------------------------------------------------------- #
# The five helpers suppress at OFF: no send-keys, ONE journal line, "not
# delivered" return shape.
# --------------------------------------------------------------------------- #
class TestHelpersSuppressWhenOff(unittest.TestCase):
    def _off(self):
        return m.patch.object(wd, "nudges_enabled", lambda *a, **k: False)

    def _assert_journaled(self, logs):
        self.assertTrue(
            any("nudges OFF: suppressed" in ln for ln in logs),
            "expected a `nudges OFF: suppressed ...` journal line, got %r" % logs)

    def test_send_verified_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.send_verified(PID, "lane-check: backlog=5", rec,
                                  tpath="/x", logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])
        self._assert_journaled(logs)

    def test_send_continue_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            r = wd.send_continue(PID, "/compact", rec, logs=logs)
        self.assertFalse(r)
        self.assertEqual(rec.sent_keys(), [])
        self._assert_journaled(logs)

    def test_send_subagent_nudge_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.send_subagent_nudge(PID, "wid-1", "api-error", rec,
                                        tpath="/x", logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])
        self._assert_journaled(logs)

    def test_submit_own_goal_verified_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.submit_own_goal_verified(PID, "/goal drive CI green", rec,
                                             logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])
        self._assert_journaled(logs)

    def test_submit_own_draft_verified_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.submit_own_draft_verified(PID, "lane-check: backlog=5", rec,
                                              tpath="/x", logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])
        self._assert_journaled(logs)


# --------------------------------------------------------------------------- #
# The owner's own Discord reply bypasses the switch (user_authored=True).
# --------------------------------------------------------------------------- #
class TestOwnerReplyBypass(unittest.TestCase):
    def _off(self):
        return m.patch.object(wd, "nudges_enabled", lambda *a, **k: False)

    def test_send_verified_user_authored_not_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            # tpath="" -> the body's own no-transcript abort fires (proving the
            # nudges gate was BYPASSED, not the suppression short-circuit).
            ok = wd.send_verified(PID, "owner reply text", rec, tpath="",
                                  logs=logs, user_authored=True)
        self.assertFalse(ok)
        self.assertFalse(any("nudges OFF: suppressed" in ln for ln in logs),
                         "user_authored must NOT be suppressed: %r" % logs)
        self.assertTrue(any("no transcript path" in ln for ln in logs),
                        "expected the body to run past the gate: %r" % logs)

    def test_submit_own_draft_user_authored_not_suppressed(self):
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.submit_own_draft_verified(
                PID, "lane-check: backlog=5", rec, tpath="", logs=logs,
                user_authored=True)
        self.assertFalse(ok)
        self.assertFalse(any("nudges OFF: suppressed" in ln for ln in logs),
                         "user_authored must NOT be suppressed: %r" % logs)


# --------------------------------------------------------------------------- #
# Compact: at OFF the request stays PENDING (never booked delivered).
# --------------------------------------------------------------------------- #
class TestCompactPendingWhenOff(unittest.TestCase):
    def test_compact_submit_verified_returns_pending_word(self):
        import watchdog.compact as compact
        rec = _Recorder()
        logs = []
        with m.patch.object(wd, "nudges_enabled", lambda *a, **k: False):
            out = compact._compact_submit_verified(
                PID, rec, lambda *a, **k: None, logs.append)
        self.assertEqual(out, "nudges-off")
        self.assertEqual(rec.sent_keys(), [])
        self.assertTrue(any("nudges OFF: suppressed" in ln for ln in logs),
                        "compact suppression must journal: %r" % logs)


# --------------------------------------------------------------------------- #
# CLI: marker write/remove + status + --fleet.
# --------------------------------------------------------------------------- #
class TestNudgesCLI(unittest.TestCase):
    def _args(self, **kw):
        import argparse
        ns = argparse.Namespace(nudges_action=None, reason=None, fleet=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_off_writes_marker_on_removes_it(self):
        with TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
                    airuleset.cmd_nudges(self._args(nudges_action="off",
                                                    reason="focus"))
                    self.assertFalse(wd.nudges_enabled(home=home))
                    marker = wd.read_nudges_marker(home=home)
                    self.assertIsInstance(marker, dict)
                    self.assertEqual(marker.get("reason"), "focus")
                    self.assertIn("since", marker)
                    airuleset.cmd_nudges(self._args(nudges_action="on"))
                    self.assertTrue(wd.nudges_enabled(home=home))
                    self.assertIsNone(wd.read_nudges_marker(home=home))

    def test_fleet_skips_paused_and_classifies(self):
        import cli_fleet
        calls = []

        def fake_runner(entry):
            calls.append(entry.get("name"))
            return ("nudges: OFF since ... by ...", 0)

        # one paused entry must be skipped; one unreachable must classify.
        hosts = [
            {"name": "boxA", "host": "h", "user": "u", "repo_path": "~/r"},
            {"name": "simap1", "host": "h", "user": "u", "repo_path": "~/r",
             "paused": "owner: paused"},
            {"name": "boxC", "host": "h", "user": "u", "repo_path": "~/r"},
        ]

        def runner2(entry):
            calls.append(entry.get("name"))
            if entry.get("name") == "boxC":
                return ("", 255)
            return ("nudges: OFF ...", 0)

        with m.patch.object(cli_fleet, "REMOTE_HOSTS", hosts):
            results = airuleset._nudges_fleet("off", runner=runner2)
        names = [r[0] for r in results]
        self.assertIn("boxA", names)
        self.assertIn("boxC", names)
        self.assertNotIn("simap1", names)          # paused skipped
        self.assertNotIn("simap1", calls)          # never ssh'd
        by = dict(results)
        self.assertEqual(by["boxA"], "OFF")
        self.assertEqual(by["boxC"], "unreachable")


# --------------------------------------------------------------------------- #
# Statusline badge: hidden ON, shown OFF.
# --------------------------------------------------------------------------- #
class TestNudgesBadge(unittest.TestCase):
    def test_hidden_when_on_shown_when_off(self):
        with TemporaryDirectory() as home:
            self.assertEqual(statusbar.nudges_off_segment(home=home), "")
            claude = Path(home) / ".claude"
            claude.mkdir(parents=True, exist_ok=True)
            (claude / "nudges-off").write_text(
                '{"since":"x","by":"y","reason":null}', encoding="utf-8")
            seg = statusbar.nudges_off_segment(home=home)
            self.assertIn("nudges OFF", seg)


# --------------------------------------------------------------------------- #
# cmd_status carries a nudges row.
# --------------------------------------------------------------------------- #
class TestStatusRow(unittest.TestCase):
    def test_status_prints_nudges_row(self):
        with TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"HOME": home}):
                with m.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
                    marker = wd.nudges_marker_path(home=home)
                    os.makedirs(os.path.dirname(marker), exist_ok=True)
                    with open(marker, "w", encoding="utf-8") as h:
                        h.write('{"since":"2026-09-11T00:00:00Z",'
                                '"by":"owner","reason":"focus"}')
                    import io
                    import contextlib
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        airuleset.cmd_status(argparse.Namespace(
                            skill_parity=False))
                    out = buf.getvalue()
        self.assertIn("nudges:", out)
        self.assertIn("OFF", out)


# --------------------------------------------------------------------------- #
# Phrase-lock: nudge texts report facts, defer to the session-agreed priority,
# and never prescribe count ("up to 5"/"saturuj"/"čo najviac").
# --------------------------------------------------------------------------- #
class TestNudgeTextPhraseLock(unittest.TestCase):
    def _banned(self, text):
        import re
        return re.search(r"up to 5|saturuj|čo najviac", text, re.I)

    def test_lane_nudge_text_facts_no_count(self):
        from watchdog.lane_resources import _lane_nudge_text
        text = _lane_nudge_text(7, 1, {"total": 5}, usage=None, live_workers=2)
        self.assertIsNone(self._banned(text))
        self.assertNotIn("PARALELNÝCH", text)          # count prescription gone
        self.assertIn("backlog", text)                 # still reports the fact
        self.assertIn("#993", text)                    # agreed-priority reference

    def test_goal_lane_nudge_text_fn_no_count(self):
        import watchdog.goal as goal
        text = goal.GOAL_LANE_NUDGE_TEXT_FN(7, 1)
        self.assertIsNone(self._banned(text))
        self.assertNotIn("PARALELNÝCH", text)

    def test_queue_arrival_nudge_text_no_count(self):
        from watchdog.queue_arrival_recheck import _nudge_text as q_nudge
        text = q_nudge([5177, 5310], 3)
        self.assertIsNone(self._banned(text))
        self.assertIn("#993", text)                    # agreed-priority reference

    def test_ops_wait_nudge_text_no_count(self):
        from watchdog.ops_wait_recheck import _nudge_text as o_nudge
        text = o_nudge(3, [])
        self.assertIsNone(self._banned(text))


if __name__ == "__main__":
    unittest.main()
