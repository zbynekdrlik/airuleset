# /mdreview research — 2026-05-01

## Sources fetched (10 high-signal)

### S1 — Anthropic official Claude Code best practices
- URL: https://code.claude.com/docs/en/best-practices
- Author: Anthropic
- Date: continuously updated; canonical SOTA
- Tags: `[autonomy] [memory] [hooks] [skills] [subagent]`
- Summary:
  - Single highest-leverage practice: give Claude a way to verify its own work (tests/screenshots/expected outputs).
  - Plan Mode for medium/large tasks; skip for one-line diffs.
  - CLAUDE.md must be SHORT and human-readable. "Bloated CLAUDE.md files cause Claude to ignore your actual instructions."
  - "If Claude keeps doing something you don't want despite having a rule against it, the file is probably too long and the rule is getting lost."
  - Hooks > CLAUDE.md for "actions that must happen every time with zero exceptions."
  - Use `IMPORTANT` / `YOU MUST` for emphasis.
  - Skills via `disable-model-invocation: true` for manual-only workflows.
  - `/btw` — side questions don't enter conversation context.
  - "Kitchen sink" / "infinite exploration" / "over-specified CLAUDE.md" / "trust-then-verify gap" listed as common failure patterns.

### S2 — Opus 4.7 prompt-engineering shift (MindStudio)
- URL: https://www.mindstudio.ai/blog/how-to-prompt-claude-opus-4-7
- Date: 2026-04-22
- Tags: `[model:4.7]`
- Summary:
  - 4.7 takes instructions LITERALLY. 4.6 inferred / filled gaps.
  - "Improve this proposal" → 4.6 rewrites whole thing; 4.7 fixes word choice only.
  - Migration concern: prompts that relied on loose interpretation now produce minimal output.
  - "Precision beats verbosity. The goal isn't longer prompts — it's prompts with fewer ambiguous gaps."

### S3 — Opus 4.7 vs 4.6 differences (multiple sources)
- URLs: https://www.mindstudio.ai/blog/claude-opus-47-vs-46-what-changed, https://llm-stats.com/blog/research/claude-opus-4-7-vs-opus-4-6
- Tags: `[model:4.7] [tokens]`
- Summary:
  - SWE-bench Verified: 80.8% → 87.6% (+6.8 pts).
  - 3× more production tasks resolved on Rakuten-SWE-Bench.
  - **NEW TOKENIZER**: same input text now produces 1.0×–1.35× more tokens. Existing token budgets may underestimate consumption.
  - Vision: 1568 → 2576 px max; coords 1:1 with pixels.
  - Spawns FEWER subagents by default.
  - **Better at file-system-based memory** natively.
  - Tone: more direct/opinionated, fewer emoji.

### S4 — HumanLayer "Writing a good CLAUDE.md"
- URL: https://www.humanlayer.dev/blog/writing-a-good-claude-md
- Date: 2025-11-25
- Tags: `[memory] [bloat]`
- Summary:
  - **Instruction budget: 150-200 instructions max**. Claude Code system prompt already ~50.
  - **Length target: <300 lines max**. HumanLayer's own: <60.
  - WHAT/WHY/HOW framework: tech stack / project intent / dev workflow.
  - "Never send an LLM to do a linter's job" — strip lint/style rules from CLAUDE.md.
  - "Prefer pointers to copies" — reference @docs/ rather than embedding.
  - Progressive disclosure: skill loaded on demand beats always-on rule.

### S5 — 2026 CLAUDE.md architecture (obviousworks)
- URL: https://www.obviousworks.ch/en/designing-claude-md-right-the-2026-architecture-that-finally-makes-claude-code-work/
- Tags: `[memory] [hooks] [skills]`
- Summary:
  - Boris Cherny (Anthropic Staff Engineer) production CLAUDE.md: ~100 lines / 2500 tokens.
  - **>5000 tokens is "almost always too many"**.
  - **Hooks 100% enforced; CLAUDE.md ~70% followed.**
  - 4 layers: CLAUDE.md / Skills / Hooks / AGENTS.md. 5 scopes (global → folder).
  - Compound engineering: every correction → new rule → mistake never repeats.
  - Update monthly.

### S6 — Anthropic best practices (mirror)
- URL: https://www.anthropic.com/engineering/claude-code-best-practices
- Tags: `[autonomy]`
- Same content as S1 (redirects).

