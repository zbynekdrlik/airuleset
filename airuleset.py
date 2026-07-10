#!/usr/bin/env python3
"""airuleset — Claude Code configuration management CLI.

Manages ~/.claude/CLAUDE.md imports, skills symlinks, and hook settings
from a centralized airuleset repository.

Usage:
    python airuleset.py install   # Deploy config to ~/.claude/
    python airuleset.py diff      # Show what install would change
    python airuleset.py validate  # Check all module/rule files exist
    python airuleset.py status    # Show current managed config
"""

import argparse
import difflib
import json
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
SETTINGS_JSON = CLAUDE_DIR / "settings.json"
SKILLS_DIR = CLAUDE_DIR / "skills"
AGENTS_DIR = CLAUDE_DIR / "agents"

MANAGED_HEADER = "# Managed by airuleset"
MANAGED_MARKER = "<!-- airuleset-managed -->"

# Externally-managed CLAUDE.md blocks to PRESERVE across regeneration. airuleset
# fully regenerates ~/.claude/CLAUDE.md from the profile; that would otherwise wipe
# a delimited block another tool injects. CodeGraph (`codegraph install`) appends
# its guidance block here — preserve it so a `push` doesn't silently delete it.
EXTERNAL_BLOCK_MARKERS = [("<!-- CODEGRAPH_START -->", "<!-- CODEGRAPH_END -->")]

# Managed default effort: `xhigh` (deep adaptive reasoning) is the persistent
# default the user wants in EVERY managed project so they never have to remember
# to raise it. `xhigh` is the highest level settings.json accepts and persists
# across sessions; `max`/`ultracode` are session-only (not valid here) — ultracode
# adds auto-workflow orchestration on top of xhigh and stays a per-session
# `/effort ultracode`. The user can still raise/lower per session with `/effort`.
MANAGED_EFFORT_LEVEL = "xhigh"

UNIVERSAL_PROFILE = REPO_DIR / "profiles" / "universal.profile"

# ---------------------------------------------------------------------------
# File-Drop integration — serve user files as clickable LAN URLs
# ---------------------------------------------------------------------------
# The file-drop service runs on EVERY machine — each serves the files produced on
# THAT machine, bound to THAT machine's own LAN IP (discovered at runtime by
# filedrop.host_ip()).
try:
    from filedrop import (PORT as FILEDROP_PORT, DEFAULT_PORT as FILEDROP_DEFAULT_PORT,
                          PORT_FILE as FILEDROP_PORT_FILE, persisted_port as filedrop_persisted_port,
                          host_ip as filedrop_host_ip, filedrop_url, FILEDROP_DIR)
except Exception:  # pragma: no cover — filedrop package should always import
    FILEDROP_PORT = int(os.environ.get("FILEDROP_PORT", "8788"))
    FILEDROP_DEFAULT_PORT = 8788
    FILEDROP_PORT_FILE = CLAUDE_DIR / "filedrop.port"
    FILEDROP_DIR = CLAUDE_DIR / "filedrop"

    def filedrop_persisted_port():
        return None

    def filedrop_host_ip():
        return os.environ.get("FILEDROP_HOST", "127.0.0.1")

    def filedrop_url():
        return f"http://{filedrop_host_ip()}:{FILEDROP_PORT}/"

FILEDROP_SERVICE_TEMPLATE = REPO_DIR / "settings" / "filedrop.service.template"
FILEDROP_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "filedrop.service"


# Skills directories in the repo that should be symlinked
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate", "architecture-check", "autopilot", "mutation-sweep", "meeting-analysis", "playbook-review", "playbook-cleanup", "mutation-testing", "local-builds", "batch-issue-development", "view-image-urls", "version-on-dashboard"]

# ---------------------------------------------------------------------------
# Caveman plugin — managed wiring (kept correct on every host by `install`)
# ---------------------------------------------------------------------------
# Caveman (JuliusBrussee/caveman) is a third-party Claude Code plugin the user
# relies on for compressed output. airuleset does NOT own its code, but DOES own
# keeping it wired correctly on every machine — it kept HALF-installing / breaking
# (plugin not enabled in enabledPlugins; statusLine pointing at a stale cache
# hash). The recurring breakage is the cache hash: the plugin's real statusline
# script lives under a content-hashed dir
# (~/.claude/plugins/cache/caveman/caveman/<hash>/hooks/caveman-statusline.sh)
# that CHANGES on every `claude plugin update`, so any hard-coded hash in
# settings.json rots and the statusline silently dies. Fix: ship a STABLE shim at
# a fixed path that resolves the current hash at RUNTIME, and point settings.json
# statusLine -> shim. `install` then reconciles enable + marketplace + statusLine
# on every push, self-healing both machines (the user asked to "put it into
# maintenance"). See modules/core/machine-identities.md sibling docs + memory.
CAVEMAN_MARKETPLACE_REPO = "JuliusBrussee/caveman"
CAVEMAN_PLUGIN_KEY = "caveman@caveman"
CAVEMAN_SHIM_DEST = CLAUDE_DIR / "airuleset-caveman-statusline.sh"
CAVEMAN_MODE_FILE = CLAUDE_DIR / ".caveman-active"
CAVEMAN_DEFAULT_MODE = "lite"
VALID_CAVEMAN_MODES = {
    "lite", "full", "ultra",
    "wenyan-lite", "wenyan-full", "wenyan-ultra",
}
# BOTH cache layouts: pre-2026-07 releases shipped <hash>/hooks/…, newer ones
# ship <hash>/src/hooks/… (a fresh install produces ONLY the new layout — the
# migrated gatekeeper box surfaced it: the old single-glob check saw "not
# built" forever and re-installed the plugin on every run).
CAVEMAN_CACHE_GLOBS = (
    "plugins/cache/caveman/caveman/*/hooks/caveman-statusline.sh",
    "plugins/cache/caveman/caveman/*/src/hooks/caveman-statusline.sh",
)
# Managed BASELINE plugins — every managed user's Claude must have these. The
# airuleset rules invoke their skills DIRECTLY (superpowers:brainstorming,
# writing-plans, subagent-driven-development, requesting-code-review are baked
# into the workflow + completion-report gates), so a user without them has
# commands like /brainstorming simply missing and gated audits reference
# nonexistent skills (david@gk, 2026-07-09). All from the built-in
# claude-plugins-official marketplace — no extraKnownMarketplaces entry needed.
MANAGED_PLUGINS = ("superpowers@claude-plugins-official",)
MANAGED_PLUGIN_CACHE_GLOBS = {
    "superpowers@claude-plugins-official":
        "plugins/cache/claude-plugins-official/superpowers/*/skills",
}
# Hash-independent entry to caveman's statusline + a context-fill meter. Must
# NEVER error (a broken statusline would break the prompt render). Caveman's real
# script lives under a content-hashed cache dir that changes on every `claude
# plugin update`; `ls -dt ... | head -1` resolves the newest hash at runtime so
# the path can't rot. A custom statusLine occupies the whole footer row, so the
# native context-fill indicator is unreliable — Claude Code pipes the session JSON
# on stdin (context_window.used_percentage etc., CC v2.1.132+) and caveman's script
# reads only its flag file, so the shim consumes stdin and renders the context
# meter itself, right next to the badge. Must NOT `exec` caveman (it has to keep
# running to append the meter). Prints nothing it can't safely render.
CAVEMAN_SHIM_CONTENT = r"""#!/usr/bin/env bash
# airuleset-managed (do NOT edit) — caveman badge + context-fill meter.
# caveman's real statusline lives under a content-hashed cache dir resolved at
# runtime (ls -dt ... | head -1) so a `claude plugin update` can never rot it.
in=$(cat)
real=$(ls -dt "$HOME"/.claude/plugins/cache/caveman/caveman/*/hooks/caveman-statusline.sh \
       "$HOME"/.claude/plugins/cache/caveman/caveman/*/src/hooks/caveman-statusline.sh 2>/dev/null | head -1)
badge=""
if [ -n "$real" ] && [ -f "$real" ]; then badge=$(bash "$real" </dev/null 2>/dev/null); fi
# de-emphasize caveman (least-important info): strip its bright color, lowercase,
# drop the brackets, render faint so it stops grabbing attention.
cm=""
if [ -n "$badge" ]; then
  plain=$(printf '%s' "$badge" | sed 's/\x1b\[[0-9;]*m//g' | tr 'A-Z' 'a-z')
  plain=${plain#[}; plain=${plain%]}
  [ -n "$plain" ] && cm=$(printf '\033[2m%s\033[0m' "$plain")
fi
meter=$(CTX_JSON="$in" python3 2>/dev/null <<'PY'
import os, json, time
try:
    d = json.loads(os.environ.get("CTX_JSON") or "{}")
except Exception:
    raise SystemExit
if not isinstance(d, dict):
    raise SystemExit
segs = []
def colr(pct, lo, hi):  # green below lo, yellow below hi, red at/above hi
    return 40 if pct < lo else (220 if pct < hi else 196)
# --- context-window fill (bar only — no % / tokens, per user pref) ---
cw = d.get("context_window") or {}
cu = cw.get("current_usage") or {}
size = cw.get("context_window_size") or 0
pct = cw.get("used_percentage")
if pct is None and cu:
    used = (cu.get("input_tokens") or 0) + (cu.get("cache_read_input_tokens") or 0) + (cu.get("cache_creation_input_tokens") or 0)
    pct = round(used / size * 100) if size else None
if pct is not None:
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 10.0)
    bar = "█" * filled + "░" * (10 - filled)
    c = colr(pct, 50, 80)
    segs.append("\033[38;5;%dmctx %s\033[0m" % (c, bar))
# --- usage limits (5h + weekly), high % = near the cap ---
rl = d.get("rate_limits") or {}
now = time.time()
def reset(ts):
    # CC stdin gives an epoch int; the watchdog cache gives an ISO-8601 string.
    if not ts:
        return ""
    try:
        s = int(ts) - now
    except (ValueError, TypeError):
        try:
            from datetime import datetime
            s = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() - now
        except Exception:
            return ""
    if s <= 0:
        return ""
    if s >= 86400:
        return " (%dd)" % round(s / 86400.0)
    if s >= 3600:
        return " (%dh)" % round(s / 3600.0)
    return " (%dm)" % max(1, round(s / 60.0))
for key, label in (("five_hour", "5h"), ("seven_day", "wk")):
    w = rl.get(key) or {}
    p = w.get("used_percentage")
    if p is None:
        continue
    p = max(0, min(100, int(p)))
    c = colr(p, 70, 90)
    segs.append("\033[38;5;%dm%s %s%%\033[0m\033[2m%s\033[0m" % (c, label, p, reset(w.get("resets_at"))))
# --- per-model usage window (Fable etc.) from the api-watchdog's oauth/usage cache.
# CC stdin `rate_limits` only carries the SHARED 5h + weekly; the per-model weekly
# (e.g. Fable's own limit — the binding one under max-performance) lives only in the
# oauth/usage limits[], which the watchdog polls every ~15 min and caches here. The
# 5h "session" window is account-wide (no per-model 5h exists). Never calls the API.
try:
    cc = json.load(open(os.path.expanduser("~/.claude/airuleset-usage-cache.json")))
except Exception:
    cc = None
if isinstance(cc, dict) and (now - (cc.get("ts") or 0)) < 6 * 3600:
    for w in cc.get("windows") or []:
        model = w.get("model")
        if not model:            # skip the shared windows (already shown above)
            continue
        p = w.get("percent")
        if p is None:
            continue
        p = max(0, min(100, int(p)))
        c = colr(p, 70, 90)
        segs.append("\033[38;5;%dm%s %s%%\033[0m\033[2m%s\033[0m" % (c, model, p, reset(w.get("resets_at"))))
# --- github ticket progress: autopilot done/total, else open issues ---
# Composed from local caches by statusbar.tickets_segment (a stale cache spawns a
# DETACHED `airuleset.py tickets-status --refresh`; the render never waits on gh).
# {{REPO_DIR}} is substituted at install time by render_caveman_shim().
try:
    import sys
    sys.path.insert(0, "{{REPO_DIR}}")
    import statusbar
    cwd = ((d.get("workspace") or {}).get("current_dir")) or d.get("cwd") or ""
    seg = statusbar.tickets_segment(cwd)
    if seg:
        segs.append(seg)
except Exception:
    pass
if not segs:
    raise SystemExit
print("  ".join(segs))
PY
)
# meter (ctx bar + usage limits) leads; faint caveman tag trails.
out="$meter"
if [ -n "$cm" ]; then
  if [ -n "$out" ]; then out="$out  $cm"; else out="$cm"; fi
fi
printf '%s' "$out"
exit 0
"""
CAVEMAN_STATUSLINE_COMMAND = f'bash "{CAVEMAN_SHIM_DEST}"'


