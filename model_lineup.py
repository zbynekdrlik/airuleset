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

# The former main (pre-#1119), BANNED as a dispatch value since #1248 (owner
# 2026-10-15: "nechcem aby sa aktualne fable puzival je horsi ako opus 5.5").
# Kept by name ONLY so gates/designdispatch.py still accepts a design comment
# the old Fable main stamped; it is NOT a MODEL_TIERS member.
FORMER_MAIN_MODEL = "claude-fable-5-1"

# Per-PROJECT main model (#1246, owner 14.10.2026: "potrebujem aby pz-server
# projekt na dev1 mohol mat prepnuty model na opus-4.6 a pracovat"). A project
# listed here LAUNCHES on its own model, is restored to it (watchdog Job 54) and
# may also run MANAGED_MODEL; every other model is still blocked by the #1203
# gate. Keyed by the project dir NAME under `devel/`. The exact ids are outside
# MODEL_TIERS on purpose: they are one project's main, never a fleet-wide
# dispatch choice. An entry is added only by an owner-approved edit.
PROJECT_MAIN_MODELS = {
    "pz-server": "claude-opus-4-6",
}


def dir_key(path):
    """Claude Code's project key for a launch dir: `/`, `.`, `_` become `-`
    (the name of the dir under ~/.claude/projects/ that holds its transcripts)."""
    return path.replace("/", "-").replace(".", "-").replace("_", "-")


def project_of_key(key):
    """The PROJECT_MAIN_MODELS name a Claude Code project key belongs to, or
    None. The key must END with `-devel-<name>` (the ~/devel/<name> layout), so
    `foo-pz-server`, `notdevel/pz-server` and a subdir launch never match."""
    if not isinstance(key, str):
        return None
    for name in PROJECT_MAIN_MODELS:
        if key.endswith("-devel-" + dir_key(name)):
            return name
    return None


def main_models(name=None):
    """`(target, allowed)` for project `name` (None = no declared project):
    the model a session launches on and is restored to, and every model it may
    run on without being blocked (the target plus MANAGED_MODEL)."""
    target = PROJECT_MAIN_MODELS.get(name) if isinstance(name, str) else None
    if not target:
        return MANAGED_MODEL, (MANAGED_MODEL,)
    return target, (target, MANAGED_MODEL)


def launcher_model_lines():
    """The managed launcher's lines that set `_model` (#1246): the
    `{{MANAGED_MODEL}}` placeholder (filled by the launcher render), switched by
    a `case` to a declared project's main. The `$PWD` encoding is the
    launcher's `_has_conversation`, the pattern `project_of_key`'s suffix."""
    lines = ["# #1246: `_model` = MANAGED_MODEL or this project's declared main (model_lineup)",
             "_model='{{MANAGED_MODEL}}'"]
    if PROJECT_MAIN_MODELS:
        lines += ['_ccdir="${PWD//\\//-}"; _ccdir="${_ccdir//./-}"; _ccdir="${_ccdir//_/-}"',
                  'case "$_ccdir" in']
        lines += ["  *-devel-%s) _model='%s' ;;" % (dir_key(name), model)
                  for name, model in sorted(PROJECT_MAIN_MODELS.items())]
        lines.append("esac")
    return "\n".join(lines)


def main_models_for_transcript(tpath):
    """`main_models` for a session transcript path: its project key is the
    name of the transcript's parent dir. A path object (watchdog's
    `newest_transcript` returns a pathlib.Path) resolves like its string."""
    if hasattr(tpath, "__fspath__"):
        tpath = tpath.__fspath__()
    if not isinstance(tpath, str):
        return main_models(None)
    parts = tpath.replace("\\", "/").split("/")
    return main_models(project_of_key(parts[-2] if len(parts) >= 2 else ""))
