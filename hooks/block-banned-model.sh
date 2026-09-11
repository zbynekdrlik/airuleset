#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent + Workflow) — #991 (owner directive 2026-09-11).
#
# The working model chooses a subagent's model natively; airuleset bans exactly
# ONE thing on the dispatch surface: a BANNED model. Ban-list source of truth =
# airuleset.BANNED_MODELS: claude-opus-5, and the bare opus / opusplan alias
# that resolves onto Opus 5. Everything else passes. tests/test_block_banned_
# model.py asserts every BANNED_MODELS id is named here.
#
# Exit 2 = block (STDERR = reason). Stdin: the JSON payload. Fail-open on no jq.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

MODEL=$(printf '%s' "$INPUT" | jq -r '.tool_input.model // empty' 2>/dev/null || echo "")
SCRIPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.script // empty' 2>/dev/null || echo "")
SCRIPT_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.scriptPath // empty' 2>/dev/null || echo "")

if [ -n "$SCRIPT_PATH" ] && [ -r "$SCRIPT_PATH" ]; then
    FILE_CONTENT=$(cat "$SCRIPT_PATH" 2>/dev/null || echo "")
    [ -z "$FILE_CONTENT" ] || SCRIPT="$SCRIPT
$FILE_CONTENT"
fi

# _is_banned <value> — true (rc 0) iff value names a BANNED model. Normalizes:
# lowercase, strip quotes/backticks/space, strip a trailing [Nm] tag, strip a
# provider prefix (us/eu/apac.anthropic.) and a served -YYYYMMDD date suffix.
_is_banned() {
    local v core
    v=$(printf '%s' "$1" | tr 'A-Z' 'a-z')
    v="${v//[\'\"\`]/}"
    v="${v// /}"
    v=$(printf '%s' "$v" | sed -E 's/\[[0-9]+m\]$//; s/^(us|eu|apac)?\.?anthropic\.//')
    core=$(printf '%s' "$v" | sed -E 's/-[0-9]{8}$//')
    case "$v" in opus|opusplan|claude-opus-5) return 0 ;; esac
    case "$core" in claude-opus-5) return 0 ;; esac
    return 1
}

_block() {
    echo "BLOCKED: dispatch model \"$1\" is a BANNED model (Opus 5 off-lineup, #991;" >&2
    echo "  the bare opus/opusplan alias floats onto it). Pick sonnet/haiku/" >&2
    echo "  claude-opus-4-8, or omit the param (native default = claude-opus-4-8)." >&2
    exit 2
}

# --- Agent surface --------------------------------------------------------- #
if [ -n "$MODEL" ] && _is_banned "$MODEL"; then
    _block "$MODEL"
fi

# --- Workflow surface: check each (opts.)model value ----------------------- #
if [ -n "$SCRIPT" ]; then
    while IFS= read -r val; do
        [ -n "$val" ] || continue
        if _is_banned "$val"; then
            _block "$val"
        fi
    done < <(printf '%s' "$SCRIPT" \
                | grep -oiE "model:[[:space:]]*[\"'\`]?[a-z0-9._:-]*[\"'\`]?" \
                | sed -E "s/^model:[[:space:]]*//I")
fi

exit 0
