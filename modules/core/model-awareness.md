### Model Awareness (2026)

The primary Claude Code agent runs **Opus 4.8** (`opus`, `claude-opus-4-8`, $5/$25); dispatched EXECUTION defaults to **Sonnet 5** (`sonnet`, `claude-sonnet-5`, 1M context, full effort range `low`–`max`, SWE-bench Pro 63.2 vs Opus 69.2). Above Opus sits **Fable 5** (`claude-fable-5`, the `fable` alias — Anthropic's Mythos-class tier, the most intelligent generally-available Claude, Claude 5 family; verified present in CC 2.1.198 as a first-class subagent `model` value): genuinely HARD tasks escalate to it AUTOMATICALLY through the **budget gate** (`airuleset.py fable-gate`) — it burns tokens fast, so it is never a blanket default and every automatic use is gated on weekly-limit headroom. Haiku 4.5 (`haiku`) for the most trivial reads. Rules must work for Opus AND the Sonnet/Haiku subagents. (Literalism behavior below holds across the family; Fable 5, Opus 4.8 and Sonnet 5 are concise, grounded, honest, and need less anti-slop frontend prompting.)

#### Model tiering — Opus 4.8 + Sonnet 5 default; Fable 5 AUTO-escalates on genuinely HARD tasks, budget-gated (ACTIVE policy, 2026-07-03)

**History that shapes this policy:** the 2026-07-01 Fable-everywhere mode burned tokens brutally and kept tripping the usage limits mid-work — the user reverted it 2026-07-02 (reverted the 2026-07-01 policy). New standing directive (2026-07-03): the user does NOT want all work to be only Opus + Sonnet — **genuinely HARD tasks escalate to Fable 5 AUTOMATICALLY** (in autopilot, brainstorming/design, workflows, hard debugging), WITHOUT asking. What makes this safe where Fable-everywhere was not: (1) only the HARD subset escalates (routine work stays Opus/Sonnet), and (2) **every automatic escalation is BUDGET-GATED** — `python3 ~/devel/airuleset/airuleset.py fable-gate` (exit 0 `OPEN` / exit 1 `CLOSED`) checks the Fable weekly + shared weekly windows from the watchdog's usage cache and closes automatic Fable once headroom runs out (default gate 80%, env `AIRULESET_FABLE_GATE_PCT`), so the limit-trip-stops-work failure cannot repeat. Gate CLOSED → the same task runs Opus. Fail-safe: missing/stale cache = CLOSED.

**The lineup:**

- **Main interactive session = the user's `/model` choice.** Never auto-downtier it; the user flips it (incl. to Fable) BY HAND when they want.
- **PLAN / DESIGN / ARCHITECTURE / SYNTHESIS / hard-debug / adversarial REVIEW + VERIFY = Opus 4.8 by default.** The judgment bookends — deciding WHAT to build and whether it is correct — run Opus (dispatch `model: "opus"`, or inherit Opus)… unless the task is genuinely HARD (below), in which case it auto-escalates to Fable through the gate.
- **HARD-task AUTO-escalation = Fable 5 (`model: "fable"`), budget-gated.** A task is HARD when ANY of these holds:
  1. **Architecture / design / synthesis of a genuinely COMPLEX or cross-cutting system or feature** — the design synthesis of a substantial brainstorm, the plan for a cross-cutting change, a judge/synthesis stage of a Workflow on a hard problem. "Multi-file" alone is NOT the bar — most ordinary features touch several files; the bar is complexity/ambiguity of the design itself, and the design of a routine feature stays Opus.
  2. **Hard debugging** — the root cause resisted a first Opus-tier attempt, or the bug is multi-component / concurrency / heisenbug-class.
  3. **Adversarial final review / verify of a safety-critical or genuinely complex change** (keystroke-injection guards, auth, data-loss paths, gnarly concurrency).
  4. **An autopilot ticket that is architectural / cross-cutting / ambiguous-design, or one a prior worker already failed on.**
  Routine work is NOT hard: scoped bug fixes, features with a settled design, execution of a settled plan, mechanical/read-only sweeps — those stay Sonnet/Opus per the rows here. When unsure whether a task is hard → it is NOT; use Opus.
  **Protocol:** run the gate ONCE per hard task/batch (not per subtask): `python3 ~/devel/airuleset/airuleset.py fable-gate` → `OPEN` → dispatch `model: "fable"` at `xhigh` (or `max` for the truly frontier); `CLOSED` → dispatch `model: "opus"` and do NOT keep re-polling the gate within the same task. Never skip the gate for an automatic escalation — an ungated automatic Fable dispatch is the exact failure the user reverted. (The user's own manual `/model` Fable is not gated — that's their call.)
- **EXECUTION of settled, scoped code = Sonnet 5 (`model: "sonnet"`) at `high`/`xhigh`.** The `subagent-driven-development` implementer, the **autopilot-worker** (full-issue end-to-end), workflow execute/transform stages. Quality is held by HIGH effort + the judgment bookends, NOT by the model tier. Execution does NOT escalate to Fable — hardness lives in design/debug/review, not in typing settled code.
- **Purely MECHANICAL / READ-ONLY plumbing = Sonnet 5 (or Haiku for the most trivial) + `low`/`medium`.** File enumeration, log scraping, grep/locate sweeps, format-only edits, "where is X" lookups, status polling. Model tier cannot change the outcome of a lookup, and lighter models return it faster.
- **Redundancy is still waste, not rigor** — ground once + pass a digest, size fan-outs to residual uncertainty, batch per-item verifies (`claude-code-tooling.md`). Unchanged under every policy. A Fable stage NEVER re-reads what a cheap stage already digested.
- **Self-audit before a multi-agent dispatch: every Fable stage must map to a named HARD criterion above AND a gate-OPEN check — an all-Fable or casually-Fable fan-out is still the tiering MISS.** Most stages are Sonnet (execute) or Opus (judgment); Fable takes the genuinely hard judgment stages when the gate is open.

Applies to all rewordings and semantic equivalents — the intent: Opus 4.8 plans/reviews by default, Sonnet 5 executes the scoped code, cheap model for cheap work, and genuinely HARD judgment work escalates to Fable 5 AUTOMATICALLY but always through the budget gate — so the user gets Fable-grade output on the hard things without Fable ever again burning the limits down mid-work.

#### Dormant — the Fable-everywhere MAX-PERFORMANCE mode (re-activate ONLY on the user's explicit say-so)

The 2026-07-01 "Fable 5 on every judgment dispatch, cost no object" policy is DORMANT — it burned tokens and tripped limits, stopping work. Re-activate it ONLY if the user AGAIN explicitly says cost is no object / max intelligence everywhere (limits reset with huge headroom): then Fable becomes the default for all judgment work at xhigh. Do NOT re-activate on your own inference — the switch is the user's alone.

#### Opus 4.8 / Fable 5 behavior (primary + escalation)

- **Literalism**: does exactly what rules say. Precise wording → high compliance. Vague wording → unpredictable.
- **Reasons more, calls tools less**: when a tool must be used (e.g., `gh run view` for CI monitoring, `Playwright` for E2E verification), the rule must say so explicitly.
- **Adaptive thinking** (no fixed budget): longer reasoning per turn, fewer back-and-forth rounds.
- **Default effort `high`** on all surfaces incl. Claude Code (per docs.anthropic.com/effort). The user runs a managed `xhigh` MAIN-session default by his own choice (his quality baseline — leave it; see [[user-max-autonomy-effort]]). Under the ACTIVE policy: DISPATCHED judgment work (plan/design/review/hard-debug) runs Opus 4.8 at `xhigh` (`max` only for genuinely frontier Opus work — it overthinks structured tasks); EXECUTION runs Sonnet 5 at `high`/`xhigh`; mechanical/read-only stays `low`/`medium` on a light model; a gated HARD-task Fable escalation runs `xhigh`/`max`. Don't blanket-`xhigh` mechanical work.

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
