#!/usr/bin/env bash
# Wrapper: run a command, fire Discord notification on completion.
# Usage: notify-on-completion.sh "command string" ["title"]
#
# Why: Claude Code's idle_prompt notification fires only while the agent session
# is active. If the agent stops while a long bg job is still running (e.g.
# `sleep 300 && gh run view <id>`), the completion never produces a Discord ping.
# This wrapper makes the completion itself fire the ping — works because Claude
# Code keeps bg shells running independently of agent stop.
#
# Recommended use: wrap any bash bg job >5min that the user cares about.
#   Bash(command: "bash ~/devel/airuleset/hooks/notify-on-completion.sh 'sleep 300 && gh run view 12345' 'CI run 12345'", run_in_background: true)

set -uo pipefail

CMD="${1:?missing command}"
TITLE="${2:-Background command finished}"

START=$(date +%s)
OUTPUT=$(bash -c "$CMD" 2>&1)
EXIT=$?
END=$(date +%s)
DURATION=$((END - START))

if [ $EXIT -eq 0 ]; then EMOJI="✅"; else EMOJI="❌"; fi

# Tail last 1500 chars of output for the Discord message body
TAIL=$(echo "$OUTPUT" | tail -c 1500)

# Read Discord bot config (same source as notify-discord.sh)
BOT_TOKEN=""
CHANNEL_ID=""
if [ -f ~/.claude/channels/discord/.env ]; then
    BOT_TOKEN=$(grep -E '^DISCORD_BOT_TOKEN=' ~/.claude/channels/discord/.env | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    CHANNEL_ID=$(grep -E '^DISCORD_NOTIFICATION_CHANNEL_ID=' ~/.claude/channels/discord/.env | cut -d'=' -f2- | tr -d '"' | tr -d "'" | tr -d '\n')
fi

if [ -n "$BOT_TOKEN" ] && [ -n "$CHANNEL_ID" ] && command -v jq &>/dev/null; then
    MACHINE=$(hostname -s 2>/dev/null || echo "unknown")
    PROJECT=$(git rev-parse --show-toplevel 2>/dev/null 2>/dev/null | xargs -I{} basename {} 2>/dev/null || basename "$PWD")
    CONTENT=$(printf '%s **%s** — %s (%s, %ss, exit %s)\n```\n%s\n```' \
        "$EMOJI" "$PROJECT" "$TITLE" "$MACHINE" "$DURATION" "$EXIT" "$TAIL")

    # Discord message limit is 2000 chars; truncate safely
    if [ ${#CONTENT} -gt 1900 ]; then
        CONTENT="${CONTENT:0:1900}..."
    fi

    curl -s --max-time 10 -X POST \
        "https://discord.com/api/v10/channels/${CHANNEL_ID}/messages" \
        -H "Authorization: Bot ${BOT_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg content "$CONTENT" '{content: $content}')" \
        >/dev/null 2>&1 || true
fi

# Replay full output to Claude (so BashOutput shows the result)
echo "$OUTPUT"
exit $EXIT
