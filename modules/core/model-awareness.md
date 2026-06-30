### Model Awareness (2026)

The primary Claude Code agent runs Opus 4.8; **dispatched sub-work defaults to Sonnet 5** (`claude-sonnet-5`, the `sonnet` alias). Sonnet 5 (released 2026-06-30; 1M context; the SAME full effort range `low`–`max` as Opus 4.8) scores 63.2% on SWE-bench Pro vs Opus 4.8's 69.2% — within striking distance on the hardest agentic-coding bench — at ~40–60% of Opus's price ($3/$15 vs $5/$25), and Anthropic ships it as the default model for most accounts. Rules must work for Opus AND the Sonnet/Haiku subagents. (Literalism behavior below holds for both; Opus 4.8 and Sonnet 5 are concise, grounded, honest, and need less anti-slop frontend prompting.)

#### Model tiering — Opus PLANS + REVIEWS, Sonnet 5 EXECUTES (the `opusplan` split)

**Opus is the orchestrator and the hard-reasoning tier, NOT the blanket default for every sub-task.** Running every subagent and every workflow stage on Opus 4.8 + xhigh — just because the main session is Opus — burned the weekly token limit with no quality gain AND starved Sonnet (the user kept seeing "I never see Sonnet used" despite the prior tiering). The fix is Anthropic's own default split (`opusplan`): **Opus does the thinking, Sonnet 5 does the doing.** The agent picks the model per dispatch automatically — never the user's job, never a `/model` toggle to babysit. **Quality is held by EFFORT + the Opus bookends, NOT by putting the whole job on Opus:** Sonnet 5 runs code work at `high`/`xhigh` (it has the full effort range), so the saving is the cheaper MODEL, never dumber thinking.

**The split (the user's chosen policy 2026-06-30 — substantially more Sonnet, never at quality cost):**

- **Main interactive session = Opus 4.8.** The orchestrator stays strong. Never downtier it.
- **PLAN / DESIGN / ARCHITECTURE / SYNTHESIS / hard-debug / final adversarial REVIEW + VERIFY = Opus.** The judgment bookends — deciding WHAT to build and whether it is correct — stay on the strong model (dispatch with `model: "opus"`, or inherit Opus). Quality is never traded away here.
- **EXECUTION of settled, scoped code = Sonnet 5 (`model: "sonnet"`) at `high`/`xhigh` — the NEW default.** Implementing an already-written plan, well-scoped edits, the `subagent-driven-development` implementer, the **autopilot-worker** (full-issue end-to-end), workflow implement/transform stages — all default to Sonnet 5. This is the surface that makes Sonnet usage grow *substantially*. Escalate a single dispatch back to Opus ONLY when the work is genuinely HARD / architectural / ambiguous (subtle multi-file reasoning, a tricky design call, a frontier bug) — by judgment, never by reflex. The Opus main session still re-verifies the worker's evidence, so there is always an Opus review bookend.
- **Purely MECHANICAL / READ-ONLY work = Sonnet 5 (or Haiku for the most trivial) + `low`/`medium` effort.** File enumeration, log scraping, grep/locate sweeps, format-only edits, ticket-validation reads, "where is X" lookups, status polling. No xhigh.
  - Agent tool: pass `model: "sonnet"` on execution AND read-only dispatches (`Explore`, `ticket-validator`, the implementer, the autopilot-worker); `model: "haiku"` for the most trivial reads; use the Opus override ONLY for the plan/design/review stages and escalated-hard execution.
  - Workflow `agent()`: set `opts.model`/`opts.effort` per stage — `sonnet` for read/ground/execute/transform stages, Opus for design/synthesis/adversarial-verify stages.
- **When a dispatch is genuinely HARD / ambiguous / frontier → Opus; when it is settled EXECUTION → Sonnet 5.** The old "when unsure → Opus" is NARROWED: "unsure" now means "the task is genuinely hard or you cannot scope it" — NOT "it touches code." Most code execution IS scoped → Sonnet 5; reserve Opus for the residual hard core. Never gamble a frontier-hard call to save tokens, and never reflexively Opus a routine edit.
- **The READ / GROUND / SCAN / ENUMERATE plumbing of a fan-out is mechanical — cheap-tier is its DEFAULT, not a "maybe."** Every multi-agent dispatch has cheap-tier stages (gathering the shared context, listing files, scraping logs, collecting status) — those go `sonnet`/`haiku` by default; only the design/synthesis/judge stage may be Opus.
- **Self-audit before launching a multi-agent dispatch: name which stages are Opus and which are Sonnet 5. If EVERYTHING is Opus, it is almost certainly a tiering MISS — re-examine.** A whole Workflow / fleet that ran ENTIRELY on Opus is the symptom the user names as "I never see Sonnet used" — and it is the dominant avoidable token cost. Pure-Opus fan-out is the exception (genuinely all-hard-logic work), never the default.

Applies to all rewordings and semantic equivalents — the intent: Opus plans and reviews, Sonnet 5 (at high effort) executes the scoped code, cheap model for cheap work, automatically, with zero manual model-switching by the user.

#### Opus 4.8 behavior (primary agent)

- **Literalism**: does exactly what rules say. Precise wording → high compliance. Vague wording → unpredictable.
- **Reasons more, calls tools less**: when a tool must be used (e.g., `gh run view` for CI monitoring, `Playwright` for E2E verification), the rule must say so explicitly.
- **Adaptive thinking** (no fixed budget): longer reasoning per turn, fewer back-and-forth rounds.
- **Default effort `high`** on all surfaces incl. Claude Code (per docs.anthropic.com/effort). The user runs a managed `xhigh` MAIN-session default by his own choice (his quality baseline — leave it; see [[user-max-autonomy-effort]]). What tiers is the effort of DISPATCHED sub-work: `xhigh`/`max` only for genuinely hard stages, `low`/`medium` for mechanical/read-only stages (which also get a cheap model — see the model-tiering section above). Code-EXECUTION stages run Sonnet 5 at `high` (its default), NOT low/medium — the cheap MODEL is the saving, never low effort on real code. Don't dispatch every subagent/workflow stage at `xhigh` by reflex — that spends meaningfully more thinking tokens with no quality gain on mechanical work.

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
