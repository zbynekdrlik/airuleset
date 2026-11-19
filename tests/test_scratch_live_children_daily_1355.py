"""#1355 — the DAILY scratch sweep ages out a LIVE session's stale scratchpad
children, not only the >= 80 % disk-guard drain.

Controller, 17.11.2026: 7.1 GB of review/worker clones sat in the airuleset
supervisor's own scratchpad. The session had been live for weeks, so
`sweep_claude_scratch` kept its whole row ("live session ... kept
unconditionally") and never looked at the `stale_children` discovery already
computes. Only the Job 40 drain consumes those rows, and on 17.11. its scratch
rung was skipped by the #965 low-yield breaker.

Safety (owner: maintenance must never harm running work): a child goes only
after 2 days without use (newest mtime AND file atime in its subtree), with no
live fd/cwd inside it, re-checked right before the delete.

All trees live under ``tmp_path``; times are set with ``os.utime``.
"""

import os
import time
from pathlib import Path

import cli_scratch_sweep as cs

_HOUR = 3600.0
_UUID = "a1355a13-5555-6666-7777-888888888888"


def _mkfile(path, now, hours):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 64)
    os.utime(path, (now - hours * _HOUR, now - hours * _HOUR))
    return path


def _live_session(tmp_path, now):
    """A live session (a process cwd inside its dir) with one clone unused for
    3 days and one tool used 10 h ago."""
    uid = os.getuid()
    sess = tmp_path / ("claude-%d" % uid) / "-home-x-devel-y" / _UUID
    _mkfile(sess / "scratchpad" / "review-clone" / "src" / "a.py", now, hours=72)
    _mkfile(sess / "scratchpad" / "tool.sh", now, hours=10)
    proc = tmp_path / "proc"
    (proc / "42" / "fd").mkdir(parents=True)
    os.symlink(str(sess.resolve()), proc / "42" / "cwd")
    return uid, sess, str(proc)


def _sweep(tmp_path, uid, now, proc, **kw):
    return cs.sweep_claude_scratch(
        tmp_dir=str(tmp_path), uid=uid, now=now, force=True,
        log_path=tmp_path / "sweep.log", state_path=tmp_path / "state.json",
        proc_dir=proc, home=str(tmp_path), **kw)


def test_daily_sweep_removes_a_stale_child_of_a_live_session(tmp_path):
    now = time.time()
    uid, sess, proc = _live_session(tmp_path, now)
    rows = _sweep(tmp_path, uid, now, proc)
    assert not (sess / "scratchpad" / "review-clone").exists(), rows
    removed = [r for r in rows if r.get("removed")]
    assert [Path(r["path"]).name for r in removed] == ["review-clone"]
    # the live session itself and its fresh child are untouched
    assert sess.is_dir()
    assert (sess / "scratchpad" / "tool.sh").is_file()


def test_a_child_with_an_open_fd_survives(tmp_path):
    now = time.time()
    uid, sess, proc = _live_session(tmp_path, now)
    held = sess / "scratchpad" / "review-clone" / "src" / "a.py"
    (Path(proc) / "43" / "fd").mkdir(parents=True)
    os.symlink(str(held.resolve()), Path(proc) / "43" / "fd" / "3")
    _sweep(tmp_path, uid, now, proc)
    assert held.is_file()


def test_a_recent_mtime_deep_inside_keeps_the_child(tmp_path):
    now = time.time()
    uid, sess, proc = _live_session(tmp_path, now)
    _mkfile(sess / "scratchpad" / "review-clone" / "deep" / "x" / "new.txt", now, hours=1)
    _sweep(tmp_path, uid, now, proc)
    assert (sess / "scratchpad" / "review-clone" / "src" / "a.py").is_file()


def test_act_time_recheck_refuses_a_child_used_since_the_plan(tmp_path):
    """A planned child row whose tree was written after the plan is refused,
    even though the row says reason=None."""
    now = time.time()
    uid, sess, proc = _live_session(tmp_path, now)
    child = sess / "scratchpad" / "lane"
    _mkfile(child / "f.txt", now, hours=0.1)
    row = {"path": str(sess), "reason": "live session -- kept", "uuid": _UUID,
           "live": True, "size": 1,
           "stale_children": [{"path": str(child), "size": 64, "cls": "scratch",
                               "reason": None, "floor_days": 2}]}
    rows = _sweep(tmp_path, uid, now, proc, candidates=[row])
    assert child.is_dir(), rows
    kid = next(r for r in rows if r["path"] == str(child))
    assert kid["removed"] is False
    assert "refused" in kid["reason"]


def test_act_time_recheck_refuses_a_child_held_open_since_the_plan(tmp_path):
    """Old by mtime AND atime, but a process opened a file in it after the
    plan: the act-time fd check refuses it."""
    now = time.time()
    uid, sess, proc = _live_session(tmp_path, now)
    child = sess / "scratchpad" / "review-clone"
    (Path(proc) / "44" / "fd").mkdir(parents=True)
    os.symlink(str((child / "src" / "a.py").resolve()), Path(proc) / "44" / "fd" / "5")
    row = {"path": str(sess), "reason": "live session -- kept", "uuid": _UUID,
           "live": True, "size": 1,
           "stale_children": [{"path": str(child), "size": 64, "cls": "scratch",
                               "reason": None, "floor_days": 2}]}
    rows = _sweep(tmp_path, uid, now, proc, candidates=[row])
    assert child.is_dir(), rows
    kid = next(r for r in rows if r["path"] == str(child))
    assert "refused" in kid["reason"]


def test_dry_run_reports_the_child_and_deletes_nothing(tmp_path):
    now = time.time()
    uid, sess, proc = _live_session(tmp_path, now)
    rows = _sweep(tmp_path, uid, now, proc, dry_run=True)
    assert (sess / "scratchpad" / "review-clone").is_dir()
    kid = next(r for r in rows if r["path"].endswith("review-clone"))
    assert kid["reason"].startswith("would remove")


def test_child_floor_stays_two_days():
    """The daily sweep reuses the #1195 floor; relatime needs >= 48 h."""
    assert cs.SCRATCHPAD_CHILD_MIN_AGE_DAYS == 2
