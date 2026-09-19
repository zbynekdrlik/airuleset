#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — LANE-FILL gate (#1078 item 1). A goal-armed PARALLEL-mode session
# must not end a `⏳ WORKING` / `✅ DONE` turn with dispatchable tickets and idle
# lane slots. Owner directive montalu1 2026-09-18 (repeated 3×): "I 19 a len jeden
# subagent pracuje!".
#
# THIN ADAPTER over gates.lanefill (gate-family #1020): the python module holds
# ALL the logic (goal-armed read, mode resolve, live-lane count, dispatchable
# quals, decide + block message). This adapter only:
#   1. cheap pre-check — skip the subprocess unless the turn's LAST non-blank
#      line carries ⏳ or ✅ DONE (the module re-checks; this just avoids the
#      python spawn on every non-⏳/✅ Stop);
#   2. dispatch `python3 -P -m gates.lanefill` with the Stop payload on stdin;
#   3. on exit 2, record the block in the unified hook-blocks log + exit 2.
#
# FAIL-OPEN by construction: the module journals + allows on any read error, and
# a missing jq / python here just exits 0. This hook adds pressure, never guards
# a write.

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(printf '%s' "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0

# Cheap pre-check: LAST non-blank line must carry ⏳ or ✅ DONE (the module's
# _last_marker semantics — the marker is on the tail line, not merely present).
LAST_LINE=$(printf '%s\n' "$MSG" | grep -vE '^[[:space:]]*$' | tail -1 || true)
# Case-insensitive to match gates.lanefill._last_marker (re.I) — never skip a
# lowercase `✅ done` tail the python gate would otherwise enforce on.
printf '%s' "$LAST_LINE" | grep -qiE "⏳|✅[[:space:]]*(DONE|complete|work complete)" || exit 0

_LF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
_LF_REPO_ROOT="$(dirname "$_LF_DIR")"

_LF_RC=0
env PYTHONPATH="${_LF_REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.lanefill <<<"$INPUT" 1>&2 || _LF_RC=$?

if [ "$_LF_RC" -eq 2 ]; then
    _HOOK_BLOCK_LOG_LIB="${_LF_DIR}/lib_hook_block_log.sh"
    # shellcheck source=/dev/null
    [ -r "$_HOOK_BLOCK_LOG_LIB" ] && . "$_HOOK_BLOCK_LOG_LIB"
    type log_hook_block >/dev/null 2>&1 && log_hook_block "stop-check-lane-fill" "$LAST_LINE"
    exit 2
fi

exit 0
