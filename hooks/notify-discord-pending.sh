#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — device notification on ❓ NEEDS YOU / ❓ ASKED (immediate) / ✅ DONE (idle).
#
# Mobile-app notification model — the device is pinged ONLY when Claude genuinely
# ASKS the user (❓ NEEDS YOU) or FULLY completed work (✅ DONE); never on
# ⏳ WORKING, never on routine progress. Split delivery by urgency:
#   - ❓ NEEDS YOU (blocked, last line) OR ❓ ASKED (raised while continuing other
#     answer-independent work; turn ends ⏳ WORKING) → SENT IMMEDIATELY from here. A
#     genuine question must reach the phone even over tmux/SSH, where Claude Code's
#     `idle_prompt` event is unreliable, and is NEVER suppressed — the old "❓ +
#     continuing language → swallow the ping" logic was the reported bug (the user
#     asked, no ping came, then got reproached hours later).
#   - ✅ DONE → recorded to a per-session pending file; notify-discord.sh delivers
#     it ONLY when the user is genuinely idle/away (a finished turn is less urgent,
#     and pinging every completed turn while the user watches the terminal = spam).
#
# This hook runs on EVERY turn (it has last_assistant_message). ⏳ / no-marker
# CLEARS any stale pending so nothing fires.
#
# Marker detection scans the WHOLE message (not just the last line): a completion
# report puts `## ✅ Work Complete` at the TOP and ends with a PR/URL or a
# `❓ Question:` line, so last-line-only detection would miss the most important
# "done" event. Precedence: an ACTIVE question (❓ on the last non-blank line) wins
# over a ✅ heading elsewhere (a report can have both — the trailing ❓ means it is
# waiting on the user).
#
# Silent + non-blocking: writes NOTHING to stdout and always exit 0, so it never
# interferes with the Stop decision pipeline (the other stop-check-*.sh gates).

INPUT=$(cat)

MSG=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
# Defang the session id so it can never escape the /tmp prefix (CC ids are uuids;
# this is belt-and-suspenders against a crafted payload).
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')
[ -z "$SID" ] && SID="unknown"
PENDING="/tmp/claude-discord-pending-${SID}"
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

LAST_LINE=$(printf '%s\n' "$MSG" | grep -vE '^[[:space:]]*$' | tail -1 || true)

# Strip markdown emphasis + a leading marker label so the phone line is clean
# Slovak prose (e.g. "❓ **Question:** approve merge?" -> "approve merge?").
strip_md() {
    printf '%s' "$1" \
        | sed -E 's/\*\*//g' \
        | sed -E 's/^[[:space:]]*(NEEDS[[:space:]]+YOU|Question|DONE)[[:space:]]*:?[[:space:]]*//I' \
        | sed -E 's/^[[:space:]]+//'
}

emit() {
    # $1 = emoji, $2 = raw content; clean + truncate to keep the device line short.
    local c
    c=$(strip_md "$2" | cut -c1-250)
    printf '%s %s' "$1" "$c" > "$PENDING"
}

send_now() {
    # $1 = emoji, $2 = raw content — deliver IMMEDIATELY via the shared send path
    # (no pending file, no waiting for an idle_prompt that may never arrive over
    # tmux/SSH). Silent + non-blocking: the send backgrounds its own curl.
    local c send
    c=$(strip_md "$2" | cut -c1-250)
    send="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/notify-discord-send.sh"
    ND_EMOJI="$1" ND_TEXT="$c" ND_CWD="$CWD" bash "$send" || true
}

# A genuine question to the user ALWAYS fires the device ping — NO suppression,
# ever. Two honest forms (message-status-marker.md):
#   ❓ ASKED: <q>      — a body line; the turn ENDS ⏳ WORKING because you keep
#     doing OTHER answer-independent work. The question is pinged NOW and tracked
#     durably on its ticket; you resume that ticket whenever the user answers.
#   ❓ NEEDS YOU: <q>  — the LAST line; you are BLOCKED (no other useful work) and
#     STOP. Pinged NOW.
# Either way the phone is pinged. The removed "❓ + continuing language → swallow
# the ping" logic was the exact bug the user reported: a mid-loop question that
# never reached the phone, then a reproach hours later. Continuing is fine; the
# ping is not optional. (An ❓ ASKED line takes precedence over the terminal ⏳:
# a question you raise this turn must ping even though the turn keeps working.)
ASKED_LINE=$(printf '%s\n' "$MSG" | grep -iE '❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:' | tail -1 || true)

if [ -n "$ASKED_LINE" ]; then
    # ask-and-continue: ping the freshly-raised question NOW; the turn keeps
    # working (⏳). No pending left → idle hook won't re-send.
    C=$(printf '%s' "$ASKED_LINE" | sed -E 's/.*❓[[:space:]]*\**[[:space:]]*ASKED[[:space:]]*\**[[:space:]]*:[[:space:]]*//I')
    rm -f "$PENDING" 2>/dev/null || true
    send_now "❓" "$C"
elif printf '%s' "$LAST_LINE" | grep -q "❓"; then
    # ❓ NEEDS YOU on the last line, genuinely blocked on the user → fire the device
    # ping IMMEDIATELY (the question must reach the phone even over SSH, where the
    # idle_prompt event is unreliable). No pending left → idle hook won't re-send.
    C=$(printf '%s' "$LAST_LINE" | sed -E 's/.*❓[[:space:]]*//')
    rm -f "$PENDING" 2>/dev/null || true
    send_now "❓" "$C"
elif printf '%s' "$LAST_LINE" | grep -q "⏳"; then
    # ⏳ WORKING is the last line → still going (even if a "✅ DONE:" appears
    # earlier in the turn, e.g. autopilot "merged #5 … now ⏳ working #6"). Clear
    # any stale pending so nothing fires while Claude keeps working.
    rm -f "$PENDING" 2>/dev/null || true
elif printf '%s' "$MSG" | grep -qiE '✅[[:space:]]*DONE:|#+[[:space:]]*✅[[:space:]]*work complete|✅[[:space:]]*work complete'; then
    # Fully-done state. Prefer an explicit "✅ DONE: <outcome>" line; else the
    # report's "What changed" / "Goal" one-liner; else a generic Slovak fallback.
    DLINE=$(printf '%s\n' "$MSG" | grep -iE '✅[[:space:]]*DONE:' | tail -1 || true)
    if [ -n "$DLINE" ]; then
        C=$(printf '%s' "$DLINE" | sed -E 's/.*✅[[:space:]]*DONE:[[:space:]]*//I')
    else
        C=$(printf '%s\n' "$MSG" | grep -iE '^\*\*(What changed|Goal)\b' | head -1 \
            | sed -E 's/^\*\*(What changed|Goal):?\*\*:?[[:space:]]*//I' || true)
        [ -z "$C" ] && C="práca dokončená"
    fi
    emit "✅" "$C"
else
    # No marker → nothing to notify. Clear any stale pending.
    rm -f "$PENDING" 2>/dev/null || true
fi

exit 0
