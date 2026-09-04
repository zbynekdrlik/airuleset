"""airuleset config-authoring + validate/diff logic (#433 cluster L-D).

Extracted VERBATIM from airuleset.py (config-author + validate/diff group,
per the binding "Design -- klaster L sub-split" comment). Config CONSTANTS stay
RESIDENT in airuleset.py (test-patchability + single-source-of-truth); this leaf
holds only the LOGIC that composes and verifies the managed config, reading the
resident constants via a per-body deferred `import airuleset` (never a
module-level one -- airuleset.py runs as __main__, so a top-level import here
would trigger a second whole-script execution at leaf-load). `REPO_DIR` is a
1-line leaf-dup (identical value, not test-patched; L-B precedent). Sibling-leaf
and L-E symbols (FILEDROP_SERVICE_TEMPLATE, TMUX_CUTOVER_*, skill_names_for_user,
read_file_safe) are all routed through the airuleset facade for merge-order
robustness. See internals-cli.md for the full lesson.
"""

import os        # symlink_global_rules, cmd_diff
import sys       # parse_profile, cmd_validate
import json      # load_hooks_json, cmd_validate, cmd_diff
import shutil    # symlink_global_rules
import difflib   # unified_diff
from pathlib import Path

# 1-line leaf-dup of airuleset.REPO_DIR: identical value (this leaf is a sibling
# in the same repo dir), not test-patched -> safe to duplicate (L-B precedent).
REPO_DIR = Path(__file__).resolve().parent


def parse_profile(profile_path: Path) -> list[str]:
    """Parse a .profile file and return list of module/rule paths (relative to repo)."""
    if not profile_path.exists():
        print(f"ERROR: Profile not found: {profile_path}", file=sys.stderr)
        sys.exit(1)

    entries = []
    for line in profile_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@include "):
            included = line.split(None, 1)[1]
            included_path = profile_path.parent / included
            entries.extend(parse_profile(included_path))
        else:
            entries.append(line)
    return entries


def categorize_entries(entries: list[str]) -> tuple[list[str], list[str]]:
    """Split profile entries into modules (for @import) and rules (for symlinks)."""
    modules = []
    rules = []
    for e in entries:
        if e.startswith("rules/"):
            rules.append(e)
        else:
            modules.append(e)
    return modules, rules


def symlink_global_rules(rule_entries: list[str], claude_dir: Path,
                          repo_dir: Path) -> list[str]:
    """Symlink each `rules/<name>.md` profile entry into
    `claude_dir/rules/<name>.md` -- Claude Code's native "User"-scope
    path-scoped-rules directory (#40; see RULES_DIR). Mirrors the
    skill-symlink pattern in cmd_install: idempotent, backs up a pre-existing
    real file before replacing it with a symlink, and prunes an
    airuleset-owned rule symlink that is no longer referenced (never touches
    a foreign symlink pointing anywhere else). Takes explicit params (not the
    module-level CLAUDE_DIR/REPO_DIR globals) so it's directly unit-testable
    with a tempdir. Returns human-readable log lines for the caller to print.
    """
    lines = []
    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    wanted_names = set()
    for entry in rule_entries:
        name = Path(entry).name
        wanted_names.add(name)
        source = repo_dir / entry
        link = rules_dir / name

        if not source.exists():
            lines.append(f"  SKIP rule (source missing): {source}")
            continue

        if link.is_symlink():
            current = Path(os.readlink(link))
            if current == source:
                lines.append(f"  OK rule:   {name}")
                continue
            link.unlink()
        elif link.exists():
            backup = link.with_suffix(".md.bak")
            shutil.move(str(link), str(backup))
            lines.append(f"  Backed up: {link} -> {backup}")

        link.symlink_to(source)
        lines.append(f"  Linked:    {link} -> {source}")

    # Prune airuleset-owned rule symlinks no longer referenced by the profile
    # (same ownership check as the skill-pruning step: only unlink a symlink
    # that points into OUR repo's rules/ dir -- a foreign/hand-made rule file
    # is never touched).
    for link in rules_dir.glob("*.md"):
        if link.name in wanted_names:
            continue
        if not link.is_symlink():
            continue
        try:
            target = Path(os.readlink(link))
        except OSError:
            continue
        if str(target).startswith(str(repo_dir / "rules")):
            link.unlink()
            lines.append(f"  Pruned:    {link.name} (not in universal profile)")

    return lines


