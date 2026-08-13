### Claude Code Tooling (2026)

Use built-in Claude Code features that accelerate autonomous work. Suggest them proactively when relevant.

#### Auto Mode (Shift+Tab in CLI)

Permission-classifier that auto-approves safe commands and pauses on risky ones. Preferred over `--dangerously-skip-permissions`. Enable at the start of any long agentic session.

#### Effort levels

Adaptive thinking with five tiers: `low`, `medium`, `high`, `xhigh`, `max`. **The API default is `high`** on the top tiers (all surfaces, incl. Claude Code — the official docs say "start with `high`, the default"); reserve `xhigh` for work that genuinely needs the depth rather than reusing an effort setting carried over from an earlier model (docs.anthropic.com/effort, #56). Guidance:
- `max` — deep debugging, complex architecture, multi-file refactors (frontier problems only — overthinks structured tasks)
- `xhigh` — genuinely HARD coding/agentic work (deep search, multi-step reasoning); meaningfully higher token use than `high`, so NOT a blanket default — reserve it for work that needs the depth
- `high` — default; complex reasoning, difficult coding, agentic tasks
- `low`/`medium` — trivial edits, formatting fixes, simple commits, mechanical/read-only work

**Tier by what the stage IS, not by reflex.** The user's MAIN session runs their `/model` choice (managed launch default: Fable 5 — see `model-awareness.md` → Model tiering) — leave it; raise effort per session with `/effort` when a task needs the depth. Under the ACTIVE policy (2026-08-13 — **Opus 5 is BANNED**, never any `opus`-aliased dispatch): PLAN / DESIGN / REVIEW / hard-debug stages run **Fable 5 (`model: "fable"`) through the budget gate** — run `python3 ~/devel/airuleset/airuleset.py fable-gate` ONCE per judgment task: `OPEN` (exit 0) → fable at `xhigh`; `CLOSED` (exit 1, incl. missing/stale cache) → the same work runs **Opus 4.8** (`claude-opus-4-8` — via agent-definition frontmatter, a Workflow stage's full-id `opts.model`, or inheritance from a `claude-opus-4-8` parent; a Fable MAIN at gate CLOSED holds the judgment itself). EXECUTION stages (implement a settled plan, scoped edits, transforms) run **Opus 4.8 at `high`/`xhigh`**; purely mechanical/read-only stages run Opus 4.8 at `low`/`medium` — `model: "sonnet"`/Haiku only for genuinely trivial lookups, never anything complex. Never dispatch an automatic Fable stage without the gate.

Set with `/model` in CLI. **ultracode** mode = `xhigh` + permission to launch multi-agent workflows (not a separate API tier) — and since 2026-08-13 it is the managed STANDING DEFAULT, not a per-session opt-in (see the Dynamic Workflows section below).

#### Dynamic Workflows (the `Workflow` tool)

The `Workflow` tool runs a deterministic JS script that orchestrates many subagents — `parallel()` fan-out, `pipeline()` per-item stages, adversarial-verify loops, loop-until-dry. It is DISTINCT from `subagent-driven-development` (which dispatches sequential `Task` subagents one per plan task). Use a Workflow when the work is fan-out-shaped:

- **Reviews / audits** — N dimensions scanned in parallel, each finding verified by skeptics before it counts.
- **Research** — multi-modal search fan-out → deep-read → synthesize (see `deep-research`).
- **Migrations / sweeps** — discover sites → transform each (worktree isolation) → verify, across many files.
- **Design** — N independent attempts → judge panel → synthesize the winner.

**Ultracode is the STANDING DEFAULT on every managed session (user directive 2026-08-13, verbatim):** *"chcel by som aby by default vzdy bol ultracode a kazdy claude spravne pouzival multiple git worktreee a mergovanie spolu a maximalizoval vyuzitie subagentov, cize by default chcem aby vsade kde na targete sa robi tak aby vzdy sa islo maximalnou akceleraciou a ak to dana uloha dovoli sa pracovalo paralelne"*. Every managed launch carries `--settings '{"ultracode":true}'` + `effortLevel: xhigh` (both halves of ultracode — the launcher's `plain` mode is the only vanilla escape hatch; `airuleset.py` #445, reversing #53's session-only opt-in on this dated instruction). The standing permission to run multi-agent Workflows/orchestration is ALREADY GRANTED on every session — never ask for it, never treat it as a capability the user must first switch on.

**Max acceleration is the DEFAULT doctrine.** When work is fan-out-shaped, orchestrate it: worktree fleet dispatch — parallel workers on disjoint lanes, integration strictly serial (one merge / one test cycle / one push per round, `two-branch-workflow.md` + the `autopilot` skill) — wherever the task allows; single-worker only when the task genuinely cannot parallelize (shared-state, strictly sequential dependencies, or a serial-fallback environment). The old "stop and ask to switch to ultracode" step is RETIRED — do NOT silently fall back to a single sequential pass where the task allows parallel lanes, and do NOT pause to request an orchestration permission that is already standing. Scale the mechanism to the work: for small/cheap fan-out just dispatch parallel `Agent` calls inline; reach for a full Workflow where its orchestration (pipelines, adversarial-verify loops, resume) genuinely beats inline parallel agents. Right-sizing still applies in full (below) — max acceleration means PARALLEL LANES for independent work, never redundant re-derivation.

