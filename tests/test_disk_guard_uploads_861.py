"""Tests for #861 — deletions journal + log rotation.

#1380 (owner 20.11.2026) removed the uploads sweep and its keep-marker
tests: ~/uploads is owner data and no rung deletes there.

RED commit: these tests reproduce the two live-data-loss defects:
1. discover_stale_uploads ignores .airuleset-keep marker → deletes protected subtrees
2. _append_log self-truncates in-place → deletion records lost
3. execute_drain has no deletions journal
"""
from __future__ import annotations

import inspect
import json
import time

import watchdog.disk_guard as dg


# --------------------------------------------------------------------------- #
# 2. execute_drain must write a deletions journal (#861 defect 2)
# --------------------------------------------------------------------------- #
def test_execute_drain_appends_deletion_journal_every_rung(tmp_path):
    """Every ACTED deletion from execute_drain is recorded in a monthly
    deletions-YYYYMM.jsonl file under ~/.claude/disk-guard/."""
    home = str(tmp_path)
    guard_dir = tmp_path / ".claude" / "disk-guard"
    guard_dir.mkdir(parents=True)

    now = 1_000_000_000.0
    deleted_path = str(tmp_path / ".cache" / "old.bin")

    # A planner that emits one deletable action
    def fake_planner():
        return [{"cls": "user-cache", "path": deleted_path,
                 "bytes": 1234, "kind": "delete", "reason": None,
                 "mtime": now - 30 * 86400}]

    recheck_calls = iter([90, 70])  # above target, then below
    log_path = str(guard_dir / dg.LOG_NAME)

    dg.execute_drain(
        status={"worst_pct": 90, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 90}]},
        home=home,
        planners=[("user-cache", fake_planner)],
        recheck_fn=lambda: next(recheck_calls),
        do_action_fn=lambda a: a.get("bytes", 0),
        geteuid_fn=lambda: 1000,          # CI runs as root; bypass the uid-0 refusal
        log_path=log_path,
        now=now,
    )

    # Find the deletions journal
    month = time.strftime("%Y%m", time.gmtime(now))
    journal_path = guard_dir / ("deletions-%s.jsonl" % month)
    assert journal_path.exists(), \
        "execute_drain must create a deletions journal file"

    entries = [json.loads(line) for line in
               journal_path.read_text().strip().split("\n") if line.strip()]
    assert len(entries) >= 1, "at least one deletion entry expected"
    entry = entries[0]
    assert entry["path"] == deleted_path
    assert entry["rung"] == "user-cache"
    assert "ts" in entry
    assert "bytes" in entry
    assert entry["mtime"] == now - 30 * 86400, "mtime must be recorded from the action"


def test_deletion_journal_never_reclaimable():
    """The deletions journal files must NOT be reclaimable — no planner walks
    the guard dir, and no cls in the fence covers them. The guard's own
    journal lives in ~/.claude/disk-guard/ which is never a planner target."""
    # No reclaimable class could cover the guard dir's journal files
    assert "deletions-journal" not in dg.RECLAIMABLE_CLASSES
    # Verify that no planner function references the guard dir as a target
    # by checking that the DELETIONS_JOURNAL_PREFIX is not in any planner
    # function's source code (it lives only in the journal writer)
    planner_fns = [dg._plan_scratch, dg._plan_worktrees,
                   dg._plan_cli_versions, dg._plan_transcripts,
                   dg._plan_toolchain]
    for fn in planner_fns:
        src = inspect.getsource(fn)
        assert dg.DELETIONS_JOURNAL_PREFIX not in src, \
            "planner %s must not reference the deletions journal" % fn.__name__


def test_deletion_journal_dry_run_writes_nothing(tmp_path):
    """Under dry_run, execute_drain must NOT write to the deletions journal
    — the journal records only REAL deletions."""
    home = str(tmp_path)
    guard_dir = tmp_path / ".claude" / "disk-guard"
    guard_dir.mkdir(parents=True)

    now = 1_000_000_000.0
    deleted_path = str(tmp_path / ".cache" / "old.bin")

    def fake_planner():
        return [{"cls": "user-cache", "path": deleted_path,
                 "bytes": 1234, "kind": "delete", "reason": None}]

    recheck_calls = iter([90, 70])
    log_path = str(guard_dir / dg.LOG_NAME)

    dg.execute_drain(
        status={"worst_pct": 90, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 90}]},
        home=home,
        planners=[("user-cache", fake_planner)],
        recheck_fn=lambda: next(recheck_calls),
        do_action_fn=lambda a: a.get("bytes", 0),
        geteuid_fn=lambda: 1000,          # CI runs as root; bypass the uid-0 refusal
        log_path=log_path,
        now=now,
        dry_run=True,
    )

    month = time.strftime("%Y%m", time.gmtime(now))
    journal_path = guard_dir / ("deletions-%s.jsonl" % month)
    assert not journal_path.exists(), \
        "dry_run must NOT create a deletions journal"


# --------------------------------------------------------------------------- #
# 3. disk-guard.log must rotate to a dated file, not truncate in place
# --------------------------------------------------------------------------- #
def test_disk_guard_log_rotates_to_dated_file_not_truncate(tmp_path):
    """When disk-guard.log exceeds LOG_MAX_BYTES, the old content must be
    preserved in a .log.1 file (one-generation rotation), NOT truncated
    in place losing the earlier half."""
    guard_dir = tmp_path / ".claude" / "disk-guard"
    guard_dir.mkdir(parents=True)
    log_file = guard_dir / dg.LOG_NAME

    # Write more than LOG_MAX_BYTES
    big_content = ("X" * 100 + "\n") * (dg.LOG_MAX_BYTES // 100 + 10)
    log_file.write_text(big_content)
    original_size = log_file.stat().st_size
    assert original_size > dg.LOG_MAX_BYTES

    # Append one more line to trigger rotation
    dg._append_log(str(log_file), ["new-line-after-rotation"])

    # The rotated file must exist
    rotated = guard_dir / (dg.LOG_NAME + ".1")
    assert rotated.exists(), \
        "rotation must create disk-guard.log.1 (not truncate in place)"

    # The main log file must contain the new line
    main_content = log_file.read_text()
    assert "new-line-after-rotation" in main_content

    # The rotated file must contain the old content (preserved, not lost)
    rotated_content = rotated.read_text()
    assert len(rotated_content) > 0, "rotated file must not be empty"
