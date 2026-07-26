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
# nothing durable has captured yet.
#
# #65 (2026-07-26): `compact-request --record` below records the request
# AND, in the same process, attempts to DELIVER `/compact` SYNCHRONOUSLY —
# right now, before an armed `/goal` loop gets a chance to dispatch the
# next ticket at all (waiting for watchdog job 14's next ~60s poll loses
# that race: a short send-keys reliably queues even into a busy pane,
# verified live, so there is no need to wait for the pane to go idle
# first). Only when the immediate attempt is unsafe (an unlocatable pane,
# copy-mode, an open dialog, or a genuine unsent draft) does the recorded
# request survive for watchdog job 14 (compact_ticket_boundary) to retry on
# its next poll, unchanged from the pre-#65 behavior.
#
# #71 (2026-07-26 live incident): a sha256 fingerprint of `$MSG` is passed
# through as `--msg-hash` so `cmd_compact_request` can recognize a REPEAT
# fire for the SAME (unchanged) completion report as a duplicate and skip
# it — live evidence (gatekeeper, journalctl-confirmed) showed a SINGLE
# ticket-boundary report producing multiple synchronous /compact deliveries
# in a row (the armed goal loop's own re-evaluation re-running this whole
# Stop hook chain against an unchanged last_assistant_message right after a
# compaction finished), with watchdog job 14 never even invoked in that
# window. See `compact_already_delivered`/`mark_compact_delivered`
# (watchdog/__init__.py).
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

# #71 — fingerprint the message (never let a failing sha256sum kill this
# `set -e` script: the `||` fallback keeps the assignment safe, per the
# repo's own documented `VAR=$(failing_cmd)` + `set -e` gotcha).
MSG_HASH=$(printf '%s' "$MSG" | sha256sum 2>/dev/null | cut -d' ' -f1) || MSG_HASH=""

AIRULESET_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/airuleset.py"
python3 "$AIRULESET_PY" compact-request --record --session "$SID" --cwd "$CWD" \
    --msg-hash "$MSG_HASH" >/dev/null 2>&1 || true

exit 0
