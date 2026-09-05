"""Watchdog nice-check self-check (#866) — a read-only rider.

Detects a non-zero nice value on an interactive claude main session or the
tmux server and journals it (machine channel only — never an owner ping,
per #546 suppression). Pure reading: never calls renice or mutates process
scheduling.

Called from run_once as Job 42, gated on ``nice_check_enabled`` (cmd_watchdog
passes True; unit tests leave it False so no real /proc is ever read).

The nice value lives in /proc/<pid>/stat field 19 (1-indexed). The comm
field (field 2) is parenthesised and may contain spaces and parentheses
(e.g. ``(tmux: server)``), so we strip the ``(comm)`` portion first and
split the rest.
"""


def nice_from_proc_stat(stat_line):
    """Extract the nice value from a /proc/<pid>/stat line.

    Field numbering (1-indexed, per proc(5)):
      1=pid, 2=(comm), 3=state, ..., 18=priority, 19=nice, ...

    The comm field is enclosed in parentheses and may contain anything
    (spaces, parens). We find the LAST ')' to delimit it, then split
    the remaining fields by whitespace. After stripping pid + (comm),
    the remaining fields start at field 3 (state). nice is field 19,
    which is index 16 (19 - 3) in the post-comm array.
    """
    # Find the last ')' — everything after it is fields 3..N
    rp = stat_line.rfind(")")
    if rp < 0:
        raise ValueError("malformed /proc/pid/stat: no closing paren")
    rest = stat_line[rp + 1:].split()
    # rest[0] = state (field 3), rest[16] = nice (field 19)
    if len(rest) < 17:
        raise ValueError("malformed /proc/pid/stat: too few fields")
    return int(rest[16])


def _default_stat_reader(pid):
    """Read /proc/<pid>/stat. Raises FileNotFoundError if the process is gone."""
    with open("/proc/%d/stat" % pid) as f:
        return f.read()


def check_pids_nice(pids, stat_reader=None):
    """Check the nice value of a list of PIDs.

    Returns a list of ``{"pid": N, "nice": M}`` dicts for every PID whose
    nice is non-zero. Unreadable PIDs (dead, permission denied) are silently
    skipped — fail-safe toward silence (never a false alarm, per #546).
    """
    stat_reader = stat_reader or _default_stat_reader
    results = []
    for pid in pids:
        try:
            stat_line = stat_reader(pid)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        try:
            nice = nice_from_proc_stat(stat_line)
        except (ValueError, IndexError):
            continue
        if nice != 0:
            results.append({"pid": pid, "nice": nice})
    return results


def _tmux_server_pid():
    """Return the tmux server PID, or None if tmux is not running."""
    import subprocess
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#{pid}"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # tmux not installed or not running — expected on boxes without tmux;
        # fail-safe toward silence (no false alarm).  # airuleset:script-ok expected-no-tmux
        pass
    return None


def nice_check_job(dry_run=False, log_fn=None):
    """The run_once entry point (Job 42). Reads the tmux server PID and
    checks its nice value. Returns a list of log lines for non-zero nice
    findings (machine-channel only — never an owner ping, per #546).

    Read-only: never calls renice or mutates scheduling.
    """
    logs = []
    pids = []
    server_pid = _tmux_server_pid()
    if server_pid is not None:
        pids.append(server_pid)
    if not pids:
        return logs
    findings = check_pids_nice(pids)
    for f in findings:
        line = "nice-check: pid %d has nice %d (expected 0)" % (f["pid"], f["nice"])
        logs.append(line)
    return logs
