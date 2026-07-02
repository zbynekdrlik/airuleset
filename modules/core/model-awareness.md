### Model Awareness (2026)

The primary Claude Code agent runs **Opus 4.8** (`opus`, `claude-opus-4-8`, $5/$25); dispatched EXECUTION defaults to **Sonnet 5** (`sonnet`, `claude-sonnet-5`, 1M context, full effort range `low`–`max`, SWE-bench Pro 63.2 vs Opus 69.2). Above Opus sits **Fable 5** (`claude-fable-5`, the `fable` alias — Anthropic's Mythos-class tier, the most intelligent generally-available Claude, Claude 5 family; verified present in CC 2.1.198 as a first-class subagent `model` value), held in RESERVE for only the genuinely hardest tasks — it burns tokens fast and trips usage limits, so it is NOT a default anywhere. Haiku 4.5 (`haiku`) for the most trivial reads. Rules must work for Opus AND the Sonnet/Haiku subagents. (Literalism behavior below holds across the family; Fable 5, Opus 4.8 and Sonnet 5 are concise, grounded, honest, and need less anti-slop frontend prompting.)

#### Model tiering — Opus 4.8 + Sonnet 5 by default; Fable 5 ONLY for the hardest tasks (ACTIVE policy, 2026-07-02)

**The user reverted the 2026-07-01 Fable-everywhere policy: it burned tokens brutally and kept tripping the usage limits mid-work — stopping the user's work constantly, unusable as a default.** New standing directive (2026-07-02): default back to **Opus 4.8 + Sonnet 5** (the `opusplan` economy split), and use **Fable 5 ONLY for the most complicated tasks** — either the orchestrator escalates a specific dispatch to it for a genuinely frontier problem, or the user manually switches `/model` to it when they feel Opus 4.8 can't crack something. The agent picks Opus/Sonnet per dispatch automatically; escalating to Fable is a deliberate, RARE exception, never automatic-by-reflex.

**The lineup:**

- **Main interactive session = Opus 4.8.** The user's `/model` default. Strong orchestrator; never auto-downtier it, and never auto-uptier it to Fable — the user flips to Fable BY HAND when they want it.
- **PLAN / DESIGN / ARCHITECTURE / SYNTHESIS / hard-debug / adversarial REVIEW + VERIFY = Opus 4.8.** The judgment bookends — deciding WHAT to build and whether it is correct — stay on Opus (dispatch `model: "opus"`, or inherit Opus).
- **EXECUTION of settled, scoped code = Sonnet 5 (`model: "sonnet"`) at `high`/`xhigh`.** The `subagent-driven-development` implementer, the **autopilot-worker** (full-issue end-to-end), workflow execute/transform stages. Quality is held by HIGH effort + the Opus design/review bookends, NOT by the model tier.
- **Purely MECHANICAL / READ-ONLY plumbing = Sonnet 5 (or Haiku for the most trivial) + `low`/`medium`.** File enumeration, log scraping, grep/locate sweeps, format-only edits, "where is X" lookups, status polling. Model tier cannot change the outcome of a lookup, and lighter models return it faster.
- **Fable 5 = the reserved TOP escalation (above Opus), for the GENUINELY HARDEST / frontier tasks ONLY.** Exactly two ways it fires: (a) the **orchestrator** deliberately escalates a specific dispatch to `model: "fable"` because the task is genuinely beyond Opus-tier judgment (a frontier bug, gnarly multi-file architecture Opus keeps failing) — RARE, the exception's exception; (b) the **user** manually switches `/model` to Fable when they feel Opus 4.8 can't solve it. **Fable burns tokens brutally and trips the usage limits fast — so it is the CEILING, never an everyday tier and never a default anywhere.** When in ANY doubt whether a task is Fable-hard → it is NOT; use Opus.
- **"Hard" resolves to Opus; only genuinely FRONTIER (beyond Opus) resolves to Fable.** Most hard work is well within Opus — Fable is the residual few percent even Opus struggles with. Never reach for Fable to "be thorough"; that is the exact token-burn the user reverted.
- **Redundancy is still waste, not rigor** — ground once + pass a digest, size fan-outs to residual uncertainty, batch per-item verifies (`claude-code-tooling.md`). Unchanged under every policy.
- **Self-audit before a multi-agent dispatch: if ANY stage is on Fable, justify it as genuinely frontier — a casually-Fable (let alone all-Fable) fan-out is the tiering MISS now.** Most stages are Sonnet (execute) or Opus (judgment); Fable appears only on the rare genuinely-hardest stage, if at all.

Applies to all rewordings and semantic equivalents — the intent: Opus 4.8 plans/reviews, Sonnet 5 executes the scoped code, cheap model for cheap work, and Fable 5 is reserved for ONLY the genuinely hardest tasks (orchestrator-escalated or user-selected), so Fable never becomes a token-burning default again.

#### Dormant — the Fable-everywhere MAX-PERFORMANCE mode (re-activate ONLY on the user's explicit say-so)

The 2026-07-01 "Fable 5 on every judgment dispatch, cost no object" policy is DORMANT — it burned tokens and tripped limits, stopping work. Re-activate it ONLY if the user AGAIN explicitly says cost is no object / max intelligence everywhere (limits reset with huge headroom): then Fable becomes the default for all judgment work at xhigh. Do NOT re-activate on your own inference — the switch is the user's alone.

#### Opus 4.8 / Fable 5 behavior (primary + escalation)

- **Literalism**: does exactly what rules say. Precise wording → high compliance. Vague wording → unpredictable.
- **Reasons more, calls tools less**: when a tool must be used (e.g., `gh run view` for CI monitoring, `Playwright` for E2E verification), the rule must say so explicitly.
- **Adaptive thinking** (no fixed budget): longer reasoning per turn, fewer back-and-forth rounds.
- **Default effort `high`** on all surfaces incl. Claude Code (per docs.anthropic.com/effort). The user runs a managed `xhigh` MAIN-session default by his own choice (his quality baseline — leave it; see [[user-max-autonomy-effort]]). Under the ACTIVE policy: DISPATCHED judgment work (plan/design/review/hard-debug) runs Opus 4.8 at `xhigh` (`max` only for genuinely frontier Opus work — it overthinks structured tasks); EXECUTION runs Sonnet 5 at `high`/`xhigh`; mechanical/read-only stays `low`/`medium` on a light model; a rare Fable escalation runs `xhigh`/`max`. Don't blanket-`xhigh` mechanical work.

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
