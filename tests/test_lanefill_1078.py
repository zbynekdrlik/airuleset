"""#1078 item 1 — the Stop-time LANE-FILL gate (gates.lanefill +
hooks/stop-check-lane-fill.sh).

A goal-armed PARALLEL-mode session must not end a `⏳ WORKING` / `✅ DONE` turn
with MORE dispatchable tickets than live lanes while a lane slot is free, unless
the final message carries a `Dependency: #N` / `Lane-fill: <reason>` line.
Sequential boxes are EXEMPT; every read error fails OPEN with a journal line.
Owner directive montalu1 2026-09-18 (3×): "I 19 a len jeden subagent pracuje!".

Covers:
  * decide() — the pure truth table (all acceptance fixtures).
  * _last_marker / has_justification — the message readers.
  * run() — the I/O shell with injected seams (block/allow/compose/fail-open).
  * python3 -m gates.lanefill — end-to-end via stdin, hermetic (fake transcript,
    fake mode/cap via .claude/lane-resources.json, fake quals via env, NO gh).
  * hooks/stop-check-lane-fill.sh — the thin-adapter wiring + cheap pre-check.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import gates.lanefill as lf  # noqa: E402


WORKING = "…work in progress\n⏳ WORKING: still going"
DONE = "…\n✅ DONE: shipped it"


# --------------------------------------------------------------------------- #
# decide() — the pure truth table.
# --------------------------------------------------------------------------- #
class TestDecide(unittest.TestCase):
    def test_armed_parallel_working_underfilled_blocks(self):
        block, reason = lf.decide(6, 1, 4, "parallel", True, "working", False)
        self.assertTrue(block)
        self.assertIn("Lane-fill:", reason)
        self.assertIn("Dependency:", reason)

    def test_done_marker_blocks_same_as_working(self):
        block, _ = lf.decide(6, 1, 4, "parallel", True, "done", False)
        self.assertTrue(block)

    def test_justification_present_allows(self):
        block, _ = lf.decide(6, 1, 4, "parallel", True, "working", True)
        self.assertFalse(block)

    def test_sequential_mode_is_exempt(self):
        block, _ = lf.decide(6, 1, 4, "sequential", True, "working", False)
        self.assertFalse(block)

    def test_not_goal_armed_allows(self):
        block, _ = lf.decide(6, 1, 4, "parallel", False, "working", False)
        self.assertFalse(block)

    def test_no_terminal_marker_allows(self):
        block, _ = lf.decide(6, 1, 4, "parallel", True, None, False)
        self.assertFalse(block)

    def test_live_equals_cap_allows(self):
        block, _ = lf.decide(6, 4, 4, "parallel", True, "working", False)
        self.assertFalse(block)

    def test_dispatchable_not_greater_than_live_allows(self):
        block, _ = lf.decide(1, 1, 4, "parallel", True, "working", False)
        self.assertFalse(block)

    def test_zero_live_underfilled_blocks(self):
        block, _ = lf.decide(6, 0, 4, "parallel", True, "working", False)
        self.assertTrue(block)


# --------------------------------------------------------------------------- #
# _last_marker / has_justification
# --------------------------------------------------------------------------- #
class TestReaders(unittest.TestCase):
    def test_last_line_working(self):
        self.assertEqual(lf._last_marker("a\nb\n⏳ WORKING: x"), "working")

    def test_last_line_done(self):
        self.assertEqual(lf._last_marker("a\n✅ DONE: x"), "done")

    def test_mid_message_done_not_on_last_line_is_none(self):
        # a ✅ row mid-turn with a non-marker tail line must NOT count.
        self.assertIsNone(lf._last_marker("✅ #1 merged\nnow doing the next thing"))

    def test_question_turn_is_none(self):
        self.assertIsNone(lf._last_marker("stuff\n❓ NEEDS YOU: pick one?"))

    def test_dependency_line_is_justification(self):
        self.assertTrue(lf.has_justification("blah\nDependency: #42 waits on it\n⏳ WORKING"))

    def test_lanefill_line_is_justification(self):
        self.assertTrue(lf.has_justification("Lane-fill: all remaining wait on gk review\n✅ DONE"))

    def test_no_justification(self):
        self.assertFalse(lf.has_justification("just a normal turn\n⏳ WORKING"))

    def test_bare_lanefill_word_is_not_justification(self):
        self.assertFalse(lf.has_justification("the lane-fill gate fired\n⏳ WORKING"))


# --------------------------------------------------------------------------- #
# run() — the I/O shell with injected seams.
# --------------------------------------------------------------------------- #
def _payload(msg, cwd="/repo", sid="s1", transcript="/t.jsonl"):
    return json.dumps({"last_assistant_message": msg, "cwd": cwd,
                       "session_id": sid, "transcript_path": transcript})


def _fakes(count=6, live=1, cap=4, mode="parallel", armed=True):
    tickets = [(100 + i, "ticket %d" % (100 + i)) for i in range(count)]
    return dict(
        goal_fn=lambda p: armed,
        mode_fn=lambda c: mode,
        cap_fn=lambda c: cap,
        live_fn=lambda p, c: live,
        quals_fn=lambda p, c: (count, tickets),
    )


class TestRunShell(unittest.TestCase):
    def _run(self, msg, **fakes):
        buf = io.StringIO()
        code = None
        with contextlib.redirect_stderr(buf):
            try:
                lf.run(_payload(msg), **fakes)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_underfilled_blocks_and_names_five(self):
        code, err = self._run(WORKING, **_fakes(count=6, live=1, cap=4))
        self.assertEqual(code, 2)
        self.assertIn("LANE-FILL:", err)
        for n in range(100, 105):
            self.assertIn("#%d" % n, err)
        self.assertNotIn("#105", err)          # only five named
        self.assertIn("a ďalších 1", err)
        self.assertIn("Dependency:", err)
        self.assertIn("Lane-fill:", err)

    def test_sequential_allows_without_quals_read(self):
        called = {"quals": False}

        def quals(p, c):
            called["quals"] = True
            return (6, [])
        code, _ = self._run(WORKING, goal_fn=lambda p: True,
                            mode_fn=lambda c: "sequential",
                            cap_fn=lambda c: 4, live_fn=lambda p, c: 1,
                            quals_fn=quals)
        self.assertIsNone(code)
        self.assertFalse(called["quals"])       # exempt before the expensive read

    def test_not_armed_allows_without_mode_read(self):
        called = {"mode": False}

        def mode(c):
            called["mode"] = True
            return "parallel"
        code, _ = self._run(WORKING, goal_fn=lambda p: False, mode_fn=mode,
                            cap_fn=lambda c: 4, live_fn=lambda p, c: 1,
                            quals_fn=lambda p, c: (6, []))
        self.assertIsNone(code)
        self.assertFalse(called["mode"])

    def test_justification_allows_without_any_read(self):
        called = {"goal": False}

        def goal(p):
            called["goal"] = True
            return True
        code, _ = self._run("Lane-fill: bounce lane holds the box\n⏳ WORKING",
                            goal_fn=goal, **{k: v for k, v in _fakes().items()
                                             if k != "goal_fn"})
        self.assertIsNone(code)
        self.assertFalse(called["goal"])        # cheap justification check first

    def test_non_terminal_turn_allows_without_reads(self):
        code, _ = self._run("just a question\n❓ NEEDS YOU: pick?", **_fakes())
        self.assertIsNone(code)

    def test_live_equals_cap_allows(self):
        code, _ = self._run(WORKING, **_fakes(count=6, live=4, cap=4))
        self.assertIsNone(code)

    def test_quals_unreadable_fails_open_with_journal(self):
        def quals(p, c):
            raise lf._Unreadable("meta read failed")
        fakes = _fakes()
        fakes["quals_fn"] = quals
        code, err = self._run(WORKING, **fakes)
        self.assertIsNone(code)
        self.assertIn("lane-fill:", err)
        self.assertIn("not enforced", err)

    def test_mode_unreadable_fails_open_with_journal(self):
        def mode(c):
            raise RuntimeError("boom")
        fakes = _fakes()
        fakes["mode_fn"] = mode
        code, err = self._run(WORKING, **fakes)
        self.assertIsNone(code)
        self.assertIn("not enforced", err)


# --------------------------------------------------------------------------- #
# Hermetic subprocess + hook wiring.
# --------------------------------------------------------------------------- #
def _fake_quals_lines(n, start=101):
    return "\n".join("%d\tticket %d" % (i, i) for i in range(start, start + n))


class _Env:
    """A hermetic box: temp HOME + a project cwd with .claude/lane-resources.json,
    a transcript carrying a `/goal` marker, and env for the fake quals seam."""

    def __init__(self, mode="parallel", max_lanes=4, goal="set",
                 quals="@LINES", live_workers=0):
        self.tmp = tempfile.mkdtemp(prefix="lf-")
        self.home = os.path.join(self.tmp, "home")
        self.cwd = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.cwd, ".claude"), exist_ok=True)
        os.makedirs(self.home, exist_ok=True)
        lr = {"mode": mode, "max_lanes": max_lanes}
        Path(self.cwd, ".claude", "lane-resources.json").write_text(json.dumps(lr))
        self.sid = "sess-1078"
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        if goal in ("set", "cleared"):
            line = {"type": "user",
                    "message": {"content":
                                "<local-command-stdout>Goal %s: work the backlog"
                                "</local-command-stdout>" % goal},
                    "timestamp": "2026-09-19T09:00:00Z"}
            Path(self.transcript).write_text(json.dumps(line) + "\n")
        else:
            Path(self.transcript).write_text('{"type":"user","message":{"content":"hi"}}\n')
        self.quals = quals
        for _i in range(live_workers):
            self._add_live_worker(_i)

    def _add_live_worker(self, i):
        import watchdog.transcripts as tr
        d = (Path(self.home) / ".claude" / "projects"
             / tr.encode_project_dir(self.cwd) / self.sid / "subagents")
        d.mkdir(parents=True, exist_ok=True)
        p = d / ("agent-w%d.jsonl" % i)
        # a RUNNING worker: last real turn is a tool_use (not finished/wedged).
        p.write_text(json.dumps({"type": "assistant", "message": {
            "role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}}) + "\n")
        now = time.time()
        os.utime(p, (now, now))

    def payload(self, msg):
        return json.dumps({"last_assistant_message": msg, "cwd": self.cwd,
                           "session_id": self.sid,
                           "transcript_path": self.transcript})

    def env(self):
        e = dict(os.environ)
        e["HOME"] = self.home
        e["PYTHONPATH"] = str(REPO) + ":" + e.get("PYTHONPATH", "")
        if self.quals == "@LINES":
            e["AIRULESET_LANEFILL_FAKE_QUALS"] = _fake_quals_lines(6)
        elif self.quals is not None:
            e["AIRULESET_LANEFILL_FAKE_QUALS"] = self.quals
        return e

    def cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestModuleSubprocess(unittest.TestCase):
    def _run(self, box, msg):
        return subprocess.run([sys.executable, "-m", "gates.lanefill"],
                              input=box.payload(msg), capture_output=True,
                              text=True, timeout=45, env=box.env())

    def test_armed_parallel_underfilled_blocks_naming_five(self):
        box = _Env(mode="parallel", max_lanes=4, goal="set")
        try:
            r = self._run(box, WORKING)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 2)
        for i in range(101, 106):
            self.assertIn("#%d" % i, r.stderr)
        self.assertNotIn("#106", r.stderr)      # only five of the six named
        self.assertIn("a ďalších 1", r.stderr)

    def test_sequential_box_is_exempt_exit_0(self):
        box = _Env(mode="sequential", goal="set")
        try:
            r = self._run(box, WORKING)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)

    def test_lanefill_justification_allows_exit_0(self):
        box = _Env(mode="parallel", goal="set")
        try:
            r = self._run(box, "Lane-fill: all remaining wait on gk review\n⏳ WORKING")
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)

    def test_goal_not_armed_allows_exit_0(self):
        box = _Env(mode="parallel", goal="cleared")
        try:
            r = self._run(box, WORKING)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)

    def test_quals_unmeasurable_fails_open_with_journal(self):
        box = _Env(mode="parallel", goal="set", quals="unmeasurable:meta read failed")
        try:
            r = self._run(box, WORKING)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)
        self.assertIn("lane-fill:", r.stderr)
        self.assertIn("not enforced", r.stderr)

    def test_one_live_lane_at_cap_one_allows(self):
        # a real count_live_workers read: 1 live worker, cap 1 -> 1 < 1 false -> allow.
        box = _Env(mode="parallel", max_lanes=1, goal="set", live_workers=1)
        try:
            r = self._run(box, WORKING)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)


class TestHookAdapter(unittest.TestCase):
    HOOK = REPO / "hooks" / "stop-check-lane-fill.sh"

    def _run(self, box, msg):
        return subprocess.run(["bash", str(self.HOOK)], input=box.payload(msg),
                              capture_output=True, text=True, timeout=45,
                              env=box.env())

    def test_hook_blocks_underfilled_turn(self):
        box = _Env(mode="parallel", max_lanes=4, goal="set")
        try:
            r = self._run(box, WORKING)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 2)
        self.assertIn("LANE-FILL:", r.stderr)

    def test_hook_skips_non_terminal_turn_exit_0(self):
        box = _Env(mode="parallel", goal="set")
        try:
            r = self._run(box, "a status update\n❓ NEEDS YOU: pick?")
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)

    def test_hook_allows_sequential_exit_0(self):
        box = _Env(mode="sequential", goal="set")
        try:
            r = self._run(box, DONE)
        finally:
            box.cleanup()
        self.assertEqual(r.returncode, 0)


class TestGoalArmedDeepScan(unittest.TestCase):
    """#1078 review (both reviewers, 🟡): `_goal_armed` must use `seed_goal_marker`
    (BACKWARD block scan to 32 MB), NOT `scan_goal_markers(off=None)` (last-4 MB
    tail only) — a long autopilot session whose `/goal` arm marker scrolled >4 MB
    back with no newer marker would otherwise read as not-armed and the gate would
    silently never fire on exactly the long-grinding sessions it targets."""

    def _transcript(self, tmp, arm_line, pad_bytes):
        p = os.path.join(tmp, "t.jsonl")
        pad = json.dumps({"type": "user",
                          "message": {"content": "x" * 200}}) + "\n"
        with open(p, "w") as f:
            f.write(arm_line + "\n")
            written = 0
            while written < pad_bytes:
                f.write(pad)
                written += len(pad)
        return p

    def _armset(self):
        return json.dumps({"type": "user", "message": {"content":
                          "<local-command-stdout>Goal set: work the backlog"
                          "</local-command-stdout>"},
                          "timestamp": "2026-09-19T09:00:00Z"})

    def test_deep_arm_past_4mb_tail_still_reads_armed(self):
        import tempfile
        import watchdog.goal_scan as gs
        with tempfile.TemporaryDirectory() as tmp:
            # arm at the TOP, then >4 MB of non-marker padding after it.
            p = self._transcript(tmp, self._armset(), pad_bytes=5 * 1024 * 1024)
            payload = json.dumps({"transcript_path": p})
            # the OLD tail-only reader misses it (proves the bug the fix closes):
            _off, tail_mark = gs.scan_goal_markers(p)
            self.assertIsNone(tail_mark)
            # the fix reads it as armed:
            self.assertTrue(lf._goal_armed(payload))

    def test_cleared_deep_marker_reads_not_armed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cleared = json.dumps({"type": "user", "message": {"content":
                                 "<local-command-stdout>Goal cleared: done"
                                 "</local-command-stdout>"},
                                 "timestamp": "2026-09-19T09:00:00Z"})
            p = self._transcript(tmp, cleared, pad_bytes=5 * 1024 * 1024)
            self.assertFalse(lf._goal_armed(json.dumps({"transcript_path": p})))

    def test_unknown_past_cap_raises_unreadable_for_journal(self):
        import unittest.mock as m
        with m.patch.object(lf, "_goal_armed",
                            side_effect=lf._Unreadable("goal-armed unknown-past-cap")):
            buf = io.StringIO()
            code = None
            with contextlib.redirect_stderr(buf):
                try:
                    lf.run(_payload(WORKING), goal_fn=lf._goal_armed,
                           mode_fn=lambda c: "parallel", cap_fn=lambda c: 4,
                           live_fn=lambda p, c: 1, quals_fn=lambda p, c: (6, []))
                except SystemExit as e:
                    code = e.code
            self.assertIsNone(code)                      # fail-open (allow)
            self.assertIn("not enforced", buf.getvalue())
            self.assertIn("unknown-past-cap", buf.getvalue())

    def test_missing_transcript_reads_not_armed(self):
        self.assertFalse(lf._goal_armed(json.dumps({})))


class TestListDispatchableEmitter(unittest.TestCase):
    """#1078 review (both reviewers, 🟡): the real `_emit_list_dispatchable`
    path is only exercised via the env seam in the lanefill tests; lock its
    shape / oldest-first ordering / dep-wait exclusion / unmeasurable head, and
    the load-bearing NO-SKEW invariant (line count == `--count-dispatchable`)."""

    def setUp(self):
        import cli_quals_cmd
        self.cqc = cli_quals_cmd
        self.rows = {5: {"title": "alpha", "createdAt": "2026-01-02"},
                     3: {"title": "beta", "createdAt": "2026-01-01"},
                     9: {"title": "gamma", "createdAt": "2026-01-03"}}

    def _emit(self, fn, dep_map, disp):
        import unittest.mock as m
        import airuleset
        buf = io.StringIO()
        with m.patch.object(self.cqc, "_dep_wait_map_for",
                            return_value=(dep_map, "montalu", True)), \
             m.patch.object(airuleset, "dispatchable_numbers",
                            return_value=(disp, None)), \
             contextlib.redirect_stdout(buf):
            fn(self.rows, "/repo")
        return buf.getvalue()

    def test_list_is_number_tab_title_oldest_first(self):
        out = self._emit(self.cqc._emit_list_dispatchable, {}, {3, 5, 9})
        self.assertEqual(out, "3\tbeta\n5\talpha\n9\tgamma\n")

    def test_dep_wait_member_is_excluded(self):
        # 5 is dep-wait (in dep_map) -> not dispatchable -> not listed.
        out = self._emit(self.cqc._emit_list_dispatchable, {5: ["#1"]}, {3, 9})
        self.assertEqual(out, "3\tbeta\n9\tgamma\n")

    def test_no_skew_line_count_equals_count_dispatchable(self):
        # The whole gate rests on: len(--list-dispatchable lines) == --count.
        disp = {3, 9}
        list_out = self._emit(self.cqc._emit_list_dispatchable, {5: ["#1"]}, disp)
        count_out = self._emit(self.cqc._emit_count_dispatchable, {5: ["#1"]}, disp)
        n_lines = len([ln for ln in list_out.splitlines() if ln.strip()])
        self.assertEqual(str(n_lines), count_out.splitlines()[0])

    def test_unmeasurable_head_passthrough(self):
        import unittest.mock as m
        buf = io.StringIO()
        with m.patch.object(self.cqc, "_dep_wait_map_for",
                            return_value=({}, None, False)), \
             contextlib.redirect_stdout(buf):
            self.cqc._emit_list_dispatchable(self.rows, "/repo")
        self.assertEqual(buf.getvalue().strip(), "unmeasurable:meta read failed")


class TestListDispatchableIsTrueGuard(unittest.TestCase):
    """#1078 review (reviewer A, 🟡): the #1036 Mock-truthy `is True` guard —
    a `--list` invocation (Mock args with no `list_dispatchable`) must NOT fire
    `_emit_list_dispatchable`; it must print the ordinary `--list` rows."""

    def _gh(self, *a, **k):
        args = [str(x) for x in a]
        if args and args[0] == "label":
            return '[{"name": "stream:montalu"}]'
        if "--search" in args:
            return json.dumps([{"number": 7, "title": "mine",
                                "createdAt": "2026-07-01T00:00:00Z",
                                "labels": [{"name": "stream:montalu"}]}])
        return "[]"

    def test_plain_list_does_not_fire_list_dispatchable(self):
        import airuleset
        from authority_testlib import _drive
        out, _err, _exc = _drive(airuleset.cmd_slice_quals, self._gh,
                                 authority="branch-merge", user="montalu1",
                                 login="zbynekdrlik", count=False, list=True)
        row = [ln for ln in out.splitlines() if ln.startswith("7\t")]
        self.assertTrue(row, out)
        # --list emits number<TAB>createdAt<TAB>action<TAB>title (>=3 tabs);
        # --list-dispatchable would emit number<TAB>title (1 tab). The guard
        # keeps the former (the #1036 trap would produce the latter).
        self.assertGreaterEqual(row[0].count("\t"), 3)


if __name__ == "__main__":
    unittest.main()
