"""Tests for #861 — uploads sweep keep-marker + deletions journal + log rotation.

RED commit: these tests reproduce the two live-data-loss defects:
1. discover_stale_uploads ignores .airuleset-keep marker → deletes protected subtrees
2. _append_log self-truncates in-place → deletion records lost
3. execute_drain has no deletions journal
"""
from __future__ import annotations

import inspect
import json
import os
import time

import watchdog.disk_guard as dg


# --------------------------------------------------------------------------- #
# 1. uploads sweep must honour .airuleset-keep marker (#861 defect 1)
# --------------------------------------------------------------------------- #
def test_uploads_sweep_honours_keep_marker_subtree(tmp_path):
    """A .airuleset-keep marker in a subtree of ~/uploads protects the ENTIRE
    subtree from the stale-uploads sweep. This is the tvdole incident: a live
    serve root under ~/uploads was deleted because no exemption existed."""
    up = tmp_path / "uploads"
    # Protected subtree
    site = up / "tvdole_site"
    site.mkdir(parents=True)
    (site / ".airuleset-keep").write_text("")
    video = site / "video.mp4"
    video.write_bytes(b"v" * 1000)
    # Unprotected file
    stale = up / "old-recording.mp4"
    stale.write_bytes(b"x" * 100)

    now = 1_000_000_000.0
    # Make all files 30 days old (well past the 14-day cutoff)
    for f in [video, stale, site / ".airuleset-keep"]:
        os.utime(f, (now - 30 * 86400, now - 30 * 86400))

    rows = dg.discover_stale_uploads(home=str(tmp_path), now=now)
    by_path = {}
    for r in rows:
        p = r.get("path")
        if p:
            by_path[os.path.basename(p)] = r

    # The protected subtree's files must be KEPT (reason is not None)
    assert "video.mp4" in by_path, "video.mp4 should appear in the scan"
    assert by_path["video.mp4"]["reason"] is not None, \
        "video.mp4 under .airuleset-keep subtree must be KEPT (reason set)"

    # The unprotected file must still be deletable (reason is None)
    assert "old-recording.mp4" in by_path
    assert by_path["old-recording.mp4"]["reason"] is None, \
        "old-recording.mp4 outside any keep marker must be deletable"


def test_uploads_sweep_honours_no_sweep_marker(tmp_path):
    """Alternative marker name .no-sweep is also accepted."""
    up = tmp_path / "uploads"
    site = up / "mysite"
    site.mkdir(parents=True)
    (site / ".no-sweep").write_text("")
    old_file = site / "data.json"
    old_file.write_bytes(b"{}")

    now = 1_000_000_000.0
    os.utime(old_file, (now - 30 * 86400, now - 30 * 86400))
    os.utime(site / ".no-sweep", (now - 30 * 86400, now - 30 * 86400))

    rows = dg.discover_stale_uploads(home=str(tmp_path), now=now)
    data_rows = [r for r in rows if r.get("path") and
                 os.path.basename(r["path"]) == "data.json"]
    assert len(data_rows) == 1
    assert data_rows[0]["reason"] is not None, \
        "data.json under .no-sweep marker must be KEPT"


def test_uploads_sweep_marker_unreadable_keeps(tmp_path):
    """When the marker's PARENT directory is unreadable (mode 000), the
    fail-safe direction is KEEP — never delete possibly-live data. The
    _uploads_dir_has_keep_marker function uses os.lstat which raises
    OSError (not returns False) on a permission-denied path, so the
    except-OSError branch fires and returns KEEP."""
    up = tmp_path / "uploads"
    site = up / "protected"
    site.mkdir(parents=True)
    marker = site / ".airuleset-keep"
    marker.write_text("")
    old_file = site / "precious.dat"
    old_file.write_bytes(b"p" * 500)

    now = 1_000_000_000.0
    os.utime(old_file, (now - 30 * 86400, now - 30 * 86400))
    os.utime(marker, (now - 30 * 86400, now - 30 * 86400))

    # Make the DIRECTORY unreadable so os.lstat on files inside fails
    if os.geteuid() != 0:
        os.chmod(str(site), 0o000)
        try:
            # Verify the fail-safe branch fires: os.lstat raises, not returns
            result = dg._uploads_dir_has_keep_marker(str(site))
            assert result is True, \
                "unreadable dir must fail-safe to KEEP (True)"
        finally:
            os.chmod(str(site), 0o755)