def render_caveman_shim():
    """The shim content with per-machine placeholders substituted ({{REPO_DIR}} →
    this checkout, so the embedded python can import statusbar for the 🎫 ticket
    segment). The install write site MUST use this, never the raw constant."""
    return CAVEMAN_SHIM_CONTENT.replace("{{REPO_DIR}}", str(REPO_DIR))

# Subagent definitions (single .md files) symlinked into ~/.claude/agents/
AGENT_NAMES = ["autopilot-worker", "ticket-validator"]

HOOKS_JSON = REPO_DIR / "settings" / "hooks.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def generate_claude_md(modules: list[str]) -> str:
    """Generate the content for ~/.claude/CLAUDE.md with @import lines."""
    lines = [
        "# User-Wide Claude Code Instructions",
        "",
        f"{MANAGED_MARKER}",
        f"{MANAGED_HEADER} — https://github.com/zbynekdrlik/airuleset",
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
    result = new_text
    for start, end in EXTERNAL_BLOCK_MARKERS:
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
    if not HOOKS_JSON.exists():
        return {}
    return json.loads(HOOKS_JSON.read_text())


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


BASHRC = Path.home() / ".bashrc"
ULTRACODE_MARK_START = "# >>> airuleset: ultracode default >>>"
ULTRACODE_MARK_END = "# <<< airuleset: ultracode default <<<"
# The managed `claude` launcher (user's explicit default): ultracode + auto-approve
# permissions + CONTINUE-OR-NEW (2026-07-09): -c only when the cwd actually has a
# prior conversation; otherwise start fresh — unconditional -c died with
# "No conversation found to continue" in every new directory (david@gk).
#   --settings '{"ultracode":true}' : ultracode is SESSION-ONLY (never on disk, NOT
#       accepted in settings.json — GH #64817); --settings is the only doc-blessed
#       always-on route and MERGES per-key, so hooks/model/effortLevel stay intact.
#   --dangerously-skip-permissions  : auto-approve (the user opted in for their dev boxes).
#   -c                              : continue the most recent conversation in the cwd.
# The conversation probe globs ~/.claude/projects/<encoded-cwd>/*.jsonl — Claude Code
# encodes cwd by turning / . _ into dashes; a project dir holding only memory/ (no
# transcript) means nothing to continue. Unknown encoding chars fail toward the
# FRESH branch (worse case: a new session instead of a cryptic error).
# A bash FUNCTION (not alias) forwards all args; `command` avoids recursing.
# Escape hatches: `claude-new` (ultracode + skip-perms, FRESH session — no -c, force
# a clean start) and `claude-plain` (vanilla `claude`, no flags).
ULTRACODE_BASHRC_BLOCK = (
    f"{ULTRACODE_MARK_START}\n"
    # claude installs to ~/.local/bin, which NON-LOGIN interactive shells (su
    # without -, tmux with a default-command, IDE terminals) never get — only
    # ~/.profile adds it, and only login shells read that. Without this guard
    # the function resolves to nothing there ("claude: command not found" on
    # montalu@dev1, 2026-07-04). Idempotent: adds the dir once, never twice.
    'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac\n'
    "claude() {\n"
    '  local _ccdir="${PWD//\\//-}"; _ccdir="${_ccdir//./-}"; _ccdir="${_ccdir//_/-}"\n'
    '  if compgen -G "$HOME/.claude/projects/$_ccdir/*.jsonl" >/dev/null 2>&1; then\n'
    "    command claude --dangerously-skip-permissions -c "
    "--settings '{\"ultracode\":true}' \"$@\"\n"
    "  else\n"
    "    command claude --dangerously-skip-permissions "
    "--settings '{\"ultracode\":true}' \"$@\"\n"
    "  fi\n"
    "}\n"
    "claude-new() { command claude --dangerously-skip-permissions "
    "--settings '{\"ultracode\":true}' \"$@\"; }\n"
    "claude-plain() { command claude \"$@\"; }\n"
    f"{ULTRACODE_MARK_END}"
)


def apply_ultracode_launcher(bashrc_path: Path = None) -> bool:
    """Install/refresh the managed ~/.bashrc block that launches `claude` in
    ultracode on every shell. Idempotent: replaces the marked block if present,
    else appends it. Returns True iff the file changed."""
    import re
    path = bashrc_path or BASHRC
    existing = path.read_text() if path.exists() else ""
    if ULTRACODE_MARK_START in existing and ULTRACODE_MARK_END in existing:
        pattern = re.compile(
            re.escape(ULTRACODE_MARK_START) + r".*?" + re.escape(ULTRACODE_MARK_END),
            re.S)
        new = pattern.sub(lambda _m: ULTRACODE_BASHRC_BLOCK, existing)
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        new = f"{existing}{sep}\n{ULTRACODE_BASHRC_BLOCK}\n"
    if new != existing:
        path.write_text(new)
        return True
    return False


def apply_managed_settings_defaults(settings: dict) -> dict:
    """Ensure airuleset's managed settings defaults are present (non-hook keys).

    - `effortLevel = xhigh` so deep adaptive reasoning is the persistent default in
      every managed project without the user remembering to raise it. The user can
      still override per session with `/effort`.
    - `disableAgentView = true` HARD-disables Claude Code's `claude agents` / fleet /
      `claude --bg` background daemon (the on-demand supervisor that spawns DETACHED
      background sessions which SURVIVE `/exit` and keep running/pinging untracked).
      The user runs explicit interactive `claude` in tmux and wants NO unmanaged
      background Claude — incident: a fleet session ran 2.9 days and kept pinging
      after the user `/exit`-ed it. Equivalent to env `CLAUDE_CODE_DISABLE_AGENT_VIEW=1`.
      This does NOT affect in-session `run_in_background` subagents (the agent strip /
      autopilot-worker) — those are a separate, session-scoped mechanism that dies
      with the session. Takes effect on the NEXT `claude` launch.

    - `tui = "default"` pins the CLASSIC inline renderer. Without the key an
      Anthropic A/B gate decides, and the fullscreen-renderer onboarding dialog can
      set `tui = "fullscreen"` on a fresh account — then output lives in the tmux
      ALTERNATE screen, nothing reaches scrollback and `Ctrl+B [` history is EMPTY
      (recurring complaint; hit again on david@gatekeeper 2026-07-09). Deliberately
      OVERRIDES an existing "fullscreen" value: the user wants keyboard scrollback
      on every managed box, always. Takes effect on the NEXT `claude` launch.

    Idempotent; preserves all other keys."""
    result = dict(settings)
    result["effortLevel"] = MANAGED_EFFORT_LEVEL
    result["disableAgentView"] = True
    result["tui"] = "default"
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _validate_filedrop():
    """Validate the File-Drop service: each filedrop/*.py imports cleanly and the
    systemd service template exists with the repo-path placeholder + ExecStart."""
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

    if not FILEDROP_SERVICE_TEMPLATE.exists():
        errors.append(f"Missing file-drop service template: {FILEDROP_SERVICE_TEMPLATE}")
    else:
        tmpl = FILEDROP_SERVICE_TEMPLATE.read_text()
        if "{{REPO_DIR}}" not in tmpl:
            errors.append("File-drop service template missing {{REPO_DIR}} placeholder")
        if "{{HOST_IP}}" not in tmpl:
            errors.append("File-drop service template missing {{HOST_IP}} placeholder")
        if "filedrop --serve" not in tmpl:
            errors.append("File-drop service template ExecStart missing `filedrop --serve`")

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
    if not tmr.exists():
        errors.append(f"Missing api-watchdog timer template: {tmr}")
    elif "OnUnitActiveSec" not in tmr.read_text():
        errors.append("api-watchdog timer template missing OnUnitActiveSec")

    return errors


def cmd_validate(args):
    """Check all module/rule files exist and all @import paths resolve."""
    errors = []

    # Validate universal profile
    if not UNIVERSAL_PROFILE.exists():
        errors.append(f"Missing profile: {UNIVERSAL_PROFILE}")
    else:
        entries = parse_profile(UNIVERSAL_PROFILE)
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
    for skill in SKILL_NAMES:
        skill_md = REPO_DIR / "skills" / skill / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"Missing skill: {skill_md}")

    # Validate agents
    for name in AGENT_NAMES:
        agent_md = REPO_DIR / "agents" / f"{name}.md"
        if not agent_md.exists():
            errors.append(f"Missing agent: {agent_md}")

    # Validate hooks
    if HOOKS_JSON.exists():
        try:
            hooks = json.loads(HOOKS_JSON.read_text())
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
        print(f"  Skills:   {len(SKILL_NAMES)}")
        print(f"  Agents:   {len(AGENT_NAMES)}")


def cmd_diff(args):
    """Show what install would change (unified diff)."""
    modules, _rules = categorize_entries(parse_profile(UNIVERSAL_PROFILE))
    new_claude_md = generate_claude_md(modules)
    old_claude_md = read_file_safe(CLAUDE_MD)

    diff_md = unified_diff(old_claude_md, new_claude_md, "CLAUDE.md")
    if diff_md:
        print("=== ~/.claude/CLAUDE.md ===")
        print(diff_md)
    else:
        print("~/.claude/CLAUDE.md: no changes")

    # Settings diff
    hooks_config = load_hooks_json()
    if hooks_config:
        old_settings_str = read_file_safe(SETTINGS_JSON)
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

    # Skills diff
    print("\n=== ~/.claude/skills/ (symlinks) ===")
    for skill in SKILL_NAMES:
        target = REPO_DIR / "skills" / skill
        link = SKILLS_DIR / skill
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


