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
import socket
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

# Managed default effort: `xhigh` (deep adaptive reasoning) is the persistent
# default the user wants in EVERY managed project so they never have to remember
# to raise it. `xhigh` is the highest level settings.json accepts and persists
# across sessions; `max`/`ultracode` are session-only (not valid here) — ultracode
# adds auto-workflow orchestration on top of xhigh and stays a per-session
# `/effort ultracode`. The user can still raise/lower per session with `/effort`.
MANAGED_EFFORT_LEVEL = "xhigh"

UNIVERSAL_PROFILE = REPO_DIR / "profiles" / "universal.profile"

# ---------------------------------------------------------------------------
# Autopilot Board integration (plan Phase F — Tasks 13 & 14)
# ---------------------------------------------------------------------------
# The board is a single always-on daemon on dev1 (10.77.9.21). Every machine
# runs the reporter (fire-and-forget client); only the board host runs the
# systemd service. Single source of truth for the host IP is board.BOARD_HOST_IP
# (env BOARD_HOST override) — re-exported here so callers can `airuleset.BOARD_HOST_IP`.
try:
    from board import BOARD_HOST_IP, PORT as BOARD_PORT, board_url
except Exception:  # pragma: no cover — board package should always import
    BOARD_HOST_IP = os.environ.get("BOARD_HOST", "10.77.9.21")
    BOARD_PORT = 8787

    def board_url():
        return f"http://{BOARD_HOST_IP}:{BOARD_PORT}/"

# Local board daemon state (all under ~/.claude, gitignored — never in the repo).
BOARD_DB_PATH = CLAUDE_DIR / "autopilot-board.sqlite"
BOARD_TOKEN_PATH = CLAUDE_DIR / "autopilot-board.token"
BOARD_SERVICE_TEMPLATE = REPO_DIR / "settings" / "autopilot-board.service.template"
BOARD_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "autopilot-board.service"

# ---------------------------------------------------------------------------
# File-Drop integration — serve user files as clickable LAN URLs
# ---------------------------------------------------------------------------
# Unlike the board (one central daemon on dev1), the file-drop service runs on
# EVERY machine — each serves the files produced on THAT machine, bound to THAT
# machine's own LAN IP (discovered at runtime by filedrop.host_ip()).
try:
    from filedrop import (PORT as FILEDROP_PORT, host_ip as filedrop_host_ip,
                          filedrop_url, FILEDROP_DIR)
except Exception:  # pragma: no cover — filedrop package should always import
    FILEDROP_PORT = int(os.environ.get("FILEDROP_PORT", "8788"))
    FILEDROP_DIR = CLAUDE_DIR / "filedrop"

    def filedrop_host_ip():
        return os.environ.get("FILEDROP_HOST", "127.0.0.1")

    def filedrop_url():
        return f"http://{filedrop_host_ip()}:{FILEDROP_PORT}/"

FILEDROP_SERVICE_TEMPLATE = REPO_DIR / "settings" / "filedrop.service.template"
FILEDROP_SERVICE_DEST = Path.home() / ".config" / "systemd" / "user" / "filedrop.service"


