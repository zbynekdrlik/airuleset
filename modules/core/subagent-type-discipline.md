### Subagent Type Discipline — Never Invent Agent Names

**The `Agent` tool's `subagent_type` parameter MUST be one of the agent types listed in the Agent tool's own description in your environment. NEVER invent agent names. Hallucinated names burn tokens on silent fallback dispatches.**

#### Context — the failure mode

Agent dispatched `caveman:cavecrew-builder` for a "workspace version bump" task. No such agent exists in any installed plugin. Harness silently fell back to a default agent and ran the task to completion — **49.8k tokens spent on a real dispatch with the wrong agent**, then Claude noticed "Wrong agent type — has no Bash" and redid the work directly. The tokens, the time, and the wrong-tool result are all the user's loss.

Pattern: when active plugin/mode branding (e.g. caveman, superpowers) primes the language, the agent invents subagent names that match the plugin namespace. **caveman plugin has ZERO subagents.** Same for most plugins.

#### Mandatory rule — use ONLY the listed types

Before EVERY `Agent` tool call, read the available agent list in the Agent tool's description (top of your prompt). The list is the COMPLETE set of valid `subagent_type` values for the current environment.

**If the agent type you want is NOT in the list:**

- Use `general-purpose` (the safe default — has all tools).
- Or use one of the listed specialized types: `Explore` (read-only search), `Plan` (architect), `claude-code-guide` (Claude Code questions), `statusline-setup` (statusline config).

**Banned actions:**

- Inventing plugin-prefixed names — `caveman:cavecrew-builder`, `caveman:builder`, `superpowers:implementer`, `superpowers:reviewer`, `myplugin:specialist`. None of these are real agent types.
- Inferring agent names from plugin/mode branding ("we're in caveman mode → use `caveman:*` agent" — WRONG, caveman is a communication mode, not an agent provider).
- Guessing agent names from skill names (`superpowers:brainstorming` is a SKILL, not a subagent type).
- Reusing agent names from previous sessions without re-checking the current environment's list.

#### Correct pattern

```
1. Open the Agent tool description in your current prompt.
2. Read the list of subagent_type values verbatim.
3. Pick the one that matches your task — or use general-purpose if uncertain.
4. NEVER concatenate `<pluginname>:<role>` unless that exact string appears in the list.
```

#### Subagent-driven-development specifics

`superpowers:subagent-driven-development` uses `general-purpose` for all three roles (implementer / spec-reviewer / code-quality-reviewer). Its own `implementer-prompt.md` says: `Task tool (general-purpose):`. There is NO `superpowers:implementer` subagent type. Dispatch all three roles with `subagent_type: "general-purpose"` and the role-specific prompt file as the task instruction.

#### Why hallucinated names "work" but waste tokens

If you pass an unknown `subagent_type`, the harness may fall back to a default agent silently. The dispatch APPEARS to succeed — `Done (N tool uses · K tokens)` — but:

- You don't get the specialized tools/model the invented name implied (e.g. you wrote `cavecrew-builder` thinking it's a fast Haiku coder; you got a default agent instead).
- Tokens are spent. The user pays.
- Your follow-up reasoning may incorrectly assume the specialized agent ran (cascading errors).

A dispatch that runs but with the wrong agent is WORSE than a dispatch that fails fast.

#### Quick check before every Agent call

Ask yourself: "Did I see this exact `subagent_type` string in the Agent tool description in THIS prompt?" If no → use `general-purpose`. If yes → proceed.

Applies to all rewordings and semantic equivalents — any made-up `<plugin>:<agent>` string is banned regardless of plugin name.
