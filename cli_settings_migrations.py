"""Self-heal of settings.json values that airuleset USED to manage (stdlib leaf).

`cli_config.apply_managed_settings_defaults` MERGES onto the existing settings
(`result = dict(settings)` keeps every key), so dropping a managed key from the
renderer alone leaves the old value on every box that already has it. This leaf
holds the frozen MIGRATION lists and the one function that heals them:

- #1060 L3a (review B): the gateway env keys + managed apiKeyHelper the DELETED
  #1062 L2 branch wrote into a flipped box's shared settings.json, which would
  otherwise keep the MAIN window on the gateway.
- #1173 (owner 2026-09-28): the `CLAUDE_CODE_SUBAGENT_MODEL` fleet default
  airuleset no longer forces. Unset, Claude Code resolves a subagent with no
  per-dispatch model and no `model:` frontmatter to the main's model.

Every list here is FROZEN (the values airuleset actually wrote), never derived
from today's config, so a future lineup edit can never widen what is deleted.
A value airuleset did not write is the owner's own and is never deleted.
"""

from pathlib import Path

# #1062 L2 era: the values match the old cli_model_backend.BACKEND_ENV_KEYS +
# APIKEY_HELPER_PATH. Popped unconditionally (a no-op on a never-flipped box).
_L2_STALE_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
    "API_TIMEOUT_MS",
)
_L2_MANAGED_APIKEY_HELPER = str(Path.home() / ".claude"
                               / "airuleset-model-gateway-apikey.sh")

# #1173: the exact CLAUDE_CODE_SUBAGENT_MODEL values airuleset wrote (#991:
# MODEL_TIERS["opus"] = claude-opus-4-8; #1119: MODEL_TIERS["opus5"] =
# claude-opus-5-5). L2 also wrote its marker's gateway alias there; that one is
# recognised by the L2 fingerprint keys above instead of by value.
_RETIRED_MANAGED_SUBAGENT_MODELS = frozenset({"claude-opus-4-8", "claude-opus-5-5"})
_SUBAGENT_MODEL_KEY = "CLAUDE_CODE_SUBAGENT_MODEL"


def _heal_subagent_model(env, err):
    """Remove `CLAUDE_CODE_SUBAGENT_MODEL` when airuleset wrote it (a retired
    managed value, or any value on an L2-flipped box); otherwise keep it and
    report it on `err`. Must run BEFORE the L2 pop erases the fingerprint."""
    if _SUBAGENT_MODEL_KEY not in env:
        return
    value = env[_SUBAGENT_MODEL_KEY]
    # A non-string (malformed) value is never one airuleset wrote: kept +
    # reported, and never hashed (an unhashable list must not crash install).
    retired = (isinstance(value, str)
               and value.strip() in _RETIRED_MANAGED_SUBAGENT_MODELS)
    l2_box = any(k in env for k in _L2_STALE_ENV_KEYS)
    if retired or l2_box:
        env.pop(_SUBAGENT_MODEL_KEY)
        print("settings: removed managed env key %s=%s (subagent models are "
              "Claude Code's native choice, #1173)" % (_SUBAGENT_MODEL_KEY, value),
              file=err)
        return
    print("settings: kept hand-set env key %s=%s (not airuleset-managed; it "
          "overrides Claude Code's native subagent model choice, #1173)"
          % (_SUBAGENT_MODEL_KEY, value), file=err)


def heal_retired_managed_settings(result, err):
    """Heal `result` (the merged settings dict, its `env` already a dict) in
    place: the #1173 subagent-model key first, then the #1062 L2 keys and the
    L2 managed apiKeyHelper (a user's own helper is kept). Reports go to `err`
    (stderr), so cmd_diff's stdout diff stays clean."""
    env = result["env"]
    _heal_subagent_model(env, err)
    for key in _L2_STALE_ENV_KEYS:
        env.pop(key, None)
    if result.get("apiKeyHelper") == _L2_MANAGED_APIKEY_HELPER:
        result.pop("apiKeyHelper", None)
