#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent + Workflow) — #871 (owner directive 2026-09-04).
#
# The fleet model lineup is an ALLOWLIST of EXACT ids (airuleset.MODEL_TIERS):
# claude-fable-5-1 / claude-opus-4-6 / claude-sonnet-5 / claude-haiku-4-5. A bare
# alias (fable|opus|sonnet|haiku) FLOATS to the latest model of its family —
# `fable` silently floated to an unvetted model the day a new one shipped. So a
# dispatch NEVER carries a `model` param — the model choice is carried by a
# PINNED agent definition (frontmatter `model: <exact id>`) or a Workflow
# `opts.model: '<exact id>'`.
#
# Two surfaces:
#   * Agent    — block ANY non-empty .tool_input.model param (aliases are the
#                float vector; the pinned agent type carries the model).
#   * Workflow — block any .tool_input.script `(opts.)model: '<v>'` whose value
#                is NOT one of the exact allowlisted ids. A `.tool_input.
#                scriptPath`-invoked Workflow (the documented iterate pattern —
#                persist the script, re-invoke by path) is scanned the SAME
#                way: its file content is read and checked, fail-OPEN if the
#                path is unreadable (never wedge a dispatch on a transient
#                read failure).
#
# Exit 2 = block; Claude reads STDERR as the reason. Stdin contract: the JSON
# payload arrives on STDIN (.tool_input.*), never $TOOL_INPUT. Fail-open on a
# missing jq (never wedge a dispatch on tooling absence).
#
# ALLOWLIST below MUST equal airuleset.MODEL_TIERS.values(); a lock test
# (tests/test_block_unpinned_model_dispatch.py) asserts they match, so a
# MODEL_TIERS edit that forgets this hook fails CI.
#
# KNOWN RESIDUAL (#871 adversarial review 🔵): the Workflow scan is a plain
# grep over the script TEXT — it also matches a `model:` token that appears
# inside PROSE or a string LITERAL (e.g. a prompt that merely NAMES a banned
# id, not an actual opts.model assignment). This is the fail-SAFE direction
# (over-block, never a silent bypass), so it is accepted, not fixed.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

MODEL=$(printf '%s' "$INPUT" | jq -r '.tool_input.model // empty' 2>/dev/null || echo "")
SCRIPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.script // empty' 2>/dev/null || echo "")
SCRIPT_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.scriptPath // empty' 2>/dev/null || echo "")

# A scriptPath-invoked Workflow carries no inline `script` — read the file and
# scan it the same way. Fail-OPEN on an unreadable path (never wedge a
# dispatch on a transient read failure).
if [ -n "$SCRIPT_PATH" ] && [ -r "$SCRIPT_PATH" ]; then
    FILE_CONTENT=$(cat "$SCRIPT_PATH" 2>/dev/null || echo "")
    if [ -n "$FILE_CONTENT" ]; then
        if [ -n "$SCRIPT" ]; then
            SCRIPT="$SCRIPT
$FILE_CONTENT"
        else
            SCRIPT="$FILE_CONTENT"
        fi
    fi
fi

# The exact-id allowlist — keep in sync with airuleset.MODEL_TIERS (lock-tested).
ALLOWLIST_RE='^(claude-fable-5-1|claude-opus-4-6|claude-sonnet-5|claude-haiku-4-5)(\[[0-9]+m\])?$'

_pointer() {
    echo "" >&2
    echo "  A dispatch NEVER carries a \`model\` param — a bare alias floats to the" >&2
    echo "  latest model (the Fable 5.1 failure, #871). The model choice is carried" >&2
    echo "  by a PINNED agent type / Workflow opts.model:" >&2
    echo "    - fable 5.1 design/review consult -> subagent_type: \"fable-advisor\"" >&2
    echo "    - opus 4.6 escalation / gate-CLOSED -> the claude-opus-4-6-pinned agent" >&2
    echo "                                            (autopilot-worker / ticket-validator)" >&2
    echo "    - sonnet 5 settled-design impl       -> subagent_type: \"sonnet-implementer\"" >&2
    echo "    - sonnet 5 mechanical/read-only      -> subagent_type: \"sonnet-mechanical\"" >&2
    echo "    - Workflow stage                     -> opts.model: '<exact id from MODEL_TIERS>'" >&2
    exit 2
}

# --- Agent surface: block ANY non-empty model param ------------------------ #
if [ -n "$MODEL" ]; then
    echo "BLOCKED: Agent dispatch carries a \`model\` param (\"$MODEL\")." >&2
    _pointer
fi

# --- Workflow surface: block any (opts.)model value not on the allowlist ---- #
if [ -n "$SCRIPT" ]; then
    # Extract every `model: <value>` / `opts.model: <value>` occurrence and
    # check each against the exact-id allowlist. `grep -oiE` pulls the values;
    # anything not matching ALLOWLIST_RE is a violation.
    BADVAL=""
    while IFS= read -r val; do
        [ -n "$val" ] || continue
        v=$(printf '%s' "$val" | tr 'A-Z' 'a-z')
        if ! printf '%s' "$v" | grep -qE "$ALLOWLIST_RE"; then
            BADVAL="$val"
            break
        fi
    done < <(printf '%s' "$SCRIPT" \
                | grep -oiE "model:[[:space:]]*[\"'\`]?[a-z0-9._-]*[\"'\`]?" \
                | sed -E "s/^model:[[:space:]]*[\"'\`]?//I; s/[\"'\`]?\$//")
    if [ -n "$BADVAL" ]; then
        echo "BLOCKED: Workflow stage model \"$BADVAL\" is not an exact allowlisted id." >&2
        _pointer
    fi
fi

exit 0
