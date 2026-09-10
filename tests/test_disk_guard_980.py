"""#980 — disk-guard gk: five rung fixes (RED tests, all must FAIL before GREEN).

Bug classes:
1. Runner Worker liveness check — new runner_worker_live() function must
   exist, use bin/Runner\\.Worker pattern, Listener-only → idle, Worker → busy.
2. Session-scratch rung for /tmp/claude-<uid>/ with liveness gating.
3. claude --version PATH fix.
4. drain_exhausted carries per-rung skip reasons in status.json.
"""

import json
import os
import subprocess
import time
import types
from pathlib import Path

import pytest

import watchdog.disk_guard as dg


# --------------------------------------------------------------------------- #
# BLOCK real gh-request subprocess (same fixture as test_disk_guard.py)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _block_real_gh(monkeypatch):
    real_run = subprocess.run

    def _guarded(argv, *a, **kw):
        if isinstance(argv, (list, tuple)) and any("gk-request" == str(x) for x in argv):
            raise AssertionError("TEST HIT REAL gk-request subprocess")
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", _guarded)


# ======================================================================= #
# 1. Runner Worker liveness — new runner_worker_live() function             #
# ======================================================================= #
class TestRunnerWorkerLiveness:
    """A new runner_worker_live() function must distinguish Worker from
    Listener using a fake ps-table seam.  Listener-only → idle (False);
    Worker present → busy (True)."""

    def test_runner_worker_live_exists(self):
        """The new helper must be importable from disk_guard_runner."""
        from watchdog.disk_guard_runner import runner_worker_live
        assert callable(runner_worker_live)

    def test_listener_only_means_idle(self):
        """Listener-only ps table → runner_worker_live() returns False."""
        from watchdog.disk_guard_runner import runner_worker_live

        # Fake ps table: only Runner.Listener processes, no Worker.
        def fake_pgrep(pat):
            # Simulate: cmdlines on gk are like
            #   /home/gh-runner/actions-runner/bin/Runner.Listener run
            # The pattern should NOT match Listener cmdlines.
            import re
            cmdlines = [
                "/home/gh-runner/actions-runner/bin/Runner.Listener run",
                "/home/gh-runner/actions-runner-miva/bin/Runner.Listener run",
                "/home/gh-runner/actions-runner-montalu/bin/Runner.Listener run",
            ]
            matched = [c for c in cmdlines if re.search(pat, c)]
            return "\n".join("1%d" % i for i, _ in enumerate(matched)) if matched else ""

        assert runner_worker_live(pgrep_fn=fake_pgrep) is False, (
            "Listener-only ps table must return idle (False)")

    def test_worker_present_means_busy(self):
        """Worker in ps table → runner_worker_live() returns True."""
        from watchdog.disk_guard_runner import runner_worker_live

        def fake_pgrep(pat):
            import re
            cmdlines = [
                "/home/gh-runner/actions-runner/bin/Runner.Listener run",
                "/home/gh-runner/actions-runner/bin/Runner.Worker spawnclient 42",
            ]
            matched = [c for c in cmdlines if re.search(pat, c)]
            return "\n".join("2%d" % i for i, _ in enumerate(matched)) if matched else ""

        assert runner_worker_live(pgrep_fn=fake_pgrep) is True, (
            "Worker in ps table must return busy (True)")


