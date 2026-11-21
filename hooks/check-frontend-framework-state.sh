#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher) -- THIN ADAPTER (#1333, review round 3 A).
#
# The house-stack STATE check. All logic lives in gates/housestate.py: after a
# Bash call whose text mentions a JS package manager or scaffolder, in a house
# checkout, it compares the repo's package.json files (working tree + untracked,
# SKIP_DIRS and hidden dirs left out) with HEAD and reports a JS UI framework
# family present now but absent from every HEAD package.json, unless the
# project's CLAUDE.md carries that technology's owner-approved
# `<!-- airuleset:stack-exception <what> — <#N or GitHub URL> -->`.
# It never parses the command beyond that prefilter: the #1220 lesson.
#
# Exit 2 = the reason on STDERR reaches the model (the command already ran: it
# says revert or get the owner's exception). A git failure fails OPEN with a
# logged reason (~/.claude/stack-gate.log); a python MALFUNCTION fails OPEN too.
# Cheap path: a payload that names no package manager, scaffolder or package.json
# exits here, before python.
# `-P` (#1061 item 5b): a stale in-cwd gates/ can never shadow the real package.
# Dry-run: echo '{"tool_name":"Bash","cwd":"/path/to/repo","tool_input":{"command":"npm i react"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
case "$PAYLOAD" in
    *npm* | *yarn* | *bun* | *npx* | *pnpx* | *corepack* | *create-* | *package.json*) ;;
    *) exit 0 ;;
esac
command -v python3 &>/dev/null || exit 0
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.housestate <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -eq 2 ]; then
    exit 2
fi
if [ "$RC" -ne 0 ]; then
    echo "stack state hook: classifier exited $RC — allowing (fail-open)." >&2
fi
exit 0
