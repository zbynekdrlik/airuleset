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

# Only notify on end_turn (Claude truly idle, waiting for user)
# Skip tool_use (Claude still working), max_tokens, stop_sequence
STOP_REASON=$(echo "$INPUT" | jq -r '.stop_reason // empty' 2>/dev/null || echo "")
[ "$STOP_REASON" != "end_turn" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")

# Derive project name from git root or cwd
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

# Machine name
MACHINE=$(hostname -s 2>/dev/null || echo "unknown")

# Extract last assistant message from transcript JSONL
MESSAGE="No message available"
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
    MESSAGE=$(tac "$TRANSCRIPT" 2>/dev/null \
        | grep -m1 '"role":"assistant"' 2>/dev/null \
        | python3 -c "
import sys, json
try:
    line = sys.stdin.readline()
    d = json.loads(line)
    # Handle different message formats
    msg = d.get('message', {})
    content = msg.get('content', '')
    if isinstance(content, list):
        texts = [c.get('text','') for c in content if c.get('type')=='text']
        content = ' '.join(texts)
    print(content[:500])
except:
    print('No message available')
" 2>/dev/null || echo "No message available")
fi

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
