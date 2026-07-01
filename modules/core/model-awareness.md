### Model Awareness (2026)

The primary Claude Code agent runs **Fable 5** (`claude-fable-5`, the `fable` alias) — Anthropic's Mythos-class tier ABOVE Opus, the most intelligent generally-available Claude (Claude 5 family, released 2026-06/07; verified present in CC 2.1.198 as a first-class subagent `model` value). Below it: Opus 4.8 (`opus`, $5/$25), Sonnet 5 (`sonnet`, `claude-sonnet-5`, 1M context, full effort range `low`–`max`, SWE-bench Pro 63.2 vs Opus 69.2), Haiku 4.5 (`haiku`). Rules must work for Fable AND the Sonnet/Haiku subagents. (Literalism behavior below holds across the family; Fable 5, Opus 4.8 and Sonnet 5 are concise, grounded, honest, and need less anti-slop frontend prompting.)

#### Model tiering — MAX-PERFORMANCE mode: Fable 5 everywhere judgment matters (ACTIVE policy, 2026-07-01)

**The user's standing directive (2026-07-01, limits reset + multiple accounts): optimize for MAXIMUM intelligence and task-solving performance, NOT for token cost.** This SUPERSEDES the `opusplan` economy split of 2026-06-30 (kept below as the dormant fallback). The agent still picks the model per dispatch automatically — never the user's job, never a `/model` toggle to babysit.

**The lineup:**

- **Main interactive session = Fable 5 + `xhigh`.** The user set it as default. Never downtier it.
- **EVERY dispatch where judgment affects the outcome = Fable 5 (`model: "fable"`).** That covers BOTH bookends AND the middle: PLAN / DESIGN / ARCHITECTURE / SYNTHESIS, hard debug, adversarial REVIEW + VERIFY, **and EXECUTION** — the `subagent-driven-development` implementer, the **autopilot-worker** (full-issue end-to-end), workflow design/execute/transform/verify stages, `ticket-validator` deep checks. Effort `xhigh` by default on these; `max` for genuinely frontier problems (deep debugging, gnarly multi-file architecture — `max` overthinks structured tasks, so not blanket).
- **Purely MECHANICAL / READ-ONLY plumbing = Sonnet 5 (or Haiku for the most trivial) + `low`/`medium`.** File enumeration, log scraping, grep/locate sweeps, format-only edits, "where is X" lookups, status polling. This survives into max-performance mode NOT to save money but because model tier cannot change the outcome of a lookup, and lighter models return it faster. **The tie-breaker is inverted from the economy split: when in ANY doubt whether judgment touches the outcome → Fable.** Never let a cheaper model make a call that shapes the result.
- **Opus 4.8 = the fallback tier**, used only when a Fable dispatch is unavailable / erroring / rate-limited — then degrade Fable→Opus (and retry Fable later), never Fable→Sonnet for judgment work.
- **Self-audit before a multi-agent dispatch is INVERTED: name any stage NOT on Fable and justify it as purely mechanical.** A judgment stage (design, review, verify, implement, debug) sitting on `sonnet`/`haiku` is now the tiering MISS. An all-Fable fan-out is perfectly fine.
- **Redundancy is still waste, not rigor.** Max-performance buys DEPTH (stronger model, higher effort, more adversarial verification), never REDUNDANCY — ground once + pass a digest, size fan-outs to residual uncertainty, batch per-item verifies (`claude-code-tooling.md`). N agents re-deriving the same thing gained nothing under the economy split and gains nothing now.

Applies to all rewordings and semantic equivalents — the intent: the strongest available model (Fable 5) does every piece of work where intelligence moves the outcome, at high/xhigh effort, automatically, with zero manual model-switching by the user.

#### Dormant fallback — the `opusplan` economy split (re-activate ONLY on the user's say-so)

When the user says to conserve tokens again (limits tighten, "šetri tokeny", or they revert `/model` to Opus), fall back to the 2026-06-30 split: Opus plans + reviews (the judgment bookends), Sonnet 5 at `high`/`xhigh` EXECUTES scoped code (incl. the autopilot-worker), cheap tier for mechanical work, escalate to Opus only for genuinely hard tickets. Do NOT re-activate it on your own inference of "high spend" — the switch is the user's.

#### Fable 5 / Opus 4.8 behavior (primary agent)

- **Literalism**: does exactly what rules say. Precise wording → high compliance. Vague wording → unpredictable.
- **Reasons more, calls tools less**: when a tool must be used (e.g., `gh run view` for CI monitoring, `Playwright` for E2E verification), the rule must say so explicitly.
- **Adaptive thinking** (no fixed budget): longer reasoning per turn, fewer back-and-forth rounds.
- **Default effort `high`** on all surfaces incl. Claude Code (per docs.anthropic.com/effort). The user runs a managed `xhigh` MAIN-session default by his own choice (his quality baseline — leave it; see [[user-max-autonomy-effort]]). Under the ACTIVE max-performance policy, DISPATCHED judgment work (plan/design/review/verify/execute/debug) runs Fable 5 at `xhigh` (`max` only for genuinely frontier problems — it overthinks structured tasks); mechanical/read-only stages stay `low`/`medium` on a light model, because extra thinking cannot improve a lookup.

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