def cmd_install(args):
    """Deploy config: generate CLAUDE.md, symlink skills, merge hooks."""
    print("airuleset install")
    print("=" * 50)

    # Ensure ~/.claude/ exists
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1. Generate ~/.claude/CLAUDE.md ---
    modules, _rules = categorize_entries(parse_profile(UNIVERSAL_PROFILE))
    new_claude_md = generate_claude_md(modules)

    if CLAUDE_MD.exists():
        old_content = CLAUDE_MD.read_text()
        # Preserve externally-managed blocks (CodeGraph) that live outside the profile.
        new_claude_md = preserve_external_blocks(old_content, new_claude_md)
        if old_content != new_claude_md:
            # Create backup
            backup = CLAUDE_MD.with_suffix(".md.bak")
            shutil.copy2(CLAUDE_MD, backup)
            print(f"  Backed up: {CLAUDE_MD} -> {backup}")
            CLAUDE_MD.write_text(new_claude_md)
            print(f"  Updated:   {CLAUDE_MD}")
        else:
            print(f"  No change: {CLAUDE_MD}")
    else:
        CLAUDE_MD.write_text(new_claude_md)
        print(f"  Created:   {CLAUDE_MD}")

    # --- 2. Symlink skills ---
    for skill in SKILL_NAMES:
        source = REPO_DIR / "skills" / skill
        link = SKILLS_DIR / skill

        if not source.exists():
            print(f"  SKIP skill (source missing): {source}")
            continue

        if link.is_symlink():
            current = Path(os.readlink(link))
            if current == source:
                print(f"  OK skill:  {skill}")
                continue
            link.unlink()
        elif link.exists():
            # Back up existing skill directory/file
            backup = link.with_suffix(".bak")
            if link.is_dir():
                if backup.exists():
                    shutil.rmtree(backup)
                shutil.move(str(link), str(backup))
            else:
                shutil.move(str(link), str(backup))
            print(f"  Backed up: {link} -> {backup}")

        link.symlink_to(source)
        print(f"  Linked:    {link} -> {source}")

    # --- 2b. Symlink agents (subagent definitions, single .md files) ---
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in AGENT_NAMES:
        source = REPO_DIR / "agents" / f"{name}.md"
        link = AGENTS_DIR / f"{name}.md"

        if not source.exists():
            print(f"  SKIP agent (source missing): {source}")
            continue

        if link.is_symlink():
            current = Path(os.readlink(link))
            if current == source:
                print(f"  OK agent:  {name}")
                continue
            link.unlink()
        elif link.exists():
            backup = link.with_suffix(".md.bak")
            shutil.move(str(link), str(backup))
            print(f"  Backed up: {link} -> {backup}")

        link.symlink_to(source)
        print(f"  Linked:    {link} -> {source}")

    # --- 3. Merge hooks into settings.json ---
    hooks_config = load_hooks_json()
    if hooks_config:
        old_settings_str = read_file_safe(SETTINGS_JSON)
        old_settings = json.loads(old_settings_str) if old_settings_str else {}
        new_settings = apply_managed_settings_defaults(
            merge_hooks_into_settings(hooks_config, old_settings))
        new_settings_str = json.dumps(new_settings, indent=2) + "\n"

        if old_settings_str.strip() != new_settings_str.strip():
            if SETTINGS_JSON.exists():
                backup = SETTINGS_JSON.with_suffix(".json.bak")
                shutil.copy2(SETTINGS_JSON, backup)
                print(f"  Backed up: {SETTINGS_JSON} -> {backup}")
            SETTINGS_JSON.write_text(new_settings_str)
            print(f"  Updated:   {SETTINGS_JSON}")
        else:
            print(f"  No change: {SETTINGS_JSON}")

    # --- 3b. ultracode launcher: managed ~/.bashrc block (every host) ---
    # ultracode can't live in settings.json (session-only, GH #64817), so wrap
    # `claude` to pass it via --settings on every launch. effortLevel=xhigh above
    # is the persistent fallback for the reasoning depth if the wrapper is bypassed.
    try:
        if apply_ultracode_launcher():
            print(f"  Updated:   {BASHRC} (ultracode launcher — `source ~/.bashrc`)")
        else:
            print(f"  No change: {BASHRC} (ultracode launcher)")
    except Exception as e:
        print(f"  ultracode launcher error (non-fatal): {e}", file=sys.stderr)

    # --- 4. File-Drop service: installed on EVERY machine (serves local files) ---
    try:
        maybe_setup_filedrop()
    except Exception as e:
        print(f"  filedrop setup error (non-fatal): {e}", file=sys.stderr)

    # --- 5. api-watchdog timer: every machine (auto-resume API-error stalls) ---
    try:
        maybe_setup_watchdog()
    except Exception as e:
        print(f"  watchdog setup error (non-fatal): {e}", file=sys.stderr)

    # --- 6. caveman plugin: every machine (enable + stable statusline shim) ---
    try:
        maybe_setup_caveman()
    except Exception as e:
        print(f"  caveman setup error (non-fatal): {e}", file=sys.stderr)

    # --- 6b. managed baseline plugins: superpowers (the rules invoke its skills) ---
    try:
        setup_managed_plugins()
    except Exception as e:
        print(f"  managed plugins setup error (non-fatal): {e}", file=sys.stderr)

    # --- 7. Discord notify config: warn LOUDLY if this host has no .env ---
    try:
        check_discord_notify_config()
    except Exception as e:
        print(f"  discord notify check error (non-fatal): {e}", file=sys.stderr)

    print()
    print("Install complete. Restart Claude Code for changes to take effect.")


def cmd_status(args):
    """Show current managed config (imports, skills, hooks)."""
    print("airuleset status")
    print("=" * 50)

    # --- CLAUDE.md ---
    print("\n~/.claude/CLAUDE.md:")
    if CLAUDE_MD.exists():
        content = CLAUDE_MD.read_text()
        if MANAGED_MARKER in content:
            imports = [ln.strip() for ln in content.splitlines()
                       if ln.strip().startswith("@~/")]
            print(f"  Managed by airuleset ({len(imports)} imports)")
            for imp in imports:
                # Check if the referenced file exists
                # @~/devel/airuleset/modules/... -> expand ~ to home
                path_str = imp[1:]  # remove @
                expanded = Path(path_str.replace("~/", str(Path.home()) + "/"))
                status = "OK" if expanded.exists() else "MISSING"
                print(f"    [{status}] {imp}")
        else:
            print("  Not managed by airuleset (no marker found)")
    else:
        print("  Does not exist")

    # --- Skills ---
    print("\n~/.claude/skills/:")
    for skill in SKILL_NAMES:
        link = SKILLS_DIR / skill
        expected_target = REPO_DIR / "skills" / skill
        if link.is_symlink():
            actual = Path(os.readlink(link))
            if actual == expected_target:
                print(f"  {skill}: OK (symlinked to airuleset)")
            else:
                print(f"  {skill}: MISMATCH (points to {actual})")
        elif link.exists():
            print(f"  {skill}: NOT MANAGED (exists but not a symlink)")
        else:
            print(f"  {skill}: NOT INSTALLED")

    # Other skills present
    if SKILLS_DIR.exists():
        all_skills = {p.name for p in SKILLS_DIR.iterdir()}
        managed = set(SKILL_NAMES)
        unmanaged = all_skills - managed
        if unmanaged:
            print(f"\n  Unmanaged skills: {', '.join(sorted(unmanaged))}")

    # --- Hooks ---
    print("\n~/.claude/settings.json hooks:")
    if SETTINGS_JSON.exists():
        try:
            settings = json.loads(SETTINGS_JSON.read_text())
            hooks = settings.get("hooks", {})
            if hooks:
                for event_type, entries in hooks.items():
                    for entry in entries:
                        matcher = entry.get("matcher", "*")
                        for hook in entry.get("hooks", []):
                            cmd = hook.get("command", hook.get("type", "?"))
                            is_ours = "airuleset" in cmd
                            tag = " (airuleset)" if is_ours else ""
                            print(f"  {event_type}[{matcher}]: {cmd}{tag}")
            else:
                print("  No hooks configured")
        except json.JSONDecodeError:
            print("  ERROR: Invalid JSON in settings.json")
    else:
        print("  settings.json does not exist")


# ---------------------------------------------------------------------------
# systemd --user helpers (shared by the File-Drop service install)
# ---------------------------------------------------------------------------


def _xdg_runtime_env():
    """A copy of os.environ with XDG_RUNTIME_DIR set explicitly.

    `systemctl --user` needs XDG_RUNTIME_DIR to find the user bus; when install
    runs over SSH (no login session) it is often unset. We set it deterministically
    to /run/user/<uid>."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run_systemctl(args):
    """Run `systemctl --user <args>` with the explicit XDG env. Returns
    (returncode, stdout, stderr). Never raises."""
    import subprocess
    try:
        r = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True, text=True, timeout=30, env=_xdg_runtime_env())
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def _whoami():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "")


# ---------------------------------------------------------------------------
# File-Drop systemd service + share/serve subcommands
# ---------------------------------------------------------------------------


def _render_filedrop_unit(port=None):
    """Read the file-drop unit template and substitute the per-machine placeholders.

    {{REPO_DIR}} -> this checkout's path (ExecStart). {{HOST_IP}} -> the LAN IP
    this machine should bind, computed HERE (unsandboxed, so `hostname -I` works)
    and baked into Environment=FILEDROP_HOST so the sandboxed server never needs
    AF_NETLINK to discover its own address. {{PORT}} -> the per-user port chosen
    by _choose_filedrop_port (a second airuleset user on the same host cannot
    reuse the first user's :8788)."""
    return (FILEDROP_SERVICE_TEMPLATE.read_text()
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{HOST_IP}}", filedrop_host_ip())
            .replace("{{PORT}}", str(port if port is not None else FILEDROP_PORT)))


