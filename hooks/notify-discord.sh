#!/usr/bin/env bash
set -euo pipefail

# Hook: Notification (idle_prompt) — sends a PENDING ✅ when the user is idle/away.
#
# Mobile-app model (paired with notify-discord-pending.sh on the Stop event):
#   - ❓ NEEDS YOU is sent IMMEDIATELY by the Stop hook (the user is blocked on us;
#     Claude Code emits `idle_prompt` unreliably over tmux/SSH, so a question must
#     NOT depend on it). By the time this idle hook runs there is no ❓ pending.
#   - ✅ DONE is still idle-gated HERE: a finished turn is less urgent, and pinging
#     every completed turn while the user watches the terminal is spam. The Stop
#     hook records the ✅ payload; this hook delivers it only on a real idle event.
# On ⏳ WORKING / no marker the pending file was cleared → NOTHING is sent.
#
# Fire-and-forget — never blocks Claude (exit 0 always).
# DISCORD_NOTIFY_DRYRUN=1 → the shared send prints the would-send line to stdout
# (used by tests). DISCORD_NOTIFY_DEBUG=1 → append a debug line to a 0600 log.

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

# Nothing pending → the last turn was ⏳ WORKING / unmarked, or the ❓ already
# fired immediately on Stop → send NOTHING.
[ -s "$PENDING" ] || { dbg "SKIPPED (nothing pending for $SID)"; exit 0; }

BODY=$(cat "$PENDING")
EMOJI=$(printf '%s' "$BODY" | grep -oE "❓|✅" | head -1 || true)
TEXT=$(printf '%s' "$BODY" | sed -E 's/^(❓|✅)[[:space:]]*//')

# A ✅ "done" claim while a background monitor shell is still alive in this cwd is
# likely intermediate → defer (leave the pending so a later idle retries once the
# shell exits). ❓ never reaches here, so this guard only protects ✅.
if [ "$EMOJI" != "❓" ] && [ -n "$CWD" ]; then
    for pid in $(pgrep -f "shell-snapshots" 2>/dev/null || true); do
        SHELL_CWD=$(readlink "/proc/$pid/cwd" 2>/dev/null || echo "")
        if [ "$SHELL_CWD" = "$CWD" ]; then
            dbg "DEFERRED ✅ (bg shell PID=$pid CWD=$SHELL_CWD)"
            exit 0
        fi
    done
fi

# Consume the pending file so re-idle does not re-send.
rm -f "$PENDING" 2>/dev/null || true

dbg "SEND idle: $EMOJI ($SID) cwd=$CWD :: $TEXT"

SEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/notify-discord-send.sh"
ND_EMOJI="$EMOJI" ND_TEXT="$TEXT" ND_CWD="$CWD" bash "$SEND" || true

exit 0
