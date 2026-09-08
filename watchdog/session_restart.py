"""#947 Job 46 — SESSION-RESTART-ON-DEGRADATION.

Claude Code's node process leaks memory proportionally to session uptime
(measured: 2.5 d -> 940 MB RSS + 210 MB swap, 87 % CPU idle, 10 s/keystroke).
A ``claude --continue`` relaunch restores performance instantly — the session
state lives on disk (transcript + ``~/.claude/sessions``), only the process is
sick.  The watchdog today acts only on a DEAD process (``resurrect.py``, census
over ``roster.dead_entries``) — this job proactively restarts DEGRADED-but-ALIVE
sessions.

Two-phase state machine (mirrors resurrect.py's relaunch-across-sweeps pattern):

Phase 1 — EXIT.  Thresholds met + all gates pass -> type ``/exit`` + Enter into
the idle pane.  CC exits cleanly, the pane falls to a shell.  Record
``phase: "exit-sent"``, the PID, and the cwd.

Phase 2 — RELAUNCH.  Next sweep: PID gone + pane at bare-idle shell + cwd
matches + recent-human veto passes -> type ``claude --continue`` + Enter
(``resurrect.relaunch``).  The existing dark-watch (job 20) detects the
``Goal set:`` marker in the restored transcript and re-arms.

Abandon.  PID still alive after ``EXIT_CONFIRM_SWEEPS`` -> log
``exit-not-taken``, abandon, anchor the 6 h cooldown.

Safety (#486 structured state, no pane-render heuristic):
- ``action_enabled()`` opt-in flag (default OFF — the supervisor enables it
  fleet-wide after live verification, exactly as resurrect's mode 5).
- Conjunctive gate cascade: idle prompt + no bg agents + no busy-waiting +
  no compacting + no recent human + 6 h cooldown + opt-in + not dry-run.
- Every verdict is a ``session-restart:`` decision-log line with measurements
  and the reason for defer/act.
- Never raises (the module contract mirrors resurrect.py).

Module-import safety: imports nothing from ``watchdog`` at top level; all
cross-module calls go through ``import watchdog`` + ``watchdog.<name>()``
call-time, keeping monkeypatch seams effective.
"""

import os

# --- Thresholds (owner-visible constants) ---------------------------------- #

# RSS + VmSwap combined (a swapped-out process shows LOWER RSS; the incident's
# 210 MB swap would hide under a pure-RSS threshold).  700 MB in KB.
SESSION_RESTART_RSS_SWAP_KB = 700 * 1024  # 716800

# Maximum uptime before proactive restart.  24 hours in seconds.
SESSION_RESTART_UPTIME_S = 24 * 3600  # 86400

# Minimum gap between restart ATTEMPTS for the same pane.  6 hours in seconds.
SESSION_RESTART_COOLDOWN_S = 6 * 3600  # 21600

# How many sweeps to wait for the /exit to take effect (PID to disappear)
# before abandoning the restart attempt.
EXIT_CONFIRM_SWEEPS = 3

# The /exit command that cleanly shuts down Claude Code.
_EXIT_CMD = "/exit"

# The relaunch command — ``claude --continue`` restores the same session.
_LAUNCH_CONTINUE = "claude --continue"


def action_enabled():
    """The opt-in flag (mirrors resurrect.action_enabled): the live /exit +
    relaunch keystroke fires ONLY when ``AIRULESET_SESSION_RESTART_ACTION``
    is truthy.  Default OFF — the supervisor enables it fleet-wide after
    verifying the restart decisions in the journal + a real kill->comeback on
    a live box."""
    return os.environ.get("AIRULESET_SESSION_RESTART_ACTION",
                          "").strip().lower() in ("1", "true", "yes", "on")


def read_health(pid, proc_read_fn):
    """Read RSS + VmSwap (combined, KB) for process ``pid`` from /proc.
    Returns ``rss_swap_kb`` (int) or ``None`` on any read/parse failure
    (fail-safe: an unreadable /proc = not degraded, never a restart).
    Never raises.

    ``proc_read_fn`` is ``watchdog.tmux_io._proc_read`` (injectable for tests).
    """
    try:
        status = proc_read_fn("/proc/%s/status" % pid)
        if not status:
            return None
        rss_kb = None
        swap_kb = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    rss_kb = int(parts[1])
            elif line.startswith("VmSwap:"):
                parts = line.split()
                if len(parts) >= 2:
                    swap_kb = int(parts[1])
        if rss_kb is None:
            return None
        return rss_kb + swap_kb
    except Exception:
        return None


def _is_degraded(rss_swap_kb, uptime_s):
    """PURE: True if either threshold is breached.  None inputs -> False."""
    if rss_swap_kb is not None and rss_swap_kb >= SESSION_RESTART_RSS_SWAP_KB:
        return True
    if uptime_s is not None and uptime_s >= SESSION_RESTART_UPTIME_S:
        return True
    return False


