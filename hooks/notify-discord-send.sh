#!/usr/bin/env bash
set -euo pipefail

# Shared Discord SEND path — the single place that composes the structured device
# line and delivers it. Both notify hooks call this so the curl/compose logic
# lives in ONE file (no patchwork duplication):
#   - notify-discord-pending.sh (Stop)  → fires it IMMEDIATELY on ❓ NEEDS YOU
#     (the user is blocked on us → ping now; do NOT wait for an `idle_prompt`
#     event, which Claude Code emits unreliably over tmux/SSH).
#   - notify-discord.sh (Notification: idle_prompt) → fires it for a pending ✅
#     when the user is genuinely idle/away (the unchanged mobile-app model for
#     "done" — a finished turn is less urgent than a question).
#
# Inputs (env):
#   ND_EMOJI  — ❓ or ✅
#   ND_TEXT   — cleaned Slovak content (already stripped of markers/markdown)
#   ND_CWD    — project dir, for the "**emoji PROJECT — status**" header
# Modes:
#   DISCORD_NOTIFY_DRYRUN=1 + ND_DRYRUN_FILE=<path> → write CONTENT to that file,
#       NOTHING to stdout (lets the silent Stop hook be tested hermetically).
#   DISCORD_NOTIFY_DRYRUN=1 (no file)               → print CONTENT to stdout
#       (the idle hook's existing test contract).
#   otherwise                                        → POST to Discord, backgrounded
#       and silent (so the Stop pipeline is never polluted / blocked).
# Always exit 0.

EMOJI="${ND_EMOJI:-}"
TEXT="${ND_TEXT:-}"
CWD="${ND_CWD:-}"
[ -n "$EMOJI" ] || exit 0
[ -n "$TEXT" ]  || exit 0

# Project name for the header: git toplevel basename, else cwd basename.
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || basename "$CWD")
fi
[ -z "$PROJECT" ] && PROJECT="unknown"

case "$EMOJI" in
    "❓") STATUS="otázka" ;;
    "✅") STATUS="hotovo" ;;
    *)    STATUS="" ;;
esac
HEADER="**${EMOJI} ${PROJECT}**"
[ -n "$STATUS" ] && HEADER="${HEADER} — ${STATUS}"
CONTENT=$(printf '%s\n> %s' "$HEADER" "$TEXT")

# @mention the tmux owner (zbynek / marek). Single source of truth =
# `airuleset.py notify` (reads owner from the tmux session group + the channel .env).
# Path is relative to THIS file.
AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
# Resolve the owner ONCE and force it onto BOTH downstream calls (--mention-prefix
# and --channel-id) via AIRULESET_NOTIFY_OWNER, so the @mention and the per-owner
# thread target ALWAYS agree — mirrors the Python send() "resolve owner once"
# invariant (a second independent tmux re-query could otherwise disagree, mentioning
# one person while posting to the other's thread). It also means only ONE tmux query
# runs: the two calls below short-circuit on the forced owner.
export AIRULESET_NOTIFY_OWNER="$(python3 "$AIRULESET_PY" notify --owner 2>/dev/null || echo "")"
MENTION=$(python3 "$AIRULESET_PY" notify --mention-prefix 2>/dev/null || echo "")
[ -n "$MENTION" ] && CONTENT="${MENTION}${CONTENT}"

if [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ]; then
    if [ -n "${ND_DRYRUN_FILE:-}" ]; then
        printf '%s\n' "$CONTENT" > "$ND_DRYRUN_FILE"
    else
        printf '%s\n' "$CONTENT"
    fi
    exit 0
fi

# Real delivery — bot token from the Discord channel config; channel/THREAD id
# from the owner-aware resolver so each person's notifications land in THEIR own
# thread (DISCORD_NOTIFICATION_CHANNEL_<OWNER>, else the shared id). The resolver
# (airuleset.py notify --channel-id) is the SINGLE source of truth shared with the
# Python send() path — no duplicated per-owner logic in bash.
ENVF=~/.claude/channels/discord/.env
BOT_TOKEN=""
if [ -f "$ENVF" ]; then
    BOT_TOKEN=$(grep -E '^DISCORD_BOT_TOKEN=' "$ENVF" | cut -d'=' -f2- | tr -d "\"'" | tr -d '\r\n')
fi
CHANNEL_ID=$(python3 "$AIRULESET_PY" notify --channel-id 2>/dev/null | tr -d '\r\n' || echo "")
[ -z "$BOT_TOKEN" ] && exit 0
[ -z "$CHANNEL_ID" ] && exit 0
command -v jq &>/dev/null || exit 0

(curl -s --max-time 5 -X POST \
    "https://discord.com/api/v10/channels/${CHANNEL_ID}/messages" \
    -H "Authorization: Bot ${BOT_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg content "$CONTENT" '{content: $content}')" \
    >/dev/null 2>&1) &

exit 0
