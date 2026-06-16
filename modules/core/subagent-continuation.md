### Subagent Dispatch Is One-Shot — Never Depend on (or Narrate) `SendMessage`

**`SendMessage` (the documented tool for continuing a previously-spawned subagent) is GATED behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` and is NOT exposed by default.** Since Claude Code v2.1.77 the `Agent` tool's `resume` param was removed and the Agent result / system prompt tell you to use `SendMessage({to: agentId})` to follow up — but with the flag off (the default) that tool isn't in your tool list, so the call fails and you cold-start a fresh `Agent` dispatch. Confirmed CC limitation (anthropics/claude-code #35240, #37051, #38183, #42737, #50622, #61248).

**Therefore, treat EVERY subagent dispatch as one-shot:**

- **NEVER attempt `SendMessage` to "continue" / "follow up with" / "send the finding to" an existing worker.** It will not work without the agent-teams flag.
- **NEVER narrate the fallback.** Banned (the user has flagged this repeatedly): "SendMessage to that worker isn't available here, so I'm dispatching a fresh worker", "SendMessage isn't available here", "I'll send the decision to the existing worker", or any rewording. Just dispatch the fresh worker — silently.
- **Pass ALL needed context in the new dispatch's prompt** — the finding, the decision, the skill to enforce, the prior state. The fresh worker has none of the old one's context; the prompt is the only channel.
- **Make workers resume from DURABLE state, not in-process continuation** — the existing git branch / open PR, gh issue state, files on disk, the board/DB. A worker re-dispatched on the same task reads that state and continues; it does not restart from scratch and does not need the old conversation.

This applies to ALL flows — `/autopilot`, `subagent-driven-development`, ad-hoc `Agent`/`Task` dispatches, and any skill that fans work to a worker. If you find yourself wanting to "message the running worker", STOP: dispatch one fresh worker with the full context embedded. See [[subagent-type-discipline]] for the companion rule (never invent `subagent_type` names).

The only way to make `SendMessage` real is the user setting `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (experimental — changes the whole multi-agent model). Do not assume it is on. Applies to all rewordings and semantic equivalents.
