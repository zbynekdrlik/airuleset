#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent + Workflow matcher) -- THIN ADAPTER (#1076).
#
# The meeting-analysis INTERPRETATION-stays-in-main gate. All logic lives in
# gates/meetingdelegation.py: it REFUSES an Agent/Workflow dispatch whose
# prompt/script carries a meeting-analysis signal (transcript.txt,
# speaker_turns.json, frames_kept, screen_inventory, *_verbatim.md, VIDEO-NOTES,
# "analyza meetingu", "meeting analysis", "doplnok z meetingu") unless it is
# marked `MECHANICAL-ONLY: extract|asr|dedup` on its FIRST line AND (for a marked
# prompt) does not also ask for interpretation ("read the screen", "summarise",
# "requirements", "precitaj", "zhrn", "co klient"). A signal-bearing dispatch is
# blocked by default (fail-CLOSED). A dispatch with NO meeting signal passes.
# Bypass: `airuleset:meeting-delegation-ok <reason>` (logged).
#
# Exit 2 = block; the reason is on STDERR (the model-visible deny channel). A
# python MALFUNCTION (the gate cannot run at all) fails OPEN -- a hook bug must
# not wedge every dispatch. `-P` (#1061 item 5b): a stale in-cwd gates/ can never
# shadow the real package.
# Dry-run: echo '{"tool_name":"Agent","tool_input":{"prompt":"read the screens in frames_kept"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.meetingdelegation <<<"$PAYLOAD" 1>&2 || RC=$?
[ "$RC" -eq 2 ] && exit 2
exit 0
