"""Tests for the session heartbeat producer + reader (#486, step G1).

`unittest.TestCase` shape (NOT bare pytest functions) so the real push gate,
`python3 -m unittest discover -s tests`, actually runs them — a bare `def
test_x()` file is silently collected as 0 tests by `unittest discover`
(internals-tests.md, #416). Every test self-isolates (explicit `base_dir=` or a
`mock.patch.dict` on the env), so it needs neither pytest nor conftest.
"""
import io
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import session_status as ss


class _TmpBase(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)


# --------------------------------------------------------------------------- #
# classify_marker — terminal status marker of a turn's final text
# --------------------------------------------------------------------------- #

class TestClassifyMarker(unittest.TestCase):
    def test_basic_markers(self):
        cases = [
            ("nejaký text\n❓ NEEDS YOU: schváliš merge PR #5?", "needs_you"),
            ("bežím\n⏳ WORKING: CI beží, ohlásim sa", "working"),
            ("hotovo\n✅ DONE: nasadené v1.2.3, CI zelené", "done"),
            ("## ✅ Work Complete\n\naudity\n\nPR #5: X\n"
             "https://x/pull/5 — merged abc1234", "done"),
            ("", "unknown"),
            ("Hotovo, commit abc. Nič viac.", "unknown"),
            # an audit `✅ Deploy:` line is NOT a terminal DONE marker
            ("✅ Deploy: overené na dashboarde v1.2.3", "unknown"),
        ]
        for msg, expected in cases:
            with self.subTest(msg=msg):
                self.assertEqual(ss.classify_marker(msg), expected)

    def test_ask_and_continue_dual_marker_is_working(self):
        # ❓ ASKED in the BODY, ⏳ WORKING as the terminal line → the terminal
        # line decides (the ask-and-continue shape the dispatch calls out).
        msg = ("**Otázka — projekt X:** ktorá farba?\n"
               "❓ ASKED: modrá alebo zelená?\n\n"
               "⏳ WORKING: medzitým robím iný ticket")
        self.assertEqual(ss.classify_marker(msg), "working")

    def test_active_question_wins_over_done_heading(self):
        msg = ("## ✅ Work Complete\n\naudity...\n\n"
               "❓ NEEDS YOU: schváliš merge?")
        self.assertEqual(ss.classify_marker(msg), "needs_you")

    def test_marker_only_at_line_start(self):
        # a ❓/⏳ MID-SENTENCE on a ✅ DONE last line is PROSE, not a marker — the
        # marker must be anchored at the line START (#486-review MAJOR).
        cases = [
            ("praca\n✅ DONE: nasadené, opravený Discord ❓ ping funguje", "done"),
            ("praca\n✅ DONE: hotovo, vyriešený ❓ v komentári PR #5", "done"),
            ("praca\n✅ DONE: kanál ⏳ už nebliká", "done"),
            # legitimate leading decorations still count as a marker
            ("praca\n- ⏳ WORKING: bežím", "working"),
            ("praca\n**❓ NEEDS YOU:** rozhodni", "needs_you"),
            ("praca\n> ✅ DONE: hotovo", "done"),
        ]
        for msg, expected in cases:
            with self.subTest(msg=msg):
                self.assertEqual(ss.classify_marker(msg), expected)


# --------------------------------------------------------------------------- #
# build_heartbeat — pure field builder
# --------------------------------------------------------------------------- #

class TestBuildHeartbeat(unittest.TestCase):
    def test_main_stop(self):
        payload = {"session_id": "sess-1", "cwd": "/repo",
                   "last_assistant_message": "x\n⏳ WORKING: y"}
        d = ss.build_heartbeat(payload, "stop", goal_armed=True, now=1000)
        self.assertEqual(d["schema"], ss.SCHEMA_VERSION)
        self.assertEqual(d["sid"], "sess-1")
        self.assertEqual(d["kind"], "main")
        self.assertEqual(d["last_turn"], "stop")
        self.assertEqual(d["marker"], "working")
        self.assertIs(d["goal_armed"], True)
        self.assertEqual(d["cwd"], "/repo")
        self.assertEqual(d["ts"], 1000)
        self.assertNotIn("agent_id", d)
        self.assertIn("_note", d)

    def test_subagent_marks_kind_and_agent(self):
        payload = {"session_id": "sess-parent", "agent_id": "agent-9",
                   "agent_type": "autopilot-worker", "cwd": "/wt",
                   "last_assistant_message": "evidence block"}
        d = ss.build_heartbeat(payload, "subagent_stop", now=5)
        self.assertEqual(d["kind"], "subagent")
        self.assertEqual(d["sid"], "sess-parent")
        self.assertEqual(d["agent_id"], "agent-9")
        self.assertEqual(d["agent_type"], "autopilot-worker")
        self.assertIsNone(d["goal_armed"])
        self.assertEqual(d["last_turn"], "subagent_stop")

    def test_session_start(self):
        d = ss.build_heartbeat({"session_id": "s", "cwd": "/r"},
                               "session_start", now=1)
        self.assertEqual(d["kind"], "main")
        self.assertEqual(d["last_turn"], "session_start")
        self.assertEqual(d["marker"], "unknown")
        self.assertIsNone(d["goal_armed"])


