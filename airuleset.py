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
# Claude Code's native "User"-scope path-scoped-rules directory (#40):
# confirmed against the installed CC binary that the User rules dir is
# join(<user config base>, "rules") -- the same base as the well-known User
# CLAUDE.md (join(<user config base>, "CLAUDE.md") == ~/.claude/CLAUDE.md).
RULES_DIR = CLAUDE_DIR / "rules"

MANAGED_HEADER = "# Managed by airuleset"
MANAGED_MARKER = "<!-- airuleset-managed -->"

# Externally-managed CLAUDE.md blocks to PRESERVE across regeneration. airuleset
# fully regenerates ~/.claude/CLAUDE.md from the profile; that would otherwise wipe
# a delimited block another tool injects. CodeGraph (`codegraph install`) appends
# its guidance block here — preserve it so a `push` doesn't silently delete it.
EXTERNAL_BLOCK_MARKERS = [("<!-- CODEGRAPH_START -->", "<!-- CODEGRAPH_END -->")]

# Managed default effort: `high` is the persistent default the user wants in
# EVERY managed project so they never have to remember to set it (#56,
# 2026-07-25). Official Anthropic docs for the Claude 5 family (Opus 5, Fable
# 5) both say "start with `high`, the default" and explicitly warn against
# reusing an effort setting carried over from an earlier model — this was
# previously `xhigh`, set in the Opus 4.7/4.8 era ("start with xhigh for
# coding and agentic use cases"), exactly the carried-over case the docs now
# flag. `xhigh` stays reserved for demanding coding/agentic work (dispatched
# via `effort:` on a specific agent/task, e.g. the autopilot-worker, or a
# gated HARD-task escalation) — never this blanket MAIN-session default.
# `max`/`ultracode` are session-only (not valid here) — ultracode adds
# auto-workflow orchestration on top and stays a per-session `/effort
# ultracode`. The user can still raise/lower per session with `/effort`.
MANAGED_EFFORT_LEVEL = "high"

# Managed default MAIN-session model (2026-07-25 cost-fix package, #37):
# **Opus 5** is now the default main + judgment tier (model-awareness.md) —
# Opus 4.8's regression + Sonnet 5's coordinator gap that made Fable-as-main
# a deliberate WORKAROUND are both gone now that Opus 5 shipped, so managed
# boxes should default MAIN to Opus 5 instead of whatever a prior session
# left in settings.json. Measured over 8 days across the 6 managed boxes:
# Fable 5 accounted for 76% of all token spend ($10,350 of ~$13,600) —
# gatekeeper $2,115, dev2 $2,027, montalu $1,392 — largely automated streams
# still defaulting to Fable as MAIN rather than the gated advisor shape.
# The `[1m]` suffix is a DELIBERATE part of the id, not a typo: it is how
# Claude Code's own usage tracking keys the 1M-context variant (verified —
# `lastModelUsage` entries in ~/.claude.json store ids exactly like
# `claude-opus-4-8[1m]`, distinct from the bare `claude-opus-4-8` key) — kept
# so this change does NOT also shrink the context window. The user relies on
# the 1M window to avoid context-loss regressions; whether to reconsider that
# is a SEPARATE decision for a later step, not bundled into this one.
MANAGED_MODEL = "claude-opus-5[1m]"

# REVERTED (2026-07-25 correction batch, same day it was added): a managed
# `MANAGED_AUTOCOMPACT_WINDOW = 300000` ("krok 1c") briefly capped the
# auto-compact threshold. The user's call, which overrides that decision:
# a LOW auto-compact threshold cuts big tasks off MID-WORK and defeats the
# entire point of the 1M context window — compaction should never fire on
# an artificial token budget. Context is bounded at SAFE BOUNDARIES instead
# — the per-ticket `✅ DONE` completion report + the ticket-boundary
# `/compact` (watchdog job 14 — see `notify-compact-request.sh` and
# `milestone-notifications.md`) for autopilot-style sessions, AND, for a
# long-lived session that never reports a ticket, an IDLE-based backstop
# (watchdog job 15, #39/#43 follow-up: a session whose context exceeds
# 400K tokens AND has sat genuinely idle >= 20 minutes — no draft, no
# worker in flight — gets `/compact`'d automatically) — never by a blanket
# token window that could fire mid-work. No replacement constant:
# `apply_managed_settings_defaults` now actively STRIPS `autoCompactWindow`
# from settings.json on every deploy so the 6 managed boxes go back to
# Claude Code's own default.

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
                          host_ip as filedrop_host_ip, bind_ips as filedrop_bind_ips,
                          filedrop_url, FILEDROP_DIR)
except Exception:  # pragma: no cover — filedrop package should always import
    FILEDROP_PORT = int(os.environ.get("FILEDROP_PORT", "8788"))
    FILEDROP_DEFAULT_PORT = 8788
    FILEDROP_PORT_FILE = CLAUDE_DIR / "filedrop.port"
    FILEDROP_DIR = CLAUDE_DIR / "filedrop"

    def filedrop_persisted_port():
        return None

    def filedrop_host_ip():
        return os.environ.get("FILEDROP_HOST", "127.0.0.1")

    def filedrop_bind_ips():
        return [filedrop_host_ip()]

    def filedrop_url():
        return f"http://{filedrop_host_ip()}:{FILEDROP_PORT}/"

FILEDROP_SERVICE_TEMPLATE = REPO_DIR / "settings" / "filedrop.service.template"
FILEDROP_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "filedrop.service"


# Skills directories in the repo that should be symlinked
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate", "architecture-check", "autopilot", "autopilot-dialog", "mutation-sweep", "meeting-analysis", "playbook-review", "playbook-cleanup", "mutation-testing", "local-builds", "batch-issue-development", "view-image-urls", "version-on-dashboard", "process-subdev", "autopilot-master", "fable-advisor",
               # Ruleset trim wave 2 (#37, 2026-07-25) — situational always-on
               # modules moved VERBATIM to hidden (user-invocable: false)
               # on-demand skills. See test_ruleset_conversion_wave2.py.
               "subagent-type-discipline", "verify-issue-still-valid", "investigate-existing-first",
               "post-deploy-verification", "regression-test-first", "ci-push-discipline",
               "comprehensive-logging", "verify-launched-work-liveness", "pr-merge-policy",
               "deliver-files-as-urls", "notification-mechanics"]

# --- Per-box skill scoping (user complaint 2026-07-11: "slash cmd by nemali byt
# vsetky vsade ale len relevantne k danemu projektu") ---
# Skills deploy per USER at install time; every user-invocable skill shows in that
# box's slash-command list, so an irrelevant skill is pure noise there. Two scopes:
#   MAINTAINER_ONLY — relevant only on the airuleset-maintainer's own boxes
#     (newlevel@dev1/dev2): airuleset self-maintenance (mdreview, rules-audit),
#     his personal workflows (meeting-analysis), his projects' tooling
#     (windows-remote-gui = win-* MCP rigs, fast-iterate + mutation-sweep = his
#     Rust/mutation-era repos). Sub-dev / gatekeeper boxes never invoke these.
#   FULL_AUTHORITY_ONLY — deploys are OUTSIDE a reduced-authority stream's job
#     (pr-merge-policy scope), so deploy-ssh stays off david/marek/montalu boxes.
# Hidden on-demand skills (user-invocable: false — mutation-testing, local-builds,
# batch-issue-development, view-image-urls, version-on-dashboard) deploy EVERYWHERE:
# rule stubs point at them and they never appear in the slash list, so they cost
# nothing. install prunes previously-linked skills that fall outside the box's set.
SKILLS_MAINTAINER_ONLY = {"mdreview", "rules-audit", "meeting-analysis",
                          "mutation-sweep", "windows-remote-gui", "fast-iterate"}
SKILLS_FULL_AUTHORITY_ONLY = {"deploy-ssh", "process-subdev", "autopilot-master"}
MAINTAINER_USERS = {"newlevel"}
# Per-user re-grants: a scoped-away skill that IS relevant on one specific box
# (montalu meeting recordings get analyzed IN that stream's session — the
# 2026-07-14 incident where the scoping prune took /meeting-analysis off montalu).
SKILLS_EXTRA_BY_USER = {"montalu": {"meeting-analysis"}}


def skill_names_for_user(user=None):
    """The skill set THIS box's user should have installed (see scoping above)."""
    import getpass
    user = user or getpass.getuser()
    extra = SKILLS_EXTRA_BY_USER.get(user, set())
    names = list(SKILL_NAMES)
    if user not in MAINTAINER_USERS:
        names = [n for n in names if n not in SKILLS_MAINTAINER_ONLY or n in extra]
    if AUTHORITY_BY_USER.get(user, "full") != "full":
        names = [n for n in names if n not in SKILLS_FULL_AUTHORITY_ONLY or n in extra]
    return names

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
# Plugins explicitly DISABLED by managed policy (#39 item 3, 2026-07-25
# /doctor findings): rust-analyzer-lsp + claude-md-management had 0 lifetime
# uses on dev2 and `/doctor` disabled them directly in settings.json
# (backup: settings.json.bak-doctor). The plugin reconcile below only ever
# ENABLES MANAGED_PLUGINS and otherwise merges the existing enabledPlugins
# dict untouched, so these disables already survive a normal push — this
# list makes the intent EXPLICIT and durable (and applies it on every box,
# not just dev2) so a future change to the reconcile logic can never
# silently resurrect them.
MANAGED_DISABLED_PLUGINS = (
    "rust-analyzer-lsp@claude-plugins-official",
    "claude-md-management@claude-plugins-official",
)
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
    q = statusbar.questions_segment(cwd)   # unanswered-❓ badge (this project · inde)
    if q:
        segs.append(q)
    # --- session context/cost: 'ctx 570K · ~$0.57/tah' (2026-07-25, #37) ---
    cc = statusbar.context_cost_segment(d)
    if cc:
        segs.append(cc)
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
# The managed claude launcher (#77, 2026-07-26): a shell FUNCTION in ~/.bashrc
# is parsed ONCE at shell startup and then stays frozen in that shell's memory
# FOREVER. Panel shells are long-lived (tmux panes running for days), so any
# logic baked directly into the .bashrc function (flags, model pin, ultracode)
# kept resurrecting on every relaunch of an ALREADY-RUNNING stale shell, no
# matter how many times `push` rewrote .bashrc -- rewriting the file has zero
# effect on a shell that already parsed the old function into memory. Measured
# live: two sessions launched HOURS after #53 (which correctly made ultracode
# opt-in ON DISK) still carried the pre-#53 default, because the panel shells
# hosting them predated the fix.
#
# Fix: .bashrc holds ONLY thin one-line wrapper functions with NO flag
# literals -- each just execs the managed SCRIPT (CLAUDE_LAUNCH_SCRIPT_DEST),
# which carries ALL the actual logic (continue-or-new, --model, skip-perms,
# ultracode only for the `ultracode` mode). A script is read fresh from disk
# on EVERY invocation, so a `push` that rewrites the script changes behavior
# in every already-running shell IMMEDIATELY -- no `source ~/.bashrc`, no
# relaunch, no restart. Same shape as the caveman stable statusline shim
# (render_caveman_shim() below) -- read that first before changing this.
CLAUDE_LAUNCH_SCRIPT_DEST = CLAUDE_DIR / "airuleset-claude-launch.sh"
# --- the script content itself -----------------------------------------------
# Ultracode is OPT-IN (#53, 2026-07-25): `--settings '{"ultracode":true}'` used
# to be baked into EVERY default launch, so ultracode mode silently came back
# on every session restart even after the user had turned it off for that
# session (found repeatedly on restreamer). Only the `ultracode` mode carries
# it now -- the `claude-ultracode()` bashrc function is the explicit opt-in
# escape hatch, carrying EXACTLY today's old default behavior.
#   --settings '{"ultracode":true}' : ultracode is SESSION-ONLY (never on disk, NOT
#       accepted in settings.json — GH #64817); --settings is the only doc-blessed
#       always-on route and MERGES per-key, so hooks/model/effortLevel stay intact.
#       Only the `ultracode` mode passes this now.
#   --dangerously-skip-permissions  : auto-approve (the user opted in for their dev boxes).
#   -c                              : continue the most recent conversation in the cwd.
#   --model '{{MANAGED_MODEL}}'     : baked in at RENDER time so EVERY mode except
#       `plain` — including a RESUMED (-c) session — explicitly requests the managed
#       model. Proven live on gatekeeper: settings.json said `claude-opus-5[1m]`, but a
#       resumed session's transcript kept showing `claude-opus-4-8` on every turn — `-c`
#       alone just continues whatever model the prior transcript was started with; only
#       an explicit --model on the launch command line forces it.
# The conversation probe globs ~/.claude/projects/<encoded-cwd>/*.jsonl — Claude Code
# encodes cwd by turning / . _ into dashes; a project dir holding only memory/ (no
# transcript) means nothing to continue. Unknown encoding chars fail toward the
# FRESH branch (worse case: a new session instead of a cryptic error).
# Modes: `default` (claude — continue-or-new, skip-perms, model, NO ultracode),
# `new` (claude-new — always FRESH, skip-perms, model, NO ultracode — force a
# clean start), `ultracode` (claude-ultracode — deliberate opt-in: continue-or-new
# + skip-perms + ultracode + model), `plain` (claude-plain — vanilla, no flags).
CLAUDE_LAUNCH_SCRIPT_CONTENT = r"""#!/usr/bin/env bash
# airuleset-managed (do NOT edit) — the claude launcher (#77). Read FRESH from
# disk on EVERY invocation (unlike a ~/.bashrc function, which is parsed once
# at shell startup and then stays frozen in that shell's memory forever), so a
# `push` that rewrites this file changes launch behavior immediately in every
# already-running shell — no `source ~/.bashrc`, no relaunch, no restart.
set -euo pipefail

mode="${1:-default}"
if [ "$#" -gt 0 ]; then shift; fi

# claude installs to ~/.local/bin, which NON-LOGIN interactive shells (su
# without -, tmux with a default-command, IDE terminals) never get — only
# ~/.profile adds it, and only login shells read that (montalu@dev1
# "claude: command not found", 2026-07-04).
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac

_has_conversation() {
  local ccdir="${PWD//\//-}"; ccdir="${ccdir//./-}"; ccdir="${ccdir//_/-}"
  compgen -G "$HOME/.claude/projects/$ccdir/*.jsonl" >/dev/null 2>&1
}

case "$mode" in
  plain)
    exec claude "$@"
    ;;
  new)
    exec claude --dangerously-skip-permissions --model '{{MANAGED_MODEL}}' "$@"
    ;;
  ultracode)
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c \
        --settings '{"ultracode":true}' --model '{{MANAGED_MODEL}}' "$@"
    else
      exec claude --dangerously-skip-permissions \
        --settings '{"ultracode":true}' --model '{{MANAGED_MODEL}}' "$@"
    fi
    ;;
  *)
    if _has_conversation; then
      exec claude --dangerously-skip-permissions -c --model '{{MANAGED_MODEL}}' "$@"
    else
      exec claude --dangerously-skip-permissions --model '{{MANAGED_MODEL}}' "$@"
    fi
    ;;
esac
"""


def render_claude_launch_script():
    """The launch-script content with the managed model substituted in — the
    write site MUST use this, never the raw constant (same discipline as
    render_caveman_shim())."""
    return CLAUDE_LAUNCH_SCRIPT_CONTENT.replace("{{MANAGED_MODEL}}", MANAGED_MODEL)


# .bashrc holds ONLY thin one-line functions -- no flag literal survives here,
# so nothing flag-shaped can ever be frozen in a shell's memory again.
ULTRACODE_BASHRC_BLOCK = (
    f"{ULTRACODE_MARK_START}\n"
    f'claude() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" default "$@"; }}\n'
    f'claude-new() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" new "$@"; }}\n'
    f'claude-ultracode() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" ultracode "$@"; }}\n'
    f'claude-plain() {{ "$HOME/.claude/{CLAUDE_LAUNCH_SCRIPT_DEST.name}" plain "$@"; }}\n'
    f"{ULTRACODE_MARK_END}"
)


def apply_ultracode_launcher(bashrc_path: Path = None, script_path: Path = None) -> bool:
    """Install/refresh the managed claude launcher (#77).

    The SCRIPT (script_path, default CLAUDE_LAUNCH_SCRIPT_DEST) is written and
    chmod +x UNCONDITIONALLY on every call — like the caveman shim, it must
    self-heal any tampering/rollback, and a missing script after write is a
    loud RuntimeError, never a silent loss of `claude`. It carries ALL the
    actual logic, so a `push` changes launch behavior in every already-running
    shell immediately, with no `source ~/.bashrc` and no restart.

    The ~/.bashrc block is idempotent (replaces the marked block if present,
    else appends it) and holds ONLY thin wrapper functions with no flag
    literals. Returns True iff the ~/.bashrc file changed."""
    import re
    bpath = bashrc_path or BASHRC
    spath = script_path or CLAUDE_LAUNCH_SCRIPT_DEST

    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(render_claude_launch_script())
    os.chmod(str(spath), 0o755)
    if not spath.exists():
        raise RuntimeError(f"claude launcher script missing right after write: {spath}")

    existing = bpath.read_text() if bpath.exists() else ""
    if ULTRACODE_MARK_START in existing and ULTRACODE_MARK_END in existing:
        pattern = re.compile(
            re.escape(ULTRACODE_MARK_START) + r".*?" + re.escape(ULTRACODE_MARK_END),
            re.S)
        new = pattern.sub(lambda _m: ULTRACODE_BASHRC_BLOCK, existing)
    else:
        sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
        new = f"{existing}{sep}\n{ULTRACODE_BASHRC_BLOCK}\n"
    if new != existing:
        bpath.write_text(new)
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

    - `model = MANAGED_MODEL` (Opus 5[1m]) is the default MAIN-session model on
      every managed box (2026-07-25 cost-fix package, #37) — see MANAGED_MODEL's
      own comment for the measured evidence. Same unconditional-managed-default
      treatment as effortLevel/disableAgentView/tui; the user can still switch
      per session with `/model`.

    - `autoCompactWindow` is ACTIVELY STRIPPED (2026-07-25 correction batch —
      reverts the SAME-DAY "krok 1c" addition). A low auto-compact threshold
      cuts big tasks off mid-work and defeats the 1M context window; context
      is bounded at ticket boundaries instead (the per-ticket `/compact`,
      watchdog job 14). This must POP the key, not merely stop setting it —
      an already-deployed settings.json from the reverted feature would
      otherwise keep carrying it forward untouched on every future install.

    Idempotent; preserves all other keys."""
    result = dict(settings)
    result["effortLevel"] = MANAGED_EFFORT_LEVEL
    result["disableAgentView"] = True
    result["tui"] = "default"
    result["model"] = MANAGED_MODEL
    result.pop("autoCompactWindow", None)
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
        if "{{HOST_IPS}}" not in tmpl:
            errors.append("File-drop service template missing {{HOST_IPS}} placeholder")
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
    modules, global_rules = categorize_entries(parse_profile(UNIVERSAL_PROFILE))
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

    # Skills diff (this box's set — scoped per skill_names_for_user)
    print("\n=== ~/.claude/skills/ (symlinks) ===")
    for skill in skill_names_for_user():
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

    # Rules diff (global path-scoped rules symlinked into ~/.claude/rules/)
    print("\n=== ~/.claude/rules/ (symlinks) ===")
    for entry in global_rules:
        name = Path(entry).name
        target = REPO_DIR / entry
        link = RULES_DIR / name
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


# Binaries the deployed surface depends on at RUNTIME: jq + curl (every
# notify/stop hook parses its stdin payload with jq and sends via curl), git +
# gh (tickets-status, autopilot, bounce), tmux (watchdog pane jobs), sshpass
# (the burn-metrics remote-ssh helpers, #98 — was used by the code but never
# tracked here, so a box missing it was never auto-installed or warned about).
# A box missing one degrades SILENTLY at hook time — subdev 2026-07-23: provisioned
# without jq, so david's ❓ never pinged Discord, never entered the question
# map and the statusline badge stayed empty. The check AUTO-INSTALLS the gap
# (user directive 2026-07-24: 'ak ti nieco chyba mas to doinstalovat') and
# re-verifies; only a box where the install itself fails (no sudo — the
# isolated sub-dev users) keeps the LOUD warning in every install/push output
# (per-machine gaps are invisible to git-deploy — the gatekeeper .env lesson).
# A sudo-less box that hits a STILL-missing dep files a gk-request naming the
# package (autonomous-verification.md's sudo-less branch, #98); fulfilling it
# means adding the package HERE and running push, which installs + verifies
# it on every target in one shot.
# Push runs install on EVERY target, so every deploy verifies + heals the
# whole fleet's toolset.
RUNTIME_DEPS = ("jq", "curl", "git", "gh", "tmux", "sshpass", "btop")


def check_runtime_deps(deps=RUNTIME_DEPS):
    """Auto-install each missing runtime binary (sudo -n apt-get, non-
    interactive) and re-verify; a failed install (no sudo) prints the LOUD
    warning instead. Returns the still-missing list (never fatal — install
    proceeds, the gap stays visible)."""
    import shutil
    import subprocess
    still = []
    for d in deps:
        if shutil.which(d):
            continue
        try:
            r = subprocess.run(["sudo", "-n", "apt-get", "install", "-y", d],
                               capture_output=True, text=True, timeout=300)
            ok = r.returncode == 0 and shutil.which(d)
        except Exception:
            ok = False
        if ok:
            print("  ✓ runtime dep '%s' was missing — auto-installed "
                  "(apt-get) and verified." % d)
        else:
            still.append(d)
            print("  ⚠ MISSING RUNTIME DEP: '%s' is not installed on this box "
                  "and auto-install failed (no sudo?) — hooks/notify/watchdog "
                  "will degrade SILENTLY. Install it as root: apt-get install "
                  "%s." % (d, d))
    return still


def cmd_install(args):
    """Deploy config: generate CLAUDE.md, symlink skills, merge hooks."""
    print("airuleset install")
    print("=" * 50)
    check_runtime_deps()

    # Ensure ~/.claude/ exists
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1. Generate ~/.claude/CLAUDE.md ---
    modules, global_rules = categorize_entries(parse_profile(UNIVERSAL_PROFILE))
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

    # --- 2. Symlink skills (per-box set — see skill_names_for_user) ---
    box_skills = skill_names_for_user()
    for skill in box_skills:
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

    # --- 2a. Prune managed skills OUTSIDE this box's set (a maintainer-only /
    # full-authority-only skill previously linked here shows as slash-command noise
    # on this box). Only OUR symlinks pointing into this repo's skills/ are removed —
    # foreign or hand-made skills are never touched (skill-ownership rule).
    for skill in SKILL_NAMES:
        if skill in box_skills:
            continue
        link = SKILLS_DIR / skill
        if link.is_symlink():
            try:
                target = Path(os.readlink(link))
            except OSError:
                continue
            if str(target).startswith(str(REPO_DIR / "skills")):
                link.unlink()
                print(f"  Pruned:    {skill} (not relevant on this box)")

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

    # --- 2c. Symlink global path-scoped rules (rules/*.md referenced by the
    # universal profile) into ~/.claude/rules/ -- Claude Code's native
    # User-scope path-scoped-rules directory. #40: these were parsed by
    # categorize_entries() but previously discarded, never installed anywhere.
    for line in symlink_global_rules(global_rules, CLAUDE_DIR, REPO_DIR):
        print(line)

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

    # --- 3b. claude launcher: managed script + thin ~/.bashrc wrappers (#77) ---
    # The SCRIPT (CLAUDE_LAUNCH_SCRIPT_DEST) is rewritten every install/push and
    # takes effect in every already-running shell IMMEDIATELY -- no `source
    # ~/.bashrc`, no relaunch, no restart (a bashrc FUNCTION, by contrast, is
    # frozen in a shell's memory at startup forever, which is exactly how
    # ultracode kept resurrecting after #53). Ultracode can't live in
    # settings.json (session-only, GH #64817) — only the `ultracode` mode
    # (claude-ultracode) passes it, deliberate opt-in only (#53). effortLevel
    # above is the persistent fallback for reasoning depth regardless of mode.
    try:
        changed = apply_ultracode_launcher()
        print(f"  Updated:   {CLAUDE_LAUNCH_SCRIPT_DEST} (claude launcher script — "
              f"takes effect immediately, no restart needed)")
        if changed:
            print(f"  Updated:   {BASHRC} (claude launcher wrappers)")
        else:
            print(f"  No change: {BASHRC} (claude launcher wrappers)")
    except Exception as e:
        print(f"  claude launcher error: {e}", file=sys.stderr)

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

    # --- Skills (this box's set — scoped per skill_names_for_user) ---
    print("\n~/.claude/skills/:")
    for skill in skill_names_for_user():
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

    {{REPO_DIR}} -> this checkout's path (ExecStart). {{HOST_IP}} -> the primary
    (tailscale-first) IP, for status/URL. {{HOST_IPS}} -> the comma list of ALL
    private IPs to bind (tailscale + LAN — filedrop.bind_ips()), so the server
    answers on every interface the user might be on. Both are computed HERE
    (unsandboxed, so `hostname -I` / `tailscale ip` work) and baked into the
    Environment so the sandboxed server never needs AF_NETLINK to discover its own
    address. {{PORT}} -> the per-user port chosen by _choose_filedrop_port (a
    second airuleset user on the same host cannot reuse the first user's :8788)."""
    return (FILEDROP_SERVICE_TEMPLATE.read_text()
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{HOST_IPS}}", ",".join(filedrop_bind_ips()))
            .replace("{{HOST_IP}}", filedrop_host_ip())
            .replace("{{PORT}}", str(port if port is not None else FILEDROP_PORT)))