def _choose_filedrop_port(bind_ip):
    """The port this user's file-drop should serve on.

    Two airuleset users on ONE host (montalu@dev1, marek@gatekeeper) collide on
    the default :8788 — the second user's service restart-loops on Errno 98
    (observed on montalu@dev1, 2026-07-04). Precedence:
      1. FILEDROP_PORT env — explicit override, never second-guessed.
      2. A previously PERSISTED choice (~/.claude/filedrop.port) — stable across
         installs so the URL never silently moves.
      3. The default, when OUR OWN service is already actively serving it.
      4. Probe bind on the actual bind IP: default free → default; taken by a
         FOREIGN instance → first free port in 8789-8798, persisted so the serve
         unit, the share CLI, and `filedrop status` all agree on the same URL.
    Fail-open to the default when nothing binds (the service then fails loudly,
    exactly as before)."""
    env = os.environ.get("FILEDROP_PORT")
    if env:
        return int(env)
    persisted = filedrop_persisted_port()
    if persisted:
        return persisted
    rc, out, _err = _run_systemctl(["is-active", "filedrop.service"])
    if rc == 0 and out.strip() == "active":
        return FILEDROP_DEFAULT_PORT     # our own live instance owns the default
    import socket as _socket
    for cand in range(FILEDROP_DEFAULT_PORT, FILEDROP_DEFAULT_PORT + 11):
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            s.bind((bind_ip, cand))
        except OSError:
            continue
        finally:
            s.close()
        if cand != FILEDROP_DEFAULT_PORT:
            try:
                FILEDROP_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
                FILEDROP_PORT_FILE.write_text(f"{cand}\n")
                print(f"  Port {FILEDROP_DEFAULT_PORT} taken by another file-drop "
                      f"on this host — using {cand} (persisted to {FILEDROP_PORT_FILE})")
            except OSError as e:
                print(f"  could not persist file-drop port choice ({e})",
                      file=sys.stderr)
        return cand
    return FILEDROP_DEFAULT_PORT


def _filedrop_is_live(url, timeout=2):
    """True iff GET <url> returns an HTTP response (root returns 404 by design,
    which still proves the server is up). Any completed request = live."""
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # 404 at root is expected — the server answered
    except Exception:
        return False


def _wait_filedrop_live(url, attempts=5, delay=1.0):
    import time
    for _ in range(attempts):
        if _filedrop_is_live(url):
            return True
        time.sleep(delay)
    return False


def _restart_filedrop_service():
    rc, _o, err = _run_systemctl(["restart", "filedrop.service"])
    if rc != 0:
        print(f"  filedrop service restart failed (rc={rc}): {err.strip()}",
              file=sys.stderr)
    return rc == 0


