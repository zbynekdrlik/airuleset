#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent + Workflow) — #871.
#
# Blocks the bare `fable` model alias and any `claude-fable-5-1` id on a
# dispatch. The Agent `model` param accepts only aliases
# (sonnet|opus|haiku|fable) and the bare `fable` alias floats to the BANNED
# Fable 5.1 (claude-fable-5-1). Fable 5.0 (claude-fable-5) is reachable from a
# dispatch ONLY via the pinned `fable-advisor` agent type (frontmatter
# model: claude-fable-5) or a Workflow stage opts.model: 'claude-fable-5' — so
# a gated Fable design/review PHASE dispatches `subagent_type: fable-advisor`
# with NO `model` param, never `model: "fable"`.
#
# Two surfaces:
#   * Agent  — .tool_input.model == "fable" (bare alias) or a claude-fable-5-1 id
#   * Workflow — .tool_input.script text carrying `model: "fable"` / `'fable'`
#                as a dispatch value, or any claude-fable-5-1 id
#
# Exit 2 = block; Claude reads STDERR as the reason. Stdin contract: the JSON
# payload arrives on STDIN (.tool_input.*), never $TOOL_INPUT. Fail-open on a
# missing jq (never wedge a dispatch on tooling absence).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

MODEL=$(printf '%s' "$INPUT" | jq -r '.tool_input.model // empty' 2>/dev/null || echo "")
SCRIPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.script // empty' 2>/dev/null || echo "")

_norm() {
    # lower-case, strip surrounding quotes/space, drop a trailing [Nm] tag.
    printf '%s' "$1" | tr 'A-Z' 'a-z' | sed -E "s/^[[:space:]'\"]+//; s/[[:space:]'\"]+$//; s/[[:space:]]*\[[0-9]+m\]$//"
}

_block() {
    echo "BLOCKED: $1" >&2
    echo "" >&2
    echo "  The bare \`fable\` alias floats to the BANNED Fable 5.1 (claude-fable-5-1)." >&2
    echo "  Fable 5.0 (claude-fable-5) is reachable from a dispatch ONLY via:" >&2
    echo "    - Agent:    subagent_type: \"fable-advisor\"   (NO model param — the agent" >&2
    echo "                definition is pinned model: claude-fable-5)" >&2
    echo "    - Workflow: opts.model: 'claude-fable-5'" >&2
    echo "" >&2
    echo "  Run \`airuleset.py fable-gate\` first: OPEN -> dispatch fable-advisor;" >&2
    echo "  CLOSED -> fall back to claude-opus-4-8 (model-awareness.md, #871)." >&2
    exit 2
}

# --- Agent surface: the model param ---------------------------------------- #
if [ -n "$MODEL" ]; then
    NM=$(_norm "$MODEL")
    if [ "$NM" = "fable" ]; then
        _block "Agent dispatch model=\"$MODEL\" is the bare \`fable\` alias."
    fi
    case "$NM" in
        *claude-fable-5-1*)
            _block "Agent dispatch model=\"$MODEL\" is the banned Fable 5.1 id." ;;
    esac
fi

# --- Workflow surface: the script text ------------------------------------- #
if [ -n "$SCRIPT" ]; then
    # A banned Fable 5.1 id anywhere in the script.
    if printf '%s' "$SCRIPT" | grep -qiE 'claude-fable-5-1'; then
        _block "Workflow script references the banned Fable 5.1 id (claude-fable-5-1)."
    fi
    # A bare `fable` alias as a (opts.)model dispatch VALUE — `model: "fable"`,
    # `model: 'fable'`, `model: fable`, `opts.model: 'fable'`. The negative
    # class (?![\w-]) keeps `claude-fable-5` from ever matching (the char after
    # `fable` is `-`).
    if printf '%s' "$SCRIPT" | grep -qiE "model:[[:space:]]*[\"']?fable([^a-z0-9_-]|$)"; then
        _block "Workflow stage uses the bare \`fable\` alias as a model value."
    fi
fi

exit 0