# ======================================================================= #
# 2. Session scratch rung                                                   #
# ======================================================================= #
class TestSessionScratch:
    """The guard must sweep dead-session /tmp/claude-<uid>/ scratchpads,
    and NEVER touch a live session's scratch."""

    def test_session_scratch_in_reclaimable_classes(self):
        """'session-scratch' must be in RECLAIMABLE_CLASSES."""
        assert "session-scratch" in dg.RECLAIMABLE_CLASSES

    def test_session_scratch_in_default_planners(self):
        """The session-scratch planner must appear in the default drain ladder."""
        now = time.time()
        labels = [label for label, _fn in dg._default_planners("/tmp/fake", now)]
        assert "session-scratch" in labels

    def test_live_session_scratch_is_noop(self, tmp_path):
        """A scratch dir whose session has a live process (cwd inside it) must
        NOT be selected for deletion — the rung is a no-op for it."""
        from watchdog.disk_guard_runner import discover_session_scratch

        uid = os.getuid()
        scratch_root = tmp_path / ("claude-%d" % uid)
        cwd_key = "test-cwd-key"
        session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        session_dir = scratch_root / cwd_key / session_id
        scratchpad = session_dir / "scratchpad"
        scratchpad.mkdir(parents=True)
        (scratchpad / "body.md").write_text("test data")

        # Create a fake /proc with a process whose cwd is inside the scratch dir
        proc = tmp_path / "proc"
        pid_dir = proc / "12345"
        pid_dir.mkdir(parents=True)
        os.symlink(str(scratchpad), pid_dir / "cwd")
        (pid_dir / "fd").mkdir()

        now = time.time()
        rows = discover_session_scratch(
            tmp_dir=str(tmp_path), uid=uid, now=now,
            proc_dir=str(proc), transcript_age_hours=0)
        # All rows for this session must be skips (live process has cwd there).
        for r in rows:
            if session_id in (r.get("path") or ""):
                assert r.get("kind") == "skip", (
                    "Live-session scratch must never be selected for deletion: %r" % r)

    def test_dead_session_scratch_is_reclaimable(self, tmp_path):
        """A scratch dir with no live process and stale transcript is reclaimable."""
        from watchdog.disk_guard_runner import discover_session_scratch

        uid = os.getuid()
        scratch_root = tmp_path / ("claude-%d" % uid)
        cwd_key = "test-cwd-key"
        session_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        session_dir = scratch_root / cwd_key / session_id
        scratchpad = session_dir / "scratchpad"
        scratchpad.mkdir(parents=True)
        (scratchpad / "old-file.txt").write_text("stale data")

        # Create an empty /proc (no processes referencing this dir)
        proc = tmp_path / "proc"
        proc.mkdir(parents=True)

        # Create a stale transcript (older than 24h)
        claude_dir = tmp_path / "fake-home" / ".claude" / "projects" / cwd_key
        claude_dir.mkdir(parents=True)
        transcript = claude_dir / ("%s.jsonl" % session_id)
        transcript.write_text('{"type":"test"}')
        old_time = time.time() - 48 * 3600
        os.utime(str(transcript), (old_time, old_time))

        now = time.time()
        rows = discover_session_scratch(
            tmp_dir=str(tmp_path), uid=uid, now=now,
            proc_dir=str(proc), transcript_age_hours=24,
            home=str(tmp_path / "fake-home"))
        # The dead session dir should be a delete candidate.
        deletes = [r for r in rows if r.get("kind") != "skip"
                   and session_id in (r.get("path") or "")]
        assert len(deletes) > 0, (
            "Dead session scratch with stale transcript must be reclaimable")


# ======================================================================= #
# 3. claude --version PATH fix                                              #
# ======================================================================= #
class TestClaudeVersionPath:
    """The claude --version probe must find claude even when $HOME/.local/bin
    is not on PATH (the gk systemd-timer environment)."""

    def test_claude_version_uses_home_local_bin(self, monkeypatch):
        """_default_running_claude_version must add $HOME/.local/bin to PATH."""
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return types.SimpleNamespace(
                stdout="1.2.3 (Claude Code)", returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = dg._default_running_claude_version()
        assert result == "1.2.3"
        # Check that the PATH env was extended
        assert len(calls) == 1
        env = calls[0][1].get("env") or {}
        path_val = env.get("PATH", "")
        assert ".local/bin" in path_val, (
            "claude --version must have $HOME/.local/bin on PATH, got: %s" % path_val)


# ======================================================================= #
# 4. drain_exhausted + per-rung skip reasons in status.json                 #
# ======================================================================= #
class TestDrainExhaustedReasons:
    """When drain_exhausted is True, status.json must carry per-rung skip
    reasons."""

    def test_exhausted_carries_skipped_rungs(self, tmp_path):
        """status.json must contain 'drain_skipped_rungs' when drain_exhausted."""
        # Seed the drain cadence so it fires
        d = tmp_path / ".claude" / "disk-guard"
        d.mkdir(parents=True, exist_ok=True)
        (d / "last-drain").write_text("0")

        # A planner that skips everything
        def _skipping_planner():
            return [{"cls": "runner-superseded", "path": "/fake", "bytes": 500_000_000,
                     "kind": "skip", "reason": "SKIP-UNPRIVILEGED runner-superseded"}]

        severe_calls = []
        dg.run_disk_guard(
            now=1000.0, home=str(tmp_path), dry_run=False,
            statvfs_fn=lambda _m: types.SimpleNamespace(
                f_blocks=1000, f_bfree=40, f_bavail=40,
                f_frsize=4096, f_files=100000, f_ffree=50000),
            dev_fn=lambda _p: 1,
            geteuid_fn=lambda: 1000,
            planners_fn=lambda _h, _n: [
                ("runner-superseded", _skipping_planner),
            ],
            severe_run_fn=lambda *a, **kw: severe_calls.append(a),
            top_consumers_fn=lambda *a, **kw: [],
        )
        cache = json.loads((tmp_path / ".claude" / "disk-guard" / "status.json").read_text())
        assert cache.get("drain_exhausted") is True
        # The NEW requirement: skip reasons must be recorded
        skipped = cache.get("drain_skipped_rungs")
        assert skipped is not None, (
            "status.json must carry 'drain_skipped_rungs' when drain_exhausted")
        assert any("UNPRIVILEGED" in (s.get("reason") or "") for s in skipped), (
            "Skipped rungs must include the unprivileged reason")
