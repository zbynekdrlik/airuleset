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
# `fork` is a built-in (forks the parent agent — inherits context, runs on the
# parent model); it is NOT a file-backed agent under ~/.claude/agents, so it must
# be allowlisted explicitly or a valid fork dispatch is wrongly blocked.
# Edit this list if a plugin ever ships real subagents (rare — most plugins ship skills, not agents).
case "$SUBAGENT_TYPE" in
    claude|claude-code-guide|Explore|general-purpose|Plan|statusline-setup|fork)
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

# Accept REAL plugin-provided subagents — `<plugin>:<agent>` is VALID iff the
# installed plugin's cache carries agents/<agent>.md (caveman 0d95a81d ships
# cavecrew-builder/investigator/reviewer; the old "plugin-prefixed = always a
# hallucination" assumption is stale). Glob BOTH cache layouts (<hash>/agents/
# and <hash>/src/agents/ — upstreams move their layout; a single glob rots).
# Both halves sanitized to bare basenames so a crafted name can't traverse paths.
case "$SUBAGENT_TYPE" in
    *:*)
        PLUGIN_PART=${SUBAGENT_TYPE%%:*}
        AGENT_PART=${SUBAGENT_TYPE#*:}
        PLUGIN_SAFE=$(printf '%s' "$PLUGIN_PART" | tr -cd 'A-Za-z0-9_-')
        AGENT_SAFE=$(printf '%s' "$AGENT_PART" | tr -cd 'A-Za-z0-9_-')
        if [ -n "$PLUGIN_SAFE" ] && [ "$PLUGIN_SAFE" = "$PLUGIN_PART" ] \
           && [ -n "$AGENT_SAFE" ] && [ "$AGENT_SAFE" = "$AGENT_PART" ]; then
            if compgen -G "$HOME/.claude/plugins/cache/*/${PLUGIN_SAFE}/*/agents/${AGENT_SAFE}.md" >/dev/null 2>&1 \
               || compgen -G "$HOME/.claude/plugins/cache/*/${PLUGIN_SAFE}/*/src/agents/${AGENT_SAFE}.md" >/dev/null 2>&1; then
                exit 0
            fi
        fi
        ;;
esac

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
echo "    - fork                 (forks the parent agent — inherits context)" >&2
echo "" >&2
echo "  A plugin-prefixed '<plugin>:<agent>' is valid ONLY when that plugin's installed cache actually ships agents/<agent>.md — this one does not, so the name is a hallucination." >&2
echo "" >&2
echo "  Notes:" >&2
echo "    - most plugins ship skills, NOT agents ('superpowers:implementer', 'superpowers:reviewer' = hallucinations)" >&2
echo "    - superpowers:subagent-driven-development uses general-purpose for ALL three roles" >&2
echo "      (implementer / spec-reviewer / code-quality-reviewer)" >&2
echo "    - Skills (superpowers:brainstorming, caveman:caveman, etc.) are NOT subagent types" >&2
echo "" >&2
echo "  Fix: re-dispatch with subagent_type='general-purpose' and pass the role-specific" >&2
echo "  instructions in the task prompt. See subagent-type-discipline.md." >&2
exit 2
