#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Write | Edit matcher) -- THIN ADAPTER (#1333).
#
# The house-stack frontend-framework guard. All logic lives in gates/housestack.py:
# in a house project (registry `stack`, or an unregistered repo) it refuses a
# `package.json` Write/Edit whose CONTENT brings a JS UI framework family NEW to
# the git repo, unless a CLAUDE.md (up to the git root, or .claude/CLAUDE.md)
# carries that technology's owner-approved
# `<!-- airuleset:stack-exception <what> — <#N or GitHub URL> -->`. It never parses
# Bash commands (round 3 A): hooks/check-frontend-framework-state.sh checks the
# repo state after one. The block reason points at ~/.claude/rules/house-stack.md
# and the owner-exception path. No bypass token: the marker IS the exception.
#
# Exit 2 = block; the reason is on STDERR (the model-visible deny channel). An
# unreadable payload, package.json or edited file fails OPEN with a logged reason
# (~/.claude/stack-gate.log), and a python MALFUNCTION fails OPEN too -- a hook bug
# must not wedge every Write/Edit.
# Cheap path: a Write/Edit payload that never names package.json exits here, before
# python; only package.json writes and unrecognisable payloads reach the gate.
# `-P` (#1061 item 5b): a stale in-cwd gates/ can never shadow the real package.
# Dry-run: echo '{"tool_name":"Write","tool_input":{"file_path":"/x/package.json","content":"{\"dependencies\":{\"react\":\"18\"}}"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
case "$PAYLOAD" in
    *package.json*) ;;
    *'"tool_input"'*) exit 0 ;;
esac
command -v python3 &>/dev/null || exit 0
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.housestack <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -eq 2 ]; then
    echo "🚫 BLOCKED: house stack (gates.housestack) — reason above." >&2
    exit 2
fi
if [ "$RC" -ne 0 ]; then
    echo "stack hook: classifier exited $RC — allowing (fail-open)." >&2
fi
exit 0
