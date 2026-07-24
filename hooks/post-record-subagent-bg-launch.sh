#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash | Monitor | Agent) — the synchronous OWNERSHIP
# ledger for subagent-stop-check-bg-work.sh (#28/#29).
#
# The SubagentStop gate needs to know which background tasks the stopping
# subagent ITSELF launched (sibling tasks must never block it — #29), but
# the agent transcript is written ASYNC: a launch seconds before the stop
# is often not flushed yet, which let an abandoning worker slip through
# live (2026-07-24). PostToolUse fires SYNCHRONOUSLY right after the tool
# call, so recording the task id here closes the gap: every background
# launch made from SUBAGENT context (payload carries agent_id) appends its
# id to /tmp/airuleset-bgtasks-<session_id>-<agent_id>. The stop gate
# unions this ledger with the transcript for ownership and removes the
# ledger when the stop passes. Main-session launches (no agent_id) are the
# supervisor pattern — never recorded.
#
# Live payload shapes (captured 2026-07-24): tool_response is the
# structured sidecar — backgroundTaskId (Bash run_in_background), taskId
# (Monitor), isAsync+agentId (background Agent dispatch; a FOREGROUND
# dispatch has agentId too but no isAsync — not a detached task, skipped).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
[ -n "$AGENT_ID" ] || exit 0

TID=$(echo "$INPUT" | jq -r '
    .tool_response.backgroundTaskId
    // .tool_response.taskId
    // (if (.tool_response.isAsync // false)
        then .tool_response.agentId else null end)
    // empty' 2>/dev/null || echo "")
[ -n "$TID" ] || exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
echo "$TID" >> "/tmp/airuleset-bgtasks-${SESSION_ID}-${AGENT_ID}"
exit 0
