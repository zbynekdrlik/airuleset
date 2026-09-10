"""Disk-guard runner + session-scratch helpers — satellite of disk_guard.py (#980).

Fixes that require new code but cannot grow disk_guard.py past its ratchet
ceiling (4026 lines).  Follows the same plan/execute-split architecture as
the main guard: every function here returns rows in the
``{cls, path, bytes, kind, reason}`` shape the executor expects.

stdlib-only at module level; deferred imports for ``cli_target_purge``,
``cli_scratch_sweep``, ``os``, ``re``, ``time``, ``subprocess`` helpers.
"""

import os
import re as _re
import subprocess
import time

# --- runner Worker liveness (#980 fix 2) --------------------------------- #
# The corrected pgrep pattern: ``bin/Runner\.Worker`` (path-anchored,
# escaped dot) so a ``Runner.Listener`` cmdline never matches.
RUNNER_WORKER_PGREP_RE = r"bin/Runner\.Worker"


def runner_worker_live(pgrep_fn=None):
    """True iff at least one ``bin/Runner.Worker`` process is running.

    Uses ``pgrep -f 'bin/Runner\\.Worker'`` (escaped dot, ``bin/`` prefix)
    so ``Runner.Listener`` daemons never cause a false-positive match.
    Self-excludes the calling PID.  Any pgrep error → True (fail-safe:
    treat unknown state as "worker live" → rung skips, never races).

    The ``pgrep_fn`` seam accepts a pattern string and returns the stdout
    of ``pgrep -f <pattern>`` (same shape as
    ``disk_guard._default_pgrep_any``).
    """
    if pgrep_fn is None:
        pgrep_fn = _default_pgrep_runner_worker
    try:
        out = pgrep_fn(RUNNER_WORKER_PGREP_RE) or ""
    except Exception:
        return True                       # fail-safe: unknown → treat as live
    # Filter out our own PID (pgrep normally excludes itself, but be safe).
    own_pid = str(os.getpid())
    for tok in out.split():
        if tok.strip().isdigit() and tok.strip() != own_pid:
            return True
    return False


def _default_pgrep_runner_worker(pattern):
    """``pgrep -f <pattern>`` across ALL users.  Returns stdout; on any error
    returns the fail-safe sentinel ``PGREP-ERROR`` so an unknown state is
    treated as "process live" → rung skips."""
    try:
        r = subprocess.run(["pgrep", "-f", pattern],
                           capture_output=True, text=True, timeout=10)
        return r.stdout or ""
    except Exception:
        return "PGREP-ERROR"


# --- session scratch discovery (#980 fix 3) ------------------------------- #
# Unit of reclaim: a SESSION dir ``/tmp/claude-<uid>/<cwd-key>/<session-id>/``.
# A session dir is reclaimable IFF ALL THREE:
#   1. No process has cwd or open fd inside it (_target_in_live_use → False)
#   2. No live claude process for the transcript
#      ``~/.claude/projects/<cwd-key>/<session-id>.jsonl``
#   3. The transcript's last write is older than ``transcript_age_hours``
# Design correction (#980 supervisor escalation): maintenance never damages
# running work — "old" is not proof; liveness is.

_SESSION_UUID_RE = _re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def discover_session_scratch(tmp_dir="/tmp", uid=None, now=None,
                             proc_dir=None, transcript_age_hours=24,
                             home=None, dir_stats_fn=None):
    """Discover reclaimable dead-session scratch dirs under
    ``/tmp/claude-<uid>/``.

    Returns rows ``{cls:"session-scratch", path, bytes, kind, reason}``.
    A row with ``kind="delete"`` is a dead-session dir safe to reclaim;
    ``kind="skip"`` is a live or undeterminable session (never touched).

    Parameters
    ----------
    tmp_dir : str
        The ``/tmp`` root (or a test-injected temp dir).
    uid : int or None
        The user id (default: ``os.getuid()``).
    now : float or None
        Current time (default: ``time.time()``).
    proc_dir : str or None
        ``/proc`` root for liveness checks (test seam).
    transcript_age_hours : int
        Transcript must be older than this to be reclaimable.
    home : str or None
        ``$HOME`` override (test seam for transcript lookup).
    dir_stats_fn : callable or None
        ``du``-like size function (test seam).
    """
    now = time.time() if now is None else now
    uid = os.getuid() if uid is None else uid
    home = home or os.path.expanduser("~")
    scratch_root = os.path.join(tmp_dir, "claude-%d" % uid)
    if not os.path.isdir(scratch_root):
        return []
    out = []
    try:
        cwd_keys = os.listdir(scratch_root)
    except OSError:
        return []
    for cwd_key in sorted(cwd_keys):
        cwd_key_path = os.path.join(scratch_root, cwd_key)
        if not os.path.isdir(cwd_key_path) or os.path.islink(cwd_key_path):
            continue
        try:
            sessions = os.listdir(cwd_key_path)
        except OSError:
            continue
        for session_id in sorted(sessions):
            if not _SESSION_UUID_RE.match(session_id):
                continue
            session_path = os.path.join(cwd_key_path, session_id)
            if not os.path.isdir(session_path) or os.path.islink(session_path):
                continue
            row = _classify_session_scratch(
                session_path, cwd_key, session_id, home, now,
                proc_dir, transcript_age_hours, dir_stats_fn)
            out.append(row)
    return out


def _classify_session_scratch(session_path, cwd_key, session_id, home, now,
                              proc_dir, transcript_age_hours, dir_stats_fn):
    """Classify one session scratch dir as reclaimable or skip."""
    from watchdog.disk_guard import _safe_dir_size

    nbytes = _safe_dir_size(session_path, dir_stats_fn)
    base = {"cls": "session-scratch", "path": session_path, "bytes": nbytes}

    # Check 1: any process with cwd or open fd inside this dir?
    try:
        from cli_target_purge import _target_in_live_use
        if _target_in_live_use(session_path,
                               proc_dir=proc_dir if proc_dir else None):
            return {**base, "kind": "skip",
                    "reason": "in live use (cwd/fd inside) — kept"}
    except Exception as e:
        return {**base, "kind": "skip",
                "reason": "liveness check failed: %r — kept (fail-safe)" % e}

    # Check 2: transcript exists and is old enough?
    transcript = os.path.join(
        home, ".claude", "projects", cwd_key, "%s.jsonl" % session_id)
    if os.path.isfile(transcript):
        try:
            mtime = os.stat(transcript).st_mtime
            age_hours = (now - mtime) / 3600.0
            if age_hours < transcript_age_hours:
                return {**base, "kind": "skip",
                        "reason": "transcript too recent (%.1fh < %dh) — kept"
                        % (age_hours, transcript_age_hours)}
        except OSError:
            return {**base, "kind": "skip",
                    "reason": "transcript stat failed — kept (fail-safe)"}
    else:
        # No transcript = session might have been created by a non-standard
        # path.  If no process uses the dir (check 1 passed), and there is
        # no transcript at all, the session is dead.  Still require a minimum
        # age on the dir itself (24h).
        try:
            dir_mtime = os.stat(session_path).st_mtime
            dir_age_hours = (now - dir_mtime) / 3600.0
            if dir_age_hours < transcript_age_hours:
                return {**base, "kind": "skip",
                        "reason": "no transcript and dir too recent (%.1fh < %dh) — kept"
                        % (dir_age_hours, transcript_age_hours)}
        except OSError:
            return {**base, "kind": "skip",
                    "reason": "dir stat failed — kept (fail-safe)"}

    # All three checks passed: this session is provably dead.
    return {**base, "kind": "delete", "reason": None}
