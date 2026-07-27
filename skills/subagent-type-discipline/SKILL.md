---
name: subagent-type-discipline
description: Never invent Agent tool subagent_type names — use only the types listed in the Agent tool's own description, or general-purpose. Load before dispatching any Agent/Task subagent, especially when plugin/mode branding (caveman, superpowers) might tempt inventing a plugin-prefixed agent name.
user-invocable: false
---

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

#### Dispatch with the LEAST tool authority the task needs (#49)

**A read-only task gets a read-only agent. `Explore` has every tool EXCEPT `Agent`/`Edit`/`Write`/`NotebookEdit` — that removal is enforced by the harness, so it holds no matter what the subagent decides mid-run.** Use it for review, audit, verification, "find/read/report" work. Reserve `general-purpose` (tool list `*`) for work that genuinely must WRITE.

**The prompt is not enforcement.** "Please only report", "do not commit", "no push" are text a subagent may reason its way past — only the agent TYPE's tool list actually constrains it. Real incident (#49, parovanie-produktov PR #228, 2026-07-25): a diff-review subagent dispatched `general-purpose` for a task whose prompt asked for a verification report wrote 8 assert-free scratch probe tests into the worktree and committed on its own. Its content happened to be correct; the authority was not.

**After ANY subagent run, `git status` BEFORE `git add -A`.** A write-capable subagent can leave scratch files behind, and a blanket add sweeps them into your commit — in #49 that is exactly how assert-free tests reached a branch, where they would have run forever in CI as false coverage. Commit named paths, or read the status first and delete what you did not intend.

#### `fork` continues the WHOLE task — a bounded side-task gets a FRESH dispatch (#50)

**`fork` inherits your ENTIRE conversation, including the parts you have NOT executed yet** — a ready-to-run command line you were about to send, a plan you laid out three steps ago, your own meta-instructions about how to behave as an agent. Those read to the fork as live directives, so a fork handed a narrow task can go and do your PENDING broader task instead. Real incident (#50, restreamer, 2026-07-25): a fork dispatched late in a long session for a 2-call post-deploy check instead spawned a nested agent, started polling its own transcript for "fork completion status" the way the parent's rules described, appears to have fired the three `notify --run-card` commands that were sitting in the inherited context unsent, and returned a bare `⏳ WORKING` placeholder with no verification in it.

Pick by SCOPE, not by convenience:

- **`fork`** — you want the WHOLE remaining task continued with your context and cache intact (that is what its full inheritance is FOR).
- **a FRESH dispatch** (`Explore` / `general-purpose`, self-contained prompt) — you want ONE bounded side-task done in isolation. It is safer here precisely BECAUSE it does not inherit your unexecuted plans; it starts from the always-on rules plus the prompt you wrote. The later you are in a long session, the bigger the difference.