def generate_claude_md(modules: list[str]) -> str:
    """Generate the content for ~/.claude/CLAUDE.md with @import lines."""
    import airuleset
    lines = [
        "# User-Wide Claude Code Instructions",
        "",
        f"{airuleset.MANAGED_MARKER}",
        f"{airuleset.MANAGED_HEADER} — https://github.com/zbynekdrlik/airuleset",
        "# Do not edit this file manually. Run: python airuleset.py install",
        "",
    ]

    # Group modules by category for readability
    groups: dict[str, list[str]] = {}
    for mod in modules:
        # Extract category from path like modules/core/foo.md -> core
        parts = mod.split("/")
        if len(parts) >= 3:
            category = parts[1]
        else:
            category = "other"
        groups.setdefault(category, []).append(mod)

    category_titles = {
        "core": "Core Workflow",
        "git": "Git Discipline",
        "ci": "CI/CD Standards",
        "deploy": "Deployment",
        "quality": "Code Quality",
    }

    for category, mods in groups.items():
        title = category_titles.get(category, category.title())
        lines.append(f"## {title}")
        lines.append("")
        for mod in mods:
            lines.append(f"@~/devel/airuleset/{mod}")
        lines.append("")

    return "\n".join(lines)


def preserve_external_blocks(old_text: str, new_text: str) -> str:
    """Re-attach externally-managed, delimited blocks (e.g. CodeGraph's guidance)
    from the OLD CLAUDE.md onto freshly-generated NEW content, so regenerating from
    the profile never silently deletes another tool's block. Pure + idempotent
    (a block already present in new_text is not duplicated; absent markers = no-op)."""
    import airuleset
    result = new_text
    for start, end in airuleset.EXTERNAL_BLOCK_MARKERS:
        if start in result:
            continue  # already present — don't duplicate
        si = old_text.find(start)
        ei = old_text.find(end)
        if si == -1 or ei == -1 or ei < si:
            continue  # no intact block in the old file
        block = old_text[si:ei + len(end)]
        result = result.rstrip("\n") + "\n\n" + block + "\n"
    return result


def load_hooks_json() -> dict:
    """Load the hooks definition from settings/hooks.json."""
    import airuleset
    if not airuleset.HOOKS_JSON.exists():
        return {}
    return json.loads(airuleset.HOOKS_JSON.read_text())


def merge_hooks_into_settings(hooks_config: dict, existing_settings: dict) -> dict:
    """Merge airuleset hooks into existing settings.json, preserving other keys.

    Strategy: remove all airuleset-managed hooks (identified by 'airuleset/hooks/' in command),
    then add all hooks from hooks.json. This ensures hooks.json is always the source of truth.
    """
    result = dict(existing_settings)

    if "hooks" not in hooks_config:
        return result

    if "hooks" not in result:
        result["hooks"] = {}

    for event_type, event_hooks in hooks_config["hooks"].items():
        if event_type not in result["hooks"]:
            result["hooks"][event_type] = []

        # Remove existing airuleset-managed hooks
        cleaned = []
        for entry in result["hooks"][event_type]:
            is_ours = False
            for hook in entry.get("hooks", []):
                if "airuleset/hooks/" in hook.get("command", ""):
                    is_ours = True
                    break
            if not is_ours:
                cleaned.append(entry)
        result["hooks"][event_type] = cleaned

        # Add all airuleset hooks from config (skip exact duplicates already present)
        for entry in event_hooks:
            if entry not in result["hooks"][event_type]:
                result["hooks"][event_type].append(entry)

    return result