def test_uploads_sweep_root_marker_protects_everything(tmp_path):
    """A .airuleset-keep at the UPLOADS ROOT (~/uploads/.airuleset-keep)
    protects the ENTIRE uploads tree — root-level files AND subtrees.
    This is the natural 'protect everything' placement (#861 review)."""
    up = tmp_path / "uploads"
    up.mkdir(parents=True)
    (up / ".airuleset-keep").write_text("")

    # Root-level file
    root_file = up / "rootfile.mp4"
    root_file.write_bytes(b"r" * 100)
    # File in a subtree
    sub = up / "subdir"
    sub.mkdir()
    sub_file = sub / "subfile.mp4"
    sub_file.write_bytes(b"s" * 100)

    now = 1_000_000_000.0
    for f in [root_file, sub_file, up / ".airuleset-keep"]:
        os.utime(f, (now - 30 * 86400, now - 30 * 86400))

    rows = dg.discover_stale_uploads(home=str(tmp_path), now=now)
    by_name = {}
    for r in rows:
        p = r.get("path")
        if p:
            by_name[os.path.basename(p)] = r

    assert "rootfile.mp4" in by_name
    assert by_name["rootfile.mp4"]["reason"] is not None, \
        "root-level file under root marker must be KEPT"
    assert "subfile.mp4" in by_name
    assert by_name["subfile.mp4"]["reason"] is not None, \
        "subtree file under root marker must be KEPT"


def test_uploads_sweep_nested_marker(tmp_path):
    """A marker deeper in the tree protects only its own subtree, not siblings."""
    up = tmp_path / "uploads"
    # Protected inner subtree
    inner = up / "projects" / "site"
    inner.mkdir(parents=True)
    (inner / ".airuleset-keep").write_text("")
    inner_file = inner / "index.html"
    inner_file.write_bytes(b"<html>")

    # Sibling unprotected
    sibling = up / "projects" / "temp"
    sibling.mkdir(parents=True)
    sibling_file = sibling / "old.txt"
    sibling_file.write_bytes(b"old")

    now = 1_000_000_000.0
    for f in [inner_file, inner / ".airuleset-keep", sibling_file]:
        os.utime(f, (now - 30 * 86400, now - 30 * 86400))

    rows = dg.discover_stale_uploads(home=str(tmp_path), now=now)
    by_name = {}
    for r in rows:
        p = r.get("path")
        if p:
            by_name[os.path.basename(p)] = r

    assert "index.html" in by_name
    assert by_name["index.html"]["reason"] is not None, \
        "index.html under keep marker must be KEPT"
    assert "old.txt" in by_name
    assert by_name["old.txt"]["reason"] is None, \
        "old.txt NOT under any keep marker must be deletable"


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
    deleted_path = str(tmp_path / "uploads" / "old.mp4")

    # A planner that emits one deletable action
    def fake_planner():
        return [{"cls": "uploads", "path": deleted_path,
                 "bytes": 1234, "kind": "delete", "reason": None,
                 "mtime": now - 30 * 86400}]

    recheck_calls = iter([90, 70])  # above target, then below
    log_path = str(guard_dir / dg.LOG_NAME)

    dg.execute_drain(
        status={"worst_pct": 90, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 90}]},
        home=home,
        planners=[("uploads", fake_planner)],
        recheck_fn=lambda: next(recheck_calls),
        do_action_fn=lambda a: a.get("bytes", 0),
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
    assert entry["rung"] == "uploads"
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
    planner_fns = [dg._plan_uploads, dg._plan_scratch, dg._plan_worktrees,
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
    deleted_path = str(tmp_path / "uploads" / "old.mp4")

    def fake_planner():
        return [{"cls": "uploads", "path": deleted_path,
                 "bytes": 1234, "kind": "delete", "reason": None}]

    recheck_calls = iter([90, 70])
    log_path = str(guard_dir / dg.LOG_NAME)

    dg.execute_drain(
        status={"worst_pct": 90, "dim": "bytes", "level": "drain",
                "mounts": [{"mount": "/", "worst_pct": 90}]},
        home=home,
        planners=[("uploads", fake_planner)],
        recheck_fn=lambda: next(recheck_calls),
        do_action_fn=lambda a: a.get("bytes", 0),
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


# --------------------------------------------------------------------------- #
# 4. fail-safe: os.lstat is used, not os.path.exists (#861 review finding)
# --------------------------------------------------------------------------- #
def test_marker_checker_uses_lstat_not_exists():
    """The marker checker must use os.lstat (which raises OSError on
    permission errors) not os.path.exists (which returns False and makes
    the fail-safe branch dead code)."""
    src = inspect.getsource(dg._uploads_dir_has_keep_marker)
    assert "os.lstat" in src, \
        "_uploads_dir_has_keep_marker must use os.lstat for fail-safe"
    assert "os.path.exists" not in src, \
        "_uploads_dir_has_keep_marker must NOT use os.path.exists (dead fail-safe)"