# --------------------------------------------------------------------------- #
# status_path — filename convention + defang
# --------------------------------------------------------------------------- #

class TestStatusPath(_TmpBase):
    def test_main_and_subagent(self):
        m = ss.status_path("abc-123", None, self.tmp)
        s = ss.status_path("abc-123", "agent-x", self.tmp)
        self.assertEqual(m, self.tmp / "abc-123.json")
        self.assertEqual(s, self.tmp / "abc-123__agent-x.json")
        self.assertNotEqual(m, s)  # collision-free

    def test_defangs_ids(self):
        p = ss.status_path("../evil/../id", "a/b;rm", self.tmp)
        self.assertEqual(p.parent, self.tmp)
        self.assertEqual(p.name, "evilid__abrm.json")


# --------------------------------------------------------------------------- #
# write_heartbeat — atomic write + subagent collision-safety (#486 b/c)
# --------------------------------------------------------------------------- #

class TestWriteHeartbeat(_TmpBase):
    def test_atomic_no_tmp_leftover(self):
        payload = {"session_id": "s1", "cwd": "/r",
                   "last_assistant_message": "x\n✅ DONE: ok"}
        p = ss.write_heartbeat(payload, "stop", base_dir=self.tmp, now=42)
        self.assertEqual(p, self.tmp / "s1.json")
        d = json.loads(p.read_text())
        self.assertEqual(d["marker"], "done")
        self.assertEqual(d["kind"], "main")
        self.assertEqual(list(self.tmp.glob("*.tmp.*")), [])  # no temp leftover

    def test_subagent_does_not_clobber_main(self):
        # THE point-(b) guarantee: a subagent SHARES the parent's sid but must
        # never overwrite the main session's file.
        ss.write_heartbeat({"session_id": "sid-shared", "cwd": "/main",
                            "last_assistant_message": "m\n⏳ WORKING: z"},
                           "stop", base_dir=self.tmp, now=1)
        ss.write_heartbeat({"session_id": "sid-shared", "agent_id": "wkr-1",
                            "agent_type": "autopilot-worker", "cwd": "/wt",
                            "last_assistant_message": "done"},
                           "subagent_stop", base_dir=self.tmp, now=2)
        main = json.loads((self.tmp / "sid-shared.json").read_text())
        sub = json.loads((self.tmp / "sid-shared__wkr-1.json").read_text())
        self.assertEqual(main["kind"], "main")
        self.assertEqual(main["cwd"], "/main")
        self.assertEqual(sub["kind"], "subagent")
        self.assertEqual(sub["agent_id"], "wkr-1")

    def test_subagent_missing_agent_id_never_clobbers_main(self):
        ss.write_heartbeat({"session_id": "sid-x", "cwd": "/main",
                            "last_assistant_message": "m\n⏳ WORKING: z"},
                           "stop", base_dir=self.tmp, now=1)
        p = ss.write_heartbeat({"session_id": "sid-x", "cwd": "/wt",
                                "last_assistant_message": "done"},
                               "subagent_stop", base_dir=self.tmp, now=2)
        self.assertEqual(p, self.tmp / "sid-x__unknown.json")
        main = json.loads((self.tmp / "sid-x.json").read_text())
        self.assertEqual(main["kind"], "main")  # NOT clobbered
        self.assertEqual(main["cwd"], "/main")

    def test_stop_reads_goal_armed(self):
        tp = _write_goal_transcript(self.tmp, "Goal set: /goal loop")
        payload = {"session_id": "sg", "cwd": "/r", "transcript_path": str(tp),
                   "last_assistant_message": "x\n⏳ WORKING: y"}
        p = ss.write_heartbeat(payload, "stop", base_dir=self.tmp, now=7)
        self.assertIs(json.loads(p.read_text())["goal_armed"], True)


# --------------------------------------------------------------------------- #
# goal_armed_from_transcript — REAL integration with canonical scan_goal_markers
# --------------------------------------------------------------------------- #

