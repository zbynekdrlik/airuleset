#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1297).
#
# Refuses `gh issue close <N>` (any -R form) and the `gh api …/issues/<N> -X PATCH
# state=closed` form while the issue DESCRIPTION still has an unchecked `- [ ]`
# line -- every line counts, the `- [ ] #child` links included. A
# `--reason "not planned"` close is exempt. All logic lives in
# gates/closechecklist.py; it FAILS OPEN on every gh / parse error (logged to
# ~/.claude/close-checklist-gate.log) and honours the bypass
# `# airuleset:checklist-close-ok <reason>`.
# Exit 2 = block; the reason goes to STDERR (the model-visible channel). A
# python3 malfunction fails OPEN with a stderr note: this gate guards a
# discipline and must never wedge every close on a broken interpreter.
# Dry-run:
#   echo '{"tool_input":{"command":"gh issue close 5 -R o/r"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
case "$PAYLOAD" in
    *close*|*PATCH*|*patch*) ;;
    *) exit 0 ;;
esac
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.closechecklist <<<"$PAYLOAD" || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "block-close-with-open-checklist: gate exited $RC (internal error) — allowing the close" >&2
    exit 0
fi
exit "$RC"
