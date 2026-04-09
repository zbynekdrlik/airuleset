#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Sends a Discord notification via n8n webhook when Claude stops.
# Fire-and-forget — never blocks Claude (exit 0 always).

# Source env file (bashrc has interactive guard, hooks run non-interactively)
[ -f ~/.claude/env ] && source ~/.claude/env

WEBHOOK_URL="${CLAUDE_DISCORD_WEBHOOK_URL:-}"
[ -z "$WEBHOOK_URL" ] && exit 0

command -v jq &>/dev/null || exit 0

# Read stdin JSON from Claude Code
INPUT=$(cat)

# Skip if background tasks still running (e.g., CI monitoring shell)
# stop_hook_active=true means Claude has async work in progress — not truly idle
ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo "false")
[ "$ACTIVE" = "true" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")

# Derive project name from git root or cwd
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

# Machine name
MACHINE=$(hostname -s 2>/dev/null || echo "unknown")

# Last assistant message is provided directly in stdin
MESSAGE=$(echo "$INPUT" | jq -r '.last_assistant_message // "No message available"' 2>/dev/null || echo "No message available")
# Truncate to 500 chars
MESSAGE="${MESSAGE:0:500}"

# Fire and forget — curl in background, never wait
curl -s --max-time 5 -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$(jq -n \
        --arg project "$PROJECT" \
        --arg machine "$MACHINE" \
        --arg message "$MESSAGE" \
        --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg session "$SESSION_ID" \
        '{project: $project, machine: $machine, message: $message, timestamp: $timestamp, session_id: $session}'
    )" &>/dev/null &

exit 0