def _write_goal_transcript(tmp, *marks):
    """A real transcript whose entries carry `<local-command-stdout>Goal
    set:/cleared: ...</local-command-stdout>` (the exact shape the canonical
    scanner reads — a top-level `system` entry with a plain-string content)."""
    p = Path(tmp) / "t.jsonl"
    lines = []
    for i, mark in enumerate(marks):
        iso = datetime.fromtimestamp(100 + i, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        wrapped = "<local-command-stdout>%s</local-command-stdout>" % mark
        lines.append(json.dumps({"type": "system", "subtype": "local_command",
                                 "timestamp": iso, "content": wrapped}))
    p.write_text(("\n".join(lines) + "\n") if lines else "")
    return p


class TestGoalArmed(_TmpBase):
    def test_true_on_set(self):
        p = _write_goal_transcript(self.tmp, "Goal set: /goal do X")
        self.assertIs(ss.goal_armed_from_transcript(str(p)), True)

    def test_false_when_cleared_last(self):
        p = _write_goal_transcript(self.tmp,
                                   "Goal set: /goal do X", "Goal cleared: done")
        self.assertIs(ss.goal_armed_from_transcript(str(p)), False)

    def test_false_no_marker(self):
        p = self.tmp / "plain.jsonl"
        p.write_text(json.dumps({"type": "assistant",
                                 "message": {"content": "hi"}}) + "\n")
        self.assertIs(ss.goal_armed_from_transcript(str(p)), False)

    def test_none_when_no_path(self):
        self.assertIsNone(ss.goal_armed_from_transcript(None))
        self.assertIsNone(ss.goal_armed_from_transcript(""))


# --------------------------------------------------------------------------- #
# read_status — fresh / stale / corrupt / absent
# --------------------------------------------------------------------------- #

class TestReadStatus(_TmpBase):
    def test_absent(self):
        v = ss.read_status(sid="nope", base_dir=self.tmp)
        self.assertEqual(v.state, "absent")
        self.assertIsNone(v.data)

    def test_fresh(self):
        ss.write_heartbeat({"session_id": "s", "cwd": "/r",
                            "last_assistant_message": "x\n❓ NEEDS YOU: q"},
                           "stop", base_dir=self.tmp, now=time.time())
        v = ss.read_status(sid="s", base_dir=self.tmp, stale_after_s=300)
        self.assertEqual(v.state, "fresh")
        self.assertEqual(v.sid, "s")
        self.assertEqual(v.kind, "main")
        self.assertEqual(v.marker, "needs_you")
        self.assertEqual(v.cwd, "/r")

    def test_stale(self):
        p = ss.write_heartbeat({"session_id": "s", "cwd": "/r"},
                               "stop", base_dir=self.tmp)
        old = time.time() - 10_000
        os.utime(p, (old, old))
        v = ss.read_status(sid="s", base_dir=self.tmp, stale_after_s=300)
        self.assertEqual(v.state, "stale")
        self.assertGreater(v.age_s, 300)

    def test_corrupt_logs_loudly(self):
        p = self.tmp / "bad.json"
        p.write_text("{ this is not json")
        warnings = []
        v = ss.read_status(path=p, on_warn=warnings.append)
        self.assertEqual(v.state, "corrupt")
        self.assertIsNone(v.data)
        self.assertTrue(warnings and "corrupt" in warnings[0])

    def test_corrupt_non_dict(self):
        p = self.tmp / "arr.json"
        p.write_text("[1,2,3]")
        v = ss.read_status(path=p, on_warn=lambda m: None)
        self.assertEqual(v.state, "corrupt")

    def test_never_raises_on_truncated(self):
        p = self.tmp / "half.json"
        p.write_text('{"schema":1,"sid":"s"')  # truncated mid-write
        v = ss.read_status(path=p, on_warn=lambda m: None)
        self.assertEqual(v.state, "corrupt")


# --------------------------------------------------------------------------- #
# env override + CLI main
# --------------------------------------------------------------------------- #

class TestEnvAndCli(_TmpBase):
    def test_status_dir_env_override(self):
        with mock.patch.dict(os.environ,
                             {"AIRULESET_SESSION_STATUS_DIR": str(self.tmp)}):
            self.assertEqual(ss.status_dir(), self.tmp)
            self.assertEqual(ss.status_path("x"), self.tmp / "x.json")

    def test_main_writes_from_stdin(self):
        payload = json.dumps({"session_id": "cli-1", "cwd": "/r",
                              "last_assistant_message": "x\n✅ DONE: ok"})
        with mock.patch.dict(os.environ,
                             {"AIRULESET_SESSION_STATUS_DIR": str(self.tmp)}), \
                mock.patch.object(sys, "stdin", io.StringIO(payload)):
            rc = ss.main(["--event", "stop"])
        self.assertEqual(rc, 0)
        d = json.loads((self.tmp / "cli-1.json").read_text())
        self.assertEqual(d["marker"], "done")
        self.assertEqual(d["kind"], "main")

    def test_main_tolerates_garbage_stdin(self):
        with mock.patch.dict(os.environ,
                             {"AIRULESET_SESSION_STATUS_DIR": str(self.tmp)}), \
                mock.patch.object(sys, "stdin", io.StringIO("not json at all")):
            rc = ss.main(["--event", "subagent_stop"])
        self.assertEqual(rc, 0)  # never raises


if __name__ == "__main__":
    unittest.main()
