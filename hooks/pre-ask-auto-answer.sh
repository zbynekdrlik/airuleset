#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (AskUserQuestion)
# Auto-blocks questions that have pre-defined answers.
# Exit 2 = block the tool call. Claude sees stderr as the reason.

command -v jq &>/dev/null || exit 0

INPUT=$(cat)
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // empty' 2>/dev/null || echo "")
[ -z "$TOOL_INPUT" ] && exit 0

# Check for visual companion question
if echo "$TOOL_INPUT" | grep -qi "visual.*companion\|mockup.*browser\|show.*it.*to.*you.*in.*a.*web.*browser\|Want to try it"; then
    echo "BLOCKED: Visual companion is always enabled. Do not ask — just use it. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# Check for subagent vs sequential question
if echo "$TOOL_INPUT" | grep -qi "subagent.*or.*sequential\|subagent.*or.*inline\|agent.*driven.*or\|which.*execution.*approach"; then
    echo "BLOCKED: Always use subagent-driven execution. Do not ask. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# Check for "ready to proceed / say go" style questions
if echo "$TOOL_INPUT" | grep -qi "say.*go\|shall.*i.*proceed\|ready.*to.*execute\|ready.*when.*you.*are\|if.*good.*say\|if.*looks.*good\|want.*me.*to.*proceed\|proceed.*to.*next.*step\|ready.*for.*next.*step\|invoke.*superpowers:writing-plans\|invoke.*superpowers:executing-plans"; then
    echo "BLOCKED: Chain directly to the next step — do not stop to ask. If the user approved the design/plan, proceed autonomously. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

exit 0
