#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop -- meeting-analysis AUTHORSHIP gate (#1076). A meeting-analysis
# completion report must prove the Fable main authored the interpretation: every
# named, on-disk deliverable (`screen_inventory.md` / `NOTES.md` / `MAPPING.md`)
# carries a first-line `Analysed-by: main claude-fable-*` stamp AND the report
# carries the `Analysed-by: main <model>` line.
#
# THIN ADAPTER over gates.meetinganalysis_stop (gate-family #1020): the python
# module holds ALL the logic (is-meeting-report scoping, deliverable path
# extraction, on-disk stamp check, report-line check, decide + block message).
# This adapter only:
#   1. cheap pre-check -- skip the subprocess unless the message even mentions a
#      deliverable basename (the module re-checks; this avoids the python spawn
#      on every non-meeting Stop);
#   2. dispatch `python3 -P -m gates.meetinganalysis_stop` with the payload on
#      stdin;
#   3. on exit 2, record the block in the unified hook-blocks log + exit 2.
#
# FAIL-OPEN by construction: the module journals + allows on any read error / an
# unrelated report / an unreadable deliverable path, and a missing jq / python
# here just exits 0. This hook adds pressure, never guards a write.
# Dry-run: echo '{"last_assistant_message":"...screen_inventory.md...","cwd":"/repo"}' | bash <this hook>

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0

# Cheap pre-check: only spawn python if a deliverable basename is even mentioned.
printf '%s' "$MSG" | grep -qiE "screen_inventory\.md|NOTES\.md|MAPPING\.md" || exit 0

_MA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
_MA_REPO_ROOT="$(dirname "$_MA_DIR")"

_MA_RC=0
env PYTHONPATH="${_MA_REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.meetinganalysis_stop <<<"$INPUT" 1>&2 || _MA_RC=$?

if [ "$_MA_RC" -eq 2 ]; then
    _HOOK_BLOCK_LOG_LIB="${_MA_DIR}/lib_hook_block_log.sh"
    # shellcheck source=/dev/null
    [ -r "$_HOOK_BLOCK_LOG_LIB" ] && . "$_HOOK_BLOCK_LOG_LIB"
    type log_hook_block >/dev/null 2>&1 && log_hook_block "stop-check-meeting-analysis" "meeting-analysis authorship stamp missing"
    exit 2
fi

exit 0
