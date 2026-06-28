### Claude Code Tooling (2026)

Use built-in Claude Code features that accelerate autonomous work. Suggest them proactively when relevant.

#### Auto Mode (Shift+Tab in CLI)

Permission-classifier that auto-approves safe commands and pauses on risky ones. Preferred over `--dangerously-skip-permissions`. Enable at the start of any long agentic session.

#### Effort levels

Adaptive thinking with five tiers: `low`, `medium`, `high`, `xhigh`, `max`. **Default is `high`** on Opus 4.8 (all surfaces, incl. Claude Code). Guidance:
- `max` — deep debugging, complex architecture, multi-file refactors (frontier problems only — overthinks structured tasks)
- `xhigh` — genuinely HARD coding/agentic work (deep search, multi-step reasoning); meaningfully higher token use than `high`, so NOT a blanket default — reserve it for work that needs the depth
- `high` — default; complex reasoning, difficult coding, agentic tasks
- `low`/`medium` — trivial edits, formatting fixes, simple commits, mechanical/read-only work

**Tier effort on DISPATCHED sub-work, don't blanket-`xhigh` it.** The user's MAIN session runs `xhigh` by his own managed default (his deliberate quality baseline — leave it; see `model-awareness.md` → Model tiering). The tiering here is what the AGENT sets on the subagents and workflow stages IT dispatches: mechanical/read-only stages pair a cheap model with `low`/`medium` effort; code-logic stages keep the inherited tier. Don't dispatch every subagent/stage at `xhigh` by reflex — that spends a lot of thinking tokens with no quality gain on mechanical work.

Set with `/model` in CLI. **ultracode** mode = `xhigh` + standing permission to launch multi-agent workflows (not a separate API tier).

#### Dynamic Workflows (the `Workflow` tool)

The `Workflow` tool runs a deterministic JS script that orchestrates many subagents — `parallel()` fan-out, `pipeline()` per-item stages, adversarial-verify loops, loop-until-dry. It is DISTINCT from `subagent-driven-development` (which dispatches sequential `Task` subagents one per plan task). Use a Workflow when the work is fan-out-shaped:

- **Reviews / audits** — N dimensions scanned in parallel, each finding verified by skeptics before it counts.
- **Research** — multi-modal search fan-out → deep-read → synthesize (see `deep-research`).
- **Migrations / sweeps** — discover sites → transform each (worktree isolation) → verify, across many files.
- **Design** — N independent attempts → judge panel → synthesize the winner.

**Opt-in is harness-level, not rule-level** — a Workflow only fires when ultracode is ON, the user types "workflow"/"fan out", or a skill calls it. The agent CANNOT enable ultracode itself; only the user can (`/effort ultracode`).

**STOP and ASK for ultracode when you'd benefit from it.** When a task is fan-out-shaped and would materially benefit from Workflow orchestration (parallel dimensions, pipeline + adversarial verify, loop-until-dry, scale beyond one context) but ultracode is OFF, **stop and ask the user to switch to ultracode before proceeding** — do NOT silently fall back to a single sequential pass and leave the parallel tool unused. Use `AskUserQuestion` with a one-line why, e.g. "This audit fans out across N dimensions — switch to ultracode so I run it as a parallel Workflow?" The user has explicitly said: when you'd like ultracode, stop and ask for it.

This ask is NOT a banned "shall I proceed?" process-pause. It is a request for a **capability only the user can grant** (same shape as "ask for the missing tool" in `autonomous-verification.md`) — so it is exempt from the `ask-before-assuming.md` / `autonomous-quality-discipline.md` bans on process questions. Scale the response to the work: for small/cheap fan-out just dispatch parallel `Agent` calls inline (no interruption needed); reserve the stop-and-ask for work where Workflow's orchestration genuinely beats inline parallel agents.

**Tier the model PER STAGE inside the script (cost discipline).** A workflow fanned out over Opus + xhigh on every stage was a major token sink with no quality gain on mechanical stages. Set `agent()` `opts.model`/`opts.effort` per stage by what the stage actually does (the conservative policy in `model-awareness.md` → Model tiering):
- Mechanical / read-only stages (file/site discovery, log/grep sweeps, status collection, format-only transforms, ticket-validation reads) → `opts.model: 'sonnet'` (or `'haiku'` for the most trivial) + `opts.effort: 'low'`/`'medium'`.
- Stages that read, write, judge, or synthesize CODE / LOGIC (implement, review, verify, design-synthesis) → omit the override (inherit the Opus main-loop model). Quality is never downtiered here.
- When unsure → omit (inherit Opus). The saving is on provably-mechanical stages only; never gamble code quality to save tokens.

