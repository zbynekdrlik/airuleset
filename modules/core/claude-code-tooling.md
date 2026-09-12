### Claude Code Tooling (2026)

Use built-in Claude Code features that accelerate autonomous work. Suggest them proactively when relevant.

History + rationale: `.claude/rules-reference/claude-code-tooling-history.md` (#859).

#### Auto Mode (Shift+Tab in CLI)

Permission-classifier that auto-approves safe commands and pauses on risky ones. Preferred over `--dangerously-skip-permissions`. Enable at the start of any long agentic session.

#### Effort levels

Adaptive thinking with five tiers: `low`, `medium`, `high`, `xhigh`, `max`. **The API default is `high`** on the top tiers (all surfaces, incl. Claude Code — the official docs say "start with `high`, the default"); reserve `xhigh` for work that genuinely needs the depth rather than reusing an effort setting carried over from an earlier model (docs.anthropic.com/effort, #56). Guidance:
- `max` — deep debugging, complex architecture, multi-file refactors (frontier problems only — overthinks structured tasks)
- `xhigh` — genuinely HARD coding/agentic work (deep search, multi-step reasoning); meaningfully higher token use than `high`, so NOT a blanket default — reserve it for work that needs the depth
- `high` — default; complex reasoning, difficult coding, agentic tasks
- `low`/`medium` — trivial edits, formatting fixes, simple commits, mechanical/read-only work

**Effort by what the stage IS.** The MAIN session runs the user's `/model` choice (managed default Fable 5.1) — leave it; raise effort per session with `/effort` when a task needs the depth. The subagent MODEL/TYPE/COUNT is the working model's decision, resolved natively (`CLAUDE_CODE_SUBAGENT_MODEL` gives the fleet default `claude-opus-4-8`, a per-dispatch `model` param overrides it, only Opus 5 is banned — `model-awareness.md`).

**ultracode** mode = `xhigh` + permission to launch multi-agent workflows (not a separate API tier); NO LONGER a managed launch flag (owner directive 2026-08-30) — sessions launch at effort `high`, ultracode is a per-session opt-in.

**Parallelism is the working model's decision** — run parallel lanes where the task allows it, sized to what the box and backlog bear (the box-resource signal is `cli_resource_guards`). Dynamic Workflows authoring detail: companion `skills/claude-code-workflows/DEEP.md` (#859).

#### Autonomous Goals (`/goal`)

`/goal <condition>` (Claude Code v2.1.139+) sets a completion condition and loops turn-after-turn WITHOUT user prompts until a fast evaluator model confirms it holds. The native mechanism for "don't stop until done" (`complete-planned-work.md`) — reach for it on verifiable-end-state work: drive CI to green, work an issue backlog until empty, migrate every call site until tests pass, split a god-file until each module is under the size cap.

The evaluator reads ONLY the conversation transcript — it does NOT run commands or read files. So the condition MUST be:

- **Transcript-provable** — `` `cargo test` exits 0 (shown in transcript) ``, NOT "the code is correct".
- **Gate-complete** — `all issues closed AND CI all-green AND PR mergeable+clean`, not just "feature works", or it declares done early.
- **Bounded** — append `…or stop after N turns`; there is no built-in max.
- **Evidence-surfaced** — print the test output / CI status / DOM read into the turn every cycle (`autonomous-verification.md`); no surfaced proof → evaluator can't confirm → infinite loop.

`/goal` IS a session-scoped Stop hook and fires ALONGSIDE existing Stop hooks (e.g. the completion-report prose check) — both run after every turn, neither overrides the other.

Do NOT use `/goal` for ambiguous-scope work needing user decisions (the loop has no one to ask) or anything gated on a destructive action. It is for verifiable execution, not design. Applies to all rewordings and semantic equivalents.

For the specific case of working a whole GitHub issue backlog hands-off — solve the WHOLE backlog one issue at a time until empty — use the **`/autopilot` skill**. It drives a `/goal` loop that dispatches each issue to an **in-session background `autopilot-worker` subagent** (`run_in_background: true` → main stays thin AND free/interactive; visible in the agent strip as `main` + `autopilot-worker`; **able to ask you the important per-issue questions directly** — CC's 2026-W26 change surfaces background-subagent prompts in the main session, which is how the loop works `needs-design`/`needs-decision` issues instead of skipping them, so the `/goal`-has-no-one-to-ask caveat above does not bite). After each issue (incl. after merge) it picks the next; it never pre-filters or refuses to start. Merging follows `pr-merge-policy.md` default auto-merge (opt-out marker `airuleset:merge=manual`); milestones ping per `milestone-notifications.md`.

#### `/loop` + Agent view + in-session subagents (3 distinct surfaces)

`/loop` (v2.1.72+) re-runs a standing prompt between turns: `/loop 5m <prompt>` fixed-interval, `/loop <prompt>` self-paced (1m–60m adaptive, ends itself when provably done), bare `/loop` runs the project's `.claude/loop.md`. Session-scoped, 7-day expiry, fires only while the session is idle.

Three DIFFERENT multi-agent surfaces — do not conflate them:
- **In-session subagents** (Agent/Task tool) — show in the **bottom agent strip** of the current session (`main` + `<subagent>` rows, `↑/↓` to select, `Enter` to view) and in `/agents` (Running tab) / `/tasks` (attach). **Foreground** subagents BLOCK the parent while they run — the parent can't accept input until they return (Claude Code 2.1.x makes a foreground dispatch synchronous; CC issue #71768). **Background** ones (`run_in_background: true`) run concurrently so the **PARENT STAYS INTERACTIVE**, and as of CC's **2026-W26** change their permission prompts/questions now **SURFACE in the parent session** (no longer auto-denied), so a background subagent CAN ask. Both kinds stay visible in the bottom strip (background is NOT the hidden `claude --bg` daemon). This is the surface the **`/autopilot`** skill uses — an in-session **background** `autopilot-worker` per issue (main stays free + thin; worker visible in the strip; it re-invokes the loop on completion).
- **Agent view** (`claude agents`, v2.1.139+) — a SEPARATE full-screen list of `claude --bg` background daemon sessions across projects (NOT the bottom strip), `--json` states working|blocked|done|failed. Use for handing off independent sessions and checking back.
- **Agent teams** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, experimental) — concurrent full sessions you switch between (Shift+Down) and message directly; for parallel independent work, not serial issue-by-issue.

Like `/goal`, only the USER can type `/loop` — a skill prints the line to paste.

#### Verification tools

The implementation tiers benefit from explicit verification paths:
- **Frontend**: Chromium extension or Playwright MCP
- **Backend**: test runners, DB inspectors
- **Desktop**: Computer Use

Wire these into the workflow so Claude can self-verify without asking you to "check it".

