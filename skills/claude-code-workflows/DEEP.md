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

**When to launch it.** Launching the full multi-agent **Workflow tool** follows its standard opt-in (no session ultracode flag since 2026-08-30): reach for it when the user invokes it, or when its orchestration (pipelines, adversarial-verify loops, resume) genuinely beats inline `Agent` calls. Whether to fan out at all, how wide, and with worktrees or not is your own judgement (#1137).

**Model per stage is your native choice (#991).** A stage's `opts.effort` scales with what the stage does (`claude-code-tooling.md` → Effort levels): high for judgment/hard work, low/medium for mechanical/read-only. A stage that omits `opts.model` runs on the session's model (no forced airuleset default, #1173); an exact-id `opts.model` (never a banned model — Opus 5) overrides it. There is no per-stage tiering doctrine and no budget gate. Still shape a costly judgment stage as an ADVISOR call — a cheap stage grounds the sources into a tight digest, the judgment stage gets ONLY digest + question and returns the decision, an execution stage applies it — a stage that re-reads the sources to ground itself is the 2026-07-01 burn re-baked into a script.

**Right-size the fan-out, and GROUND ONCE — the dominant token sink is REDUNDANCY, not depth.** Three rules:
- **Ground ONCE, pass a digest — NEVER N agents each re-reading the same big files.** Read the source ONE time, return a TIGHT digest, pass it in each agent's prompt.
- **Size the fan-out to RESIDUAL UNCERTAINTY, not to thoroughness-by-reflex.** Ultracode buys DEPTH, never REDUNDANCY.
- **Per-item fan-out MULTIPLIES — bound it and don't re-ground per item.** Batch findings into ONE verify call, or pass only the finding + its local slice. History + real incident: `.claude/rules-reference/claude-code-tooling-history.md` (#859).

Anti-patterns: riding the `brainstorming → writing-plans → subagent-driven-development` chain for a review/audit/migration without noting that a Workflow would cover it in parallel; treating "ultracode off" as "Workflows unavailable" (you can still author a one-off when the user asks); baking a banned model (Opus 5) into any `opts.model`; fanning N agents that EACH re-read the same large files instead of grounding once into a shared digest (redundancy is waste — depth is higher effort, never N copies of the same read); re-sending the whole design/file to a fresh verifier PER finding (O(findings × context)); killing an over-scoped run and discarding its partial output instead of harvesting + resuming it (`salvage-before-discarding-work.md`); joining aggregate results (verdicts, findings, scores) by TITLE or NAME instead of by INDEX — a title-join silently drops entries whose titles were reformatted or truncated, producing false-clean verdicts (odoo-erp issue 1609/issue 1623 incident). Applies to all rewordings and semantic equivalents.
