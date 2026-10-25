"""#1260 — managed settings that keep a session's always-on context small.

Two keys `cli_config.apply_managed_settings_defaults` delegates here:

- `claudeMdExcludes` (CC setting, picomatch on absolute paths; it also covers
  `.claude/rules/**` files). The odoo-erp gatekeeper review lenses
  (`.claude/rules/gk-review-lenses*.md`: the index + its prevencia parts, ~400 KB,
  72 % of a stream's always-on instructions on 25.10.2026) are excluded on every
  account whose BASE authority is not `full`. A full account keeps them loaded
  only in its review window (the gatekeeper's cold-review checklist); every other
  DECLARED window of the box (gk-infra, gk-quality) excludes them by absolute
  path (#1137). Every self-review path reads them explicitly (autopilot-worker,
  implementer, the autopilot MAIN REVIEW GATE), so a hand-off pays the cost once
  instead of on every turn of every stream. Other entries are kept; managed are
  the glob and any absolute `<dir>/.claude/rules/gk-review-lenses*.md`.
- Per-model settings that shadow a managed top-level default. CC writes them per
  model (`modelSettings.<model>.*`): the compact window (`autoCompactOffer`,
  `/autocompact`) and the effort (`/effort`). The top-level strip/default never saw
  them. On david3 a 400k window sat below the ~431k post-compact baseline and the
  session thrashed all night; a persisted per-model `xhigh` silently overrides
  the managed `high` (#751).
"""

LENS_EXCLUDE = "**/.claude/rules/gk-review-lenses*.md"
LENS_SUFFIX = "/.claude/rules/gk-review-lenses*.md"
PER_MODEL_STRIPPED = ("autoCompactWindow", "effortLevel")


def _reduced_account():
    import airuleset
    import cli_quals
    return cli_quals._authority_base(airuleset._current_user())[0] != "full"


def _managed(entry):
    return entry == LENS_EXCLUDE or (entry.startswith("/") and entry.endswith(LENS_SUFFIX))


def _account_home():
    import airuleset
    import pwd
    return pwd.getpwnam(airuleset._current_user()).pw_dir


def _window_entries():
    """Absolute lens excludes for this box's declared non-review windows (and
    their worktree lanes). Fail-safe: any error yields none, never an aborted
    install; a cwd a review window also uses is never excluded."""
    import airuleset
    import cli_fleet
    try:
        home = _account_home()
        dirs = {}
        for w in cli_fleet.box_windows(airuleset._current_user()) or []:
            cwd = w.get("cwd") if isinstance(w, dict) else None
            if not isinstance(cwd, str) or not cwd.strip():
                continue
            cwd = cwd.strip().rstrip("/")
            cwd = home + cwd[1:] if cwd.startswith("~") else cwd
            cwd = cwd if cwd.startswith("/") else home + "/" + cwd
            dirs.setdefault(cwd, set()).add(w.get("role"))
    except Exception:  # noqa: BLE001 -- a bad declaration never aborts install
        return []
    out = []
    for cwd, roles in dirs.items():
        if "review" not in roles:
            out += [cwd + LENS_SUFFIX, cwd + "/.claude/worktrees/**" + LENS_SUFFIX]
    return out


def apply(result):
    """Rewrite the managed keys of the settings dict `result` (the caller's
    shallow copy). Nested dicts are rebuilt, never mutated, so the caller's
    original settings stay untouched."""
    result.pop("autoCompactWindow", None)
    per_model = result.get("modelSettings")
    if isinstance(per_model, dict):
        kept = {}
        for name, cfg in per_model.items():
            if isinstance(cfg, dict):
                cfg = {k: v for k, v in cfg.items() if k not in PER_MODEL_STRIPPED}
                if not cfg:
                    continue
            kept[name] = cfg
        if kept:
            result["modelSettings"] = kept
        else:
            result.pop("modelSettings")
    raw = result.get("claudeMdExcludes")
    excludes = [e for e in raw if isinstance(e, str)] if isinstance(raw, list) else []
    excludes = [e for e in excludes if not _managed(e)]
    if _reduced_account():
        excludes.append(LENS_EXCLUDE)
    else:
        excludes += _window_entries()
    if excludes:
        result["claudeMdExcludes"] = excludes
    else:
        result.pop("claudeMdExcludes", None)
    return result
