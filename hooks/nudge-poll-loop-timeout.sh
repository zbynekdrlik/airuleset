#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — airuleset #90.
#
# ci-monitoring.md's bounded poll-loop shape (`for i in $(seq ...); do
# <poll>; sleep N; done`) is only safe against the harness's own SIGTERM if
# the Bash TOOL CALL's own `timeout` parameter is raised near its 600000ms
# cap — that parameter is easy to forget, since the loop body reads as
# complete without it. Live-hit (gk subagent, 2026-07-26): a poll written
# exactly per that shape, with no `timeout` param raised, was SIGTERM'd
# (exit 143) at the harness's OBSERVED default (120000ms / 2 minutes),
# mid-poll, with NO output — three tool calls where one was intended,
# because the dead call had to be silently retried before the timeout was
# finally set correctly.
#
# This hook NEVER blocks — the poll itself must always be allowed to run
# (the ticket's own explicit requirement). It only ever prints a
# corrective NUDGE via `hookSpecificOutput.additionalContext` (the same
# non-blocking injection shape `inject-situational-rule.sh` already uses)
# so the reminder actually reaches the model's context on THIS call,
# rather than depending on prose the model has already forgotten once.
#
# Detection is intentionally generic, not `gh run view`-specific (#90 point
# 3: other waiting loops — build, deploy, remote — deserve the same
# nudge): a command containing an actual `sleep` call AND a loop-body
# `done` closer is a bounded poll/wait loop shape. A one-shot
# `sleep 5 && curl ...` has no `done` and is not this pattern.
#
# Threshold: nudge when the call's own `tool_input.timeout` is missing or
# below AIRULESET_POLL_NUDGE_MIN_MS (default 400000 — comfortably above
# the harness's observed 120000ms default, comfortably below the 600000ms
# cap ci-monitoring.md recommends).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

echo "$CMD" | grep -qE '(^|[^A-Za-z0-9_])sleep[[:space:]]' || exit 0
echo "$CMD" | grep -qE '(^|[^A-Za-z0-9_])done([^A-Za-z0-9_]|$)' || exit 0

TIMEOUT_MS=$(echo "$INPUT" | jq -r '.tool_input.timeout // empty' 2>/dev/null || echo "")
MIN_MS="${AIRULESET_POLL_NUDGE_MIN_MS:-400000}"
case "$MIN_MS" in ''|*[!0-9]*) MIN_MS=400000 ;; esac

NEEDS_NUDGE=0
case "$TIMEOUT_MS" in
    ''|*[!0-9]*) NEEDS_NUDGE=1 ;;
    *) [ "$TIMEOUT_MS" -lt "$MIN_MS" ] && NEEDS_NUDGE=1 ;;
esac

[ "$NEEDS_NUDGE" = "1" ] || exit 0

MSG="NUDGE (not a block — this poll WILL run, #90): this Bash call looks like a bounded poll/wait loop (contains sleep + done) but its own \`timeout\` tool parameter is ${TIMEOUT_MS:-unset}. The harness's own default (observed: 120000ms) will SIGTERM a multi-minute loop mid-poll with NO output, costing extra tool-call round-trips instead of the one call ci-monitoring.md intends. Set the Bash tool's own \`timeout\` parameter near its 600000ms cap on this call — and, per ci-monitoring.md, also raise AIRULESET_POLL_BUDGET_S to match so the loop's own SECONDS-based self-bound uses the extra budget instead of giving up early at its conservative default."

python3 - "$MSG" <<'PYEOF' 2>/dev/null || exit 0
import json
import sys

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": sys.argv[1],
    }
}))
PYEOF

exit 0
