### Model Awareness (2026)

The primary Claude Code agent runs Opus 4.8, but subagents (`Task` tool) often run smaller models (Haiku, Sonnet). Rules must work for BOTH. (Opus 4.8 prompt guidance matches 4.7 — literalism behavior below holds; 4.8 is more concise, more grounded, more honest, needs less anti-slop frontend prompting.)

#### Model tiering — pick the model PER TASK automatically (cost discipline)

**Opus is the orchestrator, NOT the blanket default for every sub-task.** Running every subagent and every workflow stage on Opus 4.8 + xhigh — just because the main session is Opus — burned the weekly token limit with no quality gain on mechanical work. The agent itself selects the model per dispatch; this is automatic, never the user's job, and NEVER a `/model` toggle the user has to babysit.

**Conservative tiering (the user's chosen policy — quality of real work stays on Opus):**

- **Main interactive session = Opus 4.8.** The orchestrator stays strong. Never downtier it.
- **Anything that writes, edits, judges, or reasons about CODE / LOGIC = Opus.** Implementation subagents, the autopilot-worker, code-review/verify stages, design/synthesis — all Opus (omit the model override → inherit Opus). Quality is never traded away here.
- **Purely MECHANICAL / READ-ONLY work = cheap model (Sonnet, or Haiku for the most trivial) + low/medium effort.** File enumeration, log scraping, grep/locate sweeps, format-only edits, ticket-validation reads, "where is X" lookups, status polling. These need no Opus and no xhigh.
  - Agent tool: pass `model: "sonnet"` / `"haiku"` on read-only dispatches (e.g. `Explore`, `ticket-validator`). Implementation dispatches: omit (inherit Opus).
  - Workflow `agent()`: set `opts.model`/`opts.effort` per stage — cheap for mechanical stages, omit (inherit Opus) for code-logic stages.
- **When unsure which tier → Opus.** The cost saving is on PROVABLY mechanical work only; if a step might touch logic, keep it on Opus. Never gamble code quality to save tokens.

Applies to all rewordings and semantic equivalents — the intent: cheap model for cheap work, Opus for everything that touches the code, automatically, with zero manual model-switching by the user.

#### Opus 4.8 behavior (primary agent)

- **Literalism**: does exactly what rules say. Precise wording → high compliance. Vague wording → unpredictable.
- **Reasons more, calls tools less**: when a tool must be used (e.g., `gh run view` for CI monitoring, `Playwright` for E2E verification), the rule must say so explicitly.
- **Adaptive thinking** (no fixed budget): longer reasoning per turn, fewer back-and-forth rounds.
- **Default effort `high`** on all surfaces incl. Claude Code (per docs.anthropic.com/effort). The user runs a managed `xhigh` MAIN-session default by his own choice (his quality baseline — leave it; see [[user-max-autonomy-effort]]). What tiers is the effort of DISPATCHED sub-work: `xhigh`/`max` only for genuinely hard stages, `low`/`medium` for mechanical/read-only stages (which also get a cheap model — see the model-tiering section above). Don't dispatch every subagent/workflow stage at `xhigh` by reflex — that spends meaningfully more thinking tokens with no quality gain on mechanical work.

#### Subagent behavior (smaller models)

- Follow rules less reliably — they need explicit "do X, not Y" instructions.
- Benefit more from hook-level enforcement (100% compliance regardless of model).
- Benefit from pattern lists (banned phrases, anti-patterns) — literalism matters less when the model already pattern-matches.

#### How to write rules for both

- **Lead with the rule in precise terms** — 4.8 will follow it exactly.
- **Follow with anti-patterns / banned phrases** — helps subagents.
- **End with "applies to all rewordings and semantic equivalents"** — prevents 4.8 from taking bullet-point lists as exhaustive.
- **Critical enforcement goes in hooks**, not rules — works for every model.

#### Example

Bad (vague, subagent may miss): "Don't skip tests."
Good (precise + pattern + semantic gate):
> "NEVER use `#[ignore]`, `test.skip()`, or `pytest.mark.skip`. Any rewording or equivalent skip mechanism also applies."
