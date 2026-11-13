### Model Awareness (2026)

**The MAIN session model is Opus 5.5 (`claude-opus-5-5[1m]`, `airuleset.MANAGED_MODEL`)** — the user's call (#1119), NEVER recommend switching it.

**Subagent model / type / count is YOUR decision, resolved natively by Claude Code (#991, #1173).** A bare subagent inherits the main (our workers pin `model: inherit`). Also allowed: Sonnet 5.5 (`claude-sonnet-5-5`), `claude-sonnet-5`, `claude-opus-4-8`. BANNED (`hooks/block-banned-model.sh`, `airuleset.BANNED_MODELS`): **Opus 5** (`claude-opus-5`, bare `opus`/`opusplan`) and **Fable** (`claude-fable-*`, `fable`: worse than Opus 5.5, #1248; owner may pick `claude-fable-5-2` as main, #1314). History: `.claude/rules-reference/model-awareness-history.md`.
