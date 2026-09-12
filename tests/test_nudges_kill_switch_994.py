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
import watchdog.goal as goal  # noqa: E402
import statusbar  # noqa: E402
import airuleset  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_DRAFT_CAP, _write_marker_transcript,
    _isolate_goal_state)

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
        # #994 REOPEN — suppression is now observed at the `_type_literal_verified`
        # PRIMITIVE, so the helper must actually REACH it: a real (readable) tpath
        # and a bare box (the _Recorder returns "" for every capture).
        rec = _Recorder()
        logs = []
        with TemporaryDirectory() as d:
            tp = os.path.join(d, "sess.jsonl")
            open(tp, "w", encoding="utf-8").close()
            # A BARE idle box so the pre-send bare/raced checks pass and the
            # ladder reaches the `_type_literal_verified` primitive (the same
            # `_input_line_text == ""` shim the compact test uses).
            with m.patch.object(wd, "_input_line_text", lambda *a, **k: ""), \
                    self._off():
                ok = wd.send_verified(PID, "lane-check: backlog=5", rec,
                                      tpath=tp, logs=logs)
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
        # #994 REOPEN — delegates to `send_verified`, whose primitive suppresses;
        # a real tpath lets it reach that primitive (journal kind is "send").
        rec = _Recorder()
        logs = []
        with TemporaryDirectory() as d:
            tp = os.path.join(d, "sess.jsonl")
            open(tp, "w", encoding="utf-8").close()
            with m.patch.object(wd, "_input_line_text", lambda *a, **k: ""), \
                    self._off():
                ok = wd.send_subagent_nudge(PID, "wid-1", "api-error", rec,
                                            tpath=tp, logs=logs)
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
        # A BARE idle box (`_input_line_text` == "") so the pre-send raced-busy
        # gate passes and the ladder reaches the `send_continue` chokepoint,
        # where the #994 kill switch suppresses the /compact.
        with m.patch.object(wd, "nudges_enabled", lambda *a, **k: False), \
                m.patch.object(wd, "_input_line_text", lambda *a, **k: ""):
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

    def _no_count_prescription(self, text):
        # #994 point 5: no "hold up to N" count prescription. `PARALELNÝMI`
        # (the kept #848 mechanism word) is fine; the banned shapes are the
        # count phrasings the owner reported.
        self.assertIsNone(self._banned(text))
        self.assertNotIn("drž až", text)               # "hold up to N lanes"
        self.assertNotIn("menej než", text)            # cap-count framing

    def test_lane_nudge_text_facts_no_count(self):
        from watchdog.lane_resources import _lane_nudge_text
        text = _lane_nudge_text(7, 1, {"total": 5}, usage=None, live_workers=2)
        self._no_count_prescription(text)
        self.assertIn("backlog", text)                 # still reports the fact
        self.assertIn("#993", text)                    # agreed-priority reference

    def test_goal_lane_nudge_text_fn_no_count(self):
        import watchdog.goal as goal
        text = goal.GOAL_LANE_NUDGE_TEXT_FN(7, 1)
        self._no_count_prescription(text)
        self.assertIn("#993", text)

    def test_queue_arrival_nudge_text_no_count(self):
        from watchdog.queue_arrival_recheck import _nudge_text as q_nudge
        text = q_nudge([5177, 5310], 3)
        self.assertIsNone(self._banned(text))
        self.assertIn("#993", text)                    # agreed-priority reference

    def test_ops_wait_nudge_text_no_count(self):
        from watchdog.ops_wait_recheck import _nudge_text as o_nudge
        text = o_nudge(3, [])
        self.assertIsNone(self._banned(text))


