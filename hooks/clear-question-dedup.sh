#!/usr/bin/env bash
set -euo pipefail

# Hook: UserPromptSubmit — clear the per-session ❓ dedup state.
#
# notify-discord-pending.sh (Stop) dedups the device ping for a question that is
# REPEATED with identical content and NO user input in between (a /goal-loop
# re-poke of a session still blocked on the same unanswered question — the 9×
# "rovnaká otázka ako predtým" restreamer spam, 2026-07-04). The moment the user
# actually TYPES a prompt, that conversation moved on: whatever is asked next is
# a FRESH ask and must ping even if its text happens to be byte-identical to the
# old one. So every real user prompt clears the LASTQ state.
#
# Silent + non-blocking: no stdout, always exit 0 — never interferes with prompt
# processing. (Stop-hook feedback re-invocations and background task-notification
# re-invocations do NOT fire UserPromptSubmit, so the dedup correctly survives
# those — only a genuine human prompt resets it.)

INPUT=$(cat)

SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
# Defang the session id so it can never escape the /tmp prefix (same
# belt-and-suspenders as notify-discord-pending.sh).
SID=$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')
[ -z "$SID" ] && SID="unknown"

rm -f "/tmp/claude-discord-lastq-${SID}" 2>/dev/null || true

# Presence marker: a REAL user prompt means the user is AT the terminal right
# now. stop-check-question-quality.sh reads its mtime and skips the phone-shape
# template enforcement while it is fresh (<10 min) — gating a live dialog
# re-printed questions + hook errors into the user's chat (the camera-box
# "Hruza", 2026-07-05). Goal-loop re-pokes / hook feedback do NOT fire
# UserPromptSubmit, so an away session never looks "present".
touch "/tmp/claude-user-active-${SID}" 2>/dev/null || true

exit 0