Anti-patterns: riding the `brainstorming → writing-plans → subagent-driven-development` chain for a review/audit/migration without noting that a Workflow would cover it in parallel; treating "ultracode off" as "Workflows unavailable" (you can still author a one-off when the user asks); running every workflow stage on Opus+xhigh when half the stages are mechanical. Applies to all rewordings and semantic equivalents.

#### Autonomous Goals (`/goal`)

`/goal <condition>` (Claude Code v2.1.139+) sets a completion condition and loops turn-after-turn WITHOUT user prompts until a fast evaluator model confirms it holds. The native mechanism for "don't stop until done" (`complete-planned-work.md`) — reach for it on verifiable-end-state work: drive CI to green, work an issue backlog until empty, migrate every call site until tests pass, split a god-file until each module is under the size cap.

The evaluator reads ONLY the conversation transcript — it does NOT run commands or read files. So the condition MUST be:

- **Transcript-provable** — `` `cargo test` exits 0 (shown in transcript) ``, NOT "the code is correct".
- **Gate-complete** — `all issues closed AND CI all-green AND PR mergeable+clean`, not just "feature works", or it declares done early.
- **Bounded** — append `…or stop after N turns`; there is no built-in max.
- **Evidence-surfaced** — print the test output / CI status / DOM read into the turn every cycle (`autonomous-verification.md`); no surfaced proof → evaluator can't confirm → infinite loop.

`/goal` IS a session-scoped Stop hook and fires ALONGSIDE existing Stop hooks (e.g. the completion-report prose check) — both run after every turn, neither overrides the other.

Do NOT use `/goal` for ambiguous-scope work needing user decisions (the loop has no one to ask) or anything gated on a destructive action. It is for verifiable execution, not design. Applies to all rewordings and semantic equivalents.

For the specific case of working a whole GitHub issue backlog hands-off — solve the WHOLE backlog one issue at a time until empty — use the **`/autopilot` skill**. It drives a `/goal` loop that dispatches each issue to a **foreground `autopilot-worker` subagent** (fresh context → main stays thin; visible in the agent strip as `main` + `autopilot-worker`; **able to ask you the important per-issue questions directly** — which is how the loop works `needs-design`/`needs-decision` issues instead of skipping them, so the `/goal`-has-no-one-to-ask caveat above does not bite). After each issue (incl. after merge) it picks the next; it never pre-filters or refuses to start. Merging follows `pr-merge-policy.md` default auto-merge (opt-out marker `airuleset:merge=manual`); milestones ping per `milestone-notifications.md`.

#### `/loop` + Agent view + in-session subagents (3 distinct surfaces)

`/loop` (v2.1.72+) re-runs a standing prompt between turns: `/loop 5m <prompt>` fixed-interval, `/loop <prompt>` self-paced (1m–60m adaptive, ends itself when provably done), bare `/loop` runs the project's `.claude/loop.md`. Session-scoped, 7-day expiry, fires only while the session is idle.

Three DIFFERENT multi-agent surfaces — do not conflate them:
- **In-session subagents** (Agent/Task tool) — show in the **bottom agent strip** of the current session (`main` + `<subagent>` rows, `↑/↓` to select, `Enter` to view) and in `/agents` (Running tab) / `/tasks` (attach). **Foreground** subagents pass their prompts/questions through to you (can ask); **background** ones run concurrently but auto-deny prompts (can't ask). This is the surface the **`/autopilot`** skill uses — a foreground `autopilot-worker` per issue.
- **Agent view** (`claude agents`, v2.1.139+) — a SEPARATE full-screen list of `claude --bg` background daemon sessions across projects (NOT the bottom strip), `--json` states working|blocked|done|failed. Use for handing off independent sessions and checking back.
- **Agent teams** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, experimental) — concurrent full sessions you switch between (Shift+Down) and message directly; for parallel independent work, not serial issue-by-issue.

Like `/goal`, only the USER can type `/loop` — a skill prints the line to paste.

#### `/fewer-permission-prompts` skill

Analyzes session history, identifies safe Bash/MCP commands that keep triggering prompts, and suggests allowlist additions. Run periodically (monthly) to reduce friction.

#### `/focus` mode

Hides intermediate tool calls, shows only final results. Useful for long runs where you trust the model to do the right thing.

#### Recaps

Brief summaries of agent activity. Enabled by default. Review before resuming work after a break. Disable via `/config` if noisy.

#### Verification tools

4.8 benefits from explicit verification paths:
- **Frontend**: Chromium extension or Playwright MCP
- **Backend**: test runners, DB inspectors
- **Desktop**: Computer Use

Wire these into the workflow so Claude can self-verify without asking you to "check it".

#### `--channels` flag

Starts Claude Code with a push-message channel (Discord, Telegram, iMessage). Enables two-way remote control from chat platforms. Use when working away from the terminal.

```bash
claude --channels plugin:discord@claude-plugins-official
```
