"""#1260 — managed settings that keep a session's always-on context small.

Two keys `cli_config.apply_managed_settings_defaults` delegates here:

- `claudeMdExcludes` (CC setting, picomatch on absolute paths; it also covers
  `.claude/rules/**` files). The odoo-erp gatekeeper review lenses
  (`.claude/rules/gk-review-lenses*.md`, ~400 KB, 72 % of a stream's always-on
  instructions on 25.10.2026) are excluded on every account whose BASE authority
  is not `full`. The gatekeeper keeps them loaded as its cold-review checklist. A
  stream reads them explicitly in its self-review (agents/autopilot-worker.md),
  so a hand-off pays the cost once instead of on every turn of every stream.
  Entries the account added itself are never touched.
- The compact window. CC's `autoCompactOffer` writes it PER MODEL
  (`modelSettings.<model>.autoCompactWindow`), which the top-level strip
  (#1084: managed boxes run CC's default window) never saw. On david3 a 400k
  window sat below the ~431k post-compact baseline, and the session thrashed all
  night.
"""

LENS_EXCLUDE = "**/.claude/rules/gk-review-lenses*.md"


def _reduced_account():
    import airuleset
    import cli_quals
    return cli_quals._authority_base(airuleset._current_user())[0] != "full"


def apply(result):
    """Mutate the settings dict in place (the caller owns the copy)."""
    result.pop("autoCompactWindow", None)
    per_model = result.get("modelSettings")
    if isinstance(per_model, dict):
        for name in list(per_model):
            cfg = per_model[name]
            if isinstance(cfg, dict) and "autoCompactWindow" in cfg:
                cfg.pop("autoCompactWindow")
                if not cfg:
                    del per_model[name]
        if not per_model:
            del result["modelSettings"]
    raw = result.get("claudeMdExcludes")
    excludes = [e for e in raw if isinstance(e, str)] if isinstance(raw, list) else []
    excludes = [e for e in excludes if e != LENS_EXCLUDE]
    if _reduced_account():
        excludes.append(LENS_EXCLUDE)
    if excludes:
        result["claudeMdExcludes"] = excludes
    else:
        result.pop("claudeMdExcludes", None)
    return result
