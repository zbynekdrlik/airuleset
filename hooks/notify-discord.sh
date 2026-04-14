#!/usr/bin/env bash
set -euo pipefail

# Hook: Notification (idle_prompt)
# Sends Discord DM directly via the bot API when Claude is truly idle.
# Skips if background shells are running (CI monitoring etc.).
# Fire-and-forget — never blocks Claude (exit 0 always).

# Read bot token from the Discord channel config
BOT_TOKEN=""
if [ -f ~/.claude/channels/discord/.env ]; then
    BOT_TOKEN=$(grep -E '^DISCORD_BOT_TOKEN=' ~/.claude/channels/discord/.env | cut -d'=' -f2- | tr -d '"' | tr -d "'")
fi
[ -z "$BOT_TOKEN" ] && exit 0

# Read DM channel ID from env file
DM_CHANNEL_ID=""
if [ -f ~/.claude/channels/discord/.env ]; then
    DM_CHANNEL_ID=$(grep -E '^DISCORD_DM_CHANNEL_ID=' ~/.claude/channels/discord/.env | cut -d'=' -f2- | tr -d '"' | tr -d "'" | tr -d '\n')
fi
[ -z "$DM_CHANNEL_ID" ] && exit 0

command -v jq &>/dev/null || exit 0

INPUT=$(cat)

# Debug log (kept enabled while we tune)
echo "$INPUT" >> /tmp/claude-notify-debug.log

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

# Skip if background Claude shells are running in this project's directory
# (CI monitoring, sleep+gh run, etc. — Claude is NOT truly waiting for user)
if [ -n "$CWD" ]; then
    for pid in $(pgrep -f "shell-snapshots" 2>/dev/null); do
        SHELL_CWD=$(readlink /proc/$pid/cwd 2>/dev/null || echo "")
        if [ "$SHELL_CWD" = "$CWD" ]; then
            echo "SKIPPED (bg shell PID=$pid CWD=$SHELL_CWD)" >> /tmp/claude-notify-debug.log
            exit 0
        fi
    done
fi

PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

MACHINE=$(hostname -s 2>/dev/null || echo "unknown")
PROJECT_UPPER=$(echo "$PROJECT" | tr '[:lower:]' '[:upper:]')

CONTENT="**${PROJECT_UPPER}** waiting (${MACHINE})"

echo "SENT: $PROJECT ($MACHINE) → DM channel $DM_CHANNEL_ID" >> /tmp/claude-notify-debug.log

# Send DM directly via Discord REST API (fire and forget, background)
(curl -s --max-time 5 -X POST \
    "https://discord.com/api/v10/channels/${DM_CHANNEL_ID}/messages" \
    -H "Authorization: Bot ${BOT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg content "$CONTENT" '{content: $content}')" \
    >/tmp/claude-notify-debug.log 2>&1) &

exit 0