def setup_filedrop_service():
    """Install + start the file-drop systemd --user service on THIS machine.

    Runs on every host (no board-style gating). Creates the served dir first
    (the read-only server never writes it), writes the unit, enables linger, and
    enable --now. On any failure it prints the manual command rather than claiming
    success."""
    import subprocess
    print("  Installing file-drop systemd --user service")

    # 1. served dir (0700) — the read-only server depends on it existing.
    try:
        FILEDROP_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(str(FILEDROP_DIR), 0o700)
    except OSError as e:
        print(f"  could not create {FILEDROP_DIR} ({e})", file=sys.stderr)

    # 2. write the unit — with the per-user port (a second airuleset user on the
    # same host must not restart-loop on the first user's :8788).
    if not FILEDROP_SERVICE_TEMPLATE.exists():
        print(f"  ERROR: file-drop service template missing: "
              f"{FILEDROP_SERVICE_TEMPLATE}", file=sys.stderr)
        return False
    port = _choose_filedrop_port(filedrop_host_ip())
    FILEDROP_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    FILEDROP_SERVICE_DEST.write_text(_render_filedrop_unit(port))
    print(f"  Wrote unit: {FILEDROP_SERVICE_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "filedrop.service")

    # 3. linger (best-effort)
    try:
        subprocess.run(["loginctl", "enable-linger", _whoami()],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)

    # 4. daemon-reload + enable --now
    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  systemctl daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    rc, _o, err = _run_systemctl(["enable", "--now", "filedrop.service"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False

    # 4b. restart to apply the freshly-written unit + latest filedrop code.
    # `enable --now` is a no-op for an already-running service, so a re-install
    # with a changed unit (e.g. a new bind IP) or new code needs an explicit
    # restart. Stateless file server — the brief blip is harmless.
    _run_systemctl(["restart", "filedrop.service"])

    # 5. liveness check on the LAN URL (server binds the LAN IP, not loopback).
    # Built from the port chosen ABOVE — the module-level PORT was resolved at
    # import time, i.e. before a fresh port choice was persisted this run.
    url = f"http://{filedrop_host_ip()}:{port}/"
    if _wait_filedrop_live(url):
        print(f"  File-drop is live. LAN base URL: {url}")
        return True
    print(f"  File-drop service started but did NOT answer on {url}. Check "
          f"`systemctl --user status filedrop.service`.", file=sys.stderr)
    return False


def check_discord_notify_config():
    """Report whether Discord notifications are wired on THIS host (no secrets printed).

    The Discord `.env` (bot token + per-owner channels/mentions) is LOCAL and NOT
    git-deployed — `install` cannot carry it. A host that never got it wired sends
    NOTHING: every notify call fail-safes to a silent no-op. That is exactly how the
    gatekeeper box went dark (the `.env` was never wired when it was added). This
    check makes the gap LOUD at install time instead of a silent failure discovered
    weeks later. It NEVER prints the token value — only presence."""
    env = CLAUDE_DIR / "channels" / "discord" / ".env"
    print("  Checking Discord notify config")
    if not env.is_file():
        print("    ⚠ Discord notify DISABLED — no ~/.claude/channels/discord/.env on this host.")
        print("      Pings (❓/✅, api-error, autopilot cards) will silently NOT send.")
        print("      Wire it from an already-configured host (secrets stay local, not git):")
        print("        cat ~/.claude/channels/discord/.env | ssh <this-host> \\")
        print("          'umask 077 && mkdir -p ~/.claude/channels/discord && "
              "cat > ~/.claude/channels/discord/.env'")
        return
    token = ""
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip()
            break
    if not token:
        print("    ⚠ Discord .env present but DISCORD_BOT_TOKEN is empty — pings will not send.")
    else:
        print("    Discord notify: configured (bot token present).")


def maybe_setup_filedrop():
    """Install the file-drop service on this machine (every host runs one)."""
    setup_filedrop_service()


# ---------------------------------------------------------------------------
# Caveman plugin wiring (every host) — see the constants block up top
# ---------------------------------------------------------------------------

def reconcile_caveman_settings(settings: dict,
                               statusline_command: str = CAVEMAN_STATUSLINE_COMMAND) -> dict:
    """Pure: return a new settings dict with caveman correctly wired —
    statusLine -> the stable shim, the plugin enabled, the marketplace known.
    Every other key is preserved untouched. Idempotent (same input -> same output)."""
    result = dict(settings)
    result["statusLine"] = {"type": "command", "command": statusline_command}
    enabled = dict(result.get("enabledPlugins", {}))
    enabled[CAVEMAN_PLUGIN_KEY] = True
    result["enabledPlugins"] = enabled
    markets = dict(result.get("extraKnownMarketplaces", {}))
    markets["caveman"] = {"source": {"source": "github", "repo": CAVEMAN_MARKETPLACE_REPO}}
    result["extraKnownMarketplaces"] = markets
    return result


def caveman_mode_or_default(existing) -> str:
    """Pure: keep the user's current caveman mode if it's valid, else fall back
    to the managed default. Never clobbers a valid `/caveman` pick; only repairs
    a missing/empty/garbage mode file."""
    if existing is not None:
        mode = str(existing).strip()
        if mode in VALID_CAVEMAN_MODES:
            return mode
    return CAVEMAN_DEFAULT_MODE


def _caveman_plugin_built() -> bool:
    """True iff caveman's plugin cache (the real statusline script) exists on disk
    — in EITHER cache layout (old <hash>/hooks/, new <hash>/src/hooks/)."""
    import glob
    return any(glob.glob(str(CLAUDE_DIR / g)) for g in CAVEMAN_CACHE_GLOBS)


def setup_caveman():
    """Keep the caveman plugin correctly wired on THIS machine (idempotent).

    1. write the stable statusline shim (hash-independent),
    2. install the plugin if its cache is missing (best-effort, time-boxed),
    3. reconcile settings.json (enable + marketplace + statusLine -> shim),
    4. seed a valid `.caveman-active` mode (preserve a valid user pick).
    Non-fatal: prints the manual step on any failure rather than aborting install."""
    import subprocess
    print("  Wiring caveman plugin (managed)")

    # 1. stable shim — survives `claude plugin update` cache-hash churn.
    try:
        CAVEMAN_SHIM_DEST.write_text(render_caveman_shim())
        os.chmod(str(CAVEMAN_SHIM_DEST), 0o755)
    except OSError as e:
        print(f"    could not write caveman shim ({e})", file=sys.stderr)

    # 2. install if the plugin cache is missing (best-effort).
    if not _caveman_plugin_built():
        try:
            r = subprocess.run(
                ["claude", "plugin", "install", CAVEMAN_PLUGIN_KEY],
                capture_output=True, text=True, timeout=120,
                env=_claude_cli_env())
            if r.returncode == 0:
                print(f"    installed {CAVEMAN_PLUGIN_KEY}")
            else:
                print(f"    could not install {CAVEMAN_PLUGIN_KEY} (rc={r.returncode}): "
                      f"{(r.stderr or r.stdout).strip()[:200]}\n"
                      f"    Run manually: claude plugin install {CAVEMAN_PLUGIN_KEY}",
                      file=sys.stderr)
        except Exception as e:
            print(f"    caveman install skipped ({e}); run: "
                  f"claude plugin install {CAVEMAN_PLUGIN_KEY}", file=sys.stderr)

    # 3. reconcile settings.json (runs AFTER the main settings write in cmd_install).
    raw = read_file_safe(SETTINGS_JSON)
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("    settings.json invalid JSON — skipped caveman reconcile", file=sys.stderr)
        settings = None
    if settings is not None:
        new_str = json.dumps(reconcile_caveman_settings(settings), indent=2) + "\n"
        if new_str.strip() != raw.strip():
            if SETTINGS_JSON.exists():
                shutil.copy2(SETTINGS_JSON, SETTINGS_JSON.with_suffix(".json.bak"))
            SETTINGS_JSON.write_text(new_str)
            print("    settings.json: enabled + statusLine -> stable shim")
        else:
            print("    settings.json: already correct")

    # 4. seed a valid mode (preserve a valid user choice).
    existing = CAVEMAN_MODE_FILE.read_text() if CAVEMAN_MODE_FILE.exists() else None
    mode = caveman_mode_or_default(existing)
    if existing is None or existing.strip() != mode:
        try:
            CAVEMAN_MODE_FILE.write_text(mode)
            print(f"    mode: {mode}")
        except OSError as e:
            print(f"    could not write caveman mode ({e})", file=sys.stderr)


def maybe_setup_caveman():
    """Wire the caveman plugin on this machine (every host)."""
    setup_caveman()


# ---------------------------------------------------------------------------
# Managed baseline plugins (every host) — see MANAGED_PLUGINS up top
# ---------------------------------------------------------------------------

def _claude_cli_env() -> dict:
    """Env for invoking the `claude` CLI from install: a push's remote install
    runs in a NON-LOGIN ssh shell whose PATH lacks ~/.local/bin — where the CLI
    lives — so a bare subprocess call dies with [Errno 2] 'claude' (seen live
    on the gatekeeper migration, 2026-07-05). Prepend it idempotently."""
    local_bin = str(Path.home() / ".local" / "bin")
    path = os.environ.get("PATH", "")
    if local_bin not in path.split(":"):
        path = f"{local_bin}:{path}" if path else local_bin
    return {**os.environ, "PATH": path}


def reconcile_managed_plugins(settings: dict) -> dict:
    """Pure: return a new settings dict with every managed baseline plugin
    enabled. Every other key preserved untouched; idempotent."""
    result = dict(settings)
    enabled = dict(result.get("enabledPlugins", {}))
    for key in MANAGED_PLUGINS:
        enabled[key] = True
    result["enabledPlugins"] = enabled
    return result


def _managed_plugin_built(key: str) -> bool:
    """True iff the plugin's cache exists on disk (any version dir)."""
    import glob
    return bool(glob.glob(str(CLAUDE_DIR / MANAGED_PLUGIN_CACHE_GLOBS[key])))


def setup_managed_plugins():
    """Ensure the managed baseline plugins are installed + enabled (idempotent).

    1. install any plugin whose cache is missing (best-effort, time-boxed),
    2. reconcile settings.json (enabledPlugins keys true).
    Non-fatal: prints the manual step on failure rather than aborting install."""
    import subprocess
    print("  Wiring managed baseline plugins")

    for key in MANAGED_PLUGINS:
        if _managed_plugin_built(key):
            continue
        try:
            r = subprocess.run(
                ["claude", "plugin", "install", key],
                capture_output=True, text=True, timeout=180,
                env=_claude_cli_env())
            if r.returncode == 0:
                print(f"    installed {key}")
            else:
                print(f"    could not install {key} (rc={r.returncode}): "
                      f"{(r.stderr or r.stdout).strip()[:200]}\n"
                      f"    Run manually: claude plugin install {key}",
                      file=sys.stderr)
        except Exception as e:
            print(f"    {key} install skipped ({e}); run: "
                  f"claude plugin install {key}", file=sys.stderr)

    raw = read_file_safe(SETTINGS_JSON)
    try:
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("    settings.json invalid JSON — skipped plugin reconcile",
              file=sys.stderr)
        return
    new_str = json.dumps(reconcile_managed_plugins(settings), indent=2) + "\n"
    if new_str.strip() != raw.strip():
        if SETTINGS_JSON.exists():
            shutil.copy2(SETTINGS_JSON, SETTINGS_JSON.with_suffix(".json.bak"))
        SETTINGS_JSON.write_text(new_str)
        print(f"    settings.json: enabled {', '.join(MANAGED_PLUGINS)}")
    else:
        print("    settings.json: already correct")


def _filedrop_serve():
    """Run the file-drop HTTP server in the FOREGROUND (systemd ExecStart target)."""
    from filedrop.server import run_server
    run_server(host=filedrop_host_ip(), port=FILEDROP_PORT)


def cmd_share(args):
    """Copy a file into the file-drop server and print its clickable LAN URL.

    Prints ONLY the URL on stdout (easy to copy); diagnostics go to stderr. Per
    no-localhost-urls.md, the URL is live-checked before printing — if the server
    is down it tries one restart, and refuses to print a dead URL."""
    from filedrop.share import ShareError, share
    try:
        url, dest = share(args.path)
    except ShareError as e:
        print(f"share: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"share: unexpected error ({e})", file=sys.stderr)
        sys.exit(1)

    if _filedrop_is_live(url):
        print(url)
        return
    # Down — try a single restart, then re-check.
    print("share: file-drop not responding — attempting service restart...",
          file=sys.stderr)
    _restart_filedrop_service()
    if _wait_filedrop_live(url):
        print(url)
        return
    print(f"share: file copied to {dest} but the file-drop server is DOWN at "
          f"{filedrop_url()} — start it with "
          f"`systemctl --user start filedrop.service`.", file=sys.stderr)
    sys.exit(1)


def _filedrop_status():
    url = filedrop_url()
    live = _filedrop_is_live(url)
    print(f"file-drop: {url}")
    print(f"  this machine: serves {FILEDROP_DIR}")
    print(f"  liveness:     {'UP' if live else 'DOWN / unreachable'}")


def cmd_filedrop(args):
    """File-drop control: --serve (daemon), --url (live-check + print), status."""
    if getattr(args, "serve", False):
        _filedrop_serve()
        return
    if getattr(args, "url", False):
        url = filedrop_url()
        if _filedrop_is_live(url):
            print(url)
        else:
            print(f"file-drop: DOWN — {url} unreachable", file=sys.stderr)
            sys.exit(1)
        return
    _filedrop_status()


def cmd_notify(args):
    """Send a Discord notification (with the tmux-owner @mention prepended).

    Modes:
      --mention-prefix     print just the '<@id> ' prefix for the current tmux
                           owner (used by hooks/notify-discord.sh) and exit.
      --channel-id         print the resolved per-owner Discord channel/thread id
                           (DISCORD_NOTIFICATION_CHANNEL_<OWNER>, else the shared
                           DISCORD_NOTIFICATION_CHANNEL_ID) and exit — the single
                           source of truth the shell send path reads.
      --owner              print the resolved tmux owner and exit — lets the shell
                           hook resolve ONCE and force the same owner onto both the
                           --mention-prefix and --channel-id calls (so they agree).
      --autopilot-done     compose + send the canonical per-ticket completion card
                           from fields (--repo --pr --merge-sha --version --review
                           --done --remaining --tickets-json). Deduped on repo#pr.
      --body "<markdown>"  send arbitrary markdown (the general primitive).
    """
    from notify import (compose_autopilot_card, mention_prefix, mirror_owners,
                        notification_channel, resolve_owner, send)

    if getattr(args, "record_question", False):
        # Record a ❓ ping's Discord message id → the session that asked, so the
        # watchdog can route the user's Discord REPLY back into that session.
        from notify import record_question
        ok = record_question(args.message_id, args.channel, args.session, args.cwd)
        sys.stdout.write("recorded" if ok else "skip")
        return

    if getattr(args, "edit_question", False):
        # EDIT the session's recent ❓ ping in place with the reworded question
        # from stdin (edits don't push-ping — the pending hook's anti-spam path;
        # camera-box got 3 pings in 3 min for one reworded question, 2026-07-05).
        # rc 2 = nothing recent/editable → the caller falls back to a fresh POST.
        from notify import update_question
        ok = update_question(getattr(args, "session", "") or "", sys.stdin.read())
        sys.stdout.write("edited" if ok else "no-recent-question")
        sys.exit(0 if ok else 2)

    if getattr(args, "owner", False):
        sys.stdout.write(resolve_owner())
        return

    if getattr(args, "mention_prefix", False):
        sys.stdout.write(mention_prefix())
        return

    if getattr(args, "channel_id", False):
        sys.stdout.write(notification_channel())
        return

    if getattr(args, "mirror_owners", False):
        # space-separated parallel/CC recipients for the current owner (shell path)
        sys.stdout.write(" ".join(mirror_owners()))
        return

    if getattr(args, "run_card", False):
        _notify_run_card(args, compose_autopilot_card, send)
        return

    if getattr(args, "api_error", False):
        from notify import compose_api_error_alert, is_api_error
        text = args.text or ""
        if not is_api_error(text):
            return  # not a real API error → say nothing (no false ping)
        import hashlib
        project = args.project or ""
        sess = args.session or ""
        h = hashlib.sha1(text.strip().encode()).hexdigest()[:12]
        # One ping per distinct error text per session (a wedge that keeps showing
        # the same error across Stop events pings once, not every turn).
        dedup = args.dedup_key or ("apierr:%s:%s" % (sess, h))
        body = compose_api_error_alert(project, text)
        print(send(body, dedup_key=dedup, dry_run=args.dry_run))
        return

    if getattr(args, "autopilot_done", False):
        try:
            tickets = json.loads(args.tickets_json) if args.tickets_json else []
        except (ValueError, TypeError):
            print("notify: --tickets-json is not valid JSON", file=sys.stderr)
            sys.exit(1)
        body = compose_autopilot_card(
            repo=args.repo, tickets=tickets, pr=args.pr,
            version=args.version, merge_sha=args.merge_sha,
            review_ok=(args.review != "fail"),
            done=args.done, remaining=args.remaining)
        dedup = args.dedup_key
        if dedup is None and args.repo and args.pr:
            dedup = "%s#%s" % (args.repo, args.pr)
        print(send(body, dedup_key=dedup, dry_run=args.dry_run))
        return

    if args.body is not None:
        print(send(args.body, dedup_key=args.dedup_key, dry_run=args.dry_run))
        return

    print("notify: nothing to send (use --autopilot-done, --run-card, --body, "
          "or --mention-prefix)", file=sys.stderr)
    sys.exit(1)


def _gh_out(*gh_args, timeout=8):
    """Best-effort `gh ...` stdout (stripped), or "" on any failure/timeout."""
    import subprocess
    try:
        r = subprocess.run(["gh", *gh_args], capture_output=True, text=True,
                           timeout=timeout)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _write_autopilot_progress(name, remaining):
    """Persist per-repo autopilot run progress for the statusline github-tickets segment
    (~/.claude/autopilot-progress/<repo>.json). `done` counts the completion
    cards sent within ONE run window; a card after a ≥6h gap starts a new run.
    Best-effort — a failure here never blocks the card send."""
    import re
    import time
    import statusbar
    try:
        name = re.sub(r"[^A-Za-z0-9._-]", "", str(name or "")).lstrip(".")
        if not name:
            return
        d = statusbar.progress_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / (name + ".json")
        now = int(time.time())
        try:
            prev = json.loads(p.read_text())
        except (OSError, ValueError):
            prev = None
        if not isinstance(prev, dict):
            prev = None
        done = 1
        if prev and now - (prev.get("ts") or 0) <= statusbar.AUTOPILOT_RUN_WINDOW_S:
            done = int(prev.get("done") or 0) + 1
        if not isinstance(remaining, int):
            remaining = prev.get("remaining") if prev else None
        tmp = str(p) + ".tmp"
        Path(tmp).write_text(json.dumps({"done": done, "remaining": remaining,
                                         "ts": now}))
        os.replace(tmp, p)
    except Exception:
        pass


def cmd_tickets_status(args):
    """Statusline github-tickets segment. Default: PRINT the segment for --cwd
    (composed from local caches; may spawn a detached refresh). --refresh: the
    SLOW path — resolve the repo at --cwd via git+gh and rewrite its cache
    (~/.claude/tickets-status/). The statusline shim never runs the slow path
    inline; it reads the caches and lets this command refresh in the background."""
    import subprocess
    import time
    import statusbar

    cwd = getattr(args, "cwd", None) or os.getcwd()
    if not getattr(args, "refresh", False):
        sys.stdout.write(statusbar.tickets_segment(cwd))
        return

    def _out(argv, cd):
        try:
            r = subprocess.run(argv, cwd=cd, capture_output=True, text=True,
                               timeout=20)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    entry = {"ts": int(time.time()), "open": None, "name": "", "root": ""}
    root = _out(["git", "rev-parse", "--show-toplevel"], cwd)
    if root:
        entry["root"] = root
        slug = _out(["gh", "repo", "view", "--json", "nameWithOwner",
                     "-q", ".nameWithOwner"], root)
        entry["name"] = slug.rstrip("/").split("/")[-1] if slug else ""
        n = _out(["gh", "issue", "list", "--state", "open", "--search",
                  "-label:autopilot-skip", "-L", "200",
                  "--json", "number", "-q", "length"], root)
        try:
            entry["open"] = int(n)
        except (TypeError, ValueError):
            entry["open"] = None
    cache = statusbar.cache_dir() / (statusbar.cwd_key(cwd) + ".json")
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(cache) + ".tmp"
    Path(tmp).write_text(json.dumps(entry))
    os.replace(tmp, cache)
    print("refreshed open=%s name=%s" % (entry["open"], entry["name"] or "-"))


def _notify_run_card(args, compose_autopilot_card, send):
    """Send the per-ticket completion card, gathering the issue title (the Cieľ)
    and the remaining backlog count from gh. The autopilot worker fires this
    DIRECTLY at merge (`notify --run-card --repo <owner/name> --issue <N> --pr
    <url> --achieved "<slovak>"`), so it runs in the worker's context (gh auth,
    tmux owner, the channel .env). REQUIRES --repo + --issue. Best-effort, never
    raises."""
    try:
        repo = getattr(args, "repo", None)
        issue = getattr(args, "issue", None)
        if not repo or issue is None:
            return  # need --repo + --issue to build a card

        title = _gh_out("issue", "view", str(issue), "-R", repo,
                        "--json", "title", "-q", ".title") or ("#%s" % issue)
        rem_raw = _gh_out("issue", "list", "-R", repo, "--state", "open",
                          "--search", "-label:autopilot-skip", "-L", "200",
                          "--json", "number", "-q", "length")
        try:
            remaining = int(rem_raw)
        except (TypeError, ValueError):
            remaining = None

        achieved = getattr(args, "achieved", None) or getattr(args, "result", None)
        # 🎯 Cieľ = the worker's PLAIN-language --goal (simple, understandable); the
        # technical gh issue title is only the fallback when --goal is omitted.
        goal = getattr(args, "goal", None) or title
        # --pr is the full PR URL → a clickable "kód (PR)" link (the number was
        # dropped, the link kept). --url = "where to see it live" link(s).
        body = compose_autopilot_card(
            repo=repo,
            tickets=[{"n": issue, "title": title, "goal": goal,
                      "achieved": achieved or "PR zmergnutý, deploy beží"}],
            pr=getattr(args, "pr", None), version=getattr(args, "version", None),
            merge_sha=getattr(args, "merge_sha", None),
            review_ok=(getattr(args, "review", "ok") != "fail"),
            done=None, remaining=remaining, urls=getattr(args, "url", None))
        # Dedup on the REPO-NAME#ISSUE — the stable unit. /autopilot re-dispatches a
        # fresh worker each turn (SendMessage is gated), so the same issue can be
        # carded more than once; keying on repo-name#issue collapses those to one.
        # Use only the repo's last path segment so a bare name ("odoo-erp") and the
        # full "owner/odoo-erp" collapse to one key.
        name = str(repo).rstrip("/").split("/")[-1]
        dedup = getattr(args, "dedup_key", None) or ("%s#%s" % (name, issue))
        # Print the outcome (sent/dedup/dry-run/error) for visibility; harmless in
        # the detached spawn (its stdout is /dev/null).
        status = send(body, dedup_key=dedup, dry_run=getattr(args, "dry_run", False))
        print(status)
        if status == "sent":
            # Feed the statusline github done/total segment — a card that actually
            # went out counts one ticket done in this run (dedup re-sends don't).
            _write_autopilot_progress(name, remaining)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Remote deployment
# ---------------------------------------------------------------------------

# Remote machines that should receive airuleset updates.
# host = the TAILSCALE IP (stable across LAN switches; see #1). Was 10.77.8.134.
REMOTE_HOSTS = [
    {
        "name": "dev2",
        "host": "100.82.64.27",
        "user": "newlevel",
        "repo_path": "~/devel/airuleset",
    },
    {
        # odoo-gatekeeper VPS (prod merge/deploy + hotfix box). Key-based SSH,
        # NOT the shared "newlevel" password — it is a prod-critical host.
        # Migrated 2026-07-07 to Hetzner cx23 "gk.newlevel.media": tailscale
        # IP 100.90.94.41 (node "gatekeeper-cx23", public 88.99.170.148 =
        # gk.newlevel.media). Do NOT use the MagicDNS name "odoo-gatekeeper"
        # — it resolves to a RETIRED node; the previous HostKey box
        # (100.77.52.43 / 202.148.55.31) is retired too.
        "name": "gatekeeper",
        "host": "100.90.94.41",
        "user": "gatekeeper",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # Isolated montalu odoo dev stream — dedicated Linux user on dev1
        # (odoo-erp #1322: no sudo, no prod keys, scoped PAT). Reached over
        # tailscale so the entry works even if push ever runs off-dev1;
        # newlevel@dev1's default key is authorized for this user.
        "name": "montalu@dev1",
        "host": "100.104.8.125",
        "user": "montalu",
        "repo_path": "~/devel/airuleset",
    },
    {
        # Marek's isolated user on the gatekeeper VPS (his Claude dev env).
        # Same access key as the gatekeeper entry (its pubkey is authorized
        # in marek's authorized_keys). Same Hetzner-cx23 host as the
        # gatekeeper entry above (tailscale IP — never the stale MagicDNS name).
        "name": "marek@gatekeeper",
        "host": "100.90.94.41",
        "user": "marek",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # David's isolated external-dev user on the gatekeeper VPS (slovnormal
        # odoo dev stream, parallel to montalu: no sudo, no prod keys, can't
        # read other homes). Same access key as the gatekeeper entry (its
        # pubkey is authorized in david's authorized_keys). Same Hetzner-cx23
        # host (tailscale IP — never the stale MagicDNS name).
        "name": "david@gatekeeper",
        "host": "100.90.94.41",
        "user": "david",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
]


def cmd_push(args):
    """Push to GitHub and deploy to all remote machines.

    Fail-closed: `ruff check .` runs FIRST, then the full test suite — a lint
    error or a single failing test aborts the push (and therefore the dev2
    deploy) so unlinted/untested code never ships. `git push` here is an
    internal subprocess call, so the PreToolUse pre-push-lint.sh hook (which
    only fires for a real Bash `git push` tool invocation) never sees this
    flow — this in-process gate is what actually protects it (issue #7)."""
    import subprocess

    # 0a. Lint the whole repo — fail-closed before any push/deploy. Unlike the
    # PreToolUse hook (which lints only the files a real `git push` command
    # changed), this runs from inside the process itself, so a whole-repo
    # check is the only way to guarantee it; keep it fast by keeping the repo
    # clean (see the ruff cleanup commit for #7 — this is cheap post-cleanup).
    print("Running ruff check (fail-closed before push)...")
    try:
        ruff_result = subprocess.run(
            ["ruff", "check", "."],
            cwd=str(REPO_DIR),
        )
    except FileNotFoundError:
        print("  RUFF NOT INSTALLED — refusing to push unlinted code.", file=sys.stderr)
        print("  Install ruff (e.g. `pip install ruff` / `pipx install ruff`) and retry.",
              file=sys.stderr)
        sys.exit(1)
    if ruff_result.returncode != 0:
        print("  RUFF FAILED — refusing to push unlinted code.", file=sys.stderr)
        sys.exit(1)
    print("  Ruff clean.")

    # 0b. Run the full test suite — fail-closed before any push/deploy.
    print("Running test suite (fail-closed before push)...")
    test_result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(REPO_DIR),
    )
    if test_result.returncode != 0:
        print("  TESTS FAILED — refusing to push untested code.", file=sys.stderr)
        sys.exit(1)
    print("  Tests passed.")

    # 1. Push to GitHub
    print("\nPushing to GitHub...")
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  FAILED: {result.stderr.strip()}")
        sys.exit(1)
    print(f"  {result.stdout.strip() or result.stderr.strip()}")

    # 2. Install locally
    print("\nInstalling locally...")
    cmd_install(args)

    # 3. Deploy to each remote
    for remote in REMOTE_HOSTS:
        print(f"\n{'=' * 50}")
        print(f"Deploying to {remote['name']} ({remote['host']})...")
        remote_cmd = f"cd {remote['repo_path']} && git pull --ff-only && python3 airuleset.py install"
        identity = remote.get("identity")
        if identity:
            # key-based SSH (e.g. the gatekeeper — prod-critical, no shared password)
            ssh_cmd = [
                "ssh", "-i", os.path.expanduser(identity),
                "-o", "StrictHostKeyChecking=no",
                f"{remote['user']}@{remote['host']}",
                remote_cmd,
            ]
        else:
            ssh_cmd = [
                "sshpass", "-p", "newlevel",
                "ssh", "-o", "StrictHostKeyChecking=no",
                f"{remote['user']}@{remote['host']}",
                remote_cmd,
            ]
        ssh_result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if ssh_result.returncode != 0:
            print(f"  FAILED: {ssh_result.stderr.strip()}")
        else:
            print(f"  {ssh_result.stdout.strip()}")
    print("\nAll deployments complete.")


# ---------------------------------------------------------------------------
# api-watchdog — auto-resume Claude Code sessions stalled on an API error
# ---------------------------------------------------------------------------

WATCHDOG_SERVICE_TEMPLATE = REPO_DIR / "settings" / "api-watchdog.service.template"
WATCHDOG_TIMER_TEMPLATE = REPO_DIR / "settings" / "api-watchdog.timer.template"
WATCHDOG_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "api-watchdog.service"
WATCHDOG_TIMER_DEST = Path.home() / ".config" / "systemd" / "user" / "api-watchdog.timer"


def cmd_watchdog(args):
    """One poll cycle: scan `claude` tmux panes, auto-`continue` the ones stalled
    on an API error, ping on stall + give-up + on a session waiting on the user,
    (rate-limited) alert when the weekly token limit nears its cap, and route an
    owner's Discord REPLY back into the session that asked the ❓. Driven by the
    systemd timer."""
    from watchdog import run_once, fetch_usage, fetch_channel_messages
    logs = run_once(dry_run=getattr(args, "dry_run", False), usage_fetch=fetch_usage,
                    discord_fetch=fetch_channel_messages)
    if getattr(args, "verbose", False):
        for line in logs:
            print(line)


# Autopilot authority profiles (issue #16, 2026-07-09). A stream's authority is a
# property of its LINUX USER (streams are separate users by construction: david /
# marek / montalu), resolved at RUNTIME — no per-box state to lose on a home-dir
# migration (the AIRULESET_NOTIFY_OWNER loss pattern), and every push carries the
# map to every managed target. Profiles:
#   full          — merge PR to main + main green + deploy verified (default)
#   branch-merge  — own PR merged into the project INTEGRATION branch (develop)
#                   only; never staging/main promotion, never deploy
#   fork-no-merge — fork branch pushed + local verification green + ready-for-review
#                   hand-off on the issue; never opens/merges a PR, never closes
#                   the issue itself (the maintainer does, at merge)
# A project CLAUDE.md marker `airuleset:authority=<profile>` OVERRIDES the user
# default (checked by the /autopilot skill, not here). Only the user adds markers.
AUTHORITY_PROFILES = ("full", "branch-merge", "fork-no-merge")
AUTHORITY_BY_USER = {
    "david": "fork-no-merge",
    "marek": "branch-merge",
    "montalu": "branch-merge",
}


def _current_user() -> str:
    import getpass

    return getpass.getuser()


def resolve_authority() -> str:
    """Map the current linux user to their autopilot authority profile."""
    return AUTHORITY_BY_USER.get(_current_user(), "full")


def cmd_authority(args):
    """Print the current stream's autopilot authority profile (one word)."""
    profile = resolve_authority()
    print(profile)
    if getattr(args, "explain", False):
        user = _current_user()
        print(f"user={user} (map: {AUTHORITY_BY_USER.get(user, 'unmapped -> full')}); "
              f"a project CLAUDE.md marker airuleset:authority=<profile> overrides this.")


def cmd_upload(args):
    """Stand up a web UPLOAD endpoint the user opens in their own browser.

    The user works over SSH with NO local filesystem access to any managed box —
    receiving a file FROM them is ALWAYS a drag-drop web URL, NEVER an scp/sftp
    ask (modules/core/receive-files-via-upload-url.md; incident david@gk
    2026-07-10). Spawns filedrop/upload_server.py DETACHED with an unguessable
    token, advertises the TAILSCALE IP when available (stable across the user's
    LAN switches — machine-identities), verifies the URL answers 200 BEFORE
    printing it (no-localhost-urls), and self-expires after --ttl seconds."""
    import secrets as _secrets
    import socket
    import subprocess
    import time
    import urllib.request

    dest = Path(getattr(args, "dir", None) or (Path.home() / "uploads")).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    ttl = int(getattr(args, "ttl", None) or 7200)

    # advertise IP: tailscale (stable) > filedrop host_ip fallback
    ip = ""
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0:
            ip = r.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired, IndexError):
        ip = ""
    if not ip:
        from filedrop import host_ip
        ip = host_ip()

    port = int(getattr(args, "port", None) or 0) or None
    if port is None:
        for cand in range(8799, 8820):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", cand)) != 0:
                    port = cand
                    break
        else:
            print("upload: no free port in 8799-8819", file=sys.stderr)
            sys.exit(1)

    token = _secrets.token_urlsafe(12)
    log = Path(f"/tmp/airuleset-upload-{port}.log")
    with open(log, "ab") as lf:
        subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "upload_server.py"),
             token, str(port), ip, str(dest), str(ttl)],
            stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            start_new_session=True)
    url = f"http://{ip}:{port}/{token}/"
    for _ in range(20):  # verify live before presenting (no-localhost-urls)
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                break
        except OSError:
            time.sleep(0.25)
    else:
        print(f"upload: endpoint failed to come up — see {log}", file=sys.stderr)
        sys.exit(1)
    print(url)
    print(f"dest={dest}  ttl={ttl}s  log={log}")
    print("Po nahratí over: grep SAVED " + str(log))


