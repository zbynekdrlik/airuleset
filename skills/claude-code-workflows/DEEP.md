# Claude Code Workflows — Deep Reference (#859)

Companion to `modules/core/claude-code-tooling.md` — the `Workflow` tool's
authoring detail (when to reach for it, per-stage model tiering, fan-out
right-sizing) moved here VERBATIM so the always-on module stays lean. Load
this file when authoring, editing, or reasoning about a Workflow script.

#### Dynamic Workflows (the `Workflow` tool)

The `Workflow` tool runs a deterministic JS script that orchestrates many subagents — `parallel()` fan-out, `pipeline()` per-item stages, adversarial-verify loops, loop-until-dry. It is DISTINCT from `subagent-driven-development` (which dispatches sequential `Task` subagents one per plan task). Use a Workflow when the work is fan-out-shaped:

- **Reviews / audits** — N dimensions scanned in parallel, each finding verified by skeptics before it counts.
- **Research** — multi-modal search fan-out → deep-read → synthesize (see `deep-research`).
- **Migrations / sweeps** — discover sites → transform each (worktree isolation) → verify, across many files.
- **Design** — N independent attempts → judge panel → synthesize the winner.

Ultracode launch-flag policy evolution: `.claude/rules-reference/claude-code-tooling-history.md` (#859).

**Max acceleration is the DEFAULT doctrine.** When work is fan-out-shaped, orchestrate it: worktree fleet dispatch — parallel workers on disjoint lanes, integration strictly serial (one merge / one test cycle / one push per round, `two-branch-workflow.md` + the `autopilot` skill) — wherever the task allows; single-worker only when the task genuinely cannot parallelize (shared-state, strictly sequential dependencies, or a serial-fallback environment). Parallel lanes stay the default; the `/autopilot` loop refills a returned lane's slot immediately and compacts at every integration cycle, sizing the live lane set to what the box and backlog bear (the `autopilot` skill owns the full doctrine) so the supervisor's context stays bounded without waiting for a drained boundary. Parallel LANES themselves need no permission and stay the default — do NOT silently fall back to a single sequential pass where the task allows parallel lanes. Launching the full multi-agent **Workflow tool** is the one piece that, without a session ultracode flag (removed 2026-08-30), follows its standard opt-in — reach for it when the user invokes it in their own words, or when its orchestration genuinely beats inline agents and the session is one where that opt-in applies. Scale the mechanism to the work: for small/cheap fan-out just dispatch parallel `Agent` calls inline (always available); reach for a full Workflow where its orchestration (pipelines, adversarial-verify loops, resume) genuinely beats inline parallel agents. Right-sizing still applies in full (below) — max acceleration means PARALLEL LANES for independent work, never redundant re-derivation.

**Model per stage is your native choice (#991).** A stage's `opts.effort` scales with what the stage does (`claude-code-tooling.md` → Effort levels): high for judgment/hard work, low/medium for mechanical/read-only. A stage that omits `opts.model` inherits the fleet subagent default (`claude-opus-4-8`); an exact-id `opts.model` (never a banned model — Opus 5) overrides it. There is no per-stage tiering doctrine and no budget gate. Still shape a costly judgment stage as an ADVISOR call — a cheap stage grounds the sources into a tight digest, the judgment stage gets ONLY digest + question and returns the decision, an execution stage applies it — a stage that re-reads the sources to ground itself is the 2026-07-01 burn re-baked into a script.

**Right-size the fan-out, and GROUND ONCE — the dominant token sink is REDUNDANCY, not depth.** Three rules:
- **Ground ONCE, pass a digest — NEVER N agents each re-reading the same big files.** Read the source ONE time, return a TIGHT digest, pass it in each agent's prompt.
- **Size the fan-out to RESIDUAL UNCERTAINTY, not to thoroughness-by-reflex.** Ultracode buys DEPTH, never REDUNDANCY.
- **Per-item fan-out MULTIPLIES — bound it and don't re-ground per item.** Batch findings into ONE verify call, or pass only the finding + its local slice. History + real incident: `.claude/rules-reference/claude-code-tooling-history.md` (#859).

Anti-patterns: riding the `brainstorming → writing-plans → subagent-driven-development` chain for a review/audit/migration without noting that a Workflow would cover it in parallel; treating "ultracode off" as "Workflows unavailable" (you can still author a one-off when the user asks); baking a banned model (Opus 5) into any `opts.model`; fanning N agents that EACH re-read the same large files instead of grounding once into a shared digest (redundancy is waste — depth is higher effort, never N copies of the same read); re-sending the whole design/file to a fresh verifier PER finding (O(findings × context)); killing an over-scoped run and discarding its partial output instead of harvesting + resuming it (`salvage-before-discarding-work.md`); joining aggregate results (verdicts, findings, scores) by TITLE or NAME instead of by INDEX — a title-join silently drops entries whose titles were reformatted or truncated, producing false-clean verdicts (odoo-erp issue 1609/issue 1623 incident). Applies to all rewordings and semantic equivalents.
