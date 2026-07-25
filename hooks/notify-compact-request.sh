#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — records a /compact REQUEST ("krok 1c — ohraničenie kontextu",
# #39 follow-up) when the turn's final message is a COMPLETED-TICKET report:
# the `## ✅ Work Complete` heading (completion-report.md) or a terminal
# `✅ DONE:` marker (message-status-marker.md) — but NEVER when the turn is
# actually blocked on the user (`❓`) or still working (`⏳`), even if a
# `## ✅ Work Complete` heading appears earlier in the same message (a
# manual-merge report ends `❓ NEEDS YOU: schváliš merge?` — that decision is
# still pending, so it is NOT a safe compaction boundary). Same last-line
# precedence notify-discord-pending.sh already uses for its own ❓/⏳/✅
# detection.
#
# A completed ticket's durable state already lives in git / GitHub / the
# issue — whatever /compact discards AT THAT boundary is genuinely
# disposable, unlike a mid-task compact which risks losing working context
# nothing durable has captured yet. A SEPARATE watchdog job
# (compact_ticket_boundary, watchdog/__init__.py job 14) types `/compact`
# into the session's pane LATER, only once it is genuinely idle — never
# from here (a hook must never type into its own live pane mid-turn).
#
# Silent + non-blocking: never writes to stdout, always exits 0 — must
# never interfere with the Stop decision pipeline (the other
# stop-check-*.sh gates).

INPUT=$(cat)

MSG=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

[ -z "$SID" ] && exit 0
[ -z "$MSG" ] && exit 0

LAST_LINE=$(printf '%s\n' "$MSG" | grep -vE '^[[:space:]]*$' | tail -1 || true)

# A ❓/⏳ on the LAST line wins over a ✅ heading elsewhere — the turn is NOT
# actually a safe compaction boundary (still blocked on the user, or still
# working — e.g. an autopilot "merged #5 … now ⏳ working #6" turn).
if printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*❓'; then
    exit 0
fi
if printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*⏳'; then
    exit 0
fi

DONE=0
printf '%s' "$MSG" | grep -qiE '^#+[[:space:]]*✅[[:space:]]*work complete' && DONE=1
printf '%s' "$LAST_LINE" | grep -qE '^[[:space:]]*[*_>~-]*[[:space:]]*✅[[:space:]]*DONE:' && DONE=1
[ "$DONE" = "1" ] || exit 0

AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
python3 "$AIRULESET_PY" compact-request --record --session "$SID" --cwd "$CWD" \
    >/dev/null 2>&1 || true

exit 0
