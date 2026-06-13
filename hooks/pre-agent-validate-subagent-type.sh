#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent)
# Hard-blocks hallucinated `subagent_type` values like `caveman:cavecrew-builder`.
# Only accepts known-valid base types from Anthropic's stock Agent tool definition.
# Exit 2 = block. Claude sees stderr as the reason.
#
# Context: agents have hallucinated names like `caveman:cavecrew-builder`,
# `superpowers:implementer`, `myplugin:role`. Harness falls back silently to
# a default agent — the dispatch APPEARS to succeed and tokens are spent, but
# the wrong agent runs. Block at PreToolUse so the agent fails fast and picks
# the right type.

command -v jq &>/dev/null || exit 0

INPUT=$(cat)
SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || echo "")
[ -z "$SUBAGENT_TYPE" ] && exit 0

# Allowlist: known-valid base agent types from stock Agent tool.
# Edit this list if a plugin ever ships real subagents (rare — most plugins ship skills, not agents).
case "$SUBAGENT_TYPE" in
    claude|claude-code-guide|Explore|general-purpose|Plan|statusline-setup)
        exit 0
        ;;
esac

# Accept REAL installed subagents — a definition file exists for them. User-level
# (~/.claude/agents/<name>.md, e.g. airuleset-managed autopilot-worker) or project-level
# (.claude/agents/<name>.md). The hook's job is to block HALLUCINATED names, not real agents.
# Sanitize to a bare basename ([A-Za-z0-9_-]) so a crafted name can't traverse paths; this
# also rejects plugin-prefixed `<plugin>:<role>` (the ':' is stripped, so it won't equal the
# original and falls through to the block below).
AGENT_BASENAME=$(printf '%s' "$SUBAGENT_TYPE" | tr -cd 'A-Za-z0-9_-')
if [ -n "$AGENT_BASENAME" ] && [ "$AGENT_BASENAME" = "$SUBAGENT_TYPE" ]; then
    if [ -f "$HOME/.claude/agents/${AGENT_BASENAME}.md" ] || [ -f ".claude/agents/${AGENT_BASENAME}.md" ]; then
        exit 0
    fi
fi

# Unknown / hallucinated subagent_type. Block.
echo "BLOCKED: subagent_type '$SUBAGENT_TYPE' is not in the known-valid agent-type list." >&2
echo "" >&2
echo "  Valid base types (from the Agent tool description in your prompt):" >&2
echo "    - claude               (catch-all)" >&2
echo "    - claude-code-guide    (Claude Code questions)" >&2
echo "    - Explore              (read-only search)" >&2
echo "    - general-purpose      (default — has all tools, safe pick)" >&2
echo "    - Plan                 (architect, plans implementations)" >&2
echo "    - statusline-setup     (statusline config)" >&2
echo "" >&2
echo "  Plugin-prefixed names like 'caveman:cavecrew-builder', 'caveman:builder', 'superpowers:implementer', 'superpowers:reviewer', '<plugin>:<role>' are NOT valid agent types — they are hallucinations." >&2
echo "" >&2
echo "  Notes:" >&2
echo "    - caveman plugin has ZERO subagents (it's a communication mode, not an agent provider)" >&2
echo "    - superpowers:subagent-driven-development uses general-purpose for ALL three roles" >&2
echo "      (implementer / spec-reviewer / code-quality-reviewer)" >&2
echo "    - Skills (superpowers:brainstorming, caveman:caveman, etc.) are NOT subagent types" >&2
echo "" >&2
echo "  Fix: re-dispatch with subagent_type='general-purpose' and pass the role-specific" >&2
echo "  instructions in the task prompt. See subagent-type-discipline.md." >&2
exit 2