### S7 — Claude Code hooks reference (official)
- URL: https://code.claude.com/docs/en/hooks
- Tags: `[hooks]`
- Summary:
  - **31 hook events** in 2026. Up from earlier ~12-22.
  - New: Elicitation, ElicitationResult, TaskCreated, TaskCompleted, TeammateIdle, WorktreeCreate, WorktreeRemove, PreCompact, PostCompact, ConfigChange, CwdChanged, FileChanged, InstructionsLoaded, Setup, SessionEnd, StopFailure, PostToolUseFailure, PostToolBatch, PermissionRequest, PermissionDenied, SubagentStart, SubagentStop, UserPromptExpansion.
  - **Stop hook CAN block via `{"decision":"block","reason":"..."}` — "Prevents Claude from stopping, continues the conversation"**. Previously believed to cause infinite loops; that was a misconception.
  - **PreToolUse new `defer` decision** (v2.1.89+) — defers tool to external processing.
  - Notification matchers: `permission_prompt | idle_prompt | auth_success | elicitation_dialog | elicitation_complete | elicitation_response`. NO bash-bg-complete event.
  - SessionStart matchers: startup | resume | clear | compact.

### S8 — Karpathy / autoresearch / 80% AI-coded
- URLs: https://medium.com/@k.balu124/i-turned-andrej-karpathys-autoresearch-into-a-universal-skill-1cb3d44fc669, https://github.com/forrestchang/andrej-karpathy-skills
- Tags: `[autonomy] [memory]`
- Summary:
  - Karpathy: 80% of his code is AI-generated as of Dec 2025.
  - Karpathy framing: "LLM is CPU, context window is RAM, you are OS responsible for loading right info per task."
  - Karpathy CLAUDE.md principles: "Don't assume. Don't hide confusion. Surface tradeoffs."
  - Autoresearch (Mar 2026) — open-source pattern for autonomous research loops.

### S9 — Subagent best practices
- URL: https://code.claude.com/docs/en/sub-agents and https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/
- Tags: `[subagent] [autonomy]`
- Summary:
  - Skill in 2026 = decomposing work, not writing better prompts.
  - Subagent tools should be SCOPED (read-only for review/audit; full only when domain demands).
  - Roles: Product Spec / Architect / Implementer-Tester / QA. Chained via hooks.
  - Cost optimization: cheaper models for read-only exploration; capable models for decisions.
  - Parallel for independent domains; serial for high-risk steps.
  - **In 4.7, fewer subagents spawn by default** — explicit dispatch needed.

### S10 — Simon Willison on 4.6→4.7 system prompt diff
- URL: https://simonwillison.net/2026/Apr/18/opus-system-prompt/
- Date: 2026-04-18
- Tags: `[model:4.7]`
- Summary:
  - 4.7 system prompt adds `tool_search` mechanism. Claude should "call tool_search to check whether a relevant tool is available but deferred" before claiming lack of capability. (We already see this in current session.)
  - Action-first: "the person typically wants Claude to make a reasonable attempt now, not to be interviewed first."
  - Conciseness directive: "focused and concise so as to avoid potentially overwhelming the user."
  - Removed: "avoid emotes/asterisks", banned phrase list (e.g. "genuinely") — those behaviors no longer present natively.

## Cross-cutting takeaways (used in Step 3)

1. **Bloat is the #1 published anti-pattern**. Multiple SOTA sources converge: ≤200 lines, ≤2500 tokens, prune ruthlessly. Our resolved CLAUDE.md is currently >> that — measure in Step 2.
2. **Literalism in 4.7 → existing precise + anti-pattern + "intent" rules HELP, not hurt**. But generic vague rules degrade more visibly.
3. **Hooks deterministic > CLAUDE.md advisory**. Convert any rule the agent still violates → hook. (Aligns with our existing direction.)
4. **Stop hook CAN block** — long-held belief otherwise was wrong. Could enable stronger completion-report enforcement.
5. **PreCompact / PostCompact / SessionEnd / ConfigChange** hooks are new and unused by us.
6. **No upstream Discord-bg-complete hook pattern** — must be custom. Probably easiest fix: a SessionEnd or Stop hook that checks for unfinished bg jobs and emits notification.
7. **Tokenizer change (1.0×-1.35×)** — every existing token budget should be re-checked; rules with hard token caps may be tighter than intended.
8. **`disable-model-invocation: true` skill flag** — useful for skills like /mdreview itself (manual-only).
9. **`/btw` for side questions** — keeps main context clean.
10. **Karpathy's "Don't assume / don't hide confusion / surface tradeoffs"** — already encoded in `ask-before-assuming.md` and `autonomous-quality-discipline.md`.