def cmd_fable_gate(args):
    """Budget gate for AUTOMATIC Fable escalation (model-tiering policy 2026-07-03):
    exit 0 + `OPEN ...` when the Fable weekly + shared weekly windows have headroom
    (< threshold, default 80% / AIRULESET_FABLE_GATE_PCT), exit 1 + `CLOSED ...`
    otherwise (incl. missing/stale cache — fail-safe: no blind Fable burn). The
    orchestrator / autopilot supervisor runs this ONCE per hard task/batch before
    dispatching `model: fable`; CLOSED → dispatch opus instead."""
    from watchdog import fable_gate
    ok, reason = fable_gate(threshold=getattr(args, "threshold", None))
    print(("OPEN " if ok else "CLOSED ") + reason)
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# autopilot-lock — cross-session serial-per-repo dispatch lock (issue #8)
# ---------------------------------------------------------------------------


def _autopilot_lock_path(repo):
    """Repo-path-keyed lockfile under the system tempdir. Resolved (realpath)
    so relative paths, symlinks, and a trailing slash all hash to the SAME
    lock — a real cross-session lock must not fork on cosmetic path forms."""
    import hashlib
    import tempfile as _tempfile
    real = str(Path(repo).resolve())
    h = hashlib.sha1(real.encode()).hexdigest()
    return Path(_tempfile.gettempdir()) / f"airuleset-autopilot-{h}.lock"


