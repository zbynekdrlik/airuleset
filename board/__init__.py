"""Autopilot Board — central live tracking + review-gate audit (stdlib only)."""
import os

PORT = 8787
BOARD_HOST_IP = os.environ.get("BOARD_HOST", "10.77.9.21")
REPORT_TIMEOUT = 2          # seconds, reporter connect+read
CIRCUIT_BREAKER_S = 60      # skip network this long after a failure
FLUSH_CAP = 200             # max queued events flushed per reporter invocation
QUEUE_MAX_BYTES = 5 * 1024 * 1024
QUEUE_TTL_S = 6 * 3600      # drop queued events older than this on flush
BODY_MAX = 64 * 1024        # max POST body
EVENT_CAP_PER_RUN = 500     # prune older events beyond this per run
STALE_ACTIVE_S = 8 * 60     # heartbeat threshold, active phases
STALE_WAIT_S = 30 * 60      # heartbeat threshold, CI/deploy waits
GH_POLL_FLOOR_S = 30        # min seconds between gh polls
AUTO_REFRESH_S = 10         # browser meta refresh

TERMINAL_PHASES = frozenset({"done", "stopped", "obsolete-closed"})
WAIT_PHASES = frozenset({"CI", "deploy"})
ALL_PHASES = ("validating", "version-bump", "implementing", "RED", "GREEN",
              "CI", "review", "merge", "deploy", "done", "asking-user",
              "stopped", "obsolete-closed")
PHASE_RANK = {p: i for i, p in enumerate(ALL_PHASES)}

def board_url():
    return f"http://{BOARD_HOST_IP}:{PORT}/"
