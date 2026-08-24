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

# #668: a ✅ DROPPED by the dedup suppression must leave a durable trace — a
# traceless suppression is the #134/#135/#467 silence class (the ❓ path logs its
# own LASTQ dedup the same way). Home-rooted (per-user by construction), rotated
# at the same 512 KB cap as the other notify logs, dry-run-gated (the dry-run-
# logs-nothing contract), and brace-grouped so a redirect-open failure can never
# leak to stderr or abort under `set -e` (#492). $1=status $2=reason.
_ok_log() {
    [ "${DISCORD_NOTIFY_DRYRUN:-0}" = "1" ] && return 0
    local log size
    log="$HOME/.claude/notify-delivery.log"
    mkdir -p "$(dirname "$log")" 2>/dev/null || true
    size=$(stat -c %s "$log" 2>/dev/null || echo 0)
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    [ "$size" -gt 512000 ] && mv -f "$log" "$log.1" 2>/dev/null || true
    { printf '%s %s kind=idle key=✅:%s reason=%s qhash=%s\n' \
        "$(date -Iseconds 2>/dev/null || echo '?')" "$1" "$SID" "$2" "${OKFP:-}" \
        >>"$log"; } 2>/dev/null || true
    return 0
}

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-'); [ -z "$SID" ] && SID="unknown"
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
PENDING="/tmp/claude-discord-pending-${SID}"
# #668: the reliable Stop-time cwd recorded alongside the ✅ (so the project
# label never collapses to "unknown" when the idle event carries no cwd), and
# the per-session ✅ dedup fingerprint (the ✅ analogue of the ❓ path's LASTQ).
# LASTOK is cleared by clear-question-dedup.sh on a real user prompt, so a fresh
# identical ✅ after the user spoke re-pings — one device ping per DISTINCT event.
PENDING_CWD="/tmp/claude-discord-pending-cwd-${SID}"
LASTOK="/tmp/claude-discord-lastok-${SID}"

# Nothing pending → the last turn was ⏳ WORKING / unmarked, or the ❓ already
# fired immediately on Stop → send NOTHING.
[ -s "$PENDING" ] || { dbg "SKIPPED (nothing pending for $SID)"; exit 0; }

BODY=$(cat "$PENDING")
EMOJI=$(printf '%s' "$BODY" | grep -oE "❓|✅" | head -1 || true)
TEXT=$(printf '%s' "$BODY" | sed -E 's/^(❓|✅)[[:space:]]*//')

# #668: prefer the RELIABLE Stop-time cwd recorded with the ✅ over the idle
# event's own cwd, which can be empty and collapse the project label to "unknown".
if [ -s "$PENDING_CWD" ]; then
    RECORDED_CWD=$(cat "$PENDING_CWD" 2>/dev/null || echo "")
    [ -n "$RECORDED_CWD" ] && CWD="$RECORDED_CWD"
fi

# #668: dedup an identical ✅ — one device ping per DISTINCT completion, the ✅
# analogue of the ❓ path's LASTQ. A session re-reporting the SAME ✅ across
# several Stop/idle cycles (a /goal-loop re-poke of a stream that handed off its
# last ticket) otherwise pings once per cycle (David got the same ✅ 4×).
# Fingerprint the recorded line; a match with NO user input since (LASTOK still
# present — clear-question-dedup.sh removes it on a real prompt) → consume both
# and suppress. ❓ never reaches this idle hook, so this only ever touches ✅.
if [ "$EMOJI" = "✅" ]; then
    OKFP=$(printf '%s' "$BODY" | { sha1sum 2>/dev/null || cksum; } \
           | tr -cd '0-9a-fA-F' | cut -c1-16)
    if [ -n "$OKFP" ] && [ -f "$LASTOK" ] \
            && [ "$(cat "$LASTOK" 2>/dev/null)" = "$OKFP" ]; then
        rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true
        dbg "SUPPRESSED duplicate ✅ ($SID)"
        _ok_log "suppressed" "duplicate-ok"
        exit 0
    fi
fi

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

# Consume the pending file(s) so re-idle does not re-send.
rm -f "$PENDING" "$PENDING_CWD" 2>/dev/null || true

dbg "SEND idle: $EMOJI ($SID) cwd=$CWD :: $TEXT"

SEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/notify-discord-send.sh"
# #668: deliver with ND_CONFIRM=1 (FOREGROUND, delivery-confirmed) so LASTOK
# records a DELIVERY, not a mere attempt. The idle Notification hook can afford
# the bounded foreground wait (the send's own `--max-time 5`, well under the
# hook's 15s timeout), and marking a transiently-FAILED ✅ (5xx / network blip,
# logged `not-delivered` by the send path) as delivered would suppress the only
# natural retry — the /goal re-poke re-emitting the identical ✅ — until the user
# types. This mirrors the ❓ path, which records LASTQ only on a confirmed 2xx
# (notify-discord-pending.sh, review finding 2026-07-04; #135 "a marker proves a
# claim, not a delivery"). LASTOK is written ONLY on the send's zero exit.
if ND_EMOJI="$EMOJI" ND_TEXT="$TEXT" ND_CWD="$CWD" ND_CONFIRM=1 \
        ND_QHASH="${OKFP:-}" bash "$SEND"; then
    if [ -n "${OKFP:-}" ]; then
        { printf '%s' "$OKFP" > "$LASTOK"; } 2>/dev/null || true
    fi
fi

exit 0