# --------------------------------------------------------------------------- #
# Source lock: the owner-reply bypass may be granted from ONE place only.
# --------------------------------------------------------------------------- #
class TestUserAuthoredCallSiteLock(unittest.TestCase):
    """A HARD-CODED truthy `user_authored=True` bypasses the kill switch, so the
    set of production call sites that GRANT it is a security boundary: it must
    stay exactly {watchdog/discord_replies.py}. #994 REOPEN — the primitive gate
    now lives in `stash.py`, so `deliver_with_stash`/`_type_literal_verified`
    FORWARD the caller's own `user_authored` down as a plumbing kwarg
    (`user_authored=user_authored`, a variable). A forward plumbs the caller's
    EXISTING authorization; it is NOT a new grant. Only a hard-coded truthy
    literal is a grant. A new literal opt-in anywhere but discord_replies.py (a
    machine nudge quietly opting itself back in at OFF) fails this test."""

    @staticmethod
    def _grants_bypass(src):
        """True if any call in `src` HARD-CODES a truthy `user_authored` literal
        (`user_authored=True`). AST-based (not a substring match). A variable /
        attribute forward (`user_authored=user_authored`) is plumbing, not a
        grant, and a `user_authored=False` deny is not a grant."""
        import ast
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "user_authored":
                    continue
                v = kw.value
                # A hard-coded truthy CONSTANT (True / any truthy literal) is a
                # grant. A Constant False, or a Name/expr forward, is not.
                if isinstance(v, ast.Constant) and v.value:
                    return True
        return False

    def test_only_discord_replies_grants_bypass(self):
        grant_files = set()
        for path in REPO.rglob("*.py"):
            rel = path.relative_to(REPO)
            parts = rel.parts
            # Production source only: skip the test suite (behavioural tests here
            # legitimately call helpers with user_authored=True) and vendored/vcs
            # trees.
            if "tests" in parts or rel.name.startswith("test_"):
                continue
            if any(p in (".git", "worktrees", "__pycache__") for p in parts):
                continue
            if self._grants_bypass(path.read_text(encoding="utf-8", errors="ignore")):
                grant_files.add(rel.as_posix())
        self.assertEqual(
            grant_files,
            {"watchdog/discord_replies.py"},
            "user_authored bypass (a truthy/non-False user_authored kwarg) may be "
            "granted from watchdog/discord_replies.py ONLY; found: %r" % sorted(grant_files),
        )


# --------------------------------------------------------------------------- #
# #994 REOPEN — the gate lives at the literal-typing PRIMITIVE, not five helpers.
# The hole: `deliver_goal` -> `deliver_with_stash` -> `stash._type_literal`
# (`tmux send-keys -l`) typed at OFF because the #994 checks were on the FIVE
# tmux_io helpers only, and stash.py's `_type_literal` was misclassified as a
# control-key site. These reproduce the hole (RED) and lock it shut.
# --------------------------------------------------------------------------- #
class TestTypeLiteralPrimitiveGate(unittest.TestCase):
    def _off(self):
        return m.patch.object(wd, "nudges_enabled", lambda *a, **k: False)

    def test_type_literal_types_nothing_at_off(self):
        # The ROOT of the reopen: the single literal-typing primitive itself
        # must type nothing when nudges are OFF (default = machine caller).
        rec = _Recorder()
        with self._off():
            wd._type_literal(PID, rec, "lane-check: backlog=5")
        self.assertEqual(
            rec.sent_keys(), [],
            "the literal-typing primitive TYPED while nudges were OFF: %r"
            % rec.sent_keys())

    def test_type_literal_user_authored_types_at_off(self):
        # The owner's OWN reply (user_authored=True, forwarded from
        # deliver_with_stash/send_verified) BYPASSES the switch: it TYPES at OFF
        # and is never journalled as suppressed.
        rec = _Recorder()
        logs = []
        with self._off():
            r = wd._type_literal(PID, rec, "owner reply text",
                                 user_authored=True, logs=logs)
        self.assertTrue(r)
        self.assertTrue(
            any("-l" in a for a in rec.sent_keys()),
            "user_authored must TYPE at OFF: %r" % rec.sent_keys())
        self.assertFalse(any("nudges OFF: suppressed" in ln for ln in logs))


