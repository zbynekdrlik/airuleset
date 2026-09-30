"""model_lineup -- the managed Claude model lineup, the ONE source of truth.

Moved VERBATIM out of airuleset.py (#1203) so a per-tool-call hook
(`gates.modelfallback`, the model-fallback PreToolUse gate) can read the managed
model without importing the whole airuleset CLI module (~0.13 s warm per call).
airuleset.py re-exports both names unchanged (`airuleset.MODEL_TIERS`,
`airuleset.MANAGED_MODEL`), so every existing consumer and test is untouched.

STDLIB ONLY, no imports at all: this module must stay import-cheap.
"""

# --------------------------------------------------------------------------- #
# Model lineup — an ALLOWLIST of EXACT ids, the single source of truth (owner
# directive 2026-09-04, #871: "chcem pouzivat by default vzdy sonnet-5,
# opus-4.6, fable-5.0"). The float vector this closes: a bare alias
# (`fable`/`opus`/`sonnet`/`haiku`) resolves to the LATEST model of that
# family, so an exact id never floats. A new model version joins the fleet
# ONLY by an owner-approved edit of this table, never by an alias float.
MODEL_TIERS = {
    "opus5": "claude-opus-5-5",       # main session model (MANAGED_MODEL) — owner directive 2026-09-23, #1119
    "fable": "claude-fable-5-1",      # allowed dispatch choice (former main, pre-#1119)
    "opus": "claude-opus-4-8",        # allowed dispatch choice
    "sonnet": "claude-sonnet-5-5",    # allowed dispatch choice — Sonnet 5.5 (owner 2026-09-28, #1173)
    "sonnet5": "claude-sonnet-5",     # allowed dispatch choice (older pinned dispatches, pre-#1173)
    "haiku": "claude-haiku-4-5",      # allowed dispatch choice (trivial reads)
}

# Managed default MAIN-session model (user directive 2026-08-13: **Opus 5 is
# BANNED**; 2026-09-05: Fable 5.1 @ medium replaces 5.0, #894; 2026-09-23:
# **Opus 5.5 replaces Fable 5.1 as the main**, #1119; since #1173 a subagent
# with no per-dispatch model and no `model:` pin natively INHERITS this main)
# — Opus 5.5 (`claude-opus-5-5`), derived from MODEL_TIERS so
# the lineup has ONE source. The `[1m]` suffix is a DELIBERATE part of the id,
# not a typo: it is how Claude Code's own usage tracking keys the 1M-context
# variant (verified — `lastModelUsage` entries in ~/.claude.json store ids
# exactly like `claude-opus-5-5[1m]`) — kept so this does NOT shrink the context
# window. The unconditional-managed-default treatment
# (cli_config.apply_managed_settings_defaults) is what makes the lineup
# self-healing: any settings.json `model` != MANAGED_MODEL is overwritten on the
# next install/push. burn.tier("claude-opus-5-5[1m]") → "opus" (substring), so
# the statusline highlight keeps working. NB: `claude-opus-5-5` is a DISTINCT
# exact id from the BANNED `claude-opus-5` — it is on the allowlist
# (MODEL_TIERS) and never matched by the exact-id ban (see is_banned_model /
# block-banned-model.sh in airuleset.py). Full policy history:
# .claude/rules-reference/model-awareness-history.md.
MANAGED_MODEL = MODEL_TIERS["opus5"] + "[1m]"
