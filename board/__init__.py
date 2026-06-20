"""Autopilot Board — central live tracking + review-gate audit (stdlib only)."""
import os

PORT = 8787
# dev1's TAILSCALE IP. Tailscale IPs (100.64.0.0/10) are assigned per-node by the
# coordination server and stay stable even when the underlying LAN switches (the
# user moves to a fallback network for external events), so the board stays
# reachable across network changes — unlike the old DHCP LAN IP. Override per host
# with the BOARD_HOST env var. (Was 10.77.9.21 until the LAN renumber; see #1.)
BOARD_HOST_IP = os.environ.get("BOARD_HOST", "100.104.8.125")
REPORT_TIMEOUT = 2          # seconds, reporter connect+read
CIRCUIT_BREAKER_S = 60      # skip network this long after a failure
FLUSH_CAP = 200             # max queued events flushed per reporter invocation
QUEUE_MAX_BYTES = 5 * 1024 * 1024
QUEUE_TTL_S = 6 * 3600      # drop queued events older than this on flush
QUEUE_ITEM_TTL_S = 14 * 24 * 3600  # planned "Up next" rows expire after this
BODY_MAX = 64 * 1024        # max POST body
EVENT_CAP_PER_RUN = 500     # prune older events beyond this per run
STALE_ACTIVE_S = 8 * 60     # heartbeat threshold, active phases (board VISUAL stale)
STALE_WAIT_S = 30 * 60      # heartbeat threshold, CI/deploy waits
# Stall WATCHDOG silence threshold (device ping): a genuinely-running run/loop that
# goes silent this long has stopped abnormally → fire ONE Discord ping (the board
# daemon does this, so it works even when the agent is rate-limited / dead). Longer
# than a normal CI wait so a legit long CI doesn't trip it; WAIT phases use
# STALE_WAIT_S. Override with BOARD_WATCHDOG_S.
WATCHDOG_SILENCE_S = int(os.environ.get("BOARD_WATCHDOG_S", str(25 * 60)))
GH_POLL_FLOOR_S = 30        # min seconds between gh polls
AUTO_REFRESH_S = 10         # browser meta refresh

TERMINAL_PHASES = frozenset({"done", "stopped", "obsolete-closed"})
WAIT_PHASES = frozenset({"CI", "deploy"})
PAUSE_PHASES = frozenset({"asking-user"})  # non-linear pause — EXEMPT from rank-monotonicity (see db._apply)

# ALL_PHASES / PHASE_RANK define display order and linear forward ranking.
# asking-user (in PAUSE_PHASES) and terminal phases are NOT part of the strict
# forward sequence — db._apply must skip the rank-monotonicity check when either
# the current or incoming phase is in PAUSE_PHASES (or TERMINAL_PHASES).
ALL_PHASES = ("validating", "version-bump", "implementing", "RED", "GREEN",
              "CI", "review", "merge", "deploy", "done", "asking-user",
              "stopped", "obsolete-closed")
PHASE_RANK = {p: i for i, p in enumerate(ALL_PHASES)}

def board_url():
    return f"http://{BOARD_HOST_IP}:{PORT}/"
