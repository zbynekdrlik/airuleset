#!/usr/bin/env bash
# airuleset:script-ok sourced library, not standalone — set -euo pipefail inherited from caller
# Shared hook-block logging — sourced by every hook that exit-2 blocks.
# #988 deliverable (g): unified measurement log for airuleset.py status.
#
# Usage:  source "$(dirname "${BASH_SOURCE[0]}")/lib_hook_block_log.sh"
#         log_hook_block "block-ci-poll-repeat" "$CMD"

log_hook_block() {
    local hook_name="${1:-unknown}"
    local snippet="${2:-}"
    local log_file="${AIRULESET_HOOK_BLOCKS_LOG:-${HOME}/.claude/hook-blocks.log}"
    # Redact commands that may contain secrets
    case "$snippet" in
        *secret*|*token*|*password*|*SECRET*|*TOKEN*|*PASSWORD*)
            snippet="<redacted>" ;;
    esac
    local log_dir
    log_dir=$(dirname "$log_file")
    { mkdir -p "$log_dir" 2>/dev/null || true
      printf '%s\t%s\t%s\n' \
          "$(date -Is)" "$hook_name" \
          "$(printf '%s' "$snippet" | tr '\n' ' ' | head -c 80)" \
          >> "$log_file"; } 2>/dev/null || true
}
