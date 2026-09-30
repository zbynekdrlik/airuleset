"""#1195 item 5 — a LIVE session's scratchpad child is aged by USE, not by mtime.

The disk-guard scratch rung (#849 ask 2) aged each top-level child of a live
session's ``scratchpad/`` by its newest MTIME only, so a helper script the
session executes (reads) many times a day but never rewrites was deleted from
under the running session (controller, 30.9. 04:24Z: ``merge-one.sh`` and
``ratchet-fix.py`` among 1876 children). A child's age is now
``now - max(newest mtime, newest atime)`` over its regular files (relatime
advances atime on a read at most daily, well inside the 2-day child floor).

Only FILE atimes count: the sweep's own ``os.walk`` lists each directory,
which bumps the DIRECTORY's atime, so a dir atime would let the sweep mark
every dir as used by itself. The session-level age (the determinably DEAD
path) stays mtime-only — liveness is authoritative there.

All trees live under ``tmp_path``; times are set with ``os.utime``.
"""

import builtins
import io
import os
import time
from pathlib import Path

import cli_scratch_sweep as cs

_DAY = 86400.0
_LIVE_UUID = "f219f0e3-5555-6666-7777-888888888888"
_DEAD_UUID = "74e50984-1111-2222-3333-444444444444"


def _mkfile(path, now, mtime_days, atime_days=None, nbytes=64):
    """A regular file with mtime ``mtime_days`` and atime ``atime_days``
    (defaults to the mtime) before ``now``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * nbytes)
    atime_days = mtime_days if atime_days is None else atime_days
    os.utime(path, (now - atime_days * _DAY, now - mtime_days * _DAY))
    return path


def _age_dir(path, now, mtime_days, atime_days=None):
    atime_days = mtime_days if atime_days is None else atime_days
    os.utime(path, (now - atime_days * _DAY, now - mtime_days * _DAY))


def _empty_proc(root):
    proc = Path(root) / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    return str(proc)


def _live_proc(root, sess):
    proc = Path(root) / "proc"
    pdir = proc / "42"
    (pdir / "fd").mkdir(parents=True)
    os.symlink(str(sess.resolve()), pdir / "cwd")
    return str(proc)


def _candidates(rows):
    return {Path(r["path"]).name for r in rows if r.get("reason") is None}


def _rows_by_name(rows):
    return {Path(r["path"]).name: r for r in rows}


class TestLiveScratchpadChildAgedByUse:

    def test_file_read_within_floor_is_kept_even_with_old_mtime(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        _mkfile(sess / "scratchpad" / "merge-one.sh", now, mtime_days=10,
                atime_days=1 / 24.0)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert "merge-one.sh" not in _candidates(rows), \
            "a script read an hour ago is in use -- never a delete candidate"

    def test_used_child_is_logged_as_kept_with_its_reason(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        _mkfile(sess / "scratchpad" / "ratchet-fix.py", now, mtime_days=10,
                atime_days=0.5)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        row = _rows_by_name(rows).get("ratchet-fix.py")
        assert row is not None, \
            "a child kept only by its recent use must be visible in the drain log"
        assert row["reason"] is not None and "atime" in row["reason"], row

    def test_file_neither_read_nor_written_within_floor_is_reclaimable(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        _mkfile(sess / "scratchpad" / "old.log", now, mtime_days=10, atime_days=9)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert "old.log" in _candidates(rows), \
            "genuine litter (old mtime AND old atime) stays reclaimable"

    def test_dir_child_with_one_recently_read_file_is_kept(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        tools = sess / "scratchpad" / "tools"
        _mkfile(tools / "a.py", now, mtime_days=10, atime_days=10)
        _mkfile(tools / "sub" / "b.sh", now, mtime_days=10, atime_days=0.2)
        _age_dir(tools / "sub", now, mtime_days=10)
        _age_dir(tools, now, mtime_days=10)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert "tools" not in _candidates(rows), \
            "a dir child whose newest atime (any file inside) is inside the floor is in use"

    def test_dir_child_with_every_file_unused_is_reclaimable(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        venv = sess / "scratchpad" / "old-venv"
        _mkfile(venv / "lib" / "x.so", now, mtime_days=10, atime_days=8)
        _mkfile(venv / "y.txt", now, mtime_days=12, atime_days=12)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert "old-venv" in _candidates(rows)

    def test_directory_atime_never_counts_as_use(self, tmp_path):
        """Listing a dir (the sweep's own walk, an `ls`) bumps the DIR's atime;
        that must never make a dir of unused files look used."""
        now = time.time()
        sess = tmp_path / "sess"
        venv = sess / "scratchpad" / "old-venv"
        _mkfile(venv / "sub" / "x.so", now, mtime_days=10, atime_days=10)
        _age_dir(venv / "sub", now, mtime_days=10, atime_days=0)
        _age_dir(venv, now, mtime_days=10, atime_days=0)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert "old-venv" in _candidates(rows)

    def test_empty_dir_child_ages_by_its_own_mtime_not_atime(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        empty = sess / "scratchpad" / "empty-dir"
        empty.mkdir(parents=True)
        _age_dir(empty, now, mtime_days=10, atime_days=0)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert "empty-dir" in _candidates(rows)

    def test_noatime_degrades_to_the_mtime_rule(self, tmp_path):
        """On a noatime mount atime stays at creation (older than mtime): the
        age is the mtime age, exactly the old rule."""
        now = time.time()
        sess = tmp_path / "sess"
        _mkfile(sess / "scratchpad" / "fresh.py", now, mtime_days=0.5, atime_days=30)
        _mkfile(sess / "scratchpad" / "stale.py", now, mtime_days=10, atime_days=30)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert _candidates(rows) == {"stale.py"}
        assert "fresh.py" not in _rows_by_name(rows), \
            "a child fresh by mtime is not reported at all (unchanged)"

    def test_reclaimable_row_reports_the_child_size(self, tmp_path):
        now = time.time()
        sess = tmp_path / "sess"
        _mkfile(sess / "scratchpad" / "d" / "a.bin", now, mtime_days=10, nbytes=300)
        _mkfile(sess / "scratchpad" / "d" / "b.bin", now, mtime_days=10, nbytes=200)
        rows = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        assert _rows_by_name(rows)["d"]["size"] == 500


class TestSweepNeverReadsScratchContent:

    def test_discovery_leaves_every_file_atime_unchanged(self, tmp_path):
        now = time.time()
        uid = os.getuid()
        sess = tmp_path / ("claude-%d" % uid) / "-home-x-devel-y" / _LIVE_UUID
        files = [
            _mkfile(sess / "scratchpad" / "tool.sh", now, mtime_days=10, atime_days=5),
            _mkfile(sess / "scratchpad" / "d" / "e" / "f.bin", now, mtime_days=10,
                    atime_days=4),
        ]
        proc = _live_proc(tmp_path, sess)
        before = [os.lstat(f).st_atime for f in files]
        for _pass in range(2):
            cs.discover_claude_scratch_candidates(
                tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
                proc_dir=proc, home=str(tmp_path))
        assert [os.lstat(f).st_atime for f in files] == before

    def test_discovery_never_opens_a_file_under_the_scratch_root(self, tmp_path,
                                                                 monkeypatch):
        now = time.time()
        uid = os.getuid()
        root = tmp_path / ("claude-%d" % uid)
        sess = root / "-home-x-devel-y" / _LIVE_UUID
        _mkfile(sess / "scratchpad" / "tool.sh", now, mtime_days=10, atime_days=0.1)
        _mkfile(sess / "scratchpad" / "d" / "f.bin", now, mtime_days=10)
        dead = root / "-home-x-devel-y" / _DEAD_UUID
        _mkfile(dead / "scratchpad" / "g.bin", now, mtime_days=30)
        proc = _live_proc(tmp_path, sess)
        opened = []
        scratch = str(root.resolve())

        def _spy(real):
            def _wrapped(file, *a, **kw):
                p = os.fspath(file) if not isinstance(file, int) else ""
                if isinstance(p, bytes):
                    p = p.decode()
                if p and os.path.realpath(p).startswith(scratch):
                    opened.append(p)
                return real(file, *a, **kw)
            return _wrapped

        monkeypatch.setattr(builtins, "open", _spy(builtins.open))
        monkeypatch.setattr(io, "open", _spy(io.open))
        monkeypatch.setattr(os, "open", _spy(os.open))
        cs.discover_claude_scratch_candidates(
            tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
            proc_dir=proc, home=str(tmp_path))
        assert opened == [], "the scratch path may stat only, never open: %r" % opened


class TestFullDiscoveryPath:

    def test_live_session_stale_children_skip_a_recently_read_tool(self, tmp_path):
        now = time.time()
        uid = os.getuid()
        sess = tmp_path / ("claude-%d" % uid) / "-home-x-devel-y" / _LIVE_UUID
        _mkfile(sess / "scratchpad" / "merge-one.sh", now, mtime_days=10,
                atime_days=0.1)
        _mkfile(sess / "scratchpad" / "litter.bin", now, mtime_days=10, atime_days=10)
        rows = cs.discover_claude_scratch_candidates(
            tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
            proc_dir=_live_proc(tmp_path, sess), home=str(tmp_path))
        sess_row = next(r for r in rows if r.get("uuid") == _LIVE_UUID)
        assert sess_row["live"] is True
        assert _candidates(sess_row["stale_children"]) == {"litter.bin"}

    def test_dead_session_level_age_stays_mtime_only(self, tmp_path):
        """Decision lock: a determinably DEAD session is reclaimed by its mtime
        age; a read (a recursive grep, a backup) does not postpone it."""
        now = time.time()
        uid = os.getuid()
        sess = tmp_path / ("claude-%d" % uid) / "-home-x-devel-y" / _DEAD_UUID
        _mkfile(sess / "scratchpad" / "old.bin", now, mtime_days=30, atime_days=0.1)
        rows = cs.discover_claude_scratch_candidates(
            tmp_dir=str(tmp_path), uid=uid, now=now, min_age_days=7,
            proc_dir=_empty_proc(tmp_path), home=str(tmp_path))
        sess_row = next(r for r in rows if r.get("uuid") == _DEAD_UUID)
        assert sess_row["reason"] is None, sess_row


class TestActTimeRecheckAndPlan:
    """Review of 73b306c9: the drain deletes children one by one after the plan
    (1876 of them in the incident), so a child read DURING the drain must be
    refused at act time, exactly like a session that went live."""

    def _child(self, tmp_path, now, atime_days):
        uid = os.getuid()
        sess = tmp_path / ("claude-%d" % uid) / "-home-x-devel-y" / _LIVE_UUID
        return _mkfile(sess / "scratchpad" / "tool.sh", now, mtime_days=10,
                       atime_days=atime_days)

    def test_recheck_refuses_a_child_read_since_the_plan(self, tmp_path):
        now = time.time()
        child = self._child(tmp_path, now, atime_days=0.01)
        assert cs.scratch_session_live_recheck(
            str(child), home=str(tmp_path), now=now,
            proc_dir=_empty_proc(tmp_path)) is True

    def test_recheck_refuses_a_child_held_open(self, tmp_path):
        now = time.time()
        child = self._child(tmp_path, now, atime_days=10)
        proc = Path(tmp_path) / "proc"
        (proc / "7" / "fd").mkdir(parents=True)
        os.symlink(str(child.resolve()), proc / "7" / "fd" / "3")
        assert cs.scratch_session_live_recheck(
            str(child), home=str(tmp_path), now=now, proc_dir=str(proc)) is True

    def test_recheck_allows_a_child_still_unused(self, tmp_path):
        now = time.time()
        child = self._child(tmp_path, now, atime_days=10)
        assert cs.scratch_session_live_recheck(
            str(child), home=str(tmp_path), now=now,
            proc_dir=_empty_proc(tmp_path)) is False

    def test_kept_row_becomes_a_skip_action_in_the_plan(self, tmp_path):
        import watchdog.disk_guard as dg
        now = time.time()
        sess = tmp_path / "sess"
        _mkfile(sess / "scratchpad" / "merge-one.sh", now, mtime_days=10,
                atime_days=0.1)
        children = cs.discover_stale_scratchpad_children(
            sess, now, proc_dir=_empty_proc(tmp_path))
        rows = [{"path": str(sess), "reason": "live session -- kept", "uuid": _LIVE_UUID,
                 "live": True, "size": 1, "stale_children": children}]
        actions = dg._plan_scratch(str(tmp_path), now, scratch_rows=rows)
        child = next(a for a in actions if a["path"].endswith("merge-one.sh"))
        assert child["kind"] == "skip" and "atime" in child["reason"], child

    def test_child_floor_covers_the_relatime_lag(self):
        """relatime trails a read by < 24 h, so a child read at least once every
        24 h has atime < 48 h old; the child floor must stay >= 2 days."""
        assert cs.SCRATCHPAD_CHILD_MIN_AGE_DAYS >= 2
