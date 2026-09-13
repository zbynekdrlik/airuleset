"""#1012 — the ONE place tests spell the exec-marker STATE paths.

`block-main-implementation.sh` (PreToolUse guard), `post-consume-main-exec-
marker.sh` (PostToolUse consumer) and the Job 22 sweeper
(`watchdog/sweep_jobs.py::cleanup_stale_exec_markers`) all resolve the marker
directory from the `AIRULESET_MAIN_EXEC_STATE_DIR` env seam (default `/tmp`),
so a test's marker always lands where the hook/sweeper look. Every test builds
its marker paths through THIS module — no other file under `tests/` may hardcode
a `/tmp` exec-marker STATE path (locked by
`tests/test_exec_marker_state_dir_1012.py`).
"""
import os
from pathlib import Path


def exec_state_dir():
    """Mirror the hooks' `_EXEC_LOG_DIR` / the sweeper's `exec_marker_dir()`
    validation byte-for-byte: an unset/empty/root-`/`/non-dir/non-writable
    override falls back to `/tmp`, so a test computes the SAME effective
    directory the hook/sweeper will use."""
    ovr = (os.environ.get("AIRULESET_MAIN_EXEC_STATE_DIR") or "").rstrip("/")
    if ovr and os.path.isdir(ovr) and os.access(ovr, os.W_OK):
        return ovr
    return "/tmp"


def marker_ok(sid):
    return Path(exec_state_dir(), "airuleset-main-exec-ok-%s" % sid)


def marker_fable(sid):
    return Path(exec_state_dir(), "airuleset-fable-exec-ok-%s" % sid)


def marker_pending(sid):
    return Path(exec_state_dir(), "airuleset-main-exec-pending-%s" % sid)
