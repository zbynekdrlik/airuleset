#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Write | Edit matcher) -- THIN ADAPTER (#874).
#
# The built-in slash-command shadow guard. All logic lives in
# gates/commandshadow.py: it refuses creating/renaming a
# `.claude/commands/<name>.md` or `.claude/skills/<name>/SKILL.md` whose <name>
# is a Claude Code built-in slash command (the odoo-erp `/resume` incident,
# 67 days invisible). The block reason names the collision + the rename
# convention (`<name>-<scope>`). Bypass: `# airuleset:command-shadow-ok <reason>`
# in the written content (logged to ~/.claude/command-shadow-gate.log).
#
# Exit 2 = block; the reason is on STDERR (the model-visible deny channel) --
# gates.emit_block also prints it to stdout for a terminal run. A python
# MALFUNCTION (the gate cannot run at all) fails OPEN -- a hook bug must not
# wedge every Write/Edit; the gate's own fallback built-in list keeps the
# mechanical block alive even if the audit-module import is broken.
# `-P` (#1061 item 5b): a stale in-cwd gates/ can never shadow the real package.
# Dry-run: echo '{"tool_name":"Write","tool_input":{"file_path":"/x/.claude/commands/resume.md","content":"# r"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
command -v python3 &>/dev/null || exit 0
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.commandshadow <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -eq 2 ]; then
    echo "🚫 BLOCKED: built-in command shadow (gates.commandshadow) — reason above." >&2
    exit 2
fi
if [ "$RC" -ne 0 ]; then
    # Fail-OPEN by design (a gate that cannot classify never blocks a write) --
    # but say so, never silently.
    echo "command-shadow hook: classifier exited $RC — allowing (fail-open)." >&2
fi
exit 0
