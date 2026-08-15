"""Tests for the session heartbeat producer + reader (#486, step G1).

RED→GREEN: these assert the producer (marker extraction, field building,
atomic write, subagent collision-safety, real goal-armed integration) and the
reader (fresh / stale / corrupt / absent verdicts) BEFORE `watchdog/
session_status.py` exists.
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from watchdog import session_status as ss


# --------------------------------------------------------------------------- #
# classify_marker — terminal status marker of a turn's final text
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("msg,expected", [
    ("nejaký text\n❓ NEEDS YOU: schváliš merge PR #5?", "needs_you"),
    ("bežím\n⏳ WORKING: CI beží, ohlásim sa", "working"),
    ("hotovo\n✅ DONE: nasadené v1.2.3, CI zelené", "done"),
    ("## ✅ Work Complete\n\naudity\n\nPR #5: X\nhttps://x/pull/5 — merged abc1234",
     "done"),
    ("", "unknown"),
    ("Hotovo, commit abc. Nič viac.", "unknown"),
    # an audit `✅ Deploy:` line is NOT a terminal DONE marker
    ("✅ Deploy: overené na dashboarde v1.2.3", "unknown"),
])
def test_classify_marker(msg, expected):
    assert ss.classify_marker(msg) == expected


def test_ask_and_continue_dual_marker_is_working():
    # ❓ ASKED in the BODY, ⏳ WORKING as the terminal line → the terminal
    # line decides (the ask-and-continue shape the dispatch calls out).
    msg = ("**Otázka — projekt X:** ktorá farba?\n"
           "❓ ASKED: modrá alebo zelená?\n\n"
           "⏳ WORKING: medzitým robím iný ticket")
    assert ss.classify_marker(msg) == "working"


def test_active_question_wins_over_done_heading():
    msg = ("## ✅ Work Complete\n\naudity...\n\n"
           "❓ NEEDS YOU: schváliš merge?")
    assert ss.classify_marker(msg) == "needs_you"


# --------------------------------------------------------------------------- #
# build_heartbeat — pure field builder
# --------------------------------------------------------------------------- #

def test_build_heartbeat_main_stop():
    payload = {"session_id": "sess-1", "cwd": "/repo",
               "last_assistant_message": "x\n⏳ WORKING: y"}
    d = ss.build_heartbeat(payload, "stop", goal_armed=True, now=1000)
    assert d["schema"] == ss.SCHEMA_VERSION
    assert d["sid"] == "sess-1"
    assert d["kind"] == "main"
    assert d["last_turn"] == "stop"
    assert d["marker"] == "working"
    assert d["goal_armed"] is True
    assert d["cwd"] == "/repo"
    assert d["ts"] == 1000
    assert "agent_id" not in d
    assert "_note" in d


def test_build_heartbeat_subagent_marks_kind_and_agent():
    payload = {"session_id": "sess-parent", "agent_id": "agent-9",
               "agent_type": "autopilot-worker", "cwd": "/wt",
               "last_assistant_message": "evidence block"}
    d = ss.build_heartbeat(payload, "subagent_stop", now=5)
    assert d["kind"] == "subagent"
    assert d["sid"] == "sess-parent"
    assert d["agent_id"] == "agent-9"
    assert d["agent_type"] == "autopilot-worker"
    assert d["goal_armed"] is None
    assert d["last_turn"] == "subagent_stop"


def test_build_heartbeat_session_start():
    d = ss.build_heartbeat({"session_id": "s", "cwd": "/r"},
                           "session_start", now=1)
    assert d["kind"] == "main"
    assert d["last_turn"] == "session_start"
    assert d["marker"] == "unknown"
    assert d["goal_armed"] is None


# --------------------------------------------------------------------------- #
# status_path — filename convention + defang
# --------------------------------------------------------------------------- #

def test_status_path_main_and_subagent(tmp_path):
    m = ss.status_path("abc-123", None, tmp_path)
    s = ss.status_path("abc-123", "agent-x", tmp_path)
    assert m == tmp_path / "abc-123.json"
    assert s == tmp_path / "abc-123__agent-x.json"
    assert m != s  # collision-free


def test_status_path_defangs_ids(tmp_path):
    p = ss.status_path("../evil/../id", "a/b;rm", tmp_path)
    assert p.parent == tmp_path
    assert p.name == "evilid__abrm.json"


# --------------------------------------------------------------------------- #
# write_heartbeat — atomic write + subagent collision-safety (#486 point b/c)
# --------------------------------------------------------------------------- #

def test_write_heartbeat_atomic_no_tmp_leftover(tmp_path):
    payload = {"session_id": "s1", "cwd": "/r",
               "last_assistant_message": "x\n✅ DONE: ok"}
    p = ss.write_heartbeat(payload, "stop", base_dir=tmp_path, now=42)
    assert p == tmp_path / "s1.json"
    d = json.loads(p.read_text())
    assert d["marker"] == "done"
    assert d["kind"] == "main"
    assert list(tmp_path.glob("*.tmp.*")) == []  # no temp leftover


def test_subagent_missing_agent_id_never_clobbers_main(tmp_path):
    # A subagent SHARES the parent's sid; if its payload has NO agent_id the
    # producer must STILL never write to <sid>.json (the main file). It falls
    # back to <sid>__unknown.json.
    ss.write_heartbeat({"session_id": "sid-x", "cwd": "/main",
                        "last_assistant_message": "m\n⏳ WORKING: z"},
                       "stop", base_dir=tmp_path, now=1)
    p = ss.write_heartbeat({"session_id": "sid-x", "cwd": "/wt",
                            "last_assistant_message": "done"},
                           "subagent_stop", base_dir=tmp_path, now=2)
    assert p == tmp_path / "sid-x__unknown.json"
    main = json.loads((tmp_path / "sid-x.json").read_text())
    assert main["kind"] == "main"  # NOT clobbered by the subagent write
    assert main["cwd"] == "/main"


def test_subagent_heartbeat_does_not_clobber_main(tmp_path):
    # THE point-(b) guarantee: a subagent SHARES the parent's sid but must
    # never overwrite the main session's file.
    ss.write_heartbeat({"session_id": "sid-shared", "cwd": "/main",
                        "last_assistant_message": "m\n⏳ WORKING: z"},
                       "stop", base_dir=tmp_path, now=1)
    ss.write_heartbeat({"session_id": "sid-shared", "agent_id": "wkr-1",
                        "agent_type": "autopilot-worker", "cwd": "/wt",
                        "last_assistant_message": "done"},
                       "subagent_stop", base_dir=tmp_path, now=2)
    main = json.loads((tmp_path / "sid-shared.json").read_text())
    sub = json.loads((tmp_path / "sid-shared__wkr-1.json").read_text())
    assert main["kind"] == "main"
    assert main["cwd"] == "/main"
    assert sub["kind"] == "subagent"
    assert sub["agent_id"] == "wkr-1"


# --------------------------------------------------------------------------- #
# goal_armed_from_transcript — REAL integration with canonical scan_goal_markers
# --------------------------------------------------------------------------- #

def _write_goal_transcript(tmp_path, *marks):
    """A real transcript whose entries carry `<local-command-stdout>Goal
    set:/cleared: ...</local-command-stdout>` (the exact shape the canonical
    scanner reads — top-level `system` entry with a plain-string content)."""
    p = tmp_path / "t.jsonl"
    lines = []
    for i, mark in enumerate(marks):
        iso = datetime.fromtimestamp(100 + i, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        wrapped = "<local-command-stdout>%s</local-command-stdout>" % mark
        lines.append(json.dumps({"type": "system", "subtype": "local_command",
                                 "timestamp": iso, "content": wrapped}))
    p.write_text(("\n".join(lines) + "\n") if lines else "")
    return p


def test_goal_armed_true_on_set(tmp_path):
    p = _write_goal_transcript(tmp_path, "Goal set: /goal do X")
    assert ss.goal_armed_from_transcript(str(p)) is True


def test_goal_armed_false_when_cleared_last(tmp_path):
    p = _write_goal_transcript(tmp_path,
                               "Goal set: /goal do X", "Goal cleared: done")
    assert ss.goal_armed_from_transcript(str(p)) is False


def test_goal_armed_false_no_marker(tmp_path):
    p = tmp_path / "plain.jsonl"
    p.write_text(json.dumps({"type": "assistant",
                             "message": {"content": "hi"}}) + "\n")
    assert ss.goal_armed_from_transcript(str(p)) is False


def test_goal_armed_none_when_no_path():
    assert ss.goal_armed_from_transcript(None) is None
    assert ss.goal_armed_from_transcript("") is None


def test_write_heartbeat_stop_reads_goal_armed(tmp_path):
    tp = _write_goal_transcript(tmp_path, "Goal set: /goal loop")
    payload = {"session_id": "sg", "cwd": "/r", "transcript_path": str(tp),
               "last_assistant_message": "x\n⏳ WORKING: y"}
    p = ss.write_heartbeat(payload, "stop", base_dir=tmp_path, now=7)
    assert json.loads(p.read_text())["goal_armed"] is True


# --------------------------------------------------------------------------- #
# read_status — fresh / stale / corrupt / absent
# --------------------------------------------------------------------------- #

def test_read_status_absent(tmp_path):
    v = ss.read_status(sid="nope", base_dir=tmp_path)
    assert v.state == "absent"
    assert v.data is None


def test_read_status_fresh(tmp_path):
    ss.write_heartbeat({"session_id": "s", "cwd": "/r",
                        "last_assistant_message": "x\n❓ NEEDS YOU: q"},
                       "stop", base_dir=tmp_path, now=time.time())
    v = ss.read_status(sid="s", base_dir=tmp_path, stale_after_s=300)
    assert v.state == "fresh"
    assert v.sid == "s"
    assert v.kind == "main"
    assert v.marker == "needs_you"
    assert v.cwd == "/r"


def test_read_status_stale(tmp_path):
    p = ss.write_heartbeat({"session_id": "s", "cwd": "/r"},
                           "stop", base_dir=tmp_path)
    old = time.time() - 10_000
    os.utime(p, (old, old))
    v = ss.read_status(sid="s", base_dir=tmp_path, stale_after_s=300)
    assert v.state == "stale"
    assert v.age_s > 300


def test_read_status_corrupt_logs_loudly(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ this is not json")
    warnings = []
    v = ss.read_status(path=p, on_warn=warnings.append)
    assert v.state == "corrupt"
    assert v.data is None
    assert warnings and "corrupt" in warnings[0]


def test_read_status_corrupt_non_dict(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text("[1,2,3]")
    v = ss.read_status(path=p, on_warn=lambda m: None)
    assert v.state == "corrupt"


def test_read_status_never_raises_on_truncated(tmp_path):
    p = tmp_path / "half.json"
    p.write_text('{"schema":1,"sid":"s"')  # truncated mid-write
    v = ss.read_status(path=p, on_warn=lambda m: None)
    assert v.state == "corrupt"


# --------------------------------------------------------------------------- #
# env override + CLI main
# --------------------------------------------------------------------------- #

def test_status_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRULESET_SESSION_STATUS_DIR", str(tmp_path))
    assert ss.status_dir() == tmp_path
    assert ss.status_path("x") == tmp_path / "x.json"


def test_main_writes_from_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRULESET_SESSION_STATUS_DIR", str(tmp_path))
    payload = json.dumps({"session_id": "cli-1", "cwd": "/r",
                          "last_assistant_message": "x\n✅ DONE: ok"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = ss.main(["--event", "stop"])
    assert rc == 0
    d = json.loads((tmp_path / "cli-1.json").read_text())
    assert d["marker"] == "done"
    assert d["kind"] == "main"


def test_main_tolerates_garbage_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRULESET_SESSION_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
    rc = ss.main(["--event", "subagent_stop"])
    assert rc == 0  # never raises
