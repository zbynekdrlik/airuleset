#!/usr/bin/env bash
# Stop hook: skeleton heartbeat+phase for the current autopilot run.
# No-op unless AUTOPILOT_RUN set. Never blocks: backgrounds + always exit 0.
set -u
[ -n "${AUTOPILOT_RUN:-}" ] || exit 0
( python3 "$HOME/devel/airuleset/airuleset.py" report \
    --run "$AUTOPILOT_RUN" --heartbeat ${AUTOPILOT_PHASE:+--phase "$AUTOPILOT_PHASE"} \
    >/dev/null 2>&1 & ) || true
exit 0