def apply_managed_settings_defaults(settings: dict) -> dict:
    """Ensure airuleset's managed settings defaults are present (non-hook keys).

    - `effortLevel = high` (owner directive 2026-08-30, reverses the launch-flag
      half of #445) — managed sessions no longer launch with ultracode and the
      effort baseline drops `xhigh` → `high`; the launch script no longer bakes
      `--settings '{"ultracode":true}'` into any mode (user raises per session with
      `/effort`). Only the launch flags reversed — the doctrine/tiering are unchanged.
    - `disableAgentView = true` HARD-disables Claude Code's `claude agents` / fleet /
      `claude --bg` background daemon (the on-demand supervisor that spawns DETACHED
      background sessions which SURVIVE `/exit` and keep running/pinging untracked).
      The user runs explicit interactive `claude` in tmux and wants NO unmanaged
      background Claude — incident: a fleet session ran 2.9 days and kept pinging
      after the user `/exit`-ed it. Equivalent to env `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`.
      This does NOT affect in-session `run_in_background` subagents (the agent strip /
      autopilot-worker) — those are a separate, session-scoped mechanism that dies
      with the session. Takes effect on the NEXT `claude` launch.

    - `disableRemoteControl = true` + `remoteControlAtStartup = false` (user
      directive 2026-08-13, #439: "vypni vsade rc remote control aj v
      nastaveniach claude, vadi mi to") stop every session from attempting a
      Remote Control (RC) connection at startup — the persisted
      `remoteControlAtStartup: true` default made the statusline show
      `/rc connecting…` / `/rc failed` on every managed box. Both keys are
      real, not guessed: the installed 2.1.231 build's own binary carries
      the literal strings `disableRemoteControl` (12 occurrences) and
      `remoteControlAtStartup` (16 occurrences), same evidence class as
      promptSuggestionEnabled's own citation above. Same UNCONDITIONAL
      managed-default treatment as disableAgentView (the closest analog of
      the two here -- `tui`/`model` also carry a per-session `/tui`/`/model`
      escape hatch, these two do not: `/config` toggling RC back on reverts
      on the next push, same as disableAgentView): a managed box always
      gets both keys on the next install, overriding whatever was there
      before. dev1/dev2 already carry the equivalent hand-patched
      `settings.json` live (out of this pipeline, done manually); this is
      the fleet-wide enforcement so every OTHER managed box (subdev/gk
      users, a fresh box, a future hand-revert) self-heals the same way on
      its next push.

    - `tui = "fullscreen"` (#376, REVERSING the earlier `tui = "default"` pin) pins
      Claude Code's fullscreen (alt-screen) renderer fleet-wide. History: this
      function used to pin CLASSIC specifically because `Ctrl+B [` tmux-native
      scrollback goes EMPTY under fullscreen (nothing reaches tmux's own scrollback
      by design — david@gatekeeper 2026-07-09). But CLASSIC's own failure mode is
      worse and is what #376 was actually filed about: classic draws into tmux's
      NATIVE scrollback, which a resize/relayout event duplicates/loses bands of
      (upstream anthropics/claude-code#84247 + #46834, both confirmed still OPEN
      2026-08-11) — on tmux <3.6 (no synchronized output) this is routine, not
      rare; the fleet is NOT uniformly on an old build here (dev2/gk/subdev run
      3.4, but dev1 itself runs 3.7b — the corruption was live-reproduced on
      dev1's own 3.7b, so this is not purely a pre-3.6 problem, just a WORSE one
      there). Fullscreen keeps the WHOLE conversation in its OWN
      app-internal message list (`PgUp`/`PgDn` scroll it, `Ctrl+O` opens
      `/`-searchable transcript mode) — confirmed by Anthropic's own docs
      (code.claude.com/docs/en/fullscreen) to survive repeated compaction and to
      need no mouse (`PgUp`/`PgDn` alone reach it), which is what actually answers
      the complaint this ticket exists for. The `Ctrl+B [` regression is real and
      EXPECTED, not a bug in this change: `PgUp`/`PgDn` + `Ctrl+O` are fullscreen's
      documented replacement for it, not merely a workaround — verify this trade
      lands as intended on gk/david2 post-deploy (their long-running CLASSIC
      sessions need a relaunch/`/tui fullscreen` to pick this up — see below).
      Equivalent to env `CLAUDE_CODE_NO_FLICKER=1` (docs: "The `tui` setting and
      the environment variable are equivalent") — so #253's opt-in
      `claude-fullscreen` launcher mode is now redundant with this default (kept
      anyway, harmless, see CLAUDE_LAUNCH_SCRIPT_CONTENT's own comment). Takes
      effect on the NEXT `claude` launch — an ALREADY-RUNNING session needs a
      relaunch (or a manual `/tui fullscreen`) to switch, same latching this
      function's own `promptSuggestionEnabled` bullet documents for a different
      key.

    - `model = MANAGED_MODEL` (Fable 5.0 = `claude-fable-5[1m]` — user
      directive 2026-08-13, Opus 5 banned; Fable 5.1 ALSO banned per the
      owner directive 2026-09-04, #871) is the default MAIN-session model on
      every managed box — see MANAGED_MODEL's own comment for the history.
      The UNCONDITIONAL overwrite is exactly what SELF-HEALS a banned
      `model` back to `MANAGED_MODEL`: a stale banned id a prior session
      left in settings.json (an owner's `/model → Fable (5.1)` Enter, or a
      `model_changed` float that some client persisted) is rewritten on the
      next install/push. `airuleset.is_banned_model()` is the single shared
      predicate defining "banned" (reused by tests/test_launch_model_ban.py);
      MANAGED_MODEL is itself asserted never-banned by that test, so this
      overwrite can only ever land an ALLOWED id. Same unconditional-managed-
      default treatment as effortLevel/disableAgentView/tui; the user can
      still switch per session with `/model`.

    - `promptSuggestionEnabled = False` turns OFF Claude Code's predicted-next-
      prompt suggestion in the input box (#189). CC renders that suggestion as
      DIM (SGR 246) text after the `❯` glyph; `tmux capture-pane -p` strips
      attributes, so the watchdog's boundary classifiers see it as byte-identical
      to a draft the user typed, and every keystroke-sending job then refuses to
      act (or routes to a stash that has nothing to park). It was present on dev1
      only as an UNMANAGED local edit and absent on gatekeeper and montalu — a
      managed default so a push lands and self-heals it on every box. The key is
      real, not guessed: the installed 2.1.220 build carries it in the same
      global-settings key vector as effortLevel / autoCompactWindow / tui.
      NOTE this removes the SOURCE of the ambiguity, it is NOT the delivery fix —
      the value is latched at process init, so sessions already running keep
      rendering suggestions until they restart, which is precisely why
      `deliver_with_stash` was made independent of what the box appears to hold.

    - `autoCompactWindow` is ACTIVELY STRIPPED (2026-07-25 correction batch —
      reverts the SAME-DAY "krok 1c" addition). A low auto-compact threshold
      cuts big tasks off mid-work and defeats the 1M context window; context
      is bounded at ticket boundaries instead (the per-ticket `/compact`,
      watchdog job 14). This must POP the key, not merely stop setting it —
      an already-deployed settings.json from the reverted feature would
      otherwise keep carrying it forward untouched on every future install.

    - `env["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] = MANAGED_MAX_SUBAGENTS_PER_SESSION`
      (#288) raises the default 200-cumulative-spawn-per-session cap so a
      long-running /goal-armed autopilot session doesn't lose the Agent
      tool mid-day. Same unconditional-managed-default treatment as every
      other key above, applied fleet-wide (see MANAGED_MAX_SUBAGENTS_PER_SESSION's
      own comment for why no per-authority carve-out). Merges into any
      existing `env` sub-object rather than overwriting it, so a future
      feature that also needs an `env` key does not silently clobber this
      one (or vice versa).

    - `cleanupPeriodDays = MANAGED_CLEANUP_PERIOD_DAYS` (#376) overrides
      Claude Code's OWN native transcript-retention auto-cleanup (default
      30 days when unset -- see MANAGED_CLEANUP_PERIOD_DAYS's own comment
      for the confirmed source) so a fresh box never silently loses chat
      history to a default the user never configured. Same unconditional-
      managed-default treatment as every other key here.

    Idempotent; preserves all other keys."""
    import airuleset
    result = dict(settings)
    result["effortLevel"] = airuleset.MANAGED_EFFORT_LEVEL
    result["disableAgentView"] = True
    # #439: stop every session attempting an RC connection at startup -- see
    # this function's own docstring bullet above for the full citation.
    result["disableRemoteControl"] = True
    result["remoteControlAtStartup"] = False
    # #376: fullscreen is now the pin -- see this function's own docstring
    # bullet above for the full history/tradeoff/citation. The old ordering
    # concern ("re-check env-var-vs-setting precedence before changing this
    # pin") is resolved by Anthropic's own docs, not re-derived here: `tui`
    # and `CLAUDE_CODE_NO_FLICKER` are stated equivalent, so #253's launcher
    # mode is redundant-but-harmless post-#376, not removed (see
    # CLAUDE_LAUNCH_SCRIPT_CONTENT's own comment).
    result["tui"] = airuleset.MANAGED_TUI
    # Unconditional overwrite = self-heal of any banned `model`
    # (airuleset.is_banned_model — Opus 5 / Fable 5.1, #871) back to the
    # allowed managed default. See the docstring's `model` bullet.
    result["model"] = airuleset.MANAGED_MODEL
    result["promptSuggestionEnabled"] = False
    result.pop("autoCompactWindow", None)
    # A malformed/legacy `env` (a string/int/list rather than an object) must
    # be SELF-HEALED to a fresh dict, never crashed on — `dict(existing or
    # {})` raises on a non-dict-but-truthy value, which would escape
    # cmd_install (no enclosing try/except around this step) and, worse,
    # escape cmd_push's local-install call mid-deploy (it catches only
    # SystemExit, per #273 — so an ordinary exception here would run AFTER
    # `git push` to GitHub but BEFORE the remote-deploy loop, leaving main
    # updated and every remote host untouched). Adversarial-review finding,
    # #288.
    existing_env = result.get("env")
    result["env"] = dict(existing_env) if isinstance(existing_env, dict) else {}
    result["env"]["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] = airuleset.MANAGED_MAX_SUBAGENTS_PER_SESSION
    result["cleanupPeriodDays"] = airuleset.MANAGED_CLEANUP_PERIOD_DAYS
    return result


def read_file_safe(path: Path) -> str:
    """Read a file, returning empty string if it doesn't exist."""
    if path.exists():
        return path.read_text()
    return ""


def unified_diff(old: str, new: str, label: str) -> str:
    """Compute a unified diff between two strings."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines,
                                fromfile=f"a/{label}",
                                tofile=f"b/{label}")
    return "".join(diff)


def _validate_filedrop():
    """Validate the File-Drop service: each filedrop/*.py imports cleanly and the
    systemd service template exists with the repo-path placeholder + ExecStart."""
    import airuleset
    import importlib

    errors = []
    fd_dir = REPO_DIR / "filedrop"
    if not fd_dir.is_dir():
        errors.append(f"File-drop package missing: {fd_dir}")
        return errors

    for mod in ("filedrop", "filedrop.share", "filedrop.server"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            errors.append(f"File-drop module failed to import: {mod} ({e})")

    if not airuleset.FILEDROP_SERVICE_TEMPLATE.exists():
        errors.append(f"Missing file-drop service template: {airuleset.FILEDROP_SERVICE_TEMPLATE}")
    else:
        tmpl = airuleset.FILEDROP_SERVICE_TEMPLATE.read_text()
        if "{{REPO_DIR}}" not in tmpl:
            errors.append("File-drop service template missing {{REPO_DIR}} placeholder")
        if "{{HOST_IP}}" not in tmpl:
            errors.append("File-drop service template missing {{HOST_IP}} placeholder")
        if "{{HOST_IPS}}" not in tmpl:
            errors.append("File-drop service template missing {{HOST_IPS}} placeholder")
        if "filedrop --serve" not in tmpl:
            errors.append("File-drop service template ExecStart missing `filedrop --serve`")

    return errors


def _validate_tmux_cutover():
    """Validate the tmux boot-time cutover unit (#242): the systemd unit
    template exists and points ExecStart at the managed script + is ordered
    to run before login/ssh; the inline script CONTENT constant (same shape
    as the claude launcher's own CLAUDE_LAUNCH_SCRIPT_CONTENT) carries the
    expected paths and never references the packaged /usr/bin/tmux."""
    import airuleset
    errors = []
    if not airuleset.TMUX_CUTOVER_SERVICE_TEMPLATE.exists():
        errors.append(f"Missing tmux-cutover unit template: {airuleset.TMUX_CUTOVER_SERVICE_TEMPLATE}")
    else:
        t = airuleset.TMUX_CUTOVER_SERVICE_TEMPLATE.read_text()
        if airuleset.TMUX_CUTOVER_SCRIPT_DEST not in t:
            errors.append("tmux-cutover unit template ExecStart missing the managed script path")
        if "WantedBy=sysinit.target" not in t:
            errors.append("tmux-cutover unit template missing WantedBy=sysinit.target")
        if "DefaultDependencies=no" not in t:
            errors.append("tmux-cutover unit template missing DefaultDependencies=no")
    if "/usr/bin/tmux" in airuleset.TMUX_CUTOVER_SCRIPT_CONTENT:
        errors.append("tmux-cutover script must never reference the packaged /usr/bin/tmux")
    if airuleset.TMUX_CUTOVER_NEWEST not in airuleset.TMUX_CUTOVER_SCRIPT_CONTENT:
        errors.append("tmux-cutover script missing the managed NEWEST path")
    return errors


def _validate_watchdog():
    """Validate the api-watchdog: the package imports cleanly and the systemd
    service + timer templates exist with the repo-path placeholder + ExecStart."""
    import importlib

    errors = []
    wd_dir = REPO_DIR / "watchdog"
    if not wd_dir.is_dir():
        errors.append(f"api-watchdog package missing: {wd_dir}")
        return errors
    try:
        importlib.import_module("watchdog")
    except Exception as e:
        errors.append(f"api-watchdog module failed to import: ({e})")

    svc = REPO_DIR / "settings" / "api-watchdog.service.template"
    tmr = REPO_DIR / "settings" / "api-watchdog.timer.template"
    if not svc.exists():
        errors.append(f"Missing api-watchdog service template: {svc}")
    else:
        t = svc.read_text()
        if "{{REPO_DIR}}" not in t:
            errors.append("api-watchdog service template missing {{REPO_DIR}} placeholder")
        if "watchdog --once" not in t:
            errors.append("api-watchdog service template ExecStart missing `watchdog --once`")
        # #574 wiring seam: the optional per-box EnvironmentFile is what makes
        # any AIRULESET_* watchdog knob (e.g. AIRULESET_GOAL_LANE_STUCK_ALERT_STREAK)
        # reachable by the timer's env. The `-` prefix keeps it optional (a box without the
        # file is unaffected); a silent template revert must be caught.
        if "EnvironmentFile=-%h/.claude/watchdog.env" not in t:
            errors.append("api-watchdog service template missing the optional "
                          "per-box EnvironmentFile (`EnvironmentFile=-%h/.claude/"
                          "watchdog.env`)")
    if not tmr.exists():
        errors.append(f"Missing api-watchdog timer template: {tmr}")
    elif "OnUnitActiveSec" not in tmr.read_text():
        errors.append("api-watchdog timer template missing OnUnitActiveSec")

    return errors


def cmd_validate(args):
    """Check all module/rule files exist and all @import paths resolve."""
    import airuleset
    errors = []

    # Validate universal profile
    if not airuleset.UNIVERSAL_PROFILE.exists():
        errors.append(f"Missing profile: {airuleset.UNIVERSAL_PROFILE}")
    else:
        entries = parse_profile(airuleset.UNIVERSAL_PROFILE)
        for entry in entries:
            full_path = REPO_DIR / entry
            if not full_path.exists():
                errors.append(f"Missing file referenced in profile: {entry}")

    # Validate all profile files
    for profile in (REPO_DIR / "profiles").glob("*.profile"):
        try:
            entries = parse_profile(profile)
            for entry in entries:
                full_path = REPO_DIR / entry
                if not full_path.exists():
                    errors.append(f"[{profile.name}] Missing: {entry}")
        except SystemExit:
            errors.append(f"Failed to parse profile: {profile}")

    # Validate skills
    for skill in airuleset.SKILL_NAMES:
        skill_md = REPO_DIR / "skills" / skill / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"Missing skill: {skill_md}")

    # Validate agents
    for name in airuleset.AGENT_NAMES:
        agent_md = REPO_DIR / "agents" / f"{name}.md"
        if not agent_md.exists():
            errors.append(f"Missing agent: {agent_md}")

    # Validate hooks
    if airuleset.HOOKS_JSON.exists():
        try:
            hooks = json.loads(airuleset.HOOKS_JSON.read_text())
            # Check that referenced hook scripts exist
            for event_type, event_hooks in hooks.get("hooks", {}).items():
                for entry in event_hooks:
                    for hook in entry.get("hooks", []):
                        cmd = hook.get("command", "")
                        # Extract script path from command like "bash ~/devel/airuleset/hooks/foo.sh"
                        if "airuleset/hooks/" in cmd:
                            script_name = cmd.split("airuleset/hooks/")[-1]
                            script_path = REPO_DIR / "hooks" / script_name
                            if not script_path.exists():
                                errors.append(f"Missing hook script: {script_path}")
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in hooks.json: {e}")

    # Validate rules have frontmatter
    for rule_file in (REPO_DIR / "rules").glob("*.md"):
        content = rule_file.read_text()
        if not content.startswith("---"):
            errors.append(f"Rule missing YAML frontmatter: {rule_file.name}")

    # Validate the File-Drop service: filedrop/*.py loads + service template ok.
    errors.extend(_validate_filedrop())
    # Validate the api-watchdog: watchdog/ imports + service/timer templates ok.
    errors.extend(_validate_watchdog())
    # Validate the tmux boot-time cutover unit: template + script content ok.
    errors.extend(_validate_tmux_cutover())

    if errors:
        print("VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("All validations passed.")
        print(f"  Profiles: {len(list((REPO_DIR / 'profiles').glob('*.profile')))}")
        print(f"  Modules:  {len(list((REPO_DIR / 'modules').rglob('*.md')))}")
        print(f"  Rules:    {len(list((REPO_DIR / 'rules').glob('*.md')))}")
        print(f"  Skills:   {len(airuleset.SKILL_NAMES)}")
        print(f"  Agents:   {len(airuleset.AGENT_NAMES)}")


def cmd_diff(args):
    """Show what install would change (unified diff)."""
    import airuleset
    modules, global_rules = categorize_entries(parse_profile(airuleset.UNIVERSAL_PROFILE))
    new_claude_md = generate_claude_md(modules)
    old_claude_md = read_file_safe(airuleset.CLAUDE_MD)

    diff_md = unified_diff(old_claude_md, new_claude_md, "CLAUDE.md")
    if diff_md:
        print("=== ~/.claude/CLAUDE.md ===")
        print(diff_md)
    else:
        print("~/.claude/CLAUDE.md: no changes")

    # Settings diff
    hooks_config = load_hooks_json()
    if hooks_config:
        old_settings_str = read_file_safe(airuleset.SETTINGS_JSON)
        old_settings = json.loads(old_settings_str) if old_settings_str else {}
        new_settings = apply_managed_settings_defaults(
            merge_hooks_into_settings(hooks_config, old_settings))
        new_settings_str = json.dumps(new_settings, indent=2) + "\n"
        old_for_diff = old_settings_str if old_settings_str else "{}\n"

        diff_settings = unified_diff(old_for_diff, new_settings_str, "settings.json")
        if diff_settings:
            print("\n=== ~/.claude/settings.json ===")
            print(diff_settings)
        else:
            print("~/.claude/settings.json: no changes")

    # Skills diff (this box's set — scoped per skill_names_for_user)
    print("\n=== ~/.claude/skills/ (symlinks) ===")
    for skill in airuleset.skill_names_for_user():
        target = REPO_DIR / "skills" / skill
        link = airuleset.SKILLS_DIR / skill
        if link.is_symlink():
            current_target = Path(os.readlink(link))
            if current_target == target:
                print(f"  {skill}: OK (already linked)")
            else:
                print(f"  {skill}: CHANGE ({current_target} -> {target})")
        elif link.exists():
            print(f"  {skill}: REPLACE (existing dir/file -> symlink to {target})")
        else:
            print(f"  {skill}: ADD (new symlink -> {target})")

    # Rules diff (global path-scoped rules symlinked into ~/.claude/rules/)
    print("\n=== ~/.claude/rules/ (symlinks) ===")
    for entry in global_rules:
        name = Path(entry).name
        target = REPO_DIR / entry
        link = airuleset.RULES_DIR / name
        if link.is_symlink():
            current_target = Path(os.readlink(link))
            if current_target == target:
                print(f"  {name}: OK (already linked)")
            else:
                print(f"  {name}: CHANGE ({current_target} -> {target})")
        elif link.exists():
            print(f"  {name}: REPLACE (existing file -> symlink to {target})")
        else:
            print(f"  {name}: ADD (new symlink -> {target})")
