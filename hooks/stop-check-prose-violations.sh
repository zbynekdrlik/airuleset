#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# WARNING-ONLY: detects when Claude asked a pre-answered question in prose
# instead of applying the fixed answer. Warns Claude for the next turn.
# Does NOT block (exit 0) — blocking Stop causes infinite loops.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0

# Check for subagent vs inline prose question
if echo "$MSG" | grep -qiE "subagent.?driven.*inline|two execution options|which (approach|execution)|subagent or (sequential|inline)"; then
    echo "VIOLATION: You asked 'subagent or inline' in prose. This is a pre-answered question — always use subagent-driven. Next time, just dispatch subagents without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for visual companion prose question
if echo "$MSG" | grep -qiE "want to try.*(visual|mockup|browser)|easier to explain.*browser|visual companion"; then
    echo "VIOLATION: You offered visual companion in prose. This is a pre-answered question — always yes. Next time, just use it without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for "say go / ready to proceed" prose questions
if echo "$MSG" | grep -qiE "say.?go|shall (i|we) proceed|if good.?say|ready when you are|ready for.?next|ready to execute"; then
    echo "VIOLATION: You asked the user to 'say go' or confirm proceed in prose. The plan is approved — chain directly to the next step without asking. See ask-before-assuming.md pre-answered table." >&2
fi

exit 0