def decide(entry, rss_swap_kb, uptime_s, pane_idle, bg_live, busy_waiting,
           compacting, human_recent, human_reason, enabled, dry_run, now,
           pane_id="?"):
    """PURE decision for one pane.  Returns ``(log_line, action)`` where
    ``action`` is one of:

    - ``None``    — no action (not degraded, or a gate deferred it)
    - ``"exit"``  — type /exit into the pane (phase 1)
    - ``"relaunch"`` — type ``claude --continue`` (phase 2, after exit confirmed)

    ``entry`` is the per-pane state dict (mutable — the caller persists it).
    ``pane_id`` is for the log line only.

    Gate order matches the docstring cascade: degraded -> phase check ->
    idle -> bg -> busy -> compacting -> human -> cooldown -> enabled -> dry-run.
    Every denied gate gets its own log line.

    Never raises (the module contract).
    """
    if not isinstance(entry, dict):
        entry = {}

    phase = entry.get("phase")

    # -- Phase 2: waiting for PID to disappear after /exit ------------------- #
    if phase == "exit-sent":
        return _decide_relaunch_phase(entry, human_recent, human_reason,
                                      enabled, dry_run, now, pane_id)

    # -- Phase 1: evaluate whether to /exit ---------------------------------- #
    if not _is_degraded(rss_swap_kb, uptime_s):
        return ("session-restart: %s not-degraded rss_swap=%s uptime=%s"
                % (pane_id, rss_swap_kb, uptime_s), None)

    # Degraded — run the gate cascade.
    if not pane_idle:
        return ("session-restart: %s defer:not-idle rss_swap=%s uptime=%s"
                % (pane_id, rss_swap_kb, uptime_s), None)
    if bg_live:
        return ("session-restart: %s defer:bg-agents-live rss_swap=%s uptime=%s"
                % (pane_id, rss_swap_kb, uptime_s), None)
    if busy_waiting:
        return ("session-restart: %s defer:busy-waiting rss_swap=%s uptime=%s"
                % (pane_id, rss_swap_kb, uptime_s), None)
    if compacting:
        return ("session-restart: %s defer:compacting rss_swap=%s uptime=%s"
                % (pane_id, rss_swap_kb, uptime_s), None)
    if human_recent:
        return ("session-restart: %s defer:recent-human (%s) rss_swap=%s "
                "uptime=%s" % (pane_id, human_reason or "?",
                               rss_swap_kb, uptime_s), None)

    # Cooldown gate.
    last_ts = entry.get("last_restart_ts")
    if isinstance(last_ts, (int, float)) and (now - last_ts) < SESSION_RESTART_COOLDOWN_S:
        remaining = int(SESSION_RESTART_COOLDOWN_S - (now - last_ts))
        return ("session-restart: %s defer:cooldown (%ds remaining) rss_swap=%s "
                "uptime=%s" % (pane_id, remaining, rss_swap_kb, uptime_s), None)

    if not enabled:
        return ("session-restart: %s would-exit rss_swap=%s uptime=%s "
                "-- disabled (AIRULESET_SESSION_RESTART_ACTION off)"
                % (pane_id, rss_swap_kb, uptime_s), None)
    if dry_run:
        return ("session-restart: %s would-exit rss_swap=%s uptime=%s -- dry-run"
                % (pane_id, rss_swap_kb, uptime_s), None)

    # All gates passed — record phase 1.
    entry["phase"] = "exit-sent"
    entry["exit_ts"] = now
    entry["exit_sweeps"] = 0
    return ("session-restart: %s -> exit rss_swap=%s uptime=%s"
            % (pane_id, rss_swap_kb, uptime_s), "exit")


def _decide_relaunch_phase(entry, human_recent, human_reason,
                            enabled, dry_run, now, pane_id):
    """Phase-2 sub-decision: the /exit was sent in a prior sweep.  Check
    whether the PID is gone (the caller sets ``entry["pid_gone"]``) and
    whether the pane is at a bare-idle shell ready for relaunch.

    Returns ``(log_line, action)`` — same contract as ``decide``.
    """
    entry["exit_sweeps"] = entry.get("exit_sweeps", 0) + 1

    if not entry.get("pid_gone"):
        if entry["exit_sweeps"] >= EXIT_CONFIRM_SWEEPS:
            # Abandon — the /exit didn't take effect.
            entry["phase"] = None
            entry["last_restart_ts"] = now  # anchor the cooldown anyway
            return ("session-restart: %s exit-not-taken after %d sweeps "
                    "-- abandoning" % (pane_id, entry["exit_sweeps"]), None)
        return ("session-restart: %s exit-sent, waiting for PID to disappear "
                "(sweep %d/%d)" % (pane_id, entry["exit_sweeps"],
                                   EXIT_CONFIRM_SWEEPS), None)

    # PID is gone.  Check if pane is ready for relaunch.
    if not entry.get("pane_bare_idle"):
        return ("session-restart: %s exit confirmed (PID gone) but pane not "
                "at bare idle -- deferring relaunch" % pane_id, None)

    if human_recent:
        return ("session-restart: %s exit confirmed but defer:recent-human "
                "(%s) -- deferring relaunch"
                % (pane_id, human_reason or "?"), None)

    if not enabled:
        return ("session-restart: %s would-relaunch -- disabled "
                "(AIRULESET_SESSION_RESTART_ACTION off)" % pane_id, None)
    if dry_run:
        return ("session-restart: %s would-relaunch -- dry-run" % pane_id,
                None)

    # All clear — relaunch.
    entry["phase"] = "relaunched"
    entry["last_restart_ts"] = now
    return ("session-restart: %s -> relaunch (claude --continue)" % pane_id,
            "relaunch")


def execute_exit(pane_id, run):
    """Type ``/exit`` + Enter into the pane.  Returns True on success.
    Never raises."""
    if run is None or not pane_id:
        return False
    try:
        run(["tmux", "send-keys", "-t", str(pane_id), _EXIT_CMD, "Enter"])
        return True
    except Exception:
        return False


def execute_relaunch(pane_id, run):
    """Type ``claude --continue`` + Enter into the pane (a bare shell).
    Returns True on success.  Never raises."""
    if run is None or not pane_id:
        return False
    try:
        run(["tmux", "send-keys", "-t", str(pane_id), _LAUNCH_CONTINUE,
             "Enter"])
        return True
    except Exception:
        return False
