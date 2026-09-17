#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Agent | Task matcher) -- THIN ADAPTER (#1061).
#
# The MAIN-authored-design dispatch precondition. All logic lives in
# gates/designdispatch.py: it refuses an `autopilot-worker` dispatch unless the
# NEWEST `Design-by:` comment on every issue in the prompt is
# `Design-by: main <Fable id>` (the owner's standing #871 rule -- the design is
# authored by the Fable MAIN, the worker only implements). FAIL-CLOSED on an
# unreadable comment thread (gh error); ALLOWS a prompt with no parseable issue
# (cannot verify -- the ticket's design comment is the durable authority) and any
# non-autopilot-worker dispatch. Bypass: `airuleset:design-by-ok <reason>` in the
# prompt (logged to ~/.claude/design-by-gate.log).
#
# Exit 2 = block; the reason is on STDERR (the model-visible deny channel). A
# python MALFUNCTION (the gate cannot run at all) fails OPEN -- a hook bug must
# not wedge every dispatch (the same direction block-commit-without-design.sh
# takes); the gate's OWN gh-error path already returns a real block (exit 2).
# `-P` (#1061 item 5b): a stale in-cwd gates/ can never shadow the real package.
# Dry-run: echo '{"tool_name":"Agent","cwd":"/repo","tool_input":{"subagent_type":"autopilot-worker","prompt":"Work issue #1 in repo"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.designdispatch <<<"$PAYLOAD" 1>&2 || RC=$?
[ "$RC" -eq 2 ] && exit 2
exit 0
