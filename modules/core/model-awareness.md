### Model Awareness (2026)

**The MAIN session model is Opus 5.5 (`claude-opus-5-5[1m]`, `airuleset.MANAGED_MODEL`)** — the user's call (#1119, replacing Fable 5.1), NEVER recommend switching it.

**Subagent model / type / count is YOUR decision, resolved natively by Claude Code (#991).** The fleet DEFAULT for a dispatched subagent is `claude-opus-5-5` (env `CLAUDE_CODE_SUBAGENT_MODEL`, #1119); a per-dispatch `model` param overrides it. Fable 5.1 (`claude-fable-5-1`) and `claude-opus-4-8` stay ALLOWED ids, not defaults. **Opus 5** (`claude-opus-5` + bare `opus`/`opusplan`) is BANNED — `hooks/block-banned-model.sh` (`airuleset.BANNED_MODELS`); `claude-opus-5-5` is a distinct allowlisted id. No tiering, no budget gate. History: `.claude/rules-reference/model-awareness-history.md`.
