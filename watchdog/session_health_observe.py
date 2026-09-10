"""#947 Job 46 — SESSION HEALTH OBSERVATION (passive, journal-only).

REVERSAL (owner directive 2026-09-10): "regresia je ze nieco nam pcha exit
do vsetkych targetov a ked prideme rano je vsetko seknute … Ja som vobec
taketo random existovanie target claude urcite nikdy neschvalil a som proti
tomu aby sa nieco take dialo!!!"

This module was originally ``session_restart.py`` — a two-phase state machine
that typed ``/exit`` + ``claude --continue`` into tmux panes to restart
degraded Claude Code sessions.  The action path is DELETED: no ``/exit``, no
``claude --continue``, no ``tmux send-keys``, no keystroke of any kind.

What REMAINS is the read-only health measurement (RSS+Swap from /proc, uptime)
as a journal-only observation with the SAME thresholds, so the degradation is
still visible in the journal for the owner.  The ``observe()`` function returns
log lines — never a keystroke, never Discord.
"""

# --- Thresholds (owner-visible constants, unchanged from #947) ------------- #

# RSS + VmSwap combined (KB).  700 MB.
SESSION_HEALTH_RSS_SWAP_KB = 700 * 1024  # 716800

# Maximum uptime before flagging as degraded.  24 hours in seconds.
SESSION_HEALTH_UPTIME_S = 24 * 3600  # 86400


def read_health(pid, proc_read_fn):
    """Read RSS + VmSwap (combined, KB) for process ``pid`` from /proc.
    Returns ``rss_swap_kb`` (int) or ``None`` on any read/parse failure
    (fail-safe: an unreadable /proc = not degraded).
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
    if rss_swap_kb is not None and rss_swap_kb >= SESSION_HEALTH_RSS_SWAP_KB:
        return True
    if uptime_s is not None and uptime_s >= SESSION_HEALTH_UPTIME_S:
        return True
    return False


def observe(pane_id, rss_swap_kb, uptime_s):
    """PURE observation for one pane.  Returns a log line (str).

    This is journal-only — no action, no keystroke, no Discord.  The owner
    reads the journal to see which sessions are degraded.
    """
    if _is_degraded(rss_swap_kb, uptime_s):
        return ("session-health: %s degraded rss_swap=%s uptime=%s"
                % (pane_id, rss_swap_kb, uptime_s))
    return ("session-health: %s healthy rss_swap=%s uptime=%s"
            % (pane_id, rss_swap_kb, uptime_s))
