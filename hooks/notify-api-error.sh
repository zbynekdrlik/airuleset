#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — detect a turn that ENDED ON A REAL CLAUDE CODE API ERROR and fire
# ONE Discord ping with the ACTUAL error text (@mentioning the tmux owner).
#
# This is the CONCRETE replacement for the removed board-silence "stall watchdog"
# (which false-positived: a worker legitimately working a long phase without
# reporting looked identical to a dead one). Claude Code marks a genuine API error
# with `isApiErrorMessage` in the transcript and ends the turn on it, so the Stop
# payload's `last_assistant_message` IS the error (e.g. "API Error: Server is
# temporarily limiting requests · Rate limited"). We notify on THAT — a real event,
# never a guess — so there are no false positives.
#
# Selectivity: airuleset.py notify --api-error only sends when the text actually
# matches an API-error pattern (a normal turn → nothing), and dedups one ping per
# distinct error per session (a wedge repeating the same error pings once).
#
# Silent + non-blocking: writes nothing to stdout, always exit 0, so it never
# interferes with the other Stop gates.

INPUT=$(cat)

MSG=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

# Project name from the repo root (or cwd basename) — for the ping header.
PROJECT="unknown"
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null \
              | xargs basename 2>/dev/null || basename "$CWD")
fi

# airuleset.py is found relative to THIS hook (hooks/..), not via $HOME, so it
# works regardless of where the repo lives.
AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"

# Fire-and-forget: notify --api-error decides if it's really an API error, composes
# the Slovak ping, @mentions the owner, dedups, and sends. Backgrounded so the Stop
# pipeline never waits on the network.
( python3 "$AIRULESET_PY" notify --api-error \
    --text "$MSG" --session "$SID" --project "$PROJECT" \
    >/dev/null 2>&1 & ) || true

exit 0