def _proc_parent_pid(pid):
    """Linux-only /proc read (both managed machines are Linux). Returns None
    off-Linux or on any read failure — callers fall back gracefully."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except Exception:
        return None
    return None


def _proc_comm(pid):
    """Linux-only /proc read of a process's command name (`/proc/<pid>/comm`).
    Returns None off-Linux or on any read failure — callers fall back
    gracefully. Used by `_campaign_pid` to recognize the long-lived `claude`
    (or `node`) process regardless of how many ephemeral shell layers sit
    between it and this process."""
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except Exception:
        return None


_CAMPAIGN_LONG_LIVED_COMMS = {"claude", "node"}
_CAMPAIGN_ANCESTRY_MAX_HOPS = 10


def _campaign_pid():
    """The PID that should stay alive for the WHOLE autopilot campaign (the
    span between an `acquire` call and the LATER, separate `release` call).

    Each Claude Code Bash tool call spawns a fresh ephemeral shell that dies
    the instant that one tool call returns — so os.getppid() alone (this
    process's immediate parent) is USELESS for staleness detection: it would
    already look "dead" moments after `acquire` prints success. The
    long-lived `claude` CLI process itself, which persists for the entire
    session, sits further up the ancestry chain.

    This WALKS the ancestry (by `comm` name, not a fixed hop count) until it
    finds a known long-lived process. A FIXED one-hop walk (the previous
    implementation) is correct only when there is EXACTLY one ephemeral
    shell layer between this process and `claude` — an EXTRA layer (e.g. a
    `bash -c '...'` wrapper invoking this command) makes a fixed-hop walk
    land on ANOTHER ephemeral shell instead of `claude`. That shell dies the
    instant its own tool call returns, so the recorded holder PID looks
    stale almost immediately, and a concurrent `/autopilot` session on the
    same repo can steal the "live" lock — reintroducing the exact #8
    collision this lock exists to prevent. Bounded by
    `_CAMPAIGN_ANCESTRY_MAX_HOPS` as a sanity cap (real ancestry chains are
    a handful of hops); if no long-lived process is ever found, the last
    pid reached is returned (never None/0) — same fail-safe shape as the
    old implementation's `grandparent or ppid`.
    """
    pid = os.getppid()
    seen = set()
    for _ in range(_CAMPAIGN_ANCESTRY_MAX_HOPS):
        if not pid or pid in seen:
            break
        seen.add(pid)
        if _proc_comm(pid) in _CAMPAIGN_LONG_LIVED_COMMS:
            return pid
        parent = _proc_parent_pid(pid)
        if not parent or parent == pid:
            break
        pid = parent
    return pid


def _pid_alive(pid):
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else — still alive
    except Exception:
        return False


def _autopilot_lock_read(lock_path):
    try:
        return json.loads(lock_path.read_text())
    except Exception:
        return {}


def cmd_autopilot_lock(args):
    """Cross-session serial-per-repo dispatch lock for /autopilot (issue #8).

    The "serial per repo" rule (skills/autopilot/SKILL.md,
    two-branch-workflow.md) previously had only SESSION-LOCAL enforcement (a
    supervisor checks its own agent strip) — a SEPARATE `/autopilot` session
    on the same repo has no visibility into that and can dispatch a
    colliding worker onto the same `dev` branch (camera-box #495, and the
    #499/#500-vs-#505 collision).

    `acquire` FAILS (exit 1) when a LIVE holder exists; a DEAD holder's lock
    is stolen (logged) and acquisition proceeds. `release` only removes a
    lock it actually owns (matched by pid) — it never touches someone
    else's lock, and is a no-op success when nothing is locked. `status` is
    a read-only report. The acquire critical section (check-then-write) is
    guarded by a brief `fcntl.flock` on a sibling `.mutex` file so two
    concurrent `acquire` calls on the SAME repo can't both win a
    stale-steal race — the lock's real persistence across the
    acquire/release CLI-invocation gap comes from the recorded holder PID
    staying alive (see `_campaign_pid`), not from the OS-held flock itself
    (which necessarily releases the instant this short-lived CLI process
    exits).
    """
    import fcntl
    from datetime import datetime, timezone

    action = args.action
    repo = args.repo or "."
    lock_path = _autopilot_lock_path(repo)
    holder_pid = args.pid if getattr(args, "pid", None) is not None else _campaign_pid()

    if action == "status":
        if not lock_path.exists():
            print(f"UNLOCKED {lock_path}")
            sys.exit(0)
        holder = _autopilot_lock_read(lock_path)
        alive = _pid_alive(holder.get("pid"))
        state = "LOCKED" if alive else "LOCKED (stale — holder pid dead)"
        print(f"{state} pid={holder.get('pid')} session={holder.get('session', '')} "
              f"since={holder.get('acquired_at', '')} repo={holder.get('repo', '')}")
        sys.exit(0)

    if action == "release":
        if not lock_path.exists():
            print(f"already unlocked: {lock_path}")
            sys.exit(0)
        holder = _autopilot_lock_read(lock_path)
        if holder.get("pid") == holder_pid:
            lock_path.unlink(missing_ok=True)
            print(f"RELEASED {lock_path}")
            sys.exit(0)
        print(f"REFUSING to release — held by a DIFFERENT holder "
              f"(pid={holder.get('pid')}, session={holder.get('session', '')}); "
              f"not releasing a lock this caller does not own.", file=sys.stderr)
        sys.exit(1)

    if action == "acquire":
        payload = {
            "pid": holder_pid,
            "session": args.session or "",
            "repo": str(Path(repo).resolve()),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        mutex_path = str(lock_path) + ".mutex"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        mfd = os.open(mutex_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(mfd, fcntl.LOCK_EX)
            if lock_path.exists():
                holder = _autopilot_lock_read(lock_path)
                if _pid_alive(holder.get("pid")):
                    print(f"BLOCKED: {payload['repo']} already has an active "
                          f"autopilot worker (held by pid={holder.get('pid')}, "
                          f"session={holder.get('session', '')}, "
                          f"since={holder.get('acquired_at', '')}). Serial-per-repo "
                          f"dispatch — wait for it to finish (`autopilot-lock status "
                          f"--repo {repo}`), do NOT dispatch a second worker.",
                          file=sys.stderr)
                    sys.exit(1)
                # Holder's pid is dead — steal it, log the steal.
                steal_log = Path.home() / "devel" / "airuleset" / "audits" / "autopilot-lock-steals.log"
                steal_log.parent.mkdir(parents=True, exist_ok=True)
                with open(steal_log, "a") as f:
                    f.write(f"{datetime.now(timezone.utc).isoformat()}  "
                            f"repo={payload['repo']}  stole from dead "
                            f"pid={holder.get('pid')} session={holder.get('session', '')}\n")
            lock_path.write_text(json.dumps(payload))
            print(f"ACQUIRED {lock_path} pid={holder_pid}")
            sys.exit(0)
        finally:
            fcntl.flock(mfd, fcntl.LOCK_UN)
            os.close(mfd)


def setup_watchdog_service():
    """Install + start the api-watchdog systemd --user timer on THIS machine
    (every host — autopilot runs on dev1 and dev2). Mirrors the file-drop setup:
    write the .service + .timer units, daemon-reload, enable --now the timer."""
    import subprocess
    print("  Installing api-watchdog systemd --user timer")
    for tmpl in (WATCHDOG_SERVICE_TEMPLATE, WATCHDOG_TIMER_TEMPLATE):
        if not tmpl.exists():
            print(f"  ERROR: watchdog unit template missing: {tmpl}", file=sys.stderr)
            return False
    WATCHDOG_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_SERVICE_DEST.write_text(
        WATCHDOG_SERVICE_TEMPLATE.read_text().replace("{{REPO_DIR}}", str(REPO_DIR)))
    WATCHDOG_TIMER_DEST.write_text(WATCHDOG_TIMER_TEMPLATE.read_text())
    print(f"  Wrote unit: {WATCHDOG_TIMER_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "api-watchdog.timer")
    try:
        subprocess.run(["loginctl", "enable-linger", _whoami()],
                       capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"  loginctl enable-linger skipped ({e})", file=sys.stderr)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print(f"  systemctl daemon-reload FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    rc, _o, err = _run_systemctl(["enable", "--now", "api-watchdog.timer"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False
    print("  api-watchdog timer active (polls every 60s).")
    return True


def maybe_setup_watchdog():
    setup_watchdog_service()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        prog="airuleset",
        description="Claude Code configuration management tool",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("install", help="Deploy config to ~/.claude/")
    sub.add_parser("diff", help="Show what install would change")
    sub.add_parser("validate", help="Check all files exist and resolve")
    sub.add_parser("status", help="Show current managed config")
    sub.add_parser("push", help="Push to GitHub + install locally + deploy to all remotes")

    # --- File-Drop: share (give the user a clickable LAN URL) + filedrop (control)
    p_share = sub.add_parser(
        "share", help="Copy a file into the file-drop server and print its LAN URL")
    p_share.add_argument("path", help="Path to the file to serve to the user")

    p_filedrop = sub.add_parser("filedrop", help="File-drop service control")
    p_filedrop.add_argument("filedrop_action", nargs="?", default=None,
                            choices=["status"],
                            help="status (default when no flag)")
    p_filedrop.add_argument("--url", action="store_true",
                            help="Live-check the file-drop server and print its LAN base URL")
    p_filedrop.add_argument("--serve", action="store_true",
                            help="Run the file-drop HTTP server in the foreground (systemd ExecStart)")

    # --- Discord notify: @mention the tmux owner + the autopilot completion card
    p_notify = sub.add_parser(
        "notify", help="Send a Discord notification (@mentions the tmux owner)")
    p_notify.add_argument("--mention-prefix", dest="mention_prefix",
                          action="store_true",
                          help="Print just the '<@id> ' mention prefix for the current tmux owner")
    p_notify.add_argument("--channel-id", dest="channel_id", action="store_true",
                          help="Print the resolved per-owner Discord channel/thread id "
                               "(DISCORD_NOTIFICATION_CHANNEL_<OWNER>, else the shared id)")
    p_notify.add_argument("--owner", dest="owner", action="store_true",
                          help="Print the resolved tmux owner (so a caller can resolve "
                               "once and pass AIRULESET_NOTIFY_OWNER to keep mention+channel in sync)")
    p_notify.add_argument("--mirror-owners", dest="mirror_owners", action="store_true",
                          help="Print the space-separated parallel/CC recipients for the "
                               "current owner (DISCORD_MIRROR_<OWNER>) — the shell send path "
                               "posts a copy to each one's own thread + @mention")
    p_notify.add_argument("--autopilot-done", dest="autopilot_done",
                          action="store_true",
                          help="Compose + send the per-ticket completion card from fields")
    p_notify.add_argument("--run-card", dest="run_card", action="store_true",
                          help="Send a per-ticket card (requires --repo + --issue), "
                               "gathering goal/progress from gh — fired by the "
                               "autopilot worker directly at merge")
    p_notify.add_argument("--api-error", dest="api_error", action="store_true",
                          help="Ping IF --text is a real Claude Code API error "
                               "(used by the notify-api-error.sh Stop hook)")
    p_notify.add_argument("--record-question", dest="record_question",
                          action="store_true",
                          help="Record a ❓ ping's Discord message id → the session "
                               "that asked (for Discord-reply routing); needs "
                               "--message-id --channel --session --cwd")
    p_notify.add_argument("--edit-question", dest="edit_question",
                          action="store_true",
                          help="EDIT the session's recent ❓ ping in place with "
                               "the reworded question from stdin (edits don't "
                               "push-ping); rc 2 = nothing recent to edit")
    p_notify.add_argument("--message-id", dest="message_id",
                          help="Discord message id of the ❓ ping (--record-question)")
    p_notify.add_argument("--channel", help="Discord channel/thread id the ❓ ping "
                                            "was posted to (--record-question)")
    p_notify.add_argument("--cwd", help="Project cwd of the asking session (--record-question)")
    p_notify.add_argument("--text", help="The turn's last assistant message (API-error check)")
    p_notify.add_argument("--session", help="Session id (API-error dedup scope / --record-question)")
    p_notify.add_argument("--project", help="Project name for the API-error ping")
    p_notify.add_argument("--issue", type=int, help="Issue number (for --run-card)")
    p_notify.add_argument("--achieved", help="What landed (card 'Dosiahnuté') — plain language")
    p_notify.add_argument("--goal", help="Plain-language ticket goal (card 'Cieľ') — "
                                         "simple/understandable, NOT the technical issue title")
    p_notify.add_argument("--body", help="Arbitrary markdown body to send")
    p_notify.add_argument("--repo", help="owner/name (autopilot card)")
    p_notify.add_argument("--pr", help="PR URL → clickable 'kód (PR)' link on the card")
    p_notify.add_argument("--url", action="append",
                          help="'Where to see it live' link for the card — a bare URL "
                               "or 'Label=URL' (e.g. 'Prod=https://…'); repeatable")
    p_notify.add_argument("--merge-sha", dest="merge_sha", help="Merge commit SHA")
    p_notify.add_argument("--version", help="Deployed version read from the DOM")
    p_notify.add_argument("--review", choices=["ok", "fail"], default="ok",
                          help="Double-review verdict (default ok)")
    p_notify.add_argument("--done", help="Tickets completed so far this run")
    p_notify.add_argument("--remaining", help="Open non-skip issues still to do")
    p_notify.add_argument("--tickets-json", dest="tickets_json",
                          help='JSON: [{"n":41,"title":..,"goal":..,"achieved":..}]')
    p_notify.add_argument("--dedup-key", dest="dedup_key",
                          help="Dedup key (default repo#pr) — same key sends once")
    p_notify.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="Print the composed message instead of sending")

    p_watchdog = sub.add_parser(
        "watchdog", help="Detect Claude Code sessions stalled on an API error and "
                         "auto-resume them (tmux `continue`) — run by a systemd timer")
    p_watchdog.add_argument("--once", action="store_true",
                            help="Run one poll cycle and exit (the systemd-timer mode)")
    p_watchdog.add_argument("--dry-run", dest="dry_run", action="store_true",
                            help="Detect + log, but do NOT send `continue` or ping")
    p_watchdog.add_argument("--verbose", action="store_true",
                            help="Print the actions taken this cycle")

    p_tickets = sub.add_parser(
        "tickets-status",
        help="Statusline github-tickets segment — autopilot done/total or open issues")
    p_tickets.add_argument("--cwd", help="Session cwd (defaults to $PWD)")
    p_tickets.add_argument("--refresh", action="store_true",
                           help="Slow path: refresh the per-repo cache via git+gh "
                                "(run detached by the statusline, never inline)")

    p_gate = sub.add_parser(
        "fable-gate", help="Budget gate for automatic Fable escalation — exit 0 "
                           "(OPEN, dispatch fable) / 1 (CLOSED, dispatch opus)")
    p_gate.add_argument("--threshold", type=int, default=None,
                        help="Gate percent (default 80 / AIRULESET_FABLE_GATE_PCT)")

    p_up = sub.add_parser(
        "upload",
        help="Web upload URL for receiving a file FROM the user (never ask for scp)")
    p_up.add_argument("--dir", default=None, help="Destination dir (default ~/uploads)")
    p_up.add_argument("--ttl", type=int, default=7200,
                      help="Endpoint self-shutdown after N seconds (default 7200)")
    p_up.add_argument("--port", type=int, default=None,
                      help="Port (default: first free in 8799-8819)")

    p_auth = sub.add_parser(
        "authority",
        help="Print this stream's autopilot authority profile "
             "(full / branch-merge / fork-no-merge)")
    p_auth.add_argument("--explain", action="store_true",
                        help="Also print how the profile was resolved")

    p_lock = sub.add_parser(
        "autopilot-lock",
        help="Cross-session serial-per-repo dispatch lock for /autopilot")
    p_lock.add_argument("action", choices=["acquire", "release", "status"],
                        help="acquire (fails if a LIVE holder exists), "
                             "release (only removes a lock this caller owns), "
                             "or status (read-only report)")
    p_lock.add_argument("--repo", default=".", help="Repo path to lock (default: cwd)")
    p_lock.add_argument("--session", default="",
                        help="Free-text session id recorded for display only "
                             "(matching for release/steal is by pid, not this)")
    p_lock.add_argument("--pid", type=int, default=None,
                        help="Override the recorded/compared holder pid "
                             "(default: auto-detect the long-lived campaign process)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands[args.command](args)


# Command dispatch table (module-level so tests can assert registration).
SUBCOMMANDS = {
    "install": cmd_install,
    "diff": cmd_diff,
    "validate": cmd_validate,
    "status": cmd_status,
    "push": cmd_push,
    "share": cmd_share,
    "filedrop": cmd_filedrop,
    "notify": cmd_notify,
    "watchdog": cmd_watchdog,
    "fable-gate": cmd_fable_gate,
    "authority": cmd_authority,
    "upload": cmd_upload,
    "tickets-status": cmd_tickets_status,
    "autopilot-lock": cmd_autopilot_lock,
}
# Backwards-compatible alias used by main() before SUBCOMMANDS existed.
commands = SUBCOMMANDS


if __name__ == "__main__":
    main()