class TestGoalSweepHoleClosed(unittest.TestCase):
    """The reopen incident end-to-end: goal-sweep's `deliver_goal` draft path
    (-> `deliver_with_stash`) must reach the pane with ZERO keystrokes at OFF and
    journal `nudges OFF: suppressed goal`. Against the pre-fix tree the stash
    `C-s` fires before the abort, so `keys()` is non-empty (RED)."""

    SID = "sess-994-goalsweep"
    CWD = "/home/newlevel/devel/kill994"

    def setUp(self):
        _isolate_goal_state(self)

    def test_deliver_goal_draft_path_suppressed_at_off(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_DRAFT_CAP, model_type=True)
        logs = []
        with m.patch.object(wd, "nudges_enabled", lambda *a, **k: False):
            word = goal.deliver_goal(
                self.SID, self.CWD, "/goal STOP CONDITIONS x", "full",
                run=tmux, projects_dir=proj, sleep_fn=lambda s: None, logs=logs)
        self.assertEqual(
            tmux.keys(), [],
            "keystrokes reached the pane while nudges were OFF: %r" % tmux.keys())
        self.assertTrue(
            any("nudges OFF: suppressed goal" in ln for ln in logs),
            "expected a `nudges OFF: suppressed goal` journal line: %r" % logs)
        self.assertNotEqual(word, "sent")


class TestDeliverWithStashPrimitiveGate(unittest.TestCase):
    def _off(self):
        return m.patch.object(wd, "nudges_enabled", lambda *a, **k: False)

    def test_deliver_with_stash_suppressed_at_off(self):
        # (b) — the stash-around delivery primitive gates at the TOP (before the
        # C-s toggle), so an OFF box receives ZERO keystrokes.
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.deliver_with_stash(PID, "/goal test", rec,
                                       nudge_kind="goal", logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])
        self.assertTrue(
            any("nudges OFF: suppressed goal" in ln for ln in logs),
            "expected `nudges OFF: suppressed goal`: %r" % logs)

    def test_deliver_with_stash_user_authored_bypasses_at_off(self):
        # (c) — the owner's OWN reply (user_authored=True, the discord_replies
        # grant) is NOT suppressed at OFF: it runs past the gate into the body
        # (which then aborts on the empty fake's missing free prompt — proof the
        # gate was bypassed, not the suppression short-circuit).
        rec = _Recorder()
        logs = []
        with self._off():
            ok = wd.deliver_with_stash(PID, "owner reply text", rec,
                                       user_authored=True, logs=logs)
        self.assertFalse(ok)
        self.assertFalse(
            any("nudges OFF: suppressed" in ln for ln in logs),
            "user_authored must NOT be suppressed: %r" % logs)
        self.assertTrue(
            any("stash-abort: no free prompt" in ln for ln in logs),
            "expected the body to run past the gate: %r" % logs)


class TestLiteralTypingPrimitiveLock(unittest.TestCase):
    """Repo-wide structural lock: EVERY `tmux send-keys ... -l` (literal-typing)
    emission in `watchdog/` must live inside the ONE function `_type_literal`, so
    no seventh literal-typing primitive can silently reappear and reopen the hole
    (the #994 root cause). AST-based, not a substring grep."""

    @staticmethod
    def _funcs_emitting_literal_send_keys(path):
        import ast
        names = set()
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))

        def _is_literal_send_keys(node):
            # a list literal whose Constant elements include both "send-keys"
            # and the literal-typing flag "-l"
            if not isinstance(node, ast.List):
                return False
            consts = {e.value for e in node.elts
                      if isinstance(e, ast.Constant)}
            return "send-keys" in consts and "-l" in consts

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(fn):
                if _is_literal_send_keys(sub):
                    names.add(fn.name)
                    break
        return names

    def test_only_type_literal_emits_literal_send_keys(self):
        wd_dir = REPO / "watchdog"
        offenders = {}
        for path in wd_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            fns = self._funcs_emitting_literal_send_keys(path)
            for fn in fns:
                offenders.setdefault(fn, []).append(
                    path.relative_to(REPO).as_posix())
        self.assertEqual(
            set(offenders), {"_type_literal"},
            "every `send-keys ... -l` emission in watchdog/ must live in "
            "`_type_literal`; found emitters: %r" % offenders)


if __name__ == "__main__":
    unittest.main()
