#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) -- THIN ADAPTER (#1020 Part 2 gate-family rework).
#
# The Scope-gate / Dedup / chain-depth+width / daily-cap / stream-routing /
# presence / dismissal-word / net-drain classifier (#137/#329/#390/#842/#962/
# #993) now lives in the importable, testable `gates/filing` package, run as
# `python3 -P -m gates.filing`. It reads the JSON payload on STDIN (current CC
# contract), does the #842 worker (subagent) hard-block, the pre-filter, the
# `airuleset:scope-gate-ok` bypass, the presence read, all classification + git
# + logging, prints the block reason to STDERR, and exits 0 (allow) / 2 (block).
# Exit 2 = block the tool call.
#
# Fail-CLOSED wrapper (same shape as block-test-skips.sh): a python3 malfunction
# (the gate cannot run) exits non-0/2 -> this adapter turns it into an honest
# "HOOK MALFUNCTION" exit 2, never a silent pass. `log_hook_block` (the #988(g)
# measurement) stays here at the hook boundary, fired on every exit-2 block.
# Dry-run: echo '{"tool_input":{"command":"gh issue create -t x -F b.md"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"

RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.filing <<<"$PAYLOAD" || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): block-ungated-issue-filing internal error — the gate" >&2
    echo "  exited $RC instead of running the check. This is a HOOK MALFUNCTION," >&2
    echo "  not necessarily a real violation — fix the hook (or python3) first." >&2
    RC=2
fi

if [ "$RC" -eq 2 ]; then
    _LIB="${HOOK_DIR}/lib_hook_block_log.sh"
    [ -r "$_LIB" ] && . "$_LIB"
    if type log_hook_block >/dev/null 2>&1; then
        CMD=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
        log_hook_block "block-ungated-issue-filing" "$CMD"
    fi
fi
exit "$RC"