def _local_ips():
    """Collect this machine's IPs from every reliable source.

    `socket.gethostbyname*` often resolves to loopback (127.0.1.1) on Debian/
    Ubuntu where /etc/hosts maps the hostname to loopback — so it ALONE would
    make the real board host detect as a non-board host. We therefore also read
    `hostname -I` (the documented fallback in spec §12), which lists the actual
    interface addresses. All sources are best-effort; failures are ignored."""
    import subprocess
    ips = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass
    try:
        ips.add(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0:
            ips.update(tok for tok in out.stdout.split() if tok)
    except Exception:
        pass
    return ips


def is_board_host():
    """True iff this machine is the board host (BOARD_HOST_IP is one of our IPs).

    Tolerant of resolver failures (returns False) so a misconfigured /etc/hosts
    never makes a non-board host try to start the service."""
    return BOARD_HOST_IP in _local_ips()

# Skills directories in the repo that should be symlinked
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate", "architecture-check", "autopilot", "mutation-sweep", "meeting-analysis"]

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
# permissions + continue the last conversation.
#   --settings '{"ultracode":true}' : ultracode is SESSION-ONLY (never on disk, NOT
#       accepted in settings.json — GH #64817); --settings is the only doc-blessed
#       always-on route and MERGES per-key, so hooks/model/effortLevel stay intact.
#   --dangerously-skip-permissions  : auto-approve (the user opted in for their dev boxes).
#   -c                              : continue the most recent conversation in the cwd.
# A bash FUNCTION (not alias) forwards all args; `command` avoids recursing.
# Escape hatches: `claude-new` (ultracode + skip-perms, FRESH session — no -c, for a
# new dir or a clean start) and `claude-plain` (vanilla `claude`, no flags).
ULTRACODE_BASHRC_BLOCK = (
    f"{ULTRACODE_MARK_START}\n"
    "claude() { command claude --dangerously-skip-permissions -c "
    "--settings '{\"ultracode\":true}' \"$@\"; }\n"
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

    Currently: `effortLevel = xhigh` so deep adaptive reasoning is the persistent
    default in every managed project without the user remembering to raise it.
    Idempotent; preserves all other keys. The user can still override per session
    with `/effort` — this only sets the starting default."""
    result = dict(settings)
    result["effortLevel"] = MANAGED_EFFORT_LEVEL
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


def _validate_board():
    """Validate the Autopilot Board: each board/*.py imports cleanly and the
    systemd service template exists with the repo-path placeholder.

    Loads each module by spec (importlib) so a syntax error or bad import in any
    board file fails `validate` loudly — the board ships with airuleset, so a
    broken board file must block install just like a missing module."""
    import importlib

    errors = []
    board_dir = REPO_DIR / "board"
    if not board_dir.is_dir():
        errors.append(f"Board package missing: {board_dir}")
        return errors

    # Import each board submodule (in dependency order so e.g. gh's
    # `from board.gate import ...` resolves). Importing the package first puts
    # `board` on sys.modules.
    for mod in ("board", "board.gate", "board.gh", "board.db",
                "board.render", "board.reporter", "board.server"):
        try:
            importlib.import_module(mod)
        except Exception as e:
            errors.append(f"Board module failed to import: {mod} ({e})")

    # Service template must exist and carry the {{REPO_DIR}} placeholder + the
    # board --serve ExecStart.
    if not BOARD_SERVICE_TEMPLATE.exists():
        errors.append(f"Missing board service template: {BOARD_SERVICE_TEMPLATE}")
    else:
        tmpl = BOARD_SERVICE_TEMPLATE.read_text()
        if "{{REPO_DIR}}" not in tmpl:
            errors.append("Board service template missing {{REPO_DIR}} placeholder")
        if "board --serve" not in tmpl:
            errors.append("Board service template ExecStart missing `board --serve`")

    return errors


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

    # Validate the Autopilot Board: every board/*.py loads cleanly + service
    # template exists with the substitution placeholder.
    errors.extend(_validate_board())

    # Validate the File-Drop service: filedrop/*.py loads + service template ok.
    errors.extend(_validate_filedrop())

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

    # --- 4. Autopilot Board: branch on whether this is the board host ---
    try:
        maybe_setup_board()
    except Exception as e:
        # The board service is best-effort: a failure here must never abort the
        # core install (CLAUDE.md/skills/hooks already landed). Print and move on.
        print(f"  board setup error (non-fatal): {e}", file=sys.stderr)

    # --- 5. File-Drop service: installed on EVERY machine (serves local files) ---
    try:
        maybe_setup_filedrop()
    except Exception as e:
        print(f"  filedrop setup error (non-fatal): {e}", file=sys.stderr)

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
            imports = [l.strip() for l in content.splitlines()
                       if l.strip().startswith("@~/")]
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
# Autopilot Board subcommands (plan Task 13)
# ---------------------------------------------------------------------------


def cmd_report(args):
    """Thin CLI wrapper over board.reporter — fire-and-forget, never crashes.

    Modes (mutually exclusive enough for the worker's needs):
      --start        mint a run_id, emit the opening event, print the run_id
      --queue        report a planned-issue queue for --repo from --items (JSON)
      --selftest     POST a synthetic ping and print whether the board accepted it
      (default)      emit a phase/heartbeat/field event for --run

    The reporter swallows all network errors itself; this wrapper additionally
    guards against import/usage errors so a board outage can NEVER fail a worker.
    """
    try:
        from board import reporter
    except Exception as e:  # board package unimportable — degrade gracefully
        print(f"report: board package unavailable ({e})", file=sys.stderr)
        return

    try:
        if getattr(args, "start", False):
            rid = reporter.start_run(
                args.repo, args.issue, args.title or "",
                is_bug_fix=bool(getattr(args, "is_bug_fix", False)),
                has_deploy=bool(getattr(args, "has_deploy", False)),
                merge_mode=getattr(args, "merge_mode", "auto") or "auto")
            print(rid)
            return

        if getattr(args, "queue", False):
            repo = args.repo
            try:
                items = json.loads(args.items) if args.items else []
            except (ValueError, TypeError) as e:
                print(f"report --queue: bad --items JSON ({e})", file=sys.stderr)
                return
            # items may be [[issue, title], ...] or [{"issue":..,"title":..}, ...]
            norm = []
            for it in items:
                if isinstance(it, dict):
                    norm.append((it.get("issue"), it.get("title", "")))
                else:
                    norm.append((it[0], it[1] if len(it) > 1 else ""))
            reporter.queue_report(repo, norm)
            return

        if getattr(args, "selftest", False):
            _report_selftest(reporter)
            return

        # default: a phase / heartbeat / field event for an existing run.
        # Resolve the run id ROBUSTLY: prefer --run, else look it up from
        # --repo/--issue (the persisted map). The worker's $RUN shell var does
        # NOT survive across separate `report` invocations (each runs in a fresh
        # shell), which silently dropped every mid-run phase report.
        rid = args.run
        repo = getattr(args, "repo", None)
        issue = getattr(args, "issue", None)
        if not rid:
            if repo and issue is not None:
                rid = reporter.current_run(repo, issue)
            if not rid:
                print("report: need --run, or --repo+--issue of a started run",
                      file=sys.stderr)
                return
        # Recover repo/issue from the run id when not supplied, so EVERY event
        # carries them. A mid-run report with NULL repo/issue created a board row
        # that could never be joined to gh signals (and the empty repo|issue
        # stale rows).
        if repo is None or issue is None:
            r2, i2 = reporter.run_to_repo_issue(rid)
            repo = repo if repo is not None else r2
            issue = issue if issue is not None else i2
        reviews = _parse_reviews(getattr(args, "review", None))
        fields = {}
        for k in ("goal", "approach", "result", "note", "pr", "phase"):
            v = getattr(args, k, None)
            if v is not None:
                fields[k] = v
        # --pr maps to the pr_url field the board stores
        if "pr" in fields:
            fields["pr_url"] = fields.pop("pr")
        if repo is not None:
            fields["repo"] = repo
        if issue is not None:
            fields["issue"] = issue
        phase = fields.pop("phase", None)
        if getattr(args, "heartbeat", False) and phase is None and "note" not in fields:
            # a bare heartbeat carries no phase/field — just a liveness ping
            fields["note"] = fields.get("note") or "heartbeat"
        reporter.report(rid, phase=phase, reviews=reviews or None, **fields)
    except Exception as e:  # absolute backstop — a report must never crash a worker
        print(f"report: ignored error ({e})", file=sys.stderr)


def _parse_reviews(values):
    """`--review k=v` (repeatable) -> [(check, state), ...]. Ignores malformed."""
    out = []
    for raw in (values or []):
        if "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            out.append((k, v))
    return out


def _report_selftest(reporter):
    """POST a synthetic ping straight to the board and report acceptance.

    Bypasses the offline queue so we test the LIVE path: builds a minimal valid
    event and calls reporter._post_one directly. Prints OK/FAIL and the board URL."""
    import time
    import uuid
    ev = {
        "run_id": f"selftest-0-{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}",
        "event_id": uuid.uuid4().hex,
        "seq": 1,
        "phase": "validating",
        "event_ts": time.time(),
        "machine": os.uname().nodename,
        "repo": "selftest/ping",
        "issue": 1,
        "title": "board selftest ping",
        "note": "synthetic selftest ping",
    }
    ok = False
    try:
        ok = reporter._post_one(ev)
    except Exception as e:
        print(f"selftest: error posting ({e})", file=sys.stderr)
    if ok:
        print(f"selftest: OK — board accepted the ping at {board_url()}")
    else:
        print(f"selftest: FAIL — board at {board_url()} did not accept the ping "
              f"(down, wrong token, or unreachable)", file=sys.stderr)


def cmd_board(args):
    """Board control: --url (live-check + print LAN URL), status, --serve (daemon)."""
    if getattr(args, "serve", False):
        _board_serve()
        return
    if getattr(args, "url", False):
        _board_print_url()
        return
    # default / `status`
    _board_status()


def _board_serve():
    """Run the board HTTP server in the FOREGROUND (systemd ExecStart target).

    Instantiates a Board on the local sqlite DB, reads the shared token, and
    hands off to board.server.run_server (which binds BOARD_HOST_IP:PORT, fails
    loud if already bound, and starts the writer + gh refresher threads)."""
    from board.db import Board
    from board.server import run_server

    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    token = ""
    try:
        token = BOARD_TOKEN_PATH.read_text().strip()
    except OSError:
        pass
    if not token:
        print(f"FATAL: no board token at {BOARD_TOKEN_PATH}. Run "
              f"`python3 airuleset.py install` on the board host to generate it.",
              file=sys.stderr)
        sys.exit(1)
    board = Board(str(BOARD_DB_PATH))
    repos = _monitored_repos()
    run_server(board, token, host=BOARD_HOST_IP, port=BOARD_PORT, repos=repos)


def _monitored_repos():
    """Repos the gh refresher polls.

    Returns BOARD_REPOS env repos UNION repos discovered in the runs table
    (board.distinct_repos()), deduped and validated by gh.valid_repo. The
    union means the refresher activates automatically as soon as any worker
    reports a run — no BOARD_REPOS config required on deploy.

    Falls back gracefully when the DB is absent or unreadable (e.g. first
    run before any worker has reported)."""
    from board import gh as ghmod
    # BOARD_REPOS env (may be empty / unset)
    raw = os.environ.get("BOARD_REPOS", "").strip()
    env_repos = [r.strip() for r in raw.split(",") if r.strip()] if raw else []

    # DB-discovered repos
    db_repos = []
    try:
        from board.db import Board
        board = Board(str(BOARD_DB_PATH))
        db_repos = board.distinct_repos()
    except Exception:
        pass  # DB absent or unreadable on first boot; that's fine

    # Merge, dedupe, validate
    seen = set()
    result = []
    for r in env_repos + db_repos:
        if r and r not in seen and ghmod.valid_repo(r):
            seen.add(r)
            result.append(r)
    return result


def _board_print_url():
    """Curl-check the board; print the LAN URL only when it returns 200.

    Per no-localhost-urls.md: never present a dead URL. If the board is down AND
    we're on the board host, try restarting the service once before giving up."""
    url = board_url()
    if _board_is_live(url):
        print(url)
        return
    if is_board_host():
        print("board: not responding — attempting service restart...",
              file=sys.stderr)
        _restart_board_service()
        if _board_is_live(url):
            print(url)
            return
    print(f"board: DOWN — {url} did not return 200 "
          f"(not started, wrong host, or crashed)", file=sys.stderr)
    sys.exit(1)


def _board_is_live(url, timeout=2):
    """True iff GET <url> returns HTTP 200 within `timeout` seconds."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _board_status():
    """Print board liveness + whether this host is the board host."""
    url = board_url()
    here = "board host" if is_board_host() else "reporter-only host"
    live = _board_is_live(url)
    print(f"autopilot board: {url}")
    print(f"  this machine: {here} (BOARD_HOST_IP={BOARD_HOST_IP})")
    print(f"  liveness:     {'UP (200)' if live else 'DOWN / unreachable'}")
    print(f"  token:        {'present' if BOARD_TOKEN_PATH.exists() else 'absent'}")


# ---------------------------------------------------------------------------
# Autopilot Board install branching + systemd service (plan Task 14)
# ---------------------------------------------------------------------------


def _xdg_runtime_env():
    """A copy of os.environ with XDG_RUNTIME_DIR set explicitly.

    `systemctl --user` needs XDG_RUNTIME_DIR to find the user bus; when install
    runs over SSH (no login session) it is often unset. We set it deterministically
    to /run/user/<uid>."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _ensure_board_token():
    """Generate the shared board token if absent. Returns the token.

    Atomic creation: os.open with O_CREAT|O_WRONLY|O_EXCL and mode 0o600 means
    the file is born 0600 — no 0664→0600 window from write_text + chmod.
    FileExistsError means another process won the race or the file already
    exists; in that case we reuse the existing token (idempotent)."""
    import secrets
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    # Fast path: reuse if already present and non-empty.
    if BOARD_TOKEN_PATH.exists():
        existing = BOARD_TOKEN_PATH.read_text().strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    try:
        # O_EXCL ensures atomicity: only ONE process creates the file,
        # born with 0o600 — no world/group-readable window.
        fd = os.open(str(BOARD_TOKEN_PATH), os.O_CREAT | os.O_WRONLY | os.O_EXCL,
                     0o600)
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
    except FileExistsError:
        # Race: another process created the file first — reuse it.
        existing = BOARD_TOKEN_PATH.read_text().strip()
        if existing:
            return existing
        # File exists but is empty (concurrent write still in progress? unlikely).
        # Fall through: return the token we generated (best effort).
    print(f"  Generated board token: {BOARD_TOKEN_PATH} (0600)")
    return token


def _render_service_unit():
    """Read the unit template and substitute the absolute repo path placeholder.

    The template ships with a `{{REPO_DIR}}` placeholder so the ExecStart points
    at THIS checkout's airuleset.py (dev1 path may differ from dev2)."""
    text = BOARD_SERVICE_TEMPLATE.read_text()
    return text.replace("{{REPO_DIR}}", str(REPO_DIR))


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


def _restart_board_service():
    """Best-effort `systemctl --user restart autopilot-board.service`."""
    rc, _out, err = _run_systemctl(["restart", "autopilot-board.service"])
    if rc != 0:
        print(f"  board service restart failed (rc={rc}): {err.strip()}",
              file=sys.stderr)
    return rc == 0


def setup_board_service():
    """Install + start the board systemd --user service on the board host.

    Called ONLY when is_board_host() is True (the maybe_setup_board gate). On any
    failure it PRINTS the exact manual command rather than claiming success, so a
    failed install is visible and recoverable. Steps:
      1. generate the token (0600) if absent
      2. write the unit (repo path substituted) to ~/.config/systemd/user/
      3. loginctl enable-linger (best-effort — service survives logout)
      4. daemon-reload + enable --now (explicit XDG_RUNTIME_DIR)
      5. curl the LAN URL for 200 (server binds the LAN IP, not loopback), then print it
    """
    import subprocess
    print("  Board host detected — installing systemd --user service")

    # 1. token
    _ensure_board_token()

    # 2. write the unit
    if not BOARD_SERVICE_TEMPLATE.exists():
        print(f"  ERROR: service template missing: {BOARD_SERVICE_TEMPLATE} — "
              f"cannot install the board service.", file=sys.stderr)
        return False
    BOARD_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    BOARD_SERVICE_DEST.write_text(_render_service_unit())
    print(f"  Wrote unit: {BOARD_SERVICE_DEST}")

    manual = (
        "    loginctl enable-linger $(whoami)\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reload\n"
        "    XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user enable --now "
        "autopilot-board.service")

    # 3. linger (best-effort, not fatal)
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
    rc, _o, err = _run_systemctl(["enable", "--now", "autopilot-board.service"])
    if rc != 0:
        print(f"  systemctl enable --now FAILED (rc={rc}): {err.strip()}\n"
              f"  Run manually:\n{manual}", file=sys.stderr)
        return False

    # 5. liveness check then print the LAN URL.
    # The server binds BOARD_HOST_IP (the scoped LAN interface, not 0.0.0.0/loopback),
    # so the self-check MUST hit the LAN URL — 127.0.0.1 would falsely report "not live".
    if _wait_board_live(board_url()):
        print(f"  Board is live. LAN URL: {board_url()}")
        return True
    print(f"  Board service started but did NOT answer on {board_url()}. "
          f"Check `systemctl --user status autopilot-board.service` and "
          f"`journalctl --user -u autopilot-board.service`.", file=sys.stderr)
    return False


def _whoami():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "")


def _wait_board_live(local_url, attempts=5, delay=1.0):
    """Poll a local board URL up to `attempts` times for an HTTP 200."""
    import time
    for _ in range(attempts):
        if _board_is_live(local_url):
            return True
        time.sleep(delay)
    return False


def maybe_setup_board():
    """Install branching, called from cmd_install AFTER the core install work.

    Board host  → setup_board_service() (the systemd daemon).
    Other hosts → ensure ~/.claude exists (reporter writes its state there) and
                  print that the board is skipped here (reports go to the host)."""
    if is_board_host():
        setup_board_service()
    else:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  board: skipped (not board host; reports go to {board_url()})")


# ---------------------------------------------------------------------------
# File-Drop systemd service + share/serve subcommands
# ---------------------------------------------------------------------------


def _render_filedrop_unit():
    """Read the file-drop unit template and substitute the per-machine placeholders.

    {{REPO_DIR}} -> this checkout's path (ExecStart). {{HOST_IP}} -> the LAN IP
    this machine should bind, computed HERE (unsandboxed, so `hostname -I` works)
    and baked into Environment=FILEDROP_HOST so the sandboxed server never needs
    AF_NETLINK to discover its own address."""
    return (FILEDROP_SERVICE_TEMPLATE.read_text()
            .replace("{{REPO_DIR}}", str(REPO_DIR))
            .replace("{{HOST_IP}}", filedrop_host_ip()))


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

    # 2. write the unit
    if not FILEDROP_SERVICE_TEMPLATE.exists():
        print(f"  ERROR: file-drop service template missing: "
              f"{FILEDROP_SERVICE_TEMPLATE}", file=sys.stderr)
        return False
    FILEDROP_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    FILEDROP_SERVICE_DEST.write_text(_render_filedrop_unit())
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
    url = filedrop_url()
    if _wait_filedrop_live(url):
        print(f"  File-drop is live. LAN base URL: {url}")
        return True
    print(f"  File-drop service started but did NOT answer on {url}. Check "
          f"`systemctl --user status filedrop.service`.", file=sys.stderr)
    return False


def maybe_setup_filedrop():
    """Install the file-drop service on this machine (every host runs one)."""
    setup_filedrop_service()


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
      --autopilot-done     compose + send the canonical per-ticket completion card
                           from fields (--repo --pr --merge-sha --version --review
                           --done --remaining --tickets-json). Deduped on repo#pr.
      --body "<markdown>"  send arbitrary markdown (the general primitive).
    """
    from notify import (compose_autopilot_card, mention_prefix, send)

    if getattr(args, "mention_prefix", False):
        sys.stdout.write(mention_prefix())
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

    print("notify: nothing to send (use --autopilot-done, --body, or "
          "--mention-prefix)", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Remote deployment
# ---------------------------------------------------------------------------

# Remote machines that should receive airuleset updates
REMOTE_HOSTS = [
    {
        "name": "dev2",
        "host": "10.77.8.134",
        "user": "newlevel",
        "repo_path": "~/devel/airuleset",
    },
]


def cmd_push(args):
    """Push to GitHub and deploy to all remote machines.

    Fail-closed: the full test suite runs FIRST; a single failing test aborts the
    push (and therefore the dev2 deploy) so untested code never ships."""
    import subprocess

    # 0. Run the full test suite — fail-closed before any push/deploy.
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
        ssh_result = subprocess.run(
            [
                "sshpass", "-p", "newlevel",
                "ssh", "-o", "StrictHostKeyChecking=no",
                f"{remote['user']}@{remote['host']}",
                remote_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if ssh_result.returncode != 0:
            print(f"  FAILED: {ssh_result.stderr.strip()}")
        else:
            print(f"  {ssh_result.stdout.strip()}")

        # Distribute the board token so this remote's reporter can POST to the
        # token-gated board. Without it EVERY report is 403-rejected and the
        # reporter DROPS it (4xx = poison), so the remote's autopilot runs never
        # appear on the board (the dev2 "no statuses" bug). The board host holds
        # the authoritative token; copy it over ssh stdin (0600).
        if BOARD_TOKEN_PATH.exists():
            try:
                token_val = BOARD_TOKEN_PATH.read_text()
                tok_result = subprocess.run(
                    ["sshpass", "-p", "newlevel", "ssh",
                     "-o", "StrictHostKeyChecking=no",
                     f"{remote['user']}@{remote['host']}",
                     "umask 077; mkdir -p ~/.claude && "
                     "cat > ~/.claude/autopilot-board.token"],
                    input=token_val, capture_output=True, text=True, timeout=30)
                if tok_result.returncode == 0:
                    print(f"  board token distributed to {remote['name']}")
                else:
                    print(f"  WARN: board token copy failed: "
                          f"{tok_result.stderr.strip()}")
            except Exception as e:
                print(f"  WARN: board token copy error: {e}")
    print("\nAll deployments complete.")


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

    # --- Autopilot Board: report (worker client) + board (control) ---------
    p_report = sub.add_parser(
        "report", help="Report an autopilot run phase/event to the board")
    p_report.add_argument("--start", action="store_true",
                          help="Mint a new run_id, emit the opening event, print the run_id")
    p_report.add_argument("--run", help="Existing run_id to report against")
    p_report.add_argument("--repo", help="owner/name (for --start / --queue)")
    p_report.add_argument("--issue", type=int, help="Issue number (for --start)")
    p_report.add_argument("--title", help="Run title (for --start)")
    p_report.add_argument("--is-bug-fix", dest="is_bug_fix", action="store_true",
                          help="Mark the run as a bug fix (regression gate applies)")
    p_report.add_argument("--has-deploy", dest="has_deploy", action="store_true",
                          help="Mark the run as deploying (deploy_verified gate applies)")
    p_report.add_argument("--merge-mode", dest="merge_mode", default="auto",
                          help="auto | manual (default auto)")
    p_report.add_argument("--phase", help="Phase to advance to")
    p_report.add_argument("--goal", help="Goal text")
    p_report.add_argument("--approach", help="Approach text")
    p_report.add_argument("--result", help="Result text")
    p_report.add_argument("--note", help="Free-form note")
    p_report.add_argument("--pr", help="PR URL")
    p_report.add_argument("--review", action="append",
                          help="Gate claim k=v (repeatable), e.g. --review review=ok")
    p_report.add_argument("--heartbeat", action="store_true",
                          help="Emit a liveness heartbeat (no phase change required)")
    p_report.add_argument("--queue", action="store_true",
                          help="Report a planned-issue queue (--repo + --items JSON)")
    p_report.add_argument("--items",
                          help="JSON array of [issue,title] pairs (for --queue)")
    p_report.add_argument("--selftest", action="store_true",
                          help="POST a synthetic ping and report whether the board accepted it")

    p_board = sub.add_parser("board", help="Autopilot board control")
    p_board.add_argument("board_action", nargs="?", default=None,
                         choices=["status"],
                         help="status (default when no flag)")
    p_board.add_argument("--url", action="store_true",
                         help="Live-check the board and print its LAN URL (200 only)")
    p_board.add_argument("--serve", action="store_true",
                         help="Run the board HTTP server in the foreground (systemd ExecStart)")

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
    p_notify.add_argument("--autopilot-done", dest="autopilot_done",
                          action="store_true",
                          help="Compose + send the per-ticket completion card from fields")
    p_notify.add_argument("--body", help="Arbitrary markdown body to send")
    p_notify.add_argument("--repo", help="owner/name (autopilot card)")
    p_notify.add_argument("--pr", help="Shared PR number (autopilot card)")
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
    "report": cmd_report,
    "board": cmd_board,
    "share": cmd_share,
    "filedrop": cmd_filedrop,
    "notify": cmd_notify,
}
# Backwards-compatible alias used by main() before SUBCOMMANDS existed.
commands = SUBCOMMANDS


if __name__ == "__main__":
    main()
