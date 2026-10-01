"""#1195 item 7 — at CRITICAL disk a live session's scratchpad child is kept
only 24 h after its last use (2 days at drain/notice).

dev1, 1.10.2026 (#1209): 92 % with the drain exhausted and 8.0 GB in ONE live
camera-box session's ``scratchpad/``. Every child had been used within 48 h
(item 5's use-based age), so the 2-day child floor kept all of it and the
scratch rung yielded 0 bytes. With the 24 h floor at critical, 3.7 GB
qualified. The open-fd / cwd live-use check still applies at both floors, and
the act-time re-check uses the SAME floor the plan used.

All trees live under ``tmp_path``; times are set with ``os.utime``.
"""

import os
import time
import types
from pathlib import Path

import cli_scratch_sweep as cs
import watchdog.disk_guard as dg
from test_disk_guard import dev_map, statvfs_map

_HOUR = 3600.0
_LIVE_UUID = "f219f0e3-5555-6666-7777-888888888888"


def _mkfile(path, now, hours):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 64)
    os.utime(path, (now - hours * _HOUR, now - hours * _HOUR))
    return path


def _empty_proc(root):
    proc = Path(root) / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    return str(proc)


def _live_session(tmp_path, now):
    uid = os.getuid()
    sess = tmp_path / ("claude-%d" % uid) / "-home-x-devel-y" / _LIVE_UUID
    old = _mkfile(sess / "scratchpad" / "lane-copy" / "f.py", now, hours=30)
    fresh = _mkfile(sess / "scratchpad" / "tool.sh", now, hours=10)
    proc = Path(tmp_path) / "proc"
    (proc / "42" / "fd").mkdir(parents=True)
    os.symlink(str(sess.resolve()), proc / "42" / "cwd")
    return uid, sess, old.parent, fresh, str(proc)


def _children(rows):
    row = next(r for r in rows if r.get("uuid") == _LIVE_UUID)
    assert row["live"] is True
    return {Path(c["path"]).name: c for c in row["stale_children"]}


class TestPlanFloor:

    def test_the_critical_floor_is_one_day(self):
        assert cs.SCRATCHPAD_CHILD_CRITICAL_AGE_DAYS == 1
        assert cs.SCRATCHPAD_CHILD_MIN_AGE_DAYS == 2

    def test_critical_reclaims_a_child_unused_for_30h(self, tmp_path):
        now = time.time()
        uid, _sess, _old, _fresh, proc = _live_session(tmp_path, now)
        kids = _children(cs.discover_claude_scratch_candidates(
            tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
            proc_dir=proc, home=str(tmp_path),
            child_min_age_days=cs.SCRATCHPAD_CHILD_CRITICAL_AGE_DAYS))
        assert kids["lane-copy"]["reason"] is None, kids
        assert kids["lane-copy"]["floor_days"] == 1
        assert kids["tool.sh"]["reason"] is not None or "tool.sh" not in kids

    def test_default_floor_keeps_the_30h_child(self, tmp_path):
        now = time.time()
        uid, _sess, _old, _fresh, proc = _live_session(tmp_path, now)
        kids = _children(cs.discover_claude_scratch_candidates(
            tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
            proc_dir=proc, home=str(tmp_path)))
        assert "lane-copy" not in {n for n, c in kids.items() if c["reason"] is None}

    def test_a_child_held_open_is_kept_at_critical(self, tmp_path):
        now = time.time()
        uid, sess, old, _fresh, proc = _live_session(tmp_path, now)
        os.symlink(str((old / "f.py").resolve()), Path(proc) / "42" / "fd" / "3")
        kids = _children(cs.discover_claude_scratch_candidates(
            tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
            proc_dir=proc, home=str(tmp_path), child_min_age_days=1))
        assert kids["lane-copy"]["reason"] is not None, kids


class TestActTimeRecheck:

    def test_recheck_with_the_critical_floor_allows_the_30h_child(self, tmp_path):
        now = time.time()
        _uid, _sess, old, _fresh, _proc = _live_session(tmp_path, now)
        assert cs.scratch_session_live_recheck(
            str(old), home=str(tmp_path), now=now, proc_dir=_empty_proc(tmp_path),
            child_min_age_days=1) is False
        assert cs.scratch_session_live_recheck(
            str(old), home=str(tmp_path), now=now,
            proc_dir=_empty_proc(tmp_path)) is True

    def test_recheck_keeps_a_10h_child_at_critical(self, tmp_path):
        now = time.time()
        _uid, _sess, _old, fresh, _proc = _live_session(tmp_path, now)
        assert cs.scratch_session_live_recheck(
            str(fresh), home=str(tmp_path), now=now, proc_dir=_empty_proc(tmp_path),
            child_min_age_days=1) is True

    def test_the_action_carries_its_floor_to_the_executor(self, tmp_path):
        seen = []
        action = dg._row_to_action("scratch", {"path": str(tmp_path / "x"), "size": 5,
                                               "reason": None, "floor_days": 1}, "delete")
        assert action["floor_days"] == 1
        (tmp_path / "x").write_text("x")
        rc = dg._perform_action(action, scratch_live_fn=lambda p, n, **kw: (
            seen.append(kw), True)[1], now=time.time())
        assert rc == -1 and seen == [{"floor_days": 1}], seen

    def test_an_action_without_a_floor_keeps_the_old_call(self, tmp_path):
        seen = []
        (tmp_path / "y").write_text("y")
        dg._perform_action({"cls": "scratch", "path": str(tmp_path / "y"), "bytes": 1,
                            "kind": "delete", "reason": None},
                           scratch_live_fn=lambda p, n: (seen.append(p), True)[1],
                           now=time.time())
        assert seen == [str(tmp_path / "y")]


class TestDiskGuardLevel:

    def test_default_discover_passes_the_critical_floor(self, monkeypatch):
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return []
        monkeypatch.setattr(cs, "discover_claude_scratch_candidates", fake)
        dg._default_scratch_discover(1.0, "/h", level="critical")
        assert seen["child_min_age_days"] == cs.SCRATCHPAD_CHILD_CRITICAL_AGE_DAYS
        dg._default_scratch_discover(1.0, "/h", level="drain")
        assert seen["child_min_age_days"] is None

    def test_run_disk_guard_hands_the_level_to_the_discovery(self, tmp_path, monkeypatch):
        levels = []

        def fake_discover(now, home, **kw):
            levels.append(kw.get("level"))
            return []
        monkeypatch.setattr(dg, "_default_scratch_discover", fake_discover)
        dg.run_disk_guard(
            now=1_000_000_000.0, home=str(tmp_path), dry_run=True,
            statvfs_fn=statvfs_map({"/": (92, 20)}), dev_fn=dev_map({"/": 1}),
            geteuid_fn=lambda: 1000, mounts=("/",),
            planners_fn=lambda _h, _n: [],
            top_consumers_fn=lambda *a, **k: [],
            severe_run_fn=lambda argv, **kw: types.SimpleNamespace(
                returncode=0, stdout="", stderr=""))
        assert levels == ["critical"], levels
