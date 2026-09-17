#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1061 item 1).
#
# The `Design-by: main` anti-spoof block. All logic lives in
# gates/designbypost.py: it blocks a RAW `gh issue comment` whose body carries
# `Design-by: main` when the cwd is a lane worktree -- a worker must not pass off
# its design as the Fable main's (the truthful stamp is written by
# `airuleset.py design-record`). ALLOWS a non-comment command, a main cwd, a
# `Design-by: worker` comment, and design-record's own stdin post. Bypass:
# `# airuleset:design-by-ok <reason>` in the command (logged to
# ~/.claude/design-by-gate.log).
#
# Exit 2 = block; the module writes its reason to STDERR (emit_block_stderr) and
# the `1>&2` below routes any stray stdout there too (the model-visible deny
# channel). A python MALFUNCTION fails OPEN (RC not 0/2) -- a hook bug must not
# block every `gh issue comment`; the gate's own logic is the authority.
# `-P` (#1061 item 5b): a stale in-cwd gates/ can never shadow the real package.
# Dry-run: echo '{"tool_input":{"command":"gh issue comment 1 --body \"Design-by: main x\""},"cwd":"/repo/.claude/worktrees/agent-x"}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.designbypost <<<"$PAYLOAD" 1>&2 || RC=$?
[ "$RC" -eq 2 ] && exit 2
exit 0
