"""gates.commandshadow -- the built-in slash-command shadow guard (#874).

Entry for hooks/block-builtin-command-shadow.sh (PreToolUse Write|Edit).

REFUSES creating/renaming a `.claude/commands/<name>.md` or
`.claude/skills/<name>/SKILL.md` whose ``<name>`` is a Claude Code built-in
slash command. A project command/skill that reuses a built-in's name silently
SHADOWS the built-in on every checkout -- the odoo-erp `.claude/commands/resume.md`
incident (added 2026-07-13, shadowed `/resume` for 67 days, invisible until a
human needed `/resume`). This is the rule-intake gate's step 1: the one
mechanically-checkable class blocked BEFORE it lands.

The built-in list is the SSOT `cli_mdreview_audit.CLAUDE_CODE_BUILTIN_COMMANDS`
(pinned + sourced there). The audit module imports in ~45 ms (stdlib + two data
leaves, no watchdog/notify), well under the PreToolUse budget.

Bypass: `# airuleset:command-shadow-ok <reason>` anywhere in the written
content -- allowed and logged to ~/.claude/command-shadow-gate.log.

Exit 2 = block, reason on STDERR only (the model-visible deny channel); the
adapter runs this module with ``1>&2`` (the emit_block_stderr sibling shape used
by testskips/pushtest), so a stdout write too would double-render. STDLIB ONLY
at import.
"""
import os
import re
import time

from gates import allow, emit_block_stderr, read_payload

_COMMAND_RE = re.compile(r"(?:^|/)\.claude/commands/([^/]+)\.md$")
_SKILL_RE = re.compile(r"(?:^|/)\.claude/skills/([^/]+)/SKILL\.md$")
_BYPASS_RE = re.compile(
    r"#\s*airuleset:command-shadow-ok\s*(?P<reason>.*)", re.IGNORECASE)

SHADOW_LOG = "command-shadow-gate.log"


def _log(line):
    try:
        p = os.path.join(os.path.expanduser("~"), ".claude", SHADOW_LOG)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{int(time.time())} {line}\n")
    except OSError:
        pass  # airuleset:script-ok a gate must never fail on its own audit log


def _builtins_lower():
    """The pinned built-in set (lowercased). Imported from the audit module so
    there is ONE source of truth; a fallback keeps the guard alive even if the
    import is ever broken (the mechanical block is too important to fail-open on
    an import slip)."""
    try:
        from cli_mdreview_audit import CLAUDE_CODE_BUILTIN_COMMANDS
        return {c.lower() for c in CLAUDE_CODE_BUILTIN_COMMANDS}
    except Exception:
        # Fallback kept in sync with cli_mdreview_audit.CLAUDE_CODE_BUILTIN_COMMANDS
        # (the SSOT). Extracted from the CC 2.1.268 bundle command registry —
        # names + aliases, user-facing set. If the SSOT import ever breaks, this
        # keeps the mechanical block alive.
        return {
            "add-dir", "advisor", "agents", "allowed-tools", "android", "app",
            "artifacts", "background", "bashes", "bg", "branch",
            "break-reminder", "breaks", "bug", "checkpoint", "clear",
            "compact", "config", "context", "continue", "copy", "cost",
            "desktop", "diff", "downtime", "effort", "exit", "export", "fast",
            "feedback", "focus", "fork", "goal", "help", "hooks", "ide",
            "import", "init", "insights", "install", "ios", "keybindings",
            "list-agents", "login", "logout", "loops", "marketplace", "mcp",
            "memory", "memory-pause", "mobile", "model", "name", "pause-memory",
            "peers", "permissions", "plan", "plugin", "plugins",
            "privacy-settings", "quit", "rc", "recap", "remote",
            "remote-control", "rename", "restart", "resume", "review",
            "rewind", "session", "settings", "share", "skill-doctor", "skills",
            "stats", "status", "stop", "tasks", "teleport", "terminal-setup",
            "theme", "toggle-memory", "undo", "update", "upgrade", "usage",
            "version", "voice", "wellbeing", "workflows",
        }


def classify(file_path, content=""):
    """Return ``(blocked, reason)``. Blocked when ``file_path`` is a
    `.claude/commands/<builtin>.md` or `.claude/skills/<builtin>/SKILL.md`
    path AND the content carries no bypass token. Never raises."""
    if not file_path:
        return False, ""
    path = str(file_path)
    m = _COMMAND_RE.search(path)
    kind = "command"
    if not m:
        m = _SKILL_RE.search(path)
        kind = "skill"
    if not m:
        return False, ""
    name = m.group(1)
    if name.lower() not in _builtins_lower():
        return False, ""

    if _BYPASS_RE.search(content or ""):
        _log(f"BYPASS {kind} {name} path={path}")
        return False, ""

    surface = (f".claude/commands/{name}.md" if kind == "command"
               else f".claude/skills/{name}/SKILL.md")
    rename = (f"{name}-<scope>.md" if kind == "command"
              else f"{name}-<scope>/SKILL.md")
    reason = (
        f"🚫 BLOCKED: `{surface}` shadows the Claude Code built-in "
        f"slash command `/{name}` (#874).\n"
        f"A project {kind} with a built-in's name silently OVERRIDES it on "
        f"every checkout — the exact odoo-erp `/resume` incident (67 days "
        f"invisible). Rename it so it no longer collides, e.g. `{rename}` "
        f"(→ `/{name}-<scope>`).\n"
        f"Bypass (rare, logged): add `# airuleset:command-shadow-ok <reason>` "
        f"to the file content."
    )
    return True, reason


def _payload_fields(payload):
    """``(file_path, content)`` from a Write/Edit hook payload. Never raises."""
    import json
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return "", ""
    if not isinstance(obj, dict):
        return "", ""
    ti = obj.get("tool_input") or {}
    if not isinstance(ti, dict):
        return "", ""
    file_path = ti.get("file_path") or ""
    content = (ti.get("content") or ti.get("new_string")
               or ti.get("command") or "")
    return file_path, content


def main():
    payload = read_payload()
    file_path, content = _payload_fields(payload)
    blocked, reason = classify(file_path, content)
    if blocked:
        _log(f"BLOCK path={file_path}")
        # stderr-only: the adapter runs this module with `1>&2`, so writing to
        # stdout too would double-render the reason on the model channel (#874
        # review — the emit_block_stderr sibling shape used by testskips/pushtest).
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
