#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent + Workflow) — #991 (owner directive 2026-09-11).
#
# The working model picks a subagent's model natively; airuleset bans only a
# BANNED model (source: airuleset.BANNED_MODELS = Opus 5 + opus/opusplan, and
# every Fable id + fable, #1248). tests/test_block_banned_model.py asserts each
# BANNED_MODELS id is named here.
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

# _is_banned <value>: rc 0 iff a BANNED model, after lowercasing and stripping
# quotes/spaces, a [Nm] tag, a provider prefix and a -YYYYMMDD suffix.
_is_banned() {
    local v core
    v=$(printf '%s' "$1" | tr 'A-Z' 'a-z')
    v="${v//[\'\"\`]/}"
    v="${v// /}"
    v=$(printf '%s' "$v" | sed -E 's/\[[0-9]+m\]$//; s/^((us|eu|apac|global)\.)?anthropic[./]//')
    core=$(printf '%s' "$v" | sed -E 's/-[0-9]{8}$//')
    case "$v" in opus|opusplan|claude-opus-5|fable|claude-fable-5|claude-fable-5-1|fable*|claude-fable*) return 0 ;; esac
    case "$core" in claude-opus-5|claude-fable|claude-fable-*) return 0 ;; esac
    return 1
}

_block() {
    echo "BLOCKED: dispatch model \"$1\" is a BANNED model (Opus 5 off-lineup, #991, + its" >&2
    echo "  opus/opusplan alias; Fable, #1248: owner 2026-10-15, worse than Opus 5.5). Pick" >&2
    echo "  sonnet/haiku/claude-opus-5-5, or omit the param (native default = claude-opus-5-5)." >&2
    exit 2
}

# --- Agent surface --------------------------------------------------------- #
if [ -n "$MODEL" ] && _is_banned "$MODEL"; then
    _block "$MODEL"
fi

# --- Workflow surface: check each (opts.)model value ----------------------- #
if [ -n "$SCRIPT" ]; then
    # #1248: a real `model` key only (not `Self-review-model:` text), quoted
    # or bare, `:` or `=`, newlines joined so a next-line value counts.
    while IFS= read -r val; do
        [ -n "$val" ] || continue
        if _is_banned "$val"; then
            _block "$val"
        fi
    done < <(printf '%s' "$SCRIPT" | tr '\n' ' ' \
                | grep -oiE "(^|[^a-z0-9_-])[\"'\`]?model[\"'\`]?[[:space:]]*[:=][[:space:]]*[\"'\`]?[a-z0-9._:/-]*" \
                | sed -E "s/^.*model[\"'\`]?[[:space:]]*[:=][[:space:]]*[\"'\`]?//I")
fi

exit 0
