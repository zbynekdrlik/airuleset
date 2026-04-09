#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (AskUserQuestion)
# Sends Discord notification when Claude asks the user a question.
# Fire-and-forget, exit 0 always.

[ -f ~/.claude/env ] && source ~/.claude/env

WEBHOOK_URL="${CLAUDE_DISCORD_WEBHOOK_URL:-}"
[ -z "$WEBHOOK_URL" ] && exit 0

command -v jq &>/dev/null || exit 0

INPUT=$(cat)

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")

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
        --arg message "Question — waiting for your answer" \
        --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg session "$SESSION_ID" \
        '{project: $project, machine: $machine, message: $message, timestamp: $timestamp, session_id: $session}'
    )" &>/dev/null &

exit 0
