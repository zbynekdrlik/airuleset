"""gates.audit -- ONE bypass-token audit writer for the gate family (#1020).

Every gate in this family logs a one-line audit entry when a bypass token is
honored (``# airuleset:secret-ok`` on staging, ``# airuleset:test-skip-ok`` /
``[no-test:]`` on a push, ``[no-design:]`` on a commit, ``scope-gate-ok`` on a
filing). Before this module each hook re-implemented the same "mkdir -p the
audits dir, append a timestamped line" mechanism inline. This module holds that
mechanism ONCE; each adapter still composes its OWN line text so the per-hook
log FORMATS are byte-for-byte unchanged (existing log-parsing tools/tests keep
working).

This file provides the WRITER (``append_line`` + ``iso_now`` + ``project_of``)
the migrated adapters need. The ``--bypasses`` READER extension (teaching
scripts/audit_bounce_rule_updates.py to cover these per-hook CLI-token logs, not
only commit messages) is NOT in #1020 -- it is a deliberate followup, deferred
alongside the full filing-hook migration for its own reviewed lane.
"""
import datetime
import os
import subprocess


def audits_dir():
    """The fleet's fixed audit directory (~/devel/airuleset/audits) -- the same
    absolute path every hook in this family already wrote to, deliberately NOT
    relative to a worktree so a dispatched worker's bypass lands in the one
    place the metric reads."""
    return os.path.join(os.path.expanduser("~"), "devel", "airuleset", "audits")


def audit_log_path(name):
    """Absolute path of the audit log file ``name`` (e.g.
    "secret-scan-bypasses.log")."""
    return os.path.join(audits_dir(), name)


def iso_now():
    """Local-timezone ISO-8601 seconds timestamp, matching ``date -Iseconds``
    (e.g. ``2026-09-14T20:00:31+02:00``) -- the exact stamp the bash hooks
    produced."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def project_of(cwd=None):
    """The basename of the git top-level for ``cwd`` (process cwd when None),
    or "unknown" -- matching ``basename $(git rev-parse --show-toplevel ||
    echo unknown)``. Never raises."""
    args = ["git"]
    if cwd:
        args += ["-C", cwd]
    args += ["rev-parse", "--show-toplevel"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    top = (r.stdout or "").strip()
    if r.returncode != 0 or not top:
        return "unknown"
    return os.path.basename(top)


def append_line(log_name, line):
    """Append one already-composed audit ``line`` to the audit log ``log_name``,
    best-effort: mkdir -p the audits dir first, never raise (an unwritable
    ~/devel just means the audit entry is lost, never that the gate fails).
    Returns True on success. The caller owns the line's FORMAT so per-hook logs
    stay byte-for-byte identical."""
    path = audit_log_path(log_name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
        return True
    except OSError:
        return False
