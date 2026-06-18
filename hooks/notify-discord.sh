#!/usr/bin/env bash
set -euo pipefail

# Hook: Notification (idle_prompt) — sends the PENDING Discord notification.
#
# Mobile-app model (paired with notify-discord-pending.sh on the Stop event):
# fires ONLY when the user is genuinely idle/away AND the last turn ended with a
# ❓ NEEDS YOU (a real question) or ✅ DONE (fully finished). On ⏳ WORKING / no
# marker the pending file was cleared, so NOTHING is sent — no "PROJECT waiting"
# spam on every idle.
#
# Fire-and-forget — never blocks Claude (exit 0 always).
# DISCORD_NOTIFY_DRYRUN=1 → print the would-send line to stdout (used by tests).
# DISCORD_NOTIFY_DEBUG=1  → append a debug line to a 0600 per-user log (default off).

INPUT=$(cat)

dbg() {
    [ "${DISCORD_NOTIFY_DEBUG:-0}" = "1" ] || return 0
    local log="${XDG_RUNTIME_DIR:-/tmp}/claude-notify-debug.log"
    ( umask 077; printf '%s\n' "$*" >> "$log" ) 2>/dev/null || true
}

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-'); [ -z "$SID" ] && SID="unknown"
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
PENDING="/tmp/claude-discord-pending-${SID}"

# Nothing pending → the last turn was ⏳ WORKING or unmarked → send NOTHING.
[ -s "$PENDING" ] || { dbg "SKIPPED (nothing pending for $SID)"; exit 0; }

BODY=$(cat "$PENDING")
EMOJI=$(printf '%s' "$BODY" | grep -oE "❓|✅" | head -1 || true)
TEXT=$(printf '%s' "$BODY" | sed -E 's/^(❓|✅)[[:space:]]*//')

# A ❓ NEEDS YOU is the highest-priority "your turn" event — Claude is genuinely
# blocked on the user — so it ALWAYS pings, even if a background monitor shell is
# alive in this cwd. The bg-shell skip applies ONLY to ✅ (a "done" claim while a
# shell is still running in the project dir is likely intermediate → defer, and
# leave the pending file so a later idle retries once the shell exits).
if [ "$EMOJI" != "❓" ] && [ -n "$CWD" ]; then
    for pid in $(pgrep -f "shell-snapshots" 2>/dev/null || true); do
        SHELL_CWD=$(readlink "/proc/$pid/cwd" 2>/dev/null || echo "")
        if [ "$SHELL_CWD" = "$CWD" ]; then
            dbg "DEFERRED ✅ (bg shell PID=$pid CWD=$SHELL_CWD)"
            exit 0
        fi
    done
fi

PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

CONTENT="${EMOJI} ${PROJECT} · ${TEXT}"

# Consume the pending file so re-idle does not re-send.
rm -f "$PENDING" 2>/dev/null || true

if [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ]; then
    printf '%s\n' "$CONTENT"
    exit 0
fi

# Read bot token + notification channel from the Discord channel config.
ENVF=~/.claude/channels/discord/.env
BOT_TOKEN=""
CHANNEL_ID=""
if [ -f "$ENVF" ]; then
    BOT_TOKEN=$(grep -E '^DISCORD_BOT_TOKEN=' "$ENVF" | cut -d'=' -f2- | tr -d "\"'" | tr -d '\r\n')
    CHANNEL_ID=$(grep -E '^DISCORD_NOTIFICATION_CHANNEL_ID=' "$ENVF" | cut -d'=' -f2- | tr -d "\"'" | tr -d '\r\n')
fi
[ -z "$BOT_TOKEN" ] && exit 0
[ -z "$CHANNEL_ID" ] && exit 0
command -v jq &>/dev/null || exit 0

dbg "SENT: $PROJECT ($SID) → $CHANNEL_ID :: $CONTENT"

(curl -s --max-time 5 -X POST \
    "https://discord.com/api/v10/channels/${CHANNEL_ID}/messages" \
    -H "Authorization: Bot ${BOT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg content "$CONTENT" '{content: $content}')" \
    >/dev/null 2>&1) &

exit 0