**Tier the model PER STAGE inside the script (`model-awareness.md` → Model tiering).** This is the Workflow-specific APPLICATION of that general policy — the deliberate restatement in `opts.model`/`opts.effort` terms is INTENTIONAL (general tiers there, their per-`agent()`-stage form here), not a dedup target. Set `agent()` `opts.model`/`opts.effort` per stage by what the stage actually does:
- DESIGN / SYNTHESIS / ARCHITECTURE / adversarial-VERIFY / final-REVIEW / hard-debug stages (deciding WHAT to build + judging whether it is correct) → **`opts.model: 'fable'` at `opts.effort: 'xhigh'` — but ONLY when the budget gate is OPEN.** Workflow scripts cannot exec — so YOU (the orchestrator) run `python3 ~/devel/airuleset/airuleset.py fable-gate` BEFORE authoring the script and bake the result in: gate OPEN → the judgment stages get `opts.model: 'fable'`; gate CLOSED → those stages get `opts.model: 'claude-opus-4-8'` (full model names are legal in `opts.model` per the SDK docs — never the banned `opus` alias). Never let a cheaper tier make a judgment call that shapes the result — and never bake in an ungated Fable stage. **Shape every Fable stage as an ADVISOR call: digest in, decision out** — a cheap stage grounds the sources into a tight digest first, the Fable stage receives ONLY digest + question and returns the judgment, an execution stage applies it (`model-awareness.md` → SHAPE). A Fable stage that grounds itself by re-reading the sources is the 2026-07-01 burn re-baked into a script.
- EXECUTION stages — implement a settled plan, scoped edits, code transforms/migrations → `opts.model: 'claude-opus-4-8'` + `opts.effort: 'high'`/`'xhigh'`. Quality held by HIGH effort + the gated Fable judgment bookends.
- Purely mechanical / read-only stages (file/site discovery, log/grep sweeps, status collection, format-only transforms) → `opts.model: 'claude-opus-4-8'` + `opts.effort: 'low'`/`'medium'` (`'sonnet'`/`'haiku'` only for the genuinely most trivial lookups — Sonnet never carries anything complex).

**Right-size the fan-out, and GROUND ONCE — the dominant token sink is REDUNDANCY, not depth.** A real incident: a review Workflow fanned 6 agents that EACH re-read the same three ~1500-line CI files (≈4,500 lines × 6) plus the full design, then spawned a fresh verifier PER finding that re-received the whole design again — ~5 MB of tokens, all on Opus, for a design the user had already hand-converged. Three rules prevent it:
- **Ground ONCE, pass a digest — never N agents each re-reading the same big files.** When every fan-out agent needs the SAME large source (the CI YAMLs, the design doc, the log bundle), read it ONE time — a single cheap `sonnet` stage (or one inline read) that returns a TIGHT digest — and pass that digest in each agent's prompt. N agents × the same 4,500-line re-read is N× the input cost for one body of context, and is the single biggest waste in practice.
- **Size the fan-out to RESIDUAL UNCERTAINTY, not to thoroughness-by-reflex.** A 6-reviewer panel + per-finding adversarial verify is for high-stakes UNKNOWNS (a security audit, an unproven design). Work the user has already vetted / hand-converged needs ONE focused pass, not a fleet. **Ultracode buys DEPTH, never REDUNDANCY** — "cost is not the constraint" does NOT license N agents re-deriving the same thing; that is waste, not rigor.
- **Per-item fan-out MULTIPLIES — bound it and don't re-ground per item.** A verify/refine step that re-sends the entire design (or the whole file) to a fresh agent PER finding is O(findings × context). Batch the findings into ONE verify call, or pass only the finding + its local slice — never the whole body again per item.

Anti-patterns: riding the `brainstorming → writing-plans → subagent-driven-development` chain for a review/audit/migration without noting that a Workflow would cover it in parallel; treating "ultracode off" as "Workflows unavailable" (you can still author a one-off when the user asks); putting a DESIGN/REVIEW judgment stage on `sonnet`/`haiku`, reaching for the banned `opus` alias anywhere, running `fable` on a mechanical stage, or dispatching an automatic `fable` stage WITHOUT the `fable-gate` check (an ungated automatic Fable burn is the exact 2026-07-01 failure — `model-awareness.md`); fanning N agents that EACH re-read the same large files instead of grounding once into a shared digest (redundancy is waste under EVERY policy — depth is a stronger model + higher effort, never N copies of the same read); re-sending the whole design/file to a fresh verifier PER finding (O(findings × context)); killing an over-scoped run and discarding its partial output instead of harvesting + resuming it (`salvage-before-discarding-work.md`). Applies to all rewordings and semantic equivalents.

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

4.8 benefits from explicit verification paths:
- **Frontend**: Chromium extension or Playwright MCP
- **Backend**: test runners, DB inspectors
- **Desktop**: Computer Use

Wire these into the workflow so Claude can self-verify without asking you to "check it".

