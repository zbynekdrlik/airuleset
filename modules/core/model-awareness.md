### Model Awareness (2026)

The primary Claude Code agent runs Opus 4.7, but subagents (`Task` tool) often run smaller models (Haiku, Sonnet). Rules must work for BOTH.

#### Opus 4.7 behavior (primary agent)

- **Literalism**: does exactly what rules say. Precise wording → high compliance. Vague wording → unpredictable.
- **Reasons more, calls tools less**: when a tool must be used (e.g., `gh run view` for CI monitoring, `Playwright` for E2E verification), the rule must say so explicitly.
- **Adaptive thinking** (no fixed budget): longer reasoning per turn, fewer back-and-forth rounds.
- **Default effort `xhigh`**. Use `max` for deep debugging, `low` for trivial edits.

#### Subagent behavior (smaller models)

- Follow rules less reliably — they need explicit "do X, not Y" instructions.
- Benefit more from hook-level enforcement (100% compliance regardless of model).
- Benefit from pattern lists (banned phrases, anti-patterns) — literalism matters less when the model already pattern-matches.

#### How to write rules for both

- **Lead with the rule in precise terms** — 4.7 will follow it exactly.
- **Follow with anti-patterns / banned phrases** — helps subagents.
- **End with "applies to all rewordings and semantic equivalents"** — prevents 4.7 from taking bullet-point lists as exhaustive.
- **Critical enforcement goes in hooks**, not rules — works for every model.

#### Example

Bad (vague, subagent may miss): "Don't skip tests."
Good (precise + pattern + semantic gate):
> "NEVER use `#[ignore]`, `test.skip()`, or `pytest.mark.skip`. Any rewording or equivalent skip mechanism also applies."