def _choose_filedrop_port(bind_ip):
    """The port this user's file-drop should serve on.

    Two airuleset users on ONE host (montalu@dev1, marek@gatekeeper) collide on
    the default :8788 — the second user's service restart-loops on Errno 98
    (observed on montalu@dev1, 2026-07-04). Precedence:
      1. FILEDROP_PORT env — explicit override, never second-guessed.
      2. A previously PERSISTED choice (~/.claude/filedrop.port) — kept when our
         own service actively serves it OR it still bind-tests free on this
         host; a port carried over by a ~/.claude migration that a DIFFERENT
         user's file-drop holds here is dropped and re-picked (montalu@subdev
         inherited dev1's 8789 == marek's subdev port, #33, 2026-07-24).
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
    rc, out, _err = _run_systemctl(["is-active", "filedrop.service"])
    our_active = rc == 0 and out.strip() == "active"
    if persisted:
        if our_active:
            return persisted        # our own live instance serves it
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            s.bind((bind_ip, persisted))
        except OSError:
            # a ~/.claude migrated from another box carries THAT box's port —
            # here it can be a DIFFERENT user's live file-drop (montalu@subdev
            # inherited dev1's 8789 == marek's subdev port; #33, 2026-07-24).
            # Stale → drop the file and fall through to the probe re-pick.
            print(f"  persisted file-drop port {persisted} is held by another "
                  f"instance on this host (not ours) — dropping "
                  f"{FILEDROP_PORT_FILE} and re-picking")
            FILEDROP_PORT_FILE.unlink(missing_ok=True)
        else:
            return persisted        # still free on THIS host
        finally:
            s.close()
    if our_active:
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


def _current_remote_host_entry():
    """Best-effort match of "the box currently running install" to its own
    REMOTE_HOSTS deploy-target entry (#151) -- keyed on the LOCAL system
    username via the existing `_whoami()` helper. Usernames are unique across
    every REMOTE_HOSTS entry today (newlevel/gatekeeper/montalu/marek/david/
    simap), so this needs no new per-entry data and can't confuse the four
    subdev-VPS users (montalu/marek/david/simap), which all share the SAME
    physical hostname and would be indistinguishable by a hostname-only match.

    Returns None when no entry's `user` matches -- expected on dev1 itself
    (the deploy SOURCE, never listed in REMOTE_HOSTS, and always the primary
    already-configured host in practice, so it essentially never reaches the
    caller's warning branch). Known edge case: dev1's local user is also
    `newlevel`, same as the `dev2` entry -- a mismatch there is harmless
    since dev2 pins no identity either."""
    me = _whoami()
    if not me:
        return None
    for entry in REMOTE_HOSTS:
        if entry.get("user") == me:
            return entry
    return None


def check_discord_notify_config():
    """Report whether Discord notifications are wired on THIS host (no secrets printed).

    The Discord `.env` (bot token + per-owner channels/mentions) is LOCAL and NOT
    git-deployed — `install` cannot carry it. A host that never got it wired sends
    NOTHING: every notify call fail-safes to a silent no-op. That is exactly how the
    gatekeeper box went dark (the `.env` was never wired when it was added). This
    check makes the gap LOUD at install time instead of a silent failure discovered
    weeks later. It NEVER prints the token value — only presence.

    The "wire it from an already-configured host" ssh one-liner (#151) is built
    from THIS box's own REMOTE_HOSTS entry (`_current_remote_host_entry()`) so it
    carries a pinned `-i <identity>` when one exists -- e.g. simap/marek/david on
    subdev all pin `~/.secrets/gatekeeper_access_ed25519`. A wrong-key ssh attempt
    against subdev trips its fail2ban and bans dev1 on every interface (tailscale
    included) for an hour, so a bare host-agnostic hint there is not cosmetic.
    When no REMOTE_HOSTS entry matches this box, the old `<this-host>` placeholder
    is kept unchanged rather than guessing."""
    env = CLAUDE_DIR / "channels" / "discord" / ".env"
    print("  Checking Discord notify config")
    if not env.is_file():
        print("    ⚠ Discord notify DISABLED — no ~/.claude/channels/discord/.env on this host.")
        print("      Pings (❓/✅, api-error, autopilot cards) will silently NOT send.")
        print("      Wire it from an already-configured host (secrets stay local, not git):")
        entry = _current_remote_host_entry()
        target = f"{entry['user']}@{entry['host']}" if entry else "<this-host>"
        identity = entry.get("identity") if entry else None
        id_flag = f"-i {identity} " if identity else ""
        print(f"        cat ~/.claude/channels/discord/.env | ssh {id_flag}{target} \\")
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
    enabled, and every MANAGED_DISABLED_PLUGINS key forced off (#39 item 3).
    Every other key preserved untouched; idempotent."""
    result = dict(settings)
    enabled = dict(result.get("enabledPlugins", {}))
    for key in MANAGED_PLUGINS:
        enabled[key] = True
    for key in MANAGED_DISABLED_PLUGINS:
        enabled[key] = False
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
    hosts_env = os.environ.get("FILEDROP_HOSTS", "").strip()
    hosts = [h for h in hosts_env.split(",") if h] or None
    run_server(host=filedrop_host_ip(), port=FILEDROP_PORT, hosts=hosts)


def cmd_share(args):
    """Copy a file into the file-drop server and print its clickable LAN URL.

    Prints ONLY the URL on stdout (easy to copy); diagnostics go to stderr. Per
    no-localhost-urls.md, the URL is live-checked before printing — if the server
    is down it tries one restart, and refuses to print a dead URL."""
    from urllib.parse import urlsplit

    from filedrop import advertise_urls
    from filedrop.share import ShareError, share
    try:
        url, dest = share(args.path)
    except ShareError as e:
        print(f"share: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"share: unexpected error ({e})", file=sys.stderr)
        sys.exit(1)

    if not _filedrop_is_live(url):
        # Down — try a single restart, then re-check the primary before printing.
        print("share: file-drop not responding — attempting service restart...",
              file=sys.stderr)
        _restart_filedrop_service()
        if not _wait_filedrop_live(url):
            print(f"share: file copied to {dest} but the file-drop server is DOWN at "
                  f"{filedrop_url()} — start it with "
                  f"`systemctl --user start filedrop.service`.", file=sys.stderr)
            sys.exit(1)

    # Primary is live — print ONE URL per private interface (tailscale + LAN) that
    # actually answers, so the user has a working link whichever network they are on.
    sp = urlsplit(url)
    reachable = [u for u in advertise_urls(port=sp.port, path=sp.path)
                 if _filedrop_is_live(u)]
    for u in (reachable or [url]):
        print(u)


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
        # The send hook pipes the posted ❓ CONTENT on stdin (arbitrary quotes /
        # backticks never touch shell argv) — stored so the reply delivery can
        # wrap the answer with the question it answers (2026-07-17).
        # stdin is read ONLY on the explicit --question-stdin flag (the hook
        # sets it when piping). An unconditional isatty()-guarded read HUNG
        # forever when a caller spawned this command with an inherited
        # never-closing pipe as stdin (the 2026-07-19 push-gate hang — the
        # unittest child inherited the push's stdin and blocked in read()).
        from notify import record_question
        q_text = ""
        if getattr(args, "question_stdin", False):
            try:
                q_text = sys.stdin.read()
            except (OSError, ValueError):
                q_text = ""
        ok = record_question(args.message_id, args.channel, args.session,
                             args.cwd, question=q_text)
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

    if getattr(args, "repo_name", False):
        # The GitHub repo NAME for a cwd, from its `origin` remote — never the
        # directory basename (marek's checkout is `parovanie_produktov` while
        # every marker is keyed `parovanie-produktov`). Used by
        # notify-discord-pending.sh's delivery-conditional suppression (#134).
        from notify import repo_name_for
        sys.stdout.write(repo_name_for(getattr(args, "cwd", "") or "."))
        return

    if getattr(args, "newest_card", False):
        # mtime of the newest DELIVERED card marker for a repo, or nothing.
        # "Delivered", not "claimed" — the marker is written before the POST
        # (#135), so presence alone would let a FAILED card suppress the
        # fallback ping, which is precisely the hole being closed.
        from notify import newest_delivered_card
        ts = newest_delivered_card(getattr(args, "repo", "") or "")
        if ts is not None:
            sys.stdout.write(repr(ts))
        return

    if getattr(args, "backfill_digest", False):
        _notify_backfill_digest(args, send)
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


def _gh_out(*gh_args, timeout=8, cwd=None):
    """Best-effort `gh ...` stdout (stripped), or "" on any failure/timeout.

    Runs under `_gh_env()` — the SAME token resolution `cmd_tickets_status`
    has always used (#181 I-6, live-confirmed on david@subdev 2026-07-30).
    A reduced-authority stream box never runs `gh auth login` and carries no
    GH_TOKEN/GITHUB_TOKEN in its shell env; it authenticates per-command from
    ~/.git-credentials instead. Without that env a bare `gh` there exits 4
    ("To get started with GitHub CLI, please run: gh auth login"), so EVERY
    query through this helper failed closed — `slice-quals --count` printed
    "a gh query failed" and exited 1, which meant the fork-no-merge /goal
    template's condition (B) could NEVER hold on that box and the loop could
    never legitimately finish. A real token already in the env always wins,
    so this is a no-op on every box that has one.

    `cwd` runs gh inside a specific checkout (gh resolves the repo from the
    git remote there), so a caller that resolved the repo ROOT does not
    depend on the process cwd happening to be it (#181 I-5)."""
    import subprocess
    try:
        r = subprocess.run(["gh", *gh_args], capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, env=_gh_env())
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _write_autopilot_progress(name, remaining, bump_done=True):
    """Persist per-repo autopilot run progress for the statusline github-tickets segment
    (~/.claude/autopilot-progress/<repo>.json). `done` counts the completion
    cards sent within ONE run window; a card after a ≥6h gap starts a new run.

    `bump_done=False` (#181 I6, round 2 regression fix) writes the HEARTBEAT
    (`ts`) and `remaining` WITHOUT incrementing `done`. This is the ONLY
    writer of `ts`, which is what keeps the 6h AUTOPILOT_RUN_WINDOW_S run
    window alive (statusbar.py). #164's original fix SKIPPED CALLING this
    function entirely for a full-authority box's stream-ticket card, which
    correctly kept `done` from inflating but ALSO stopped refreshing `ts` —
    a run that cards only sub-dev stream tickets never opens/refreshes a
    progress file, so 'Issues D/T' silently reverts to 'Issues N core'
    mid-run. Always call this function; use `bump_done` to separate the two
    concerns instead of skipping the call.

    A heartbeat-only write REFRESHES an already-live run window; it never
    OPENS one (#164 M-1, round 3). With no prior progress file — or one whose
    `ts` has fallen outside AUTOPILOT_RUN_WINDOW_S, which zeroes `base_done` —
    an unconditional heartbeat wrote `{"done": 0, "ts": now}`, and statusbar
    then rendered `Issues 0/40` for a full 6h window: an assertion that a run
    is active and has achieved nothing, replacing the correct `Issues 40 core`.
    Round 2 fixed a heartbeat that stopped too early by introducing one that
    started too eagerly.

    Round 4 corrects the REASON this docstring used to give for declining that
    write. It used to justify the guard by asserting that the run a heartbeat
    exists to keep alive must already have a progress file inside the window;
    that is FALSE for a REVIEW-ONLY gatekeeper run,
    whose cards are all sub-dev stream tickets, so nothing ever sets
    `bump_done` and no file is ever created — a shape the obligation-set
    change makes more common, since a gatekeeper's obligation set is
    explicitly the action-only tickets. The real reason is simpler and holds
    everywhere: a heartbeat carries no evidence of CORE progress, so opening a
    window on one would assert `0/N` — an active run that has achieved nothing
    — for a full 6h window, which is M-1 verbatim.

    The accepted consequence, deliberately NOT "fixed": a review-only run
    renders `Issues N core · streamy M` throughout instead of a D/T. That is
    accurate — actioning a stream ticket genuinely does not change the core
    count — and the only way to make D/T meaningful for such a run is to scope
    `remaining` to the OBLIGATION set while the idle render stays core-scoped,
    i.e. one label over two different populations depending on run state,
    which is #164's own title. `remaining` is overwritten from the live 120s
    tickets cache on every render anyway (statusbar.py).

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
        within_window = bool(
            prev and now - (prev.get("ts") or 0) <= statusbar.AUTOPILOT_RUN_WINDOW_S)
        if not bump_done and not within_window:
            return          # refresh a live run, never open one (#164 M-1)
        base_done = int(prev.get("done") or 0) if within_window else 0
        done = base_done + 1 if bump_done else base_done
        if not isinstance(remaining, int):
            remaining = prev.get("remaining") if prev else None
        tmp = str(p) + ".tmp"
        Path(tmp).write_text(json.dumps({"done": done, "remaining": remaining,
                                         "ts": now}))
        os.replace(tmp, p)
    except Exception:
        pass


def _gh_env():
    """Env for the `git`/`gh` subprocess calls in tickets-status --refresh, with
    a fallback GH_TOKEN extracted from ~/.git-credentials when the shell has
    none (#25). A reduced-authority sub-dev stream (david) never runs
    `gh auth login` — its CLAUDE.md External Developer Workflow authenticates
    per-command by extracting the token from ~/.git-credentials instead. Without
    this, every `gh` call in that shell fails silently and the cache is stuck
    at open=None forever. A real GH_TOKEN/GITHUB_TOKEN already in the env
    always wins — never overridden by a stale credentials-file token."""
    import re

    env = os.environ.copy()
    if env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"):
        return env
    try:
        text = (Path.home() / ".git-credentials").read_text()
    except OSError:
        return env
    m = re.search(r"https://[^:/@\s]+:([^@\s]+)@github\.com", text)
    if m:
        env["GH_TOKEN"] = m.group(1)
    return env


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

    gh_env = _gh_env()

    def _out(argv, cd):
        try:
            r = subprocess.run(argv, cwd=cd, capture_output=True, text=True,
                               timeout=20, env=gh_env)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    entry = {"ts": int(time.time()), "open": None, "name": "", "root": ""}
    # ONE root definition, shared with cmd_slice_quals / cmd_core_quals (#181
    # I-5) — including #61's fallback for a session cwd that is the PARENT of
    # the actual repo (montalu's ~/devel/odoo, repo at ~/devel/odoo/odoo-erp).
    root = _repo_root(cwd, runner=_out)
    if root:
        entry["root"] = root
        slug = _out(["gh", "repo", "view", "--json", "nameWithOwner",
                     "-q", ".nameWithOwner"], root)
        entry["name"] = slug.rstrip("/").split("/")[-1] if slug else ""
        # A reduced-authority stream (sub-dev box: david/montalu/marek — resolved
        # marker-aware via resolve_authority) counts only ITS OWN slice: open,
        # non-skip, assigned-to-me OR authored-by-me. The full repo backlog on a
        # sub-dev statusline was noise ("Issues 16" where David's slice is 6 —
        # gatekeeper goal, 2026-07-11). Full-authority boxes keep the full count.
        # The counter's meaning on EVERY box: "tickets THIS box should work via
        # /autopilot so they don't rot" (stream-label ownership convention — odoo-erp
        # PR #1440: stream:<name> labels a sub-dev-owned ticket; unlabeled = core).
        if resolve_authority(cwd=root) != "full":
            entry["scope"] = "mine"
            # Own slice = assigned-to-me ∪ authored-by-me ∪ labeled stream:<me>,
            # partitioned into ACTIVE-on-me vs already HANDED OFF to the gatekeeper:
            # a ticket carrying `ready-for-review` (auto-labeled by the repo's
            # subdev-handoff-label workflow at the hand-off comment) is waiting on
            # the gatekeeper — the statusline shows both ("Issues 1 · gk 5").
            # SHARED-ACCOUNT boxes (montalu's PAT logs in as the MAINTAINER
            # account) must NOT use @me — author:@me matched every user-authored
            # ticket and the footer showed foreign streams' numbers (2026-07-20);
            # there the slice is the stream LABEL alone.
            handed, mine, failed = {}, set(), False
            # SliceUnresolved (#181 I-2): an unresolvable gh identity is a gh
            # ERROR, and a gh error is never an empty slice — keep open=None,
            # exactly as a failed query already does. Guessing a qual set here
            # would show a shared-account box the maintainer's whole backlog.
            try:
                quals = _slice_quals(_current_user(), root)
            except SliceUnresolved:
                quals, failed = [], True
            for qual in quals:
                raw = _out(["gh", "issue", "list", "--state", "open", "--search",
                            "-label:autopilot-skip " + qual, "-L", "200",
                            "--json", "number,labels"], root)
                try:
                    for x in json.loads(raw):
                        n_num = x["number"]
                        mine.add(n_num)
                        labels = {(lb or {}).get("name") for lb in (x.get("labels") or [])}
                        handed[n_num] = handed.get(n_num, False) or \
                            ("ready-for-review" in labels)
                except (ValueError, TypeError, KeyError):
                    failed = True   # gh error ≠ empty slice — keep open=None
            gk = sum(1 for n_num in mine if handed.get(n_num))
            entry["open"] = None if failed else len(mine) - gk
            entry["gk"] = None if failed else gk
            # Skipped bucket (2026-07-16): same slice quals, POSITIVE label
            # filter — how many of MY tickets are excluded from autopilot runs.
            # `quals` empty ⟺ SliceUnresolved above (it is otherwise always 1
            # or 3 quals), so the skipped bucket stays None for the same
            # reason the open count does.
            skipped, sfailed = set(), not quals
            for qual in quals:
                raw = _out(["gh", "issue", "list", "--state", "open", "--search",
                            "label:autopilot-skip " + qual, "-L", "200",
                            "--json", "number"], root)
                try:
                    skipped.update(x["number"] for x in json.loads(raw))
                except (ValueError, TypeError, KeyError):
                    sfailed = True   # gh error ≠ zero skips — keep skipped=None
            entry["skipped"] = None if sfailed else len(skipped)
        else:
            # Full-authority (core/gatekeeper) slice: the whole backlog MINUS the
            # sub-dev-owned stream:<user> tickets (each reduced stream in
            # AUTHORITY_BY_USER). On repos without stream labels the exclusions
            # match nothing → the full count, unchanged.
            entry["scope"] = "core"
            excl = _core_search_excl()
            # -L 1000 (#181 I5, round 2): was -L 200 -- above 200 open
            # non-skip issues this AND the total query below both silently
            # clamped to the SAME 200, so streamy=0 exactly when the hidden
            # population was largest. 1000 comfortably exceeds any real
            # backlog size seen on a managed repo.
            n = _out(["gh", "issue", "list", "--state", "open", "--search",
                      "-label:autopilot-skip " + excl, "-L", "1000",
                      "--json", "number", "-q", "length"], root)
            try:
                entry["open"] = int(n)
            except (TypeError, ValueError):
                entry["open"] = None
            # Streamy bucket (#164): open non-skip tickets EXCLUDED from the
            # core slice because they carry a sub-dev stream:<user> label —
            # the footer used to hide this population behind a bare "Issues
            # 28" while 45 more sat unlabeled-visible (the user's own
            # forensics: 28 core + 45 streamy = 73 repo-wide, non-skip). A
            # hidden 45 looks exactly like a broken counter — the same
            # reasoning that already keeps `gk` visible at 0 applies with far
            # more force here. total(non-skip, whole repo) - core = streamy.
            t = _out(["gh", "issue", "list", "--state", "open", "--search",
                      "-label:autopilot-skip", "-L", "1000",
                      "--json", "number", "-q", "length"], root)
            try:
                total_open = int(t)
                entry["streamy"] = (total_open - entry["open"]
                                    if entry["open"] is not None else None)
            except (TypeError, ValueError):
                entry["streamy"] = None
            # Skipped bucket (2026-07-16): the POSITIVE label query over the
            # same core slice — how many tickets are excluded from autopilot.
            s = _out(["gh", "issue", "list", "--state", "open", "--search",
                      "label:autopilot-skip " + excl, "-L", "1000",
                      "--json", "number", "-q", "length"], root)
            try:
                entry["skipped"] = int(s)
            except (TypeError, ValueError):
                entry["skipped"] = None
            # gk-req badge (#30): open needs-gatekeeper stream→supervisor
            # action requests — the WHOLE repo (requests carry stream labels;
            # the supervisor must see them all), full-authority boxes only.
            g = _out(["gh", "issue", "list", "--state", "open", "--label",
                      "needs-gatekeeper", "-L", "200",
                      "--json", "number", "-q", "length"], root)
            try:
                entry["gk_req"] = int(g)
            except (TypeError, ValueError):
                entry["gk_req"] = None
    cache = statusbar.cache_dir() / (statusbar.cwd_key(cwd) + ".json")
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(cache) + ".tmp"
    Path(tmp).write_text(json.dumps(entry))
    os.replace(tmp, cache)
    print("refreshed open=%s name=%s" % (entry["open"], entry["name"] or "-"))


def cmd_gk_request(args):
    """Stream→supervisor action request (#30): file (or mark) the ticket that
    asks the gatekeeper/supervisor for an action the stream cannot perform
    itself (box access, workflow re-dispatch, infra). Canonical form = label
    `needs-gatekeeper` in the upstream repo; a stream whose PAT cannot label
    degrades AUTOMATICALLY to the `GATEKEEPER-ACTION:` title/comment prefix,
    which the watchdog's job-11 query also matches (the supervisor adds the
    label on pickup). Delivery to the supervisor is the watchdog's job — the
    stream files and keeps working; no user middleman, no ssh to foreign
    boxes."""
    import subprocess

    def _gh(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=30)
        except Exception as e:
            return subprocess.CompletedProcess(argv, 1, "", str(e))

    repo = getattr(args, "repo", None)
    R = ["-R", repo] if repo else []
    issue = getattr(args, "issue", None)
    if issue:
        labeled = _gh(["gh", "issue", "edit", str(issue), "--add-label",
                       "needs-gatekeeper"] + R).returncode == 0
        text = getattr(args, "comment", None) or (
            "Žiadosť o akciu supervízora — detail v tickete.")
        if not labeled and not text.startswith("GATEKEEPER-ACTION:"):
            text = "GATEKEEPER-ACTION: " + text
        c = _gh(["gh", "issue", "comment", str(issue), "--body", text] + R)
        if c.returncode != 0:
            print("gk-request FAILED: could not comment #%s: %s"
                  % (issue, c.stderr.strip()))
            return 1
        retitled = False
        if not labeled:
            # a comment-only marker is INVISIBLE to job 11's queries (label +
            # in:title) — best-effort retitle so the request stays
            # machine-discoverable (works when the issue is the stream's own)
            v = _gh(["gh", "issue", "view", str(issue),
                     "--json", "title", "-q", ".title"] + R)
            old = (v.stdout or "").strip()
            if v.returncode == 0 and old \
                    and not old.startswith("GATEKEEPER-ACTION:"):
                retitled = _gh(["gh", "issue", "edit", str(issue), "--title",
                                "GATEKEEPER-ACTION: " + old] + R
                               ).returncode == 0
        print("gk-request: #%s commented (label %s)"
              % (issue, "added" if labeled
                 else ("DENIED — retitled with GATEKEEPER-ACTION" if retitled
                       else "DENIED and retitle failed — auto-delivery NOT "
                            "guaranteed; prefer a NEW ticket via gk-request "
                            "--title")))
        return 0

    title = getattr(args, "title", None)
    if not title:
        print("gk-request: --title (new ticket) or --issue N required")
        return 1
    body_file = getattr(args, "body_file", None)
    B = (["--body-file", body_file] if body_file
         else ["--body", getattr(args, "body", None) or title])
    r = _gh(["gh", "issue", "create", "--title", title,
             "--label", "needs-gatekeeper"] + B + R)
    if r.returncode == 0:
        print("gk-request filed: %s" % r.stdout.strip())
        return 0
    ft = (title if title.startswith("GATEKEEPER-ACTION:")
          else "GATEKEEPER-ACTION: " + title)
    r2 = _gh(["gh", "issue", "create", "--title", ft] + B + R)
    if r2.returncode == 0:
        print("gk-request filed (label denied — GATEKEEPER-ACTION title "
              "fallback): %s" % r2.stdout.strip())
        return 0
    print("gk-request FAILED: %s / %s"
          % (r.stderr.strip(), r2.stderr.strip()))
    return 1


# How many tickets the digest NAMES. Also the suppression bound: the caller
# may only mark what this body listed by number.
BACKFILL_MAX_SHOWN = 10


def compose_backfill_digest(repo_name, tickets, since_label):
    """ONE catch-up message per repo for a window that went unreported (#134).

    Deliberately a DIGEST and not N cards: the silent window held ~103 closed
    issues across two repos, and firing a retroactive card per ticket would
    put a hundred pings on the user's phone to apologise for having sent
    none. Plain Slovak, phone-readable, bounded — the numbers plus a few
    titles, never a wall.

    `BACKFILL_MAX_SHOWN` is shared with the caller on purpose: the tickets
    this body NAMES are exactly the tickets the caller may then suppress."""
    shown = tickets[:BACKFILL_MAX_SHOWN]
    n = len(tickets)
    # Slovak plural: 1 ticket / 2-4 tickety / 5+ ticketov.
    word = "ticket" if n == 1 else ("tickety" if 2 <= n <= 4 else "ticketov")
    lines = ["\U0001f4ec **%s** — dobiehacie hlásenie" % repo_name,
             # NOT "a nasadená": these closures were never verified as
             # deployed by anything here, and a catch-up message that
             # overclaims is a worse repair than the silence it apologises
             # for.
             "> Od %s sa uzavrelo **%d** %s, ale správa o nich neprišla na "
             "telefón. Práca je hotová — chýbalo len hlásenie o nej."
             % (since_label, n, word)]
    for t in shown:
        title = (t.get("title") or "").strip()
        if len(title) > 90:
            title = title[:87] + "…"
        lines.append("> • #%s %s" % (t.get("number"), title))
    if len(tickets) > len(shown):
        lines.append("> • …a ďalších %d" % (len(tickets) - len(shown)))
    lines.append("> Odteraz sa hlásenie posiela automaticky a jeho vynechanie "
                 "sa kontroluje.")
    return "\n".join(lines)


# A repo's own checkout can sit deeper than the watchdog's repo sweep looks,
# and a worktree or submodule keeps `.git` as a FILE rather than a directory
# — `discover_managed_repos` sees neither (it stops at depth 4 and tests only
# `dirnames`). Measured on dev1: 40 discovered against 55 real checkouts,
# among them a real depth-4 repo and a submodule. That sweep is shared with
# watchdog jobs 27/28 and is right for what THEY do, so this walks its own
# way rather than widening a function two other jobs depend on.
_CHECKOUT_MAX_DEPTH = 6
_CHECKOUT_SKIP = {"node_modules", ".cache", ".local", "venv", ".venv",
                  "__pycache__", ".npm", "target", "dist", "build"}


def _checkout_roots(home=None):
    """Every checkout root under `home` — `.git` as a directory OR a file."""
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    roots, base = [], str(home).rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(home, topdown=True,
                                                onerror=lambda e: None):
        if dirpath.rstrip("/").count("/") - base >= _CHECKOUT_MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _CHECKOUT_SKIP]
        if ".git" in dirnames:
            roots.append(dirpath)
            dirnames.remove(".git")          # never descend into .git itself
        elif ".git" in filenames:
            roots.append(dirpath)            # worktree / submodule
    return roots


def _local_checkout_for_repo(name, cwd=None, home=None, roots=None,
                             name_of=None):
    """The local checkout root whose `origin` resolves to repo NAME `name`,
    or None when this box holds no such checkout.

    Matched on the NAME, not `owner/name`, because that is exactly the
    granularity of the marker namespace this answer guards
    (`notify._dedup_path` keys cards as `<name>#<n>`). It is a NECESSARY
    condition, never a sufficient one — a fork, a same-named repo of another
    owner, or a repo checked out on two boxes all pass it — so the caller
    pairs it with an explicit override rather than treating it as proof.

    The invocation cwd is consulted FIRST: standing in the repo is the
    common case and answers the question with no walk at all. A root that
    cannot be identified is skipped, never fatal — one unreadable checkout
    must not hide the real match further down the list."""
    if name_of is None:
        from notify import repo_name_for
        name_of = repo_name_for

    def named(path):
        try:
            return name_of(path)
        except Exception:
            return ""

    here = cwd if cwd is not None else os.getcwd()
    if here and named(here) == name:
        return here
    try:
        if roots is None:
            candidates = _checkout_roots(home)
        else:
            candidates = roots() if callable(roots) else roots
    except Exception:
        candidates = []
    for root in candidates:
        if named(root) == name:
            return root
    return None


def _checkout_pane_owner(path, panes=None, owner_of=None):
    """The tmux owner of the live Claude pane sitting in checkout `path`, or
    "" when no pane answers or more than one owner does.

    This is the only source that actually KNOWS who a repo belongs to. The
    operator's own tmux does not: a catch-up digest for someone else's repo
    is typically issued from a third box, and over ssh there is no tmux at
    all. On 2026-07-29 a codex-bridge digest — a repo whose only pane sits in
    the `david` session group — was addressed from a dev1 session and landed
    in the wrong thread.

    Two owners on one checkout resolve to "" for the same reason job 5 stops
    guessing on a multi-owner box: an unaddressed report costs less than a
    misdirected one. Every failure degrades to "" (no tmux, an unreadable
    pane list, an owner lookup that raises) so the caller's own override
    still works on a box with no panes at all."""
    if not path:
        return ""
    try:
        from watchdog import list_claude_panes, pane_owner
    except Exception:                      # a box without the package
        if panes is None:
            return ""
        list_claude_panes = pane_owner = None
    panes = panes or list_claude_panes
    owner_of = owner_of or pane_owner
    try:
        rows = panes()
    except Exception:
        return ""
    try:
        target = os.path.realpath(path)
    except Exception:
        target = path
    found = set()
    for pid, cwd in rows or ():
        if not cwd:
            continue
        try:
            here = os.path.realpath(cwd)
        except Exception:
            here = cwd
        # `startswith` alone would match a SIBLING sharing the prefix
        # (`proj-old` vs `proj`); the separator is what makes it containment.
        if here != target and not here.startswith(target.rstrip("/") + os.sep):
            continue
        try:
            owner = (owner_of(pid) or "").strip().lower()
        except Exception:
            owner = ""
        if owner:
            found.add(owner)
    return found.pop() if len(found) == 1 else ""


def _notify_backfill_digest(args, send):
    """One catch-up digest for a repo whose completion cards never fired.

    Reads the closed issues in the window from gh, drops any that DID get a
    delivered card, and sends a single message. Idempotent through the same
    dedup path as every other notification.

    MACHINE-LOCAL BY CONSTRUCTION: `marker_delivered` reads this box's own
    `~/.claude/autopilot-notify-sent/`. Job 25 never trips over that (it
    only examines checkouts a live pane sits in), but this command takes a
    bare `owner/name` from an operator, so it refuses outright when the repo
    has no checkout here — under-reporting would produce a digest
    apologising for reports another box really delivered."""
    from notify import (backfill_marker_key, mark_backfill_reported,
                        marker_delivered)
    repo = getattr(args, "repo", None)
    since = getattr(args, "since", None)
    if not repo or not since:
        print("notify --backfill-digest needs --repo owner/name and --since",
              file=sys.stderr)
        sys.exit(1)
    name = str(repo).rstrip("/").split("/")[-1]
    checkout = _local_checkout_for_repo(name)
    if getattr(args, "force", False):
        print("notify --backfill-digest: --force — skipping the local-checkout "
              "check for '%s' on %s. If this box does not hold that repo's "
              "markers, the digest will over-report."
              % (name, os.uname().nodename), file=sys.stderr)
    elif checkout is None:
        print("notify --backfill-digest: no local checkout of '%s' on this "
              "box (%s).\nThe digest is computed from THIS box's card markers "
              "(~/.claude/%s/), which are machine-local — with no checkout "
              "here every ticket reads as unreported and the digest would "
              "apologise for reports another box already delivered.\nRun it "
              "on the box holding the '%s' checkout, or leave it to that "
              "box's watchdog job 25. If the checkout IS here and this "
              "check simply missed it, re-run with --force."
              % (name, os.uname().nodename, "autopilot-notify-sent", name),
              file=sys.stderr)
        sys.exit(1)
    raw = _gh_out("issue", "list", "-R", repo, "--state", "closed", "-L", "200",
                  "--json", "number,title,closedAt", timeout=60)
    try:
        issues = json.loads(raw or "[]")
    except ValueError:
        issues = []
    # Unreported = no card of its own AND not already accounted for by an
    # earlier DELIVERED digest — so a second run over a wider window does
    # not re-report what the user has already been told.
    tickets = [i for i in issues
               if (i.get("closedAt") or "") >= since
               and not marker_delivered("%s#%s" % (name, i.get("number")))
               and not marker_delivered(
                   backfill_marker_key(name, i.get("number")))]
    tickets.sort(key=lambda i: i.get("number") or 0)
    if not tickets:
        print("backfill: nothing unreported for %s since %s" % (name, since))
        return
    # WHO it goes to is resolved from the checkout's own pane, not from the
    # operator. `--owner-name` remains for a box where the repo has no live
    # pane, but it may no longer CONTRADICT one: obeying a flag over the pane
    # is exactly how a codex-bridge catch-up reached the wrong thread.
    derived = _checkout_pane_owner(checkout)
    stated = (getattr(args, "owner_name", None) or "").strip().lower()
    if derived and stated and derived != stated:
        print("notify --backfill-digest: --owner-name %s contradicts the "
              "checkout's own pane, which belongs to %s.\n'%s' is where this "
              "repo's reports go; sending it anywhere else puts the report in "
              "the wrong thread and gives someone else the noise.\nDrop "
              "--owner-name (the pane answers it), or correct it to '%s'."
              % (stated, derived, derived, derived), file=sys.stderr)
        sys.exit(1)
    body = compose_backfill_digest(name, tickets, since[:10])
    status = send(body, owner=derived or stated or None,
                  dedup_key="backfill:%s:%s" % (name, since[:10]),
                  dry_run=getattr(args, "dry_run", False))
    print("%s (%d tickets)" % (status, len(tickets)))
    # Record what this digest reported, so watchdog job 25 stops re-flagging
    # tickets the user has already heard about — but ONLY the tickets the
    # message actually NAMED. The body shows at most BACKFILL_MAX_SHOWN
    # titles and then "a ďalších N": the overflow is counted, never named,
    # so the user has no number to chase and a marker for it would claim
    # per-ticket coverage the message never gave. Suppressing exactly what
    # was named leaves job 25 surfacing the rest a batch at a time, which is
    # the direction that cannot lose a ticket. `mark_backfill_reported`
    # itself writes only on a proven 'sent' (#134).
    named = tickets[:BACKFILL_MAX_SHOWN]
    marked = mark_backfill_reported(
        name, [t.get("number") for t in named], status)
    if marked:
        print("backfill: %d of %d tickets recorded as reported (only those "
              "the message named)" % (marked, len(tickets)))
    if status == "dedup":
        # The digest-level key is date-truncated, so a same-day re-run after
        # a new ticket closed hits the claim and sends NOTHING. Printing
        # 'dedup' and exiting 0 read exactly like a delivery while those
        # tickets stayed unreported.
        print("notify --backfill-digest: NOTHING SENT — an earlier digest "
              "for %s/%s already claimed this key, so these %d ticket(s) "
              "were not reported. Re-run with a different --since."
              % (name, since[:10], len(tickets)), file=sys.stderr)
        sys.exit(1)
    if status not in ("sent", "dry-run"):
        sys.exit(1)


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

        # title + labels in ONE call — labels decide whether THIS ticket is a
        # sub-dev stream:<user> hand-off (#164 defect 2): the D/T progress
        # counter must stay within ONE population, so a stream ticket's card
        # must never inflate a CORE-scoped 'done' the core-scoped 'remaining'
        # cannot back.
        view_raw = _gh_out("issue", "view", str(issue), "-R", repo,
                           "--json", "title,labels")
        try:
            view = json.loads(view_raw) if view_raw else {}
        except ValueError:
            view = {}
        if not isinstance(view, dict):
            view = {}
        title = view.get("title") or ("#%s" % issue)
        # M11 (round 2): on a parse/lookup FAILURE (empty view_raw, malformed
        # JSON, non-dict) `view` is {} and `view.get("labels")` is None ->
        # `_ticket_is_stream_labeled(None)` is False -> the OLD
        # `not _ticket_is_stream_labeled(...)` collapsed to True, silently
        # restoring the PRE-#164 wrong behaviour: a ticket we could not
        # identify was treated as CORE and its card could inflate the
        # core-scoped 'done'. A failed lookup now defaults the SAFE
        # direction — the worst case is an undercounted 'done' (annoying),
        # never a corrupted cross-population ratio (wrong).
        view_ok = "labels" in view
        is_core_ticket = view_ok and not _ticket_is_stream_labeled(view.get("labels"))
        if not view_ok and not getattr(args, "dry_run", False):
            # --dry-run stays silent on stderr (an established contract —
            # test_dry_run_still_exits_zero_and_says_nothing_on_stderr); this
            # diagnostic is genuinely best-effort visibility, not part of
            # dry-run's "preview with no side effects, no noise" promise.
            print("notify --run-card: could not determine whether #%s is a "
                  "sub-dev stream ticket (gh issue view returned no labels) "
                  "-- treating conservatively as non-core so this card does "
                  "not inflate 'done'." % issue, file=sys.stderr)
        # #181 I-5 residual (round 4): resolve identity against the REPO ROOT,
        # like cmd_tickets_status / cmd_slice_quals / cmd_core_quals already
        # do. This was the FOURTH call site and the only one still resolving
        # against the PROCESS cwd — so a session running from a subdirectory
        # made the run-card and the footer disagree about which profile the
        # box is even on (a project CLAUDE.md `airuleset:authority=` marker is
        # invisible from a subdirectory), and "one definition, resolved per
        # box" was not true until all four agreed.
        card_root = _repo_root() or None
        is_full = resolve_authority(cwd=card_root) == "full"
        # remaining feeds the statusline's D/T — on a reduced-authority box it
        # must be the STREAM's slice, not the whole repo (david saw 'Issues
        # 2/26' while his slice was 5 — 2026-07-19). Same quals as
        # tickets-status; gh error → None, never a wrong number.
        if not is_full:
            nums, failed = set(), False
            try:
                slice_quals = _slice_quals(_current_user(), cwd=card_root)
            except SliceUnresolved:
                # #181 I-2: an unresolvable gh identity is a gh error, and a
                # gh error is never a wrong number here — remaining stays None.
                slice_quals, failed = [], True
            for qual in slice_quals:
                raw = _gh_out("issue", "list", "-R", repo, "--state", "open",
                              "--search", "-label:autopilot-skip " + qual,
                              "-L", "200", "--json", "number")
                try:
                    nums.update(x["number"] for x in json.loads(raw))
                except (ValueError, TypeError, KeyError):
                    failed = True
            remaining = None if failed else len(nums)
            scope_label = None
        else:
            # Full-authority: scope to the CORE slice — the SAME exclusion the
            # footer uses (#164) — never the whole-repo count. A whole-repo
            # 'ostáva 72' next to a core 'done' silently divides two
            # populations; scoping here makes the two agree by construction,
            # and the 'core' word states which population it is.
            excl = _core_search_excl()
            # -L 1000, not 200 (#181 I5, round 2): the same clamp-difference
            # arithmetic that could zero out the footer's `streamy` bucket
            # also understated `remaining` here.
            rem_raw = _gh_out("issue", "list", "-R", repo, "--state", "open",
                              "--search", "-label:autopilot-skip " + excl,
                              "-L", "1000", "--json", "number", "-q", "length",
                              timeout=20)
            try:
                remaining = int(rem_raw)
            except (TypeError, ValueError):
                remaining = None
            scope_label = "core"

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
            done=None, remaining=remaining, urls=getattr(args, "url", None),
            handoff=getattr(args, "handoff", False), scope_label=scope_label)
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
            # On a full-authority box a STREAM ticket's card must NOT advance
            # the CORE-scoped D/T counter (#164 defect 2) — but it must STILL
            # refresh the `ts` window heartbeat, or a run that cards only
            # stream tickets never keeps 'Issues D/T' alive at all (#181 I6,
            # round 2 regression). ALWAYS write; only the done-bump is gated.
            # Reduced-authority boxes have no core/stream split (their own
            # slice IS their whole population) — always bump.
            _write_autopilot_progress(
                name, remaining, bump_done=(not is_full or is_core_ticket))
        elif status not in ("dedup", "dry-run"):
            # #135: a card that never reached Discord must NOT report success.
            # It still never raises and never blocks the work — but the
            # worker's own Bash call now SEES the failure instead of being
            # told to ignore it, and the reason is durable.
            from notify import log_delivery
            log_delivery(status, kind="run-card", key=dedup,
                         reason="send-returned-%s" % status)
            print("notify --run-card: NOT delivered (%s) for %s — the ticket "
                  "has no completion card. Re-run it, or report it in the "
                  "evidence block." % (status, dedup), file=sys.stderr)
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        # Never raise into the worker, but never vanish either (#135).
        try:
            from notify import log_delivery
            log_delivery("error", kind="run-card",
                         key="%s#%s" % (getattr(args, "repo", "-"),
                                        getattr(args, "issue", "-")),
                         reason=type(exc).__name__)
        except Exception as log_exc:              # logging must not mask `exc`
            print("notify --run-card: logging failed (%r)" % log_exc,
                  file=sys.stderr)
        print("notify --run-card: FAILED (%r) — no completion card was sent."
              % exc, file=sys.stderr)
        sys.exit(1)


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
        # Isolated montalu odoo dev stream — MIGRATED 2026-07-24 from dev1 to
        # the subdev VPS (airuleset#33 + odoo-erp#1895; same box as marek and
        # david: tailscale 100.118.174.27 / MagicDNS "subdev", public
        # subdev.newlevel.media = fallback only — address by tailscale per
        # machine-identities). The old dev1 account (uid 1001) is LOCKED with
        # a ForceCommand redirect notice; /home/montalu on dev1 stays
        # untouched as the rollback backup per the #1895 contract. Unlike
        # marek/david, montalu authorizes the DEFAULT newlevel key (no
        # gatekeeper_access identity — live-verified at the swap).
        "name": "montalu@subdev",
        "host": "100.118.174.27",
        "user": "montalu",
        "repo_path": "~/devel/airuleset",
    },
    {
        # Marek's isolated user — MIGRATED 2026-07-21/22 from the gatekeeper
        # VPS to the dedicated subdev VPS (Hetzner cx33/nbg1 "subdev", project
        # odoo-subdev, id 153587360): tailscale 100.118.174.27 / MagicDNS
        # "subdev", public 116.203.108.177 = subdev.newlevel.media (fallback
        # only — address by tailscale per machine-identities). Old marek@gk
        # account is BLOCKED (ForceCommand notice). Same gatekeeper_access key
        # (authorized_keys byte-copied in the migration). Evidence: airuleset
        # #23 + odoo-erp #1895 hand-over comments.
        "name": "marek@subdev",
        "host": "100.118.174.27",
        "user": "marek",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # David's isolated external-dev user (slovnormal odoo dev stream: no
        # sudo, no prod keys, can't read other homes) — MIGRATED 2026-07-22
        # from the gatekeeper VPS to the same subdev VPS as marek (see the
        # marek@subdev entry above for the box facts). Old david@gk account is
        # BLOCKED (ForceCommand notice). Same gatekeeper_access key.
        "name": "david@subdev",
        "host": "100.118.174.27",
        "user": "david",
        "repo_path": "~/devel/airuleset",
        "identity": "~/.secrets/gatekeeper_access_ed25519",
    },
    {
        # simap — 4th sub-dev stream, on the same subdev VPS as marek/david
        # (Odoo 15 -> Odoo 19 demo for the potential SIMAP client; tracking
        # ticket odoo-erp#2391, registered here via airuleset#143). Built by
        # gatekeeper 2026-07-28: uid 1003, no sudo, home /home/simap (750),
        # own fresh id_ed25519 (not copied from anywhere), authorized_keys =
        # the SAME operator public keys as marek — so it uses the identical
        # gatekeeper_access identity, not montalu's default-key path.
        "name": "simap@subdev",
        "host": "100.118.174.27",
        "user": "simap",
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


def _watchdog_bounce_fetch(root):
    """Job 8's real gh fetch (wired here so run_once unit tests stay network-free)."""
    from watchdog import _fetch_bounce_tickets
    return _fetch_bounce_tickets(root)


def _watchdog_gkreq_fetch(root):
    """Job 11's real gh fetch (#30) — same network-free-tests wiring as job 8."""
    from watchdog import _fetch_gkreq_tickets
    return _fetch_gkreq_tickets(root)


def _watchdog_card_probe(root, base):
    """Job 25's confirming fetch (#134).

    Wired HERE, like jobs 8/11/16/24, so run_once's unit tests stay
    network-free. Job 25 measures which issues a merge on the base branch
    closed, so a base ref that has merely gone stale in the local checkout
    would under-report — the opposite of job 24's failure mode, and the
    reason the same fetch is required before the measurement rather than
    after it. Writes ONLY the remote-tracking ref; every repo this touches
    belongs to somebody else. No `gh` half at all: the `Closes #N` in the
    merge commit is the whole fact this job needs, for free.

    #172 (reopened) smaller item: timeout cut 90s -> 15s, matching jobs
    27/28's own cuts (`_watchdog_git_fetch`). Job 25 runs BEFORE jobs 27/28
    in every sweep — one hung fetch here used to still eat 90s of the 120s
    `TimeoutStartSec` unit budget on its own, leaving jobs 27/28 no chance
    to run at all even after their OWN timeouts were bounded.
    """
    import subprocess
    remote, _, branch = (base or "origin/main").partition("/")
    try:
        subprocess.run(["git", "-C", root, "fetch", "--quiet", "--no-tags",
                        remote or "origin", branch or "main"],
                       capture_output=True, timeout=15)
    except Exception as e:
        # Degrade to the local-only read rather than going quiet: an
        # unreported ticket the user never hears about is the failure this
        # job exists to prevent, and the worst case of a stale base ref is
        # that the ping arrives a sweep later.
        return {"fetch_error": repr(e)}
    return None


def _watchdog_closed_fetch(root, since_ts):
    """Job 25's fallback for a repo that never writes `Closes #N` trailers.

    Wired HERE, like every other network call, so run_once's unit tests stay
    network-free. Job 25 asks for this ONLY when the local read found no
    trailers at all yet the base branch did take fresh merges — the exact
    signature of a repo whose issues close from the PR body instead. tvdole
    is that repo (20 commits in 48h, zero trailers in its last 200 commits),
    and it is one of the two in #134's evidence, so without this the backstop
    would have been blind to half the incident it was written for.

    Cost stays where the design intended: a repo that answers locally never
    reaches this, so it is roughly one call per trailer-less repo per sweep
    rather than one per repo — and returning None on any failure degrades to
    the local-only answer rather than to silence.

    #172 (reopened) smaller item: timeout cut 45s -> 10s, matching jobs
    27/28's own `gh` cuts (`_watchdog_issue_counts_fetch`) for the same
    reason `_watchdog_card_probe` above was cut — this call dispatches
    before jobs 27/28 in every sweep.
    """
    import subprocess
    import time
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since_ts))
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--state", "closed", "--limit", "100",
             "--json", "number,closedAt"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return [i["number"] for i in json.loads(r.stdout or "[]")
                if (i.get("closedAt") or "") >= since]
    except Exception:
        return None


def _watchdog_delivery_probe(root, base):
    """Job 24's confirming fetch + best-effort blocker lookup (#138).

    Wired HERE, like jobs 8/11/16, so run_once's unit tests stay network-free.
    Two halves with very different standing:

      * The FETCH is load-bearing. Job 24 RE-MEASURES its verdict after this
        call returns, so an `origin/<base>` ref that had merely gone stale in
        the local checkout is corrected here and the "stall" disappears
        instead of paging anyone. It writes ONLY the remote-tracking ref —
        never the worktree, index or a local branch — which matters because
        every repo this touches belongs to somebody else.
      * The gh lookup is pure ENRICHMENT: it names the open PR that is
        BLOCKED and the check that is red, so the ping says *why* nothing is
        landing. Any failure (no gh, no network, a repo with no PRs) costs
        that one sentence and nothing else.

    #172 (reopened) smaller item: timeouts cut 90s -> 15s / 45s -> 10s,
    matching jobs 27/28's own cuts. Job 24 runs before jobs 27/28 in every
    sweep — one hung fetch or gh call here used to still eat most of the
    120s `TimeoutStartSec` unit budget on its own.
    """
    import subprocess
    remote, _, branch = (base or "origin/main").partition("/")
    try:
        subprocess.run(["git", "-C", root, "fetch", "--quiet", "--no-tags",
                        remote or "origin", branch or "main"],
                       capture_output=True, timeout=15)
    except Exception as e:
        # No fetch means no confirmation: job 24 then re-reads the SAME local
        # refs, so its verdict simply stands and the job degrades to the
        # local-only heuristic rather than going quiet. Deliberate — a MISSED
        # delivery stall is the failure this job exists to prevent, and the
        # price of the alternative is at most one extra ping a day.
        return {"fetch_error": repr(e)}
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "5", "--json",
             "number,mergeStateStatus,statusCheckRollup"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        for pr in json.loads(r.stdout or "[]"):
            if pr.get("mergeStateStatus") != "BLOCKED":
                continue
            red = [c.get("name") or c.get("context")
                   for c in (pr.get("statusCheckRollup") or [])
                   if (c.get("conclusion") or c.get("state")) in
                   ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED")]
            return {"pr": pr.get("number"), "check": red[0] if red else None}
    except Exception as e:
        return {"probe_error": repr(e)}
    return None


def _watchdog_vault_purge():
    """Job 29's credential-store sweep (#144) — the injection point so run_once
    never imports the store (or touches a real `~/.claude/secrets/`) in a test.

    The store's TTL used to be enforced only by the NEXT `airuleset.py secret`
    invocation, so the ordinary one-off shape — request a credential, use it
    once, never run the command again — left the value on disk indefinitely.
    A box that already sweeps every 60s is the right place for an expiry that
    must not depend on anyone remembering to run something."""
    from filedrop.vault import purge
    return purge()


def _watchdog_repo_roots():
    """Jobs 27/28's repo enumeration (#137) — every `.git` this box hosts,
    per #138's own corrected lesson that the corpus is `$HOME`, never a
    guessed project directory. `discover_managed_repos` does the actual
    `os.walk`; this is just the injection point so run_once's unit tests
    never touch the real filesystem."""
    from watchdog import discover_managed_repos
    return discover_managed_repos()


def _watchdog_git_fetch(root):
    """Job 28's best-effort ref refresh — same shape as job 24's own probe
    fetch, minus the enrichment half (job 28 needs no blocker lookup, only
    fresh refs). Errors are swallowed by the caller (logged, never raised).

    #172: timeout cut 90s -> 15s. One hung `git fetch` must never eat most
    of the 120s `TimeoutStartSec` unit budget — the repo-batch cap
    (`_repo_sweep_batch`, `AIRULESET_REPO_SWEEP_BATCH`) bounds how many
    repos this costs per sweep; this bounds what ONE of them can cost."""
    import subprocess
    subprocess.run(["git", "-C", root, "fetch", "--quiet", "--no-tags",
                    "origin"], capture_output=True, timeout=15, check=True)


def _watchdog_issue_counts_fetch(repo_label, window_s):
    """Job 27's trailing-window opened/closed count via `gh` (#137).

    Wired HERE, like every other network call, so run_once's unit tests stay
    network-free. `repo_label` is `owner/name` (from `_repo_label`, i.e. the
    remote, never a directory basename). Returns `(opened, closed)` or None
    on any failure — never treated as a stall, per the "never block on
    don't-know" contract every other fetch in this file already follows.

    #172: timeout cut 45s -> 10s per `gh` call (two calls per repo, so 20s
    worst case per repo instead of 90s) — same reasoning as
    `_watchdog_git_fetch`, paired with the repo-batch cap."""
    import subprocess
    import time
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - window_s))
    try:
        opened = subprocess.run(
            ["gh", "issue", "list", "-R", repo_label, "--state", "all",
             "--search", "created:>=%s" % since[:10], "--limit", "1000",
             "--json", "number"],
            capture_output=True, text=True, timeout=10)
        closed = subprocess.run(
            ["gh", "issue", "list", "-R", repo_label, "--state", "closed",
             "--search", "closed:>=%s" % since[:10], "--limit", "1000",
             "--json", "number"],
            capture_output=True, text=True, timeout=10)
        if opened.returncode != 0 or closed.returncode != 0:
            return None
        return (len(json.loads(opened.stdout or "[]")),
                len(json.loads(closed.stdout or "[]")))
    except Exception:
        return None


def cmd_watchdog(args):
    """One poll cycle: scan `claude` tmux panes, auto-`continue` the ones stalled
    on an API error, ping on stall + give-up + on a session waiting on the user,
    (rate-limited) alert when the weekly token limit nears its cap, route an
    owner's Discord REPLY back into the session that asked the ❓, and backstop
    gatekeeper-returned prio:bounce tickets (nudge idle pane / Discord ping),
    RESTARTS any long-lived session still parked on Fable/Opus-4 so it picks
    up the managed default model by construction — never `/model`, which a
    running session's fixed-at-start model list can never accept (#42
    rework of job 12), writes an hourly burn snapshot
    (#37 job 13, the automatic --compare feedback loop), types `/compact`
    into a session whose Stop hook just recorded a completed-ticket report
    once its pane goes genuinely idle (#39 krok 1c job 14), and — for a
    long-lived session that never reports a ticket — `/compact`'s any
    session whose context exceeds 400K tokens once it has sat genuinely
    idle for 20+ minutes with no worker in flight (#39/#43 job 15), and — ONLY
    on the coordinator box (dev1) — merges every managed box's own hourly
    burn-snapshot row into one combined fleet.jsonl row, pinging when the
    observed weekly-%/day pace exceeds budget (#55 job 16), pings a SECOND,
    independent way right after that merge when the completed hour itself
    crosses an absolute/relative/weekly-step threshold (#81 job 19), and — since
    Claude Code snapshots its hook set once at process start and never
    re-reads it — RESTARTS any long-lived session whose settings.json hooks
    block content hash no longer matches the hash it started under, so a
    newly-deployed hook actually takes effect instead of staying inert for
    that session's whole remaining lifetime (#70 job 18, coalesced with job
    12's model restart into one restart when both fire the same sweep), and
    — since an armed `/goal` can die SILENTLY, in the same process, without
    ever writing a `Goal cleared:` marker — cross-checks each session's
    transcript marker against CC's own `◎ /goal` footer indicator and
    re-arms a proven mismatch with the marker's exact bytes, bounded, with
    one ping on give-up (#76 job 20). Driven by the systemd timer.

    Job logs print UNCONDITIONALLY (issue #36) — the systemd unit runs
    `watchdog --once` with NO `--verbose`, so gating the print behind that
    flag meant every job's arm/skip/ping decision was silently lost in
    production; the journal showed only systemd boilerplate, and the
    strip-selection keystroke bug (#36 itself) was undebuggable from it.
    `--verbose` is kept for any additional debug output a caller wants later.

    #172: printing is now INCREMENTAL (`log_fn=`, below) rather than
    "collect the whole list, print it after run_once() returns" — a sweep
    killed mid-way by systemd's TimeoutStartSec=120 never returns at all,
    so the old collect-then-print shape showed NOTHING in the journal for
    the whole 14h the #172 livelock recurred, even though early jobs (incl.
    job 1's 529 auto-resume) had already decided plenty before a later
    job's hung network call ate the rest of the unit's budget.

    #172 (reopened) finding 1: a bare `log_fn=print` still did not fix the
    "prints nothing" symptom in production. `ExecStart` runs with no `-u`
    and no `PYTHONUNBUFFERED=1`, so under systemd stdout is a PIPE and
    CPython block-buffers it (8 KiB) — `print()` WITHOUT `flush=True` never
    actually leaves that buffer, and SIGTERM discards it unread. Measured:
    `print('x')` + SIGTERM 1s later captured nothing; `print('x',
    flush=True)` + the same SIGTERM captured 'x'. `log_fn` below now wraps
    `print` with an explicit flush so a decision line is genuinely durable
    the instant it is logged, not merely "printed" into a buffer a kill can
    still erase."""
    import burn
    from watchdog import (run_once, fetch_usage, fetch_channel_messages,
                          compact_requests_path, goal_templates_path)
    # Job 16 (#55) is coordinator-only: every OTHER managed box already writes
    # its own local hourly row via job 13, so only dev1 fans out over ssh to
    # merge them. `os.uname().nodename` is the same "which host am I" check
    # `burn.local_report()`/`hourly_snapshot()` already use as their own
    # host tag — the machine hostnames ARE the tailscale/MagicDNS names now
    # (machine-identities.md), so this is a plain string compare, no ssh probe.
    fleet_fetch = _watchdog_fleet_fetch if os.uname().nodename == "dev1" else None
    # Job 19 (#81) is coordinator-only for the identical reason job 16 is:
    # every OTHER managed box never writes fleet.jsonl at all (only dev1
    # collects the merged fleet view), so evaluating it anywhere else would
    # just see an empty file. Same host check, reused verbatim.
    burn_alert_enabled = os.uname().nodename == "dev1"
    logs = run_once(dry_run=getattr(args, "dry_run", False), usage_fetch=fetch_usage,
                    discord_fetch=fetch_channel_messages,
                    bounce_fetch=_watchdog_bounce_fetch,
                    gkreq_fetch=_watchdog_gkreq_fetch,
                    # Job 24 (#138) runs on EVERY managed box — a loop whose
                    # merges have stopped is a per-repo failure, not a
                    # coordinator-only one, and the box that hosts the loop
                    # is the one holding the checkout it has to read.
                    delivery_probe=_watchdog_delivery_probe,
                    # Job 25 (#134) runs on EVERY managed box for the same
                    # reason: a ticket that merged without a report is a
                    # per-repo failure, and the box hosting the loop holds
                    # the checkout that proves it.
                    card_probe=_watchdog_card_probe,
                    closed_fetch=_watchdog_closed_fetch,
                    burn_snapshot_path=burn.snapshots_path(),
                    compact_requests_path=compact_requests_path(),
                    fleet_fetch=fleet_fetch, fleet_hosts=REMOTE_HOSTS,
                    fleet_path=burn.fleet_path(),
                    burn_alert_enabled=burn_alert_enabled,
                    # Job 20 (#76) runs on EVERY managed box — a silently
                    # dead /goal is a per-session failure, not a
                    # coordinator-only one (it was montalu's stream that
                    # lost its loop twice in a day).
                    goal_rearm_enabled=True,
                    # …and its STALE-TEMPLATE shape (#64) reads the skill
                    # `install` actually deployed on THIS box — the sub-dev
                    # users have no repo checkout, and the installed copy is
                    # by definition the text their own /autopilot prints.
                    goal_templates_path=str(goal_templates_path()),
                    # Job 21 (#84) likewise runs on EVERY managed box — a
                    # multi-hour turn starves compaction, question delivery
                    # and the keystroke queue of THAT session, wherever it
                    # runs. Detection only, so it never types into a pane.
                    long_turn_enabled=True,
                    # Job 26 (#140) runs on EVERY managed box: a session that
                    # asked for compaction and never got it is a per-session
                    # failure, and both measured incidents were on DIFFERENT
                    # boxes (forestshop@dev1, montalu@subdev) on the same day.
                    # Detection only, so it never types into a pane.
                    compact_stall_enabled=True,
                    # Jobs 27/28 (#137) run on EVERY managed box — both are
                    # per-repo local/gh reads, and each box holds the
                    # checkouts it can actually measure. Self-gated hourly
                    # internally, so wiring them costs nothing on the 59
                    # sweeps out of 60 that skip.
                    vault_purge=_watchdog_vault_purge,
                    repo_roots=_watchdog_repo_roots,
                    issue_counts_fetch=_watchdog_issue_counts_fetch,
                    git_fetch=_watchdog_git_fetch,
                    # #172: print each job's decision line AS IT HAPPENS,
                    # not only from the list run_once() returns — a sweep
                    # killed mid-way (systemd TimeoutStartSec=120) used to
                    # print NOTHING at all, for 14h, because the only print
                    # path was this loop running AFTER run_once() returned.
                    # #172 REOPENED finding 1: bare `print` alone is NOT
                    # enough — under systemd's piped, non-tty stdout, an
                    # unflushed print sits in CPython's 8 KiB buffer and a
                    # SIGTERM discards it unread, reproducing the exact
                    # "prints nothing" symptom this fix exists to kill.
                    log_fn=lambda line: print(line, flush=True))
    del logs   # already streamed via log_fn above; nothing left to print


def cmd_compact_request(args):
    """Record a `/compact` request for a session at a safe ticket boundary
    ("krok 1c — ohraničenie kontextu", #39 follow-up), and ALSO attempt to
    DELIVER it SYNCHRONOUSLY in this SAME process (#65, 2026-07-26). Called
    by the Stop hook `notify-compact-request.sh` the MOMENT a turn's final
    message is a completed-ticket report.

    #65: waiting for watchdog job 14's next ~60s poll loses the race with an
    armed `/goal` loop, which can dispatch the next ticket within seconds —
    long before that poll ever sees the pane idle. So this command records
    the request FIRST (never lost, even if the immediate attempt below
    raises) and then calls `deliver_compact_now`, which resolves the pane
    hosting this EXACT session and, when safe, types `/compact` right now
    (a short send-keys reliably queues even into a BUSY pane, so this does
    not need to wait for idle the way job 14's poll does). On success the
    just-recorded request is cleared immediately — job 14 never needs to
    act on it. On any failure/exception the request stays recorded, exactly
    as before #65, for job 14's polled retry (its own draft-handling,
    #67, covers the one case this synchronous path stays conservative on:
    a genuine unsent draft).

    #71 (2026-07-26 live incident): a REPEAT `--record` for a boundary
    ALREADY delivered — live-observed as the armed goal loop's own
    re-evaluation re-running the Stop hook chain against an UNCHANGED
    `last_assistant_message` several times right after a compaction
    finishes, each fire independently reaching this exact code path — must
    be a complete no-op: no re-record, no second `deliver_compact_now`
    attempt. `--msg-hash` (from the hook, a fingerprint of the triggering
    message) is checked against `compact_already_delivered` BEFORE doing
    anything; on a match, this call returns immediately. On a genuine
    delivery, `mark_compact_delivered` records the hash so any LATER repeat
    (from this same synchronous path, or from job 14) is recognized. A
    blank/absent `--msg-hash` (every pre-#71 caller) never dedupes this
    way — the check and the mark are both no-ops on a blank hash.

    #121 (2026-07-28): `--origin` records WHAT proved the boundary. The Stop
    hook passes nothing (unchanged behavior); the SubagentStop hook
    `notify-compact-subagent-boundary.sh` passes `subagent-stop`, meaning an
    autopilot-worker concluded with zero other live tasks in the session's
    own task registry. That is the durable ticket boundary for a supervisor
    whose work is done by dispatched workers — its own turn ALWAYS ends `⏳`
    (it reports batch N and dispatches batch N+1 in the same turn), so the
    Stop-shaped boundary is structurally unreachable for it.

    #125 (2026-07-28): the printed word on a HANDLED request is whatever
    `deliver_compact_now` itself returns (see its own docstring) --
    "sent"/"claim-queued"/"queued-compact"/"dropped-no-work"/
    "dropped-small-context" -- never the single generic "delivered" this
    command used to print for all five dispositions regardless of what
    actually happened downstream."""
    from watchdog import (record_compact_request, deliver_compact_now,
                          clear_compact_request, compact_already_delivered,
                          mark_compact_delivered)
    if getattr(args, "record", False):
        msg_hash = (getattr(args, "msg_hash", "") or "").strip()
        origin = (getattr(args, "origin", "") or "").strip()
        if compact_already_delivered(args.session, msg_hash):
            sys.stdout.write("dup")
            return
        ok = record_compact_request(args.session, args.cwd, msg_hash=msg_hash,
                                    origin=origin)
        if not ok:
            sys.stdout.write("skip")
            return
        try:
            delivered = deliver_compact_now(args.session, args.cwd, origin=origin)
        except Exception:
            delivered = ""
        if delivered:
            clear_compact_request(args.session)
            if msg_hash:
                mark_compact_delivered(args.session, msg_hash)
            # #125 -- `deliver_compact_now` now returns the REASON word
            # itself (e.g. "sent"/"claim-queued"/"queued-compact"/
            # "dropped-no-work"/"dropped-small-context") instead of a bare
            # `True` that this command used to collapse into one generic
            # "delivered" for every disposition -- print it verbatim. A
            # caller (or test double) that still returns a bare truthy
            # non-string value is treated as the legacy generic "sent",
            # never a crash.
            word = delivered if isinstance(delivered, str) else "sent"
            sys.stdout.write(word)
        else:
            sys.stdout.write("recorded")
        return
    print("compact-request: nothing to do (use --record --session <sid> --cwd <cwd>)",
          file=sys.stderr)
    sys.exit(1)


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
    # simap (airuleset#143, 2026-07-28): phase-1 demo stream that MERGES
    # NOWHERE — no `develop`, no gatekeeper hand-off. fork-no-merge is the
    # existing lowest profile and already expresses exactly that ("never
    # opens/merges a PR, hand-off via comment only") — no new profile needed.
    "simap": "fork-no-merge",
}


AUTHORITY_PROFILES = ("full", "branch-merge", "fork-no-merge")

# The maintainer's GitHub account. Some sub-dev boxes authenticate gh with a
# scoped PAT of THIS account (montalu), so @me search quals there match every
# maintainer-authored ticket — foreign streams leaked into the montalu footer
# (2026-07-20). A shared-account box scopes its slice by the stream LABEL only.
MAINTAINER_GH_LOGIN = "zbynekdrlik"


def _current_user() -> str:
    import getpass

    return getpass.getuser()


def _gh_login(cwd=None):
    """The active gh login for this box, or **None** when the query itself
    FAILED. Cheap single call, run under `_gh_env()` (#181 I-6).

    It used to return `""` on any error, which made "gh api user failed"
    indistinguishable from "this box is not the maintainer" — and the ONE
    caller (`_slice_quals`) treats those two oppositely (#181 I-2). A broken
    `gh` therefore silently produced the own-account 3-qual union: the
    shared-account C2 validation was skipped entirely, and on odoo-erp
    `author:@me` re-opens the 2026-07-20 foreign-stream leak the branch
    exists to prevent. Live-reproduced on david@subdev, where `slice-quals`
    printed all three quals because this query fails, not because the login
    differs. None forces the caller to decide explicitly."""
    import subprocess
    try:
        r = subprocess.run(["gh", "api", "user", "-q", ".login"], cwd=cwd,
                           capture_output=True, text=True, timeout=15,
                           env=_gh_env())
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


class SliceUnresolved(Exception):
    """This box's own gh identity could not be resolved, so "my slice" is
    undefined. Raised by `_slice_quals` instead of falling back to a default
    qual set — every caller must handle it in ITS OWN established way
    (the CLI refuses; the footer and the run-card keep `None`, never a
    wrong number). #181 I-2."""


def _core_search_excl():
    """The full-authority CORE slice's exclusion fragment: every REDUCED-
    authority sub-dev stream's own `stream:<user>` label, so the footer
    (`cmd_tickets_status`), the Discord card (`_notify_run_card`), and the
    `/goal` stop-proof (`cmd_core_quals`) all exclude EXACTLY the same
    sub-dev-owned tickets from a full-authority box's own count — single
    source of truth (#164 / #181 I4).

    Only entries whose profile is NOT `full` are excluded (#181 M-5): a
    hypothetical `full` entry in AUTHORITY_BY_USER is not a sub-dev stream
    at all, and excluding its label would silently remove a whole population
    from every full-authority count."""
    return " ".join("-label:stream:%s" % u
                    for u, profile in sorted(AUTHORITY_BY_USER.items())
                    if profile != "full")


def _slice_quals(user, cwd=None):
    """gh search quals for a reduced-authority stream's OWN ticket slice.
    Own-account streams (david/kvaskodev): assigned ∪ authored ∪ stream label.
    Shared-account boxes (gh login == the maintainer account): the stream
    LABEL alone — @me there matches the whole maintainer-authored backlog.

    Raises `SliceUnresolved` when the gh login cannot be resolved at all
    (#181 I-2) — an unresolvable identity cannot pick between those two
    branches, and guessing either one is a wrong answer on some box."""
    login = _gh_login(cwd)
    if login is None:
        raise SliceUnresolved(
            "gh api user failed — cannot tell whether this box authenticates "
            "as the maintainer account (slice = the stream LABEL alone) or as "
            "its own (assignee ∪ author ∪ label). Refusing to guess: the two "
            "branches disagree on every shared-account box.")
    if login == MAINTAINER_GH_LOGIN:
        return ["label:stream:" + user]
    return ["assignee:@me", "author:@me", "label:stream:" + user]


# An open, non-skip ticket carrying ANY of these labels is an obligation of the
# FULL-authority (core / gatekeeper) box even when it also carries a sub-dev
# `stream:<user>` label: only this box can perform the action they stand for.
#
# `needs-gatekeeper` = a stream→supervisor action request (cross-stream
# protocol rule 7 — by definition nobody else can do it).
#
# `prio:bounce` = the gatekeeper returned this ticket to the SUB-DEV with
# findings that need a fix. The sub-dev fixes it; the sub-dev's own worker (or
# the repo automation, since a read-role stream cannot remove labels) clears
# the label at its done-point, and the gatekeeper clears any leftover at
# re-review. While it is open the full-authority loop HOLDS — review-watch,
# stay alive, re-check hourly, never end the loop (rule 4: "neither side ever
# finishes while the other holds its ball") — so `core-quals --count`
# legitimately never reaching 0 in that state is CORRECT, and is NOT the
# never-stops failure the original ticket rejected. (#181 round 4: this
# comment used to say the RE-REVIEW is this box's ball, which reads as "the
# gatekeeper does the work now" and contradicted both cross-stream rules 2/3
# and the branch-merge template, whose (B) makes "no open prio:bounce for my
# stream" the SUB-DEV's own stop condition.)
#
# `ready-for-review` = a hand-off awaiting this box's review / merge / close
# (rule 4 again, and the fork-no-merge template's "CLOSED by the maintainer").
MAINTAINER_ACTION_LABELS = ("needs-gatekeeper", "prio:bounce", "ready-for-review")


def _obligation_quals():
    """The per-qual search fragments whose UNION is a full-authority box's
    OBLIGATION set: the CORE slice, PLUS every open ticket only this box can
    action regardless of which stream owns it (#181 round 3, CRITICAL).

    `_core_search_excl()` is the FOOTER's *display* partition — "which
    population am I showing". Round 2 reused it as the `/goal` stop-proof's
    *obligation* partition — "which tickets must I finish before I may stop" —
    and those are not the same set. Measured on zbynekdrlik/odoo-erp
    2026-07-30: 83 open non-skip, 40 in the core partition, and 13 tickets
    outside it that only this box can move (#2396 and #2377 are
    `stream:montalu` + `needs-gatekeeper`, plus 11 open `prio:bounce`). The
    gatekeeper would close its 40, the proof would print 0, the loop would
    stop — leaving 13 tickets blocked on the very box that just stopped. That
    is #181 verbatim at a new address.

    This is NOT a revert to the whole-repo count the original ticket rejected
    (that was the never-stops failure): a stream ticket the sub-dev is
    actively working carries none of these labels and still does not block
    this box. Union in Python, one query per qual — gh's `--search` ANDs
    space-joined qualifiers ACROSS qualifier types and cannot OR them.

    Known residual, deliberate: a hand-off is detected by the
    `ready-for-review` LABEL (the same signal the footer's `gk` bucket uses,
    applied by the repo's own subdev-handoff-label workflow), not by the
    `READY-FOR-REVIEW:` comment that is its primary signal. The only
    single-query comment form is `"READY-FOR-REVIEW:" in:comments`, and
    GitHub tokenizes quoted phrases (the 2026-07-24 `in:title` false match),
    so it over-matches — and over-counting the obligation set is the
    never-stops failure again."""
    return [_core_search_excl()] + ["label:" + lb
                                    for lb in MAINTAINER_ACTION_LABELS]


def _repo_root(cwd=None, runner=None):
    """The git repo root for `cwd`, or "" when it cannot be resolved.

    ONE definition, so `cmd_tickets_status` (the footer), `cmd_slice_quals`
    and `cmd_core_quals` all resolve authority — and run gh — against the
    SAME root. #181 I-5: the CLI commands used a bare `resolve_authority()`,
    which reads `Path.cwd()/CLAUDE.md`, while the footer passes the repo
    root; a project marker `airuleset:authority=...` was therefore invisible
    to the CLI whenever the session cwd was a subdirectory, so the "ONE
    definition, resolved per box" claim held only when cwd was exactly the
    repo root.

    Carries #61's fallback: the session cwd may be the PARENT of the actual
    repo (montalu's ~/devel/odoo with the repo at ~/devel/odoo/odoo-erp) and
    `git rev-parse` only ever walks UPWARD. Exactly one `.git` subdirectory
    is descended into; 0 or >1 stays ambiguous — never guess."""
    import subprocess

    cwd = cwd or os.getcwd()

    def _default_run(argv, cd):
        try:
            r = subprocess.run(argv, cwd=cd, capture_output=True, text=True,
                               timeout=20, env=_gh_env())
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    run = runner or _default_run
    root = run(["git", "rev-parse", "--show-toplevel"], cwd)
    if root:
        return root
    try:
        candidates = [p for p in Path(cwd).iterdir()
                      if p.is_dir() and (p / ".git").exists()]
    except OSError:
        candidates = []
    if len(candidates) == 1:
        return run(["git", "rev-parse", "--show-toplevel"], str(candidates[0]))
    return ""


def _ticket_is_stream_labeled(labels):
    """True if `labels` (a gh --json labels value: a list of {'name': ...}
    dicts, or None/malformed) carries a stream:<user> label for any
    AUTHORITY_BY_USER stream — i.e. this ticket belongs to a sub-dev stream's
    slice, not the full-authority CORE slice (#164 defect 2: the D/T progress
    counter must not let a stream ticket's card inflate a core-scoped 'done'
    the core-scoped 'remaining' can't back)."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    return any(("stream:%s" % u) in names for u in AUTHORITY_BY_USER)


def _authority_marker(cwd=None):
    """Read an `<!-- airuleset:authority=<profile> -->` override from the project
    CLAUDE.md (cwd-relative), or None. Lets a project raise/lower a stream's default
    authority (e.g. grant `full` to a montalu repo). The single place the marker is
    parsed, so the CLI, the autopilot skill, and the issue-close guard hook agree.

    The marker MUST be the HTML-COMMENT form (exactly like `<!-- airuleset:merge=manual
    -->`) — a bare/prose mention of `airuleset:authority=…` is deliberately NOT honored:
    an unanchored match could let a documentation sentence naming a profile silently
    ELEVATE a fork-no-merge stream to `full` and disable the issue-close guard (the
    UNSAFE direction). If several comment markers exist (a misconfig) the LAST one wins,
    so an operative marker placed after any example cannot be shadowed."""
    import re
    try:
        p = (Path(cwd) if cwd else Path.cwd()) / "CLAUDE.md"
        if p.is_file():
            hits = re.findall(r"<!--\s*airuleset:authority=([a-z-]+)\s*-->",
                              p.read_text(errors="ignore"))
            for tok in reversed(hits):
                if tok in AUTHORITY_PROFILES:
                    return tok
    except OSError:
        return None
    return None


def resolve_authority(cwd=None) -> str:
    """The current stream's autopilot authority profile: a project CLAUDE.md
    `airuleset:authority=<profile>` marker (cwd-relative) OVERRIDES the per-user
    default map. This makes `airuleset.py authority` authoritative for both the
    autopilot skill and the `block-fork-no-merge-issue-close` hook (single source
    of truth) — cmd_authority's explain text has always PROMISED this override; it
    is now actually honored, not just documented."""
    return _authority_marker(cwd) or AUTHORITY_BY_USER.get(_current_user(), "full")


def cmd_authority(args):
    """Print the current stream's autopilot authority profile (one word)."""
    profile = resolve_authority()
    print(profile)
    if getattr(args, "explain", False):
        user = _current_user()
        print(f"user={user} (map: {AUTHORITY_BY_USER.get(user, 'unmapped -> full')}); "
              f"a project CLAUDE.md marker airuleset:authority=<profile> overrides this.")


def _label_exists_on_repo(label, cwd=None):
    """True if `label` is a DEFINED label on the current repo (gh label list
    --search), False if confirmed absent, None if the query itself failed —
    an unreachable/erroring gh is NOT evidence the label is missing (#181
    C2, round 2)."""
    raw = _gh_out("label", "list", "--search", label, "--json", "name",
                  "-L", "50", cwd=cwd)
    try:
        names = {(x or {}).get("name") for x in json.loads(raw)}
    except (ValueError, TypeError):
        return None
    return label in names


def _search_index_healthy(cwd=None):
    """Does gh's SEARCH path demonstrably work for this identity/repo?
    True = yes; False = demonstrably not (or unprovable); None = the repo
    genuinely has no open issues at all, so an empty slice is trivially
    correct.

    #181 I-1: round 2's cross-check ran `involves:@me` and only required the
    response to PARSE — but `[]` parses, and "search returns nothing
    everywhere" IS `[]`, i.e. the exact state the check claimed to detect was
    the state it accepted. The reviewer executed it: login zbynekdrlik, user
    montalu, label present, every query `[]` → rc 0, stdout `0`. A real
    cross-check must ASSERT NON-EMPTY on a query that cannot legitimately be
    empty.

    A SORT-ONLY search (`sort:created-desc`) is that query: it carries no
    filtering qualifier, so it matches every open issue in the repo. If it
    comes back empty the repo may genuinely have none — settled by the REST
    listing path (`gh issue list` with no `--search`, a different gh code
    path that does not touch the search index). REST sees issues but search
    sees none ⇒ the search index is not answering ⇒ refuse."""
    probe = _gh_out("issue", "list", "--state", "open", "--search",
                    "sort:created-desc", "-L", "1", "--json", "number", cwd=cwd)
    try:
        rows = json.loads(probe)
    except (ValueError, TypeError):
        return False
    if not isinstance(rows, list):
        return False
    if rows:
        return True
    rest = _gh_out("issue", "list", "--state", "open", "-L", "1",
                   "--json", "number", cwd=cwd)
    try:
        rest_rows = json.loads(rest)
    except (ValueError, TypeError):
        return False
    if not isinstance(rest_rows, list):
        return False
    return None if not rest_rows else False


def _union_open_issues(quals, base, cwd=None):
    """Run ONE `gh issue list --search` per qual and union the rows by issue
    number, returning `(rows_by_number, failed)`.

    Per-qual queries are not an optimisation choice: gh's `--search` ANDs
    space-joined qualifiers across qualifier types and cannot OR them, so a
    caller that needs a UNION (assignee ∪ author ∪ label; core ∪ the
    maintainer-action labels) must union client-side. `failed` is True if ANY
    query failed to parse — a gh error is never an empty result.

    `labels` is fetched alongside (#181 round 4): one extra field on queries
    already being made, and the thing that lets `_print_issue_rows` mark every
    row with what THIS box may DO with it. Without it the mandated backlog
    SELECTION source emitted no not-mine-to-implement discriminator at all, so
    the only thing between the FULL template's bounce-lane seed ("the OLDEST
    open prio:bounce ticket" — live on odoo-erp that is #2150, `stream:david`)
    and a gatekeeper writing code on a sub-dev's ticket was a prose clause the
    worker may never have loaded."""
    seen, failed = {}, False
    for qual in quals:
        search = (base + " " + qual).strip() if qual else base
        raw = _gh_out("issue", "list", "--state", "open", "--search", search,
                      "-L", "1000", "--json", "number,title,createdAt,labels",
                      cwd=cwd, timeout=20)
        try:
            for x in json.loads(raw):
                seen[x["number"]] = x
        except (ValueError, TypeError, KeyError):
            failed = True
    return seen, failed


ROW_ACTION_ONLY = "action-only"
ROW_IMPLEMENT = "implement"


def _stream_owner_of(labels):
    """The REDUCED-authority stream that owns this ticket, or "" — read from a
    gh `--json labels` value (a list of {'name': ...} dicts, or None).

    Only non-`full` AUTHORITY_BY_USER entries count, the same filter
    `_core_search_excl()` applies (#181 M-5): a hypothetical `full` entry is
    not a sub-dev stream, and treating its label as ownership would wrongly
    mark its tickets untouchable."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    for user, profile in sorted(AUTHORITY_BY_USER.items()):
        if profile != "full" and ("stream:%s" % user) in names:
            return user
    return ""


def _row_action(row, own_stream=None):
    """What THIS box may do with an issue row: `action-only` or `implement`.

    A ticket owned by a stream OTHER than this box's is in the obligation set
    because only this box can REVIEW / MERGE / CLOSE / UNBLOCK it — never
    because this box should write its code. The discriminator is deliberately
    relative to this box (`own_stream`), not absolute: a reduced-authority
    stream's own `stream:<me>` tickets ARE its to implement, and an absolute
    "carries any stream label" rule would mark every row of its own slice
    untouchable.

    A row with NO `labels` key at all is UNDETERMINABLE, not "unlabelled", and
    takes the conservative side — the same `"labels" in view` discrimination
    `_notify_run_card` already makes, and for the same reason (#181 M11: a
    failed lookup read as the negative case silently restored a pre-existing
    wrong behaviour). The two errors here are not symmetric: a sub-dev's
    ticket printed `implement` invites this box to write code on a foreign
    stream's ticket — the exact harm the column exists to prevent — while a
    core ticket printed `action-only` merely stalls visibly. `labels: []` is a
    genuinely unlabelled core ticket and stays `implement`."""
    if not isinstance(row, dict) or "labels" not in row:
        return ROW_ACTION_ONLY
    owner = _stream_owner_of(row.get("labels"))
    if owner and owner != (own_stream or ""):
        return ROW_ACTION_ONLY
    return ROW_IMPLEMENT


def _print_issue_rows(rows, own_stream=None):
    """`number<TAB>createdAt<TAB>action<TAB>title`, OLDEST first (the bounce
    lane picks the oldest — no client-side sort needed downstream).

    The action column is third, ahead of the title, so a title containing a
    tab cannot shift it. It is the #181-round-4 fix for the SELECTION source
    emitting no ownership discriminator: `action-only` = only this box can act
    on it and it must never write its code; `implement` = ordinary work."""
    for n in sorted(rows, key=lambda k: rows[k].get("createdAt") or ""):
        row = rows[n]
        print("%s\t%s\t%s\t%s" % (n, row.get("createdAt") or "",
                                  _row_action(row, own_stream),
                                  row.get("title") or ""))


def _refuse_unless_empty_is_trustworthy(cmd, quals, cwd=None):
    """Refuse (stderr + non-zero exit, nothing on stdout) unless an EMPTY
    search-derived result is TRUSTWORTHY. Shared by BOTH `/goal` stop-proof
    commands, called at the identical point.

    #181 round 4, CRITICAL. `_search_index_healthy()` (round 3) is the right
    guard for this defect class, but it was installed as an extra validation
    for ONE caller's zero rather than as a precondition on trusting ANY zero
    derived from the GitHub issue SEARCH index — one call site, nested inside
    `cmd_slice_quals` behind `len(quals) == 1 and quals[0].startswith("label:")`,
    the SHARED-account shape. Two paths walked straight past it, both
    reproduced live on dev1 2026-07-30 against the shipped code:

      * `cmd_core_quals` never called it at all. In a checkout whose `origin`
        still points at the pre-rename name (GitHub's issue SEARCH index does
        not follow a repo rename; the REST/repository-listing path does):
        REST 110 open issues, every `--search` 0, `core-quals --count` -> `0`
        with rc 0. The gatekeeper pastes that 0, writes the mandated BACKLOG
        EMPTY line, and stops with the whole backlog outstanding.
      * `cmd_slice_quals` on an OWN-account stream. `_slice_quals("david")` is
        `['assignee:@me', 'author:@me', 'label:stream:david']`, so
        `len(quals) == 1` is False and the guard was skipped in the very
        command round 3 fixed: stdout `0`, no SystemExit.

    The rename is only the cheapest trigger; ANY state where search answers
    empty while REST does not reaches the same line. Rounds 1-3 each moved the
    guard one call frame outward instead of making the refusal a property of
    the RESULT — "this zero came out of the search index, and nothing has
    shown the search index is answering" — which is why the class survived
    three fixes. One helper, two callers, one contract: a third stop-proof
    command gets it by calling this.

    Runs ONLY when the union is empty; a non-empty union is itself proof the
    index answers, so the healthy path costs no extra gh call."""
    if len(quals) == 1 and quals[0].startswith("label:"):
        # C2 (round 2) — a shared-account slice's ONLY signal is one label,
        # and a forgotten/never-created label makes gh search return `[]` with
        # exit 0 for a query that can never match anything.
        label_name = quals[0].split(":", 1)[1]     # "label:stream:x" -> "stream:x"
        exists = _label_exists_on_repo(label_name, cwd=cwd)
        if exists is not True:
            print(
                "%s: cannot confirm label '%s' exists on this repo (%s) — a "
                "single-label slice of 0 resting on an unconfirmed label is "
                "UNRELIABLE. Refusing rather than reporting it (#181 C2)."
                % (cmd, label_name,
                   "not found" if exists is False else "the check itself failed"),
                file=sys.stderr)
            sys.exit(1)
    healthy = _search_index_healthy(cwd=cwd)
    if healthy is False:
        print(
            "%s: gh's SEARCH path is not answering for this identity/repo (a "
            "sort-only search that must match every open issue came back "
            "empty, or the probe itself failed, while the repository listing "
            "path does show open issues) — an empty result here is NOT "
            "evidence of an empty backlog. Refusing (#181 round 4)." % cmd,
            file=sys.stderr)
        sys.exit(1)
    # healthy is True (search demonstrably works) or None (the repo has no
    # open issues at all, so an empty result is trivially correct).


HANDOFF_LABEL_WORKFLOW_HINT = "handoff"


def _handoff_label_mechanism_health(cwd=None):
    """Is the mechanism the `ready-for-review` arm rests on actually working?
    Returns `(state, detail)` with state in `ok` / `broken` / `unknown` /
    `n/a`.

    #181 round 4. The obligation set detects an outstanding sub-dev hand-off
    by the `ready-for-review` LABEL, and a read-role collaborator gets a 403
    adding that label itself — so the arm depends ENTIRELY on the repo's own
    hand-off-label workflow. Measured on zbynekdrlik/odoo-erp 2026-07-30: the
    workflow is `active` but 23 of its last 30 runs FAILED (the 5 newest all
    failed, job `label`, startup-shaped), and the repo carries 0 open
    `ready-for-review` issues. So the arm contributes a zero while the only
    thing that can produce a non-zero is failing three runs in four — this
    ticket's own failure mode by a different road. Filed as odoo-erp #2584.

    A miss is made DETECTABLE, never guessed: the alternative is a comment
    query (`"READY-FOR-REVIEW:" in:comments`), and GitHub tokenizes quoted
    phrases so that over-matches — over-counting the obligation set is the
    never-stops failure the original ticket rejected.

    `n/a` when the repo is not enrolled in the gatekeeper<->sub-dev flow, or
    when enrollment itself cannot be determined: enrollment is a static local
    fact, and if it is unknowable then nothing here depends on the workflow.
    `unknown` (which the caller treats like `broken`, exactly as C2 treats a
    failed label probe) when the repo IS enrolled but the health probe fails —
    an unreachable gh is not evidence the mechanism is fine."""
    try:
        import notify
        from watchdog import _CROSS_STREAM_REPOS as enrolled
    except Exception:
        return ("n/a", "the cross-stream registry is not resolvable here")
    try:
        name = notify.repo_name_for(cwd or os.getcwd())
    except Exception:
        name = ""
    if not name:
        return ("n/a", "the repo name is not resolvable from origin")
    if name not in enrolled:
        return ("n/a", "%s is not enrolled in the cross-stream flow" % name)

    raw = _gh_out("workflow", "list", "--all", "--json", "name,state,path",
                  cwd=cwd, timeout=20)
    try:
        flows = json.loads(raw)
    except (ValueError, TypeError):
        return ("unknown", "`gh workflow list` failed on %s" % name)
    if not isinstance(flows, list):
        return ("unknown", "`gh workflow list` returned no list on %s" % name)
    match = [f for f in flows if isinstance(f, dict)
             and HANDOFF_LABEL_WORKFLOW_HINT in str(f.get("path") or "").lower()]
    if not match:
        return ("broken",
                "%s is enrolled in the cross-stream flow but carries no "
                "hand-off-label workflow, so nothing can label a hand-off"
                % name)
    inactive = [f for f in match if f.get("state") != "active"]
    if inactive:
        return ("broken", "the hand-off-label workflow is %r, not active"
                % (inactive[0].get("state"),))

    path = str(match[0].get("path") or "")
    raw = _gh_out("run", "list", "-w", path, "-L", "1",
                  "--json", "conclusion,status", cwd=cwd, timeout=20)
    try:
        runs = json.loads(raw)
    except (ValueError, TypeError):
        return ("unknown", "`gh run list` failed for %s" % path)
    if not isinstance(runs, list):
        return ("unknown", "`gh run list` returned no list for %s" % path)
    if runs and isinstance(runs[0], dict) and runs[0].get("conclusion") == "failure":
        return ("broken", "the newest %s run FAILED" % path)
    return ("ok", path)


def cmd_slice_quals(args):
    """THE single definition of "my slice" (#181) — reused verbatim by the
    reduced-authority `/goal` stop-proof templates in skills/autopilot/SKILL.md
    instead of each one hand-rolling its own `--search` string.

    Before this command existed, the templates hardcoded `--assignee @me`,
    which silently resolves to `0` on a SHARED-gh-account box (montalu/marek/
    simap — see `_slice_quals`' own docstring): `@me` there is the maintainer
    account, matching nothing assigned, so the /goal loop declared the
    backlog empty with real labelled work open. `_slice_quals()` already
    fixed this for the footer/Discord-card paths via a LIST of quals unioned
    in Python — but gh's own `--search` syntax ANDs space-joined qualifiers,
    it cannot OR them, so a caller cannot just embed that list as one
    `--search` fragment (that would silently switch an own-account stream's
    3-qual union into an intersection). This command runs the SAME per-qual
    queries + Python-side union already used by `_notify_run_card`/
    `cmd_tickets_status`, and prints only the RESULT — never a raw fragment a
    template could misuse.

    C1 (round 2, live-confirmed on dev1): this command used to build quals
    unconditionally from `_current_user()`, never consulting
    `resolve_authority()` — on a FULL-authority box (no stream at all) that
    silently built `label:stream:<linux-user>`, which matches nothing, so
    `--count` printed a clean `0` with real open work sitting untouched. It
    now REFUSES outright when this box does not resolve to a
    reduced-authority profile — never a printed count.

    C2 (round 2): on a SHARED-gh-account box (montalu/marek/simap) the
    slice is `label:stream:<user>` ALONE — a forgotten/never-created label
    makes gh search return `[]` with exit 0 for a query that can never
    match anything. A ZERO result from a single-label (shared-account)
    slice is now VALIDATED before being trusted: the label must be
    confirmed to exist on the repo, AND an `involves:@me` cross-check query
    must itself succeed (proving gh search genuinely works for this
    identity/repo, not just silently returning nothing everywhere) —
    refusing rather than trusting an unconfirmed zero.

    --count: prints an integer (0 = slice empty — the /goal stop-proof).
    --list:  prints `number<TAB>createdAt<TAB>action<TAB>title`, one per open
             non-skip issue in the slice, OLDEST first (the bounce lane picks
             the oldest — no client-side sort needed). `action` is relative to
             THIS box, so a stream's own `stream:<me>` tickets read
             `implement` (#181 round 4).
    --extra <qual>: ANDs one extra search qualifier onto every per-qual query
             (e.g. `label:prio:bounce` for the bounce-lane seed).
    No flag: prints each qual defining this box's slice, one per line
             (informational).

    A gh query failure prints to stderr and exits non-zero — NEVER prints `0`
    on failure, which would be exactly the false-empty bug this exists to
    fix.

    Authority, the slice quals and every gh query resolve against the REPO
    ROOT, not the process cwd (#181 I-5) — a project CLAUDE.md marker
    `airuleset:authority=...` was invisible to this command whenever the
    session cwd was a subdirectory, while the footer saw it, so the two
    consumers of "THE one definition" could disagree about which profile the
    box was even running."""
    root = _repo_root() or None
    authority = resolve_authority(cwd=root)
    if authority == "full":
        print(
            "slice-quals: this box resolves to FULL authority — there is no "
            "stream slice to report here. Refusing rather than printing a "
            "plausible-looking 0 (this command answers 'my slice' for a "
            "reduced-authority branch-merge/fork-no-merge stream only; "
            "#181 C1).", file=sys.stderr)
        sys.exit(1)
    user = _current_user()
    try:
        quals = _slice_quals(user, cwd=root)
    except SliceUnresolved as exc:
        print("slice-quals: %s" % exc, file=sys.stderr)
        sys.exit(1)
    want_count = getattr(args, "count", False)
    want_list = getattr(args, "list", False)
    if not (want_count or want_list):
        for q in quals:
            print(q)
        return

    extra = getattr(args, "extra", None)
    base = "-label:autopilot-skip" + ((" " + extra) if extra else "")
    # -L 1000, matching core-quals (#181 M-2): a single population can only
    # be UNDER-counted by a clamp, never zeroed — but the documented
    # "0 = slice empty" contract must not silently cap either.
    seen, failed = _union_open_issues(quals, base, cwd=root)
    if failed:
        print("slice-quals: a gh query failed — this is NOT a reliable 0",
              file=sys.stderr)
        sys.exit(1)
    if not seen:
        # Round 4: the validation is no longer nested behind the SHARED-account
        # shape. An own-account stream has THREE quals, so `len(quals) == 1`
        # was False and this command skipped its own guard entirely — a false
        # SLICE EMPTY for david@subdev whenever the search index is not
        # answering. One shared helper, identical contract in both commands.
        _refuse_unless_empty_is_trustworthy("slice-quals", quals, cwd=root)
    if want_count:
        print(len(seen))
        return
    _print_issue_rows(seen, own_stream=user)


def cmd_core_quals(args):
    """The full-authority box's OBLIGATION set: every open, non-skip issue
    THIS box must action before its `/goal` loop may stop (#181, round 3).

    That is the CORE slice — the backlog minus every reduced-authority
    stream's own `stream:<user>` tickets, the SAME exclusion the footer
    (`cmd_tickets_status`) and the Discord run-card (`_notify_run_card`) use
    — UNIONED with every open ticket carrying a MAINTAINER_ACTION_LABELS
    label, whatever stream owns it. See `_obligation_quals()` for why the
    core partition alone is the wrong set (it excluded odoo-erp #2396/#2377,
    `stream:montalu` + `needs-gatekeeper`, which only this box can move) and
    why this is not a revert to the whole-repo count.

    NOTE, deliberately: this is NOT the same number the footer renders as
    `Issues N core`. The footer answers a DISPLAY question ("which population
    am I showing, and how much is hidden behind `streamy M`"); this answers an
    OBLIGATION question ("what must I finish before I may stop"). Round 2's
    I3 finding settled the same shape for `slice-quals` vs the footer's `gk`
    partition: document the deliberate difference and lock it with a
    regression test, never force two different questions to the same number.

    --count: prints an integer (0 = nothing left for this box to action).
    --list:  prints `number<TAB>createdAt<TAB>action<TAB>title`, OLDEST first —
             so the skill's backlog SELECTION and its stop-proof read the same
             set. The `action` column is `action-only` for a ticket a SUB-DEV
             stream owns (review / merge / close / unblock it, NEVER write its
             code) and `implement` otherwise (#181 round 4): the discriminator
             lives in the data the worker reads, not in a prose clause it may
             never have loaded.
    --extra <qual>: ANDs one extra search qualifier onto every per-qual query
             (e.g. `label:prio:bounce` for the bounce-lane seed), mirroring
             `slice-quals`. Without it the full-authority bounce seed went
             through a raw `gh issue list`, so the single highest-priority
             SELECTION path was the one path with neither this command's guard
             nor its ownership column — while the oldest open `prio:bounce`
             ticket on odoo-erp is #2150, `stream:david`.
    No flag: prints each qual whose union defines the obligation set.

    A gh query failure prints to stderr and exits non-zero — NEVER prints a
    number on failure (mirrors `slice-quals`'s own contract), and an EMPTY
    result is refused unless it is demonstrably trustworthy — see
    `_refuse_unless_empty_is_trustworthy` (#181 round 4, CRITICAL: this
    command never consulted the search-index guard at all, so a repo whose
    search index answers empty while its REST listing does not produced a
    clean stop-proof `0` with the whole backlog open)."""
    root = _repo_root() or None
    authority = resolve_authority(cwd=root)
    if authority != "full":
        # I-3: C1's fix, applied in the mirror direction. `slice-quals`
        # correctly refuses on a full box; this one answered on ANY box, so
        # run on montalu it printed a number that is neither that box's slice
        # nor a valid stop-proof for it.
        print(
            "core-quals: this box resolves to %s authority — the core/"
            "obligation slice is a FULL-authority (core / gatekeeper) "
            "question. Use `slice-quals` here. Refusing rather than printing "
            "a plausible-looking number (#181 I-3)." % authority,
            file=sys.stderr)
        sys.exit(1)

    quals = _obligation_quals()
    want_count = getattr(args, "count", False)
    want_list = getattr(args, "list", False)
    if not (want_count or want_list):
        for q in quals:
            print(q)
        return

    extra = getattr(args, "extra", None)
    base = "-label:autopilot-skip" + ((" " + extra) if extra else "")
    seen, failed = _union_open_issues(quals, base, cwd=root)
    if failed:
        print("core-quals: a gh query failed — this is NOT a reliable 0",
              file=sys.stderr)
        sys.exit(1)
    if not seen:
        _refuse_unless_empty_is_trustworthy("core-quals", quals, cwd=root)
    if not seen and not extra:
        # The `ready-for-review` arm of this set rests ENTIRELY on the repo's
        # own hand-off-label workflow (a read-role stream gets a 403 adding
        # the label itself). A zero that rests on a mechanism which may have
        # MISSED a hand-off is not evidence — same shape as C2's "validate
        # the evidence's own existence before trusting its absence of hits".
        #
        # `not extra` is load-bearing: with a filter (`--extra
        # "label:prio:bounce"`, the bounce seed) the question asked is "any
        # open bounce ticket?", and "none" is an ordinary answer that does not
        # depend on hand-off labels at all. Gating that would refuse a
        # legitimate result and spin the loop forever on the one repo the
        # cross-stream flow runs on — the mirror of this ticket's own bug, not
        # a safer version of it. The arm belongs to the UNFILTERED obligation
        # set, which is what the stop-proof reads. (The search-index guard
        # above stays unconditional: a dead index makes a FILTERED answer just
        # as meaningless as an unfiltered one.)
        health, detail = _handoff_label_mechanism_health(cwd=root)
        if health not in ("ok", "n/a"):
            print(
                "core-quals: the obligation set is empty, but the "
                "`ready-for-review` arm rests on this repo's hand-off-label "
                "workflow and that mechanism is %s (%s) — a hand-off can be "
                "outstanding with no label, so this 0 is NOT evidence. "
                "Refusing (#181 round 4)." % (health, detail),
                file=sys.stderr)
            sys.exit(1)
    if want_count:
        print(len(seen))
        return
    # own_stream=None: a full-authority box owns no stream, so EVERY
    # stream-labelled row in its obligation set is action-only.
    _print_issue_rows(seen, own_stream=None)


UPLOAD_LOG_DIR_ENV = "AIRULESET_UPLOAD_LOG_DIR"


def _upload_log_path(port):
    """Where `upload` writes an endpoint's log — under the USER'S OWN tree.

    It used to be `/tmp/airuleset-upload-<port>.log`: a world-shared directory
    with the port as the only key, so the FIRST user to run on a port owned that
    filename for every other user on the box. dev1 still carries
    `-rw-rw-r-- montalu /tmp/airuleset-upload-8811.log`, and `--port 8811` as
    anyone else died with an unhandled `PermissionError` instead of a diagnosis
    (#115) — from the one CLI whose whole job is to be reachable the moment
    someone needs to hand a file over. Two users have two $HOMEs, so this cannot
    collide by construction rather than by luck of the port. AIRULESET_UPLOAD_LOG_DIR
    relocates it (tests — the same escape hatch filedrop gives itself with
    FILEDROP_DIR); the name stays port-keyed over a 21-port range, so the file set
    is bounded and reused by append, never accumulating."""
    base = os.environ.get(UPLOAD_LOG_DIR_ENV) or (Path.home() / ".claude" / "upload-logs")
    return Path(base) / f"upload-{port}.log"


def _pick_free_port(ips, ports):
    """The first port in `ports` that `ips` can actually BIND — None if none can.

    The pre-#115 scan probed `connect_ex(("127.0.0.1", cand))`, but
    `filedrop._is_private` EXCLUDES loopback and `upload_server.py` binds exactly
    `bind_ips()`, deliberately never 0.0.0.0 (a WRITE endpoint on a box that may
    have a public IP). The probe's address and the server's addresses were
    therefore disjoint BY CONSTRUCTION: a live endpoint answers on none of the
    addresses the scan asked about, so the scan handed out an occupied port
    (observed on dev1: five listeners on :8799, scan picked 8799, and the second
    endpoint then failed to bind anything).

    Binding the very addresses the server is about to bind asks the server's own
    question. Only EADDRINUSE rejects a candidate — any other error
    (EADDRNOTAVAIL from a stale or departed interface) is tolerated, because
    upload_server.py SKIPS such an address rather than dying on it and needs only
    one success; treating that as "occupied" would let one stale IP reject every
    candidate on a box that serves fine. SO_REUSEADDR mirrors
    HTTPServer.allow_reuse_address, so the probe's verdict is the server's."""
    import errno
    import socket

    for port in ports:
        for ip in ips:
            s = socket.socket()
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((ip, port))
            except OSError as e:
                if e.errno == errno.EADDRINUSE:
                    break
            finally:
                s.close()
        else:
            return port
    return None


def cmd_upload(args):
    """Stand up a web UPLOAD endpoint the user opens in their own browser.

    The user works over SSH with NO local filesystem access to any managed box —
    receiving a file FROM them is ALWAYS a drag-drop web URL, NEVER an scp/sftp
    ask (modules/core/receive-files-via-upload-url.md; incident david@gk
    2026-07-10). Spawns filedrop/upload_server.py DETACHED with an unguessable
    token, binds every PRIVATE interface (tailscale + LAN — bind_ips(); never the
    public IP, since this is a WRITE endpoint) and advertises ONE URL per interface
    so the user has a working link whether they are on tailscale or the LAN. Each
    URL is verified to answer 200 BEFORE printing (no-localhost-urls); the endpoint
    self-expires after --ttl seconds."""
    import secrets as _secrets
    import subprocess
    import time
    import urllib.request

    from filedrop import bind_ips

    dest = Path(getattr(args, "dir", None) or (Path.home() / "uploads")).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    ttl = int(getattr(args, "ttl", None) or 7200)

    # Bind + advertise every private interface (tailscale first, then LAN). Computed
    # FRESH here (unsandboxed) so it always reflects the current network.
    ips = bind_ips()

    port = int(getattr(args, "port", None) or 0) or None
    if port is None:
        # Probe the addresses the server is ABOUT TO BIND, not loopback (#115).
        port = _pick_free_port(ips, range(8799, 8820))
        if port is None:
            print("upload: no free port in 8799-8819", file=sys.stderr)
            sys.exit(1)

    token = _secrets.token_urlsafe(12)
    log = _upload_log_path(port)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        lf = open(log, "ab")
    except OSError as e:
        # A diagnosis, never the bare traceback #115 was filed for.
        print(f"upload: cannot open log {log}: {e}", file=sys.stderr)
        sys.exit(1)
    with lf:
        subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "upload_server.py"),
             token, str(port), ",".join(ips), str(dest), str(ttl)],
            stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            start_new_session=True)

    def _live(u):
        try:
            return urllib.request.urlopen(u, timeout=2).status == 200
        except OSError:
            return False

    urls = [f"http://{ip}:{port}/{token}/" for ip in ips]
    # Wait for ANY interface to come up (no-localhost-urls) — NOT urls[0]
    # specifically: the upload_server skips an interface that fails to bind (a
    # transiently-down tailscale while the LAN binds fine), so gating on the first
    # URL alone would abort + orphan a working endpoint on another interface.
    for _ in range(20):
        if any(_live(u) for u in urls):
            break
        time.sleep(0.25)
    else:
        print(f"upload: endpoint failed to come up — see {log}", file=sys.stderr)
        sys.exit(1)
    reachable = [u for u in urls if _live(u)] or [urls[0]]
    for u in reachable:   # one URL per interface — open whichever your network reaches
        print(u)
    print(f"dest={dest}  ttl={ttl}s  log={log}")
    print("Otvor ktorúkoľvek URL v prehliadači (podľa siete). Po nahratí over: grep SAVED "
          + str(log))


SECRET_ACTIONS = ("request", "status", "list", "exec", "forget", "purge")
# Both lifetimes are CLAMPED, not merely defaulted. `int(args.ttl or DEFAULT)`
# let a negative value through (0 is falsy and fell back; -1 is truthy), and the
# server armed its shutdown timer only for a positive TTL — so `--ttl -1` gave a
# credential-receiving endpoint with no timer at all, alive until reboot, while
# its store record was already expired and `status` reported `absent`.
SECRET_MIN_TTL_S, SECRET_MAX_TTL_S = 30, 3600
SECRET_MIN_KEEP_S, SECRET_MAX_KEEP_S = 60, 24 * 3600


def _secret_clamp_ttl(value):
    return max(SECRET_MIN_TTL_S, min(int(value), SECRET_MAX_TTL_S))


def _secret_clamp_keep(value):
    return max(SECRET_MIN_KEEP_S, min(int(value), SECRET_MAX_KEEP_S))
# A distinct range from `upload`'s 8799-8819, so the two endpoint kinds can
# never be confused for one another by a port alone.
SECRET_PORTS = range(8830, 8850)


def _secret_bindable(ip):
    """May a credential endpoint listen here?

    The CLI-side half of the two independent checks (the other is
    `filedrop/vault_server.py:is_private`, which re-validates its own argv). A
    public address is refused outright — the token in the path is the endpoint's
    only auth, and on a box with a public IP (the gatekeeper VPS) an open
    credential endpoint on the internet is not a recoverable mistake. Loopback
    is allowed here even though `filedrop._is_private` drops it for the file
    endpoints: it cannot leave the box, so it is strictly more private than
    tailscale — it is simply not reachable BY the user, which the URL print
    makes obvious.
    """
    from filedrop import _is_private
    return bool(_is_private(ip) or (isinstance(ip, str) and ip.startswith("127.")))


# Interfaces whose traffic is encrypted BEFORE it leaves the box, so a plain
# HTTP endpoint on them is not actually in the clear. Deliberately does NOT
# include `tun` — `tunl0` is IPIP, a tunnel with no encryption at all, and the
# only safe direction for this label is to under-claim.
_SECRET_ENCRYPTED_IFACE = ("tailscale", "wg", "wireguard", "zt")


def _secret_iface_for(ip):
    """The interface `ip` is configured on, or None."""
    from filedrop import _iface_ips
    for cand, ifname in _iface_ips():
        if cand == ip:
            return ifname
    return None


def _secret_opener():
    """A urllib opener with proxies DISABLED.

    The default opener reads $http_proxy/$https_proxy, so the liveness probe
    was sending its URL to a proxy host — and a proxy error page returning 200
    made a dead endpoint look live. This probe only ever targets an address on
    this very machine; a proxy is never right for it.
    """
    import urllib.request as _u
    return _u.build_opener(_u.ProxyHandler({}))


def _secret_health_url(ip, port):
    """The liveness probe's URL — deliberately token-free.

    The probe used to GET the token URL, which hands the endpoint's only
    authentication to whatever is listening on that port. If another local
    user's process won the pick-a-port race, that is their listener.
    """
    return "http://%s:%d/healthz" % (ip, port)


def _secret_probe_urls(ips, port):
    """Every address's liveness URL — the list the readiness loop consumes.

    A named helper rather than an inline comprehension because the inline one
    is exactly what silently kept probing the TOKEN urls after the health route
    was added: nothing could assert on it.
    """
    return [_secret_health_url(ip, port) for ip in ips]


def _secret_is_encrypted(ip, iface=None):
    """True when traffic to `ip` is encrypted before it leaves the box.

    Tailscale by CIDR, loopback (it never leaves at all), and any interface
    whose name starts with a known encrypted-overlay prefix. Everything else is
    genuine cleartext, whatever its address range says — the ranges do not
    carry the answer, since wg0 and a zerotier both look like plain LAN.
    """
    from filedrop import _is_tailscale
    if _is_tailscale(ip) or str(ip).startswith("127."):
        return True
    if iface is None:
        iface = _secret_iface_for(ip)
    return bool(iface and str(iface).lower().startswith(_SECRET_ENCRYPTED_IFACE))


def _secret_partition_ips(ips):
    """(encrypted, cleartext) — the split `request` advertises from."""
    enc, plain = [], []
    for ip in ips:
        (enc if _secret_is_encrypted(ip) else plain).append(ip)
    return enc, plain


def _secret_select_ips(ips, allow_plain=False):
    """(chosen, dropped) addresses for a credential endpoint.

    Cleartext is OPT-IN. A LAN URL carries the token in the request line and
    the credential in the POST body with nothing around them, and offering it
    beside the encrypted one means some of the time it gets picked. A box with
    only cleartext returns an EMPTY chosen list rather than falling back — the
    caller then tells the user to re-run with --allow-plain, which is a
    decision, not a default.
    """
    enc, plain = _secret_partition_ips(ips)
    if allow_plain:
        return enc + plain, []
    return enc, plain


def _secret_url_line(ip, port, token, iface=None):
    """One advertised URL plus its TRANSPORT, spelled out.

    The ticket's own requirement: when several URLs are offered the user must be
    able to SEE which one is encrypted before deciding where to type a password.

    The label keys on the INTERFACE, not on the address range, because the
    ranges do not carry the answer: `bind_ips()` legitimately advertises real
    overlays, and on this box wg0 (10.88.*), wg-money (192.168.10.*) and a
    zerotier (10.243.*) all look exactly like plain LAN addresses. Calling an
    encrypted tunnel "NEŠIFROVANÉ" steers the user to the worse option on the
    one page where it matters most.
    """
    from filedrop import _is_tailscale
    url = "http://%s:%d/%s/" % (ip, port, token)
    if _is_tailscale(ip):
        return "%s   [tailscale — šifrované (WireGuard), odporúčané]" % url
    if str(ip).startswith("127."):
        return "%s   [loopback — len z tohto stroja]" % url
    if iface is None:
        iface = _secret_iface_for(ip)
    if iface and str(iface).lower().startswith(_SECRET_ENCRYPTED_IFACE):
        return "%s   [%s — šifrovaný tunel]" % (url, iface)
    return "%s   [LAN — NEŠIFROVANÉ (plain HTTP), použi radšej tailscale]" % url


def _secret_redact(blob, value, marker=b"<<REDACTED>>"):
    """`blob` with every anticipated rendering of `value` replaced.

    A child of `secret exec` must not be able to put the credential on the
    CLI's stdout/stderr, because those are the agent's transcript — the one
    place this whole channel exists to keep the value out of. The child's argv
    is chosen by the agent, so `secret exec DB_PASS -- env` was a one-command
    leak and any verbose or failing child (`curl -v`, `bash -x`, a tool that
    echoes its config on error) was an accidental one.

    Fragments shorter than 4 bytes are NOT redacted: at that length the value
    matches ordinary text everywhere and the filter would destroy the child's
    output instead of protecting anything.

    TWO KINDS OF RENDERING, and the second was missing (#153 finding 2). An
    ENCODING re-encodes the value whole (b64, hex, percent) — those were
    covered from the start. ESCAPING leaves the value's own bytes in place and
    rewrites only its metacharacters, so a search for the raw value misses it
    completely: for a value containing a quote, a backslash or a newline,
    `json.dumps({"pw": v})` and `repr(v)` both passed straight through. That is
    the ACCIDENTAL class this filter exists for — a child dumping its config as
    JSON, a traceback printing a dict — not a deliberate transformation, so the
    gap was real and the old docstring's disclaimer did not cover it. The
    escaped forms a stdlib dump actually produces are now in the set: JSON,
    repr() of a str and of bytes, unicode_escape, HTML/XML escaping,
    shell-quoting, and configparser's %-doubling.

    HONEST LIMIT, stated rather than implied: this stops the value appearing
    VERBATIM, in an obvious encoding, or in an ordinary escaped rendering. A
    child that deliberately transforms it (encrypts it, reverses it, prints it
    a character per line, base64s it twice) still defeats the filter — nothing
    at this layer can prevent that, because the session genuinely has to be
    able to USE the credential. The containment that would (resolving the
    command from a user-written template instead of agent argv) is a product
    decision, filed as #154 and blocked on the user's call there.

    THE OTHER RESIDUAL, which redaction cannot touch at all: this filters the
    child's captured fd 1/2 ONLY. Nothing constrains where the child WRITES —
    `secret exec DB -- sh -c 'echo "$DB" > config.ini'` puts the value in a
    git-tracked file, and no output filter can see that happen.
    """
    import base64
    import html
    import json
    import shlex
    import urllib.parse

    if not value:
        return blob
    # The floor is on the VALUE, not on each derived form. Escaping EXPANDS:
    # `"` renders as `&quot;` and `<` as `&lt;`, both of which clear a
    # per-form floor — so a per-form test silently broke this docstring's own
    # promise and turned every `&quot;` in a child's HTML output into the
    # marker. Base64 had the same shape long before the escaped forms existed
    # (one byte encodes to four characters).
    if len(value.strip()) < 4:
        return blob
    forms = {value, value.strip()}
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        forms.add(urllib.parse.quote(text).encode())
        forms.add(urllib.parse.quote_plus(text).encode())
        # Escaped renderings. Each is sliced to the BODY the dump would embed,
        # without the quotes the dumper adds around it, so the form matches
        # wherever it is nested (a dict, a config line, a traceback).
        forms.add(json.dumps(text)[1:-1].encode())
        # ensure_ascii=False is an ordinary dump option and renders a
        # non-ASCII value completely differently from the default.
        forms.add(json.dumps(text, ensure_ascii=False)[1:-1].encode())
        forms.add(repr(text)[1:-1].encode())
        forms.add(repr(value)[2:-1].encode())
        forms.add(text.encode("unicode_escape"))
        forms.add(html.escape(text).encode())
        forms.add(html.escape(text, quote=False).encode())
        forms.add(shlex.quote(text).encode())
        forms.add(text.replace("%", "%%").encode())
    forms.add(base64.b64encode(value))
    forms.add(base64.b64encode(value).rstrip(b"="))
    forms.add(base64.urlsafe_b64encode(value))
    forms.add(base64.urlsafe_b64encode(value).rstrip(b"="))
    forms.add(value.hex().encode())
    forms.add(value.hex().upper().encode())      # plenty of tools print caps
    out = blob
    # Longest first, so a form that contains another does not leave a tail.
    for form in sorted((f for f in forms if len(f) >= 4), key=len, reverse=True):
        out = out.replace(form, marker)
    return out


def _secret_apply_remainder(args):
    """Move the flags argparse's REMAINDER swallowed back onto `args`.

    `cmd` is an `argparse.REMAINDER` (needed so `exec NAME -- CMD ...` can carry
    arbitrary child arguments), and REMAINDER stops parsing at the first token
    after the positional NAME — so `secret request DB_PASS --ttl 900` puts
    `--ttl 900` in the remainder and the flag is silently ignored. Found live:
    a request made with `--ttl 900 --keep 900` reported `endpoint-ttl=600s
    keep=28800s` and stored the value with the 8-hour default.

    Consume only OUR flags, only from the head, and stop dead at `--` so a flag
    meant for the child is never eaten (`exec DB -- psql --ttl 1` keeps its own
    `--ttl`). An explicitly parsed value wins — the remainder only ever fills a
    field argparse left unset.
    """
    ints = {"--ttl": "ttl", "--keep": "keep", "--port": "port"}
    strs = {"--env": "env"}
    rest = list(getattr(args, "cmd", None) or [])
    while rest:
        tok = rest[0]
        if tok == "--":
            rest.pop(0)
            break
        if tok in ("--stdin", "--replace", "--allow-plain"):
            setattr(args, tok[2:].replace("-", "_"), True)
            rest.pop(0)
            continue
        key, eq, inline = tok.partition("=")
        if key not in ints and key not in strs:
            break                           # not ours — it belongs to the child
        if eq:
            value, width = inline, 1
        elif len(rest) > 1:
            value, width = rest[1], 2
        else:
            break                           # a dangling flag: leave it visible
        dest = ints.get(key) or strs[key]
        if key in ints:
            try:
                value = int(value)
            except ValueError:
                break                       # not a flag of ours after all
        if getattr(args, dest, None) is None:
            setattr(args, dest, value)      # an explicit value always wins
        del rest[:width]
    args.cmd = rest


def cmd_secret(args):
    """Receive a CREDENTIAL from the user through a URL — never through chat.

    A password / SSH key / PAT / token typed into the chat is written
    permanently into the session transcript (`~/.claude/projects/**/*.jsonl`),
    survives compaction, and cannot be revoked. This command is the alternative:
    `request` prints a one-shot URL, the user posts the value from their own
    browser, and the session learns only that the NAME is ready. The value is
    stored 0600 under `~/.claude/secrets/` and is handed to a child process by
    `exec` — no action here prints it, and there is deliberately no action that
    could.

    WHAT THAT DOES NOT COVER — the residuals, stated here because this is where
    a reader forms their belief about the channel (#153 finding 3):

      * The store is readable by the AGENT'S OWN UID. "No action here prints
        it" is a claim about this command, not about the box: `cat` is not one
        of this command's actions. That path is now closed by an artifact,
        `hooks/block-vault-store-read.sh` — but it is a GUARDRAIL, not a
        boundary, because the agent's uid holds NOPASSWD sudo on these boxes,
        so no store location is out of its reach. It guarantees the unsafe path
        is refused by default and that circumventing it leaves an artifact.
      * `exec`'s CHILD is unconstrained on disk. Output redaction covers the
        captured fd 1/2 only; nothing stops the child WRITING the value
        anywhere, including a git-tracked file — `secret exec DB -- sh -c
        'echo "$DB" > config.ini'` is not something any output filter can see.
        The containment that would close it is #154, blocked on a product
        decision.
      * `exec` BUFFERS all child output until the child exits
        (`capture_output=True`, required in order to filter it). So there is no
        streaming for a long-running or interactive child, and if the CLI is
        killed mid-run the child's output is lost entirely.
      * `request` PRINTS a capability URL into the transcript BY DESIGN — that
        is how the user receives it. The token in it is live for the endpoint
        TTL (default 600s, capped at 3600s), so for that window anyone who can
        read the transcript AND reach a private interface can POST a SUBSTITUTE
        credential before the user does. The nonce binds the endpoint to the
        request, not to whoever posts. Keep the TTL short; `secret forget`
        cancels a pending endpoint.
      * TTL is swept HOURLY (watchdog job 29), so a value stored with the 60s
        minimum `keep` can survive up to ~1h past its expiry.
    """
    import secrets as _secrets
    import subprocess
    import time

    from filedrop import bind_ips
    from filedrop import vault as st

    _secret_apply_remainder(args)
    action = args.action
    name = getattr(args, "name", None)

    # Opportunistic TTL sweep on EVERY invocation — the guarantee that a value
    # cannot lie on disk indefinitely must not depend on anyone remembering to
    # run `purge` (the same shape filedrop.share.prune has).
    expired = st.purge()

    def _need_name():
        if not name:
            print("secret %s: needs a NAME" % action, file=sys.stderr)
            sys.exit(2)
        try:
            st.check_name(name)
        except st.SecretError as e:
            print("secret: %s" % e, file=sys.stderr)
            sys.exit(2)
        return name

    if action == "purge":
        print("purged: %s" % (", ".join(expired) if expired else "nothing"))
        return

    if action == "list":
        rows = st.list_entries()
        if not rows:
            print("no secrets stored")
            return
        print("%-24s %-8s %-26s %s" % ("NAME", "STATE", "RECEIVED", "EXPIRES"))
        for nm, state, _req, recv, exp in rows:
            print("%-24s %-8s %-26s %s" % (nm, state, recv, exp))
        return

    if action == "status":
        nm = _need_name()
        state = st.state(nm)
        meta = st.read_meta(nm)
        extra = ""
        if state == "ready" and isinstance(meta.get("expires_at"), (int, float)):
            extra = "  expires=%s" % st._iso(meta["expires_at"])
        print("%s %s%s" % (nm, state, extra))
        return

    if action == "forget":
        nm = _need_name()
        try:
            done = st.forget(nm)
        except st.SecretError as e:
            print("secret forget: %s" % e, file=sys.stderr)
            sys.exit(1)
        print("%s %s" % (nm, "forgotten" if done else "was not stored"))
        return

    if action == "exec":
        nm = _need_name()
        cmd = list(getattr(args, "cmd", None) or [])
        if not cmd:
            print("secret exec: needs a command after `--`", file=sys.stderr)
            sys.exit(2)
        try:
            value = st.read_value(nm)
        except st.SecretError as e:
            print("secret exec: %s" % e, file=sys.stderr)
            sys.exit(1)
        st.log_event("used", nm)
        # NEVER let the child inherit fd 1/2: those are the agent's transcript.
        # Capture, filter, then re-emit — so a child that echoes its own
        # environment or config cannot write the credential into a file nobody
        # can revoke (adversarial review, finding 1).
        if getattr(args, "stdin", False):
            # stdin, so the value is not in the child's environment at all
            # (/proc/<pid>/environ is owner-only, but a child that dumps its own
            # env into a log is a real shape).
            res = subprocess.run(cmd, input=value, capture_output=True)
        else:
            env = dict(os.environ)
            given = getattr(args, "env", None)
            # `is not None`, not `or`: an explicitly EMPTY --env is a caller
            # error, not a request for the default.
            key = nm if given is None else given
            try:
                # The same grammar as a secret name, and for the same reason:
                # an unchecked key is an injection point (`BASH_FUNC_x%%` makes
                # a bash child EXECUTE the value; `A=B` splits into two).
                st.check_name(key)
            except st.SecretError as e:
                print("secret exec: bad --env key: %s" % e, file=sys.stderr)
                sys.exit(2)
            try:
                env[key] = value.decode("utf-8")
            except UnicodeDecodeError:
                print("secret exec: %s is not UTF-8 — use --stdin" % nm,
                      file=sys.stderr)
                sys.exit(1)
            res = subprocess.run(cmd, env=env, capture_output=True)
        for stream, data in ((sys.stdout, res.stdout), (sys.stderr, res.stderr)):
            if data:
                stream.buffer.write(_secret_redact(data, value))
                stream.flush()
        sys.exit(res.returncode)

    # --- request -----------------------------------------------------------
    nm = _need_name()
    state = st.state(nm)
    if state == "ready":
        print("%s is already stored — `secret forget %s` first" % (nm, nm),
              file=sys.stderr)
        sys.exit(1)
    if state == "pending":
        # A second endpoint for the same name means two live tokens, neither
        # invalidating the other. Replacing is fine, but it must be asked for.
        if not getattr(args, "replace", False):
            print("%s already has a pending request — finish it, or re-run "
                  "with --replace to cancel it and issue a new URL" % nm,
                  file=sys.stderr)
            sys.exit(1)
        print("cancelling the previous request: %s" % st.stop_endpoint(nm))
        st.forget(nm)
    ttl = _secret_clamp_ttl(getattr(args, "ttl", None) or st.DEFAULT_ENDPOINT_TTL_S)
    keep = _secret_clamp_keep(getattr(args, "keep", None) or st.DEFAULT_KEEP_S)

    private = [ip for ip in bind_ips() if _secret_bindable(ip)]
    if not private:
        print("secret: no private interface to bind (refusing a public bind)",
              file=sys.stderr)
        sys.exit(1)
    # Bind only what we advertise: an address the user is not being offered is
    # pure attack surface.
    ips, dropped = _secret_select_ips(private,
                                      allow_plain=getattr(args, "allow_plain", False))
    if not ips:
        print("secret: only unencrypted interfaces are available (%s). A "
              "credential would cross the LAN in cleartext — re-run with "
              "--allow-plain if that is acceptable here."
              % ", ".join(dropped), file=sys.stderr)
        sys.exit(1)

    port = int(getattr(args, "port", None) or 0) or _pick_free_port(ips, SECRET_PORTS)
    if port is None:
        print("secret: no free port in %d-%d" % (SECRET_PORTS[0], SECRET_PORTS[-1]),
              file=sys.stderr)
        sys.exit(1)

    token = _secrets.token_urlsafe(24)
    nonce = st.register_request(nm, endpoint_ttl_s=ttl, keep_s=keep)
    # The endpoint's own diagnostics (bind failures) — NOT the value log, and
    # the server deliberately never writes the token or the body here.
    endpoint_log = st.log_path().parent / ("endpoint-%d.log" % port)
    endpoint_log.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(endpoint_log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    # The token goes through the ENVIRONMENT, never argv: /proc/<pid>/cmdline is
    # world-readable (0444) and these boxes host foreign uids, while
    # /proc/<pid>/environ is owner-only (0400). In argv, the endpoint's only
    # auth would be readable by every local account for the whole TTL.
    child_env = dict(os.environ)
    child_env["AIRULESET_VAULT_TOKEN"] = token
    child_env["AIRULESET_VAULT_NONCE"] = nonce
    with os.fdopen(fd, "ab") as lf:
        child = subprocess.Popen(
            [sys.executable, str(REPO_DIR / "filedrop" / "vault_server.py"),
             str(port), ",".join(ips), nm, str(ttl), str(keep)],
            stdout=subprocess.DEVNULL, stderr=lf, stdin=subprocess.DEVNULL,
            env=child_env, start_new_session=True)
    # Recorded so `forget` and the TTL sweep can actually STOP the endpoint,
    # rather than deleting the value while its URL stays open.
    st.record_endpoint(nm, child.pid)

    probes = _secret_probe_urls(ips, port)

    opener = _secret_opener()

    def _live(u):
        try:
            # /healthz answers 204. Accepting only 200 made every probe read
            # "dead" and `request` printed no URL at all — see the tests.
            return opener.open(u, timeout=2).status in (200, 204)
        except OSError:
            return False

    for _ in range(20):
        if any(_live(u) for u in probes):
            break
        time.sleep(0.25)
    else:
        print("secret: endpoint failed to come up — see %s" % endpoint_log,
              file=sys.stderr)
        sys.exit(1)

    for ip in ips:
        if _live(_secret_health_url(ip, port)):
            print(_secret_url_line(ip, port, token))
    if dropped:
        print("(skipped %s — cleartext; --allow-plain offers them too)"
              % ", ".join(dropped))
    print("name=%s  endpoint-ttl=%ds  keep=%ds" % (nm, ttl, keep))
    print("Otvor URL v prehliadači a vlož hodnotu — do chatu ju NEPÍŠ. "
          "Stav: `airuleset.py secret status %s`." % nm)


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


def _burn_remote_cmd(remote, days):
    """Pure ssh-command builder for a remote `burn` collection — invokes that
    box's OWN already-deployed `airuleset.py burn --json` (the box gets this
    module from the ordinary `push` deploy; never scp'd separately, per
    `deploy-from-clean-tree.md`). Split out from `_burn_remote` so the
    command shape is unit-testable without a real network call."""
    return _remote_ssh_prefix(remote) + [
        f"cd {remote['repo_path']} && python3 airuleset.py burn --json --days {days}"]


def _remote_ssh_prefix(remote):
    """The identity/sshpass selection shared by every remote collection.

    ONE place, so a second collector (`_delegation_remote_cmd`, #130) reuses
    the sanctioned ssh shape byte-for-byte instead of inventing a parallel one
    — `hooks/block-subdev-ssh-misuse.sh` guards exactly this."""
    identity = remote.get("identity")
    if identity:
        return ["ssh", "-i", os.path.expanduser(identity),
                "-o", "StrictHostKeyChecking=no",
                f"{remote['user']}@{remote['host']}"]
    return ["sshpass", "-p", "newlevel", "ssh", "-o", "StrictHostKeyChecking=no",
            f"{remote['user']}@{remote['host']}"]


def _burn_remote(remote, days):
    """Collect one remote box's burn report over ssh. Fail-safe: any ssh
    error, non-zero exit, or unparsable stdout prints a WARN to stderr and
    returns None — one unreachable box never aborts the whole report."""
    import subprocess
    cmd = _burn_remote_cmd(remote, days)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"  WARN: burn collection failed for {remote['name']}: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  WARN: burn collection failed for {remote['name']}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print(f"  WARN: burn collection returned invalid JSON for {remote['name']}",
              file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# #55 follow-up — fleet-wide hourly collection for the watchdog's job 16
# (fleet_burn_job). Unlike `_burn_remote` above (which re-runs a full
# `airuleset.py burn --json --days N` scan remotely — heavy, and NOT what an
# hourly poll needs), this just TAILS the box's own job-13 output
# (`~/.claude/burn-history/snapshots.jsonl`, already written locally every
# hour by every managed box) — cheap, no remote transcript scanning. Reuses
# the EXACT same identity/sshpass selection as `_burn_remote_cmd` — never
# invent a new ssh shape (hooks/block-subdev-ssh-misuse.sh guards this).
# --------------------------------------------------------------------------- #

def _fleet_remote_cmd(remote):
    """Pure ssh-command builder — split out for unit-testability, mirroring
    `_burn_remote_cmd`'s own split."""
    remote_cmd = "tail -n 1 ~/.claude/burn-history/snapshots.jsonl"
    identity = remote.get("identity")
    if identity:
        return ["ssh", "-i", os.path.expanduser(identity),
                "-o", "StrictHostKeyChecking=no",
                f"{remote['user']}@{remote['host']}", remote_cmd]
    return ["sshpass", "-p", "newlevel", "ssh", "-o", "StrictHostKeyChecking=no",
            f"{remote['user']}@{remote['host']}", remote_cmd]


def _hour_bucket_of_ts(ts_str):
    """Epoch-hour bucket (`int(epoch_seconds // 3600)`) of an ISO-8601
    timestamp STRING, converted to UTC first — comparing raw hour-of-day
    digits (or the raw string) across differing UTC offsets is exactly the
    #60 bug (gk writes `+00:00`, dev1 `+02:00` — the SAME instant renders
    with different hour digits in each). None when `ts_str` is missing,
    None, or unparsable — the caller (`_fleet_remote_row`) treats that as
    "can't verify freshness" and errors rather than trusting it.

    Thin wrapper — the canonical implementation is `burn.hour_bucket_of_ts`
    (#63: shared with `watchdog.fleet_burn_job`'s own local-row freshness
    check, so the convention can never drift between the two call sites)."""
    import burn as burn_mod
    return burn_mod.hour_bucket_of_ts(ts_str)


def _fleet_remote_row(remote, want_hour_bucket, timeout=15):
    """One remote host's latest hourly burn-snapshot row FOR THE SPECIFIC
    `want_hour_bucket` (an epoch-hour index — see `_hour_bucket_of_ts`), or
    `{"error": ...}` on ANY failure: ssh, timeout, empty file, bad JSON, OR
    a STALE/mismatched-hour row (#60). The remote's tail line existing does
    NOT mean it is fresh for the hour being collected — the remote may not
    have written this hour's row yet (job 16 now waits until HH:05 to give
    the remote's own job 13 time to write it), or the remote's clock/offset
    may differ from ours (`_hour_bucket_of_ts` always converts to UTC
    before comparing — never the raw string/local hour-of-day). A
    stale/mismatched row is returned as `{"error": ..., "stale": True}` so
    callers (`merge_fleet_row`/`render_fleet`) can render it distinctly
    from a hard collection failure (`—` vs `ERR`) — this IS the #60 fix:
    silently reusing an old row produced a false fleet trend/total (5/6
    hosts double-counting the same stale sample read as "-39.8%
    (lepšie)"). A single unreachable/stale box must never crash the fleet
    job or the rest of the watchdog sweep. Never raises."""
    import subprocess
    cmd = _fleet_remote_cmd(remote)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
    if result.returncode != 0:
        return {"error": (result.stderr or "").strip()[:200] or "ssh failed"}
    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return {"error": "no snapshot data yet"}
    try:
        row = json.loads(lines[-1])
    except ValueError:
        return {"error": "invalid JSON from remote"}
    if not isinstance(row, dict):
        return {"error": "unexpected JSON shape from remote"}
    row_hour = _hour_bucket_of_ts(row.get("ts"))
    if row_hour != want_hour_bucket:
        return {"error": "no sample for hour %s (latest %s)" % (want_hour_bucket, row.get("ts")),
               "stale": True}
    return row


def _watchdog_fleet_fetch(hosts=None, want_hour_bucket=None):
    """Real remote collector used by cmd_watchdog's job 16 wiring — one row
    per REMOTE_HOSTS entry, hour-matched against `want_hour_bucket` (#60).
    Defaults to the CURRENT UTC epoch-hour when not given — the plain
    top-level/manual-invocation case; `fleet_burn_job` always passes its own
    `now`-derived bucket explicitly (this repo's convention of threading
    `now` through every job for determinism/testability — see
    `_fleet_remote_row`). Never raises; a single bad or stale host degrades
    to `{"error": ...}` in its own slot rather than dropping the whole
    fleet."""
    hosts = hosts if hosts is not None else REMOTE_HOSTS
    if want_hour_bucket is None:
        import datetime
        want_hour_bucket = int(datetime.datetime.now(datetime.timezone.utc).timestamp() // 3600)
    return {h["name"]: _fleet_remote_row(h, want_hour_bucket) for h in hosts}


def cmd_burn(args):
    """Token-spend report from local transcripts — the measurement behind the
    2026-07-25 cost-fix package (Opus-5-default MANAGED_MODEL, this
    diagnostic, the statusline context/cost segment): ~$13,600 across all 6
    managed boxes over 8 days, 76% Fable 5 running as MAIN (not advisor), 92%
    of that in input context. The local box is always included; `--host
    <name>` (or `--host all`) also collects a remote box over ssh by
    invoking ITS OWN deployed `airuleset.py burn --json` — never scp (the
    clean-tree hook would block it anyway).

    `--mark "<text>"` / `--compare` are the follow-up AUTOMATIC feedback
    loop (#37): `--mark` records that a change was made NOW (or at
    `--mark-ts <iso>` for backdating from a known event, e.g. a git commit
    timestamp) to `~/.claude/burn-history/changes.jsonl`; `--compare` reads
    that alongside the watchdog's hourly `snapshots.jsonl` (AND, when
    present, the fleet-wide `fleet.jsonl` — #55 point D) and prints, per
    change, the mean $/h, avg context and msgs/h in `--window` hours (default
    6) before vs after it — so the user never has to check anything himself,
    the report just tells him whether a change made things better or worse.

    `--fleet [--hours N]` (#55) prints the monitored-fleet hourly report:
    per-host + total $ for the last N hours (default 24), the trend (latest
    hour vs mean of the previous 3), and a sustainability verdict against the
    watchdog's weekly usage-cache budget. The fleet.jsonl feed is written by
    watchdog job 16 (`fleet_burn_job`), coordinator-only (dev1)."""
    import burn
    if getattr(args, "mark", None):
        ts = None
        mark_ts = getattr(args, "mark_ts", None)
        if mark_ts:
            import datetime
            ts = datetime.datetime.fromisoformat(mark_ts)
        path = burn.mark_change(args.mark, now=ts)
        print("Marked: %s -> %s" % (args.mark, path))
        return
    if getattr(args, "compare", False):
        window = getattr(args, "window", None) or 6
        changes = burn.load_changes()
        results = burn.compare_changes(burn.load_snapshots(), changes, window_hours=window)
        fleet_rows = burn.load_fleet()
        fleet_results = None
        if fleet_rows:
            fleet_results = burn.compare_changes(burn.fleet_compare_rows(fleet_rows),
                                                 changes, window_hours=window)
        print(burn.render_compare(results, window_hours=window, fleet_results=fleet_results))
        return
    if getattr(args, "fleet", False):
        hours = getattr(args, "hours", None) or 24
        print(burn.render_fleet(burn.load_fleet(), hours=hours, cache=burn.load_usage_cache()))
        return
    days = getattr(args, "days", None) or 7
    reports = [burn.local_report(days=days)]
    host_arg = getattr(args, "host", None)
    if host_arg:
        if host_arg == "all":
            targets = REMOTE_HOSTS
        else:
            targets = [h for h in REMOTE_HOSTS if h["name"] == host_arg]
            if not targets:
                names = ", ".join(h["name"] for h in REMOTE_HOSTS)
                print(f"ERROR: unknown --host '{host_arg}' — choices: {names}, all",
                      file=sys.stderr)
                sys.exit(1)
        for remote in targets:
            print(f"Collecting burn from {remote['name']}...", file=sys.stderr)
            rep = _burn_remote(remote, days)
            if rep:
                reports.append(rep)
    combined = burn.merge_reports(reports)
    if getattr(args, "json", False):
        print(json.dumps(combined, indent=1))
    else:
        print(burn.render_human(combined, days))


# --------------------------------------------------------------------------- #
# #130 — `airuleset.py delegation`: the standing MAIN vs SUBAGENT cost meter.
#
# The ruleset's central move is to push work out of the main agent because a
# main turn re-sends the whole conversation. That reasoning has never been
# checked against what the subagents themselves cost, and it could not be:
# `burn.scan()` is structurally blind to subagent transcripts (see the header
# comment on `burn.scan_split`). This is the instrument, not a gate — it
# reports, it never blocks, and it changes no threshold anywhere.
# --------------------------------------------------------------------------- #

def _delegation_remote_cmd(remote, hours):
    """Pure ssh-command builder — invokes the remote box's OWN deployed
    `airuleset.py delegation --json`, exactly as `_burn_remote_cmd` does for
    `burn`, sharing the same identity/sshpass prefix. READ-ONLY on the remote:
    it scans that box's transcripts and prints JSON, writes nothing."""
    return _remote_ssh_prefix(remote) + [
        f"cd {remote['repo_path']} && python3 airuleset.py delegation "
        f"--json --hours {hours}"]


def _delegation_remote(remote, hours):
    """Collect one remote box's split report. Fail-safe like `_burn_remote`:
    any ssh error, non-zero exit or unparsable stdout WARNs and returns None,
    so one unreachable box never aborts the fleet report."""
    import subprocess
    cmd = _delegation_remote_cmd(remote, hours)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"  WARN: delegation collection failed for {remote['name']}: {e}",
              file=sys.stderr)
        return None
    if result.returncode != 0:
        print(f"  WARN: delegation collection failed for {remote['name']}: "
              f"{result.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        print("  WARN: delegation collection returned invalid JSON for "
              f"{remote['name']}", file=sys.stderr)
        return None


def _gh_closed_issues_json(repo):
    import subprocess
    r = subprocess.run(
        ["gh", "issue", "list", "-R", repo, "--state", "closed",
         "--limit", "500", "--json", "number,closedAt"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout


def _closed_ticket_count(repo, start, end, _runner=None):
    """Issues on `repo` closed inside [start, end], or None.

    None — never 0 — when `gh` is unavailable or errors: a fabricated
    zero-ticket denominator would render as "spend with no ticket", which is a
    real and serious finding, and it must never be manufactured by a missing
    tool."""
    import burn
    import datetime
    runner = _runner or _gh_closed_issues_json
    try:
        raw = runner(repo)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = burn._parse_ts(r.get("closedAt"))
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        if start <= t <= end:
            n += 1
    return n


def _attach_ticket_counts(merged, hours, _counter=None):
    """Join closed-ticket counts onto each project row that resolved a repo.

    Opt-in (`--tickets`) because it needs network + `gh` auth the base
    measurement must not depend on. A project whose repo did not resolve keeps
    `closed_tickets: None` and renders no per-ticket line, rather than
    borrowing someone else's denominator."""
    import datetime
    counter = _counter or _closed_ticket_count
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(hours=hours)
    cache = {}
    for row in (merged.get("by_project") or {}).values():
        repo = row.get("repo")
        if not repo:
            continue
        if repo not in cache:
            cache[repo] = counter(repo, start, end)
        row["closed_tickets"] = cache[repo]
        if cache[repo]:
            import burn
            total = row["main"]["units"] + row["sub"]["units"]
            row["units_per_ticket"] = burn.units_per_ticket(total, cache[repo])
    return merged


def cmd_delegation(args):
    """Per-box, per-project MAIN vs SUBAGENT token attribution over a window.

    Reports turns, the four token sums, a weighted (relative, never a price)
    cost unit, mean context per turn, and — with `--tickets` — cost per closed
    ticket, for MAIN and SUBAGENT separately. `--host <name>` / `--host all`
    also collects the remote fleet over ssh via each box's own deployed copy.
    """
    import burn
    hours = getattr(args, "hours", None) or 12
    root = getattr(args, "root", None)
    if getattr(args, "floor", False):
        # #131 — a DIFFERENT question from the standing meter's, so it gets its
        # own report rather than extra columns on the by-project table: this one
        # is per dispatch and local-only (a remote box's split is already
        # folded per project and cannot be decomposed back into dispatches).
        rep = burn.scan_dispatches(root or os.path.expanduser(
            "~/.claude/projects"), hours=hours)
        if getattr(args, "json", False):
            print(json.dumps(rep, indent=1))
        else:
            print(burn.render_floor(rep, hours=hours))
        return
    reports = [burn.split_report(hours=hours, root=root)]
    host_arg = getattr(args, "host", None)
    if host_arg:
        if host_arg == "all":
            targets = REMOTE_HOSTS
        else:
            targets = [h for h in REMOTE_HOSTS if h["name"] == host_arg]
            if not targets:
                names = ", ".join(h["name"] for h in REMOTE_HOSTS)
                print(f"ERROR: unknown --host '{host_arg}' — choices: {names}, all",
                      file=sys.stderr)
                sys.exit(1)
        for remote in targets:
            print(f"Collecting delegation split from {remote['name']}...",
                  file=sys.stderr)
            rep = _delegation_remote(remote, hours)
            if rep:
                reports.append(rep)
    merged = burn.merge_splits(reports)
    if getattr(args, "tickets", False):
        _attach_ticket_counts(merged, hours)
    if getattr(args, "json", False):
        print(json.dumps(merged, indent=1))
    else:
        print(burn.render_split(merged, hours=hours))


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


def watchdog_disable_marker():
    """`~/.claude/api-watchdog.disabled` — the opt-out that makes a deliberate
    `systemctl --user stop api-watchdog.timer` SURVIVE a deploy (#132).

    Resolved at CALL time, never frozen at import (same reasoning as
    `watchdog.compact_requests_path()`).

    Why this exists: on 2026-07-28 the watchdog typed `/exit` into a live
    session, the timer was stopped fleet-wide as the mitigation — and was found
    running again on all 6 boxes the next morning, because `install` ends with
    an unconditional `enable --now` and every `airuleset.py push` runs
    `install`. A mitigation a routine deploy silently undoes is not a
    mitigation. Touch this file to keep the timer off across pushes; delete it
    to hand control back to `install`."""
    return Path.home() / ".claude" / "api-watchdog.disabled"


def setup_watchdog_service():
    """Install + start the api-watchdog systemd --user timer on THIS machine
    (every host — autopilot runs on dev1 and dev2). Mirrors the file-drop setup:
    write the .service + .timer units, daemon-reload, enable --now the timer —
    unless `watchdog_disable_marker()` exists, in which case the units are still
    refreshed but the timer is left exactly as the operator set it (#132)."""
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
    if watchdog_disable_marker().exists():
        # ENFORCE the stop, don't merely decline to start it. A timer that is
        # stopped but still ENABLED comes back at the next boot or linger
        # restart, so "skip enable --now" alone would let the mitigation expire
        # on its own (#132). Both calls are idempotent, so a box that is
        # already stopped+disabled just no-ops.
        _run_systemctl(["stop", "api-watchdog.timer"])
        _run_systemctl(["disable", "api-watchdog.timer"])
        print(f"  api-watchdog timer STOPPED + DISABLED — disable marker "
              f"present ({watchdog_disable_marker()}).\n"
              f"  Units refreshed. To re-arm: delete the marker and run "
              f"`systemctl --user enable --now api-watchdog.timer`.")
        return True
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
    p_notify.add_argument("--backfill-digest", dest="backfill_digest",
                          action="store_true",
                          help="ONE catch-up digest for --repo covering the "
                               "tickets closed since --since that never got a "
                               "delivered card (never one card per ticket). "
                               "Card markers are machine-local, so this MUST "
                               "run on the box holding the repo's checkout — "
                               "it refuses otherwise rather than reporting "
                               "another box's delivered cards as missing")
    p_notify.add_argument("--since", help="ISO8601 window start (--backfill-digest)")
    p_notify.add_argument("--force", action="store_true",
                          help="With --backfill-digest: run even though no "
                               "local checkout of --repo was found. The "
                               "check can miss a real checkout (a worktree, "
                               "an unusual location), and repairing a "
                               "reporting gap must never be impossible on "
                               "the box that holds it")
    p_notify.add_argument("--owner-name", dest="owner_name",
                          help="Deliver to this owner's thread "
                               "(--backfill-digest). Rarely needed: the owner "
                               "is read from the checkout's own live pane, "
                               "which is the only thing that actually knows "
                               "whose repo it is. Use it only when the repo "
                               "has no pane here — a value contradicting the "
                               "pane is refused, not obeyed")
    p_notify.add_argument("--repo-name", dest="repo_name", action="store_true",
                          help="Print the GitHub repo NAME for --cwd, from its "
                               "origin remote (never the directory basename)")
    p_notify.add_argument("--newest-card", dest="newest_card",
                          action="store_true",
                          help="Print the mtime of the newest DELIVERED "
                               "per-ticket card marker for --repo")
    # NOTE: `--cwd` already exists further down (--record-question) and is
    # reused by --repo-name; re-declaring it raises ArgumentError at import
    # and breaks EVERY `notify` subcommand.
    p_notify.add_argument("--api-error", dest="api_error", action="store_true",
                          help="Ping IF --text is a real Claude Code API error "
                               "(used by the notify-api-error.sh Stop hook)")
    p_notify.add_argument("--record-question", dest="record_question",
                          action="store_true",
                          help="Record a ❓ ping's Discord message id → the session "
                               "that asked (for Discord-reply routing); needs "
                               "--message-id --channel --session --cwd")
    p_notify.add_argument("--question-stdin", dest="question_stdin",
                          action="store_true",
                          help="With --record-question: read the posted ❓ text "
                               "from stdin (the send hook pipes it). Without "
                               "this flag stdin is NEVER touched — an "
                               "unconditional read blocked forever on an "
                               "inherited never-closing pipe")
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
    p_notify.add_argument("--handoff", action="store_true",
                          help="Fork-no-merge card: fired at the READY-FOR-REVIEW "
                               "hand-off (locally verified, waiting for gatekeeper "
                               "merge) — no merge/version, shows a 🔎 review status")
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

    p_creq = sub.add_parser(
        "compact-request",
        help="Record a /compact request for a session at a safe ticket "
             "boundary (#39 krok 1c) — consumed by watchdog job 14")
    p_creq.add_argument("--record", action="store_true",
                        help="Record the request (called by the Stop hook)")
    p_creq.add_argument("--session", default="", help="Session id (transcript stem)")
    p_creq.add_argument("--cwd", default="", help="Session cwd")
    p_creq.add_argument("--msg-hash", dest="msg_hash", default="",
                        help="Fingerprint (e.g. sha256) of the triggering "
                             "last_assistant_message (#71 delivered-dedup — "
                             "a repeat with the SAME hash after a delivered "
                             "compact is a no-op)")
    p_creq.add_argument("--origin", default="",
                        help="What PROVED this is a ticket boundary (#121). "
                             "'subagent-stop' = an autopilot-worker concluded "
                             "with zero other live tasks in the session's task "
                             "registry; for such a request a `⏳` last line is "
                             "not evidence of anything and never holds the "
                             "delivery (a `❓` still does). Empty = the Stop-hook "
                             "origin, whose gate is unchanged.")

    p_tickets = sub.add_parser(
        "tickets-status",
        help="Statusline github-tickets segment — autopilot done/total or open issues")
    p_tickets.add_argument("--cwd", help="Session cwd (defaults to $PWD)")
    p_tickets.add_argument("--refresh", action="store_true",
                           help="Slow path: refresh the per-repo cache via git+gh "
                                "(run detached by the statusline, never inline)")

    p_gkr = sub.add_parser(
        "gk-request",
        help="Stream→supervisor action request (#30): file/mark a "
             "needs-gatekeeper ticket the watchdog delivers to the supervisor "
             "(no user middleman)")
    p_gkr.add_argument("--repo", help="owner/name (default: current repo)")
    p_gkr.add_argument("--issue", type=int,
                       help="Mark an EXISTING issue instead of creating one")
    p_gkr.add_argument("--title", help="New ticket title (create mode)")
    p_gkr.add_argument("--body", help="New ticket body text")
    p_gkr.add_argument("--body-file", dest="body_file",
                       help="New ticket body from a file (backtick-safe)")
    p_gkr.add_argument("--comment",
                       help="Request text for --issue mode (Slovak, plain)")

    p_gate = sub.add_parser(
        "fable-gate", help="Budget gate for automatic Fable escalation — exit 0 "
                           "(OPEN, dispatch fable) / 1 (CLOSED, dispatch opus)")
    p_gate.add_argument("--threshold", type=int, default=None,
                        help="Gate percent (default 80 / AIRULESET_FABLE_GATE_PCT)")

    p_burn = sub.add_parser(
        "burn",
        help="Token-spend report from local Claude Code transcripts — "
             "by model / day / project")
    p_burn.add_argument("--days", type=int, default=7,
                        help="Lookback window in days (default 7)")
    p_burn.add_argument("--json", action="store_true",
                        help="Print the raw aggregated JSON instead of a table")
    p_burn.add_argument("--host", default=None,
                        help="Also collect a remote box by REMOTE_HOSTS name, "
                             "or 'all' for every managed remote (over ssh)")
    p_burn.add_argument("--mark", default=None,
                        help="Record a change NOW to burn-history/changes.jsonl "
                             "(the automatic --compare feedback loop, #37)")
    p_burn.add_argument("--mark-ts", dest="mark_ts", default=None,
                        help="Backdate --mark to this ISO8601 timestamp "
                             "(e.g. a git commit's --format=%%cI)")
    p_burn.add_argument("--compare", action="store_true",
                        help="Print mean $/h, avg context, msgs/h before vs "
                             "after each --mark'd change (--window hours each side)")
    p_burn.add_argument("--window", type=int, default=None,
                        help="--compare lookback/lookahead window in hours (default 6)")
    p_burn.add_argument("--fleet", action="store_true",
                        help="Print the monitored-fleet hourly report (#55) — "
                             "per-host + total $, trend, sustainability verdict")
    p_burn.add_argument("--hours", type=int, default=None,
                        help="--fleet lookback window in hours (default 24)")

    p_del = sub.add_parser(
        "delegation",
        help="MAIN vs SUBAGENT cost meter — per box, per project: turns, "
             "token sums, weighted units, mean context per turn")
    p_del.add_argument("--hours", type=int, default=12,
                       help="Lookback window in hours (default 12)")
    p_del.add_argument("--json", action="store_true",
                       help="Print the raw merged JSON instead of a table")
    p_del.add_argument("--host", default=None,
                       help="Also collect a remote box by REMOTE_HOSTS name, "
                            "or 'all' for every managed remote (read-only, over ssh)")
    p_del.add_argument("--tickets", action="store_true",
                       help="Join closed-issue counts per repo and report cost "
                            "per completed ticket (needs gh; opt-in)")
    p_del.add_argument("--root", default=None,
                       help="Transcript store to scan (default ~/.claude/projects)")
    p_del.add_argument("--floor", action="store_true",
                       help="Per-DISPATCH report (#131): the fixed floor a "
                            "dispatch starts with vs the growth it "
                            "accumulates, as distributions, plus turns per "
                            "dispatch and a per-subagent_type breakdown")

    p_up = sub.add_parser(
        "upload",
        help="Web upload URL for receiving a file FROM the user (never ask for scp)")
    p_up.add_argument("--dir", default=None, help="Destination dir (default ~/uploads)")
    p_up.add_argument("--ttl", type=int, default=7200,
                      help="Endpoint self-shutdown after N seconds (default 7200)")
    p_up.add_argument("--port", type=int, default=None,
                      help="Port (default: first free in 8799-8819)")

    p_sec = sub.add_parser(
        "secret",
        help="Ask the user for a CREDENTIAL through a one-shot URL — never in "
             "chat (a value typed into chat is in the transcript forever)")
    p_sec.add_argument("action", choices=list(SECRET_ACTIONS),
                       help="request (stand up the URL) | status | list | "
                            "exec NAME -- CMD (hand the value to a child) | "
                            "forget NAME | purge (drop everything past its TTL)")
    p_sec.add_argument("name", nargs="?", default=None,
                       help="Secret name: letters/digits/underscore, also used "
                            "as the env var name for `exec`")
    p_sec.add_argument("--ttl", type=int, default=None,
                       help="Endpoint self-shutdown after N seconds "
                            "(default 600 — minutes, not hours)")
    p_sec.add_argument("--keep", type=int, default=None,
                       help="Delete the stored value after N seconds "
                            "(default 28800); `forget` removes it sooner")
    p_sec.add_argument("--port", type=int, default=None,
                       help="Port (default: first free in 8830-8849)")
    p_sec.add_argument("--env", default=None,
                       help="exec: environment variable to set (default: NAME)")
    p_sec.add_argument("--allow-plain", action="store_true",
                       help="request: also offer UNENCRYPTED LAN URLs (a "
                            "credential would cross the network in cleartext)")
    p_sec.add_argument("--replace", action="store_true",
                       help="request: cancel an existing pending request for "
                            "this name (stopping its endpoint) and issue a new URL")
    p_sec.add_argument("--stdin", action="store_true",
                       help="exec: feed the value on the child's stdin instead "
                            "of through the environment")
    p_sec.add_argument("cmd", nargs=argparse.REMAINDER,
                       help="exec: the command to run, after `--`")

    p_auth = sub.add_parser(
        "authority",
        help="Print this stream's autopilot authority profile "
             "(full / branch-merge / fork-no-merge)")
    p_auth.add_argument("--explain", action="store_true",
                        help="Also print how the profile was resolved")

    p_slice = sub.add_parser(
        "slice-quals",
        help="THE single definition of a reduced-authority stream's own "
             "ticket slice (#181) — used by the /goal stop-proof templates")
    p_slice.add_argument("--count", action="store_true",
                         help="Print the slice's open non-skip issue count "
                              "(0 = slice empty)")
    p_slice.add_argument(
        "--list", action="store_true",
        help="Print number<TAB>createdAt<TAB>action<TAB>title, oldest first")
    p_slice.add_argument("--extra", default=None,
                         help="Extra search qualifier ANDed onto every query "
                              "(e.g. label:prio:bounce)")

    p_core = sub.add_parser(
        "core-quals",
        help="A full-authority box's OBLIGATION set — the CORE slice plus "
             "every ticket only this box can action (#181) — used by the "
             "FULL /goal stop-proof template and its backlog listing")
    p_core.add_argument("--count", action="store_true",
                        help="Print the obligation set's open non-skip issue "
                             "count (0 = nothing left for this box to action)")
    p_core.add_argument(
        "--list", action="store_true",
        help="Print number<TAB>createdAt<TAB>action<TAB>title, oldest first "
             "(action = action-only for a sub-dev stream's ticket, implement "
             "otherwise)")
    p_core.add_argument("--extra", default=None,
                        help="Extra search qualifier ANDed onto every query "
                             "(e.g. label:prio:bounce for the bounce seed)")

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
    "compact-request": cmd_compact_request,
    "fable-gate": cmd_fable_gate,
    "burn": cmd_burn,
    "delegation": cmd_delegation,
    "authority": cmd_authority,
    "slice-quals": cmd_slice_quals,
    "core-quals": cmd_core_quals,
    "upload": cmd_upload,
    "secret": cmd_secret,
    "tickets-status": cmd_tickets_status,
    "gk-request": cmd_gk_request,
    "autopilot-lock": cmd_autopilot_lock,
}
# Backwards-compatible alias used by main() before SUBCOMMANDS existed.
commands = SUBCOMMANDS


if __name__ == "__main__":
    main()
