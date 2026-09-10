#!/usr/bin/env bash
# airuleset:script-ok sourced library, not standalone — set -euo pipefail inherited from caller
# Shared hook-block logging — sourced by every hook that exit-2 blocks.
# #988 deliverable (g): unified measurement log for airuleset.py status.
#
# Usage:  source "$(dirname "${BASH_SOURCE[0]}")/lib_hook_block_log.sh"
#         log_hook_block "block-ci-poll-repeat" "$CMD"

# _is_pytest_ancestor: walk the parent chain via /proc and return 0 (true)
# if any ancestor's cmdline contains 'pytest'. Needs no env var — works even
# when tests construct clean env dicts that omit PYTEST_CURRENT_TEST.
# Supports AIRULESET_PROC_ROOT seam for testing (default: /proc).
# Fails OPEN on unreadable /proc (returns 1 = not pytest = write normally).
_is_pytest_ancestor() {
    local proc_root="${AIRULESET_PROC_ROOT:-/proc}"
    local pid="$$"
    local i cmdline
    for i in 1 2 3 4 5 6 7 8; do
        # Read PPID from /proc/<pid>/status
        local status_file="$proc_root/$pid/status"
        [ -r "$status_file" ] || return 1
        pid=$(awk '/^PPid:/{print $2}' "$status_file" 2>/dev/null) || return 1
        [ -n "$pid" ] && [ "$pid" != "0" ] || return 1
        # Read cmdline (NUL-separated)
        local cmdline_file="$proc_root/$pid/cmdline"
        [ -r "$cmdline_file" ] || return 1
        cmdline=$(tr '\0' ' ' < "$cmdline_file" 2>/dev/null) || return 1
        # Check for pytest in any form
        case "$cmdline" in
            *" pytest "*|*"/pytest "*|*" pytest"|*"/pytest"|*"py.test"*|*"-m pytest"*|*".venv/bin/pytest"*)
                return 0 ;;
        esac
    done
    return 1
}

log_hook_block() {
    local hook_name="${1:-unknown}"
    local snippet="${2:-}"
    # Priority:
    # 1. AIRULESET_HOOK_BLOCK_LOG=<path> → write there (explicit override wins)
    #    AIRULESET_HOOK_BLOCK_LOG=off   → suppress entirely
    # 2. PYTEST_CURRENT_TEST set        → suppress (env guard)
    # 3. pytest in ancestor chain       → suppress (/proc walk, #988 fix-forward)
    # 4. default                        → ~/.claude/hook-blocks.log
    if [[ -n "${AIRULESET_HOOK_BLOCK_LOG:-}" ]]; then
        [[ "${AIRULESET_HOOK_BLOCK_LOG}" == "off" ]] && return 0
        local log_file="${AIRULESET_HOOK_BLOCK_LOG}"
    elif [[ -n "${PYTEST_CURRENT_TEST:-}" ]]; then
        return 0
    elif _is_pytest_ancestor; then
        return 0
    else
        local log_file="${HOME}/.claude/hook-blocks.log"
    fi
    # Redact commands that may contain secrets
    case "$snippet" in
        *secret*|*token*|*password*|*SECRET*|*TOKEN*|*PASSWORD*)
            snippet="<redacted>" ;;
    esac
    local log_dir
    log_dir=$(dirname "$log_file")
    { mkdir -p "$log_dir" 2>/dev/null || true
      printf '%s\t%s\t%s\n' \
          "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$hook_name" \
          "$(printf '%s' "$snippet" | tr '\n' ' ' | head -c 80)" \
          >> "$log_file"; } 2>/dev/null || true
}
