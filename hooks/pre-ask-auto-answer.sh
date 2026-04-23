#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (AskUserQuestion)
# Auto-blocks questions that have pre-defined answers.
# Exit 2 = block the tool call. Claude sees stderr as the reason.

command -v jq &>/dev/null || exit 0

INPUT=$(cat)
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // empty' 2>/dev/null || echo "")
[ -z "$TOOL_INPUT" ] && exit 0

# Visual companion question (all phrasings)
if echo "$TOOL_INPUT" | grep -qiE "visual.?companion|mockup.*browser|show.*it.*in.*a.*web.*browser|want to try it|visual.*option|browser.*preview"; then
    echo "BLOCKED: Visual companion is always enabled. Do not ask — just use it. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# Subagent vs inline/sequential question (all phrasings)
# Catches: "subagent or sequential", "Subagent-Driven ... Inline Execution",
# "which execution approach", "Two execution options", "Which approach?"
if echo "$TOOL_INPUT" | grep -qiE "subagent.?driven|subagent.*(or|vs).*(sequential|inline)|agent.?driven.*(or|vs)|which.*execution.*approach|two.*execution.*option|execution.*option.*subagent|inline.*execution.*subagent|subagent.*inline.*execution"; then
    echo "BLOCKED: Always use subagent-driven execution. Do not ask. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

# "Ready to proceed / say go / which approach" style questions
if echo "$TOOL_INPUT" | grep -qiE "say.*go|shall.*(i|we).*proceed|ready.*to.*(execute|start)|ready.*when.*you.*are|if.*good.*say|if.*(looks|seems).*good|want.*me.*to.*proceed|proceed.*to.*next.*step|ready.*for.*next.*step|invoke.*superpowers:writing-plans|invoke.*superpowers:executing-plans|which.*approach\?|which.*do.*you.*prefer|how.*would.*you.*like.*to.*proceed"; then
    echo "BLOCKED: Chain directly to the next step — do not stop to ask. If the user approved the design/plan, proceed autonomously. See ask-before-assuming.md pre-answered questions table." >&2
    exit 2
fi

exit 0
