### Model Awareness (2026)

**The MAIN session model is Fable 5.1 (`claude-fable-5-1[1m]`, `airuleset.MANAGED_MODEL`)** — the user's call, NEVER recommend switching it.

**Subagent model / type / count is YOUR decision, resolved natively by Claude Code (#991).** The fleet DEFAULT for a dispatched subagent is `claude-opus-4-8` (the managed env `CLAUDE_CODE_SUBAGENT_MODEL`); a per-dispatch `model` param is a legitimate override. The ONE ban: **Opus 5** (`claude-opus-5` + the bare `opus`/`opusplan` alias) — `hooks/block-banned-model.sh` refuses it on Agent + Workflow (`airuleset.BANNED_MODELS`). There is no tiering doctrine and no budget gate; turn the default off by removing the env var. History: `.claude/rules-reference/model-awareness-history.md`.
