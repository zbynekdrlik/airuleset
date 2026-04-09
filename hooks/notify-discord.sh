#!/usr/bin/env bash
set -euo pipefail

# Hook: Notification (idle_prompt)
# Sends a Discord notification via n8n webhook when Claude is idle.
# Fire-and-forget — never blocks Claude (exit 0 always).

[ -f ~/.claude/env ] && source ~/.claude/env

WEBHOOK_URL="${CLAUDE_DISCORD_WEBHOOK_URL:-}"
[ -z "$WEBHOOK_URL" ] && exit 0

command -v jq &>/dev/null || exit 0

INPUT=$(cat)

# Debug: log stdin (leave enabled until stable)
echo "$INPUT" >> /tmp/claude-notify-debug.log

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")

# Skip if background Claude shells are running in this project's directory
# Claude background shells (CI monitoring etc.) source shell-snapshots
# Their CWD is readable via /proc/PID/cwd
if [ -n "$CWD" ]; then
    for pid in $(pgrep -f "shell-snapshots" 2>/dev/null); do
        SHELL_CWD=$(readlink /proc/$pid/cwd 2>/dev/null || echo "")
        if [ "$SHELL_CWD" = "$CWD" ]; then
            exit 0
        fi
    done
fi
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")
MESSAGE=$(echo "$INPUT" | jq -r '.message // "Waiting for input"' 2>/dev/null || echo "Waiting for input")
MESSAGE="${MESSAGE:0:500}"

PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

MACHINE=$(hostname -s 2>/dev/null || echo "unknown")

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
